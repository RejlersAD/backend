"""
Process Datasheet Admin Interface
"""
from django.contrib import admin
from .models import (
    EquipmentType,
    ProcessDatasheet,
    DatasheetRevision,
    DatasheetTemplate,
    DatasheetValidationRule,
    DatasheetExtractionJob
)


@admin.register(EquipmentType)
class EquipmentTypeAdmin(admin.ModelAdmin):
    list_display = ['icon', 'name', 'code', 'category', 'version', 'status', 'created_at']
    list_filter = ['status', 'category']
    search_fields = ['name', 'code', 'description']
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('code', 'name', 'icon', 'description', 'category')
        }),
        ('Configuration', {
            'fields': ('configuration', 'template_file', 'calculation_module')
        }),
        ('Standards & Version', {
            'fields': ('applicable_standards', 'version', 'status')
        }),
        ('Metadata', {
            'fields': ('created_by', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


class DatasheetRevisionInline(admin.TabularInline):
    model = DatasheetRevision
    extra = 0
    readonly_fields = ['revision_number', 'description', 'revised_by', 'revision_date']
    can_delete = False


@admin.register(ProcessDatasheet)
class ProcessDatasheetAdmin(admin.ModelAdmin):
    list_display = [
        'document_number',
        'tag_number',
        'equipment_type',
        'status',
        'revision',
        'validation_score',
        'updated_at'
    ]
    list_filter = ['status', 'equipment_type', 'validation_status', 'document_class']
    search_fields = ['document_number', 'tag_number', 'title', 'service_description']
    readonly_fields = ['created_at', 'updated_at', 'validation_score']
    
    inlines = [DatasheetRevisionInline]
    
    fieldsets = (
        ('Document Information', {
            'fields': (
                'document_number',
                'contractor_document_number',
                'title',
                'document_class',
                'status',
                'revision'
            )
        }),
        ('Equipment Information', {
            'fields': (
                'equipment_type',
                'tag_number',
                'service_description',
                'location'
            )
        }),
        ('Project Information', {
            'fields': (
                'project_name',
                'project_number',
                'unit_number',
                'area'
            )
        }),
        ('Data', {
            'fields': ('data', 'calculated_values'),
            'classes': ('collapse',)
        }),
        ('Validation', {
            'fields': (
                'validation_status',
                'validation_results',
                'validation_score'
            ),
            'classes': ('collapse',)
        }),
        ('References', {
            'fields': (
                'pid_drawing_number',
                'line_number',
                'material_spec',
                'related_documents'
            ),
            'classes': ('collapse',)
        }),
        ('Workflow', {
            'fields': (
                'prepared_by',
                'date_prepared',
                'checked_by',
                'date_checked',
                'approved_by',
                'date_approved'
            )
        }),
        ('Holds & Comments', {
            'fields': ('holds', 'comments'),
            'classes': ('collapse',)
        }),
        ('Files', {
            'fields': ('source_files', 'generated_pdf'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(DatasheetTemplate)
class DatasheetTemplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'equipment_type', 'usage_count', 'is_global', 'created_by', 'created_at']
    list_filter = ['equipment_type', 'is_global']
    search_fields = ['name', 'description']
    readonly_fields = ['usage_count', 'created_at', 'updated_at']


@admin.register(DatasheetValidationRule)
class DatasheetValidationRuleAdmin(admin.ModelAdmin):
    list_display = ['rule_id', 'name', 'equipment_type', 'severity', 'is_active']
    list_filter = ['equipment_type', 'severity', 'is_active']
    search_fields = ['rule_id', 'name', 'description']


@admin.register(DatasheetExtractionJob)
class DatasheetExtractionJobAdmin(admin.ModelAdmin):
    list_display = ['id', 'job_type', 'status', 'progress', 'created_by', 'created_at']
    list_filter = ['status', 'job_type']
    readonly_fields = ['started_at', 'completed_at', 'created_at']
