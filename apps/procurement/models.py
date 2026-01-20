"""
Procurement Management Models
Smart data models for procurement tracking, vendor management, and purchasing workflows
"""

from django.db import models
from django.contrib.auth import get_user_model
from apps.core.models import TimeStampedModel
import uuid

User = get_user_model()


# Soft-coded configuration for procurement categories
PROCUREMENT_CATEGORIES = {
    'equipment': {'name': 'Equipment', 'icon': 'CubeIcon', 'color': 'blue'},
    'materials': {'name': 'Materials', 'icon': 'ArchiveBoxIcon', 'color': 'green'},
    'services': {'name': 'Services', 'icon': 'WrenchIcon', 'color': 'purple'},
    'software': {'name': 'Software & Licenses', 'icon': 'ComputerDesktopIcon', 'color': 'indigo'},
    'consumables': {'name': 'Consumables', 'icon': 'ShoppingCartIcon', 'color': 'yellow'},
    'other': {'name': 'Other', 'icon': 'EllipsisHorizontalIcon', 'color': 'gray'},
}


class Vendor(TimeStampedModel):
    """
    Vendor/Supplier master data
    """
    
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('pending', 'Pending Approval'),
        ('blacklisted', 'Blacklisted'),
    ]
    
    RATING_CHOICES = [
        (5, 'Excellent'),
        (4, 'Good'),
        (3, 'Average'),
        (2, 'Below Average'),
        (1, 'Poor'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vendor_code = models.CharField(max_length=50, unique=True, db_index=True)
    name = models.CharField(max_length=300)
    contact_person = models.CharField(max_length=200, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    address = models.TextField(blank=True)
    country = models.CharField(max_length=100, blank=True)
    
    # Financial
    tax_id = models.CharField(max_length=100, blank=True)
    payment_terms = models.CharField(max_length=200, blank=True)
    credit_limit = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    
    # Performance
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    rating = models.IntegerField(choices=RATING_CHOICES, null=True, blank=True)
    performance_notes = models.TextField(blank=True)
    
    # Categories handled
    categories = models.JSONField(default=list, blank=True)  # List of category codes
    
    # Metadata
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='vendors_created')
    notes = models.TextField(blank=True)
    attachments = models.JSONField(default=list, blank=True)
    
    class Meta:
        db_table = 'procurement_vendors'
        ordering = ['name']
        indexes = [
            models.Index(fields=['vendor_code']),
            models.Index(fields=['status']),
            models.Index(fields=['rating']),
        ]
    
    def __str__(self):
        return f"{self.vendor_code} - {self.name}"


class PurchaseRequisition(TimeStampedModel):
    """
    Purchase Requisition (PR) - Internal request for procurement
    """
    
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
        ('converted', 'Converted to PO'),
    ]
    
    PRIORITY_CHOICES = [
        ('urgent', 'Urgent'),
        ('high', 'High'),
        ('normal', 'Normal'),
        ('low', 'Low'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pr_number = models.CharField(max_length=50, unique=True, db_index=True)
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=50)  # From PROCUREMENT_CATEGORIES
    
    # Requestor info
    requested_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='prs_requested')
    department = models.CharField(max_length=200, blank=True)
    project = models.CharField(max_length=200, blank=True)
    
    # Details
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='normal')
    required_date = models.DateField(null=True, blank=True)
    estimated_budget = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    
    # Items (JSON field for flexibility)
    items = models.JSONField(default=list, blank=True)
    # Example: [{'item': 'Laptop', 'qty': 2, 'unit': 'ea', 'estimated_price': 1500}]
    
    # Approval workflow
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='prs_approved')
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    
    # Metadata
    notes = models.TextField(blank=True)
    attachments = models.JSONField(default=list, blank=True)
    
    class Meta:
        db_table = 'procurement_requisitions'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['pr_number']),
            models.Index(fields=['status', 'priority']),
            models.Index(fields=['requested_by', 'status']),
            models.Index(fields=['-created_at']),
        ]
    
    def __str__(self):
        return f"PR-{self.pr_number}: {self.title}"


class PurchaseOrder(TimeStampedModel):
    """
    Purchase Order (PO) - Official order to vendor
    """
    
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('sent', 'Sent to Vendor'),
        ('acknowledged', 'Acknowledged by Vendor'),
        ('in_progress', 'In Progress'),
        ('partially_received', 'Partially Received'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    po_number = models.CharField(max_length=50, unique=True, db_index=True)
    pr_reference = models.ForeignKey(PurchaseRequisition, on_delete=models.SET_NULL, null=True, blank=True, related_name='purchase_orders')
    
    # Vendor info
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name='purchase_orders')
    
    # Details
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    category = models.CharField(max_length=50)
    
    # Financial
    total_amount = models.DecimalField(max_digits=15, decimal_places=2)
    currency = models.CharField(max_length=10, default='USD')
    tax_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    
    # Items
    items = models.JSONField(default=list, blank=True)
    # Example: [{'item': 'Laptop', 'qty': 2, 'unit_price': 1500, 'total': 3000}]
    
    # Dates
    po_date = models.DateField(auto_now_add=True)
    expected_delivery = models.DateField(null=True, blank=True)
    actual_delivery = models.DateField(null=True, blank=True)
    
    # People
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='pos_created')
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='pos_approved')
    
    # Metadata
    terms_and_conditions = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    attachments = models.JSONField(default=list, blank=True)
    
    class Meta:
        db_table = 'procurement_orders'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['po_number']),
            models.Index(fields=['vendor', 'status']),
            models.Index(fields=['status']),
            models.Index(fields=['-created_at']),
        ]
    
    def __str__(self):
        return f"PO-{self.po_number}: {self.title}"


class Receipt(TimeStampedModel):
    """
    Goods Receipt - Track deliveries and receiving
    """
    
    STATUS_CHOICES = [
        ('pending', 'Pending Inspection'),
        ('accepted', 'Accepted'),
        ('rejected', 'Rejected'),
        ('partial', 'Partially Accepted'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    receipt_number = models.CharField(max_length=50, unique=True, db_index=True)
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='receipts')
    
    # Receipt details
    receipt_date = models.DateField(auto_now_add=True)
    received_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='receipts_received')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    
    # Items received
    items_received = models.JSONField(default=list, blank=True)
    # Example: [{'item': 'Laptop', 'ordered_qty': 2, 'received_qty': 2, 'accepted_qty': 2}]
    
    # Quality check
    quality_check_passed = models.BooleanField(default=True)
    inspection_notes = models.TextField(blank=True)
    
    # Metadata
    delivery_note_number = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    attachments = models.JSONField(default=list, blank=True)
    
    class Meta:
        db_table = 'procurement_receipts'
        ordering = ['-receipt_date']
        indexes = [
            models.Index(fields=['receipt_number']),
            models.Index(fields=['purchase_order', 'status']),
            models.Index(fields=['-receipt_date']),
        ]
    
    def __str__(self):
        return f"GRN-{self.receipt_number} for {self.purchase_order.po_number}"
