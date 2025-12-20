"""
CRS Documents Admin
"""

from django.contrib import admin
from .models import CRSDocument, CRSDocumentVersion


@admin.register(CRSDocument)
class CRSDocumentAdmin(admin.ModelAdmin):
    list_display = ['document_number', 'title', 'department', 'status', 'version', 'created_at']
    list_filter = ['status', 'department', 'created_at']
    search_fields = ['document_number', 'title', 'description']
    readonly_fields = ['created_at', 'updated_at', 'approved_at']


@admin.register(CRSDocumentVersion)
class CRSDocumentVersionAdmin(admin.ModelAdmin):
    list_display = ['document', 'version_number', 'created_by', 'created_at']
    list_filter = ['created_at']
    search_fields = ['document__document_number', 'version_number']
