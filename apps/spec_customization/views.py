"""
Spec Customization — DRF Views.

Endpoints (mounted at /api/v1/spec-customization/):

  POST   paper-spec/upload/                 → upload PDF + queue job
  GET    paper-spec/jobs/                   → list jobs
  GET    paper-spec/jobs/<id>/              → job detail + live progress
  GET    paper-spec/jobs/<id>/classes/      → extracted classes (paginated)
  GET    paper-spec/jobs/<id>/export/       → xlsx / json export
  POST   paper-spec/jobs/<id>/cancel/       → mark job cancelled
  GET    paper-spec/classes/<id>/           → class detail + components
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
from typing import Any, Dict

from django.core.cache import cache
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import (
    PaperSpecDocument,
    PaperSpecExtractionJob,
    PipingClass,
)
from .serializers import (
    PaperSpecDocumentSerializer,
    PaperSpecExtractionJobSerializer,
    PipingClassSerializer,
    PipingClassListSerializer,
)
from .services.config import (
    SPEC_EXTRACTION_CONFIG,
    PROGRESS_CACHE_KEY_TPL,
    PARTIAL_CACHE_KEY_TPL,
)
from .tasks import extract_paper_spec

logger = logging.getLogger(__name__)


def _sha256_of_file(django_file) -> str:
    h = hashlib.sha256()
    for chunk in django_file.chunks():
        h.update(chunk)
    django_file.seek(0)
    return h.hexdigest()


def _page_count(django_file_path: str) -> int:
    try:
        import fitz
        with fitz.open(django_file_path) as doc:
            return doc.page_count
    except Exception:
        try:
            import PyPDF2
            with open(django_file_path, 'rb') as f:
                return len(PyPDF2.PdfReader(f).pages)
        except Exception:
            return 0


# ─────────────────────────────────────────────────────────────────────────────
# Upload + start extraction
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['POST'])
@parser_classes([MultiPartParser, FormParser])
@permission_classes([IsAuthenticated])
def upload_paper_spec(request):
    pdf = request.FILES.get('file') or request.FILES.get('pdf_file')
    if not pdf:
        return Response({"error": "No file uploaded (expected field: 'file' or 'pdf_file')"},
                        status=status.HTTP_400_BAD_REQUEST)

    project_id = request.data.get('project_id') or None
    title = request.data.get('title') or ''
    document_number = request.data.get('document_number') or ''

    # Dedupe by sha256 — reuse latest completed job for identical bytes.
    sha = _sha256_of_file(pdf)

    if SPEC_EXTRACTION_CONFIG.get("dedupe_by_sha256", True):
        existing = (
            PaperSpecDocument.objects
            .filter(sha256_hash=sha)
            .order_by('-created_at')
            .first()
        )
        if existing:
            latest_completed = (
                existing.jobs
                .filter(status=PaperSpecExtractionJob.STATUS_COMPLETED)
                .order_by('-completed_at')
                .first()
            )
            if latest_completed:
                return Response({
                    "deduped": True,
                    "document": PaperSpecDocumentSerializer(existing).data,
                    "job":      PaperSpecExtractionJobSerializer(latest_completed).data,
                })

    # Persist document
    doc = PaperSpecDocument.objects.create(
        file=pdf,
        original_filename=pdf.name,
        file_size_bytes=pdf.size,
        sha256_hash=sha,
        project_id=project_id,
        title=title,
        document_number=document_number,
        uploaded_by=request.user if request.user.is_authenticated else None,
    )
    doc.total_pages = _page_count(doc.file.path)
    doc.save(update_fields=['total_pages'])

    # Queue extraction job
    job = PaperSpecExtractionJob.objects.create(
        document=doc,
        status=PaperSpecExtractionJob.STATUS_QUEUED,
        created_by=request.user if request.user.is_authenticated else None,
    )

    try:
        async_result = extract_paper_spec.delay(str(job.id))
        job.celery_task_id = async_result.id or ''
        job.save(update_fields=['celery_task_id'])
    except Exception as e:
        logger.exception("[SpecExtraction] failed to queue task: %s", e)
        # Best-effort fallback: run inline (smoke test path).
        try:
            extract_paper_spec(str(job.id))
        except Exception as run_err:
            job.status = PaperSpecExtractionJob.STATUS_FAILED
            job.error_message = f"Queue failed: {e}; inline failed: {run_err}"
            job.save(update_fields=['status', 'error_message'])

    return Response({
        "deduped": False,
        "document": PaperSpecDocumentSerializer(doc).data,
        "job":      PaperSpecExtractionJobSerializer(job).data,
    }, status=status.HTTP_201_CREATED)


# ─────────────────────────────────────────────────────────────────────────────
# Job listing / detail
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_jobs(request):
    qs = PaperSpecExtractionJob.objects.all().order_by('-created_at')[:50]
    return Response(PaperSpecExtractionJobSerializer(qs, many=True).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def job_detail(request, job_id):
    job = get_object_or_404(PaperSpecExtractionJob, pk=job_id)
    data = PaperSpecExtractionJobSerializer(job).data
    # Add live progress + partial classes from cache.
    progress = cache.get(PROGRESS_CACHE_KEY_TPL.format(job_id=str(job.id))) or {}
    partial = cache.get(PARTIAL_CACHE_KEY_TPL.format(job_id=str(job.id))) or []
    data['live_progress'] = progress
    data['partial_classes_preview'] = [
        {
            'class_code':      c.get('class_code', ''),
            'class_full_code': c.get('class_full_code', ''),
            'engine':          c.get('_engine', ''),
            'components':      len(c.get('components', []) or []),
        }
        for c in partial[:50]
    ]
    return Response(data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cancel_job(request, job_id):
    job = get_object_or_404(PaperSpecExtractionJob, pk=job_id)
    if job.status in (PaperSpecExtractionJob.STATUS_COMPLETED,
                      PaperSpecExtractionJob.STATUS_FAILED,
                      PaperSpecExtractionJob.STATUS_CANCELLED):
        return Response({"ok": False, "message": f"Job already {job.status}"},
                        status=status.HTTP_400_BAD_REQUEST)
    job.status = PaperSpecExtractionJob.STATUS_CANCELLED
    job.save(update_fields=['status'])
    return Response({"ok": True, "status": job.status})


# ─────────────────────────────────────────────────────────────────────────────
# Classes
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def job_classes(request, job_id):
    job = get_object_or_404(PaperSpecExtractionJob, pk=job_id)
    qs = (job.piping_classes
              .annotate(components_count=Count('components'))
              .order_by('class_code'))
    return Response(PipingClassListSerializer(qs, many=True).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def class_detail(request, class_id):
    cls = get_object_or_404(PipingClass, pk=class_id)
    return Response(PipingClassSerializer(cls).data)


# ─────────────────────────────────────────────────────────────────────────────
# Export
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def export_job(request, job_id):
    job = get_object_or_404(PaperSpecExtractionJob, pk=job_id)
    fmt = (request.query_params.get('format') or 'json').lower()

    classes = list(
        job.piping_classes.prefetch_related('components').order_by('class_code')
    )

    if fmt == 'xlsx':
        try:
            from openpyxl import Workbook
        except ImportError:
            return Response({"error": "openpyxl not installed on backend"},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        wb = Workbook()
        wb.remove(wb.active)
        for cls in classes:
            sheet_name = (cls.class_code or 'CLASS')[:31] or 'CLASS'
            ws = wb.create_sheet(title=sheet_name)
            ws.append(['Field', 'Value'])
            ws.append(['Class Code', cls.class_code])
            ws.append(['Full Header', cls.class_full_code])
            ws.append(['Material', cls.material_grade])
            ws.append(['Rating', cls.pressure_rating])
            ws.append(['Facing', cls.flange_facing])
            ws.append(['Corrosion Allowance', cls.corrosion_allowance])
            ws.append(['Services', '; '.join(cls.service_list or [])])
            ws.append([])
            ws.append(['P/T Rating Table'])
            ws.append(['Pressure (bar-g)', 'Temperature (°C)', 'Notes'])
            for r in (cls.pt_rating_table or []):
                ws.append([r.get('pressure_bar_g'), r.get('temperature_c'), r.get('notes', '')])
            ws.append([])
            ws.append(['Components'])
            ws.append(['Type', 'Sub-Type', 'Size From', 'Size To',
                       'Schedule/Rating', 'Material Std', 'End Conn.', 'Description', 'Notes'])
            for c in cls.components.all().order_by('display_order'):
                ws.append([
                    c.component_type, c.sub_type, c.size_from, c.size_to,
                    c.schedule_or_rating, c.material_standard, c.end_connection,
                    c.description, c.notes,
                ])
        if not classes:
            ws = wb.create_sheet(title='Empty')
            ws.append(['No piping classes extracted'])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        resp = HttpResponse(
            buf.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        fname = f"paper_spec_{job.id}.xlsx"
        resp['Content-Disposition'] = f'attachment; filename="{fname}"'
        return resp

    # JSON default
    payload: Dict[str, Any] = {
        "job_id": str(job.id),
        "status": job.status,
        "document": job.document.original_filename,
        "classes": [PipingClassSerializer(c).data for c in classes],
    }
    resp = HttpResponse(json.dumps(payload, default=str), content_type='application/json')
    resp['Content-Disposition'] = f'attachment; filename="paper_spec_{job.id}.json"'
    return resp


# ─────────────────────────────────────────────────────────────────────────────
# Health / config introspection (debug-friendly)
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def config_view(request):
    """Expose soft-coded config to the UI so feature flags stay in sync."""
    return Response({
        "chunk_size_pages":          SPEC_EXTRACTION_CONFIG["chunk_size_pages"],
        "ai_engines":                SPEC_EXTRACTION_CONFIG["ai_engines"],
        "max_ai_pages_per_job":      SPEC_EXTRACTION_CONFIG["max_ai_pages_per_job"],
        "skip_ai_if_text_chars_gte": SPEC_EXTRACTION_CONFIG["skip_ai_if_text_chars_gte"],
        "dedupe_by_sha256":          SPEC_EXTRACTION_CONFIG["dedupe_by_sha256"],
    })
