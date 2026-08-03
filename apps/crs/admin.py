from django.contrib import admin
from .models import CRSDocument, CRSComment, CRSActivity, GoogleSheetConfig


@admin.register(CRSDocument)
class CRSDocumentAdmin(admin.ModelAdmin):
    list_display = ['id', 'document_name', 'status', 'total_comments', 'resolved_comments', 'completion_percentage', 'uploaded_by', 'created_at']
    list_filter = ['status', 'created_at', 'updated_at']
    search_fields = ['document_name', 'document_number', 'project_name', 'contractor_name']
    readonly_fields = ['created_at', 'updated_at', 'processed_at', 'completion_percentage', 'is_completed']
    
    fieldsets = (
        ('Document Information', {
            'fields': ('document_name', 'document_number', 'pdf_file', 'status')
        }),
        ('Google Sheets', {
            'fields': ('google_sheet_id', 'google_sheet_url')
        }),
        ('Statistics', {
            'fields': ('total_comments', 'resolved_comments', 'pending_comments', 'completion_percentage', 'is_completed')
        }),
        ('Project Details', {
            'fields': ('project_name', 'contractor_name', 'revision_number')
        }),
        ('Assignment', {
            'fields': ('uploaded_by', 'assigned_to')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'processed_at')
        }),
        ('Notes', {
            'fields': ('notes',)
        }),
    )


@admin.register(CRSComment)
class CRSCommentAdmin(admin.ModelAdmin):
    list_display = ['id', 'document', 'serial_number', 'page_number', 'comment_type', 'status', 'priority', 'has_contractor_response', 'has_company_response']
    list_filter = ['comment_type', 'status', 'priority', 'created_at']
    search_fields = ['comment_text', 'clause_number', 'document__document_name']
    readonly_fields = ['created_at', 'updated_at', 'resolved_at', 'has_contractor_response', 'has_company_response', 'is_fully_responded']
    
    fieldsets = (
        ('Comment Details', {
            'fields': ('document', 'serial_number', 'page_number', 'clause_number', 'comment_text')
        }),
        ('Classification', {
            'fields': ('comment_type', 'status', 'priority')
        }),
        ('Technical Data', {
            'fields': ('color_rgb', 'bbox'),
            'classes': ('collapse',)
        }),
        ('Contractor Response', {
            'fields': ('contractor_response', 'contractor_response_date', 'contractor_responder', 'has_contractor_response')
        }),
        ('Company Response', {
            'fields': ('company_response', 'company_response_date', 'company_responder', 'has_company_response')
        }),
        ('Assignment', {
            'fields': ('assigned_to',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'resolved_at')
        }),
        ('Additional Info', {
            'fields': ('notes', 'is_fully_responded')
        }),
    )


@admin.register(CRSActivity)
class CRSActivityAdmin(admin.ModelAdmin):
    list_display = ['id', 'document', 'comment', 'action', 'performed_by', 'performed_at']
    list_filter = ['action', 'performed_at']
    search_fields = ['description', 'document__document_name']
    readonly_fields = ['performed_at']
    
    fieldsets = (
        ('Activity Information', {
            'fields': ('document', 'comment', 'action', 'description')
        }),
        ('Performed By', {
            'fields': ('performed_by', 'performed_at')
        }),
        ('Changes', {
            'fields': ('old_value', 'new_value'),
            'classes': ('collapse',)
        }),
    )


@admin.register(GoogleSheetConfig)
class GoogleSheetConfigAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'is_active', 'created_by', 'created_at']
    list_filter = ['is_active', 'created_at']
    search_fields = ['name']
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('Configuration', {
            'fields': ('name', 'is_active')
        }),
        ('Google API Credentials', {
            'fields': ('client_id', 'client_secret', 'credentials_json'),
            'classes': ('collapse',)
        }),
        ('OAuth Token', {
            'fields': ('token_json',),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('created_by', 'created_at', 'updated_at')
        }),
    )
