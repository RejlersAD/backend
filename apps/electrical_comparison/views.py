"""
Electrical Comparison API Views
"""
import logging
import os
import re
import tempfile
import threading

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from django.shortcuts import get_object_or_404

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser

from apps.core.queue_service import RobustQueueService, QueueUnavailableException
from .models import ElectricalComparisonJob, ElectricalProject
from .services.excel_export import export_job_to_xlsx
from .services.seed_electrical_legend import seed_electrical_legend_for_user
# BUG FIX: _build_panel_verification/_collect_comparison_rows/
# _build_combined_comparison used to be DEFINED here and lazy-imported
# by tasks.py from THIS module inside process_electrical_comparison()'s
# own function body — pulling in views.py's full module-level import
# list (DRF, the queue service, etc.) at task-execution time. Moved to
# helpers.py (which neither this file nor tasks.py has any circular
# dependency on) so both sides import from there instead of from each
# other. See helpers.py's own module docstring for the full reasoning.
from .helpers import _build_panel_verification, _collect_comparison_rows, _build_combined_comparison

logger = logging.getLogger(__name__)

# API key format validation
API_KEY_PATTERNS = {
    'openai': re.compile(r'^sk-[A-Za-z0-9\-_]{20,}$'),
    'claude': re.compile(r'^sk-ant-[A-Za-z0-9\-_]{20,}$'),
}

# Supported models
CLAUDE_MODELS = [
    'claude-sonnet-5',
    'claude-opus-5',
    'claude-sonnet-4-5-20250929',
]
OPENAI_MODELS = [
    'gpt-4o',
]


# ===========================================================================
# PROJECT CRUD — same pattern as apps.pid_verification_v2.views.projects()/
# project_detail() (plain function views there; class-based here to match
# this app's own existing style — identical behavior: user-scoped,
# plain-array response, 404 on not-owned).
# ===========================================================================

