from django.contrib import admin
from .models import PIDVDocument, PIDVDrawing, PIDVFinding


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
