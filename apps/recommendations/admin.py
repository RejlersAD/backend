from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.safestring import mark_safe
import json

from .models import (
    DocumentEmbedding, RecommendationHistory, UserRecommendationPreferences,
    DocumentUploadPattern, AIModelUsageTracking, DocumentSimilarityCache
)

@admin.register(DocumentEmbedding)
class DocumentEmbeddingAdmin(admin.ModelAdmin):
    list_display = [
        'document_id', 'filename', 'document_type', 'user', 
        'project_code', 'file_size_mb', 'has_embedding', 'created_at'
    ]
    list_filter = ['document_type', 'created_at', 'user', 'project_code']
    search_fields = ['filename', 'document_id', 'user__username', 'project_code']
    readonly_fields = ['document_id', 'content_hash', 'created_at', 'updated_at']
    
    fieldsets = (
        ('Document Info', {
            'fields': ('document_id', 's3_key', 'filename', 'document_type')
        }),
        ('User & Project', {
            'fields': ('user', 'project_code')
        }),
        ('Content Analysis', {
            'fields': ('content_hash', 'file_size', 'ai_metadata_display')
        }),
        ('AI Processing', {
            'fields': ('has_embedding', 'embedding_info'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    
    def file_size_mb(self, obj):
        return f"{obj.file_size / (1024*1024):.2f} MB"
    file_size_mb.short_description = 'File Size'
    
    def has_embedding(self, obj):
        if obj.semantic_embedding:
            return format_html('<span style="color: green;">✓ Yes</span>')
        return format_html('<span style="color: red;">✗ No</span>')
    has_embedding.short_description = 'Has Embedding'
    has_embedding.admin_order_field = 'semantic_embedding'
    
    def ai_metadata_display(self, obj):
        if obj.ai_metadata:
            formatted_json = json.dumps(obj.ai_metadata, indent=2)
            return format_html('<pre style="font-size: 12px;">{}</pre>', formatted_json)
        return "No AI metadata"
    ai_metadata_display.short_description = 'AI Metadata'
    
    def embedding_info(self, obj):
        if obj.semantic_embedding:
            embedding_length = len(obj.semantic_embedding) if isinstance(obj.semantic_embedding, list) else 0
            return f"Embedding dimensions: {embedding_length}"
        return "No embedding generated"
    embedding_info.short_description = 'Embedding Info'

@admin.register(RecommendationHistory)
class RecommendationHistoryAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'user', 'recommendation_type', 'confidence_score_percent',
        'has_feedback', 'feedback_score', 'created_at'
    ]
    list_filter = [
        'recommendation_type', 'user_action', 'created_at', 
        'user_feedback_score', 'confidence_score'
    ]
    search_fields = ['user__username', 'source_document_id', 'ai_reasoning']
    readonly_fields = ['created_at', 'responded_at']
    
    fieldsets = (
        ('Basic Info', {
            'fields': ('user', 'source_document_id', 'recommendation_type', 'confidence_score')
        }),
        ('AI Analysis', {
            'fields': ('ai_reasoning_display', 'recommended_documents_display', 'suggested_actions_display'),
            'classes': ('collapse',)
        }),
        ('User Feedback', {
            'fields': ('user_action', 'user_feedback_score', 'user_feedback_text')
        }),
        ('Metadata', {
            'fields': ('metadata_display',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'responded_at')
        })
    )
    
    def confidence_score_percent(self, obj):
        percentage = obj.confidence_score * 100
        if percentage >= 90:
            color = 'green'
        elif percentage >= 70:
            color = 'orange'
        else:
            color = 'red'
        return format_html(
            '<span style="color: {};">{:.1f}%</span>', 
            color, percentage
        )
    confidence_score_percent.short_description = 'Confidence'
    confidence_score_percent.admin_order_field = 'confidence_score'
    
    def has_feedback(self, obj):
        if obj.user_action:
            return format_html('<span style="color: green;">✓</span>')
        return format_html('<span style="color: gray;">—</span>')
    has_feedback.short_description = 'Feedback'
    has_feedback.admin_order_field = 'user_action'
    
    def ai_reasoning_display(self, obj):
        return format_html('<div style="max-width: 500px; word-wrap: break-word;">{}</div>', obj.ai_reasoning)
    ai_reasoning_display.short_description = 'AI Reasoning'
    
    def recommended_documents_display(self, obj):
        if obj.recommended_documents:
            count = len(obj.recommended_documents)
            return format_html('{} documents recommended', count)
        return "No documents recommended"
    recommended_documents_display.short_description = 'Recommended Documents'
    
    def suggested_actions_display(self, obj):
        if obj.suggested_actions:
            actions_html = '<ul style="margin: 0; padding-left: 20px;">'
            for action in obj.suggested_actions[:3]:  # Show first 3 actions
                actions_html += f'<li>{action}</li>'
            if len(obj.suggested_actions) > 3:
                actions_html += f'<li><em>... and {len(obj.suggested_actions) - 3} more</em></li>'
            actions_html += '</ul>'
            return format_html(actions_html)
        return "No actions suggested"
    suggested_actions_display.short_description = 'Suggested Actions'
    
    def metadata_display(self, obj):
        if obj.metadata:
            formatted_json = json.dumps(obj.metadata, indent=2)
            return format_html('<pre style="font-size: 11px; max-height: 200px; overflow-y: auto;">{}</pre>', formatted_json)
        return "No metadata"
    metadata_display.short_description = 'Metadata'

@admin.register(UserRecommendationPreferences)
class UserRecommendationPreferencesAdmin(admin.ModelAdmin):
    list_display = [
        'user', 'enable_duplicate_alerts', 'enable_similarity_suggestions',
        'enable_quality_checks', 'similarity_threshold', 'use_advanced_ai', 'updated_at'
    ]
    list_filter = [
        'enable_duplicate_alerts', 'enable_similarity_suggestions', 
        'enable_quality_checks', 'use_advanced_ai', 'email_notifications'
    ]
    search_fields = ['user__username', 'user__email']
    
    fieldsets = (
        ('User', {
            'fields': ('user',)
        }),
        ('Notification Preferences', {
            'fields': (
                'enable_duplicate_alerts', 'enable_similarity_suggestions',
                'enable_quality_checks', 'enable_auto_completion'
            )
        }),
        ('Thresholds', {
            'fields': ('similarity_threshold', 'duplicate_threshold')
        }),
        ('AI Settings', {
            'fields': ('use_advanced_ai', 'preferred_document_types_display')
        }),
        ('Communication', {
            'fields': ('email_notifications', 'in_app_notifications')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    
    readonly_fields = ['created_at', 'updated_at', 'preferred_document_types_display']
    
    def preferred_document_types_display(self, obj):
        if obj.preferred_document_types:
            return ', '.join(obj.preferred_document_types)
        return "All document types"
    preferred_document_types_display.short_description = 'Preferred Document Types'

@admin.register(DocumentUploadPattern)
class DocumentUploadPatternAdmin(admin.ModelAdmin):
    list_display = [
        'user', 'document_type', 'upload_count', 'last_upload',
        'avg_frequency_days', 'updated_at'
    ]
    list_filter = ['document_type', 'last_upload', 'upload_count']
    search_fields = ['user__username', 'document_type']
    readonly_fields = ['created_at', 'updated_at']
    
    def avg_frequency_days(self, obj):
        if obj.average_upload_frequency:
            return f"{obj.average_upload_frequency:.1f} days"
        return "N/A"
    avg_frequency_days.short_description = 'Avg. Frequency'
    avg_frequency_days.admin_order_field = 'average_upload_frequency'

@admin.register(AIModelUsageTracking)
class AIModelUsageTrackingAdmin(admin.ModelAdmin):
    list_display = [
        'model_type', 'user', 'tokens_used', 'processing_time_ms',
        'estimated_cost_display', 'success', 'created_at'
    ]
    list_filter = ['model_type', 'success', 'created_at', 'request_type']
    search_fields = ['user__username', 'document_id', 'error_message']
    readonly_fields = ['created_at']
    date_hierarchy = 'created_at'
    
    def estimated_cost_display(self, obj):
        if obj.estimated_cost > 0:
            return f"${obj.estimated_cost:.4f}"
        return "Free"
    estimated_cost_display.short_description = 'Est. Cost'
    estimated_cost_display.admin_order_field = 'estimated_cost'
    
    # Add some aggregate info
    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(request, extra_context=extra_context)
        
        try:
            qs = response.context_data['cl'].queryset
            total_cost = sum(usage.estimated_cost for usage in qs)
            total_tokens = sum(usage.tokens_used for usage in qs)
            
            response.context_data['summary'] = {
                'total_cost': total_cost,
                'total_tokens': total_tokens,
            }
        except (AttributeError, KeyError):
            pass
            
        return response

@admin.register(DocumentSimilarityCache)
class DocumentSimilarityCacheAdmin(admin.ModelAdmin):
    list_display = [
        'document1_id', 'document2_id', 'similarity_score_percent',
        'calculation_method', 'is_expired_display', 'created_at'
    ]
    list_filter = ['calculation_method', 'created_at', 'expires_at']
    search_fields = ['document1_id', 'document2_id']
    readonly_fields = ['created_at']
    
    def similarity_score_percent(self, obj):
        percentage = obj.similarity_score * 100
        if percentage >= 95:
            color = 'red'  # Very high similarity (potential duplicate)
        elif percentage >= 80:
            color = 'orange'  # High similarity
        else:
            color = 'green'  # Normal similarity
        return format_html(
            '<span style="color: {};">{:.1f}%</span>',
            color, percentage
        )
    similarity_score_percent.short_description = 'Similarity'
    similarity_score_percent.admin_order_field = 'similarity_score'
    
    def is_expired_display(self, obj):
        from django.utils import timezone
        if obj.expires_at < timezone.now():
            return format_html('<span style="color: red;">Expired</span>')
        return format_html('<span style="color: green;">Valid</span>')
    is_expired_display.short_description = 'Status'

# Custom admin actions
@admin.action(description='Regenerate embeddings for selected documents')
def regenerate_embeddings(modeladmin, request, queryset):
    # This would trigger embedding regeneration
    updated = 0
    for embedding in queryset:
        # Mark for regeneration by clearing existing embedding
        embedding.semantic_embedding = None
        embedding.save()
        updated += 1
    
    modeladmin.message_user(
        request, 
        f'{updated} documents marked for embedding regeneration.'
    )

@admin.action(description='Clear user feedback for selected recommendations')
def clear_feedback(modeladmin, request, queryset):
    updated = queryset.update(
        user_action=None,
        user_feedback_score=None, 
        user_feedback_text=None,
        responded_at=None
    )
    modeladmin.message_user(
        request,
        f'Cleared feedback for {updated} recommendations.'
    )

# Register actions
DocumentEmbeddingAdmin.actions = [regenerate_embeddings]
RecommendationHistoryAdmin.actions = [clear_feedback]

# Custom admin site configuration
admin.site.site_header = "RADAI Recommendation System Admin"
admin.site.site_title = "RADAI Admin"
admin.site.index_title = "Welcome to RADAI Recommendation System Administration"