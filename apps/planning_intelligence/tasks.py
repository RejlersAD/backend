"""Celery tasks for the RADAI Project Planning Application."""
from __future__ import annotations

import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone
from apps.rbac.ai_telemetry import tracked_planning_job
from .services.operational_jobs import update_job_progress

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, name='apps.planning_intelligence.tasks.parse_uploaded_planning_file')
def parse_uploaded_planning_file(self, file_id):
    """Extracts text from an uploaded PlanningFile in the background so the
    upload request never blocks on PDF/Excel parsing (RADAI global rule)."""
    from .models import PlanningFile
    from .services.parsers import extract_text_with_coverage

    try:
        planning_file = PlanningFile.objects.get(pk=file_id)
    except PlanningFile.DoesNotExist as exc:
        # A worker may briefly see an older database snapshot. Retry this narrow
        # visibility failure instead of acknowledging a permanently queued file.
        logger.warning('parse_uploaded_planning_file: file %s not yet visible', file_id)
        raise self.retry(exc=exc, countdown=1)

    planning_file.parse_status = 'processing'
    planning_file.parse_error = ''
    planning_file.save(update_fields=['parse_status', 'parse_error', 'updated_at'])

    try:
        planning_file.file.open('rb')
        text, confidence, coverage = extract_text_with_coverage(planning_file.file, planning_file.original_filename)
        planning_file.extracted_text = text
        planning_file.confidence_score = confidence
        planning_file.parse_status = 'done' if text else 'failed'
        planning_file.parse_error = ''
        if not text:
            planning_file.parse_error = 'No text could be extracted from this file.'
        planning_file.save(update_fields=[
            'extracted_text', 'confidence_score', 'parse_status', 'parse_error', 'updated_at',
        ])
        try:
            from .services.document_intelligence import profile_document
            profile_document(planning_file, extraction_coverage=coverage)
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
def run_planning_job(self, job_id, *, dispatch_token=None):
    """Run a durable analysis/generation job and persist progress for polling."""
    from .models import PlanningJob

    with transaction.atomic():
        try:
            job = PlanningJob.objects.select_for_update(of=('self',)).select_related(
                'project', 'requested_by',
            ).get(pk=job_id, is_deleted=False)
        except PlanningJob.DoesNotExist:
            return {'job_id': job_id, 'error': 'not_found'}
        delivery_id = dispatch_token or self.request.id
        if delivery_id and job.task_id and delivery_id != job.task_id:
            return {'job_id': job_id, 'status': job.status, 'stale_dispatch': True}
        if job.status == 'cancelled':
            return {'job_id': job_id, 'status': 'cancelled'}
        if job.status == 'succeeded':
            return {'job_id': job.id, 'status': job.status, 'idempotent_replay': True}
        if job.status == 'running' and not (self.request.delivery_info or {}).get('redelivered'):
            return {'job_id': job.id, 'status': job.status, 'already_running': True}

        job.status = 'running'
        job.started_at = timezone.now()
        job.heartbeat_at = job.started_at
        job.attempt_count += 1
        job.task_id = delivery_id or job.task_id
        job.save(update_fields=['status', 'started_at', 'heartbeat_at', 'attempt_count', 'task_id', 'updated_at'])
    return _execute_planning_job(self, job_id)


