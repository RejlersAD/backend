from django.contrib import admin
from .models import (
    Invoice,
    InvoiceLineItem,
    InvoicePurchaseOrderAllocation,
    Approval,
    AuditLog,
    ApprovalRoute,
)


class InvoiceLineItemInline(admin.TabularInline):
    model = InvoiceLineItem
    extra = 0


class InvoicePurchaseOrderAllocationInline(admin.TabularInline):
    model = InvoicePurchaseOrderAllocation
    extra = 0
    show_change_link = True


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = [
        'invoice_number', 'vendor_name', 'total_amount', 'currency',
        'procurement_status', 'match_status', 'payment_status', 'created_at',
    ]
    list_filter = [
        'procurement_status', 'match_status', 'payment_status',
        'status', 'invoice_type', 'created_at',
    ]
    search_fields = ['invoice_number', 'vendor_name', 'vendor__name', 'po_reference_text']
    readonly_fields = ['created_at', 'updated_at']
    inlines = [InvoiceLineItemInline, InvoicePurchaseOrderAllocationInline]


@admin.register(InvoiceLineItem)
class InvoiceLineItemAdmin(admin.ModelAdmin):
    list_display = ['invoice', 'line_number', 'description', 'quantity', 'total_amount', 'currency']
    search_fields = ['invoice__invoice_number', 'description', 'po_item_reference']


@admin.register(InvoicePurchaseOrderAllocation)
class InvoicePurchaseOrderAllocationAdmin(admin.ModelAdmin):
    list_display = [
        'invoice', 'purchase_order', 'allocated_amount', 'currency',
        'match_method', 'match_status', 'created_at',
    ]
    list_filter = ['match_method', 'match_status', 'vendor_matched', 'currency_matched']
    search_fields = ['invoice__invoice_number', 'purchase_order__po_number']
    filter_horizontal = ['receipts']


@admin.register(Approval)
class ApprovalAdmin(admin.ModelAdmin):
    list_display = ['invoice', 'approver_name', 'approval_level', 'status', 'decision_date']
    list_filter = ['status', 'approval_level']
    search_fields = ['invoice__invoice_number', 'approver_email']


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ['invoice', 'action', 'timestamp']
    list_filter = ['action', 'timestamp']
    search_fields = ['invoice__invoice_number', 'description']


@admin.register(ApprovalRoute)
class ApprovalRouteAdmin(admin.ModelAdmin):
    list_display = ['invoice_type', 'priority', 'is_active']
    list_filter = ['invoice_type', 'is_active']
