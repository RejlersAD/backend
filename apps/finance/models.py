"""
Finance Invoice Models
Converted from SQLAlchemy to Django ORM
"""
from django.db import models
from django.contrib.auth import get_user_model
from django.core.validators import MaxValueValidator, MinValueValidator
from django.utils import timezone
import uuid

User = get_user_model()


class InvoiceStatus(models.TextChoices):
    PENDING_EXTRACTION = 'pending_extraction', 'Pending Extraction'
    EXTRACTION_FAILED = 'extraction_failed', 'Extraction Failed'
    PENDING_CLASSIFICATION = 'pending_classification', 'Pending Classification'
    CLASSIFICATION_FAILED = 'classification_failed', 'Classification Failed'
    PENDING_APPROVAL = 'pending_approval', 'Pending Approval'
    APPROVED = 'approved', 'Approved'
    REJECTED = 'rejected', 'Rejected'
    PROCESSED = 'processed', 'Processed'


class InvoiceType(models.TextChoices):
    FINANCE = 'finance', 'Finance Invoice'
    IT = 'it', 'IT Invoice'
    PROJECT = 'project', 'Project Invoice'
    ADMIN = 'admin', 'Admin/General Invoice'


class ApprovalStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    APPROVED = 'approved', 'Approved'
    REJECTED = 'rejected', 'Rejected'


class ProcurementInvoiceStatus(models.TextChoices):
    """A/P lifecycle layered over the legacy extraction/approval status."""
    OCR_REVIEW = 'ocr_review', 'OCR Review'
    READY_FOR_MATCHING = 'ready_for_matching', 'Ready for Matching'
    PROCUREMENT_REVIEW = 'procurement_review', 'Procurement Review'
    FINANCE_REVIEW = 'finance_review', 'Finance Review'
    APPROVED_FOR_PAYMENT = 'approved_for_payment', 'Approved for Payment'
    REJECTED = 'rejected', 'Rejected'
    CLOSED = 'closed', 'Closed'


class InvoiceMatchStatus(models.TextChoices):
    UNMATCHED = 'unmatched', 'Unmatched'
    AUTO_MATCHED = 'auto_matched', 'Automatically Matched'
    MANUAL_MATCHED = 'manual_matched', 'Manually Matched'
    EXCEPTION = 'exception', 'Matching Exception'
    VERIFIED = 'verified', 'Verified'


class InvoicePaymentStatus(models.TextChoices):
    NOT_SCHEDULED = 'not_scheduled', 'Not Scheduled'
    SCHEDULED = 'scheduled', 'Scheduled'
    PARTIAL = 'partial', 'Partially Paid'
    PAID = 'paid', 'Paid'
    ON_HOLD = 'on_hold', 'On Hold'
    CANCELLED = 'cancelled', 'Cancelled'


class AllocationMatchMethod(models.TextChoices):
    AUTOMATIC = 'automatic', 'Automatic'
    MANUAL = 'manual', 'Manual'
    IMPORTED = 'imported', 'Imported'


class InvoiceOCRJobStatus(models.TextChoices):
    QUEUED = 'queued', 'Queued'
    PROCESSING = 'processing', 'Processing'
    COMPLETED = 'completed', 'Completed'
    FAILED = 'failed', 'Failed'