@tracked_planning_job
def _execute_planning_job(task, job_id):
    """Only an accepted delivery enters workflow telemetry or domain work."""
    from .models import PlanningJob
    from .services.audit import record_event
    from .services.pipeline import analyze_documents, generate_schedule

    job = PlanningJob.objects.select_related('project', 'requested_by').get(pk=job_id, is_deleted=False)
    update_job_progress(job, 5, 'Worker accepted the job', phase='started')

    try:
        if job.job_type == 'analyze':
            update_job_progress(job, 15, 'Reading parsed project documents', phase='documents')

            def report_analysis(event):
                phase = event['phase']
                if phase == 'source_extraction':
                    progress, message = 20, 'Extracting source facts from parsed project documents'
                elif phase == 'ai_not_run':
                    progress, message = 75, 'AI analysis not run; preparing source findings'
                elif phase == 'persistence':
                    progress, message = 85, 'Saving extracted findings and review gaps'
                elif phase == 'ai_review':
                    total = event['chunks_total']
                    # Splitting a limited response changes the number of chunks,
                    # but not the source length or work already completed.
                    finished = event.get('characters_finished', event['chunks_finished'])
                    whole = event.get('characters_total', total)
                    progress = 25 + int(50 * finished / max(1, whole))
                    chunk = f"document chunk {event['chunk_number']} of {total}"
                    provider = {'anthropic': 'Anthropic', 'gemini': 'Google Gemini'}.get(event['provider'], 'AI provider')
                    messages = {
                        'waiting': f'Waiting for {provider} to review {chunk}',
                        'receiving': f"RADAI is reviewing {chunk} ({event.get('response_characters_received', 0):,} response characters received)",
                        'processed': f'AI processed {chunk}',
                        'partial': f'AI response incomplete for {chunk}',
                        'failed': f'AI request failed for {chunk}; continuing with available source evidence',
                        'skipped': f'AI review deferred for {chunk}',
                        'cached': f'Reused saved AI result for {chunk}',
                        'splitting': f'Splitting {chunk} into smaller sections after the AI response limit',
                    }
                    message = messages[event['chunk_status']]
                else:
                    return
                update_job_progress(job, progress, message, phase=phase, details=event)

            from .models import DocumentIntelligenceRun
            resume_id = (job.request_data or {}).get('resume_run_id')
            if resume_id:
                from .services.document_intelligence import get_or_run_document_intelligence
                previous = DocumentIntelligenceRun.objects.get(pk=resume_id, project=job.project, is_deleted=False)
                _resumed, intelligence = get_or_run_document_intelligence(
                    job.project, user=job.requested_by, resume_run=previous, progress_callback=report_analysis,
                )
            else:
                intelligence = analyze_documents(job.project, user=job.requested_by, force=True, progress_callback=report_analysis)
            update_job_progress(job, 95, 'Updating the planning basis from saved findings', phase='basis')
            from .services.schedule_basis import build_schedule_basis
            run = DocumentIntelligenceRun.objects.get(pk=intelligence['document_intelligence_run_id'])
            basis = run.schedule_bases.filter(is_deleted=False).first() or build_schedule_basis(run)
            job.result_data = {
                'intelligence': intelligence,
                'schedule_basis_id': basis.id,
                'schedule_basis_version': basis.version,
                'schedule_basis_readiness': basis.readiness,
            }
            coverage = intelligence.get('extraction_summary') or {}
            job.message = 'Document analysis saved; extraction remains partial' if coverage.get('status') == 'partial' else 'Document intelligence completed; review extracted facts'
            record_event(
                project=job.project, actor=job.requested_by, action='intelligence.completed', entity=job,
                after={
                    'run_id': intelligence.get('document_intelligence_run_id'),
                    'evidence_summary': intelligence.get('evidence_summary') or {},
                },
            )
        elif job.job_type == 'preview':
            from .services.pipeline import preview_schedule
            from .services.planning_package_request import resolve_generation_options, PlanningPackageRequestError
            options = resolve_generation_options(job.project, job.request_data or {})
            if options['mode'] == 'planning_package':
                from .services.operational_jobs import operation_fingerprint
                if (job.result_data or {}).get('planning_input_fingerprint') != operation_fingerprint(job.project, job.job_type, job.request_data):
                    raise PlanningPackageRequestError('Planning inputs changed while this request was queued. Open a fresh planning request.', code='planning_request_conflict', status_code=409)
            package_options = options if options['mode'] == 'planning_package' else {}
            update_job_progress(job, 20, 'Building deterministic schedule preview', phase='preview')
            preview = preview_schedule(
                job.project, user=job.requested_by,
                overrides=(job.request_data or {}).get('intelligence_overrides'),
                **package_options,
            )
            update_job_progress(job, 90, 'Persisting preview validation results', phase='preview_persistence')
            job.result_data = {**(job.result_data or {}), 'preview': preview}
            job.message = 'Schedule preview completed'
        elif job.job_type == 'generate':
            from .services.planning_package_request import resolve_generation_options, PlanningPackageRequestError
            options = resolve_generation_options(job.project, job.request_data or {})
            if options['mode'] == 'planning_package':
                from .services.operational_jobs import operation_fingerprint
                if (job.result_data or {}).get('planning_input_fingerprint') != operation_fingerprint(job.project, job.job_type, job.request_data):
                    raise PlanningPackageRequestError('Planning inputs changed while this request was queued. Open a fresh planning request.', code='planning_request_conflict', status_code=409)
            package_options = options if options['mode'] == 'planning_package' else {}
            update_job_progress(job, 20, 'Building WBS, workflow activities and planning logic', phase='generation')
            generation = generate_schedule(
                job.project, user=job.requested_by,
                overrides=(job.request_data or {}).get('intelligence_overrides'),
                input_fingerprint=job.idempotency_key,
                **package_options,
            )
            update_job_progress(job, 65, 'Materializing the relational schedule', phase='materialization')
            from .services.schedule_materializer import materialize_generation
            schedule_version, calculation_run, materialization_issues = materialize_generation(
                generation, requested_by=job.requested_by,
            )
            update_job_progress(job, 90, 'Finalizing CPM dates and persistent results', phase='finalizing')
            job.result_generation = generation
            job.result_data = {**(job.result_data or {}),
                'generation_id': generation.id,
                'version': generation.version,
                'schedule_id': schedule_version.schedule_id if schedule_version else None,
                'schedule_version_id': schedule_version.id if schedule_version else None,
                'calculation_run_id': calculation_run.id if calculation_run else None,
                'materialization_issues': materialization_issues,
                'state': 'calculated' if schedule_version else 'needs_evidence_review',
                'generation_mode': options['mode'],
                'intelligence_run_id': options['intelligence_run'].pk if options['intelligence_run'] else None,
            }
            job.message = f'Schedule version {generation.version} completed' if schedule_version else 'Source evidence extracted; review Not Specified fields.'
            record_event(
                project=job.project, actor=job.requested_by, action='generation.created',
                entity=generation, after={'version': generation.version}, metadata={'job_id': job.id},
            )
        elif job.job_type == 'build_plan':
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
        elif job.job_type == 'agreement_setup':
            from .services.agreement_workspace import analyze_agreement_workspace

            def agreement_progress(entry):
                update_job_progress(job, min(95, max(5, int(entry.get('percent', 5)))),
                                    entry.get('message') or 'Analyzing agreement',
                                    phase=entry.get('phase') or 'agreement')

            workspace = analyze_agreement_workspace(
                job.project, job.requested_by, file_ids=(job.request_data or {}).get('file_ids'),
                progress=agreement_progress, job=job,
            )
            job.result_data = {'workspace_id': str(workspace.pk), 'version': workspace.version,
                               'revision': workspace.revision, 'status': workspace.status}
            job.message = 'Agreement draft is ready. Review source-backed inputs and remaining exceptions.'
        elif job.job_type == 'evidence_bulk':
            from .services.evidence_bulk import run_bulk_evidence_review

            def evidence_progress(entry):
                update_job_progress(job, min(95, max(5, int(entry.get('progress', 5)))),
                                    entry.get('message') or 'Reviewing source evidence',
                                    phase=entry.get('phase') or 'evidence', details=entry.get('details'))

            job.result_data = run_bulk_evidence_review(
                job.project, job.requested_by, dict(job.request_data or {}),
                progress_callback=evidence_progress, job=job,
            )
            counts = job.result_data.get('counts') or {}
            accepted = counts.get('accepted_verified', 0) + counts.get('accepted_ai', 0)
            remaining = counts.get('unresolved', 0)
            job.message = f'Bulk review completed: {accepted} values accepted; {remaining} issues need input'
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
        from .services.document_intelligence import ResumeSourceChanged
        from .services.evidence_graph import EvidenceError
        from .services.planning_package_request import PlanningPackageRequestError
        job.status = 'failed'
        job.error_code = 'intelligence_resume_sources_changed' if isinstance(exc, ResumeSourceChanged) else 'planning_job_failed'
        job.error_message = str(exc) if isinstance(exc, ResumeSourceChanged) else f'Planning job failed. Contact support with job id {job.id}.'
        job.message = 'Source documents changed; start a new analysis' if isinstance(exc, ResumeSourceChanged) else 'Planning job failed'
        if isinstance(exc, PlanningPackageRequestError):
            job.error_code = exc.code
            job.error_message = str(exc)
            job.message = 'Planning inputs need attention'
        if job.job_type in {'evidence_bulk', 'agreement_setup'} and isinstance(exc, EvidenceError):
            job.error_code = exc.payload['code']
            job.error_message = str(exc)
            job.message = 'Agreement analysis needs attention' if job.job_type == 'agreement_setup' else 'Bulk evidence review needs attention'
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
