"""Operational evidence for coverage, workflow frequency and business value."""
from datetime import timedelta
from decimal import Decimal
from django.db.models import Count, Max, Q
from django.utils import timezone
from .ai_cohort import DEFAULT_MODULES
from .ai_measurement_models import AIWorkflowRun, AIWorkforceSnapshot
from .ai_champion_models import AIUsageLog
from .ai_outcome_models import AIOutcomeEvidence

PILOTS = {
    'planning_package': {'name': 'Planning packages', 'paths': ['Document intelligence', 'Schedule generation'],
                         'boundary': 'Claude SDK calls in document intelligence and generation. Deterministic/cached outputs are not new AI use.'},
    'pid_analysis': {'name': 'P&ID analysis', 'paths': ['Upload with auto-analysis', 'Legacy analysis', 'Hybrid analysis'],
                     'boundary': 'OpenAI and Gemini SDK calls in drawing analysis and verification. Equipment and instrument extraction are separate workflows outside this pilot.'},
}

ADAPTERS = {
    **PILOTS,
    'pfd_to_pid': {'name': 'PFD to P&ID', 'paths': ['Upload and analysis', 'Conversion and verification', 'Drawing generation'],
        'boundary': 'OpenAI SDK calls in conversion requests. Cached and programmatic drawing output alone is not new AI use.'},
    'designiq': {'name': 'DesignIQ', 'paths': ['P&ID processing jobs', 'Base extraction: Celery and thread fallback', 'Enriched uploads'],
        'boundary': 'OpenAI SDK calls attributed to the submitting employee. Temporary extraction responses are marked returned; only persisted outputs support linked evidence.'},
    'crs_documents': {'name': 'CRS documents', 'paths': ['Process PDF comments', 'Extract comments', 'Upload and process'],
        'boundary': 'OpenAI comment-cleaning calls. Delivered spreadsheets and JSON responses are not persisted outcome evidence.'},
    'pfd_quality': {'name': 'PFD Quality', 'paths': ['PDF extraction', 'OCR fallback', 'Quality rules'],
        'boundary': 'The current checker uses PDF extraction, local OCR and deterministic rules. No provider SDK calls are implemented; processing is not counted as generative AI use.',
        'status': 'Rules / OCR; no provider calls'},
}


def scoped_runs(organization_id=None):
    rows = AIWorkflowRun.objects.all()
    return rows.filter(organization_id=organization_id) if organization_id is not None else rows


def reconcile_outputs(module, scope, start, organization_id):
    """Compare surviving persisted pilot outputs after instrumentation began."""
    from django.apps import apps
    from django.db.models import CharField
    from django.db.models.functions import Cast
    sources = {
        'planning_package': ('planning_intelligence.PlanningGeneration', 'created_at', 'generated_by'),
        'pid_analysis': ('pid_analysis.PIDAnalysisReport', 'generated_at', 'pid_drawing__uploaded_by'),
        'pfd_to_pid': ('pfd_converter.PIDConversion', 'created_at', 'converted_by'),
        'designiq': ('designiq.ProcessedPIDOutput', 'processing_date', 'processed_by'),
    }
    if module not in sources:
        return {'status': 'Response only; no persisted output' if module == 'crs_documents' else 'Not applicable: rules / OCR' if module == 'pfd_quality' else 'Not instrumented', 'outputs': None, 'unlinked_outputs': None, 'since': None}
    began = scope.filter(module=module).order_by('started_at').values_list('started_at', flat=True).first()
    if not began:
        return {'status': 'Awaiting first pilot run', 'outputs': None, 'unlinked_outputs': None, 'since': None}
    label, time_field, owner = sources[module]
    since = max(began, start)
    outputs = apps.get_model(label).objects.filter(**{f'{time_field}__gte': since})
    if module == 'pfd_to_pid':
        outputs = outputs.filter(status__in=['completed', 'approved'])
    if module == 'designiq':
        outputs = outputs.filter(edited_from__isnull=True)
    if organization_id is not None:
        outputs = outputs.filter(**{f'{owner}__rbac_profile__organization_id': organization_id})
    linked = scope.filter(module=module, source_type=label).values('source_id')
    missing = outputs.annotate(source_key=Cast('pk', CharField())).exclude(source_key__in=linked).count()
    count = outputs.count()
    return {'status': 'Missing workflow links' if missing else 'Observed outputs linked' if count else 'No persisted outputs to compare',
            'outputs': count, 'unlinked_outputs': missing, 'since': since}


