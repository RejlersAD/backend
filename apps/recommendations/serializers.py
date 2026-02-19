from rest_framework import serializers
from .models import (
    DocumentEmbedding, RecommendationHistory, 
    UserRecommendationPreferences, DocumentUploadPattern,
    AIModelUsageTracking, DocumentSimilarityCache
)
from .ai_recommendation_engine import DocumentMetadata, RecommendationResult

class DocumentEmbeddingSerializer(serializers.ModelSerializer):
    user_username = serializers.CharField(source='user.username', read_only=True)
    
    class Meta:
        model = DocumentEmbedding
        fields = [
            'id', 'document_id', 's3_key', 'filename', 'document_type',
            'user', 'user_username', 'project_code', 'content_hash',
            'file_size', 'ai_metadata', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'user_username']

class DocumentMetadataSerializer(serializers.Serializer):
    """Serializer for DocumentMetadata dataclass"""
    document_id = serializers.CharField()
    s3_key = serializers.CharField()
    filename = serializers.CharField()
    document_type = serializers.CharField()
    file_size = serializers.IntegerField()
    upload_date = serializers.DateTimeField()
    user_id = serializers.IntegerField()
    project_code = serializers.CharField(allow_null=True)
    content_hash = serializers.CharField(allow_null=True)
    semantic_embedding = serializers.ListField(
        child=serializers.FloatField(),
        allow_null=True,
        required=False
    )
    ai_extracted_metadata = serializers.DictField(allow_null=True, required=False)
    similarity_score = serializers.FloatField(allow_null=True, required=False)

class RecommendationResultSerializer(serializers.Serializer):
    """Serializer for RecommendationResult dataclass"""
    recommendation_type = serializers.ChoiceField(choices=[
        'duplicate', 'similar', 'related', 'quality_check', 'auto_complete'
    ])
    confidence_score = serializers.FloatField(min_value=0.0, max_value=1.0)
    recommended_documents = DocumentMetadataSerializer(many=True)
    ai_reasoning = serializers.CharField()
    suggested_actions = serializers.ListField(child=serializers.CharField())
    metadata = serializers.DictField()

class RecommendationHistorySerializer(serializers.ModelSerializer):
    user_username = serializers.CharField(source='user.username', read_only=True)
    recommendation_type_display = serializers.CharField(
        source='get_recommendation_type_display', 
        read_only=True
    )
    user_action_display = serializers.CharField(
        source='get_user_action_display',
        read_only=True
    )
    
    # Format timestamps nicely
    created_at_formatted = serializers.SerializerMethodField()
    responded_at_formatted = serializers.SerializerMethodField()
    
    class Meta:
        model = RecommendationHistory
        fields = [
            'id', 'user', 'user_username', 'source_document_id',
            'recommendation_type', 'recommendation_type_display',
            'confidence_score', 'ai_reasoning', 'recommended_documents',
            'suggested_actions', 'metadata', 'user_action', 'user_action_display',
            'user_feedback_score', 'user_feedback_text', 'created_at', 
            'created_at_formatted', 'responded_at', 'responded_at_formatted'
        ]
        read_only_fields = [
            'id', 'user_username', 'recommendation_type_display',
            'user_action_display', 'created_at_formatted', 'responded_at_formatted'
        ]
    
    def get_created_at_formatted(self, obj):
        return obj.created_at.strftime('%Y-%m-%d %H:%M:%S UTC') if obj.created_at else None
    
    def get_responded_at_formatted(self, obj):
        return obj.responded_at.strftime('%Y-%m-%d %H:%M:%S UTC') if obj.responded_at else None

class UserRecommendationPreferencesSerializer(serializers.ModelSerializer):
    user_username = serializers.CharField(source='user.username', read_only=True)
    
    class Meta:
        model = UserRecommendationPreferences
        fields = [
            'id', 'user', 'user_username', 'enable_duplicate_alerts',
            'enable_similarity_suggestions', 'enable_quality_checks',
            'enable_auto_completion', 'similarity_threshold',
            'duplicate_threshold', 'preferred_document_types',
            'use_advanced_ai', 'email_notifications', 'in_app_notifications',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'user', 'user_username', 'created_at', 'updated_at']
    
    def validate_similarity_threshold(self, value):
        """Ensure similarity threshold is reasonable"""
        if not 0.5 <= value <= 0.99:
            raise serializers.ValidationError(
                "Similarity threshold must be between 0.5 and 0.99"
            )
        return value
    
    def validate_duplicate_threshold(self, value):
        """Ensure duplicate threshold is reasonable"""
        if not 0.8 <= value <= 1.0:
            raise serializers.ValidationError(
                "Duplicate threshold must be between 0.8 and 1.0"
            )
        return value
    
    def validate(self, attrs):
        """Cross-field validation"""
        similarity_threshold = attrs.get('similarity_threshold', self.instance.similarity_threshold if self.instance else 0.8)
        duplicate_threshold = attrs.get('duplicate_threshold', self.instance.duplicate_threshold if self.instance else 0.95)
        
        if similarity_threshold >= duplicate_threshold:
            raise serializers.ValidationError(
                "Similarity threshold must be lower than duplicate threshold"
            )
        
        return attrs

