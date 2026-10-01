"""
Piping Valve MTO — Async Extraction Endpoints
=============================================

Extraction + BYOK endpoints:

    POST /api/v1/pid-verification/extract-valve-mto/        (start)
        multipart/form-data: pid_file=<PDF>, vision_provider=<openai|claude>
        (optional), vision_api_key=<key> (optional — see BYOK note below)
        → 202 {status: "queued", job_id, ...}

    GET  /api/v1/pid-verification/extract-valve-mto/<job_id>/   (poll)
        → live job snapshot (progress, partial rows, final result)

    POST /api/v1/pid-verification/extract-valve-mto/test-key/
        ({provider, api_key}) — connectivity check for a BYOK key before
        the user commits to an actual extraction run.

BYOK: vision_provider/vision_api_key are both OPTIONAL. When a key is
given, that exact key is used for the requested provider (OpenAI or
Claude) — never silently substituted for the admin-managed key. When
omitted, extraction falls back to the admin-managed OpenAI credential
exactly as it always has (see
services.piping_valve_mto_extractor._resolve_vision_credential for the
full precedence). There is no admin-managed fallback for Claude.

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

    vision_provider = (request.data.get('vision_provider') or '').strip() or None
    vision_api_key  = (request.data.get('vision_api_key') or '').strip() or None

    try:
        job_id = start_job(
            tmp_path, upload.name, vision_provider=vision_provider, vision_api_key=vision_api_key,
            user_id=request.user.pk,
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
    valid, message = test_api_key(provider, api_key)
    return Response({'valid': valid, 'message': message})
