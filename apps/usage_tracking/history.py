"""Usage history that spans retention: daily summaries for old days, raw rows for recent days.

retention.py moves whole calendar days from usage_log / system_activity into
UsageDailySummary / ActivityDailySummary. Reports that can look further back than
the retention window read through these helpers, so their numbers do not change
when old raw rows are deleted.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from .models import ActivityDailySummary, UsageDailySummary, UsageLog
from .retention import first_full_day


def _summary_dates(start, end):
    """Summary days that lie inside [start, end] (summaries only hold whole days)."""
    return Q(date__gte=first_full_day(start), date__lte=timezone.localtime(end).date())


def usage_rows(start, end, emails=None):
    """One dict per (day, user_email, discipline_key) in [start, end]:
    name, label, requests, success, ms_sum, ms_count, last_seen."""
    merged = {}

    def add(day, email, key, name, label, requests, success, ms_sum, ms_count, last):
        row = merged.setdefault((day, email or '', key), {
            'day': day, 'user_email': email or '', 'discipline_key': key, 'name': '', 'label': '',
            'requests': 0, 'success': 0, 'ms_sum': 0, 'ms_count': 0, 'last_seen': None})
        row['name'] = row['name'] or name or ''
        row['label'] = row['label'] or label or ''
        row['requests'] += requests
        row['success'] += success
        row['ms_sum'] += ms_sum or 0
        row['ms_count'] += ms_count
        if last and (row['last_seen'] is None or last > row['last_seen']):
            row['last_seen'] = last

    summaries = UsageDailySummary.objects.filter(_summary_dates(start, end))
    raw = UsageLog.objects.filter(timestamp__gte=start, timestamp__lte=end)
    if emails is not None:
        summaries, raw = summaries.filter(user_email__in=emails), raw.filter(user_email__in=emails)
    for s in summaries.values_list('date', 'user_email', 'discipline_key', 'user_full_name', 'discipline_label',
                                   'requests', 'success_count', 'response_ms_sum', 'response_ms_count', 'last_seen'):
        add(*s)
    for r in (raw.order_by().annotate(day=TruncDate('timestamp'))
              .values('day', 'user_email', 'discipline_key')
              .annotate(name=Max('user_full_name'), label=Max('discipline_label'), requests=Count('id'),
                        success=Count('id', filter=Q(success=True)), ms_sum=Sum('response_time_ms'),
                        ms_count=Count('response_time_ms'), last=Max('timestamp'))):
        add(r['day'], r['user_email'], r['discipline_key'], r['name'], r['label'], r['requests'],
            r['success'], r['ms_sum'], r['ms_count'], r['last'])
    return list(merged.values())


def activity_counts(start, end, *, types=None, emails=None):
    """{(day, user_email, activity_type, category): count} for system_activity in [start, end]."""
    from apps.activity.models import SystemActivity
    counts = defaultdict(int)
    summaries = ActivityDailySummary.objects.filter(_summary_dates(start, end))
    raw = SystemActivity.objects.filter(timestamp__gte=start, timestamp__lte=end)
    if types is not None:
        summaries, raw = summaries.filter(activity_type__in=types), raw.filter(activity_type__in=types)
    if emails is not None:
        summaries, raw = summaries.filter(user_email__in=emails), raw.filter(user_email__in=emails)
    for day, email, kind, category, n in summaries.values_list('date', 'user_email', 'activity_type', 'category', 'count'):
        counts[(day, email or '', kind, category or '')] += n
    for r in (raw.order_by().annotate(day=TruncDate('timestamp'))
              .values('day', 'user_email', 'activity_type', 'category').annotate(n=Count('id'))):
        counts[(r['day'], r['user_email'] or '', r['activity_type'], r['category'] or '')] += r['n']
    return counts


def bucket_start(day: date, period: str) -> date:
    """Start date of the report bucket a day falls in (same buckets as TruncDate/Week/Month/Quarter)."""
    if period == 'weekly':
        return date.fromordinal(day.toordinal() - day.weekday())
    if period == 'monthly':
        return day.replace(day=1)
    if period == 'quarterly':
        return date(day.year, ((day.month - 1) // 3) * 3 + 1, 1)
    return day
