"""
Django Admin for Electrical Checklist
"""
from django.contrib import admin
from .models import ChecklistExtractionJob


@admin.register(ChecklistExtractionJob)
class ChecklistExtractionJobAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'template_id', 'status', 'fields_extracted', 'signatures_found', 'confidence_score', 'created_at']
    list_filter = ['status', 'template_id', 'created_at']
    search_fields = ['user__username', 'template_id']
    readonly_fields = ['created_at', 'updated_at', 'completed_at']
    
    fieldsets = (
        ('Job Information', {
            'fields': ('user', 'template_id', 'status', 'progress')
        }),
        ('Results', {
            'fields': ('fields_extracted', 'signatures_found', 'confidence_score', 'extracted_data')
        }),
        ('Files', {
            'fields': ('file_count', 'total_pages', 'excel_file')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'completed_at')
        }),
        ('Error Info', {
            'fields': ('error_message',),
            'classes': ('collapse',)
        })
    )
