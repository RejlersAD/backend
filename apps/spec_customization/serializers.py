"""Spec Customization — DRF Serializers."""
from rest_framework import serializers

from .models import (
    PaperSpecDocument,
    PaperSpecExtractionJob,
    PipingClass,
    PipingClassComponent,
)


class PaperSpecDocumentSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = PaperSpecDocument
        fields = [
            'id', 'original_filename', 'file_url', 'file_size_bytes', 'total_pages',
            'sha256_hash', 'project_id', 'title', 'document_number',
            'uploaded_by', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_file_url(self, obj):
        try:
            return obj.file.url if obj.file else None
        except Exception:
            return None


class PaperSpecExtractionJobSerializer(serializers.ModelSerializer):
    document_filename = serializers.CharField(source='document.original_filename', read_only=True)
    document_pages = serializers.IntegerField(source='document.total_pages', read_only=True)

    class Meta:
        model = PaperSpecExtractionJob
        fields = [
            'id', 'document', 'document_filename', 'document_pages',
            'status', 'progress_percent', 'current_phase',
            'pages_processed', 'chunks_total', 'chunks_done',
            'celery_task_id', 'error_message',
            'created_at', 'started_at', 'completed_at',
        ]
        read_only_fields = fields


class PipingClassComponentSerializer(serializers.ModelSerializer):
    class Meta:
        model = PipingClassComponent
        fields = [
            'id', 'component_type', 'sub_type', 'size_from', 'size_to',
            'description', 'schedule_or_rating', 'material_standard',
            'end_connection', 'notes', 'display_order',
        ]


class PipingClassSerializer(serializers.ModelSerializer):
    components = PipingClassComponentSerializer(many=True, read_only=True)
    components_count = serializers.SerializerMethodField()

    class Meta:
        model = PipingClass
        fields = [
            'id', 'job', 'class_code', 'class_full_code',
            'material_grade', 'pressure_rating', 'flange_facing',
            'corrosion_allowance', 'service_list', 'pt_rating_table',
            'source_pages', 'confidence_score', 'raw_notes',
            'extraction_engine', 'components_count', 'components',
            'created_at',
        ]
        read_only_fields = fields

    def get_components_count(self, obj):
        return obj.components.count()


class PipingClassListSerializer(serializers.ModelSerializer):
    """Lightweight list serializer (no nested components)."""
    components_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = PipingClass
        fields = [
            'id', 'job', 'class_code', 'class_full_code',
            'material_grade', 'pressure_rating', 'flange_facing',
            'service_list', 'source_pages', 'confidence_score',
            'extraction_engine', 'components_count', 'created_at',
        ]



class PaperSpecExtractionJobBriefSerializer(serializers.ModelSerializer):
    """
    Brief serializer for job history list view.
    Mirrors electrical_checklist.ChecklistExtractionJobBriefSerializer pattern.
    
    BACKWARD COMPATIBILITY: Uses SerializerMethodField for cost tracking fields
    to gracefully handle cases where migrations 0005/0006 haven't been applied yet.
    This prevents 500 errors in production during deployment transitions.
    """
    user_name = serializers.SerializerMethodField()
    document_name = serializers.SerializerMethodField()
    components_count = serializers.SerializerMethodField()
    classes_count = serializers.SerializerMethodField()
    
    # Cost tracking fields - use SerializerMethodField for backward compatibility
    gemini_prompt_tokens = serializers.SerializerMethodField()
    gemini_completion_tokens = serializers.SerializerMethodField()
    openai_prompt_tokens = serializers.SerializerMethodField()
    openai_completion_tokens = serializers.SerializerMethodField()
    cost_usd = serializers.SerializerMethodField()

    class Meta:
        model = PaperSpecExtractionJob
        fields = [
            'id', 'status', 'progress_percent', 'current_phase',
            'user_name', 'document_name', 'created_at', 'completed_at',
            'components_count', 'classes_count',
            'gemini_prompt_tokens', 'gemini_completion_tokens',
            'openai_prompt_tokens', 'openai_completion_tokens', 'cost_usd',
        ]
        read_only_fields = fields

    def get_user_name(self, obj):
        if not obj.created_by:
            return 'Unknown'
        return obj.created_by.get_full_name() or obj.created_by.username

    def get_document_name(self, obj):
        if not obj.document:
            return f'Job #{str(obj.id)[:8]}'
        return obj.document.original_filename or obj.document.title or f'Document #{str(obj.document.id)[:8]}'

    def get_components_count(self, obj):
        # Prefetch via annotation in the view (components_count)
        return getattr(obj, 'components_count', 0)

    def get_classes_count(self, obj):
        # Prefetch via annotation in the view (classes_count)
        return getattr(obj, 'classes_count', 0)
    
    # Backward-compatible getters for cost tracking fields (migration 0005)
    def get_gemini_prompt_tokens(self, obj):
        """Return 0 if migration 0005 not applied yet."""
        return getattr(obj, 'gemini_prompt_tokens', 0)
    
    def get_gemini_completion_tokens(self, obj):
        """Return 0 if migration 0005 not applied yet."""
        return getattr(obj, 'gemini_completion_tokens', 0)
    
    def get_openai_prompt_tokens(self, obj):
        """Return 0 if migration 0005 not applied yet."""
        return getattr(obj, 'openai_prompt_tokens', 0)
    
    def get_openai_completion_tokens(self, obj):
        """Return 0 if migration 0005 not applied yet."""
        return getattr(obj, 'openai_completion_tokens', 0)
    
    def get_cost_usd(self, obj):
        """Return 0.00 if migration 0005 not applied yet."""
        from decimal import Decimal
        return getattr(obj, 'cost_usd', Decimal('0.000000'))