class Invoice(models.Model):
    """Invoice model - stores all invoice data and processing status"""
    
    # Primary key
    id = models.BigAutoField(primary_key=True)
    
    # Unique tracking ID (RAD-INV-YYYYMMDD-XXXX format)
    tracking_id = models.CharField(max_length=50, unique=True, db_index=True, null=True, blank=True)
    
    # Email source (if from email)
    email_subject = models.CharField(max_length=500, null=True, blank=True)
    email_from = models.EmailField(null=True, blank=True)
    email_date = models.DateTimeField(null=True, blank=True)
    
    # Invoice details
    # Supplier invoice numbers are only unique inside a vendor account. Two
    # different suppliers can legitimately issue the same invoice number.
    invoice_number = models.CharField(max_length=100, db_index=True)
    vendor_name = models.CharField(max_length=500, null=True, blank=True)  # Increased from 255
    invoice_date = models.DateField(null=True, blank=True)
    received_date = models.DateField(null=True, blank=True, db_index=True)
    due_date = models.DateField(null=True, blank=True, db_index=True)
    payment_terms = models.CharField(max_length=300, blank=True, default='')
    
    # Amounts
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=10, default='AED')
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    vat_percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    vat_registration_number = models.CharField(max_length=100, blank=True, default='')

    # Procurement master-data linkage. Keep vendor_name for OCR and legacy rows.
    vendor = models.ForeignKey(
        'procurement.Vendor',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='vendor_invoices',
    )
    po_reference_text = models.CharField(
        max_length=100,
        blank=True,
        default='',
        db_index=True,
        help_text='PO reference captured from the invoice before confirmed allocation.',
    )
    
    # Classification
    invoice_type = models.CharField(
        max_length=20,
        choices=InvoiceType.choices,
        null=True,
        blank=True
    )
    classification_confidence = models.FloatField(null=True, blank=True)
    classification_reasoning = models.TextField(null=True, blank=True)
    
    # Extracted data
    extracted_text = models.TextField(null=True, blank=True)
    line_items = models.JSONField(null=True, blank=True)  # Store as JSON
    ocr_metadata = models.JSONField(default=dict, blank=True)
    ocr_confidence = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    manual_review_required = models.BooleanField(default=True, db_index=True)
    source_file_sha256 = models.CharField(max_length=64, blank=True, default='', db_index=True)
    
    # File storage
    original_filename = models.CharField(max_length=500)  # Increased from 255
    file_path = models.CharField(max_length=1000)  # Increased from 500
    
    # Status
    status = models.CharField(
        max_length=30,
        choices=InvoiceStatus.choices,
        default=InvoiceStatus.PENDING_EXTRACTION
    )
    procurement_status = models.CharField(
        max_length=30,
        choices=ProcurementInvoiceStatus.choices,
        default=ProcurementInvoiceStatus.OCR_REVIEW,
        db_index=True,
    )
    match_status = models.CharField(
        max_length=20,
        choices=InvoiceMatchStatus.choices,
        default=InvoiceMatchStatus.UNMATCHED,
        db_index=True,
    )
    payment_status = models.CharField(
        max_length=20,
        choices=InvoicePaymentStatus.choices,
        default=InvoicePaymentStatus.NOT_SCHEDULED,
        db_index=True,
    )

    # Procurement / Finance review and settlement audit fields.
    procurement_reviewed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='procurement_reviewed_vendor_invoices',
    )
    procurement_reviewed_at = models.DateTimeField(null=True, blank=True)
    finance_reviewed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='finance_reviewed_vendor_invoices',
    )
    finance_reviewed_at = models.DateTimeField(null=True, blank=True)
    scheduled_payment_date = models.DateField(null=True, blank=True)
    payment_date = models.DateField(null=True, blank=True)
    payment_reference = models.CharField(max_length=150, blank=True, default='')
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    
    # Submitter (optional - linked to RAD AI user)
    submitted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='submitted_invoices'
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['invoice_number']),
            models.Index(fields=['status']),
            models.Index(fields=['invoice_type']),
            models.Index(fields=['created_at']),
            models.Index(fields=['vendor', 'procurement_status']),
            models.Index(fields=['match_status', 'payment_status']),
            models.Index(fields=['due_date', 'payment_status']),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(paid_amount__gte=0),
                name='finance_invoice_paid_amount_nonnegative',
            ),
            models.UniqueConstraint(
                fields=['source_file_sha256'],
                condition=~models.Q(source_file_sha256=''),
                name='finance_invoice_unique_source_hash',
            ),
            models.UniqueConstraint(
                models.functions.Lower('invoice_number'), 'vendor',
                condition=models.Q(vendor__isnull=False),
                name='finance_invoice_unique_number_per_vendor_ci',
            ),
            models.UniqueConstraint(
                models.functions.Lower('invoice_number'),
                condition=models.Q(vendor__isnull=True),
                name='finance_invoice_unique_unlinked_number_ci',
            ),
        ]
    
    def __str__(self):
        return f"{self.tracking_id or self.invoice_number} - {self.vendor_name or 'Unknown'}"
    
    def save(self, *args, **kwargs):
        """Override save to generate tracking_id if not present"""
        if not self.tracking_id:
            # Generate tracking ID: RAD-INV-YYYYMMDD-XXXX
            today = timezone.localdate()
            date_str = today.strftime('%Y%m%d')
            # Get count of invoices created today
            today_count = Invoice.objects.filter(created_at__date=today).count() + 1
            self.tracking_id = f"RAD-INV-{date_str}-{today_count:04d}"
        super().save(*args, **kwargs)


