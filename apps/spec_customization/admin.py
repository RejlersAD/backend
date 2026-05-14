"""Spec Customization — Django admin."""
from django.contrib import admin

from .models import (
    PaperSpecDocument,
    PaperSpecExtractionJob,
    PipingClass,
    PipingClassComponent,
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
