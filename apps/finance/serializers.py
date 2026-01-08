"""
Finance API Serializers
"""
from rest_framework import serializers
from .models import Invoice, Approval, AuditLog, ApprovalRoute


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = ['id', 'action', 'description', 'metadata', 'timestamp']


class ApprovalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Approval
        fields = [
            'id', 'approver_name', 'approver_email', 'approval_level',
            'level_name', 'status', 'decision', 'comments',
            'decision_date', 'created_at'
        ]
        read_only_fields = ['approval_token']


class InvoiceListSerializer(serializers.ModelSerializer):
    invoice_type_display = serializers.CharField(source='get_invoice_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    
    class Meta:
        model = Invoice
        fields = [
            'id', 'tracking_id', 'invoice_number', 'vendor_name', 'invoice_date',
            'total_amount', 'currency', 'invoice_type', 'invoice_type_display',
            'status', 'status_display', 'created_at', 'updated_at'
        ]


class InvoiceDetailSerializer(serializers.ModelSerializer):
    approvals = ApprovalSerializer(many=True, read_only=True)
    audit_logs = AuditLogSerializer(many=True, read_only=True)
    invoice_type_display = serializers.CharField(source='get_invoice_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    
    class Meta:
        model = Invoice
        fields = [
            'id', 'tracking_id', 'invoice_number', 'vendor_name', 'invoice_date',
            'amount', 'tax_amount', 'total_amount', 'currency',
            'invoice_type', 'invoice_type_display',
            'classification_confidence', 'classification_reasoning',
            'extracted_text', 'line_items',
            'original_filename', 'file_path',
            'status', 'status_display',
            'submitted_by', 'created_at', 'updated_at', 'processed_at',
            'approvals', 'audit_logs'
        ]
        read_only_fields = [
            'extracted_text', 'classification_confidence',
            'classification_reasoning'
        ]


class InvoiceUploadSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True)
    
    class Meta:
        model = Invoice
        fields = [
            'file', 'invoice_number', 'vendor_name', 'invoice_date',
            'total_amount', 'currency'
        ]
        extra_kwargs = {
            'invoice_number': {'required': False},
            'vendor_name': {'required': False},
            'invoice_date': {'required': False},
            'total_amount': {'required': False},
        }


class ApprovalRouteSerializer(serializers.ModelSerializer):
    invoice_type_display = serializers.CharField(source='get_invoice_type_display', read_only=True)
    
    class Meta:
        model = ApprovalRoute
        fields = [
            'id', 'invoice_type', 'invoice_type_display',
            'min_amount', 'max_amount', 'approval_chain',
            'is_active', 'priority', 'created_at', 'updated_at'
        ]


class ApprovalDecisionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=['approve', 'reject'])
    comments = serializers.CharField(required=False, allow_blank=True)


class InvoiceExportFilterSerializer(serializers.Serializer):
    """Serializer for invoice export filters"""
    
    # Status filter (can be single or multiple)
    status = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text="Filter by invoice status (can be multiple)"
    )
    
    # Invoice type filter (can be single or multiple)
    invoice_type = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text="Filter by invoice type (can be multiple)"
    )
    
    # Date range filters
    date_from = serializers.DateField(
        required=False,
        help_text="Filter invoices from this date (YYYY-MM-DD)"
    )
    date_to = serializers.DateField(
        required=False,
        help_text="Filter invoices up to this date (YYYY-MM-DD)"
    )
    
    # Amount range filters
    min_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        help_text="Minimum total amount"
    )
    max_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        help_text="Maximum total amount"
    )
    
    # Search filter
    search = serializers.CharField(
        required=False,
        max_length=255,
        help_text="Search in invoice number, vendor name, or email"
    )
    
    # Export format
    format = serializers.ChoiceField(
        choices=['excel', 'pdf'],
        default='excel',
        help_text="Export format: excel or pdf"
    )
    
    def validate_status(self, value):
        """Handle empty strings and convert to None"""
        if value == '' or value == []:
            return None
        return value
    
    def validate_invoice_type(self, value):
        """Handle empty strings and convert to None"""
        if value == '' or value == []:
            return None
        return value
    
    def validate_search(self, value):
        """Handle empty strings and convert to None"""
        if value == '':
            return None
        return value
