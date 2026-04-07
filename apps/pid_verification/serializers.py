"""
P&ID Verification Serializers
"""
from rest_framework import serializers
from .models import PIDVProject, PIDVDocument, PIDVDrawing, PIDVFinding


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------

class PIDVProjectSerializer(serializers.ModelSerializer):
    document_count      = serializers.SerializerMethodField()
    has_project_legend  = serializers.SerializerMethodField()

    class Meta:
        model  = PIDVProject
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
        model  = PIDVProject
        fields = ['project_name', 'description']


# ---------------------------------------------------------------------------
# Findings / Drawings / Documents
# ---------------------------------------------------------------------------

class PIDVFindingSerializer(serializers.ModelSerializer):
    class Meta:
        model  = PIDVFinding
        fields = [
            'id', 'sl_no', 'category', 'issue_observed',
            'action_required', 'evidence', 'direction',
            'severity', 'status', 'rule_id', 'created_at',
        ]


class PIDVFindingUpdateSerializer(serializers.ModelSerializer):
    """Used by the PATCH /findings/<id>/ endpoint for engineer review overrides."""
    class Meta:
        model  = PIDVFinding
        fields = ['severity', 'status']
        extra_kwargs = {
            'severity': {'required': False},
            'status':   {'required': False},
        }


class PIDVDrawingSerializer(serializers.ModelSerializer):
    issues = PIDVFindingSerializer(source='findings', many=True, read_only=True)
    issue_count = serializers.SerializerMethodField()

    class Meta:
        model  = PIDVDrawing
        fields = ['id', 'drawing_id', 'title', 'page_index', 'metadata', 'issue_count', 'issues', 'created_at']

    def get_issue_count(self, obj):
        return obj.findings.count()


class PIDVDocumentSerializer(serializers.ModelSerializer):
    drawings = PIDVDrawingSerializer(many=True, read_only=True)
    total_issues = serializers.SerializerMethodField()
    project_id   = serializers.UUIDField(source='project.project_id', read_only=True, allow_null=True)
    project_name = serializers.CharField(source='project.project_name', read_only=True, allow_null=True)

    class Meta:
        model  = PIDVDocument
        fields = [
            'document_id', 'file_name', 's3_path', 'status',
            'error_message', 'excel_s3_url', 'pdf_s3_url',
            'project_id', 'project_name',
            'total_issues', 'drawings', 'created_at', 'updated_at',
        ]

    def get_total_issues(self, obj):
        return PIDVFinding.objects.filter(drawing__document=obj).count()


class PIDVDocumentListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views (no nested findings)."""
    total_drawings = serializers.SerializerMethodField()
    total_issues   = serializers.SerializerMethodField()
    project_id     = serializers.UUIDField(source='project.project_id', read_only=True, allow_null=True)
    project_name   = serializers.CharField(source='project.project_name', read_only=True, allow_null=True)

    class Meta:
        model  = PIDVDocument
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


class UploadSerializer(serializers.Serializer):
    """Validates the file upload request."""
    file       = serializers.FileField()
    project_id = serializers.UUIDField(required=False, allow_null=True)
