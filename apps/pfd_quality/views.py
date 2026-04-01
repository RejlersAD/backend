"""
PFD Quality Checker — API Views
=================================
Projects:
  GET    /api/v1/pfd-quality/projects/              → list user's projects
  POST   /api/v1/pfd-quality/projects/              → create project
  PUT    /api/v1/pfd-quality/projects/<project_id>/ → update project
  DELETE /api/v1/pfd-quality/projects/<project_id>/ → delete project

Documents:
  POST   /api/v1/pfd-quality/upload-pfd/            → upload PFD file
  GET    /api/v1/pfd-quality/status/<document_id>/  → poll status
  GET    /api/v1/pfd-quality/results/<document_id>/ → full findings
  GET    /api/v1/pfd-quality/export/excel/<document_id>/
  GET    /api/v1/pfd-quality/export/pdf/<document_id>/
  GET    /api/v1/pfd-quality/list/                  → user document history
  DELETE /api/v1/pfd-quality/delete/<document_id>/  → remove document

Engineer Review:
  PATCH  /api/v1/pfd-quality/findings/<finding_id>/ → override severity/status
"""
import logging

from django.http import HttpResponse
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import PFDQProject, PFDQDocument, PFDQFinding
from .serializers import (
    PFDQProjectSerializer,
    PFDQProjectCreateSerializer,
    PFDQDocumentSerializer,
    PFDQDocumentListSerializer,
    PFDQFindingSerializer,
    PFDQFindingUpdateSerializer,
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
    if request.method == "GET":
        qs = PFDQProject.objects.filter(created_by=request.user)
        return Response(PFDQProjectSerializer(qs, many=True).data)

    serializer = PFDQProjectCreateSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    project = serializer.save(created_by=request.user)
    return Response(PFDQProjectSerializer(project).data, status=status.HTTP_201_CREATED)


@api_view(["GET", "PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def project_detail(request, project_id):
    project = _get_project_or_404(project_id, request.user)
    if project is None:
        return Response({"error": "Project not found"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(PFDQProjectSerializer(project).data)

    if request.method == "PUT":
        serializer = PFDQProjectCreateSerializer(project, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(PFDQProjectSerializer(project).data)

    # DELETE
    project.delete()
    return Response({"message": "Project deleted"}, status=status.HTTP_200_OK)


# ===========================================================================
# UPLOAD & PIPELINE
# ===========================================================================

@api_view(["POST"])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def upload_pfd(request):
    serializer = UploadSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    uploaded_file = serializer.validated_data['file']
    project_id    = serializer.validated_data.get('project_id')

    # Resolve project
    project = None
    if project_id:
        project = _get_project_or_404(str(project_id), request.user)
        if project is None:
            return Response({"error": "Project not found"}, status=status.HTTP_404_NOT_FOUND)

    # Hash-based deduplication
    file_hash = compute_file_hash(uploaded_file)
    cached    = check_cache(file_hash)
    if cached:
        return Response({
            "document_id": str(cached.document_id),
            "status":      cached.status,
            "cached":      True,
        })

    # Persist document record
    doc = PFDQDocument.objects.create(
        project       = project,
        file_name     = uploaded_file.name,
        file_hash     = file_hash,
        original_file = uploaded_file,
        status        = PFDQDocument.Status.UPLOADED,
        uploaded_by   = request.user,
    )

    # Dispatch Celery task
    from .tasks import process_pfd_document
    process_pfd_document.delay(str(doc.document_id))

    # Soft-hook: keep cross-feature snapshot updated in background.
    try:
        from apps.cross_recommendation.tasks import sync_s3_snapshot
        sync_s3_snapshot.delay()
    except Exception as exc:
        logger.warning("[PFDQUpload] Cross snapshot queue skipped: %s", exc)

    return Response({
        "document_id": str(doc.document_id),
        "status":      doc.status,
        "cached":      False,
    }, status=status.HTTP_202_ACCEPTED)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_status(request, document_id):
    doc = _get_doc_or_404(document_id, request.user)
    if doc is None:
        return Response({"error": "Document not found"}, status=status.HTTP_404_NOT_FOUND)
    return Response({
        "document_id":   str(doc.document_id),
        "status":        doc.status,
        "error_message": doc.error_message,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_results(request, document_id):
    doc = _get_doc_or_404(document_id, request.user)
    if doc is None:
        return Response({"error": "Document not found"}, status=status.HTTP_404_NOT_FOUND)

    if doc.status not in (PFDQDocument.Status.COMPLETED, PFDQDocument.Status.FAILED):
        return Response(
            {"error": "Processing not yet complete", "status": doc.status},
            status=status.HTTP_202_ACCEPTED,
        )

    return Response(PFDQDocumentSerializer(doc).data)


# ===========================================================================
# EXPORTS  (always regenerate in-memory — no S3 redirect to avoid CORS errors)
# ===========================================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def export_excel(request, document_id):
    doc = _get_doc_or_404(document_id, request.user)
    if doc is None:
        return Response({"error": "Document not found"}, status=status.HTTP_404_NOT_FOUND)

    if doc.status != PFDQDocument.Status.COMPLETED:
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
    response["Content-Disposition"] = f'attachment; filename="pfdq_findings_{safe_name}.xlsx"'
    return response


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def export_pdf(request, document_id):
    doc = _get_doc_or_404(document_id, request.user)
    if doc is None:
        return Response({"error": "Document not found"}, status=status.HTTP_404_NOT_FOUND)

    if doc.status != PFDQDocument.Status.COMPLETED:
        return Response({"error": "Document not yet completed"}, status=status.HTTP_400_BAD_REQUEST)

    from .services.export_service import generate_pdf
    data = generate_pdf(doc)
    if not data:
        return Response({"error": "PDF generation failed"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    safe_name = doc.file_name.rsplit(".", 1)[0].replace(" ", "_")
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="pfdq_report_{safe_name}.pdf"'
    return response


# ===========================================================================
# MANAGEMENT
# ===========================================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_documents(request):
    qs = PFDQDocument.objects.filter(uploaded_by=request.user)

    project_id = request.query_params.get("project_id")
    if project_id:
        qs = qs.filter(project__project_id=project_id)

    qs = qs.order_by("-created_at")[:100]
    return Response(PFDQDocumentListSerializer(qs, many=True).data)


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
        logger.warning("[PFDQDelete] Cross snapshot queue skipped: %s", exc)

    return Response({"message": "Document deleted"}, status=status.HTTP_200_OK)


# ===========================================================================
# ENGINEER REVIEW — finding overrides
# ===========================================================================

@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def update_finding(request, finding_id):
    """
    PATCH /api/v1/pfd-quality/findings/<finding_id>/
    Override severity and/or status of a finding.
    Clears cached S3 URLs so next export regenerates fresh.
    """
    try:
        finding = PFDQFinding.objects.select_related('drawing__document').get(pk=finding_id)
    except PFDQFinding.DoesNotExist:
        return Response({"error": "Finding not found"}, status=status.HTTP_404_NOT_FOUND)

    doc = finding.drawing.document
    if doc.uploaded_by != request.user:
        return Response({"error": "Access denied"}, status=status.HTTP_403_FORBIDDEN)

    serializer = PFDQFindingUpdateSerializer(finding, data=request.data, partial=True)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    serializer.save()

    update_fields = ['updated_at']
    if doc.excel_s3_url:
        doc.excel_s3_url = ''
        update_fields.append('excel_s3_url')
    if doc.pdf_s3_url:
        doc.pdf_s3_url = ''
        update_fields.append('pdf_s3_url')
    if update_fields:
        doc.save(update_fields=update_fields)

    return Response(PFDQFindingSerializer(finding).data)


# ===========================================================================
# Helpers
# ===========================================================================

def _get_doc_or_404(document_id: str, user):
    try:
        doc = PFDQDocument.objects.get(document_id=document_id)
        user_obj = getattr(user, "user", user)
        if doc.uploaded_by == user or getattr(user_obj, "is_staff", False):
            return doc
        return None
    except PFDQDocument.DoesNotExist:
        return None


def _get_project_or_404(project_id: str, user):
    try:
        project = PFDQProject.objects.get(project_id=project_id)
        user_obj = getattr(user, "user", user)
        if project.created_by == user or getattr(user_obj, "is_staff", False):
            return project
        return None
    except PFDQProject.DoesNotExist:
        return None