class InvoiceLineItem(models.Model):
    """Normalized invoice line used by OCR review and later three-way matching."""

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name='structured_line_items',
    )
    line_number = models.PositiveIntegerField()
    description = models.TextField(blank=True, default='')
    quantity = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    unit_price = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    net_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    tax_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=10, blank=True, default='')
    po_item_reference = models.CharField(max_length=100, blank=True, default='')
    source_data = models.JSONField(default=dict, blank=True)
    ocr_confidence = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    manually_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['line_number', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['invoice', 'line_number'],
                name='finance_invoice_unique_line_number',
            ),
        ]
        indexes = [models.Index(fields=['invoice', 'line_number'])]

    def __str__(self):
        return f"{self.invoice.invoice_number} line {self.line_number}"


class InvoicePurchaseOrderAllocation(models.Model):
    """Auditable allocation and match evidence between an invoice and a PO."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name='po_allocations',
    )
    purchase_order = models.ForeignKey(
        'procurement.PurchaseOrder',
        on_delete=models.PROTECT,
        related_name='invoice_allocations',
    )
    receipts = models.ManyToManyField(
        'procurement.Receipt',
        blank=True,
        related_name='invoice_allocations',
    )
    allocated_amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=10, default='AED')
    match_method = models.CharField(
        max_length=20,
        choices=AllocationMatchMethod.choices,
        default=AllocationMatchMethod.MANUAL,
    )
    match_status = models.CharField(
        max_length=20,
        choices=InvoiceMatchStatus.choices,
        default=InvoiceMatchStatus.UNMATCHED,
        db_index=True,
    )
    match_confidence = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    po_amount_at_match = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    invoice_amount_at_match = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    amount_variance = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    tolerance_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=5,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    amount_within_tolerance = models.BooleanField(default=False)
    vendor_matched = models.BooleanField(default=False)
    currency_matched = models.BooleanField(default=False)
    receipt_required = models.BooleanField(default=True)
    exception_codes = models.JSONField(default=list, blank=True)
    match_evidence = models.JSONField(
        default=dict,
        blank=True,
        help_text='Snapshot of PO, invoice-line, and receipt quantity/value checks.',
    )
    line_items_matched = models.BooleanField(default=False)
    receipt_quantities_matched = models.BooleanField(default=False)
    review_notes = models.TextField(blank=True, default='')
    matched_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='matched_vendor_invoice_allocations',
    )
    matched_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='verified_vendor_invoice_allocations',
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['invoice', 'purchase_order'],
                name='finance_invoice_unique_po_allocation',
            ),
            models.CheckConstraint(
                check=models.Q(allocated_amount__gte=0),
                name='finance_invoice_allocation_nonnegative',
            ),
        ]
        indexes = [
            models.Index(fields=['purchase_order', 'match_status']),
            models.Index(fields=['invoice', 'match_status']),
        ]

    def __str__(self):
        return f"{self.invoice.invoice_number} -> {self.purchase_order.po_number}"


class InvoiceOCRJob(models.Model):
    """Durable asynchronous OCR job used by the A/P import preview."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(
        max_length=20,
        choices=InvoiceOCRJobStatus.choices,
        default=InvoiceOCRJobStatus.QUEUED,
        db_index=True,
    )
    original_filename = models.CharField(max_length=500)
    file_path = models.CharField(max_length=1000)
    source_file_sha256 = models.CharField(max_length=64, db_index=True)
    result = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True, default='')
    requested_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='invoice_ocr_jobs',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['requested_by', 'status', '-created_at'])]


