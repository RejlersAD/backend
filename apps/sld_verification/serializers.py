"""
SLD Verification Serializers
"""
from rest_framework import serializers
from .models import SLDProject, SLDDocument, SLDDrawing, SLDFinding


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------

class SLDProjectSerializer(serializers.ModelSerializer):
    document_count = serializers.SerializerMethodField()

    class Meta:
        model  = SLDProject
        fields = [
            'id', 'project_id', 'project_name', 'description',
            'document_count', 'created_at', 'updated_at',
        ]

    def get_document_count(self, obj):
        return obj.documents.count()


class SLDProjectCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model  = SLDProject
        fields = ['project_name', 'description']


# ---------------------------------------------------------------------------
# Findings / Drawings / Documents
# ---------------------------------------------------------------------------

class SLDFindingSerializer(serializers.ModelSerializer):
    class Meta:
        model  = SLDFinding
        fields = [
            'id', 'sl_no', 'category', 'issue_observed',
            'action_required', 'evidence', 'direction',
            'severity', 'status', 'rule_id', 'created_at',
        ]


class SLDFindingUpdateSerializer(serializers.ModelSerializer):
    """Used by the PATCH /findings/<id>/ endpoint for engineer review overrides."""
    class Meta:
        model  = SLDFinding
        fields = ['severity', 'status']
        extra_kwargs = {
            'severity': {'required': False},
            'status':   {'required': False},
        }


class SLDDrawingSerializer(serializers.ModelSerializer):
    issues = SLDFindingSerializer(source='findings', many=True, read_only=True)
    issue_count = serializers.SerializerMethodField()

    class Meta:
        model  = SLDDrawing
        fields = ['id', 'drawing_id', 'title', 'page_index', 'metadata', 'issue_count', 'issues', 'created_at']

    def get_issue_count(self, obj):
        return obj.findings.count()


class SLDDocumentSerializer(serializers.ModelSerializer):
    drawings = SLDDrawingSerializer(many=True, read_only=True)
    total_issues = serializers.SerializerMethodField()
    project_id   = serializers.UUIDField(source='project.project_id', read_only=True, allow_null=True)
    project_name = serializers.CharField(source='project.project_name', read_only=True, allow_null=True)

    class Meta:
        model  = SLDDocument
        fields = [
            'document_id', 'file_name', 's3_path', 'status',
            'error_message', 'excel_s3_url', 'pdf_s3_url',
            'project_id', 'project_name',
            'uploaded_at', 'processed_at',
            'drawings', 'total_issues',
        ]

    def get_total_issues(self, obj):
        count = 0
        for d in obj.drawings.all():
            count += d.findings.count()
        return count


class SLDDocumentListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for document history lists."""
    total_issues = serializers.SerializerMethodField()
    project_id   = serializers.UUIDField(source='project.project_id', read_only=True, allow_null=True)
    project_name = serializers.CharField(source='project.project_name', read_only=True, allow_null=True)

    class Meta:
        model  = SLDDocument
        fields = [
            'document_id', 'file_name', 'status',
            'project_id', 'project_name',
            'uploaded_at', 'processed_at',
            'total_issues',
        ]

    def get_total_issues(self, obj):
        count = 0
        for d in obj.drawings.all():
            count += d.findings.count()
        return count


class UploadSerializer(serializers.Serializer):
    """Validates the upload-sld request."""
    file       = serializers.FileField()
    project_id = serializers.UUIDField(required=False, allow_null=True)
