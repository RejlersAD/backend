"""Reviewed monthly awards based on recorded AI requests, never browsing."""
import hashlib
import json
from datetime import datetime, timezone as utc

from django.contrib.auth import get_user_model
from django.db import transaction, IntegrityError
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .ai_champion_models import AIUsageLog, MonthlyChampionPublication

METHOD = {
    'version': 'ai-request-engagement-v3',
    'weights': {'successful_requests': 50, 'request_success_rate': 30, 'active_days': 20},
    'description': '50% successful request volume relative to the cohort maximum, 30% request success rate, 20% active AI days relative to the cohort maximum. Ties: successful requests, active days, then user ID.',
    'eligibility': 'Active account with at least one successful server or legacy AI request in the UTC calendar month. Explicit client telemetry is excluded.',
    'limitations': 'Recorded request engagement, not verified business outcomes. Historical request origin and deduplication are not attested. Browsing, spend and token volume do not earn points.',
}


def month_window(year, month):
    now = timezone.now()
    if not 2000 <= year <= now.year or not 1 <= month <= 12 or (year, month) > (now.year, now.month):
        raise ValidationError({'period': 'Choose a valid current or past month from 2000 onward.'})
    start = datetime(year, month, 1, tzinfo=utc.utc)
    end = datetime(year + (month == 12), 1 if month == 12 else month + 1, 1, tzinfo=utc.utc)
    return start, end


def candidates(year, month, user_ids=None):
    start, end = month_window(year, month)
    logs = AIUsageLog.objects.filter(timestamp__gte=start, timestamp__lt=min(end, timezone.now()), user__is_active=True).exclude(provenance='client')
    if user_ids is not None:
        logs = logs.filter(user_id__in=user_ids)
    rows = list(logs.order_by().values('user_id').annotate(
        requests=Count('id'), successful_requests=Count('id', filter=Q(success=True)),
        active_days=Count(TruncDate('timestamp', tzinfo=utc.utc), distinct=True),
        modules=Count('application', distinct=True, filter=~Q(application='')),
        tokens=Sum('total_tokens'), cost=Sum('cost_usd')).filter(successful_requests__gt=0))
    maximum_requests = max((r['successful_requests'] for r in rows), default=1)
    maximum_days = max((r['active_days'] for r in rows), default=1)
    people = {u.pk: u for u in get_user_model().objects.filter(pk__in=[r['user_id'] for r in rows])}
    for row in rows:
        user = people[row['user_id']]
        row['user_id'] = str(row['user_id'])
        row['user'] = {'id': str(user.pk), 'name': user.get_full_name() or user.email, 'email': user.email}
        row['cost_usd'] = float(row.pop('cost') or 0)
        row['success_rate'] = round(row['successful_requests'] / row['requests'] * 100, 2)
        row['breakdown'] = {
            'successful_requests': round(row['successful_requests'] / maximum_requests * 50, 2),
            'request_success_rate': round(row['successful_requests'] / row['requests'] * 30, 2),
            'active_days': round(row['active_days'] / maximum_days * 20, 2),
        }
        row['score'] = round(sum(row['breakdown'].values()), 2)
    rows.sort(key=lambda r: (-r['score'], -r['successful_requests'], -r['active_days'], r['user_id']))
    for rank, row in enumerate(rows, 1):
        row['rank'] = rank
    return rows


def snapshot(year, month, user_ids=None):
    result = {'year': year, 'month': month, 'methodology': METHOD, 'candidates': candidates(year, month, user_ids)}
    result['fingerprint'] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result


def serialize_publication(publication, user_ids=None):
    if not publication:
        return None
    podium = publication.snapshot['candidates'][:3]
    if user_ids is not None:
        permitted = {str(uid) for uid in user_ids}
        podium = [row for row in podium if row['user_id'] in permitted]
        if not podium:
            return None
    return {'id': str(publication.pk), 'year': publication.period_year, 'month': publication.period_month,
            'published_at': publication.published_at,
            'reviewer': publication.reviewer_name if user_ids is None else 'Super Administrator',
            'reason': publication.reason if user_ids is None else 'Reviewed platform-wide award; showing recognized participants in your organization.',
            'podium': podium, 'methodology': publication.snapshot['methodology']}


def monthly_report(year, month, user_ids=None, can_publish=False):
    start, end = month_window(year, month)
    preview = snapshot(year, month, user_ids)
    publication = MonthlyChampionPublication.objects.filter(period_year=year, period_month=month).first()
    history = [serialize_publication(p, user_ids) for p in MonthlyChampionPublication.objects.all()[:24]]
    return {**preview, 'period': {'start': start, 'end': end, 'closed': end <= timezone.now()},
            'publication': serialize_publication(publication, user_ids),
            'period_published': publication is not None,
            'history': [p for p in history if p], 'can_publish': can_publish,
            'scope': 'All organizations' if user_ids is None else 'Your organization'}


def publish(year, month, fingerprint, reason, reviewer):
    from .models import AuditLog
    _, end = month_window(year, month)
    if end > timezone.now():
        raise ValidationError({'period': 'Current-month candidates are provisional. Publish after the month ends.'})
    if len(reason.strip()) < 10:
        raise ValidationError({'reason': 'Explain the recognition decision in at least 10 characters.'})
    try:
        with transaction.atomic():
            if MonthlyChampionPublication.objects.filter(period_year=year, period_month=month).exists():
                raise ValidationError({'period': 'This month already has a published award. Published records cannot be overwritten.'})
            preview = snapshot(year, month)
            if fingerprint != preview['fingerprint']:
                raise ValidationError({'preview': 'Candidate data changed. Refresh and review the latest preview.'})
            if not preview['candidates']:
                raise ValidationError({'period': 'There are no eligible candidates for this month.'})
            award = MonthlyChampionPublication.objects.create(
                period_year=year, period_month=month, reviewer_id=str(reviewer.pk),
                reviewer_name=reviewer.get_full_name() or reviewer.email, reason=reason.strip(), snapshot=preview)
            AuditLog.objects.create(
                user=reviewer, user_email=reviewer.email, action='create',
                resource_type='MonthlyChampionPublication', resource_id=award.pk,
                resource_repr=f'AI Champion of the Month {year}-{month:02d}',
                changes={'published': {'before': False, 'after': True}},
                metadata={'year': year, 'month': month, 'scoring_version': METHOD['version'],
                          'snapshot_fingerprint': preview['fingerprint']}, success=True)
            return serialize_publication(award)
    except IntegrityError:
        if MonthlyChampionPublication.objects.filter(period_year=year, period_month=month).exists():
            raise ValidationError({'period': 'This month was published by another reviewer. Refresh to see the saved award.'})
        raise
