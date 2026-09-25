"""Durable, idempotent orchestration for planning workloads."""
from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.db import close_old_connections
from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import PlanningJob


logger = logging.getLogger(__name__)
_local_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='planning-job-fallback')


def canonical_fingerprint(payload):
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def generation_fingerprint(project, request_data):
    from .document_intelligence import ENGINE_VERSION

    basis = project.schedule_bases.filter(is_deleted=False, status='approved').first()
    plan = project.generation_plans.filter(is_deleted=False, status='approved', basis=basis).first() if basis else None
    configuration = getattr(project, 'schedule_configuration', None)
    options = (request_data or {}).get('generation_options') or {}
    package_inputs = None
    if isinstance(options, dict) and options.get('mode') == 'planning_package':
        from ..models import DocumentIntelligenceRun, WorkflowTemplate, WorkflowStage, ProjectScheduleConfiguration
        configuration = ProjectScheduleConfiguration.objects.filter(project=project, is_deleted=False).first()
        from .preview_confirmation import review_fingerprint
        from .planning_package_request import package_preview_selection
        from .register_geometry_cache import cached_register_geometry
        run = DocumentIntelligenceRun.objects.filter(
            pk=options.get('intelligence_run_id'), project=project, is_deleted=False,
        ).first()
        review_token = review_fingerprint(run) if run else None
        calendar = project.work_calendars.filter(is_deleted=False, is_default=True).order_by('pk').first()
        overrides = list(configuration.overrides.filter(is_deleted=False, is_active=True).order_by('pk').values()) if configuration else []
        template_ids = {row['workflow_template_id'] for row in overrides}
        if configuration:
            template_ids.add(configuration.workflow_template_id)
        else:
            template_ids.update(WorkflowTemplate.objects.filter(
                project__isnull=True, is_system=True, is_default=True, status='active', is_deleted=False,
            ).order_by('-version').values_list('pk', flat=True)[:1])
        package_inputs = {
            'policy': 'planning-package-proposal/1',
            'analysis_run_id': run.pk if run else None,
            'review_fingerprint': review_token,
            'confirmed_preview': package_preview_selection(run, review_token=review_token) if run else None,
            'source_manifest': (run.summary or {}).get('extraction_source_manifest') if run else None,
            'register_geometry': [
                {'file_id': source.pk, 'fingerprint': canonical_fingerprint(geometry)}
                for source in project.files.filter(is_deleted=False).select_related('document_profile').order_by('pk')
                if (geometry := cached_register_geometry(source))
            ],
            'calendar': ({'id': calendar.pk, 'working_weekdays': calendar.working_weekdays,
                          'hours_per_day': calendar.hours_per_day, 'timezone': calendar.timezone} if calendar else None),
            'calendar_exceptions': list(calendar.exceptions.filter(is_deleted=False).order_by('pk').values()) if calendar else [],
            'calendar_overrides': project.calendar_overrides,
            'workflow_templates': list(WorkflowTemplate.objects.filter(pk__in=template_ids).order_by('pk').values()),
            'workflow_stages': list(WorkflowStage.objects.filter(template_id__in=template_ids).order_by('pk').values()),
            'workflow_overrides': overrides,
            'configuration_updated_at': getattr(configuration, 'updated_at', None),
        }
    files = list(project.files.filter(is_deleted=False, parse_status='done').order_by('id').values(
        'id', 'updated_at', 'size_bytes', 'confidence_score',
    ))
    return canonical_fingerprint({
        'operation': 'generate-v6-document-evidence', 'project_id': project.id,
        'engine_version': ENGINE_VERSION,
        'project_updated_at': project.updated_at, 'basis_id': getattr(basis, 'id', None),
        'basis_updated_at': getattr(basis, 'updated_at', None), 'plan_id': getattr(plan, 'id', None),
        'plan_updated_at': getattr(plan, 'updated_at', None),
        'configuration_version': getattr(configuration, 'configuration_version', None),
        'files': files, 'request_data': request_data or {},
        **({'planning_package': package_inputs} if package_inputs is not None else {}),
    })


