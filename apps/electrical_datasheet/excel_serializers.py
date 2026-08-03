"""
Serializers for Excel Quality Checker API
"""

from rest_framework import serializers
from .excel_document_models import (
    UploadedExcelDocument,
    ValidationIssue,
    SheetMetadata,
    ParsedItem
)


class ValidationIssueSerializer(serializers.ModelSerializer):
    """Serializer for validation issues"""
    
    severity_display = serializers.CharField(source='get_severity_display', read_only=True)
    
    class Meta:
        model = ValidationIssue
        fields = [
            'id',
            'document',
            'sheet_name',
            'section',
            'item',
            'row_number',
            'column_name',
            'severity',
            'severity_display',
            'code',
            'message',
            'expected_value',
            'actual_value',
            'rule_name',
            'category',
            'created_at',
            'is_acknowledged',
            'acknowledged_by',
            'acknowledged_at',
            'resolution_notes',
        ]
        read_only_fields = ['id', 'created_at', 'severity_display']


class SheetMetadataSerializer(serializers.ModelSerializer):
    """Serializer for sheet metadata"""
    
    sheet_type_display = serializers.CharField(source='get_sheet_type_display', read_only=True)
    
    class Meta:
        model = SheetMetadata
        fields = [
            'id',
            'sheet_name',
            'sheet_index',
            'sheet_type',
            'sheet_type_display',
            'row_count',
            'column_count',
            'has_data',
            'description',
            'key_sections',
        ]
        read_only_fields = ['id']


class ParsedItemSerializer(serializers.ModelSerializer):
    """Serializer for parsed items"""
    
    class Meta:
        model = ParsedItem
        fields = [
            'id',
            'sheet_name',
            'section',
            'sl_no',
            'description',
            'unit',
            'specified_design_data',
            'vendor_data',
            'row_number',
            'is_section_header',
            'is_empty',
        ]
        read_only_fields = ['id']


class UploadedExcelDocumentListSerializer(serializers.ModelSerializer):
    """Serializer for list view of Excel documents"""
    
    equipment_type_display = serializers.CharField(source='get_equipment_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    uploaded_by_name = serializers.SerializerMethodField()
    
    class Meta:
        model = UploadedExcelDocument
        fields = [
            'id',
            'filename',
            'equipment_type',
            'equipment_type_display',
            'company_doc_number',
            'revision',
            'status',
            'status_display',
            'validation_score',
            'error_count',
            'warning_count',
            'info_count',
            'uploaded_at',
            'uploaded_by_name',
        ]
        read_only_fields = fields
    
    def get_uploaded_by_name(self, obj):
        return obj.uploaded_by.get_full_name() if obj.uploaded_by else None


class UploadedExcelDocumentDetailSerializer(serializers.ModelSerializer):
    """Serializer for detailed view of Excel documents"""
    
    equipment_type_display = serializers.CharField(source='get_equipment_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    uploaded_by_name = serializers.SerializerMethodField()
    
    # Nested relationships
    validation_issues = ValidationIssueSerializer(many=True, read_only=True)
    sheet_metadata = SheetMetadataSerializer(many=True, read_only=True)
    
    class Meta:
        model = UploadedExcelDocument
        fields = [
            'id',
            'filename',
            'file_size',
            'equipment_type',
            'equipment_type_display',
            'company_doc_number',
            'contractor_doc_number',
            'rejlers_doc_number',
            'document_title',
            'classification_code',
            'revision',
            'doc_status',
            'doc_purpose',
            'project_name',
            'project_location',
            'agreement_number',
            'parsed_data',
            'sheet_names',
            'status',
            'status_display',
            'validation_score',
            'error_count',
            'warning_count',
            'info_count',
            'processing_started_at',
            'processing_completed_at',
            'processing_error',
            'uploaded_by_name',
            'uploaded_at',
            'validation_issues',
            'sheet_metadata',
        ]
        read_only_fields = fields
    
    def get_uploaded_by_name(self, obj):
        return obj.uploaded_by.get_full_name() if obj.uploaded_by else None


class UploadedExcelDocumentUploadSerializer(serializers.Serializer):
    """Serializer for file upload"""
    
    file = serializers.FileField(
        required=True,
        help_text='Excel file (.xlsx) to upload and validate'
    )
    
    def validate_file(self, value):
        """Validate uploaded file"""
        # Check file extension
        if not value.name.endswith('.xlsx'):
            raise serializers.ValidationError(
                'Only .xlsx files are supported. Please upload an Excel 2007+ file.'
            )
        
        # Check file size (max 50 MB)
        max_size = 50 * 1024 * 1024  # 50 MB
        if value.size > max_size:
            raise serializers.ValidationError(
                f'File size exceeds maximum allowed size of 50 MB. File size: {value.size / (1024*1024):.2f} MB'
            )
        
        return value


class ValidationIssueSummarySerializer(serializers.Serializer):
    """Serializer for validation issue summary by category/severity"""
    
    category = serializers.CharField()
    severity = serializers.CharField()
    count = serializers.IntegerField()
