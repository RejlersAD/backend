"""
QHSE Admin Configuration - Soft-coded admin interface
"""
from django.contrib import admin
from .models import QHSERunningProject, QHSESpotCheckRegister, QHSEAudit


@admin.register(QHSERunningProject)
class QHSERunningProjectAdmin(admin.ModelAdmin):
    """Admin interface for Running Projects"""
    list_display = [
        'sr_no', 'project_no', 'project_title_short', 'client', 
        'project_manager', 'project_starting_date', 'project_closing_date',
        'cars_open', 'obs_open', 'project_completion_percent', 'is_active'
    ]
    list_filter = [
        'client', 'project_manager', 'project_quality_eng', 
        'is_active', 'project_starting_date'
    ]
    search_fields = ['project_no', 'project_title', 'client', 'project_manager']
    readonly_fields = ['created_at', 'updated_at', 'manhours_balance', 'created_by', 'updated_by']
    fieldsets = (
        ('Project Information', {
            'fields': ('sr_no', 'project_no', 'project_title', 'project_title_key', 
                      'client', 'project_manager', 'project_quality_eng')
        }),
        ('Timeline', {
            'fields': ('project_starting_date', 'project_closing_date', 'project_extension')
        }),
        ('Manhours', {
            'fields': ('man_hour_for_quality', 'manhours_used', 'manhours_balance', 
                      'quality_billability_percent')
        }),
        ('Quality Plan', {
            'fields': ('project_quality_plan_status_rev', 'project_quality_plan_status_issue_date')
        }),
        ('Audits', {
            'fields': ('project_audit_1', 'project_audit_2', 'project_audit_3', 'project_audit_4',
                      'client_audit_1', 'client_audit_2', 'delay_in_audits_no_days'),
            'classes': ('collapse',)
        }),
        ('CARs & Observations', {
            'fields': ('cars_open', 'cars_delayed_closing_no_days', 'cars_closed',
                      'obs_open', 'obs_delayed_closing_no_days', 'obs_closed')
        }),
        ('Performance Metrics', {
            'fields': ('project_kpis_achieved_percent', 'project_completion_percent',
                      'rejection_of_deliverables_percent', 'cost_of_poor_quality_aed')
        }),
        ('Additional Information', {
            'fields': ('remarks', 'is_active')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at', 'created_by', 'updated_by'),
            'classes': ('collapse',)
        }),
    )
    
    def project_title_short(self, obj):
        """Display shortened project title"""
        return obj.project_title[:50] + '...' if len(obj.project_title) > 50 else obj.project_title
    project_title_short.short_description = 'Project Title'
    
    def save_model(self, request, obj, form, change):
        """Auto-set user on save"""
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(QHSESpotCheckRegister)
class QHSESpotCheckRegisterAdmin(admin.ModelAdmin):
    """Admin interface for Spot Check Register"""
    list_display = [
        'sr_no', 'project_no', 'date_of_spot_check', 'qhse_engineer',
        'category', 'status', 'is_active'
    ]
    list_filter = ['status', 'category', 'qhse_engineer', 'date_of_spot_check', 'is_active']
    search_fields = ['project_no', 'project_title', 'qhse_engineer', 'document_no']
    readonly_fields = ['created_at', 'updated_at', 'created_by', 'updated_by']
    date_hierarchy = 'date_of_spot_check'
    fieldsets = (
        ('Project Information', {
            'fields': ('sr_no', 'project_no', 'project_title', 'client')
        }),
        ('Spot Check Details', {
            'fields': ('qhse_engineer', 'date_of_spot_check', 'time')
        }),
        ('Document Information', {
            'fields': ('document_no', 'document_title', 'originator_lead')
        }),
        ('Findings', {
            'fields': ('comments', 'category', 'remarks')
        }),
        ('Status & Resolution', {
            'fields': ('status', 'resolution_date', 'resolution_comments')
        }),
        ('Metadata', {
            'fields': ('is_active', 'created_at', 'updated_at', 'created_by', 'updated_by'),
            'classes': ('collapse',)
        }),
    )
    
    def save_model(self, request, obj, form, change):
        """Auto-set user on save"""
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(QHSEAudit)
class QHSEAuditAdmin(admin.ModelAdmin):
    """Admin interface for Audits"""
    list_display = ['project', 'audit_type', 'audit_number', 'audit_date', 'auditor', 'status']
    list_filter = ['audit_type', 'status', 'audit_date']
    search_fields = ['project__project_no', 'project__project_title', 'auditor']
    date_hierarchy = 'audit_date'
    autocomplete_fields = ['project']
