from rest_framework import serializers

from .models import (
    BasisDeliverable, DocumentAuthorityRule, DocumentIntelligenceRun, DocumentProfile,
    GenerationDecisionGate, GenerationDependency, GenerationPhase, GenerationPlan,
    IntelligenceConflict, IntelligenceFact, PlanDeliverable, ScheduleBasis,
)
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


class DocumentAuthorityRuleSerializer(serializers.ModelSerializer):
    information_type_label = serializers.CharField(source='get_information_type_display', read_only=True)

    class Meta:
        model = DocumentAuthorityRule
        fields = [
            'id', 'information_type', 'information_type_label', 'document_category',
            'priority', 'rationale', 'is_system',
        ]
        read_only_fields = fields


class BasisDeliverableSerializer(serializers.ModelSerializer):
    class Meta:
        model = BasisDeliverable
        fields = [
            'id', 'basis', 'discipline', 'canonical_key', 'canonical_name', 'original_title',
            'document_number', 'document_revision', 'status', 'confidence', 'source_fact_ids',
            'source_references', 'aliases', 'reviewed_by', 'reviewed_at', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'basis', 'canonical_key', 'source_fact_ids', 'source_references', 'aliases',
            'reviewed_by', 'reviewed_at', 'created_at', 'updated_at',
        ]


class BasisDeliverableReviewSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=['confirmed', 'excluded'])


class BulkBasisDeliverableReviewSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=['confirmed', 'excluded'])
    deliverable_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, allow_empty=False,
    )


class ScheduleBasisSerializer(serializers.ModelSerializer):
    deliverables = BasisDeliverableSerializer(many=True, read_only=True)
    source_run_id = serializers.IntegerField(source='source_run.id', read_only=True)
    approved_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ScheduleBasis
        fields = [
            'id', 'project', 'source_run', 'source_run_id', 'version', 'status',
            'project_name', 'client', 'location', 'effective_date', 'contractual_finish',
            'duration_months', 'calendar', 'authority_snapshot', 'readiness', 'deliverables',
            'approved_by', 'approved_by_name', 'approved_at', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'project', 'source_run', 'version', 'status', 'authority_snapshot', 'readiness',
            'approved_by', 'approved_at', 'created_at', 'updated_at',
        ]

    def get_approved_by_name(self, obj):
        user = obj.approved_by
        return user.get_full_name() or user.username if user else ''


class PlanDeliverableSerializer(serializers.ModelSerializer):
    canonical_name = serializers.CharField(source='basis_deliverable.canonical_name', read_only=True)
    discipline = serializers.CharField(source='basis_deliverable.discipline', read_only=True)
    document_number = serializers.CharField(source='basis_deliverable.document_number', read_only=True)
    source_references = serializers.JSONField(source='basis_deliverable.source_references', read_only=True)

    class Meta:
        model = PlanDeliverable
        fields = [
            'id', 'plan', 'basis_deliverable', 'canonical_name', 'discipline', 'document_number',
            'workflow_family', 'recurrence', 'recurrence_count', 'scenario_code',
            'technical_sequence', 'classification_reason', 'source_references',
        ]
        read_only_fields = ['plan', 'basis_deliverable']


class GenerationDependencySerializer(serializers.ModelSerializer):
    predecessor_name = serializers.CharField(source='predecessor.basis_deliverable.canonical_name', read_only=True)
    successor_name = serializers.CharField(source='successor.basis_deliverable.canonical_name', read_only=True)

    class Meta:
        model = GenerationDependency
        fields = [
            'id', 'plan', 'predecessor', 'predecessor_name', 'successor', 'successor_name',
            'relationship_type', 'lag_days', 'rationale', 'source_type', 'source_references',
            'status', 'reviewed_by', 'reviewed_at',
        ]
        read_only_fields = ['plan', 'reviewed_by', 'reviewed_at']


class GenerationPhaseSerializer(serializers.ModelSerializer):
    class Meta:
        model = GenerationPhase
        fields = ['id', 'code', 'name', 'sequence', 'duration_months', 'source_references']
        read_only_fields = fields


class GenerationDecisionGateSerializer(serializers.ModelSerializer):
    class Meta:
        model = GenerationDecisionGate
        fields = ['id', 'code', 'name', 'sequence', 'scenarios', 'source_references']
        read_only_fields = fields


class GenerationPlanSerializer(serializers.ModelSerializer):
    deliverables = PlanDeliverableSerializer(many=True, read_only=True)
    dependencies = GenerationDependencySerializer(many=True, read_only=True)
    phases = GenerationPhaseSerializer(many=True, read_only=True)
    decision_gates = GenerationDecisionGateSerializer(many=True, read_only=True)

    class Meta:
        model = GenerationPlan
        fields = [
            'id', 'project', 'basis', 'version', 'status', 'readiness', 'selected_scenario',
            'deliverables', 'dependencies', 'phases', 'decision_gates',
            'approved_by', 'approved_at', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'project', 'basis', 'version', 'status', 'readiness', 'approved_by',
            'approved_at', 'created_at', 'updated_at',
        ]


class GenerationDependencyReviewSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=['confirmed', 'rejected'])
    dependency_ids = serializers.ListField(child=serializers.IntegerField(), required=False, allow_empty=False)


class AddGenerationDependencySerializer(serializers.Serializer):
    predecessor = serializers.IntegerField()
    successor = serializers.IntegerField()
    relationship_type = serializers.ChoiceField(choices=['FS', 'SS', 'FF'], default='FS')
    lag_days = serializers.DecimalField(max_digits=8, decimal_places=2, default=0)
    rationale = serializers.CharField(max_length=500)
