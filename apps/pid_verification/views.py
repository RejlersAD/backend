"""
P&ID Verification — API Views
===============================
Projects:
  GET    /api/v1/pid-verification/projects/              → list user's projects
  POST   /api/v1/pid-verification/projects/              → create project
  PUT    /api/v1/pid-verification/projects/<project_id>/ → update project
  DELETE /api/v1/pid-verification/projects/<project_id>/ → delete project

Documents:
  POST   /api/v1/pid-verification/upload-pid/            → upload (pass project_id in form)
  GET    /api/v1/pid-verification/status/<document_id>/  → poll status
  GET    /api/v1/pid-verification/results/<document_id>/ → full findings
  GET    /api/v1/pid-verification/export/excel/<document_id>/
  GET    /api/v1/pid-verification/export/pdf/<document_id>/
  GET    /api/v1/pid-verification/list/                  → user document history
  DELETE /api/v1/pid-verification/delete/<document_id>/  → remove document
"""
import logging

from django.http import HttpResponse
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.rbac.permissions import HasDisciplineAccess
from apps.core.queue_service import RobustQueueService, QueueUnavailableException

from .models import PIDVProject, PIDVDocument, PIDVFinding
from .serializers import (
    PIDVProjectSerializer,
    PIDVProjectCreateSerializer,
    PIDVDocumentSerializer,
    PIDVDocumentListSerializer,
    PIDVFindingSerializer,
    PIDVFindingUpdateSerializer,
    UploadSerializer,
)
from .services.consistency import compute_file_hash, check_cache

