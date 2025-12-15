from django.contrib import admin
from .models import PFDDocument, PIDConversion, ConversionFeedback


@admin.register(PFDDocument)
class PFDDocumentAdmin(admin.ModelAdmin):
    list_display = ['document_number', 'document_title', 'status', 'uploaded_by', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['document_number', 'document_title', 'project_name']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(PIDConversion)
class PIDConversionAdmin(admin.ModelAdmin):
    list_display = ['pid_drawing_number', 'pid_title', 'status', 'confidence_score', 'converted_by', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['pid_drawing_number', 'pid_title']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(ConversionFeedback)
class ConversionFeedbackAdmin(admin.ModelAdmin):
    list_display = ['conversion', 'user', 'rating', 'created_at']
    list_filter = ['rating', 'created_at']
    readonly_fields = ['created_at', 'updated_at']
