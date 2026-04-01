"""
PFD Quality Checker — Serializers
"""
from rest_framework import serializers

from .models import PFDQProject, PFDQDocument, PFDQDrawing, PFDQFinding


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------

class PFDQProjectSerializer(serializers.ModelSerializer):
    document_count = serializers.SerializerMethodField()

    class Meta:
        model  = PFDQProject
        fields = [
            'id', 'project_id', 'project_name', 'description',
            'document_count', 'created_at', 'updated_at',
        ]

    def get_document_count(self, obj):
        return obj.documents.count()


class PFDQProjectCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model  = PFDQProject
        fields = ['project_name', 'description']


# ---------------------------------------------------------------------------
# Findings / Drawings / Documents
# ---------------------------------------------------------------------------

class PFDQFindingSerializer(serializers.ModelSerializer):
    class Meta:
        model  = PFDQFinding
        fields = [
            'id', 'sl_no', 'category', 'rule_id',
            'issue_observed', 'action_required', 'evidence',
            'direction', 'severity', 'status', 'created_at',
        ]


class PFDQFindingUpdateSerializer(serializers.ModelSerializer):
    """PATCH /findings/<id>/ — engineer review overrides."""
    class Meta:
        model  = PFDQFinding
        fields = ['severity', 'status']
        extra_kwargs = {
            'severity': {'required': False},
            'status':   {'required': False},
        }


class PFDQDrawingSerializer(serializers.ModelSerializer):
    issues      = PFDQFindingSerializer(source='findings', many=True, read_only=True)
    issue_count = serializers.SerializerMethodField()

    class Meta:
        model  = PFDQDrawing
        fields = ['id', 'drawing_id', 'title', 'page_index', 'metadata', 'issue_count', 'issues', 'created_at']

    def get_issue_count(self, obj):
        return obj.findings.count()


class PFDQDocumentSerializer(serializers.ModelSerializer):
    drawings     = PFDQDrawingSerializer(many=True, read_only=True)
    total_issues = serializers.SerializerMethodField()
    project_id   = serializers.UUIDField(source='project.project_id', read_only=True, allow_null=True)
    project_name = serializers.CharField(source='project.project_name', read_only=True, allow_null=True)

    class Meta:
        model  = PFDQDocument
        fields = [
            'document_id', 'file_name', 's3_path', 'status',
            'error_message', 'excel_s3_url', 'pdf_s3_url',
            'project_id', 'project_name',
            'total_issues', 'drawings', 'created_at', 'updated_at',
        ]

    def get_total_issues(self, obj):
        return PFDQFinding.objects.filter(drawing__document=obj).count()


class PFDQDocumentListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views (no nested findings)."""
    total_drawings = serializers.SerializerMethodField()
    total_issues   = serializers.SerializerMethodField()
    project_id     = serializers.UUIDField(source='project.project_id', read_only=True, allow_null=True)
    project_name   = serializers.CharField(source='project.project_name', read_only=True, allow_null=True)

    class Meta:
        model  = PFDQDocument
        fields = [
            'document_id', 'file_name', 'status', 'error_message',
            'excel_s3_url', 'pdf_s3_url',
            'project_id', 'project_name',
            'total_drawings', 'total_issues', 'created_at', 'updated_at',
        ]

    def get_total_drawings(self, obj):
        return obj.drawings.count()

    def get_total_issues(self, obj):
        return PFDQFinding.objects.filter(drawing__document=obj).count()


class UploadSerializer(serializers.Serializer):
    file       = serializers.FileField()
    project_id = serializers.UUIDField(required=False, allow_null=True)
