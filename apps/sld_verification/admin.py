from django.contrib import admin
from .models import SLDProject, SLDDocument, SLDDrawing, SLDFinding


class SLDFindingInline(admin.TabularInline):
    model  = SLDFinding
    extra  = 0
    fields = ('sl_no', 'category', 'rule_id', 'issue_observed', 'severity', 'status')
    readonly_fields = ('sl_no', 'category', 'rule_id', 'issue_observed', 'severity')


class SLDDrawingInline(admin.TabularInline):
    model  = SLDDrawing
    extra  = 0
    fields = ('drawing_id', 'title', 'page_index')
    readonly_fields = ('drawing_id', 'title', 'page_index')


@admin.register(SLDProject)
class SLDProjectAdmin(admin.ModelAdmin):
    list_display  = ('project_name', 'project_id', 'created_by', 'document_count', 'created_at')
    search_fields = ('project_name', 'project_id')
    readonly_fields = ('project_id', 'created_at', 'updated_at')


@admin.register(SLDDocument)
class SLDDocumentAdmin(admin.ModelAdmin):
    list_display  = ('file_name', 'document_id', 'status', 'uploaded_by', 'uploaded_at')
    list_filter   = ('status',)
    search_fields = ('file_name', 'document_id', 'file_hash')
    readonly_fields = ('document_id', 'file_hash', 'uploaded_at', 'processed_at')
    inlines       = [SLDDrawingInline]


@admin.register(SLDDrawing)
class SLDDrawingAdmin(admin.ModelAdmin):
    list_display  = ('drawing_id', 'document', 'page_index', 'title')
    search_fields = ('drawing_id',)
    inlines       = [SLDFindingInline]


@admin.register(SLDFinding)
class SLDFindingAdmin(admin.ModelAdmin):
    list_display  = ('sl_no', 'drawing', 'category', 'rule_id', 'severity', 'status')
    list_filter   = ('severity', 'category', 'status')
    search_fields = ('issue_observed', 'rule_id', 'evidence')
