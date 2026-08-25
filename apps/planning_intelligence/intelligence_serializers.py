from rest_framework import serializers

from .models import DocumentIntelligenceRun, DocumentProfile, IntelligenceConflict, IntelligenceFact
from .services.document_intelligence import compile_run_intelligence


class DocumentProfileSerializer(serializers.ModelSerializer):
    filename = serializers.CharField(source='file.original_filename', read_only=True)

    class Meta:
        model = DocumentProfile
        fields = [
            'id', 'file', 'filename', 'declared_category', 'detected_category',
            'classification_confidence', 'extension', 'mime_type', 'language',
            'page_count', 'word_count', 'checksum_sha256', 'extraction_method',
            'quality_flags', 'classified_at', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class IntelligenceFactSerializer(serializers.ModelSerializer):
    source_filename = serializers.CharField(source='source_file.original_filename', read_only=True)

    class Meta:
        model = IntelligenceFact
        fields = [
            'id', 'run', 'source_file', 'source_filename', 'fact_type', 'key', 'value',
            'normalized_value', 'confidence', 'extraction_method', 'source_excerpt',
            'source_locator', 'status', 'reviewed_by', 'reviewed_at', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class IntelligenceConflictSerializer(serializers.ModelSerializer):
    facts = serializers.SerializerMethodField()

    class Meta:
        model = IntelligenceConflict
        fields = [
            'id', 'run', 'key', 'conflict_type', 'fact_ids', 'facts', 'description',
            'status', 'resolution', 'resolved_by', 'resolved_at', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_facts(self, obj):
        facts = IntelligenceFact.objects.filter(id__in=obj.fact_ids, is_deleted=False).select_related('source_file')
        return IntelligenceFactSerializer(facts, many=True).data


class DocumentIntelligenceRunSerializer(serializers.ModelSerializer):
    intelligence = serializers.SerializerMethodField()

    class Meta:
        model = DocumentIntelligenceRun
        fields = [
            'id', 'project', 'status', 'engine_version', 'source_file_ids', 'fact_count',
            'conflict_count', 'intelligence', 'started_at', 'finished_at', 'error_message',
            'requested_by', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_intelligence(self, obj):
        return compile_run_intelligence(obj) if obj.status == 'succeeded' else None


class ManualIntelligenceFactSerializer(serializers.Serializer):
    fact_type = serializers.ChoiceField(choices=IntelligenceFact.TYPE_CHOICES)
    key = serializers.CharField(max_length=160)
    value = serializers.JSONField()
    source_excerpt = serializers.CharField(max_length=1000, required=False, allow_blank=True)


class FactReviewSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=['confirmed', 'rejected'])


class ConflictResolutionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=['select_fact', 'ignore'])
    selected_fact_id = serializers.IntegerField(required=False)

    def validate(self, attrs):
        if attrs['action'] == 'select_fact' and not attrs.get('selected_fact_id'):
            raise serializers.ValidationError({'selected_fact_id': 'Select a fact to resolve this conflict.'})
        return attrs
