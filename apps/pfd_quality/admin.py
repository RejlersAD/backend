from django.contrib import admin
from .models import PFDQProject, PFDQDocument, PFDQDrawing, PFDQFinding


@admin.register(PFDQProject)
class PFDQProjectAdmin(admin.ModelAdmin):
    list_display  = ('project_name', 'created_by', 'created_at')
    search_fields = ('project_name',)
    readonly_fields = ('project_id', 'created_at', 'updated_at')


@admin.register(PFDQDocument)
class PFDQDocumentAdmin(admin.ModelAdmin):
    list_display  = ('file_name', 'status', 'uploaded_by', 'created_at')
    list_filter   = ('status',)
    search_fields = ('file_name',)
    readonly_fields = ('document_id', 'file_hash', 'created_at', 'updated_at')


@admin.register(PFDQDrawing)
class PFDQDrawingAdmin(admin.ModelAdmin):
    list_display  = ('drawing_id', 'title', 'document', 'page_index')
    readonly_fields = ('created_at',)


@admin.register(PFDQFinding)
class PFDQFindingAdmin(admin.ModelAdmin):
    list_display  = ('sl_no', 'category', 'rule_id', 'severity', 'status', 'drawing')
    list_filter   = ('severity', 'status', 'category')
    search_fields = ('rule_id', 'issue_observed')
    readonly_fields = ('created_at',)