def monetary_values(outcomes):
    """Never add currencies or mix measured and self-reported observations."""
    totals = {}
    for row in outcomes.filter(status='approved', hourly_rate__isnull=False):
        key = (row.value_currency, row.measurement)
        totals[key] = totals.get(key, Decimal('0')) + Decimal(row.saved_minutes) / 60 * row.hourly_rate
    return [{'currency': currency, 'measurement': measurement, 'estimated_capacity_value': str(value.quantize(Decimal('.01')))}
            for (currency, measurement), value in sorted(totals.items())]


def measurement_report(days=30, organization_id=None, page=1, module='', search='', state='', now=None):
    now = now or timezone.now()
    start = now - timedelta(days=days)
    scope = scoped_runs(organization_id)
    runs = scope.filter(started_at__gte=start, started_at__lt=now)
    requests = AIUsageLog.objects.filter(provenance='server', workflow__in=runs)
    with_ai = runs.filter(requests__provenance='server').distinct()
    completed = with_ai.filter(status='completed')
    active_users = with_ai.values('user_id').distinct().count()
    sessions = with_ai.values('session_id').distinct().count()
    coverage = []
    from django.conf import settings
    for code in getattr(settings, 'AI_ADOPTION_MODULE_APPLICATIONS', DEFAULT_MODULES):
        pilot = ADAPTERS.get(code)
        row_runs = runs.filter(module=code)
        count = row_runs.count()
        linked = row_runs.exclude(source_id='').count()
        observed = requests.filter(workflow__module=code)
        coverage.append({'module': code, 'name': pilot['name'] if pilot else code.replace('_', ' ').title(),
                         'status': pilot.get('status', 'Instrumented pilot' if code in PILOTS else 'Instrumented') if pilot else 'Not instrumented',
                         'paths': pilot['paths'] if pilot else [], 'boundary': pilot['boundary'] if pilot else 'No verified workflow adapter is registered.',
                         'workflows': count, 'linked_outputs': linked, 'sdk_calls': observed.count(),
                         'last_request': observed.aggregate(last=Max('timestamp'))['last'],
                         'pricing_missing': observed.exclude(pricing_recorded=True).count(),
                         'reconciliation': reconcile_outputs(code, scope, start, organization_id)})
    filtered = runs.select_related('user').annotate(sdk_calls=Count('requests', filter=Q(requests__provenance='server')),
        successful_calls=Count('requests', filter=Q(requests__provenance='server', requests__success=True)),
        recorded_outcomes=Count('outcome', distinct=True))
    if module:
        filtered = filtered.filter(module=module)
    if state:
        filtered = filtered.filter(status=state)
    if search:
        filtered = filtered.filter(Q(user__email__icontains=search) | Q(user__first_name__icontains=search) | Q(user__last_name__icontains=search) | Q(source_id__icontains=search))
    snapshots = AIWorkforceSnapshot.objects.all()
    outcomes = AIOutcomeEvidence.objects.filter(created_at__gte=start, created_at__lt=now)
    if organization_id is not None:
        snapshots = snapshots.filter(organization_id=organization_id)
        outcomes = outcomes.filter(organization_id=organization_id)
    latest_snapshot = snapshots.aggregate(last=Max('captured_at'))['last']
    from .models import Organization
    from .ai_snapshots import cohort_at
    fresh = snapshots.filter(captured_at__gte=now - timedelta(hours=36)).values('organization_id').distinct().count()
    organizations = 1 if organization_id is not None else Organization.objects.count()
    eligible = len(cohort_at(start, organization_id=organization_id)[0])
    return {'window': {'start': start, 'end': now}, 'count': filtered.count(), 'page': page,
        'totals': {'workflows': runs.count(), 'ai_workflows': with_ai.count(), 'completed_ai_workflows': completed.count(),
                   'sdk_calls': requests.count(), 'successful_sdk_calls': requests.filter(success=True).count(),
                   'active_users': active_users, 'derived_sessions': sessions,
                   'sessions_per_active_employee_per_week': round(sessions / active_users / (days / 7), 2) if active_users else None,
                   'eligible_employees': eligible,
                   'sessions_per_eligible_employee_per_week': round(sessions / eligible / (days / 7), 2) if eligible else None,
                   'stalled_workflows': runs.filter(status='running', started_at__lt=now - timedelta(hours=6)).count(),
                   'input_submissions': runs.count(), 'user_prompts': None,
                   'verified_outcomes': outcomes.filter(status='approved').count()},
        'coverage': coverage, 'latest_snapshot': latest_snapshot,
        'snapshot_stale': latest_snapshot is None or fresh < organizations,
        'snapshot_fresh_organizations': fresh, 'snapshot_organizations': organizations,
        'estimated_values': monetary_values(outcomes),
        'results': [{'id': str(r.pk), 'user': r.user.get_full_name() or r.user.email, 'module': r.module, 'operation': r.operation,
                     'status': r.status, 'started_at': r.started_at, 'finished_at': r.finished_at, 'session_id': str(r.session_id),
                     'source_type': r.source_type, 'source_id': r.source_id, 'sdk_calls': r.sdk_calls,
                     'can_record_outcome': r.module in PILOTS and r.status == 'completed' and bool(r.successful_calls and r.source_id) and not r.recorded_outcomes,
                     'outcome_recorded': bool(r.recorded_outcomes)} for r in filtered[(page-1)*10:page*10]],
        'methodology': {'version': 'pilot-workflow-v1',
            'workflows': 'Server-owned pipeline submissions. Reused idempotency keys within the same user, module and operation count once. Completed means output persisted, not quality accepted.',
            'sessions': 'Derived from pilot workflow activity with a 30-minute inactivity boundary, scoped to employee and organization. Not login sessions or measured working time.',
            'requests': 'One recorded SDK operation; SDK-internal retries are not individually observable. Generated messages and fan-out are not employee prompts. Streaming operations are not measured by these adapters.',
            'coverage': 'Adapter coverage is not workforce reporting coverage. No percentage is inferred from the number of installed adapters. Legacy/client records do not enter these operational totals.',
            'value': 'Estimated capacity value = reviewed net hours × supplied hourly rate, grouped by currency and measurement source. It is not realized revenue, cash savings or ROI.'}}


