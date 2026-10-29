"""
Shared P&ID Projects Registry API — kept after the P&ID Verification V1 removal.
================================================================================

The V1 analysis pipeline (views.py, serializers.py, services/*, urls.py) was
removed for a from-scratch redesign (see docs/pidv1-removal-inventory.md).
However, the V1 *projects registry* endpoints were NOT V1-only: the shared
frontend service `pidProjectsService.js` is consumed by P&ID Checker V2
(`PIDCheckerV2.jsx` — listProjects / createProject / updateProject /
deleteProject / getProjectHistory), which has no project registry of its own.

This module therefore keeps ONLY the project-registry endpoints, unchanged in
behaviour, so P&ID Checker V2 keeps working against the same pidv_projects /
pidv_documents tables. All analysis/upload/export endpoints are gone.
"""
from __future__ import annotations

import logging
import uuid

from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import PIDVDocument, PIDVFinding, PIDVProject

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Serializers (copied 1:1 from the removed serializers.py)
# ---------------------------------------------------------------------------

class PIDVProjectSerializer(serializers.ModelSerializer):
    document_count = serializers.SerializerMethodField()
    has_project_legend = serializers.SerializerMethodField()

    class Meta:
        model = PIDVProject
        fields = [
            'id', 'project_id', 'project_name', 'description',
            'document_count', 'has_project_legend',
            'legend_knowledge_data', 'legend_built_at',
            'created_at', 'updated_at',
        ]

    def get_document_count(self, obj):
        return obj.documents.count()

    def get_has_project_legend(self, obj):
        return obj.legend_knowledge_data is not None


class PIDVProjectCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PIDVProject
        fields = ['project_name', 'description']


class PIDVDocumentListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views (no nested findings)."""
    total_drawings = serializers.SerializerMethodField()
    total_issues = serializers.SerializerMethodField()
    project_id = serializers.UUIDField(source='project.project_id', read_only=True, allow_null=True)
    project_name = serializers.CharField(source='project.project_name', read_only=True, allow_null=True)

    class Meta:
        model = PIDVDocument
        fields = [
            'document_id', 'file_name', 'status',
            'project_id', 'project_name',
            'total_drawings', 'total_issues',
            'excel_s3_url', 'pdf_s3_url',
            'created_at', 'updated_at',
        ]

    def get_total_drawings(self, obj):
        return obj.drawings.count()

    def get_total_issues(self, obj):
        return PIDVFinding.objects.filter(drawing__document=obj).count()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_project_or_404(project_id: str, user):
    """
    Soft-coded helper: Retrieves a PIDVProject with permission checking.
    Returns None if project doesn't exist OR user lacks permission.
    """
    try:
        project = PIDVProject.objects.get(project_id=project_id)
        user_obj = getattr(user, "user", user)

        # Permission check: project creator or staff
        if project.created_by == user or getattr(user_obj, "is_staff", False):
            return project

        logger.warning(
            "[PIDVProject] Permission denied - user=%s tried to access project_id=%s (creator=%s)",
            getattr(user, 'username', 'unknown'),
            project_id,
            getattr(project.created_by, 'username', 'unknown'),
        )
        return None

    except PIDVProject.DoesNotExist:
        logger.info(
            "[PIDVProject] Project not found - user=%s requested project_id=%s",
            getattr(user, 'username', 'unknown'),
            project_id,
        )
        return None


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------

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

    serializer = PIDVProjectCreateSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    project = serializer.save(created_by=request.user)
    return Response(PIDVProjectSerializer(project).data, status=status.HTTP_201_CREATED)


@api_view(["GET", "PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def project_detail(request, project_id):
    """
    GET    -> retrieve a single project by ID.
    PUT    -> update project name / description.
    DELETE -> delete project (documents become project-less, not deleted).
    """
    project = _get_project_or_404(project_id, request.user)
    if project is None:
        return Response({"error": "Project not found"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(PIDVProjectSerializer(project).data)

    if request.method == "PUT":
        serializer = PIDVProjectCreateSerializer(project, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(PIDVProjectSerializer(project).data)

    # DELETE
    project.delete()
    return Response({"message": "Project deleted"}, status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_documents(request):
    """List user documents; optionally filter by project_id."""
    qs = PIDVDocument.objects.filter(uploaded_by=request.user)

    project_id = request.query_params.get("project_id")
    if project_id:
        project_id_str = str(project_id).strip()

        if project_id_str:
            try:
                uuid.UUID(project_id_str)
                qs = qs.filter(project__project_id=project_id_str)
            except ValueError:
                logger.warning(
                    "[PIDVList] Invalid UUID format for project_id=%s, ignoring filter",
                    project_id_str,
                )

    qs = qs.order_by("-created_at")[:100]
    return Response(PIDVDocumentListSerializer(qs, many=True).data)