def operation_fingerprint(project, job_type, request_data):
    if job_type in {'generate', 'preview'}:
        return generation_fingerprint(project, request_data)
    if job_type == 'analyze':
        # A completed job from an older extractor must not hide corrected
        # evidence when the planner runs Document Intelligence again.
        from .document_intelligence import ENGINE_VERSION
        from .pdf_register_geometry import GEOMETRY_VERSION
        from ..config import (
            CLAUDE_MAX_INPUT_CHARS, CLAUDE_INTELLIGENCE_MAX_TOKENS,
            AI_MIN_CHUNK_CHARS, AI_MAX_SPLIT_DEPTH, AI_MAX_CALLS_PER_PASS,
        )

        files = list(project.files.filter(is_deleted=False, parse_status='done').order_by('id').values(
            'id', 'updated_at', 'size_bytes', 'confidence_score',
        ))
        return canonical_fingerprint({
            'operation': 'analyze-v8-register-geometry-provider-recovery', 'project_id': project.id,
            'engine_version': ENGINE_VERSION,
            'register_geometry_version': GEOMETRY_VERSION,
            'chunk_policy': {
                'input_chars': CLAUDE_MAX_INPUT_CHARS, 'output_tokens': CLAUDE_INTELLIGENCE_MAX_TOKENS,
                'minimum_chars': AI_MIN_CHUNK_CHARS, 'maximum_depth': AI_MAX_SPLIT_DEPTH,
                'call_budget': AI_MAX_CALLS_PER_PASS,
            },
            'project_updated_at': project.updated_at, 'files': files,
        })
    return canonical_fingerprint({
        'operation': f'{job_type}-v4', 'project_id': project.id, 'request_data': request_data or {},
    })


def generation_plan_build_fingerprint(basis):
    """Identify the exact approved basis content used to build a Generation Plan."""
    deliverables = list(basis.deliverables.filter(is_deleted=False).order_by('id').values(
        'id', 'canonical_name', 'document_number', 'discipline', 'status',
        'source_references', 'updated_at',
    ))
    source_files = list(basis.project.files.filter(
        id__in=basis.source_run.source_file_ids, is_deleted=False,
    ).order_by('id').values('id', 'parse_status', 'updated_at'))
    return canonical_fingerprint({
        'operation': 'build-generation-plan-v5-document-evidence', 'project_id': basis.project_id,
        'basis_id': basis.id, 'basis_version': basis.version, 'basis_status': basis.status,
        'basis_updated_at': basis.updated_at, 'deliverables': deliverables, 'source_files': source_files,
    })


def workable_plan_fingerprint(project, request_data):
    run = project.intelligence_runs.filter(is_deleted=False, status='succeeded').first()
    basis = project.schedule_bases.filter(is_deleted=False).first()
    files = list(project.files.filter(is_deleted=False).order_by('id').values(
        'id', 'parse_status', 'updated_at', 'size_bytes',
    ))
    return canonical_fingerprint({
        'operation': 'workable-plan-v1', 'project_id': project.id, 'project_updated_at': project.updated_at,
        'run_id': getattr(run, 'id', None), 'run_updated_at': getattr(run, 'updated_at', None),
        'basis_id': getattr(basis, 'id', None), 'basis_updated_at': getattr(basis, 'updated_at', None),
        'files': files, 'request_data': request_data or {},
    })


def schedule_state_fingerprint(version):
    from ..evidence_models import EvidenceGraph
    from .evidence_graph import input_fingerprint
    from .planning_boundaries import BOUNDARY_RULE_VERSION, CALCULATION_RULE_VERSION
    from ..schedule_serializers import WorkCalendarSerializer
    activities = list(version.activities.filter(is_deleted=False).order_by('id').values(
        'id', 'external_id', 'duration_days', 'calendar_id', 'constraint_type', 'constraint_date', 'metadata',
    ))
    relationships = list(version.relationships.filter(is_deleted=False).order_by('id').values(
        'id', 'predecessor_id', 'successor_id', 'relationship_type', 'lag_days', 'updated_at',
    ))
    schedule = version.schedule
    from .planning_package_boundary import package_context
    proposal = package_context(version)
    default_calendar = proposal['calendar'] if proposal else schedule.default_calendar
    calendar_ids = {row['calendar_id'] for row in activities if row['calendar_id'] is not None}
    if default_calendar:
        calendar_ids.add(default_calendar.pk)
    calendars = WorkCalendarSerializer(schedule.project.work_calendars.filter(pk__in=calendar_ids).order_by('pk'), many=True).data
    graph = EvidenceGraph.objects.filter(project=schedule.project).first()
    current_sources = input_fingerprint(schedule.project)
    return canonical_fingerprint({
        'operation': 'calculate-v5-evidence-boundary', 'version_id': version.id,
        'boundary_rule': BOUNDARY_RULE_VERSION, 'calculation_rule': CALCULATION_RULE_VERSION,
        'planned_start': proposal['start'] if proposal else schedule.planned_start,
        'contractual_finish': proposal['finish'] if proposal else schedule.project.planned_end_date,
        'calendar_id': getattr(default_calendar, 'pk', None),
        'planning_build_id': version.planning_build_id,
        'planning_profile_selection': _build_profile_selection(version),
        'calendar_updated_at': getattr(default_calendar, 'updated_at', None),
        'calendars': calendars,
        'evidence_graph_revision': graph.revision if graph else None,
        'graph_source_fingerprint': graph.source_fingerprint if graph else None,
        'current_source_fingerprint': current_sources,
        'graph_stale': graph is None or graph.source_fingerprint != current_sources,
        'activities': activities, 'relationships': relationships,
    })


