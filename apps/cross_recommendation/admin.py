from django.contrib import admin

from .models import CrossRecommendationLink


@admin.register(CrossRecommendationLink)
class CrossRecommendationLinkAdmin(admin.ModelAdmin):
    list_display = (
        'source_type', 'source_document_id', 'target_type',
        'target_document_id', 'decision', 'score', 'updated_at',
    )
    list_filter = ('source_type', 'target_type', 'decision')
    search_fields = ('source_document_id', 'target_document_id', 'reason')
    readonly_fields = ('link_id', 'created_at', 'updated_at')