logger = logging.getLogger(__name__)


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
        qs = PIDVProject.objects.filter(created_by=request.user)
        return Response(PIDVProjectSerializer(qs, many=True).data)

    # POST -- create
    serializer = PIDVProjectCreateSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    project = serializer.save(created_by=request.user)
    return Response(PIDVProjectSerializer(project).data, status=status.HTTP_201_CREATED)


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
        serializer = PIDVProjectCreateSerializer(project, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(PIDVProjectSerializer(project).data)

    # DELETE
    project.delete()
    return Response({"message": "Project deleted"}, status=status.HTTP_200_OK)


# ===========================================================================
# UPLOAD
# ===========================================================================

@api_view(["POST"])
@permission_classes([IsAuthenticated, HasDisciplineAccess])
@parser_classes([MultiPartParser, FormParser])
def upload_pid(request):
    """
    Accept a P&ID file, optionally associate with a project_id,
    create a PIDVDocument, enqueue background processing.
    
    RBAC: User must have "engineering" or "qa_qc" discipline or be admin.
    Queue: Intelligent fallback to synchronous processing if queue unavailable.
    """
    # Set module requirement for discipline check
    upload_pid.module_required = 'pid_verification'
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
        logger.info("[PIDVUpload] Cache hit hash=%s doc_id=%s", file_hash, cached.document_id)
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
    doc = PIDVDocument.objects.create(
        file_name     = uploaded_file.name,
        file_hash     = file_hash,
        original_file = uploaded_file,
        uploaded_by   = request.user,
        project       = project,
        status        = PIDVDocument.Status.UPLOADED,
    )

    # Enqueue Celery task with intelligent fallback
    try:
        from .tasks import process_pid_document
        
        # Define synchronous fallback (for when queue unavailable)
        def sync_process_fallback(doc_id):
            """Synchronous processing fallback called when queue fails"""
            logger.warning(f"[PIDVUpload] Using synchronous fallback for doc_id={doc_id}")
            try:
                # Import locally to avoid circular import
                from apps.pid_verification.services.segmentation import segment_document
                from apps.pid_verification.services.extraction import extract_drawing
                from apps.pid_verification.services.graph_builder import build_graph
                from apps.pid_verification.services.rule_engine import run_rules
                
                doc = PIDVDocument.objects.get(document_id=doc_id)
                doc.status = PIDVDocument.Status.PROCESSING
                doc.save(update_fields=["status", "updated_at"])
                
                # Run synchronous pipeline
                doc.status = PIDVDocument.Status.PROCESSING
                segment_document(doc)
                extract_drawing(doc)
                build_graph(doc)
                run_rules(doc)
                
                doc.status = PIDVDocument.Status.COMPLETED
                doc.save(update_fields=["status", "updated_at"])
                logger.info(f"[PIDVUpload] Sync fallback completed for doc_id={doc_id}")
            except Exception as e:
                logger.error(f"[PIDVUpload] Sync fallback failed: {e}", exc_info=True)
                try:
                    doc.status = PIDVDocument.Status.FAILED
                    doc.error_message = f"Sync processing failed: {e}"
                    doc.save(update_fields=["status", "error_message", "updated_at"])
                except:
                    pass
        
        # Use robust queue service with fallback
        try:
            result = RobustQueueService.queue_task(
                process_pid_document,
                args=(str(doc.document_id),),
                sync_fallback=sync_process_fallback,
                max_retries=3
            )
            logger.info("[PIDVUpload] Task queued (async or sync fallback): doc_id=%s", doc.document_id)
        except QueueUnavailableException as queue_exc:
            logger.error("[PIDVUpload] Queue unavailable and sync fallback failed: %s", queue_exc)
            doc.status = PIDVDocument.Status.FAILED
            doc.error_message = "Processing service unavailable. Please try again."
            doc.save(update_fields=["status", "error_message", "updated_at"])
            return Response(
                {"error": "Processing queue unavailable. Please try again shortly."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
    
    except Exception as exc:
        logger.error("[PIDVUpload] Unexpected error setting up task: %s", exc)
        doc.status = PIDVDocument.Status.FAILED
        doc.error_message = f"Failed to start processing: {exc}"
        doc.save(update_fields=["status", "error_message", "updated_at"])
        return Response(
            {"error": "Failed to process document. Please try again."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # Soft-hook: keep cross-feature snapshot updated in background (non-blocking)
    try:
        from apps.cross_recommendation.tasks import sync_s3_snapshot
        exec_result = RobustQueueService.queue_task(
            sync_s3_snapshot,
            max_retries=1
        )
        logger.debug("[PIDVUpload] Queued cross-recommendation snapshot sync")
    except Exception as exc:
        logger.warning("[PIDVUpload] Cross snapshot sync skipped: %s", exc)

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
        "updated_at":    doc.updated_at,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_results(request, document_id):
    doc = _get_doc_or_404(document_id, request.user)
    if doc is None:
        return Response({"error": "Document not found"}, status=status.HTTP_404_NOT_FOUND)

    if doc.status not in (PIDVDocument.Status.COMPLETED, PIDVDocument.Status.FAILED):
        return Response({"error": "Processing not yet complete", "status": doc.status}, status=status.HTTP_202_ACCEPTED)

    return Response(PIDVDocumentSerializer(doc).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def export_excel(request, document_id):
    doc = _get_doc_or_404(document_id, request.user)
    if doc is None:
        return Response({"error": "Document not found"}, status=status.HTTP_404_NOT_FOUND)

    if doc.status != PIDVDocument.Status.COMPLETED:
        return Response({"error": "Document not yet completed"}, status=status.HTTP_400_BAD_REQUEST)

    # Always regenerate in-memory so the response is served from the same origin
    # (avoids S3 CORS errors that occur when axios follows a cross-origin redirect).
    from .services.export_service import generate_excel
    data = generate_excel(doc)
    if not data:
        return Response({"error": "Excel generation failed"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    safe_name = doc.file_name.rsplit(".", 1)[0].replace(" ", "_")
    response = HttpResponse(
        data,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="pidv_findings_{safe_name}.xlsx"'
    return response


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def export_pdf(request, document_id):
    doc = _get_doc_or_404(document_id, request.user)
    if doc is None:
        return Response({"error": "Document not found"}, status=status.HTTP_404_NOT_FOUND)

    if doc.status != PIDVDocument.Status.COMPLETED:
        return Response({"error": "Document not yet completed"}, status=status.HTTP_400_BAD_REQUEST)

    # Always regenerate in-memory so the response is served from the same origin
    # (avoids S3 CORS errors that occur when axios follows a cross-origin redirect).
    from .services.export_service import generate_pdf
    data = generate_pdf(doc)
    if not data:
        return Response({"error": "PDF generation failed"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    safe_name = doc.file_name.rsplit(".", 1)[0].replace(" ", "_")
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="pidv_report_{safe_name}.pdf"'
    return response


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_documents(request):
    """List user documents; optionally filter by project_id."""
    qs = PIDVDocument.objects.filter(uploaded_by=request.user)

    project_id = request.query_params.get("project_id")
    if project_id:
        qs = qs.filter(project__project_id=project_id)

    qs = qs.order_by("-created_at")[:100]
    return Response(PIDVDocumentListSerializer(qs, many=True).data)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def delete_document(request, document_id):
    doc = _get_doc_or_404(document_id, request.user)
    if doc is None:
        return Response({"error": "Document not found"}, status=status.HTTP_404_NOT_FOUND)
    doc.delete()

    # Soft-hook: keep cross-feature snapshot updated in background.
    try:
        from apps.cross_recommendation.tasks import sync_s3_snapshot
        sync_s3_snapshot.delay()
    except Exception as exc:
        logger.warning("[PIDVDelete] Cross snapshot queue skipped: %s", exc)

    return Response({"message": "Document deleted"}, status=status.HTTP_200_OK)


# ===========================================================================
# Engineer Review — finding overrides
# ===========================================================================

@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def update_finding(request, finding_id):
    """
    PATCH /api/v1/pid-verification/findings/<finding_id>/
    Allows the document owner to override severity and/or status of a finding.
    Clears cached S3 export URLs so the next export regenerates from updated DB data.
    """
    try:
        finding = PIDVFinding.objects.select_related('drawing__document').get(pk=finding_id)
    except PIDVFinding.DoesNotExist:
        return Response({"error": "Finding not found"}, status=status.HTTP_404_NOT_FOUND)

    doc = finding.drawing.document
    if doc.uploaded_by != request.user:
        return Response({"error": "Access denied"}, status=status.HTTP_403_FORBIDDEN)

    serializer = PIDVFindingUpdateSerializer(finding, data=request.data, partial=True)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    serializer.save()

    # Clear cached S3 export URLs so next export regenerates with updated findings
    update_fields = ['updated_at'] if hasattr(doc, 'updated_at') else []
    if doc.excel_s3_url:
        doc.excel_s3_url = ''
        update_fields.append('excel_s3_url')
    if doc.pdf_s3_url:
        doc.pdf_s3_url = ''
        update_fields.append('pdf_s3_url')
    if update_fields:
        doc.save(update_fields=update_fields)

    return Response(PIDVFindingSerializer(finding).data)


# ===========================================================================
# Helpers
# ===========================================================================

def _get_doc_or_404(document_id: str, user):
    try:
        doc = PIDVDocument.objects.get(document_id=document_id)
        user_obj = getattr(user, "user", user)
        if doc.uploaded_by == user or getattr(user_obj, "is_staff", False):
            return doc
        return None
    except PIDVDocument.DoesNotExist:
        return None


def _get_project_or_404(project_id: str, user):
    try:
        project = PIDVProject.objects.get(project_id=project_id)
        user_obj = getattr(user, "user", user)
        if project.created_by == user or getattr(user_obj, "is_staff", False):
            return project
        return None
    except PIDVProject.DoesNotExist:
        return None
