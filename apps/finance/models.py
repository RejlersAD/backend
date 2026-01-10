"""
Finance Invoice Models
Converted from SQLAlchemy to Django ORM
"""
from django.db import models
from django.contrib.auth import get_user_model
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
    invoice_number = models.CharField(max_length=100, unique=True, db_index=True)
    vendor_name = models.CharField(max_length=500, null=True, blank=True)  # Increased from 255
    invoice_date = models.DateField(null=True, blank=True)
    
    # Amounts
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=10, default='AED')
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    
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
    
    # File storage
    original_filename = models.CharField(max_length=500)  # Increased from 255
    file_path = models.CharField(max_length=1000)  # Increased from 500
    
    # Status
    status = models.CharField(
        max_length=30,
        choices=InvoiceStatus.choices,
        default=InvoiceStatus.PENDING_EXTRACTION
    )
    
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
        ]
    
    def __str__(self):
        return f"{self.tracking_id or self.invoice_number} - {self.vendor_name or 'Unknown'}"
    
    def save(self, *args, **kwargs):
        """Override save to generate tracking_id if not present"""
        if not self.tracking_id:
            from datetime import datetime
            # Generate tracking ID: RAD-INV-YYYYMMDD-XXXX
            date_str = datetime.now().strftime('%Y%m%d')
            # Get count of invoices created today
            today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            today_count = Invoice.objects.filter(created_at__gte=today_start).count() + 1
            self.tracking_id = f"RAD-INV-{date_str}-{today_count:04d}"
        super().save(*args, **kwargs)


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