class ProjectsView(APIView):
    """GET  -> list all projects belonging to the authenticated user.
    POST -> create a new project."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = ElectricalProject.objects.filter(created_by=request.user).order_by('-created_at')
        return Response([
            {
                'project_id': str(p.project_id),
                'project_name': p.project_name,
                'description': p.description,
                'created_at': p.created_at,
            }
            for p in qs
        ])

    def post(self, request):
        project_name = (request.data.get('project_name') or '').strip()
        if not project_name:
            return Response({'project_name': ['This field is required.']}, status=status.HTTP_400_BAD_REQUEST)
        project = ElectricalProject.objects.create(
            created_by=request.user,
            project_name=project_name,
            description=request.data.get('description', ''),
        )
        return Response({
            'project_id': str(project.project_id),
            'project_name': project.project_name,
            'description': project.description,
            'created_at': project.created_at,
        }, status=status.HTTP_201_CREATED)


class ProjectHistoryView(APIView):
    """GET /api/v1/electrical-comparison/projects/<project_id>/history/
    All ElectricalComparisonJobs for this project, newest first — the
    "Previous Analyses" list on the Electrical Comparison page (same
    per-project upload-history pattern used elsewhere in this codebase,
    e.g. apps.instrument_io_workflow's own "Previous Uploads" list)."""
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = get_object_or_404(ElectricalProject, project_id=project_id, created_by=request.user)
        jobs = ElectricalComparisonJob.objects.filter(
            project=project, created_by=request.user,
        ).prefetch_related('results').order_by('-created_at')

        history = []
        for job in jobs:
            results_list = [{'status': r.status, 'source': r.source} for r in job.results.all()]
            history.append({
                'job_id': str(job.job_id),
                'status': job.status,
                'pid_file_name': job.pid_file_name,
                'created_at': job.created_at,
                'pid_tags_found': _derive_pid_tags_found(results_list),
                'equipment_comparison': _counts_for_source(results_list, 'equipment_list'),
                'load_list_comparison': _counts_for_source(results_list, 'load_list'),
            })
        return Response(history)


class ProjectDetailView(APIView):
    """GET    -> retrieve a single project by ID.
    PUT    -> update project name / description.
    DELETE -> delete project (jobs become project-less, not deleted —
    see ElectricalComparisonJob.project's SET_NULL)."""
    permission_classes = [IsAuthenticated]

    def _get_project(self, request, project_id):
        return get_object_or_404(ElectricalProject, project_id=project_id, created_by=request.user)

    def get(self, request, project_id):
        p = self._get_project(request, project_id)
        return Response({
            'project_id': str(p.project_id), 'project_name': p.project_name,
            'description': p.description, 'created_at': p.created_at,
        })

    def put(self, request, project_id):
        p = self._get_project(request, project_id)
        project_name = request.data.get('project_name')
        if project_name is not None:
            p.project_name = project_name.strip()
        if 'description' in request.data:
            p.description = request.data.get('description', '')
        p.save()
        return Response({
            'project_id': str(p.project_id), 'project_name': p.project_name,
            'description': p.description, 'created_at': p.created_at,
        })

    def delete(self, request, project_id):
        p = self._get_project(request, project_id)
        p.delete()
        return Response({'message': 'Project deleted'}, status=status.HTTP_200_OK)


class TestAPIKeyView(APIView):
    """Test if API key is valid before analysis."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        provider = request.data.get('provider', 'claude')
        api_key = request.data.get('api_key', '').strip()

        if not api_key:
            return Response(
                {'valid': False,
                 'error': 'API key is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        pattern = API_KEY_PATTERNS.get(provider)
        if not pattern:
            return Response(
                {'valid': False,
                 'error': f'Unsupported provider: {provider}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not pattern.match(api_key):
            return Response(
                {'valid': False,
                 'error': 'Invalid API key format. '
                          'Please check and try again.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # REAL connectivity test — a plain regex-format check (above) can
        # never tell you a key is revoked/expired/out of credit; it only
        # confirms the string LOOKS like a key. This makes one minimal,
        # cheap call against the actual provider using the raw SDK
        # directly (same pattern as apps.pid_verification.services.
        # piping_valve_mto_extractor.test_api_key, built earlier this
        # session for exactly the same reason) — NOT apps.core.
        # ai_consumer_clients.provider_api_key, which only resolves which
        # key STRING to use and never talks to the provider at all, so it
        # can never raise an "insufficient credit" error the way the error
        # branches below expect.
        try:
            if provider == 'claude':
                import anthropic
                client = anthropic.Anthropic(api_key=api_key, timeout=30.0)
                # Model + thinking:disabled matches vision_extractor.py's
                # own VISION_MODEL_CLAUDE_LATEST exactly — the model this
                # app's real extraction calls already use successfully.
                # (An earlier attempt elsewhere in this codebase to use an
                # unverified model name for a test-only call produced a
                # persistent, never-resolved 400 — deliberately not
                # repeating that mistake here.)
                client.messages.create(
                    model='claude-sonnet-5',
                    max_tokens=16,
                    thinking={'type': 'disabled'},
                    messages=[{'role': 'user', 'content': 'Hi'}],
                )
            else:
                from openai import OpenAI
                client = OpenAI(api_key=api_key, timeout=30.0)
                client.chat.completions.create(
                    model='gpt-4o',
                    max_tokens=16,
                    messages=[{'role': 'user', 'content': 'Hi'}],
                )
            return Response({'valid': True, 'provider': provider})
        except Exception as e:
            error_msg = str(e)
            http_status = getattr(e, 'status_code', None) or getattr(e, 'http_status', None)
            if http_status in (401, 403):
                return Response(
                    {'valid': False,
                     'error': 'Invalid API key. Please check and try again.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            if http_status == 429 or 'insufficient' in error_msg.lower() or \
               'quota' in error_msg.lower() or \
               'credit' in error_msg.lower():
                return Response(
                    {'valid': False,
                     'error': 'Insufficient tokens/credits. '
                              'Please top up your account.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            return Response(
                {'valid': False,
                 'error': 'Invalid API key. '
                          'Please check and try again.'},
                status=status.HTTP_400_BAD_REQUEST
            )


# ===========================================================================
# Celery dispatch — same "ping for an active worker, Celery with sync
# fallback, or a background thread" pattern as
# apps.pid_verification_v2.views.upload_pid / _has_active_celery_workers.
# ===========================================================================
_WORKER_CHECK_CACHE_KEY = 'elec_compare_celery_worker_active'
_WORKER_CHECK_TTL = int(getattr(settings, 'ELEC_COMPARE_WORKER_CHECK_TTL', 60))


def _has_active_celery_workers() -> bool:
    """True if at least one Celery worker is listening (or tasks run
    eagerly/synchronously, in which case no worker is needed at all).
    Cached for _WORKER_CHECK_TTL seconds so this never adds latency to
    every upload request — identical contract to pid_verification_v2's
    own helper of the same name."""
    if getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
        return True

    cached = cache.get(_WORKER_CHECK_CACHE_KEY)
    if cached is not None:
        return cached

    try:
        from celery import current_app as celery_app
        inspector = celery_app.control.inspect(timeout=1.5)
        active = bool(inspector.ping())
    except Exception:
        active = False

    cache.set(_WORKER_CHECK_CACHE_KEY, active, timeout=_WORKER_CHECK_TTL)
    return active


def _save_uploaded_file_to_temp(uploaded_file):
    """Writes an uploaded file's bytes to a temp file on disk and
    returns its path. Needed because the Celery task that will actually
    process this file runs in a SEPARATE worker process — an in-memory
    Django UploadedFile object cannot cross that process boundary, so
    the bytes must be persisted somewhere the task can re-open by path.
    Caller is responsible for deleting the temp file once the task is
    done with it (see tasks.py's own cleanup)."""
    suffix = os.path.splitext(uploaded_file.name)[1] or ''
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        for chunk in uploaded_file.chunks():
            tmp.write(chunk)
    finally:
        tmp.close()
    return tmp.name


def _run_electrical_comparison_sync(job_id: str, context: dict = None) -> None:
    """Synchronous fallback — runs the Celery task function directly, in
    this same process, when Celery/Redis itself is unavailable
    (RobustQueueService's sync_fallback contract) or when no active
    worker was detected at all (the thread-based fallback below).
    Lazy-imports tasks.py to avoid a module-load-time circular import
    (tasks.py itself lazy-imports back from this module — see its own
    docstring)."""
    from .tasks import process_electrical_comparison
    process_electrical_comparison.apply(args=(job_id,), kwargs={'context': context or {}})


# Status vocabularies used to tell a panel-verification row apart from a
# generic matched/missing/extra row when reconstructing a persisted job's
# results — see _reconstruct_panel_verification below.
_PANEL_STATUSES = {'panel_verified', 'panel_missing', 'panel_no_load_list'}
_MOTOR_STATUSES = {'motor_verified', 'motor_not_verified'}


def _derive_comparisons_done(results_list):
    """Which comparisons produced saved rows for this job, derived from
    the distinct `source` values present — used when re-viewing a
    completed job's history, where (unlike the live upload response) no
    separate `comparisons_done` list was ever persisted. results_list:
    [{'source': ..., 'status': ...}, ...]."""
    sources = {r['source'] for r in results_list}
    done = []
    if 'equipment_list' in sources:
        done.append('pid_vs_equipment')
    if 'load_list' in sources:
        done.append('pid_vs_loadlist')
    if 'equipment_vs_loadlist' in sources:
        done.append('equipment_vs_loadlist')
    if 'combined' in sources:
        done.append('combined')
    return done


def _counts_for_source(results_list, source):
    """matched/missing/extra counts for one source's rows, or None if
    that source has no rows at all (so callers can omit the key entirely,
    matching the live upload response's own behavior)."""
    rows = [r for r in results_list if r['source'] == source]
    if not rows:
        return None
    return {
        'matched': sum(1 for r in rows if r['status'] == 'matched'),
        'missing': sum(1 for r in rows if r['status'] == 'missing'),
        'extra': sum(1 for r in rows if r['status'] == 'extra'),
    }


def _counts_for_combined(results_list):
    """fully_matched/partial/single_source counts for Tab 4's
    source='combined' rows, or None if this job has none (not all 3
    files were uploaded)."""
    rows = [r for r in results_list if r['source'] == 'combined']
    if not rows:
        return None
    return {
        'fully_matched': sum(1 for r in rows if r['status'] == 'fully_matched'),
        'partial': sum(1 for r in rows if r['status'] == 'partial'),
        'single_source': sum(1 for r in rows if r['status'] == 'single_source'),
        'total': len(rows),
    }


def _derive_pid_tags_found(results_list):
    """Number of distinct P&ID tags involved in this job's comparisons,
    derived from saved rows rather than read back from a persisted
    count (ElectricalComparisonJob has no such column). For a
    pid_vs_equipment/pid_vs_loadlist comparison, every row where the
    P&ID side was present (matched/extra/mismatch/uncertain — anything
    but 'missing', which means P&ID-side absent) counts once; the larger
    of the two comparisons (they should normally agree) is used. Jobs
    with neither comparison (e.g. Equipment List vs Load List only, no
    P&ID uploaded) correctly report 0."""
    candidates = []
    for source in ('equipment_list', 'load_list'):
        rows = [r for r in results_list if r['source'] == source]
        if rows:
            candidates.append(sum(1 for r in rows if r['status'] != 'missing'))
    return max(candidates) if candidates else 0


def _reconstruct_panel_verification(result_rows):
    """Rebuilds the {'panels', 'motors', 'counts'} structure
    _build_panel_verification produces for a LIVE analysis, from the
    flat ElectricalComparisonResult rows saved for
    source='equipment_vs_loadlist' — needed when re-viewing a completed
    job's history, since only the flat rows are persisted, not that
    richer structure itself. `motor_count` / the motor's `panel` tag are
    both re-derived from the remarks text _build_panel_verification
    itself wrote ("N motor(s) in Load List" / "Panel: <tag>") — best
    effort, but those are the only two fields not otherwise present on
    the row, and this module is the only writer of that remarks format.
    Returns None if this job has no panel-verification rows at all (an
    Excel-only Equipment-vs-Load-List comparison, which never produces
    these statuses).
    """
    panel_rows = [r for r in result_rows if r.source == 'equipment_vs_loadlist' and r.status in _PANEL_STATUSES]
    motor_rows = [r for r in result_rows if r.source == 'equipment_vs_loadlist' and r.status in _MOTOR_STATUSES]
    if not panel_rows and not motor_rows:
        return None

    panels = []
    for r in panel_rows:
        motor_count_match = re.match(r'(\d+)\s+motor', r.remarks or '')
        panels.append({
            'panel_tag': r.tag_number,
            'description': r.description,
            'in_equipment_list': r.status != 'panel_missing',
            'motor_count': int(motor_count_match.group(1)) if motor_count_match else None,
            'status': r.status,
        })

    motors = []
    for r in motor_rows:
        panel_match = re.match(r'Panel:\s*(.+)', r.remarks or '')
        motors.append({
            'motor_tag': r.tag_number,
            'description': r.description,
            'panel': panel_match.group(1).strip() if panel_match else '',
            'status': r.status,
        })

    counts = {
        'panels_verified': sum(1 for p in panels if p['status'] == 'panel_verified'),
        'panels_missing': sum(1 for p in panels if p['status'] == 'panel_missing'),
        'total_motors': len(motors),
        'motors_verified': sum(1 for m in motors if m['status'] == 'motor_verified'),
    }
    return {'panels': panels, 'motors': motors, 'counts': counts}


class UploadComparisonView(APIView):
    """
    Any combination of the 3 files is valid (see the 5-case matrix in this
    method's own comments). AI Vision only ever runs when a P&ID file is
    present — Equipment List vs Load List is a pure Excel-to-Excel
    comparison and needs no API key at all.

    The actual extraction/parsing/comparison work now runs in a Celery
    background task (tasks.process_electrical_comparison) instead of
    inline in this request — this view's job is only to validate,
    persist the uploaded files to temp storage (a Celery worker runs in
    a separate process and can't see this request's in-memory
    UploadedFile objects), enqueue the task, and return immediately.
    Same "ping for an active worker → Celery with sync fallback, or a
    background thread" dispatch pattern as
    apps.pid_verification_v2.views.upload_pid. Progress is reported via
    GET /status/<job_id>/ (JobStatusView), which the frontend polls.
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]

    def post(self, request):
        # Auto-seed this user's Electrical legend in PidCheckerV2LegendSheet
        # on first use (see services/seed_electrical_legend.py's own
        # docstring for why this is per-user here, not a global
        # server-startup seed) — best-effort, never blocks the actual
        # upload/comparison if it fails for any reason.
        seed_electrical_legend_for_user(request.user)

        pid_file = request.FILES.get('pid_file')
        equipment_file = request.FILES.get('equipment_list')
        load_list_file = request.FILES.get('load_list')
        api_key = request.data.get('api_key', '').strip()
        provider = request.data.get('provider', 'claude')
        model = request.data.get('model', None)
        project_id = request.data.get('project_id') or None

        project = None
        if project_id:
            project = get_object_or_404(ElectricalProject, project_id=project_id, created_by=request.user)

        # ── Validation — 5 valid cases, 2 explicitly rejected combinations ──
        #   1. P&ID only                      — allowed (extraction, no compare)
        #   2. Equipment List + Load List      — allowed (no AI Vision)
        #   3. P&ID + Equipment List           — allowed
        #   4. P&ID + Load List                — allowed
        #   5. P&ID + Equipment List + Load List — allowed (all 3 comparisons)
        #   Equipment List alone (no P&ID, no Load List) — rejected, nothing
        #   to compare it against.
        #   Load List alone (no P&ID, no Equipment List) — rejected, same reason.
        if not pid_file and not equipment_file and not load_list_file:
            return Response(
                {'error': 'Please upload at least one file'},
                status=status.HTTP_400_BAD_REQUEST
            )
        if equipment_file and not load_list_file and not pid_file:
            return Response(
                {'error': 'Please also upload Load List to compare'},
                status=status.HTTP_400_BAD_REQUEST
            )
        if load_list_file and not equipment_file and not pid_file:
            return Response(
                {'error': 'Please also upload Equipment List to compare'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate file type only when a P&ID was actually given.
        if pid_file and not pid_file.name.lower().endswith('.pdf'):
            return Response(
                {'error': 'P&ID must be a PDF file'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Equipment List / Load List accept BOTH .xlsx and .pdf — there
        # was no explicit extension check here before (anything uploaded
        # just got passed straight to parse_excel_tags, which previously
        # only ever handled .xlsx and would raise a confusing low-level
        # pandas error for anything else). Explicit allowlist now, same
        # clear-error convention as the P&ID check above.
        if equipment_file and not equipment_file.name.lower().endswith(('.xlsx', '.pdf')):
            return Response(
                {'error': 'Equipment List must be an Excel (.xlsx) or PDF file'},
                status=status.HTTP_400_BAD_REQUEST
            )
        if load_list_file and not load_list_file.name.lower().endswith(('.xlsx', '.pdf')):
            return Response(
                {'error': 'Load List must be an Excel (.xlsx) or PDF file'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Create job
        job = ElectricalComparisonJob.objects.create(
            created_by=request.user,
            project=project,
            status='processing',
            pid_file_name=pid_file.name if pid_file else '',
            provider=provider,
            current_stage='uploading',
        )

        # Save uploaded files to temp storage — see
        # _save_uploaded_file_to_temp's own docstring for why this is
        # required at all (the Celery worker that will process these
        # runs in a different OS process). task_context carries
        # everything the task needs that isn't already on the job row;
        # file paths are plain strings (not FileFields — no model change
        # beyond the progress fields was in scope for this turn), so the
        # task opens them by path and this view/thread deletes them once
        # the task has consumed them (see tasks.py's own cleanup).
        task_context = {'api_key': api_key, 'provider': provider, 'model': model}
        try:
            if pid_file:
                task_context['pid_file_path'] = _save_uploaded_file_to_temp(pid_file)
            if equipment_file:
                task_context['equipment_file_path'] = _save_uploaded_file_to_temp(equipment_file)
                task_context['equipment_file_name'] = equipment_file.name
            if load_list_file:
                task_context['load_list_file_path'] = _save_uploaded_file_to_temp(load_list_file)
                task_context['load_list_file_name'] = load_list_file.name
        except Exception as e:
            job.status = 'failed'
            job.error_message = f'Failed to save uploaded files: {e}'
            job.save()
            return Response(
                {'error': job.error_message},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # ── Enqueue Celery task with intelligent fallback — same pattern
        # as apps.pid_verification_v2.views.upload_pid ──
        try:
            from .tasks import process_electrical_comparison

            worker_check_enabled = getattr(settings, 'ELEC_COMPARE_WORKER_CHECK_ENABLED', True)
            use_celery = (not worker_check_enabled) or _has_active_celery_workers()

            if not use_celery:
                # No Celery workers detected — run in a daemon thread so
                # the HTTP response is returned immediately while
                # processing continues.
                logger.info(
                    '[ElecCompareUpload] No active Celery workers detected — '
                    'processing job_id=%s in background thread.', job.job_id,
                )
                t = threading.Thread(
                    target=_run_electrical_comparison_sync,
                    args=(str(job.job_id),),
                    kwargs={'context': task_context},
                    daemon=True,
                    name=f'elec-compare-sync-{job.job_id}',
                )
                t.start()
            elif getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
                # BUG FIX: same root cause, same fix, as apps.
                # instrument_io_workflow.tasks.dispatch_io_document_
                # processing (see that function's own docstring for the
                # full "confirmed live" write-up). _has_active_celery_
                # workers() returns True unconditionally when EAGER mode
                # is on (see that function below), so use_celery is True
                # here even though there is no real broker/worker — but
                # calling task.delay(...) directly in THIS (request)
                # thread, under EAGER semantics, runs the ENTIRE task
                # synchronously inline before .delay() itself returns.
                # For a multi-page P&ID with AI Vision that's many
                # minutes of the upload POST just hanging, with the
                # frontend's poll loop never getting the job_id/202
                # response it needs to even start polling — pages_done/
                # current_stage were already being written correctly by
                # the task the whole time, there was just no HTTP
                # response yet for anything to poll against.
                #
                # Fix: run the SAME .delay() call on a background daemon
                # thread instead of inline. The task still runs fully
                # synchronously (EAGER mode itself is unchanged) — it
                # just runs in a background thread rather than the
                # request thread, so this view's 202 response below goes
                # out immediately and the frontend's poll loop can start
                # observing real per-page progress right away.
                logger.info(
                    '[ElecCompareUpload] EAGER mode active — dispatching job_id=%s via '
                    'background thread so the upload request returns immediately.', job.job_id,
                )

                def _run_eager_task_in_thread(job_id, context):
                    from django.db import connection
                    try:
                        process_electrical_comparison.delay(job_id, context=context)
                    except Exception as thread_exc:  # noqa: BLE001
                        logger.error(
                            '[ElecCompareUpload] Background EAGER-mode dispatch failed for job_id=%s: %s',
                            job_id, thread_exc,
                        )
                        try:
                            ElectricalComparisonJob.objects.filter(job_id=job_id).update(
                                status='failed',
                                error_message='Processing failed unexpectedly — please try again.',
                            )
                        except Exception:  # noqa: BLE001
                            pass
                    finally:
                        # This thread opened its own DB connection
                        # (Django connections are thread-local); outside
                        # the request/response cycle nothing closes it
                        # automatically, so it would otherwise leak for
                        # the life of the dev server process.
                        connection.close()

                t = threading.Thread(
                    target=_run_eager_task_in_thread,
                    args=(str(job.job_id), task_context),
                    daemon=True,
                    name=f'elec-compare-eager-{job.job_id}',
                )
                t.start()
            else:
                try:
                    RobustQueueService.queue_task(
                        process_electrical_comparison,
                        args=(str(job.job_id),),
                        kwargs={'context': task_context},
                        # Same "zero-arg lambda silently breaks the
                        # fallback" bug pid_verification_v2's own views.py
                        # already called out once — the fallback is
                        # invoked with the SAME args/kwargs as the primary
                        # call, so it must accept them too.
                        sync_fallback=lambda job_id, context=None: _run_electrical_comparison_sync(job_id, context),
                        max_retries=2,
                    )
                    logger.info('[ElecCompareUpload] Task queued via Celery: job_id=%s', job.job_id)
                except QueueUnavailableException as queue_exc:
                    logger.error('[ElecCompareUpload] Queue unavailable and sync fallback failed: %s', queue_exc)
                    job.status = 'failed'
                    job.error_message = 'Processing service unavailable. Please try again.'
                    job.save()
                    return Response(
                        {'error': job.error_message},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE,
                    )
        except Exception as exc:
            logger.error('[ElecCompareUpload] Unexpected error setting up task: %s', exc)
            job.status = 'failed'
            job.error_message = f'Failed to start processing: {exc}'
            job.save()
            return Response(
                {'error': 'Failed to process. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response({
            'job_id': str(job.job_id),
            'status': job.status,
            'message': 'Upload received. Processing started.',
        }, status=status.HTTP_202_ACCEPTED)


class JobStatusView(APIView):
    """Polled by the frontend every few seconds while a job is
    'processing' — now also reports real progress (pages_total/
    pages_done/current_stage/progress_percentage), updated live by
    tasks.process_electrical_comparison as it runs, instead of only
    ever flipping straight from 'processing' to 'completed'/'failed'
    with nothing observable in between."""
    permission_classes = [IsAuthenticated]

    def get(self, request, job_id):
        job = get_object_or_404(
            ElectricalComparisonJob,
            job_id=job_id,
            created_by=request.user
        )
        progress_percentage = None
        if job.pages_total > 0:
            progress_percentage = round((job.pages_done / job.pages_total) * 100, 1)
        return Response({
            'job_id': str(job.job_id),
            'status': job.status,
            'error_message': job.error_message,
            'pages_total': job.pages_total,
            'pages_done': job.pages_done,
            'current_stage': job.current_stage,
            'progress_percentage': progress_percentage,
        })


class JobResultsView(APIView):
    """GET    -> a completed job's full results — used both by the
    original "poll after upload" flow and (new) the history list's
    "View Results" action, which calls this with NO files re-uploaded.
    Response now also includes comparisons_done / pid_tags_found /
    equipment_comparison / load_list_comparison / panel_verification —
    none of those are columns on the model, so they're derived from the
    saved flat rows every time (see the _derive_*/_reconstruct_* helpers
    above) rather than read back from a persisted copy, which keeps this
    endpoint's response shape line up with the live upload response
    SingleLineDiagram.jsx's results view was built to render, so
    re-opening a historical job looks the same as a fresh analysis.
    DELETE -> removes the job (and its results, via CASCADE) — the
    history list's "Delete" action."""
    permission_classes = [IsAuthenticated]

    def get(self, request, job_id):
        job = get_object_or_404(
            ElectricalComparisonJob,
            job_id=job_id,
            created_by=request.user
        )
        result_rows = list(job.results.all())
        results_list = [
            {
                'tag_number': r.tag_number,
                'description': r.description,
                'status': r.status,
                'source': r.source,
                'equipment_type': r.equipment_type,
                'remarks': r.remarks,
            }
            for r in result_rows
        ]
        comparisons_done = _derive_comparisons_done(results_list)

        response_data = {
            'job_id': str(job.job_id),
            'status': job.status,
            'pid_file_name': job.pid_file_name,
            'provider': job.provider,
            'model_used': job.model_used,
            'comparisons_done': comparisons_done,
            'pid_tags_found': _derive_pid_tags_found(results_list),
            'results': results_list,
        }

        equipment_counts = _counts_for_source(results_list, 'equipment_list')
        if equipment_counts is not None:
            response_data['equipment_comparison'] = equipment_counts
        load_list_counts = _counts_for_source(results_list, 'load_list')
        if load_list_counts is not None:
            response_data['load_list_comparison'] = load_list_counts

        panel_verification = _reconstruct_panel_verification(result_rows)
        if panel_verification is not None:
            response_data['panel_verification'] = panel_verification
        elif 'equipment_vs_loadlist' in comparisons_done:
            eq_vs_ll_counts = _counts_for_source(results_list, 'equipment_vs_loadlist')
            if eq_vs_ll_counts is not None:
                response_data['equipment_vs_loadlist_comparison'] = eq_vs_ll_counts

        combined_counts = _counts_for_combined(results_list)
        if combined_counts is not None:
            response_data['combined_comparison'] = combined_counts

        return Response(response_data)

    def delete(self, request, job_id):
        job = get_object_or_404(
            ElectricalComparisonJob,
            job_id=job_id,
            created_by=request.user
        )
        job.delete()
        return Response({'message': 'Job deleted'}, status=status.HTTP_200_OK)


# Per-tab export filename prefixes — keyed by ElectricalComparisonResult.source.
_EXPORT_FILENAME_PREFIX = {
    'equipment_list': 'pid_vs_equipment',
    'load_list': 'pid_vs_loadlist',
    'equipment_vs_loadlist': 'equipment_vs_loadlist',
}


class ExportExcelView(APIView):
    """GET /api/v1/electrical-comparison/export/<job_id>/?source=<source>
    Required by the frontend's per-tab 'Export Excel' button — not in the
    originally-specified backend file list, added here (inside this same
    new app, no other file touched) since the frontend has nothing to
    call without it. ?source= filters to just that tab's rows and picks
    the matching filename; omitted = every saved row, generic filename."""
    permission_classes = [IsAuthenticated]

    def get(self, request, job_id):
        job = get_object_or_404(
            ElectricalComparisonJob,
            job_id=job_id,
            created_by=request.user
        )
        source = request.query_params.get('source') or None
        data = export_job_to_xlsx(job, source=source)
        prefix = _EXPORT_FILENAME_PREFIX.get(source, 'electrical_comparison')
        filename = f'{prefix}_{job.job_id}.xlsx'
        resp = HttpResponse(
            data,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        resp['Content-Disposition'] = f'attachment; filename="{filename}"'
        return resp
