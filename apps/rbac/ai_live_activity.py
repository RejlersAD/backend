"""Read-only operational signals. Recent activity is not presence or productivity."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Case, CharField, Count, Exists, F, IntegerField, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Coalesce, Greatest
from django.utils import timezone
from rest_framework.exceptions import NotFound

from .ai_champion_models import ActivityEvent, AIUsageLog
from .ai_measurement_models import AIWorkflowRun


def live_activity(organization_id=None, search='', state='', page=1, page_size=10, selected_user=None, now=None):
    now = now or timezone.now()
    start, recent = now - timedelta(hours=24), now - timedelta(minutes=5)
    people = get_user_model().objects.filter(rbac_profile__is_deleted=False)
    workflows = AIWorkflowRun.objects.filter(started_at__lt=now)
    calls = AIUsageLog.objects.filter(provenance='server', workflow__isnull=False, timestamp__gte=start, timestamp__lt=now)
    if organization_id is not None:
        people = people.filter(rbac_profile__organization_id=organization_id)
        workflows = workflows.filter(organization_id=organization_id)
        calls = calls.filter(workflow__organization_id=organization_id)
    events = ActivityEvent.objects.filter(user_id__in=people.values('pk'), timestamp__gte=start, timestamp__lt=now)
    scoped_people = people
    if search:
        people = people.filter(Q(first_name__icontains=search) | Q(last_name__icontains=search) | Q(email__icontains=search))
    event = events.filter(user_id=OuterRef('pk')).order_by('-timestamp', '-pk')
    call = calls.filter(user_id=OuterRef('pk')).order_by('-timestamp', '-pk')
    workflow = workflows.filter(user_id=OuterRef('pk')).filter(Q(started_at__gte=start) | Q(finished_at__gte=start)).annotate(observed=Coalesce('finished_at', 'started_at')).order_by('-observed', '-pk')
    running = workflows.filter(user_id=OuterRef('pk'), status='running', started_at__gte=now - timedelta(hours=6)).order_by('-started_at')
    stale = workflows.filter(user_id=OuterRef('pk'), status='running', started_at__lt=now - timedelta(hours=6)).order_by('-started_at')

    def count(query):
        return Coalesce(Subquery(query.order_by().values('user_id').annotate(n=Count('pk')).values('n')), 0, output_field=IntegerField())

    people = people.annotate(
        last_event=Subquery(event.values('timestamp')[:1]), event_module=Subquery(event.values('application')[:1]),
        event_action=Subquery(event.values('action_type')[:1]), event_feature=Subquery(event.values('feature')[:1]),
        last_call=Subquery(call.values('timestamp')[:1]), call_module=Subquery(call.values('application')[:1]),
        last_workflow=Subquery(workflow.values('observed')[:1]),
        workflow_module=Subquery(workflow.values('module')[:1]), workflow_operation=Subquery(workflow.values('operation')[:1]),
        workflow_state=Subquery(workflow.values('status')[:1]),
        running=Exists(running), stalled=Exists(stale), running_module=Subquery(running.values('module')[:1]),
        running_operation=Subquery(running.values('operation')[:1]),
        stale_module=Subquery(stale.values('module')[:1]), stale_operation=Subquery(stale.values('operation')[:1]),
        events_24h=count(event), calls_24h=count(call), failed_calls_24h=count(call.filter(success=False)),
    ).annotate(last_signal=Greatest('last_event', 'last_call', 'last_workflow')).annotate(
        signal_state=Case(When(running=True, then=Value('processing')), When(stalled=True, then=Value('attention')),
                         When(last_signal__gte=recent, then=Value('recent')), default=Value('quiet'), output_field=CharField()))
    # Summary reflects search, before the state filter; each state is mutually exclusive.
    summary = people.aggregate(total=Count('pk'), processing=Count('pk', filter=Q(signal_state='processing')),
                               recent=Count('pk', filter=Q(signal_state='recent')), attention=Count('pk', filter=Q(signal_state='attention')))
    summary['quiet'] = summary['total'] - summary['processing'] - summary['recent'] - summary['attention']
    if state:
        people = people.filter(signal_state=state)
    total = people.count()
    page = min(page, max(1, (total + page_size - 1) // page_size))
    rows = people.annotate(priority=Case(When(signal_state='processing', then=0), When(signal_state='attention', then=1),
                                        When(signal_state='recent', then=2), default=3, output_field=IntegerField()))
    rows = rows.order_by('priority', F('last_signal').desc(nulls_last=True), 'last_name', 'first_name', 'pk')[(page - 1) * page_size:page * page_size]
    results = []
    for user in rows:
        event_action = ': '.join(filter(None, [user.event_feature, user.event_action]))
        candidates = [(user.last_event, user.event_module, event_action, 'Submitted activity'),
                      (user.last_call, user.call_module, 'AI request finished', 'Server AI request'),
                      (user.last_workflow, user.workflow_module, user.workflow_operation, 'Server workflow')]
        latest = max((r for r in candidates if r[0]), key=lambda r: r[0], default=(None, '', '', 'No observation'))
        results.append({'id': str(user.pk), 'name': user.get_full_name() or user.email, 'email': user.email,
                        'account_enabled': user.is_active, 'state': user.signal_state, 'last_signal_at': latest[0],
                        'module': user.running_module if user.running else user.stale_module if user.stalled else latest[1],
                        'action': user.running_operation if user.running else user.stale_operation if user.stalled else latest[2],
                        'source': 'Server workflow' if user.running or user.stalled else latest[3], 'events_24h': user.events_24h,
                        'ai_calls_24h': user.calls_24h, 'failed_ai_calls_24h': user.failed_calls_24h})
    detail = None
    if selected_user:
        user = scoped_people.filter(pk=selected_user).first()
        if user is None:
            raise NotFound('User is not available in this reporting scope.')
        timeline = []
        for r in events.filter(user=user).order_by('-timestamp', '-pk')[:20]:
            timeline.append({'id': f'event:{r.pk}', 'timestamp': r.timestamp, 'module': r.application,
                             'action': ': '.join(filter(None, [r.feature, r.action_type])), 'source': 'Submitted activity', 'status': 'recorded'})
        for r in calls.filter(user=user).order_by('-timestamp', '-pk')[:20]:
            timeline.append({'id': f'call:{r.pk}', 'timestamp': r.timestamp, 'module': r.application,
                             'action': 'AI request', 'source': 'Server AI request', 'status': 'succeeded' if r.success else 'failed'})
        for r in workflows.filter(user=user).filter(Q(started_at__gte=start) | Q(finished_at__gte=start) | Q(status='running')).annotate(observed=Coalesce('finished_at', 'started_at')).order_by('-observed', '-pk')[:20]:
            timeline.append({'id': f'workflow:{r.pk}', 'timestamp': r.finished_at or r.started_at, 'module': r.module,
                             'action': r.operation, 'source': 'Server workflow',
                             'status': 'needs_check' if r.status == 'running' and r.started_at < now - timedelta(hours=6) else r.status})
        timeline.sort(key=lambda r: (r['timestamp'], r['id']), reverse=True)
        detail = {'id': str(user.pk), 'name': user.get_full_name() or user.email, 'email': user.email,
                  'timeline': timeline[:20], 'limit': 20}
    return {'generated_at': now, 'window_start': start, 'refresh_seconds': 15, 'recent_minutes': 5,
            'scope': 'All organizations' if organization_id is None else 'Your organization',
            'summary': summary, 'count': total, 'page': page, 'page_size': page_size, 'results': results, 'selected': detail}