def maturity_indicators(people, organization_id=None, as_of=None):
    """Usage levels 1–3 are observed; levels 4–5 require reviewed evidence."""
    outcomes = AIOutcomeEvidence.objects.filter(status='approved', workflow__isnull=False,
        workflow__user_id__in=[p['user_id'] for p in people]).exclude(contribution_type='task')
    if as_of is not None:
        outcomes = outcomes.filter(reviewed_at__lt=as_of)
    if organization_id is not None:
        outcomes = outcomes.filter(organization_id=organization_id)
    evidence = {}
    organizations = {str(p['user_id']): p.get('organization_id') for p in people}
    for uid, kind, org in outcomes.values_list('workflow__user_id', 'contribution_type', 'organization_id'):
        if organizations.get(str(uid)) != str(org):
            continue
        evidence[str(uid)] = max(evidence.get(str(uid), 0), 5 if kind == 'automation_creator' else 4)
    for person in people:
        level = (3 if person['active_days'] >= 16 and person['active_weeks'] == 4 else
                 2 if person['active_days'] >= 4 and person['active_weeks'] >= 2 else 1 if person['active_days'] else None)
        person['maturity_level'] = max(level or 0, evidence[str(person['user_id'])]) if str(person['user_id']) in evidence else level
        person['maturity_basis'] = 'Reviewed workflow contribution' if str(person['user_id']) in evidence else 'Observed usage pattern' if level else 'Insufficient coverage to assert level 0'
