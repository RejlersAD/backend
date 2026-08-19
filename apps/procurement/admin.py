"""
Procurement Management Admin Configuration
"""

from django.contrib import admin
from .models import Vendor, PurchaseRequisition, PurchaseOrder, Receipt


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    """Admin interface for Vendor model"""
    
    list_display = [
        'vendor_code', 'name', 'contact_person', 'email', 'phone',
        'country', 'status', 'rating', 'created_at'
    ]
    list_filter = ['status', 'rating', 'country', 'created_at']
    search_fields = ['vendor_code', 'name', 'contact_person', 'email', 'phone']
    readonly_fields = ['id', 'created_at', 'updated_at']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('vendor_code', 'name', 'contact_person', 'email', 'phone')
        }),
        ('Address', {
            'fields': ('address', 'country')
        }),
        ('Financial', {
            'fields': ('tax_id', 'payment_terms', 'credit_limit')
        }),
        ('Status & Rating', {
            'fields': ('status', 'rating', 'performance_notes')
        }),
        ('Categories', {
            'fields': ('categories',)
        }),
        ('Additional Information', {
            'fields': ('created_by', 'notes', 'attachments')
        }),
        ('Timestamps', {
            'fields': ('id', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )


@admin.register(PurchaseRequisition)
class PurchaseRequisitionAdmin(admin.ModelAdmin):
    """Admin interface for Purchase Requisition model"""
    
    list_display = [
        'pr_number', 'title', 'category', 'requested_by', 'department',
        'status', 'priority', 'required_date', 'estimated_budget', 'created_at'
    ]
    list_filter = ['status', 'priority', 'category', 'department', 'created_at']
    search_fields = ['pr_number', 'title', 'description', 'department']
    readonly_fields = ['id', 'created_at', 'updated_at', 'approved_at']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('pr_number', 'title', 'description', 'category')
        }),
        ('Requester Details', {
            'fields': ('requested_by', 'department', 'project')
        }),
        ('Priority & Timeline', {
            'fields': ('status', 'priority', 'required_date', 'estimated_budget')
        }),
        ('Items', {
            'fields': ('items',)
        }),
        ('Approval', {
            'fields': ('approved_by', 'approved_at', 'rejection_reason')
        }),
        ('Additional Information', {
            'fields': ('notes', 'attachments')
        }),
        ('Timestamps', {
            'fields': ('id', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    """Admin interface for Purchase Order model"""
    
    list_display = [
        'po_number', 'pr_reference', 'vendor', 'title', 'status',
        'total_amount', 'currency', 'po_date', 'expected_delivery', 'created_at'
    ]
    list_filter = ['status', 'currency', 'po_date', 'expected_delivery', 'created_at']
    search_fields = ['po_number', 'pr_reference', 'title', 'description', 'vendor__name']
    readonly_fields = ['id', 'po_date', 'created_at', 'updated_at']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('po_number', 'pr_reference', 'vendor', 'title', 'description')
        }),
        ('Status & Category', {
            'fields': ('status', 'category')
        }),
        ('Financial Details', {
            'fields': ('total_amount', 'currency', 'tax_amount', 'discount_amount')
        }),
        ('Items', {
            'fields': ('items',)
        }),
        ('Dates', {
            'fields': ('po_date', 'expected_delivery', 'actual_delivery')
        }),
        ('Approval', {
            'fields': ('created_by', 'approved_by')
        }),
        ('Terms & Conditions', {
            'fields': ('terms_and_conditions', 'notes', 'attachments')
        }),
        ('Timestamps', {
            'fields': ('id', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )


@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    """Admin interface for Receipt model"""
    
    list_display = [
        'receipt_number', 'purchase_order', 'receipt_date', 'received_by',
        'status', 'quality_check_passed', 'delivery_note_number', 'created_at'
    ]
    list_filter = ['status', 'quality_check_passed', 'receipt_date', 'created_at']
    search_fields = ['receipt_number', 'purchase_order__po_number', 'delivery_note_number']
    readonly_fields = ['id', 'receipt_date', 'created_at', 'updated_at']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('receipt_number', 'purchase_order', 'receipt_date', 'received_by')
        }),
        ('Status', {
            'fields': ('status', 'quality_check_passed')
        }),
        ('Items Received', {
            'fields': ('items_received',)
        }),
        ('Inspection', {
            'fields': ('inspection_notes', 'delivery_note_number')
        }),
        ('Additional Information', {
            'fields': ('notes', 'attachments')
        }),
        ('Timestamps', {
            'fields': ('id', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
