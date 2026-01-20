"""
DesignIQ Admin Configuration
"""

from django.contrib import admin
from .models import DesignProject, DesignAnalysis, DesignOptimization, DesignTemplate, EngineeringListItem


@admin.register(DesignProject)
class DesignProjectAdmin(admin.ModelAdmin):
    list_display = ['project_name', 'design_type', 'status', 'created_by', 'ai_confidence_score', 'created_at']
    list_filter = ['status', 'design_type', 'created_at']
    search_fields = ['project_name', 'description', 'organization']
    readonly_fields = ['id', 'created_at', 'updated_at']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('id', 'project_name', 'design_type', 'description', 'status')
        }),
        ('User & Organization', {
            'fields': ('created_by', 'organization')
        }),
        ('Design Parameters', {
            'fields': ('design_parameters',)
        }),
        ('AI Analysis', {
            'fields': ('ai_analysis_results', 'ai_confidence_score', 'ai_recommendations')
        }),
        ('Files', {
            'fields': ('input_file', 'output_file')
        }),
        ('Metadata', {
            'fields': ('processing_time', 'error_message', 'created_at', 'updated_at')
        }),
    )


@admin.register(DesignAnalysis)
class DesignAnalysisAdmin(admin.ModelAdmin):
    list_display = ['title', 'project', 'severity', 'is_resolved', 'ai_confidence', 'created_at']
    list_filter = ['severity', 'is_resolved', 'analysis_type', 'created_at']
    search_fields = ['title', 'description', 'ai_finding']
    readonly_fields = ['id', 'created_at', 'updated_at']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('id', 'project', 'analysis_type', 'title', 'description', 'severity')
        }),
        ('AI Insights', {
            'fields': ('ai_finding', 'ai_recommendation', 'ai_confidence')
        }),
        ('Standards & References', {
            'fields': ('standard_reference', 'code_section')
        }),
        ('Resolution', {
            'fields': ('is_resolved', 'resolved_by', 'resolved_at', 'resolution_notes')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )


@admin.register(DesignOptimization)
class DesignOptimizationAdmin(admin.ModelAdmin):
    list_display = ['title', 'project', 'impact', 'is_implemented', 'estimated_cost_savings', 'created_at']
    list_filter = ['impact', 'is_implemented', 'category', 'created_at']
    search_fields = ['title', 'description', 'category']
    readonly_fields = ['id', 'created_at', 'updated_at']


@admin.register(DesignTemplate)
class DesignTemplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'design_type', 'is_public', 'usage_count', 'created_by', 'created_at']
    list_filter = ['design_type', 'is_public', 'created_at']
    search_fields = ['name', 'description']
    readonly_fields = ['id', 'usage_count', 'created_at', 'updated_at']


@admin.register(EngineeringListItem)
class EngineeringListItemAdmin(admin.ModelAdmin):
    list_display = ['item_tag', 'list_type', 'project', 'status', 'is_validated', 'version', 'created_at']
    list_filter = ['list_type', 'status', 'is_validated', 'created_at']
    search_fields = ['item_tag', 'description', 'notes']
    readonly_fields = ['id', 'version', 'revision_history', 'created_at', 'updated_at']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('id', 'project', 'list_type', 'item_tag', 'description', 'status')
        }),
        ('Data', {
            'fields': ('data',),
            'classes': ('collapse',)
        }),
        ('User Tracking', {
            'fields': ('created_by', 'updated_by', 'notes', 'attachments')
        }),
        ('Validation', {
            'fields': ('is_validated', 'validation_notes', 'validated_at', 'validated_by')
        }),
        ('Version Control', {
            'fields': ('version', 'revision_history'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )
    
    def get_queryset(self, request):
        """Optimize queries"""
        return super().get_queryset(request).select_related('project', 'created_by', 'validated_by')

