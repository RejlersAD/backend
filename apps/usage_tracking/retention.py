"""Roll old usage_log / system_activity rows into daily summaries, then delete them.

One calendar day (settings.TIME_ZONE) per transaction: the day's raw rows are
added to the summary table and deleted together, so a crash never leaves a day
half-counted. On PostgreSQL an advisory lock serialises concurrent runs.
Summaries accumulate, so raw rows that arrive late for an already-processed
day are simply added on the next run.

Settings: apps/usage_tracking/config.py.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from django.db import connection, transaction
from django.db.models import Count, F, Max, Q, Sum
from django.utils import timezone

from . import config as cfg
from .models import ActivityDailySummary, UsageDailySummary, UsageLog

logger = logging.getLogger(__name__)
_LOCK_KEY = 72_412_026  # arbitrary, unique to this job


@dataclass
class TableResult:
    table: str
    keep_days: int
    days: list = field(default_factory=list)
    rows_summarised: int = 0
    rows_deleted: int = 0

    def as_dict(self):
        return {'table': self.table, 'keep_days': self.keep_days, 'days_processed': len(self.days),
                'first_day': str(self.days[0]) if self.days else None,
                'last_day': str(self.days[-1]) if self.days else None,
                'rows_summarised': self.rows_summarised, 'rows_deleted': self.rows_deleted}


def day_bounds(day: date):
    start = timezone.make_aware(datetime.combine(day, time.min), timezone.get_current_timezone())
    return start, start + timedelta(days=1)


def first_full_day(moment: datetime) -> date:
    """First calendar day lying entirely at or after `moment` (summaries hold whole days)."""
    local = timezone.localtime(moment)
    return local.date() if local.timetz().replace(tzinfo=None) == time.min else local.date() + timedelta(days=1)


def _days_before(qs, keep_days: int, max_days: int):
    """Calendar days older than the retention window that still have raw rows (oldest first)."""
    cutoff = timezone.localdate() - timedelta(days=keep_days)
    oldest = qs.filter(timestamp__lt=day_bounds(cutoff)[0]).order_by('timestamp').values_list('timestamp', flat=True).first()
    if oldest is None:
        return []
    day, days = timezone.localtime(oldest).date(), []
    while day < cutoff and len(days) < max_days:
        days.append(day)
        day += timedelta(days=1)
    return days


def _lock():
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute('select pg_advisory_xact_lock(%s)', [_LOCK_KEY])


# ── usage_log ────────────────────────────────────────────────────────────────
def _summarise_usage_day(day: date, dry_run: bool):
    start, end = day_bounds(day)
    raw = UsageLog.objects.filter(timestamp__gte=start, timestamp__lt=end)
    groups = list(raw.order_by().values('user_email', 'discipline_key').annotate(
        name=Max('user_full_name'), label=Max('discipline_label'), requests=Count('id'),
        success=Count('id', filter=Q(success=True)), ms_sum=Sum('response_time_ms'),
        ms_count=Count('response_time_ms'), last=Max('timestamp')))
    total = sum(g['requests'] for g in groups)
    if dry_run or not groups:
        return total, 0
    for g in groups:
        row, created = UsageDailySummary.objects.select_for_update().get_or_create(
            date=day, user_email=g['user_email'] or '', discipline_key=g['discipline_key'],
            defaults={'user_full_name': g['name'] or '', 'discipline_label': g['label'] or '',
                      'requests': g['requests'], 'success_count': g['success'],
                      'response_ms_sum': g['ms_sum'] or 0, 'response_ms_count': g['ms_count'], 'last_seen': g['last']})
        if not created:
            UsageDailySummary.objects.filter(pk=row.pk).update(
                requests=F('requests') + g['requests'], success_count=F('success_count') + g['success'],
                response_ms_sum=F('response_ms_sum') + (g['ms_sum'] or 0),
                response_ms_count=F('response_ms_count') + g['ms_count'],
                last_seen=max(filter(None, [row.last_seen, g['last']]), default=None))
    deleted, _ = raw.delete()
    return total, deleted


# ── system_activity ──────────────────────────────────────────────────────────
def _activity_model():
    from apps.activity.models import SystemActivity
    return SystemActivity


def _summarise_activity_day(day: date, type_filter: Q, dry_run: bool):
    start, end = day_bounds(day)
    raw = _activity_model().objects.filter(type_filter, timestamp__gte=start, timestamp__lt=end)
    groups = list(raw.order_by().values('user_email', 'activity_type', 'category', 'severity').annotate(
        n=Count('id'), success=Count('id', filter=Q(success=True))))
    total = sum(g['n'] for g in groups)
    if dry_run or not groups:
        return total, 0
    for g in groups:
        key = {'date': day, 'user_email': g['user_email'] or '', 'activity_type': g['activity_type'],
               'category': g['category'] or '', 'severity': g['severity'] or ''}
        row, created = ActivityDailySummary.objects.select_for_update().get_or_create(
            **key, defaults={'count': g['n'], 'success_count': g['success']})
        if not created:
            ActivityDailySummary.objects.filter(pk=row.pk).update(
                count=F('count') + g['n'], success_count=F('success_count') + g['success'])
    deleted, _ = raw.delete()
    return total, deleted


def _run_table(result: TableResult, days, summarise, dry_run: bool):
    for day in days:
        with transaction.atomic():
            _lock()
            summarised, deleted = summarise(day, dry_run)
        result.days.append(day)
        result.rows_summarised += summarised
        result.rows_deleted += deleted
    return result


def apply_retention(*, dry_run: bool = False, max_days: int | None = None) -> list[dict]:
    """Process every table's backlog (up to max_days calendar days each). Returns one dict per table."""
    max_days = max_days or cfg.LOG_RETENTION_MAX_DAYS_PER_RUN
    results = []

    usage = TableResult('usage_log', cfg.USAGE_LOG_RETENTION_DAYS)
    results.append(_run_table(usage, _days_before(UsageLog.objects.all(), usage.keep_days, max_days),
                              _summarise_usage_day, dry_run))

    activity = _activity_model().objects
    noise = Q(activity_type__in=cfg.ACTIVITY_NOISE_TYPES)
    if cfg.ACTIVITY_NOISE_TYPES:
        res = TableResult('system_activity (' + ', '.join(cfg.ACTIVITY_NOISE_TYPES) + ')', cfg.ACTIVITY_NOISE_RETENTION_DAYS)
        results.append(_run_table(res, _days_before(activity.filter(noise), res.keep_days, max_days),
                                  lambda d, dr: _summarise_activity_day(d, noise, dr), dry_run))
    if cfg.ACTIVITY_RETENTION_DAYS > 0:
        res = TableResult('system_activity (other types)', cfg.ACTIVITY_RETENTION_DAYS)
        results.append(_run_table(res, _days_before(activity.exclude(noise), res.keep_days, max_days),
                                  lambda d, dr: _summarise_activity_day(d, ~noise, dr), dry_run))

    out = [r.as_dict() for r in results]
    logger.info('log retention%s: %s', ' (dry run)' if dry_run else '', out)
    return out
