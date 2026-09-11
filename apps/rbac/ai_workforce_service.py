"""Observed employee adoption using an explicit, current eligibility cohort."""
from datetime import datetime, timedelta, timezone as utc
from collections import defaultdict

from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .ai_champion_models import AIUsageLog
from .models import UserProfile
from .ai_engagement import engagement_score, VERSION

from .ai_cohort import DEFAULT_MODULES, current_cohort


def workforce_adoption(week=None, user_ids=None, now=None, organization_id=None):
    now = now or timezone.now()
    today = now.astimezone(utc.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    this_monday = today - timedelta(days=today.weekday())
    if week:
        try:
            start = datetime.strptime(week, '%Y-%m-%d').replace(tzinfo=utc.utc)
        except (ValueError, TypeError):
            raise ValidationError({'week': 'Use a Monday date in YYYY-MM-DD format.'})
        if start.weekday() or start > this_monday or start < this_monday - timedelta(weeks=104):
            raise ValidationError({'week': 'Choose a Monday within the past two years, including the current week.'})
    else:
        start = this_monday - timedelta(weeks=1)
    end = min(start + timedelta(weeks=1), now)
    previous_start = start - timedelta(weeks=1)
    # Compare equal elapsed durations when the current week is incomplete.
    previous_end = previous_start + (end - start)
    month_start = (end - timedelta(microseconds=1)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    from .ai_snapshots import cohort_at
    current_data = current_cohort(user_ids, organization_id)
    eligible, cohort_quality, modules, mapping, history = cohort_at(start, user_ids, organization_id, current_data)
    codes = {m.code for m in modules}
    applications = {a for code in codes for a in mapping[code]}
    trend_start = start - timedelta(weeks=7)
    earliest = min(trend_start, month_start)
    logs = AIUsageLog.objects.filter(user_id__in=eligible, success=True, application__in=applications,
                                     timestamp__gte=earliest, timestamp__lt=end).exclude(provenance='client')
    # Distinct employee/day activity makes model fan-out and retries irrelevant to WAU.
    days = defaultdict(set)
    for uid, day in logs.order_by().annotate(day=TruncDate('timestamp', tzinfo=utc.utc)).values_list('user_id', 'day').distinct().iterator():
        days[uid].add(day)

    def active(a, b):
        return {uid for uid, values in days.items() if any(a.date() <= day <= (b - timedelta(microseconds=1)).date() for day in values)}

    current = active(start, end)
    previous = set(logs.filter(timestamp__gte=previous_start, timestamp__lt=previous_end).values_list('user_id', flat=True).distinct())
    monthly = active(month_start, end)
    retained = current & previous
    def pct(n, d):
        return round(n / d * 100, 2) if d else None
    def grouped(kind):
        groups = {}
        for uid, person in eligible.items():
            key = (person['organization_id'], person['department'] if kind == 'departments' else person['manager_id'])
            entry = groups.setdefault(key, {'id': '|'.join(key), 'name': person['department'] if kind == 'departments' else person['manager'],
                                           'organization': person['organization'], 'eligible': 0, 'wau': 0, 'mau': 0, 'previous_wau': 0, 'returning': 0})
            entry['eligible'] += 1
            entry['wau'] += uid in current
            entry['mau'] += uid in monthly
            entry['previous_wau'] += uid in previous
            entry['returning'] += uid in retained
        for row in groups.values():
            row['adoption_rate'] = pct(row['wau'], row['eligible'])
            row['repeat_rate'] = pct(row['returning'], row['previous_wau'])
        return sorted(groups.values(), key=lambda r: (r['adoption_rate'], r['organization'], r['name']))
    trend = []
    for n in range(8):
        a = trend_start + timedelta(weeks=n)
        b = min(a + timedelta(weeks=1), end)
        period_cohort, _, _, _, period_basis = cohort_at(a, user_ids, organization_id, current_data)
        count = AIUsageLog.objects.filter(user_id__in=period_cohort, success=True, application__in=applications, timestamp__gte=a, timestamp__lt=b).exclude(provenance='client').values('user_id').distinct().count()
        trend.append({'week_start': a.date().isoformat(), 'wau': count, 'eligible': len(period_cohort), 'adoption_rate': pct(count, len(period_cohort)), 'basis': period_basis['basis']})
    # Use complete UTC days so the 28-day score is comparable during a partial week.
    engagement_end = end.replace(hour=0, minute=0, second=0, microsecond=0)
    engagement_start = engagement_end - timedelta(days=28)
    application_modules = {a: code for code in codes for a in mapping[code]}
    used_modules = defaultdict(set)
    for uid, application in logs.filter(timestamp__gte=engagement_start, timestamp__lt=engagement_end).order_by().values_list('user_id', 'application').distinct():
        used_modules[uid].add(application_modules[application])
    engagement = []
    for uid, person in eligible.items():
        engagement.append({'user_id': str(uid), 'name': person['name'],
                           'organization_id': person['organization_id'],
                           'department': person['department'], 'organization': person['organization'],
                           **engagement_score(days[uid], used_modules[uid], person['grants'], engagement_start, engagement_end)})
    from .ai_measurement_service import maturity_indicators
    maturity_indicators(engagement, organization_id, engagement_end)
    engagement.sort(key=lambda r: (r['name'].casefold(), r['user_id']))
    from .ai_outcome_models import AIOutcomeEvidence
    from django.db.models import Sum, F
    outcomes = AIOutcomeEvidence.objects.filter(status='approved', created_at__gte=start, created_at__lt=end)
    if organization_id is not None:
        outcomes = outcomes.filter(organization_id=organization_id)
    elif user_ids is not None:
        organization_ids = UserProfile.objects.filter(user_id__in=user_ids).values_list('organization_id', flat=True)
        outcomes = outcomes.filter(organization_id__in=organization_ids)
    minutes = outcomes.filter(measurement='measured').aggregate(total=Sum(F('baseline_minutes') - F('ai_minutes') - F('review_minutes') - F('rework_minutes')))['total']
    return {
        'engagement': {'version': VERSION, 'start': engagement_start, 'end': engagement_end,
                       'people': engagement,
                       'methodology': '40% frequency (20 active days target), 30% entitled AI module breadth, 30% consistency (four active weeks). Trailing 28 complete UTC days. Entitlements follow the selected cohort basis. Scores describe observed engagement, not productivity or award eligibility. No observations means unknown; coverage is not established.'},
        'generated_at': now, 'scope': 'All organizations' if user_ids is None and organization_id is None else 'Your organization',
        'window': {'start': start, 'end': end, 'partial': end < start + timedelta(weeks=1), 'timezone': 'UTC',
                   'previous_start': previous_start, 'previous_end': previous_end, 'month_start': month_start},
        'totals': {'eligible': len(eligible), 'wau': len(current), 'mau': len(monthly), 'previous_wau': len(previous),
                   'returning': len(retained), 'weekly_adoption_rate': pct(len(current), len(eligible)),
                   'repeat_rate': pct(len(retained), len(previous)), 'no_observed_use': len(eligible) - len(current),
                   'average_active_days': round(sum(len({d for d in days[uid] if start.date() <= d <= (end - timedelta(microseconds=1)).date()}) for uid in current) / len(current), 2) if current else None},
        'departments': grouped('departments'), 'teams': grouped('teams'), 'trend': trend,
        'quality': {**cohort_quality, 'coverage_percent': None, 'snapshot': history,
                    'eligibility_basis': 'Current active accounts linked to active canonical employees, entitled to at least one configured active AI module. Explicitly excluded accounts are omitted.',
                    'history_basis': history['description'],
                    'usage_basis': 'Successful server or historical AI requests in a configured application. Browser-submitted telemetry is excluded. Legacy origin is not attested. Monthly and retention metrics use the cohort of the selected week.',
                    'missing_data': 'No observed use does not prove non-use: full pipeline coverage has not been established.',
                    'modules': [{'code': m.code, 'name': m.name, 'applications': mapping[m.code]} for m in modules]},
        'effectiveness': {'verified_hours_saved': round(minutes / 60, 2) if minutes is not None else None,
                          'verified_outcomes': outcomes.count(), 'value': None,
                          'reason': 'Approved evidence submitted in the selected week, within your organization scope. Measured hours exclude self-reported estimates and subtract review/rework. Collect and review pilot evidence in Productivity outcomes. Monetary value is not inferred.'},
    }
