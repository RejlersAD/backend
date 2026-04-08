"""
SLD Verification — API Views
===============================
Projects:
  GET    /api/v1/sld-verification/projects/              → list user's projects
  POST   /api/v1/sld-verification/projects/              → create project
  PUT    /api/v1/sld-verification/projects/<project_id>/ → update project
  DELETE /api/v1/sld-verification/projects/<project_id>/ → delete project

Documents:
  POST   /api/v1/sld-verification/upload-sld/            → upload (pass project_id in form)
  GET    /api/v1/sld-verification/status/<document_id>/  → poll status
  GET    /api/v1/sld-verification/results/<document_id>/ → full findings
  GET    /api/v1/sld-verification/export/excel/<document_id>/
  GET    /api/v1/sld-verification/export/pdf/<document_id>/
  GET    /api/v1/sld-verification/list/                  → user document history
  DELETE /api/v1/sld-verification/delete/<document_id>/  → remove document
"""
import logging
import threading
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.rbac.permissions import HasDisciplineAccess
from apps.core.queue_service import RobustQueueService, QueueUnavailableException

from .models import SLDProject, SLDDocument, SLDDrawing, SLDFinding
from .serializers import (
    SLDProjectSerializer,
    SLDProjectCreateSerializer,
    SLDDocumentSerializer,
    SLDDocumentListSerializer,
    SLDFindingSerializer,
    SLDFindingUpdateSerializer,
    UploadSerializer,
)
from .services.consistency import compute_file_hash, check_cache

logger = logging.getLogger(__name__)

# SOFT-CODED: Worker availability cache TTL (seconds).
_WORKER_CHECK_TTL = int(getattr(settings, 'SLDV_WORKER_CHECK_TTL', 60))
_WORKER_CHECK_CACHE_KEY = 'sldv_celery_worker_active'


def _has_active_celery_workers() -> bool:
    """
    Return True if at least one Celery worker is listening.
    Result is cached to avoid latency on every upload request.
    """
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


# ===========================================================================
# PROJECT CRUD
# ===========================================================================

@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def projects(request):
    """
    GET  -> list all projects belonging to the authenticated user.
    POST -> create a new project.
    """
    if request.method == "GET":
        qs = SLDProject.objects.filter(created_by=request.user)
        return Response(SLDProjectSerializer(qs, many=True).data)

    # POST -- create
    serializer = SLDProjectCreateSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    project = serializer.save(created_by=request.user)
    return Response(SLDProjectSerializer(project).data, status=status.HTTP_201_CREATED)


