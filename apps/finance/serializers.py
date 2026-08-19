"""
Finance API Serializers
"""
from rest_framework import serializers
from .models import (
    Invoice,
    InvoiceLineItem,
    InvoicePurchaseOrderAllocation,
    InvoiceOCRJob,
    PayablePayment,
    Approval,
    AuditLog,
    ApprovalRoute,
)


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


class InvoiceLineItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceLineItem
        fields = [
            'id', 'line_number', 'description', 'quantity', 'unit_price',
            'net_amount', 'tax_rate', 'tax_amount', 'total_amount', 'currency',
            'po_item_reference', 'source_data', 'ocr_confidence',
            'manually_verified', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class InvoicePurchaseOrderAllocationSerializer(serializers.ModelSerializer):
    purchase_order_number = serializers.CharField(source='purchase_order.po_number', read_only=True)
    receipt_numbers = serializers.SlugRelatedField(
        source='receipts',
        many=True,
        read_only=True,
        slug_field='receipt_number',
    )

    class Meta:
        model = InvoicePurchaseOrderAllocation
        fields = [
            'id', 'purchase_order', 'purchase_order_number', 'receipt_numbers',
            'allocated_amount', 'currency', 'match_method', 'match_status',
            'match_confidence', 'po_amount_at_match', 'invoice_amount_at_match',
            'amount_variance', 'tolerance_percentage', 'amount_within_tolerance',
            'vendor_matched', 'currency_matched', 'receipt_required',
            'exception_codes', 'match_evidence', 'line_items_matched',
            'receipt_quantities_matched', 'review_notes', 'matched_by', 'matched_at',
            'verified_by', 'verified_at', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class PayablePaymentSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = PayablePayment
        fields = [
            'id', 'operation', 'amount', 'currency', 'effective_date',
            'reference', 'notes', 'metadata', 'created_by',
            'created_by_name', 'created_at',
        ]
        read_only_fields = ['id', 'created_by', 'created_by_name', 'created_at']

    def get_created_by_name(self, obj):
        if not obj.created_by:
            return None
        return obj.created_by.get_full_name() or obj.created_by.email


class InvoiceOCRJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceOCRJob
        fields = [
            'id', 'status', 'original_filename', 'source_file_sha256',
            'result', 'error_message', 'created_at', 'started_at', 'completed_at',
        ]
        read_only_fields = fields


class InvoiceListSerializer(serializers.ModelSerializer):
    invoice_type_display = serializers.CharField(source='get_invoice_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    vendor_master_name = serializers.CharField(source='vendor.name', read_only=True, allow_null=True)
    
    class Meta:
        model = Invoice
        fields = [
            'id', 'tracking_id', 'invoice_number', 'vendor', 'vendor_master_name',
            'vendor_name', 'invoice_date', 'received_date', 'due_date',
            'total_amount', 'currency', 'invoice_type', 'invoice_type_display',
            'status', 'status_display', 'procurement_status', 'match_status',
            'payment_status', 'manual_review_required', 'po_reference_text',
            'created_at', 'updated_at'
        ]


class InvoiceDetailSerializer(serializers.ModelSerializer):
    approvals = ApprovalSerializer(many=True, read_only=True)
    audit_logs = AuditLogSerializer(many=True, read_only=True)
    invoice_type_display = serializers.CharField(source='get_invoice_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    vendor_master_name = serializers.CharField(source='vendor.name', read_only=True, allow_null=True)
    structured_line_items = InvoiceLineItemSerializer(many=True, read_only=True)
    po_allocations = InvoicePurchaseOrderAllocationSerializer(many=True, read_only=True)
    payment_operations = PayablePaymentSerializer(many=True, read_only=True)
    
    class Meta:
        model = Invoice
        fields = [
            'id', 'tracking_id', 'invoice_number', 'vendor', 'vendor_master_name',
            'vendor_name', 'invoice_date', 'received_date', 'due_date', 'payment_terms',
            'amount', 'tax_amount', 'total_amount', 'currency', 'vat_percentage',
            'vat_registration_number', 'po_reference_text',
            'invoice_type', 'invoice_type_display',
            'classification_confidence', 'classification_reasoning',
            'extracted_text', 'line_items', 'structured_line_items',
            'ocr_metadata', 'ocr_confidence', 'manual_review_required',
            'source_file_sha256', 'po_allocations',
            'original_filename', 'file_path',
            'status', 'status_display', 'procurement_status', 'match_status',
            'payment_status', 'procurement_reviewed_by', 'procurement_reviewed_at',
            'finance_reviewed_by', 'finance_reviewed_at', 'scheduled_payment_date',
            'payment_date', 'payment_reference', 'paid_amount',
            'submitted_by', 'created_at', 'updated_at', 'processed_at',
            'approvals', 'audit_logs', 'payment_operations'
        ]
        read_only_fields = [
            'extracted_text', 'classification_confidence',
            'classification_reasoning', 'structured_line_items', 'po_allocations'
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
