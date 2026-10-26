"""
Piping Valve MTO — Async Extraction Endpoints
=============================================

Extraction + BYOK endpoints:

    POST /api/v1/pid-verification/extract-valve-mto/        (start)
        multipart/form-data: pid_file=<PDF>, vision_provider=<openai|claude>
        (optional), vision_model=<model id> (optional — caller-chosen model
        for the selected provider, e.g. the frontend's "AI Model" dropdown;
        empty/omitted falls back to services.piping_valve_mto_extractor's
        own VISION_MODELS[provider] default, unchanged from before this
        parameter existed), vision_api_key=<key> (optional — see BYOK note
        below), scan_mode=<quick|thorough> (optional — defaults to
        "thorough" when omitted; see
        services.piping_valve_mto_extractor._scan_mode_params for exactly
        what each resolves to), pms_data=<JSON array string> (optional —
        a list of {code, pms_class?, rating?, facing?} dicts parsed
        client-side from an uploaded PMS Excel/CSV; overlays and takes
        priority over legend-sourced lookups for the same code — see
        services.piping_valve_mto_extractor.extract_valve_mto_streaming's
        own docstring)
        → 202 {status: "queued", job_id, ...}

    GET  /api/v1/pid-verification/extract-valve-mto/<job_id>/   (poll)
        → live job snapshot (progress, partial rows, final result)

Long-running extractions (Thorough Scan's 2x2-tile/2-pass design can mean
25+ minutes end-to-end on a dense multi-page PDF): extract_valve_mto_view
below has NO execution timeout of its own to raise — confirmed by reading
it end to end. It stages the upload, calls start_job() (which spawns an
independent daemon thread and returns a job_id immediately — see
services.valve_mto_job_store.start_job/_run_in_thread), and responds 202
without ever blocking on the extraction itself. The background thread
then runs for exactly as long as its Vision batches take, with no
timeout/kill-switch cutting it off early. The real per-call ceiling that
WAS too tight for Thorough Scan is services.piping_valve_mto_extractor's
own VISION_TIMEOUT_SECS (raised 90s -> 300s) and max_tokens (raised to
40000) — see that module's own comments on both.

    POST /api/v1/pid-verification/extract-valve-mto/test-key/
        ({provider, api_key}) — connectivity check for a BYOK key before
        the user commits to an actual extraction run.

In central mode the Valve MTO page sends the provider, not an API key. Workers
resolve OpenAI or Claude credentials from the central registry. Managed
credentials always take priority and fail closed when unavailable.
Legacy vision_api_key inputs/environment keys remain compatible only
when the selected provider has no managed configuration.

Explicit BYOK: vision_credential_mode=byok requires vision_api_key and
uses that personal key independently of central configuration/disabling.
No personal key is persisted in the job snapshot. Omitted credential mode
is central; central mode's managed precedence remains unchanged.

Legend Sheet integration: NOT handled by any endpoint in this file.
Legend sheets are managed entirely through the EXISTING, shared
LegendSheetsModal (frontend) / apps.pid_checker_v2 legend system
(PidCheckerV2LegendSheet, sections 'valve'/'piping'/'line_list'/
'scope_symbols'/'limit_line') — the same one P&ID Verification V1/V2
already use. extract_valve_mto_view below passes request.user.pk into
start_job purely so services.piping_valve_mto_extractor can look up
THIS user's own active legend sheets for those sections at extraction
time (see that module's _build_legend_context) — no new legend
upload/list/detail endpoints exist or are needed here.

CORRECTION: an earlier version of this file reused
apps.pid_verification's OWN PIDVLegendSheet model/AI-extraction
pipeline (a completely different, unstructured, AI-Vision-extracted
legend system with no "Valve" section) — that was the wrong system and
has been removed. apps.pid_checker_v2's structured, regex-rule legend
system (with per-section activation) is the correct one, per the
existing LegendSheetsModal component.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile

from django.http import JsonResponse
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .services.valve_mto_job_store import JobStore, start_job

logger = logging.getLogger(__name__)

# ─── Soft-coded constants ──────────────────────────────────────────────
ALLOWED_EXTENSIONS = ('.pdf',)
MAX_FILE_SIZE_MB   = 50
FILE_FIELD_NAMES   = ('pid_file', 'valve_file', 'pfd_file', 'file')


def _resolve_uploaded_file(request):
    for name in FILE_FIELD_NAMES:
        f = request.FILES.get(name)
        if f is not None:
            return f, name
    return None, None


@api_view(['POST'])
@parser_classes([MultiPartParser, FormParser])
@permission_classes([IsAuthenticated])
def extract_valve_mto_view(request):
    """Start a Valve MTO extraction job. Returns immediately with a job_id."""
    upload, used_name = _resolve_uploaded_file(request)
    if upload is None:
        return JsonResponse(
            {
                'status': 'error',
                'message': 'No file uploaded. Send the PDF under one of: ' + ', '.join(FILE_FIELD_NAMES),
            },
            status=400,
        )

    if not upload.name.lower().endswith(ALLOWED_EXTENSIONS):
        return JsonResponse(
            {'status': 'error', 'message': 'Only PDF files are accepted.'},
            status=400,
        )

    if upload.size > MAX_FILE_SIZE_MB * 1024 * 1024:
        return JsonResponse(
            {'status': 'error', 'message': f'File exceeds {MAX_FILE_SIZE_MB} MB limit.'},
            status=400,
        )

    extraction_mode = (request.data.get('extraction_mode') or 'vision').strip().lower()
    if extraction_mode not in ('local', 'vision'):
        return JsonResponse({'status': 'error', 'message': 'Choose local or vision extraction.'}, status=400)
    vision_provider = None
    vision_api_key = None
    vision_model = None
    credential_mode = 'central'
    if extraction_mode == 'vision':
        from .services.piping_valve_mto_extractor import SUPPORTED_PROVIDERS
        vision_provider = (request.data.get('vision_provider') or 'openai').strip().lower() or 'openai'
        if vision_provider not in SUPPORTED_PROVIDERS:
            return JsonResponse({'status': 'error', 'message': 'Choose OpenAI or Claude for AI Vision extraction.'}, status=400)
        vision_api_key = (request.data.get('vision_api_key') or '').strip() or None
        # Caller-chosen model for the selected provider (frontend's "AI
        # Model" dropdown) — optional; empty/omitted falls back to
        # piping_valve_mto_extractor.VISION_MODELS[provider] untouched.
        vision_model = (request.data.get('vision_model') or '').strip() or None
        credential_mode = (request.data.get('vision_credential_mode') or 'central').strip().lower()
        if credential_mode not in ('central', 'byok'):
            return JsonResponse({'status': 'error', 'message': 'Choose central credentials or BYOK.'}, status=400)
        if credential_mode == 'byok' and not vision_api_key:
            return JsonResponse({'status': 'error', 'message': 'Enter a personal API key for BYOK.'}, status=400)

    # Stage the upload to a temp file (worker thread cleans up on completion).
    try:
        tmp = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False, prefix='valvemto_')
        for chunk in upload.chunks():
            tmp.write(chunk)
        tmp.flush()
        tmp.close()
        tmp_path = tmp.name
    except Exception as exc:
        logger.exception('Failed to stage upload: %s', exc)
        return JsonResponse(
            {'status': 'error', 'message': f'Could not stage upload: {exc}'},
            status=500,
        )

    # Defaults to 'thorough' when not provided at all — same default
    # services.piping_valve_mto_extractor._scan_mode_params itself falls
    # back to, so this is belt-and-suspenders rather than the only place
    # the default is enforced; kept explicit here too so it's visible
    # right where the request is parsed, not just buried in the extractor.
    scan_mode = (request.data.get('scan_mode') or '').strip().lower() or 'thorough'

    # Optional PMS (Piping Material Specification) overlay — parsed
    # client-side from an uploaded Excel/CSV by ValveMTO.jsx's PMS
    # Document stage, sent here as a JSON-encoded list of
    # {code, pms_class?, rating?, facing?} dicts. Malformed JSON or a
    # non-list payload is rejected with 400 rather than let a bad request
    # crash the whole upload — see services.piping_valve_mto_extractor's
    # own docstring for how this overlays (and takes priority over)
    # legend-sourced lookups for the same code.
    pms_data_raw = request.data.get('pms_data')
    pms_data = None
    if pms_data_raw:
        try:
            parsed = json.loads(pms_data_raw)
            if not isinstance(parsed, list):
                raise ValueError('pms_data must be a JSON array')
        except (TypeError, ValueError):
            # The upload was already staged to tmp_path above — clean it up
            # before bailing out, same as the start_job() failure path below.
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            return JsonResponse({'status': 'error', 'message': 'pms_data must be a valid JSON array.'}, status=400)
        pms_data = parsed

    try:
        job_id = start_job(
            tmp_path, upload.name, vision_provider=vision_provider, vision_api_key=vision_api_key,
            vision_model=vision_model, pms_data=pms_data,
            user_id=request.user.pk, scan_mode=scan_mode,
            extraction_mode=extraction_mode,
            credential_mode=credential_mode,
        )
    except Exception as exc:
        logger.exception('Failed to start extraction job: %s', exc)
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        return JsonResponse(
            {'status': 'error', 'message': f'Could not start job: {exc}'},
            status=500,
        )

    return JsonResponse(
        {
            'status':          'queued',
            'job_id':          job_id,
            'source_filename': upload.name,
            'upload_field':    used_name,
        },
        status=202,
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def extract_valve_mto_status_view(request, job_id: str):
    """Return the current snapshot of a Valve MTO extraction job."""
    snap = JobStore.get(job_id)
    if not snap:
        return JsonResponse(
            {'status': 'error', 'message': 'Job not found or expired.'},
            status=404,
        )
    return JsonResponse({'job_id': job_id, **snap}, status=200)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def test_valve_mto_api_key_view(request):
    """BYOK connectivity check — mirrors
    apps.instrument_io_workflow.views.vision_test_key_view's contract
    ({provider, api_key} -> {valid, message}), reimplemented independently
    here (piping_valve_mto_extractor.test_api_key) per this codebase's
    established per-app isolation convention for BYOK vision features —
    intentionally does NOT silently test an admin-managed key instead of
    the one the user just typed (see that function's own docstring)."""
    from .services.piping_valve_mto_extractor import test_api_key

    provider = request.data.get('provider')
    api_key  = request.data.get('api_key')
    model    = request.data.get('model')  # optional BYOK model dropdown choice
    valid, message = test_api_key(provider, api_key, model=model)
    return Response({'valid': valid, 'message': message})