def assurance_state_fingerprint(version):
    from .planning_package_boundary import package_context
    proposal = package_context(version)
    resources = list(version.schedule.project.schedule_resources.filter(is_deleted=False).order_by('id').values(
        'id', 'capacity_units_per_day', 'updated_at',
    ))
    assignments = list(version.activities.filter(is_deleted=False).order_by('id', 'assignments__id').values(
        'assignments__id', 'assignments__resource_id', 'assignments__planned_units',
        'assignments__budgeted_hours', 'assignments__updated_at',
    ))
    payload = {
        'operation': 'assurance-v4', 'version_id': version.id,
        'calculated_at': version.calculated_at, 'calculated_finish': version.calculated_finish,
        'contractual_finish': proposal['finish'] if proposal else version.schedule.project.planned_end_date,
        'resources': resources, 'assignments': assignments,
        'parent_version_id': version.parent_version_id,
        'risks': list(version.planning_risks.order_by('pk').values('id', 'revision', 'status', 'priority', 'owner_id')),
    }
    reviews = list(version.logic_reviews.order_by('pk').values('id', 'fingerprint', 'group_id',
        'rationale', 'capacity_basis', 'duration_basis', 'max_parallel_deliverables', 'reviewed_by_id'))
    if reviews:
        payload['logic_reviews'] = reviews
    return canonical_fingerprint(payload)


def _build_profile_selection(version):
    if not version.planning_build_id:
        return None
    from .planning_profiles import planning_profile_selection
    selection = planning_profile_selection(version.schedule.project)
    return {key: selection.get(key) for key in ('revision', 'profile_id', 'content_fingerprint', 'valid')}


def update_job_progress(job, progress, message, *, phase=None, details=None):
    now = timezone.now()
    log = list(job.progress_log or [])
    entry = {'progress': int(progress), 'message': message, 'at': now.isoformat()}
    if phase:
        entry['phase'] = phase
    if details:
        entry['details'] = details
    if not log or log[-1].get('progress') != int(progress) or log[-1].get('message') != message:
        log.append(entry)
    job.progress = max(0, min(100, int(progress)))
    job.message = message[:255]
    job.progress_log = log[-100:]
    job.heartbeat_at = now
    update_fields = ['progress', 'message', 'progress_log', 'heartbeat_at', 'updated_at']
    if details:
        result_data = dict(job.result_data or {})
        result_data['progress_context'] = {**(result_data.get('progress_context') or {}), **details}
        job.result_data = result_data
        update_fields.append('result_data')
    job.save(update_fields=update_fields)


@transaction.atomic
def get_or_create_job(project, job_type, request_data, user, *, idempotency_key=None):
    key = idempotency_key or operation_fingerprint(project, job_type, request_data)
    package = job_type in {'preview', 'generate'} and ((request_data or {}).get('generation_options') or {}).get('mode') == 'planning_package'
    input_key = operation_fingerprint(project, job_type, request_data) if package else None
    def compatible(job):
        if package and (job.request_data != request_data or
                        (job.result_data or {}).get('planning_input_fingerprint') != input_key):
            from .planning_package_request import PlanningPackageRequestError
            raise PlanningPackageRequestError('Planning inputs changed. Start a new planning request.',
                                              code='planning_request_conflict', status_code=409)
        return job, False
    existing = PlanningJob.objects.filter(
        project=project, job_type=job_type, idempotency_key=key, is_deleted=False,
    ).first()
    if existing:
        return compatible(existing)
    try:
        with transaction.atomic():
            job = PlanningJob.objects.create(
                project=project, job_type=job_type, request_data=request_data or {}, requested_by=user,
                result_data={'planning_input_fingerprint': input_key} if package else {},
                idempotency_key=key, progress_log=[{
                    'progress': 0, 'message': 'Queued', 'phase': 'queued', 'at': timezone.now().isoformat(),
                }],
            )
    except IntegrityError:
        job = PlanningJob.objects.get(
            project=project, job_type=job_type, idempotency_key=key, is_deleted=False,
        )
        return compatible(job)
    return job, True


