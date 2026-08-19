from django.contrib import admin
from .models import (
    PIDVDocument, PIDVDrawing, PIDVFinding, PIDVProject,
    PIDVLegendSheet, PIDVInstrumentSymbol, PIDVReferenceData, PIDVAICheckRun
)


class PIDVFindingInline(admin.TabularInline):
    model  = PIDVFinding
    extra  = 0
    fields = ('sl_no', 'category', 'rule_id', 'issue_observed', 'severity', 'status')
    readonly_fields = ('sl_no', 'category', 'rule_id', 'issue_observed', 'severity')


class PIDVDrawingInline(admin.TabularInline):
    model  = PIDVDrawing
    extra  = 0
    fields = ('drawing_id', 'title', 'page_index')
    readonly_fields = ('drawing_id', 'title', 'page_index')


@admin.register(PIDVDocument)
class PIDVDocumentAdmin(admin.ModelAdmin):
    list_display  = ('file_name', 'document_id', 'status', 'uploaded_by', 'created_at')
    list_filter   = ('status',)
    search_fields = ('file_name', 'document_id', 'file_hash')
    readonly_fields = ('document_id', 'file_hash', 'created_at', 'updated_at')
    inlines       = [PIDVDrawingInline]


@admin.register(PIDVDrawing)
class PIDVDrawingAdmin(admin.ModelAdmin):
    list_display  = ('drawing_id', 'document', 'page_index', 'title')
    search_fields = ('drawing_id',)
    inlines       = [PIDVFindingInline]


@admin.register(PIDVFinding)
class PIDVFindingAdmin(admin.ModelAdmin):
    list_display  = ('sl_no', 'drawing', 'category', 'rule_id', 'severity', 'status')
    list_filter   = ('severity', 'category', 'status')
    search_fields = ('issue_observed', 'rule_id', 'evidence')


@admin.register(PIDVReferenceData)
class PIDVReferenceDataAdmin(admin.ModelAdmin):
    list_display  = ('file_name', 'data_type', 'project', 'status', 'uploaded_by', 'created_at')
    list_filter   = ('data_type', 'status')
    search_fields = ('file_name', 'reference_id')
    readonly_fields = ('reference_id', 'created_at', 'updated_at')


@admin.register(PIDVAICheckRun)
class PIDVAICheckRunAdmin(admin.ModelAdmin):
    list_display  = ('run_id', 'project', 'status', 'analysis_mode', 'triggered_by', 'created_at', 'completed_at')
    list_filter   = ('status', 'analysis_mode')
    search_fields = ('run_id', 'project__project_name')
    readonly_fields = ('run_id', 'created_at', 'updated_at', 'completed_at')
    
    fieldsets = (
        ('Basic Info', {
            'fields': ('run_id', 'project', 'status', 'analysis_mode', 'triggered_by')
        }),
        ('Results', {
            'fields': ('extracted_data', 'check_results', 'summary_stats')
        }),
        ('Metadata', {
            'fields': ('processing_metadata', 'error_message')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'completed_at')
        }),
    )
