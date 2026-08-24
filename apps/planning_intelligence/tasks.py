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


@shared_task(bind=True, name='apps.planning_intelligence.tasks.run_planning_job')
def run_planning_job(self, job_id):
    """Run a durable analysis/generation job and persist progress for polling."""
    from .models import PlanningJob
    from .services.audit import record_event
    from .services.pipeline import analyze_documents, generate_schedule

    try:
        job = PlanningJob.objects.select_related('project', 'requested_by').get(pk=job_id, is_deleted=False)
    except PlanningJob.DoesNotExist:
        return {'job_id': job_id, 'error': 'not_found'}
    if job.status == 'cancelled':
        return {'job_id': job_id, 'status': 'cancelled'}

    job.status = 'running'
    job.progress = 10
    job.message = 'Reading parsed project documents'
    job.started_at = timezone.now()
    job.task_id = self.request.id or job.task_id
    job.save(update_fields=['status', 'progress', 'message', 'started_at', 'task_id', 'updated_at'])

    try:
        if job.job_type == 'analyze':
            intelligence = analyze_documents(job.project, user=job.requested_by, force=True)
            job.result_data = {'intelligence': intelligence}
            job.message = 'Document intelligence completed'
            record_event(
                project=job.project, actor=job.requested_by, action='intelligence.completed', entity=job,
                after={
                    'run_id': intelligence.get('document_intelligence_run_id'),
                    'evidence_summary': intelligence.get('evidence_summary') or {},
                },
            )
        else:
            job.progress = 35
            job.message = 'Building WBS, logic, schedule and deliverables'
            job.save(update_fields=['progress', 'message', 'updated_at'])
            generation = generate_schedule(
                job.project, user=job.requested_by,
                overrides=(job.request_data or {}).get('intelligence_overrides'),
            )
            job.progress = 75
            job.message = 'Materializing and calculating the CPM schedule'
            job.save(update_fields=['progress', 'message', 'updated_at'])
            from .services.schedule_materializer import materialize_generation
            schedule_version, calculation_run, materialization_issues = materialize_generation(
                generation, requested_by=job.requested_by,
            )
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
        job.status = 'succeeded'
        job.progress = 100
        job.finished_at = timezone.now()
        job.save(update_fields=['status', 'progress', 'message', 'result_data', 'result_generation', 'finished_at', 'updated_at'])
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
        job.save(update_fields=['status', 'error_code', 'error_message', 'message', 'finished_at', 'updated_at'])
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