def _run_job_with_local_connection(task, job_id, task_id):
    """Run one emergency fallback job with thread-local database connections."""
    close_old_connections()
    try:
        task.run(job_id, dispatch_token=task_id)
    except Exception:  # noqa: BLE001
        logger.exception('Local planning fallback failed for job %s', job_id)
    finally:
        close_old_connections()


def _dispatch_local_fallback(task, job, broker_error):
    """Queue work on one bounded web-process thread when the broker is down."""
    if not getattr(settings, 'PLANNING_JOB_LOCAL_FALLBACK', True):
        return False
    previous_task_id = job.task_id
    fallback_task_id = f'local-{previous_task_id}'
    job.task_id = fallback_task_id
    job.status = 'queued'
    job.message = 'Worker queue unavailable; using local recovery worker'
    job.error_code = ''
    job.error_message = ''
    job.finished_at = None
    job.progress_log = [*(job.progress_log or []), {
        'progress': job.progress, 'message': job.message, 'phase': 'local_fallback',
        'at': timezone.now().isoformat(),
    }][-100:]
    fields = ['task_id', 'status', 'message', 'error_code', 'error_message', 'finished_at', 'progress_log']
    updated = PlanningJob.objects.filter(pk=job.pk, status='queued', task_id=previous_task_id).update(
        **{field: getattr(job, field) for field in fields}, updated_at=timezone.now(),
    )
    if not updated:
        job.refresh_from_db()
        return True  # A worker claimed this job, or an explicit retry superseded it.
    try:
        _local_executor.submit(_run_job_with_local_connection, task, job.id, fallback_task_id)
    except Exception:  # noqa: BLE001
        logger.exception('Could not start local planning fallback for job %s', job.id)
        return False
    logger.warning(
        'Celery dispatch failed for planning job %s (%s); local recovery worker accepted it',
        job.id, type(broker_error).__name__,
    )
    return True


def dispatch_job(job):
    """Publish only after the transaction that created/queued the job commits."""
    database = job._state.db or 'default'
    if transaction.get_connection(using=database).in_atomic_block:
        job_id, expected_task_id = job.pk, job.task_id

        def dispatch_committed_job():
            committed = PlanningJob.objects.using(database).filter(pk=job_id, is_deleted=False).first()
            if not committed or committed.status != 'queued' or committed.task_id != expected_task_id:
                return
            try:
                _dispatch_job_now(committed)
            except RuntimeError:
                # The job already records the failure. A committed API command
                # still returns its durable job for normal failure monitoring.
                logger.error('Post-commit dispatch failed for planning job %s', job_id)

        transaction.on_commit(dispatch_committed_job, using=database)
        return job
    return _dispatch_job_now(job)


def _dispatch_job_now(job):
    """Prefer Celery, with a bounded non-blocking recovery path for broker outages."""
    from ..tasks import run_planning_job
    # Persist the delivery identity before a fast worker can claim this job.
    previous_task_id = job.task_id
    dispatch_id = previous_task_id or f'planning-job-{job.id}'
    updated = PlanningJob.objects.filter(pk=job.pk, status='queued', task_id=previous_task_id).update(
        task_id=dispatch_id, updated_at=timezone.now(),
    )
    if not updated:
        job.refresh_from_db()
        return job
    job.task_id = dispatch_id
    try:
        run_planning_job.apply_async(args=[job.id], task_id=job.task_id)
    except Exception as exc:  # noqa: BLE001
        if _dispatch_local_fallback(run_planning_job, job, exc):
            return job
        job.status = 'failed'
        job.error_code = 'queue_unavailable'
        job.error_message = 'The background worker queue is unavailable. Retry this operation after worker recovery.'
        job.message = 'Queue dispatch failed'
        job.finished_at = timezone.now()
        job.progress_log = [*(job.progress_log or []), {
            'progress': job.progress, 'message': job.message, 'phase': 'dispatch_failed',
            'at': job.finished_at.isoformat(),
        }][-100:]
        fields = ['status', 'error_code', 'error_message', 'message', 'finished_at', 'progress_log']
        updated = PlanningJob.objects.filter(pk=job.pk, status='queued', task_id=job.task_id).update(
            **{field: getattr(job, field) for field in fields}, updated_at=timezone.now(),
        )
        if not updated:
            job.refresh_from_db()
            return job
        raise RuntimeError(job.error_message) from exc
    return job