class PayablePayment(models.Model):
    """Immutable payment-operation ledger for supplier invoices."""

    class Operation(models.TextChoices):
        SCHEDULE = 'schedule', 'Schedule'
        PAYMENT = 'payment', 'Payment'
        HOLD = 'hold', 'Hold'
        RELEASE = 'release', 'Release Hold'
        CANCEL = 'cancel', 'Cancel'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name='payment_operations')
    operation = models.CharField(max_length=20, choices=Operation.choices, db_index=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=10, default='AED')
    effective_date = models.DateField(null=True, blank=True)
    reference = models.CharField(max_length=150, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    metadata = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='payable_payment_operations',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['invoice', 'operation', '-created_at'])]
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount__isnull=True) | models.Q(amount__gt=0),
                name='finance_payable_payment_positive_amount',
            ),
        ]


class Approval(models.Model):
    """Approval workflow for invoices - multi-level approval system"""
    
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name='approvals'
    )
    
    # Approval details
    approver_name = models.CharField(max_length=100)
    approver_email = models.EmailField()
    approval_level = models.IntegerField(help_text="1=Department, 2=CEO, etc.")
    level_name = models.CharField(max_length=50, help_text="e.g., Finance Head, CEO")
    
    # Additional metadata (title, CC emails, mandatory flag)
    approval_metadata = models.JSONField(null=True, blank=True, help_text="Store title, CC emails, mandatory flag")
    
    # Status
    status = models.CharField(
        max_length=20,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.PENDING
    )
    
    # Decision
    decision = models.CharField(max_length=20, null=True, blank=True)
    comments = models.TextField(null=True, blank=True)
    decision_date = models.DateTimeField(null=True, blank=True)
    
    # Security token for email-based approval
    approval_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['approval_level', 'created_at']
        indexes = [
            models.Index(fields=['approval_token']),
            models.Index(fields=['status']),
        ]
    
    def __str__(self):
        return f"{self.invoice.invoice_number} - Level {self.approval_level} - {self.approver_name}"


class AuditLog(models.Model):
    """Audit trail for all invoice actions"""
    
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name='audit_logs'
    )
    
    action = models.CharField(max_length=100)
    description = models.TextField(null=True, blank=True)
    metadata = models.JSONField(null=True, blank=True)
    
    # User who performed action (optional)
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    
    timestamp = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['timestamp']),
            models.Index(fields=['action']),
        ]
    
    def __str__(self):
        return f"{self.invoice.invoice_number} - {self.action} - {self.timestamp}"


class ApprovalRoute(models.Model):
    """
    Configurable approval routing based on invoice type and amount
    Soft-coded approval rules
    """
    
    invoice_type = models.CharField(
        max_length=20,
        choices=InvoiceType.choices
    )
    
    # Amount-based routing (optional)
    min_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    max_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    
    # Approval chain (stored as JSON)
    approval_chain = models.JSONField(help_text="Array of approval levels with approver details")
    
    # Example structure:
    # [
    #   {"level": 1, "name": "Finance Head", "email": "finance@company.com"},
    #   {"level": 2, "name": "CEO", "email": "ceo@company.com"}
    # ]
    
    is_active = models.BooleanField(default=True)
    priority = models.IntegerField(default=0, help_text="Higher priority routes are checked first")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-priority', 'invoice_type']
    
    def __str__(self):
        return f"{self.get_invoice_type_display()} - {len(self.approval_chain)} levels"


# =================================================================
# SALARY SLIP AUTOMATION SYSTEM MODELS
# Import all salary-related models from salary_models.py
# =================================================================
from .salary_models import (
    SalaryStatus,
    ApprovalStatus,
    EmailStatus,
    EmployeeSalaryInfo,
    SalaryComponent,
    EmployeeSalaryComponent,
    PayrollRun,
    SalarySlip,
    SalarySlipApproval,
    SalarySlipEmail,
    SalarySlipAuditLog,
)
