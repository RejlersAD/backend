"""Celery tasks for the RADAI Project Planning Application."""
from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(name='apps.planning_intelligence.tasks.parse_uploaded_planning_file')
def parse_uploaded_planning_file(file_id):
    """Extracts text from an uploaded PlanningFile in the background so the
    upload request never blocks on PDF/Excel parsing (RADAI global rule)."""
    from .models import PlanningFile
    from .services.parsers import extract_text

    try:
        planning_file = PlanningFile.objects.get(pk=file_id)
    except PlanningFile.DoesNotExist:
        logger.warning('parse_uploaded_planning_file: file %s not found', file_id)
        return {'file_id': file_id, 'error': 'not_found'}

    planning_file.parse_status = 'processing'
    planning_file.parse_error = ''
    planning_file.save(update_fields=['parse_status', 'parse_error', 'updated_at'])

    try:
        planning_file.file.open('rb')
        text, confidence = extract_text(planning_file.file, planning_file.original_filename)
        planning_file.extracted_text = text
        planning_file.confidence_score = confidence
        planning_file.parse_status = 'done' if text else 'failed'
        planning_file.parse_error = ''
        if not text:
            planning_file.parse_error = 'No text could be extracted from this file.'
        planning_file.save(update_fields=[
            'extracted_text', 'confidence_score', 'parse_status', 'parse_error', 'updated_at',
        ])
        if planning_file.parse_status == 'done':
            try:
                from .services.document_intelligence import profile_document
                profile_document(planning_file)
            except Exception:  # noqa: BLE001
                logger.exception('Document classification failed for parsed file %s', file_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning('parse_uploaded_planning_file failed for %s: %s', file_id, exc)
        planning_file.parse_status = 'failed'
        planning_file.parse_error = str(exc)
        planning_file.save(update_fields=['parse_status', 'parse_error', 'updated_at'])
    finally:
        try:
            planning_file.file.close()
        except Exception:  # noqa: BLE001
            pass

    return {'file_id': planning_file.id, 'parse_status': planning_file.parse_status}


@shared_task(
    bind=True, acks_late=True, reject_on_worker_lost=True,
    name='apps.planning_intelligence.tasks.run_planning_job',
)
def run_planning_job(self, job_id):
    """Run a durable analysis/generation job and persist progress for polling."""
    from .models import PlanningJob
    from .services.audit import record_event
    from .services.operational_jobs import update_job_progress
    from .services.pipeline import analyze_documents, generate_schedule

    try:
        job = PlanningJob.objects.select_related('project', 'requested_by').get(pk=job_id, is_deleted=False)
    except PlanningJob.DoesNotExist:
        return {'job_id': job_id, 'error': 'not_found'}
    if job.status == 'cancelled':
        return {'job_id': job_id, 'status': 'cancelled'}
    if job.status == 'succeeded':
        return {'job_id': job.id, 'status': job.status, 'idempotent_replay': True}

    job.status = 'running'
    job.started_at = timezone.now()
    job.heartbeat_at = job.started_at
    job.attempt_count += 1
    job.task_id = self.request.id or job.task_id
    job.save(update_fields=['status', 'started_at', 'heartbeat_at', 'attempt_count', 'task_id', 'updated_at'])
    update_job_progress(job, 5, 'Worker accepted the job', phase='started')

    try:
        if job.job_type == 'analyze':
            update_job_progress(job, 15, 'Reading parsed project documents', phase='documents')
            intelligence = analyze_documents(job.project, user=job.requested_by, force=True)
            from .models import DocumentIntelligenceRun
            from .services.schedule_basis import build_schedule_basis
            run = DocumentIntelligenceRun.objects.get(pk=intelligence['document_intelligence_run_id'])
            basis = run.schedule_bases.filter(is_deleted=False).first() or build_schedule_basis(run)
            job.result_data = {
                'intelligence': intelligence,
                'schedule_basis_id': basis.id,
                'schedule_basis_version': basis.version,
                'schedule_basis_readiness': basis.readiness,
            }
            job.message = 'Document intelligence completed'
            record_event(
                project=job.project, actor=job.requested_by, action='intelligence.completed', entity=job,
                after={
                    'run_id': intelligence.get('document_intelligence_run_id'),
                    'evidence_summary': intelligence.get('evidence_summary') or {},
                },
            )
        elif job.job_type == 'preview':
            from .services.pipeline import preview_schedule
            update_job_progress(job, 20, 'Building deterministic schedule preview', phase='preview')
            preview = preview_schedule(
                job.project, user=job.requested_by,
                overrides=(job.request_data or {}).get('intelligence_overrides'),
            )
            update_job_progress(job, 90, 'Persisting preview validation results', phase='preview_persistence')
            job.result_data = {'preview': preview}
            job.message = 'Schedule preview completed'
        elif job.job_type == 'generate':
            update_job_progress(job, 20, 'Building evidence-controlled WBS and activities', phase='generation')
            generation = generate_schedule(
                job.project, user=job.requested_by,
                overrides=(job.request_data or {}).get('intelligence_overrides'),
                input_fingerprint=job.idempotency_key,
            )
            update_job_progress(job, 65, 'Materializing the relational schedule', phase='materialization')
            from .services.schedule_materializer import materialize_generation
            schedule_version, calculation_run, materialization_issues = materialize_generation(
                generation, requested_by=job.requested_by,
            )
            update_job_progress(job, 90, 'Finalizing CPM dates and persistent results', phase='finalizing')
            job.result_generation = generation
            job.result_data = {
                'generation_id': generation.id,
                'version': generation.version,
                'schedule_id': schedule_version.schedule_id,
                'schedule_version_id': schedule_version.id,
                'calculation_run_id': calculation_run.id if calculation_run else None,
                'materialization_issues': materialization_issues,
            }
            job.message = f'Schedule version {generation.version} completed'
            record_event(
                project=job.project, actor=job.requested_by, action='generation.created',
                entity=generation, after={'version': generation.version}, metadata={'job_id': job.id},
            )
        elif job.job_type == 'build_plan':
            from django.db import transaction
            from .models import ScheduleBasis
            from .services.generation_plan import build_generation_plan
            basis = ScheduleBasis.objects.get(
                pk=(job.request_data or {}).get('basis_id'), project=job.project, is_deleted=False,
            )
            update_job_progress(job, 20, 'Classifying approved deliverables and source evidence', phase='classification')
            existing_plan_id = (job.result_data or {}).get('generation_plan_id')
            if existing_plan_id:
                plan = job.project.generation_plans.get(pk=existing_plan_id, is_deleted=False)
            else:
                # Commit the generated plan and its durable job pointer together. A worker retry
                # can therefore reuse the output instead of creating another plan version.
                with transaction.atomic():
                    plan = build_generation_plan(basis)
                    job.result_data = {'generation_plan_id': plan.id}
                    job.save(update_fields=['result_data', 'updated_at'])
            update_job_progress(job, 90, 'Saving phases, scenarios, and dependency logic', phase='plan_persistence')
            job.result_data = {
                'generation_plan_id': plan.id, 'generation_plan_version': plan.version,
                'generation_plan_status': plan.status, 'readiness': plan.readiness,
            }
            job.message = f'Generation Plan v{plan.version} completed'
            record_event(
                project=job.project, actor=job.requested_by, action='generation_plan.created', entity=plan,
                after={'version': plan.version, 'readiness': plan.readiness}, metadata={'job_id': job.id},
            )
        elif job.job_type == 'workable_plan':
            from .services.workable_plan import approve_workable_baseline, build_workable_plan
            request_data = dict(job.request_data or {})
            progress_callback = lambda progress, message, phase, details=None: update_job_progress(
                job, progress, message, phase=phase, details=details,
            )
            if request_data.get('approval'):
                approval = request_data['approval']
                job.result_data = approve_workable_baseline(
                    job.project, job.requested_by, approval.get('schedule_version_id'),
                    approval.get('name'), progress_callback,
                )
            else:
                request_data['output_fingerprint'] = job.idempotency_key
                job.result_data = build_workable_plan(
                    job.project, job.requested_by, request_data, progress_callback,
                )
            state = job.result_data.get('state')
            job.message = (
                'Workable plan baseline approved' if state == 'baselined'
                else 'Workable plan is ready for baseline approval' if state == 'ready_for_approval'
                else 'Planner decisions are required'
            )
            record_event(
                project=job.project, actor=job.requested_by, action='workable_plan.completed', entity=job,
                after={'state': state, 'schedule_version_id': (job.result_data.get('summary') or {}).get('schedule_version_id')},
            )
        elif job.job_type == 'calculate':
            from .models import ScheduleVersion
            from .services.cpm import calculate_schedule_version
            version = ScheduleVersion.objects.get(
                pk=(job.request_data or {}).get('schedule_version_id'),
                schedule__project=job.project, is_deleted=False,
            )
            if version.status in {'approved', 'baselined', 'superseded'}:
                raise ValueError('This schedule version is immutable.')
            update_job_progress(job, 20, 'Validating activity network and calendars', phase='network')
            calculation_run = calculate_schedule_version(version, requested_by=job.requested_by)
            update_job_progress(job, 90, 'Persisting dates, float, and critical path', phase='persistence')
            job.result_data = {
                'schedule_version_id': version.id, 'calculation_run_id': calculation_run.id,
                'project_finish': calculation_run.project_finish.isoformat() if calculation_run.project_finish else None,
                'issues': calculation_run.issues,
            }
            job.message = 'CPM calculation completed'
            record_event(project=job.project, actor=job.requested_by, action='schedule.calculated_async', entity=job, after=job.result_data)
        elif job.job_type == 'assurance':
            from .models import ScheduleVersion
            from .services.trustworthy_scheduling import run_schedule_assurance
            version = ScheduleVersion.objects.get(
                pk=(job.request_data or {}).get('schedule_version_id'),
                schedule__project=job.project, is_deleted=False,
            )
            update_job_progress(job, 20, 'Running expanded network validation', phase='network_assurance')
            review = run_schedule_assurance(version, requested_by=job.requested_by)
            update_job_progress(job, 85, 'Saving contract, resource, and comparison results', phase='assurance_persistence')
            job.result_data = {
                'schedule_version_id': version.id, 'assurance_review_id': review.id,
                'assurance_status': review.status, 'blocker_count': len(review.blockers),
                'warning_count': len(review.warnings),
            }
            job.message = 'Phase 3 schedule assurance completed'
            record_event(project=job.project, actor=job.requested_by, action='schedule.assurance_run_async', entity=job, after=job.result_data)
        else:
            raise ValueError(f'Unsupported planning job type: {job.job_type}')
        job.status = 'succeeded'
        job.progress = 100
        job.finished_at = timezone.now()
        job.heartbeat_at = job.finished_at
        job.progress_log = [*(job.progress_log or []), {
            'progress': 100, 'message': job.message, 'phase': 'completed',
            'at': job.finished_at.isoformat(),
        }][-100:]
        job.save(update_fields=[
            'status', 'progress', 'message', 'progress_log', 'result_data', 'result_generation',
            'finished_at', 'heartbeat_at', 'updated_at',
        ])
        record_event(
            project=job.project, actor=job.requested_by, action='job.completed', entity=job,
            after={'job_type': job.job_type, 'status': job.status},
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception('Planning job %s failed', job_id)
        job.status = 'failed'
        job.error_code = 'planning_job_failed'
        job.error_message = f'Planning job failed. Contact support with job id {job.id}.'
        job.message = 'Planning job failed'
        job.finished_at = timezone.now()
        job.heartbeat_at = job.finished_at
        job.progress_log = [*(job.progress_log or []), {
            'progress': job.progress, 'message': job.message, 'phase': 'failed',
            'at': job.finished_at.isoformat(),
        }][-100:]
        job.save(update_fields=['status', 'error_code', 'error_message', 'message', 'finished_at', 'heartbeat_at', 'progress_log', 'updated_at'])
        record_event(
            project=job.project, actor=job.requested_by, action='job.failed', entity=job,
            after={'job_type': job.job_type, 'error_code': job.error_code},
        )
    return {'job_id': job.id, 'status': job.status}


@shared_task(bind=True, max_retries=2, name='apps.planning_intelligence.tasks.deliver_schedule_integration')
def deliver_schedule_integration(self, delivery_id):
    """Deliver a signed schedule payload with bounded retries and durable status."""
    from .models import IntegrationDelivery
    from .services.audit import record_event
    from .services.integration_delivery import deliver

    try:
        delivery = IntegrationDelivery.objects.select_related(
            'endpoint', 'version__schedule__project',
        ).get(pk=delivery_id, is_deleted=False)
    except IntegrationDelivery.DoesNotExist:
        return {'delivery_id': delivery_id, 'status': 'not_found'}
    delivery.status = 'delivering'
    delivery.attempt_count += 1
    delivery.started_at = delivery.started_at or timezone.now()
    delivery.save(update_fields=['status', 'attempt_count', 'started_at', 'updated_at'])
    endpoint = delivery.endpoint
    try:
        response, digest = deliver(delivery)
        delivery.payload_sha256 = digest
        delivery.response_status = response.status_code
        delivery.response_excerpt = response.text[:1000]
        response.raise_for_status()
        delivery.status = 'succeeded'
        delivery.error_message = ''
        delivery.finished_at = timezone.now()
        delivery.save(update_fields=[
            'payload_sha256', 'response_status', 'response_excerpt', 'status',
            'error_message', 'finished_at', 'updated_at',
        ])
        endpoint.last_success_at = timezone.now()
        endpoint.last_error = ''
        endpoint.save(update_fields=['last_success_at', 'last_error', 'updated_at'])
        record_event(
            project=delivery.version.schedule.project, actor=delivery.requested_by,
            action='integration.delivery_succeeded', entity=delivery,
            after={'endpoint_id': endpoint.id, 'version_id': delivery.version_id, 'status': response.status_code},
        )
        return {'delivery_id': delivery.id, 'status': 'succeeded'}
    except Exception as exc:  # noqa: BLE001
        logger.warning('Integration delivery %s attempt %s failed: %s', delivery.id, delivery.attempt_count, exc)
        delivery.status = 'failed'
        delivery.error_message = str(exc)[:1000]
        delivery.finished_at = timezone.now()
        delivery.save(update_fields=['status', 'error_message', 'finished_at', 'updated_at'])
        endpoint.last_failure_at = timezone.now()
        endpoint.last_error = delivery.error_message[:500]
        endpoint.save(update_fields=['last_failure_at', 'last_error', 'updated_at'])
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=2 ** (self.request.retries + 1))
        record_event(
            project=delivery.version.schedule.project, actor=delivery.requested_by,
            action='integration.delivery_failed', entity=delivery,
            after={'endpoint_id': endpoint.id, 'version_id': delivery.version_id},
        )
        return {'delivery_id': delivery.id, 'status': 'failed'}