class DocumentUploadPatternSerializer(serializers.ModelSerializer):
    user_username = serializers.CharField(source='user.username', read_only=True)
    last_upload_formatted = serializers.SerializerMethodField()
    
    class Meta:
        model = DocumentUploadPattern
        fields = [
            'id', 'user', 'user_username', 'document_type', 'upload_count',
            'common_upload_sequences', 'project_associations', 'last_upload',
            'last_upload_formatted', 'average_upload_frequency', 'created_at',
            'updated_at'
        ]
        read_only_fields = [
            'id', 'user_username', 'last_upload_formatted', 'created_at', 'updated_at'
        ]
    
    def get_last_upload_formatted(self, obj):
        return obj.last_upload.strftime('%Y-%m-%d %H:%M:%S UTC') if obj.last_upload else None

class AIModelUsageTrackingSerializer(serializers.ModelSerializer):
    user_username = serializers.CharField(source='user.username', read_only=True)
    model_type_display = serializers.CharField(source='get_model_type_display', read_only=True)
    created_at_formatted = serializers.SerializerMethodField()
    
    class Meta:
        model = AIModelUsageTracking
        fields = [
            'id', 'model_type', 'model_type_display', 'user', 'user_username',
            'document_id', 'tokens_used', 'processing_time_ms', 'estimated_cost',
            'request_type', 'success', 'error_message', 'created_at',
            'created_at_formatted'
        ]
        read_only_fields = [
            'id', 'model_type_display', 'user_username', 'created_at_formatted'
        ]
    
    def get_created_at_formatted(self, obj):
        return obj.created_at.strftime('%Y-%m-%d %H:%M:%S UTC') if obj.created_at else None

class DocumentSimilarityCacheSerializer(serializers.ModelSerializer):
    expires_at_formatted = serializers.SerializerMethodField()
    is_expired = serializers.SerializerMethodField()
    
    class Meta:
        model = DocumentSimilarityCache
        fields = [
            'id', 'document1_id', 'document2_id', 'similarity_score',
            'calculation_method', 'created_at', 'expires_at',
            'expires_at_formatted', 'is_expired'
        ]
        read_only_fields = [
            'id', 'expires_at_formatted', 'is_expired'
        ]
    
    def get_expires_at_formatted(self, obj):
        return obj.expires_at.strftime('%Y-%m-%d %H:%M:%S UTC') if obj.expires_at else None
    
    def get_is_expired(self, obj):
        from django.utils import timezone
        return obj.expires_at < timezone.now() if obj.expires_at else True

class UploadAnalysisRequestSerializer(serializers.Serializer):
    """Serializer for document upload analysis requests"""
    document_type = serializers.ChoiceField(
        choices=[
            'pid_drawing', 'pfd_document', 'pump_datasheet', 
            'valve_specification', 'instrument_specification',
            'equipment_datasheet', 'technical_specification',
            'installation_drawing', 'process_diagram', 'other'
        ],
        required=True
    )
    project_code = serializers.CharField(max_length=50, required=False, allow_blank=True)
    enable_advanced_analysis = serializers.BooleanField(default=True)
    similarity_threshold = serializers.FloatField(
        min_value=0.5, 
        max_value=0.99, 
        default=0.8,
        required=False
    )
    duplicate_threshold = serializers.FloatField(
        min_value=0.8,
        max_value=1.0,
        default=0.95,
        required=False
    )

class BatchAnalysisRequestSerializer(serializers.Serializer):
    """Serializer for batch document analysis requests"""
    project_code = serializers.CharField(max_length=50, required=False, allow_blank=True)
    default_document_type = serializers.CharField(max_length=100, default='unknown')
    enable_advanced_analysis = serializers.BooleanField(default=True)
    
    # File type mapping for batch uploads
    file_type_mappings = serializers.DictField(
        child=serializers.CharField(),
        required=False,
        help_text="Map filenames or patterns to document types"
    )

class RecommendationFeedbackSerializer(serializers.Serializer):
    """Serializer for recommendation feedback"""
    recommendation_id = serializers.IntegerField(required=True)
    action = serializers.ChoiceField(
        choices=[
            'viewed', 'accepted', 'dismissed', 'followed_link', 'implemented_suggestion'
        ],
        required=True
    )
    feedback_score = serializers.IntegerField(
        min_value=1,
        max_value=5,
        required=False,
        help_text="1-5 star rating"
    )
    feedback_text = serializers.CharField(
        max_length=1000,
        required=False,
        allow_blank=True,
        help_text="Optional detailed feedback"
    )

class SimilarDocumentsRequestSerializer(serializers.Serializer):
    """Serializer for similar documents search requests"""
    document_id = serializers.CharField(required=True)
    limit = serializers.IntegerField(min_value=1, max_value=50, default=10)
    min_similarity = serializers.FloatField(
        min_value=0.1,
        max_value=0.99,
        default=0.7
    )
    document_types = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text="Filter by specific document types"
    )
    project_codes = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text="Filter by specific project codes"
    )

class RecommendationStatisticsSerializer(serializers.Serializer):
    """Serializer for recommendation statistics"""
    user_statistics = serializers.DictField(read_only=True)
    system_statistics = serializers.DictField(read_only=True)
    ai_statistics = serializers.DictField(read_only=True)
    generated_at = serializers.DateTimeField(read_only=True)
    
class QuickRecommendationSerializer(serializers.Serializer):
    """Serializer for quick recommendation responses"""
    message = serializers.CharField()
    recommendation_type = serializers.CharField()
    confidence = serializers.FloatField()
    actions = serializers.ListField(child=serializers.CharField())
    related_documents_count = serializers.IntegerField(default=0)