from django.contrib import admin
from .models import PIDDrawing, PIDAnalysisReport, PIDIssue


@admin.register(PIDDrawing)
class PIDDrawingAdmin(admin.ModelAdmin):
    list_display = ['drawing_number', 'original_filename', 'status', 'uploaded_by', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['drawing_number', 'original_filename', 'project_name']
    readonly_fields = ['created_at', 'updated_at', 'file_size']
    
    fieldsets = (
        ('File Information', {
            'fields': ('file', 'original_filename', 'file_size')
        }),
        ('Drawing Metadata', {
            'fields': ('drawing_number', 'drawing_title', 'revision', 'project_name')
        }),
        ('Analysis Status', {
            'fields': ('status', 'analysis_started_at', 'analysis_completed_at')
        }),
        ('Tracking', {
            'fields': ('uploaded_by', 'created_at', 'updated_at')
        }),
    )


class PIDIssueInline(admin.TabularInline):
    model = PIDIssue
    extra = 0
    fields = ['serial_number', 'pid_reference', 'severity', 'status']
    readonly_fields = ['serial_number']


@admin.register(PIDAnalysisReport)
class PIDAnalysisReportAdmin(admin.ModelAdmin):
    list_display = ['pid_drawing', 'total_issues', 'approved_count', 'pending_count', 'generated_at']
    list_filter = ['generated_at']
    readonly_fields = ['generated_at', 'updated_at']
    inlines = [PIDIssueInline]
    
    fieldsets = (
        ('Report Summary', {
            'fields': ('pid_drawing', 'total_issues', 'approved_count', 'ignored_count', 'pending_count')
        }),
        ('Generated Files', {
            'fields': ('pdf_report', 'excel_report')
        }),
        ('Timestamps', {
            'fields': ('generated_at', 'updated_at')
        }),
    )


@admin.register(PIDIssue)
class PIDIssueAdmin(admin.ModelAdmin):
    list_display = ['serial_number', 'pid_reference', 'severity', 'status', 'category']
    list_filter = ['severity', 'status', 'category']
    search_fields = ['pid_reference', 'issue_observed', 'action_required']
    
    fieldsets = (
        ('Issue Details', {
            'fields': ('report', 'serial_number', 'pid_reference', 'issue_observed', 'action_required')
        }),
        ('Classification', {
            'fields': ('severity', 'category')
        }),
        ('Review Status', {
            'fields': ('status', 'approval', 'remark')
        }),
    )