@api_view(["PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def project_detail(request, project_id):
    """
    PUT    -> update project name / description.
    DELETE -> delete project (documents become project-less, not deleted).
    """
    project = _get_project_or_404(project_id, request.user)
    if project is None:
        return Response({"error": "Project not found"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "PUT":
        serializer = SLDProjectCreateSerializer(project, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(SLDProjectSerializer(project).data)

    # DELETE
    project.delete()
    return Response({"message": "Project deleted"}, status=status.HTTP_200_OK)


# ===========================================================================
# UPLOAD
# ===========================================================================

@api_view(["POST"])
@permission_classes([IsAuthenticated, HasDisciplineAccess])
@parser_classes([MultiPartParser, FormParser])
def upload_sld(request):
    """
    Accept an SLD file, optionally associate with a project_id,
    create an SLDDocument, enqueue background processing.

    RBAC: User must have "engineering" or "electrical" discipline or be admin.
    Queue: Intelligent fallback to synchronous processing if queue unavailable.
    """
    upload_sld.module_required = 'sld_verification'
    serializer = UploadSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    uploaded_file = serializer.validated_data["file"]
    project_id    = serializer.validated_data.get("project_id")

    allowed_ext = {"pdf", "png", "jpg", "jpeg", "tiff", "tif", "dwg"}
    file_ext    = uploaded_file.name.rsplit(".", 1)[-1].lower() if "." in uploaded_file.name else ""
    if file_ext not in allowed_ext:
        return Response(
            {"error": f"Unsupported file type: {file_ext}. Allowed: {', '.join(sorted(allowed_ext))}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Resolve project (must belong to the user)
    project = None
    if project_id:
        project = _get_project_or_404(str(project_id), request.user)
        if project is None:
            return Response({"error": "Project not found or access denied"}, status=status.HTTP_404_NOT_FOUND)

    # Deterministic cache check (only reuse if same project)
    file_hash = compute_file_hash(uploaded_file)
    cached    = check_cache(file_hash)
    if cached and cached.project == project:
        if cached.status == SLDDocument.Status.COMPLETED and not cached.drawings.exists():
            logger.warning(
                "[SLDVUpload] Ignoring degraded cache hash=%s doc_id=%s (0 drawings)",
                file_hash, cached.document_id,
            )
        elif cached.status == SLDDocument.Status.FAILED:
            logger.warning(
                "[SLDVUpload] Ignoring failed cache hash=%s doc_id=%s",
                file_hash, cached.document_id,
            )
        else:
            logger.info("[SLDVUpload] Cache hit hash=%s doc_id=%s", file_hash, cached.document_id)
            return Response(
                {
                    "document_id": str(cached.document_id),
                    "status":      cached.status,
                    "cached":      True,
                    "message":     "Identical file already processed – returning cached results.",
                    "project_id":  str(project.project_id) if project else None,
                },
                status=status.HTTP_200_OK,
            )

    # Create new document record
    doc = SLDDocument.objects.create(
        file_name     = uploaded_file.name,
        file_hash     = file_hash,
        original_file = uploaded_file,
        uploaded_by   = request.user,
        project       = project,
        status        = SLDDocument.Status.UPLOADED,
    )

    # Enqueue Celery task with intelligent fallback
    try:
        from .tasks import process_sld_document, _resolve_file_path

        # ── Shared synchronous processing pipeline ─────────────────────────
        def _run_sync_pipeline(doc_id: str) -> None:
            """
            Execute the full SLD processing pipeline synchronously.
            Used both as the RobustQueueService sync_fallback (when Redis is
            down) AND as a background-thread fallback (when no Celery workers
            are active in production).
            """
            logger.warning("[SLDVUpload] Running sync pipeline for doc_id=%s", doc_id)
            try:
                from apps.sld_verification.services.analysis import analyse_sld_document

                _doc = SLDDocument.objects.get(document_id=doc_id)
                _doc.status = SLDDocument.Status.PROCESSING
                _doc.save(update_fields=["status"])

                file_path = _resolve_file_path(_doc)
                analyse_sld_document(str(_doc.document_id), file_path)

                _doc.status = SLDDocument.Status.COMPLETED
                _doc.save(update_fields=["status"])
                logger.info("[SLDVUpload] Sync pipeline completed for doc_id=%s", doc_id)

            except Exception as exc:
                logger.error("[SLDVUpload] Sync pipeline failed for doc_id=%s: %s", doc_id, exc, exc_info=True)
                try:
                    _doc = SLDDocument.objects.get(document_id=doc_id)
                    _doc.status = SLDDocument.Status.FAILED
                    _doc.error_message = f"Sync processing failed: {exc}"
                    _doc.save(update_fields=["status", "error_message"])
                except Exception:
                    pass

        # ── Dispatch: Celery when workers exist, thread otherwise ──────────
        worker_check_enabled = getattr(settings, 'SLDV_WORKER_CHECK_ENABLED', True)
        use_celery = (not worker_check_enabled) or _has_active_celery_workers()

        if not use_celery:
            logger.info(
                "[SLDVUpload] No active Celery workers detected – "
                "processing doc_id=%s in background thread.", doc.document_id
            )
            t = threading.Thread(
                target=_run_sync_pipeline,
                args=(str(doc.document_id),),
                daemon=True,
                name=f"sldv-sync-{doc.document_id}",
            )
            t.start()
        else:
            try:
                RobustQueueService.queue_task(
                    process_sld_document,
                    args=(str(doc.document_id),),
                    sync_fallback=_run_sync_pipeline,
                    max_retries=3,
                )
                logger.info("[SLDVUpload] Task queued via Celery: doc_id=%s", doc.document_id)
            except QueueUnavailableException as queue_exc:
                logger.error("[SLDVUpload] Queue unavailable and sync fallback failed: %s", queue_exc)
                doc.status = SLDDocument.Status.FAILED
                doc.error_message = "Processing service unavailable. Please try again."
                doc.save(update_fields=["status", "error_message"])
                return Response(
                    {"error": "Processing queue unavailable. Please try again shortly."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

    except Exception as exc:
        logger.error("[SLDVUpload] Unexpected error setting up task: %s", exc)
        doc.status = SLDDocument.Status.FAILED
        doc.error_message = f"Failed to start processing: {exc}"
        doc.save(update_fields=["status", "error_message"])
        return Response(
            {"error": "Failed to process document. Please try again."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return Response(
        {
            "document_id": str(doc.document_id),
            "status":      doc.status,
            "file_name":   doc.file_name,
            "project_id":  str(project.project_id) if project else None,
            "message":     "File uploaded successfully. Processing started.",
        },
        status=status.HTTP_202_ACCEPTED,
    )


# ===========================================================================
# STATUS / RESULTS / EXPORTS / LIST / DELETE
# ===========================================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_status(request, document_id):
    doc = _get_doc_or_404(document_id, request.user)
    if doc is None:
        return Response({"error": "Document not found"}, status=status.HTTP_404_NOT_FOUND)

    return Response({
        "document_id":   str(doc.document_id),
        "status":        doc.status,
        "file_name":     doc.file_name,
        "error_message": doc.error_message or None,
        "excel_s3_url":  doc.excel_s3_url or None,
        "pdf_s3_url":    doc.pdf_s3_url   or None,
        "project_id":    str(doc.project.project_id) if doc.project else None,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_results(request, document_id):
    doc = _get_doc_or_404(document_id, request.user)
    if doc is None:
        return Response({"error": "Document not found"}, status=status.HTTP_404_NOT_FOUND)

    if doc.status not in (SLDDocument.Status.COMPLETED, SLDDocument.Status.FAILED):
        return Response({"error": "Processing not yet complete", "status": doc.status}, status=status.HTTP_202_ACCEPTED)

    return Response(SLDDocumentSerializer(doc).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def export_excel(request, document_id):
    doc = _get_doc_or_404(document_id, request.user)
    if doc is None:
        return Response({"error": "Document not found"}, status=status.HTTP_404_NOT_FOUND)

    if doc.status != SLDDocument.Status.COMPLETED:
        return Response({"error": "Document not yet completed"}, status=status.HTTP_400_BAD_REQUEST)

    from .services.export_service import generate_excel
    data = generate_excel(doc)
    if not data:
        return Response({"error": "Excel generation failed"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    safe_name = doc.file_name.rsplit(".", 1)[0].replace(" ", "_")
    response = HttpResponse(
        data,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="sldv_findings_{safe_name}.xlsx"'
    return response


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def export_pdf(request, document_id):
    doc = _get_doc_or_404(document_id, request.user)
    if doc is None:
        return Response({"error": "Document not found"}, status=status.HTTP_404_NOT_FOUND)

    if doc.status != SLDDocument.Status.COMPLETED:
        return Response({"error": "Document not yet completed"}, status=status.HTTP_400_BAD_REQUEST)

    from .services.export_service import generate_pdf
    data = generate_pdf(doc)
    if not data:
        return Response({"error": "PDF generation failed"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    safe_name = doc.file_name.rsplit(".", 1)[0].replace(" ", "_")
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="sldv_report_{safe_name}.pdf"'
    return response


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_documents(request):
    """List user documents; optionally filter by project_id."""
    qs = SLDDocument.objects.filter(uploaded_by=request.user)

    project_id = request.query_params.get("project_id")
    if project_id:
        qs = qs.filter(project__project_id=project_id)

    qs = qs.order_by("-uploaded_at")[:100]
    return Response(SLDDocumentListSerializer(qs, many=True).data)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def delete_document(request, document_id):
    doc = _get_doc_or_404(document_id, request.user)
    if doc is None:
        return Response({"error": "Document not found"}, status=status.HTTP_404_NOT_FOUND)
    doc.delete()
    return Response({"message": "Document deleted"}, status=status.HTTP_200_OK)


# ===========================================================================
# Engineer Review — finding overrides
# ===========================================================================

@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def update_finding(request, finding_id):
    """
    PATCH /api/v1/sld-verification/findings/<finding_id>/
    Allows the document owner to override severity and/or status of a finding.
    """
    try:
        finding = SLDFinding.objects.select_related('drawing__document').get(pk=finding_id)
    except SLDFinding.DoesNotExist:
        return Response({"error": "Finding not found"}, status=status.HTTP_404_NOT_FOUND)

    doc = finding.drawing.document
    if doc.uploaded_by != request.user:
        return Response({"error": "Access denied"}, status=status.HTTP_403_FORBIDDEN)

    serializer = SLDFindingUpdateSerializer(finding, data=request.data, partial=True)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    serializer.save()

    # Clear cached S3 export URLs so next export regenerates with updated findings
    update_fields = []
    if doc.excel_s3_url:
        doc.excel_s3_url = ''
        update_fields.append('excel_s3_url')
    if doc.pdf_s3_url:
        doc.pdf_s3_url = ''
        update_fields.append('pdf_s3_url')
    if update_fields:
        doc.save(update_fields=update_fields)

    return Response(SLDFindingSerializer(finding).data)


# ===========================================================================
# Drawing image renderer
# ===========================================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def drawing_image(request, document_id, page_index):
    """
    Render the specified page of an uploaded SLD document as a PNG image.
    For PDFs  → PyMuPDF rasterises the page at 2× (150 dpi).
    For images → file served directly (or PIL converts to PNG).
    """
    doc = _get_doc_or_404(document_id, request.user)
    if doc is None:
        return Response({"error": "Document not found"}, status=status.HTTP_404_NOT_FOUND)

    if not doc.original_file:
        return Response({"error": "Original file not stored"}, status=status.HTTP_404_NOT_FOUND)

    try:
        file_path = doc.original_file.path
    except Exception:
        return Response({"error": "File path unavailable"}, status=status.HTTP_404_NOT_FOUND)

    ext = Path(file_path).suffix.lower().lstrip(".")
    png_data = None

    if ext == "pdf":
        try:
            import fitz  # PyMuPDF
            pdf_doc = fitz.open(file_path)
            if page_index >= len(pdf_doc):
                pdf_doc.close()
                return Response({"error": "Page index out of range"}, status=status.HTTP_400_BAD_REQUEST)
            page = pdf_doc[page_index]
            mat = fitz.Matrix(2.0, 2.0)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            png_data = pix.tobytes("png")
            pdf_doc.close()
        except ImportError:
            return Response({"error": "PyMuPDF not available"}, status=status.HTTP_501_NOT_IMPLEMENTED)
        except Exception as exc:
            logger.warning("[SLDVDrawingImage] PDF render failed: %s", exc)
            return Response({"error": "Failed to render PDF page"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    elif ext in {"png", "jpg", "jpeg", "tiff", "tif"}:
        try:
            with open(file_path, "rb") as f:
                raw = f.read()
            if ext == "png":
                png_data = raw
            else:
                from PIL import Image
                import io
                img = Image.open(io.BytesIO(raw)).convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                png_data = buf.getvalue()
        except Exception as exc:
            logger.warning("[SLDVDrawingImage] Image read failed: %s", exc)
            return Response({"error": "Failed to read image file"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    else:
        return Response({"error": f"Unsupported file type: {ext}"}, status=status.HTTP_400_BAD_REQUEST)

    response = HttpResponse(png_data, content_type="image/png")
    response["Cache-Control"] = "private, max-age=3600"
    response["Content-Length"] = len(png_data)
    return response


# ===========================================================================
# Helpers
# ===========================================================================

def _get_doc_or_404(document_id: str, user):
    try:
        doc = SLDDocument.objects.get(document_id=document_id)
        user_obj = getattr(user, "user", user)
        if doc.uploaded_by == user or getattr(user_obj, "is_staff", False):
            return doc
        return None
    except SLDDocument.DoesNotExist:
        return None


def _get_project_or_404(project_id: str, user):
    try:
        project = SLDProject.objects.get(project_id=project_id)
        user_obj = getattr(user, "user", user)
        if project.created_by == user or getattr(user_obj, "is_staff", False):
            return project
        return None
    except SLDProject.DoesNotExist:
        return None
