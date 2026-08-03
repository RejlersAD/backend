"""RADAI Project Planning Application — DRF serializers."""
from rest_framework import serializers

from .models import PlanningFile, PlanningGeneration, PlanningProject


class PlanningFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanningFile
        fields = [
            'id', 'project', 'category', 'file', 'original_filename', 'content_type',
            'size_bytes', 'parse_status', 'extracted_text', 'confidence_score',
            'parse_error', 'uploaded_by', 'created_at',
        ]
        read_only_fields = [
            'id', 'original_filename', 'content_type', 'size_bytes', 'parse_status',
            'extracted_text', 'confidence_score', 'parse_error', 'uploaded_by', 'created_at',
        ]


class PlanningFileListSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanningFile
        fields = [
            'id', 'project', 'category', 'file', 'original_filename', 'size_bytes',
            'parse_status', 'confidence_score', 'created_at',
        ]


class PlanningProjectSerializer(serializers.ModelSerializer):
    file_count = serializers.IntegerField(source='files.count', read_only=True)
    latest_generation_version = serializers.SerializerMethodField()
    ai_enabled = serializers.SerializerMethodField()
    ai_provider = serializers.SerializerMethodField()
    ai_model = serializers.SerializerMethodField()
    ai_key_configured = serializers.SerializerMethodField()

    class Meta:
        model = PlanningProject
        fields = [
            'id', 'name', 'client', 'location', 'phase', 'effective_date',
            'duration_months', 'calendar_overrides', 'review_cycle_overrides',
            'created_by', 'file_count', 'latest_generation_version',
            'ai_enabled', 'ai_provider', 'ai_model', 'ai_key_configured',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_by', 'created_at', 'updated_at']
        # NOTE: `ai_settings` (which holds the encrypted BYOK key) is
        # deliberately NOT included in `fields` above — it must never be
        # serialized to the API. Use the dedicated ai-settings action
        # (views.PlanningProjectViewSet.ai_settings) to read/write it.

    def get_latest_generation_version(self, obj):
        latest = obj.generations.first()
        return latest.version if latest else None

    def get_ai_enabled(self, obj):
        return bool((obj.ai_settings or {}).get('enabled'))

    def get_ai_provider(self, obj):
        return (obj.ai_settings or {}).get('provider') or None

    def get_ai_model(self, obj):
        return (obj.ai_settings or {}).get('model') or None

    def get_ai_key_configured(self, obj):
        return bool((obj.ai_settings or {}).get('api_key_encrypted'))


class PlanningGenerationListSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanningGeneration
        fields = ['id', 'project', 'version', 'created_at']


class PlanningGenerationSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanningGeneration
        fields = [
            'id', 'project', 'version', 'intelligence', 'wbs', 'activities',
            'logic_matrix', 'eddr', 'milestones', 'manhours', 'validation',
            'narrative', 'generated_by', 'created_at',
        ]
        read_only_fields = fields
