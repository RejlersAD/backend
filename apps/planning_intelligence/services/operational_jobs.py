"""Durable, idempotent orchestration for planning workloads."""
from __future__ import annotations

import hashlib
import json

from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import PlanningJob


def canonical_fingerprint(payload):
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def generation_fingerprint(project, request_data):
    basis = project.schedule_bases.filter(is_deleted=False, status='approved').first()
    plan = project.generation_plans.filter(is_deleted=False, status='approved', basis=basis).first() if basis else None
    configuration = getattr(project, 'schedule_configuration', None)
    files = list(project.files.filter(is_deleted=False, parse_status='done').order_by('id').values(
        'id', 'updated_at', 'size_bytes', 'confidence_score',
    ))
    return canonical_fingerprint({
        'operation': 'generate-v5', 'project_id': project.id,
        'project_updated_at': project.updated_at, 'basis_id': getattr(basis, 'id', None),
        'basis_updated_at': getattr(basis, 'updated_at', None), 'plan_id': getattr(plan, 'id', None),
        'plan_updated_at': getattr(plan, 'updated_at', None),
        'configuration_version': getattr(configuration, 'configuration_version', None),
        'files': files, 'request_data': request_data or {},
    })


def operation_fingerprint(project, job_type, request_data):
    if job_type in {'generate', 'preview'}:
        return generation_fingerprint(project, request_data)
    if job_type == 'analyze':
        files = list(project.files.filter(is_deleted=False, parse_status='done').order_by('id').values(
            'id', 'updated_at', 'size_bytes', 'confidence_score',
        ))
        return canonical_fingerprint({
            'operation': 'analyze-v4', 'project_id': project.id,
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
        'operation': 'build-generation-plan-v4', 'project_id': basis.project_id,
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
    activities = list(version.activities.filter(is_deleted=False).order_by('id').values(
        'id', 'external_id', 'duration_days', 'calendar_id', 'constraint_type', 'constraint_date',
    ))
    relationships = list(version.relationships.filter(is_deleted=False).order_by('id').values(
        'id', 'predecessor_id', 'successor_id', 'relationship_type', 'lag_days', 'updated_at',
    ))
    schedule = version.schedule
    return canonical_fingerprint({
        'operation': 'calculate-v4', 'version_id': version.id,
        'planned_start': schedule.planned_start, 'contractual_finish': schedule.project.planned_end_date,
        'calendar_id': schedule.default_calendar_id,
        'calendar_updated_at': getattr(schedule.default_calendar, 'updated_at', None),
        'activities': activities, 'relationships': relationships,
    })


def assurance_state_fingerprint(version):
    resources = list(version.schedule.project.schedule_resources.filter(is_deleted=False).order_by('id').values(
        'id', 'capacity_units_per_day', 'updated_at',
    ))
    assignments = list(version.activities.filter(is_deleted=False).order_by('id', 'assignments__id').values(
        'assignments__id', 'assignments__resource_id', 'assignments__planned_units',
        'assignments__budgeted_hours', 'assignments__updated_at',
    ))
    return canonical_fingerprint({
        'operation': 'assurance-v4', 'version_id': version.id,
        'calculated_at': version.calculated_at, 'calculated_finish': version.calculated_finish,
        'contractual_finish': version.schedule.project.planned_end_date,
        'resources': resources, 'assignments': assignments,
        'parent_version_id': version.parent_version_id,
    })


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
    existing = PlanningJob.objects.filter(
        project=project, job_type=job_type, idempotency_key=key, is_deleted=False,
    ).first()
    if existing:
        return existing, False
    try:
        job = PlanningJob.objects.create(
            project=project, job_type=job_type, request_data=request_data or {}, requested_by=user,
            idempotency_key=key, progress_log=[{
                'progress': 0, 'message': 'Queued', 'phase': 'queued', 'at': timezone.now().isoformat(),
            }],
        )
    except IntegrityError:
        job = PlanningJob.objects.get(
            project=project, job_type=job_type, idempotency_key=key, is_deleted=False,
        )
        return job, False
    return job, True


def dispatch_job(job):
    """Dispatch only; never execute expensive planning work in the web process."""
    from ..tasks import run_planning_job
    try:
        result = run_planning_job.apply_async(args=[job.id], task_id=f'planning-job-{job.id}')
        job.task_id = result.id or f'planning-job-{job.id}'
        job.save(update_fields=['task_id', 'updated_at'])
    except Exception as exc:  # noqa: BLE001
        job.status = 'failed'
        job.error_code = 'queue_unavailable'
        job.error_message = 'The background worker queue is unavailable. Retry this operation after worker recovery.'
        job.message = 'Queue dispatch failed'
        job.finished_at = timezone.now()
        job.progress_log = [*(job.progress_log or []), {
            'progress': job.progress, 'message': job.message, 'phase': 'dispatch_failed',
            'at': job.finished_at.isoformat(),
        }][-100:]
        job.save(update_fields=[
            'status', 'error_code', 'error_message', 'message', 'finished_at', 'progress_log', 'updated_at',
        ])
        raise RuntimeError(job.error_message) from exc
    return job
