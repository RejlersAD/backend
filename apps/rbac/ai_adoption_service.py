"""Read-only adoption reporting over the existing telemetry tables.

Recorded activity is not evidence of business outcomes or provider billing.
No scoring rules or historical awards are changed by opening this dashboard.
"""
from datetime import timedelta, timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.db.models import Avg, Count, F, Max, Q, Sum, Window
from django.db.models.functions import RowNumber, TruncDate

from .ai_champion_models import ActivityEvent, AIUsageLog, AIPricingConfig, MonthlyChampion
from .ai_champion_service import compute_scores, SCORING_WEIGHTS


def _person(user):
    return {'id': user.pk, 'name': user.get_full_name() or user.email, 'email': user.email}


def adoption_dashboard(start, end, user_ids=None):
    activity_all = ActivityEvent.objects.all()
    usage_all = AIUsageLog.objects.all()
    awards = MonthlyChampion.objects.all()
    if user_ids is not None:
        activity_all = activity_all.filter(user_id__in=user_ids)
        usage_all = usage_all.filter(user_id__in=user_ids)
        awards = awards.filter(user_id__in=user_ids)
    activity = activity_all.filter(timestamp__gte=start, timestamp__lt=end)
    usage = usage_all.filter(timestamp__gte=start, timestamp__lt=end)
    events = activity.aggregate(events=Count('id'), views=Count('id', filter=Q(action_type='view')),
                                users=Count('user_id', distinct=True), latest=Max('timestamp'))
    totals = usage.aggregate(requests=Count('id'), users=Count('user_id', distinct=True),
                             successful=Count('id', filter=Q(success=True)),
                             tokens=Sum('total_tokens'), cost=Sum('cost_usd'),
                             latency=Avg('latency_ms', filter=Q(latency_ms__gt=0)), latest=Max('timestamp'))
    from .radai_activity import activity_days
    observations = activity_days(start, end, user_ids)
    radai = {'users': len({r['user_id'] for r in observations}),
             'activities': sum(r['count'] for r in observations),
             'modules': len({r['application'] for r in observations}),
             'basis': 'Recorded RADAI activity, including page visits; workflows and non-client provider telemetry fill missing user/module/day observations.'}
    requests = totals['requests']
    tracked_users = activity.order_by().values_list('user_id', flat=True).union(
        usage.order_by().values_list('user_id', flat=True)).count()

    # Missing pricing is a coverage signal, not proof that a stored cost is wrong.
    prices = set(AIPricingConfig.objects.filter(is_active=True, effective_from__lte=end)
                 .values_list('provider', 'model_name'))
    def cost_rows(fields):
        rows = list(usage.order_by().values(*fields).annotate(
            requests=Count('id'), successful=Count('id', filter=Q(success=True)),
            users=Count('user_id', distinct=True), tokens=Sum('total_tokens'), cost=Sum('cost_usd'))
            .order_by('-cost', *fields))
        for row in rows:
            row['recorded_cost_usd'] = float(row.pop('cost') or 0)
            row['tokens'] = row['tokens'] or 0
            row['success_rate'] = round(row['successful'] / row['requests'] * 100, 2)
        return rows
    models = cost_rows(['provider', 'model_name'])
    for model in models:
        model['pricing_configured'] = (model['provider'], model['model_name']) in prices
    missing_prices = sum(row['requests'] for row in models if not row['pricing_configured'])

    # The contribution register excludes browsing events. Existing request logs
    # do not attest server origin, deduplication, accepted outputs or approvals.
    contribution_query = usage.order_by().values('user_id', 'application', 'provider', 'model_name').annotate(
        requests=Count('id'), successful=Count('id', filter=Q(success=True)),
        tokens=Sum('total_tokens'), cost=Sum('cost_usd'), last_activity=Max('timestamp'))
    contribution_count = contribution_query.count()
    contributions = list(contribution_query.order_by('-last_activity', 'user_id', 'application', 'provider', 'model_name')[:2000])
    contributor_people = {u.pk: _person(u) for u in get_user_model().objects.filter(
        pk__in={r['user_id'] for r in contributions})}
    samples = {}
    recent_requests = usage.annotate(sample_rank=Window(
        expression=RowNumber(), partition_by=[F('user_id'), F('application'), F('provider'), F('model_name')],
        order_by=[F('timestamp').desc(), F('id').desc()])).filter(sample_rank__lte=3)
    for row in recent_requests.values('user_id', 'application', 'provider', 'model_name', 'request_id', 'timestamp', 'success'):
        key = (row['user_id'], row['application'], row['provider'], row['model_name'])
        samples.setdefault(key, []).append({
            'request_id': row['request_id'] or None, 'timestamp': row['timestamp'], 'success': row['success']})
    for row in contributions:
        key = (row['user_id'], row['application'], row['provider'], row['model_name'])
        row.update(user=contributor_people.get(row['user_id']),
                   recorded_cost_usd=float(row.pop('cost') or 0),
                   pricing_configured=(row['provider'], row['model_name']) in prices,
                   evidence=samples.get(key, []), eligibility='not_evaluated', verified_outcomes=None)

    apps = {}
    for row in activity.order_by().values('application').annotate(
        events=Count('id'), views=Count('id', filter=Q(action_type='view')),
        activity_users=Count('user_id', distinct=True), last_activity=Max('timestamp')):
        apps[row['application']] = row
    for row in cost_rows(['application']):
        apps.setdefault(row['application'], {'application': row['application']}).update(row)
    applications = []
    for app, row in apps.items():
        applications.append({
            'application': app or 'Unspecified', 'events': row.get('events', 0),
            'page_views': row.get('views', 0),
            'other_events': row.get('events', 0) - row.get('views', 0),
            'activity_users': row.get('activity_users', 0), 'ai_users': row.get('users', 0),
            'requests': row.get('requests', 0), 'successful': row.get('successful', 0),
            'success_rate': row.get('success_rate'), 'tokens': row.get('tokens', 0),
            'recorded_cost_usd': row.get('recorded_cost_usd'),
            'last_activity': row.get('last_activity'),
            'telemetry_status': 'ai_recorded' if row.get('requests') else 'activity_only',
        })
    applications.sort(key=lambda row: (-row['requests'], -row['events'], row['application']))

    activity_daily = {r['day']: r for r in activity.order_by().annotate(day=TruncDate('timestamp', tzinfo=dt_timezone.utc))
                      .values('day').annotate(events=Count('id'), views=Count('id', filter=Q(action_type='view')))}
    ai_daily = {r['day']: r for r in usage.order_by().annotate(day=TruncDate('timestamp', tzinfo=dt_timezone.utc))
                .values('day').annotate(requests=Count('id'), successful=Count('id', filter=Q(success=True)))}
    daily = []
    day = start.astimezone(dt_timezone.utc).date()
    while day <= end.astimezone(dt_timezone.utc).date():
        a, u = activity_daily.get(day, {}), ai_daily.get(day, {})
        daily.append({'date': day.isoformat(), 'page_views': a.get('views', 0),
                      'other_events': a.get('events', 0) - a.get('views', 0),
                      'requests': u.get('requests', 0), 'successful': u.get('successful', 0)})
        day += timedelta(days=1)

    rankings = compute_scores(start, end, user_ids=user_ids)
    month_start = end.astimezone(dt_timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    candidates = [row for row in compute_scores(month_start, end, user_ids=user_ids)
                  if row['stats']['total_ai_requests'] > 0][:3]
    latest = awards.order_by('-period_year', '-period_month', 'rank').first()
    podium = list(awards.filter(period_year=latest.period_year, period_month=latest.period_month)
                  .select_related('user').order_by('rank')) if latest else []
    history = list(awards.filter(rank=1).select_related('user').order_by('-period_year', '-period_month')[:12])
    people = {u.pk: _person(u) for u in get_user_model().objects.filter(
        pk__in={r['user_id'] for r in rankings[:100] + candidates})}
    for rank, row in enumerate(rankings[:100], start=1):
        row.update(rank=rank, user=people.get(row['user_id']))
    for rank, row in enumerate(candidates, start=1):
        row.update(rank=rank, user=people.get(row['user_id']))
    def award(row):
        return {'id': str(row.id), 'year': row.period_year, 'month': row.period_month,
                'rank': row.rank, 'user': _person(row.user), 'score': row.champion_score,
                'tier': row.badge_tier, 'citation': row.citation}

    flags = []
    if not requests:
        flags.append({'code': 'no_ai_usage', 'title': 'No AI requests recorded in this period',
                      'detail': 'This does not establish zero AI usage or zero provider spending.'})
    if missing_prices:
        flags.append({'code': 'pricing_gaps', 'title': 'Pricing configuration is incomplete',
                      'detail': f'{missing_prices} requests use models without an active pricing entry. Stored costs are not reconciled invoices.'})
    activity_only = sum(a['telemetry_status'] == 'activity_only' for a in applications)
    if activity_only:
        flags.append({'code': 'activity_only', 'title': 'Some applications report activity only',
                      'detail': f'{activity_only} applications have activity events but no AI request records in this period. RADAI activity still counts toward adoption and champion eligibility; provider calls are separate technical evidence.'})
    return {
        'window': {'start': start, 'end': end, 'timezone': 'UTC'}, 'generated_at': end,
        'radai': radai,
        'totals': {'tracked_users': tracked_users, 'activity_users': events['users'],
                   'ai_users': totals['users'], 'events': events['events'], 'page_views': events['views'],
                   'other_events': events['events'] - events['views'], 'requests': requests,
                   'successful_requests': totals['successful'], 'failed_requests': requests - totals['successful'],
                   'success_rate': round(totals['successful'] / requests * 100, 2) if requests else None,
                   'tokens': totals['tokens'] or 0, 'recorded_cost_usd': float(totals['cost'] or 0) if requests else None,
                   'avg_latency_ms': round(totals['latency'], 2) if totals['latency'] is not None else None},
        'applications': applications, 'daily': daily, 'providers': cost_rows(['provider']), 'models': models,
        'contributions': {'results': contributions, 'count': contribution_count, 'limit': 2000,
                          'basis': 'AI usage logs only; browsing excluded. Server origin and deduplication are not attested.'},
        'leaderboard': {'count': len(rankings), 'results': rankings[:100], 'limit': 100,
                        'weights': SCORING_WEIGHTS, 'scoring_version': 'legacy-engagement-v1'},
        'recognition': {'current_period': {'year': month_start.year, 'month': month_start.month},
                        'candidates': candidates, 'latest_podium': [award(r) for r in podium],
                        'history': [award(r) for r in history], 'publication_workflow_available': True},
        'quality': {'flags': flags, 'last_activity_at': activity_all.aggregate(v=Max('timestamp'))['v'],
                    'last_ai_usage_at': usage_all.aggregate(v=Max('timestamp'))['v'],
                    'applications_with_ai_records': sum(a['requests'] > 0 for a in applications),
                    'applications_with_activity_only': activity_only,
                    'requests_without_active_pricing': missing_prices,
                    'billing_reconciled': False, 'pipeline_coverage_percent': None,
                    'verified_business_outcomes_available': False,
                    'telemetry_provenance': 'Submitted telemetry; server origin is not attested for historical records.'},
    }
