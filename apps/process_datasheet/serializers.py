"""
Process Datasheet Serializers
API data serialization for datasheets
"""
from rest_framework import serializers
from .models import (
    EquipmentType,
    ProcessDatasheet,
    DatasheetRevision,
    DatasheetTemplate,
    DatasheetValidationRule,
    DatasheetExtractionJob
)
from django.contrib.auth import get_user_model

User = get_user_model()


class EquipmentTypeSerializer(serializers.ModelSerializer):
    """Serializer for Equipment Type"""
    
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    
    class Meta:
        model = EquipmentType
        fields = [
            'id', 'code', 'name', 'icon', 'description', 'category',
            'configuration', 'template_file', 'calculation_module',
            'status', 'version', 'applicable_standards',
            'created_by', 'created_by_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class EquipmentTypeListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing equipment types"""
    
    class Meta:
        model = EquipmentType
        fields = [
            'id', 'code', 'name', 'icon', 'category',
            'version', 'status'
        ]


class DatasheetRevisionSerializer(serializers.ModelSerializer):
    """Serializer for Datasheet Revisions"""
    
    revised_by_name = serializers.CharField(source='revised_by.get_full_name', read_only=True)
    
    class Meta:
        model = DatasheetRevision
        fields = [
            'id', 'revision_number', 'description',
            'data_snapshot', 'changes', 'pages_affected',
            'revised_by', 'revised_by_name', 'revision_date'
        ]
        read_only_fields = ['id', 'revision_date']


class ProcessDatasheetSerializer(serializers.ModelSerializer):
    """Complete serializer for Process Datasheet"""
    
    equipment_type_detail = EquipmentTypeSerializer(source='equipment_type', read_only=True)
    prepared_by_name = serializers.CharField(source='prepared_by.get_full_name', read_only=True)
    checked_by_name = serializers.CharField(source='checked_by.get_full_name', read_only=True)
    approved_by_name = serializers.CharField(source='approved_by.get_full_name', read_only=True)
    revisions = DatasheetRevisionSerializer(many=True, read_only=True)
    
    class Meta:
        model = ProcessDatasheet
        fields = [
            'id', 'document_number', 'contractor_document_number', 'title',
            'equipment_type', 'equipment_type_detail',
            'tag_number', 'service_description', 'location',
            'project_name', 'project_number', 'unit_number', 'area',
            'data', 'calculated_values',
            'validation_status', 'validation_results', 'validation_score',
            'extraction_metadata',
            'status', 'document_class', 'revision',
            'pid_drawing_number', 'line_number', 'material_spec', 'related_documents',
            'source_files', 'generated_pdf',
            'holds', 'comments',
            'prepared_by', 'prepared_by_name', 'date_prepared',
            'checked_by', 'checked_by_name', 'date_checked',
            'approved_by', 'approved_by_name', 'date_approved',
            'revisions',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'calculated_values', 'validation_status', 
            'validation_results', 'validation_score',
            'created_at', 'updated_at'
        ]


class ProcessDatasheetListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing datasheets"""
    
    equipment_type_name = serializers.CharField(source='equipment_type.name', read_only=True)
    equipment_type_icon = serializers.CharField(source='equipment_type.icon', read_only=True)
    prepared_by_name = serializers.CharField(source='prepared_by.get_full_name', read_only=True)
    
    class Meta:
        model = ProcessDatasheet
        fields = [
            'id', 'document_number', 'tag_number', 'title',
            'equipment_type', 'equipment_type_name', 'equipment_type_icon',
            'status', 'revision', 'validation_score',
            'prepared_by_name', 'updated_at'
        ]


class ProcessDatasheetCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating new datasheets"""
    
    class Meta:
        model = ProcessDatasheet
        fields = [
            'document_number', 'contractor_document_number', 'title',
            'equipment_type', 'tag_number', 'service_description', 'location',
            'project_name', 'project_number', 'unit_number', 'area',
            'data',
            'pid_drawing_number', 'line_number', 'material_spec'
        ]
    
    def create(self, validated_data):
        # Set prepared_by from request user
        validated_data['prepared_by'] = self.context['request'].user
        return super().create(validated_data)


class DatasheetTemplateSerializer(serializers.ModelSerializer):
    """Serializer for Datasheet Templates"""
    
    equipment_type_name = serializers.CharField(source='equipment_type.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    
    class Meta:
        model = DatasheetTemplate
        fields = [
            'id', 'name', 'description',
            'equipment_type', 'equipment_type_name',
            'template_data', 'usage_count',
            'is_global', 'created_by', 'created_by_name',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'usage_count', 'created_at', 'updated_at']


class DatasheetValidationRuleSerializer(serializers.ModelSerializer):
    """Serializer for Validation Rules"""
    
    equipment_type_name = serializers.CharField(source='equipment_type.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    
    class Meta:
        model = DatasheetValidationRule
        fields = [
            'id', 'rule_id', 'name', 'description',
            'equipment_type', 'equipment_type_name',
            'condition', 'severity', 'message',
            'is_active', 'applies_to_projects',
            'created_by', 'created_by_name', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']


class DatasheetExtractionJobSerializer(serializers.ModelSerializer):
    """Serializer for Extraction Jobs"""
    
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    equipment_type_name = serializers.CharField(source='equipment_type.name', read_only=True)
    pdf_file = serializers.FileField(write_only=False, required=False)
    
    class Meta:
        model = DatasheetExtractionJob
        fields = [
            'id', 'equipment_type', 'equipment_type_name', 'pdf_file',
            'datasheet', 'job_type', 'extraction_mode', 'source_files',
            'status', 'progress',
            'extracted_data', 'confidence_scores',
            'error_message', 'retry_count',
            'started_at', 'completed_at',
            'created_by', 'created_by_name', 'created_at'
        ]
        read_only_fields = [
            'id', 'status', 'progress', 'extracted_data',
            'confidence_scores', 'error_message',
            'started_at', 'completed_at', 'created_at',
            'equipment_type_name', 'created_by_name'
        ]
