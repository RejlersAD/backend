"""
Pump Hydraulic Extraction View
==============================

Single, additive endpoint that accepts a PFD / pump-data PDF and returns the
fields needed by the front-end Pump Hydraulic Calculation form.

The view is intentionally synchronous and lightweight:
    * extraction is fast (text + optional Vision on <=4 pages)
    * the response is consumed directly by the form (no job/poll cycle needed)
    * if Vision is unavailable, only text-based fields are returned

POST /api/v1/process-datasheet/datasheets/extract-pump-hydraulic/
Body: multipart/form-data with `pump_file` (PDF, required)

Response shape::

    {
      "status": "ok",
      "engine": "text" | "text+vision",
      "page_count": int,
      "fields": { "<form_field>": "<string>", ... },
      "provenance": { "<form_field>": "text" | "vision", ... },
      "warnings": [ ... ]
    }
"""
from __future__ import annotations

import logging
import os
import tempfile

from django.http import JsonResponse
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated

from .services.pump_hydraulic_extractor import extract_pump_hydraulic

logger = logging.getLogger(__name__)

# ─── Soft-coded constants ──────────────────────────────────────────────────
ALLOWED_EXTENSIONS = ('.pdf',)
MAX_FILE_SIZE_MB = 50
FILE_FIELD_NAMES = ('pump_file', 'pid_file', 'pfd_file', 'file')


def _resolve_uploaded_file(request):
    """Accept any of several common field names for the upload."""
    for name in FILE_FIELD_NAMES:
        f = request.FILES.get(name)
        if f is not None:
            return f, name
    return None, None


@api_view(['POST'])
@parser_classes([MultiPartParser, FormParser])
@permission_classes([IsAuthenticated])
def extract_pump_hydraulic_view(request):
    upload, used_name = _resolve_uploaded_file(request)
    if upload is None:
        return JsonResponse(
            {
                'status': 'error',
                'message': (
                    'No file uploaded. Send the PDF under one of: '
                    + ', '.join(FILE_FIELD_NAMES)
                ),
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
            {
                'status': 'error',
                'message': f'File exceeds {MAX_FILE_SIZE_MB} MB limit.',
            },
            status=400,
        )

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix='.pdf', delete=False, prefix='pumphydr_'
        ) as tmp:
            for chunk in upload.chunks():
                tmp.write(chunk)
            tmp_path = tmp.name

        result = extract_pump_hydraulic(tmp_path)
        result['source_filename'] = upload.name
        result['upload_field'] = used_name
        return JsonResponse(result, status=200)

    except Exception as exc:
        logger.exception('Pump hydraulic extraction failed: %s', exc)
        return JsonResponse(
            {'status': 'error', 'message': f'Extraction failed: {exc}'},
            status=500,
        )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
