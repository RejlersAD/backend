from django.contrib import admin
from .models import Invoice, Approval, AuditLog, ApprovalRoute


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ['invoice_number', 'vendor_name', 'total_amount', 'currency', 'invoice_type', 'status', 'created_at']
    list_filter = ['status', 'invoice_type', 'created_at']
    search_fields = ['invoice_number', 'vendor_name']
    readonly_fields = ['created_at', 'updated_at']


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
