"""
Piping Valve MTO — Async Extraction Endpoints
=============================================

Two endpoints to avoid HTTP timeouts on long extractions:

    POST /api/v1/pid-verification/extract-valve-mto/        (start)
        multipart/form-data: pid_file=<PDF>
        → 202 {status: "queued", job_id, ...}

    GET  /api/v1/pid-verification/extract-valve-mto/<job_id>/   (poll)
        → live job snapshot (progress, partial rows, final result)
"""
from __future__ import annotations

import logging
import os
import tempfile

from django.http import JsonResponse
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated

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

    try:
        job_id = start_job(tmp_path, upload.name)
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
