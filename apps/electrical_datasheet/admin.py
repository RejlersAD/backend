from django.contrib import admin
from .models import ElectricalEquipmentType, ElectricalDatasheet, DatasheetRevisionHistory, DatasheetComment


@admin.register(ElectricalEquipmentType)
class ElectricalEquipmentTypeAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'code', 'category', 'is_active', 'created_at']
    list_filter = ['is_active', 'category']
    search_fields = ['name', 'code', 'description']
    ordering = ['name']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(ElectricalDatasheet)
class ElectricalDatasheetAdmin(admin.ModelAdmin):
    list_display = [
        'tag_number', 'equipment_type', 'status', 'revision_number',
        'project_number', 'created_by', 'created_at'
    ]
    list_filter = ['status', 'equipment_type', 'discipline', 'created_at']
    search_fields = ['tag_number', 'service_description', 'location', 'project_name', 'project_number']
    ordering = ['-created_at']
    readonly_fields = [
        'created_by', 'updated_by', 'reviewed_by', 'approved_by',
        'created_at', 'updated_at', 'reviewed_at', 'approved_at',
        'deleted_by', 'deleted_at'
    ]
    fieldsets = (
        ('Basic Information', {
            'fields': ('equipment_type', 'tag_number', 'service_description', 'location')
        }),
        ('Form Data', {
            'fields': ('form_data',)
        }),
        ('Status & Workflow', {
            'fields': ('status', 'revision_number', 'revision_notes')
        }),
        ('Project Information', {
            'fields': ('project_name', 'project_number', 'discipline')
        }),
        ('Attachments', {
            'fields': ('attachments',)
        }),
        ('User Tracking', {
            'fields': (
                'created_by', 'created_at', 'updated_by', 'updated_at',
                'reviewed_by', 'reviewed_at', 'approved_by', 'approved_at'
            )
        }),
        ('Deletion', {
            'fields': ('is_deleted', 'deleted_by', 'deleted_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(DatasheetRevisionHistory)
class DatasheetRevisionHistoryAdmin(admin.ModelAdmin):
    list_display = ['datasheet', 'revision_number', 'status', 'revised_by', 'revised_at']
    list_filter = ['status', 'revised_at']
    search_fields = ['datasheet__tag_number', 'revision_notes']
    ordering = ['-revised_at']
    readonly_fields = ['revised_by', 'revised_at']


@admin.register(DatasheetComment)
class DatasheetCommentAdmin(admin.ModelAdmin):
    list_display = ['datasheet', 'commented_by', 'field_id', 'is_resolved', 'commented_at']
    list_filter = ['is_resolved', 'commented_at']
    search_fields = ['datasheet__tag_number', 'comment_text']
    ordering = ['-commented_at']
    readonly_fields = ['commented_by', 'commented_at']

