"""Spec Customization — Django admin."""
from django.contrib import admin

from .models import (
    PaperSpecDocument,
    PaperSpecExtractionJob,
    PipingClass,
    PipingClassComponent,
)
from .project_models import SpecProject
from .matching_models import (
    MatchingWorkbookSet,
    MatchingRule,
    ComponentMatchingResult,
)


@admin.register(PaperSpecDocument)
class PaperSpecDocumentAdmin(admin.ModelAdmin):
    list_display = ('original_filename', 'total_pages', 'file_size_bytes', 'uploaded_by', 'created_at')
    search_fields = ('original_filename', 'sha256_hash', 'title', 'document_number')
    readonly_fields = ('sha256_hash', 'created_at', 'updated_at')


@admin.register(PaperSpecExtractionJob)
class PaperSpecExtractionJobAdmin(admin.ModelAdmin):
    list_display = ('id', 'document', 'status', 'progress_percent', 'pages_processed', 'created_at')
    list_filter = ('status',)
    search_fields = ('document__original_filename', 'celery_task_id')
    readonly_fields = ('created_at', 'started_at', 'completed_at', 'config_snapshot')


class PipingClassComponentInline(admin.TabularInline):
    model = PipingClassComponent
    extra = 0


@admin.register(PipingClass)
class PipingClassAdmin(admin.ModelAdmin):
    list_display = ('class_code', 'job', 'material_grade', 'pressure_rating', 'confidence_score', 'extraction_engine')
    list_filter = ('extraction_engine',)
    search_fields = ('class_code', 'class_full_code', 'material_grade')
    inlines = [PipingClassComponentInline]


@admin.register(PipingClassComponent)
class PipingClassComponentAdmin(admin.ModelAdmin):
    list_display = ('piping_class', 'component_type', 'sub_type', 'size_from', 'size_to', 'material_standard')
    list_filter = ('component_type',)
    search_fields = ('description', 'material_standard', 'sub_type')


# ─────────────────────────────────────────────────────────────────────────────
# Matching Workbook Admin
# ─────────────────────────────────────────────────────────────────────────────
def _reference_locked(ws):
    return bool(ws and (ws.frozen_at or ws.pinned_jobs.exists()))


class MatchingRuleInline(admin.TabularInline):
    model = MatchingRule
    extra = 0
    fields = ('pdf_component_name', 'catalog_component_name', 'cat_sheet_name', 'row_number')
    readonly_fields = ('row_number', 'created_at')

    def has_add_permission(self, request, obj=None):
        return not _reference_locked(obj) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return not _reference_locked(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return not _reference_locked(obj) and super().has_delete_permission(request, obj)


@admin.register(MatchingWorkbookSet)
class MatchingWorkbookSetAdmin(admin.ModelAdmin):
    list_display = ('project', 'version_label', 'is_active', 'is_parsed', 'rules_count', 'uploaded_by', 'created_at')
    list_filter = ('is_active', 'is_parsed', 'created_at')
    search_fields = ('project__name', 'version_label', 'match_file_name', 'spec_file_name', 'cat_file_name')
    readonly_fields = ('created_at', 'updated_at', 'rules_count', 'spec_sheets_count', 'cat_sheets_count')
    inlines = [MatchingRuleInline]

    def has_change_permission(self, request, obj=None):
        return not _reference_locked(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return not _reference_locked(obj) and super().has_delete_permission(request, obj)

    def delete_queryset(self, request, queryset):
        from django.core.exceptions import PermissionDenied
        if any(_reference_locked(ws) for ws in queryset):
            raise PermissionDenied('Frozen or pinned reference sets cannot be deleted.')
        return super().delete_queryset(request, queryset)
    
    fieldsets = (
        ('Project', {
            'fields': ('project', 'version_label', 'is_active')
        }),
        ('Workbook Files', {
            'fields': ('match_file', 'match_file_name', 'spec_file', 'spec_file_name', 'cat_file', 'cat_file_name')
        }),
        ('Parsing Status', {
            'fields': ('is_parsed', 'parse_error', 'rules_count', 'spec_sheets_count', 'cat_sheets_count')
        }),
        ('Audit', {
            'fields': ('uploaded_by', 'created_at', 'updated_at')
        }),
    )


@admin.register(MatchingRule)
class MatchingRuleAdmin(admin.ModelAdmin):
    list_display = ('pdf_component_name', 'catalog_component_name', 'cat_sheet_name', 'workbook_set', 'row_number')
    list_filter = ('workbook_set', 'created_at')
    search_fields = ('pdf_component_name', 'catalog_component_name', 'cat_sheet_name')
    readonly_fields = ('created_at', 'updated_at')


    def has_change_permission(self, request, obj=None):
        return not (obj and _reference_locked(obj.workbook_set)) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return not (obj and _reference_locked(obj.workbook_set)) and super().has_delete_permission(request, obj)

    def delete_queryset(self, request, queryset):
        from django.core.exceptions import PermissionDenied
        if any(_reference_locked(rule.workbook_set) for rule in queryset.select_related('workbook_set')):
            raise PermissionDenied('Frozen or pinned reference rules cannot be deleted.')
        return super().delete_queryset(request, queryset)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'workbook_set':
            kwargs['queryset'] = MatchingWorkbookSet.objects.filter(frozen_at__isnull=True, pinned_jobs__isnull=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(ComponentMatchingResult)
class ComponentMatchingResultAdmin(admin.ModelAdmin):
    list_display = ('pdf_component_name', 'matched_commodity_code', 'match_score', 'match_method', 'workbook_set', 'created_at')
    list_filter = ('match_method', 'created_at')
    search_fields = ('pdf_component_name', 'matched_commodity_code', 'matched_description')
    readonly_fields = ('created_at', 'result_data')
