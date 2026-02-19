from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone
import json

class DocumentEmbedding(models.Model):
    """Store document embeddings for similarity calculations"""
    document_id = models.CharField(max_length=255, unique=True, db_index=True)
    s3_key = models.CharField(max_length=1000)
    filename = models.CharField(max_length=255)
    document_type = models.CharField(max_length=100, db_index=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    project_code = models.CharField(max_length=50, null=True, blank=True, db_index=True)
    
    # Content analysis
    content_hash = models.CharField(max_length=64, db_index=True)  # SHA-256
    file_size = models.BigIntegerField()
    
    # AI-generated data
    semantic_embedding = models.JSONField(null=True, blank=True)  # Store as JSON array
    ai_metadata = models.JSONField(default=dict, blank=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'recommendations_document_embeddings'
        indexes = [
            models.Index(fields=['document_type', 'created_at']),
            models.Index(fields=['content_hash']),
            models.Index(fields=['user', 'document_type']),
            models.Index(fields=['project_code', 'document_type']),
        ]
    
    def __str__(self):
        return f"{self.document_type}: {self.filename}"

class RecommendationHistory(models.Model):
    """Track recommendations generated and user responses"""
    
    RECOMMENDATION_TYPES = [
        ('duplicate', 'Duplicate Detection'),
        ('similar', 'Similar Documents'),
        ('related', 'Related Content'),
        ('quality_check', 'Quality Assessment'),
        ('auto_complete', 'Auto Completion'),
    ]
    
    ACTION_TYPES = [
        ('viewed', 'Viewed Recommendation'),
        ('accepted', 'Accepted Suggestion'),
        ('dismissed', 'Dismissed Recommendation'),
        ('followed_link', 'Followed Document Link'),
        ('implemented_suggestion', 'Implemented Suggestion'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    source_document_id = models.CharField(max_length=255)
    recommendation_type = models.CharField(max_length=50, choices=RECOMMENDATION_TYPES)
    
    # Recommendation details
    confidence_score = models.FloatField(validators=[MinValueValidator(0.0), MaxValueValidator(1.0)])
    ai_reasoning = models.TextField()
    recommended_documents = models.JSONField(default=list)  # List of document IDs
    suggested_actions = models.JSONField(default=list)
    metadata = models.JSONField(default=dict)
    
    # User interaction
    user_action = models.CharField(max_length=50, choices=ACTION_TYPES, null=True, blank=True)
    user_feedback_score = models.IntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="User rating 1-5 stars"
    )
    user_feedback_text = models.TextField(null=True, blank=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'recommendations_history'
        indexes = [
            models.Index(fields=['user', 'created_at']),
            models.Index(fields=['recommendation_type', 'created_at']),
            models.Index(fields=['confidence_score']),
        ]
    
    def __str__(self):
        return f"{self.get_recommendation_type_display()} for {self.user.username}"

class UserRecommendationPreferences(models.Model):
    """Store user preferences for recommendations"""
    
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    
    # Notification preferences
    enable_duplicate_alerts = models.BooleanField(default=True)
    enable_similarity_suggestions = models.BooleanField(default=True)
    enable_quality_checks = models.BooleanField(default=True)
    enable_auto_completion = models.BooleanField(default=True)
    
    # Thresholds
    similarity_threshold = models.FloatField(
        default=0.8,
        validators=[MinValueValidator(0.5), MaxValueValidator(0.99)],
        help_text="Minimum similarity score for recommendations"
    )
    
    duplicate_threshold = models.FloatField(
        default=0.95,
        validators=[MinValueValidator(0.8), MaxValueValidator(1.0)],
        help_text="Minimum similarity score for duplicate detection"
    )
    
    # Document type preferences
    preferred_document_types = models.JSONField(
        default=list,
        help_text="List of document types user is most interested in"
    )
    
    # AI model preferences
    use_advanced_ai = models.BooleanField(
        default=True,
        help_text="Use OpenAI for advanced analysis (may incur costs)"
    )
    
    # Notification settings
    email_notifications = models.BooleanField(default=False)
    in_app_notifications = models.BooleanField(default=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'recommendations_user_preferences'
    
    def __str__(self):
        return f"Preferences for {self.user.username}"

class DocumentSimilarityCache(models.Model):
    """Cache similarity calculations for performance"""
    
    document1_id = models.CharField(max_length=255)
    document2_id = models.CharField(max_length=255)
    similarity_score = models.FloatField()
    calculation_method = models.CharField(max_length=50)  # 'embedding', 'content_hash', 'metadata'
    
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()  # Cache expiration
    
    class Meta:
        db_table = 'recommendations_similarity_cache'
        unique_together = ['document1_id', 'document2_id']
        indexes = [
            models.Index(fields=['similarity_score']),
            models.Index(fields=['expires_at']),
        ]
    
    def save(self, *args, **kwargs):
        # Set expiration to 30 days from now
        if not self.expires_at:
            self.expires_at = timezone.now() + timezone.timedelta(days=30)
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"Similarity: {self.similarity_score:.3f} between {self.document1_id} and {self.document2_id}"

class AIModelUsageTracking(models.Model):
    """Track AI model usage for cost management"""
    
    MODEL_TYPES = [
        ('embedding', 'Embedding Generation'),
        ('openai_gpt35', 'OpenAI GPT-3.5'),
        ('openai_gpt4', 'OpenAI GPT-4'),
        ('ocr', 'OCR Processing'),
        ('image_analysis', 'Image Analysis'),
    ]
    
    model_type = models.CharField(max_length=50, choices=MODEL_TYPES)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    document_id = models.CharField(max_length=255, null=True, blank=True)
    
    # Usage metrics
    tokens_used = models.IntegerField(default=0)
    processing_time_ms = models.IntegerField(default=0)
    estimated_cost = models.DecimalField(max_digits=10, decimal_places=6, default=0.0)
    
    # Request details
    request_type = models.CharField(max_length=100)  # 'similarity', 'duplicate_check', 'quality_assessment'
    success = models.BooleanField(default=True)
    error_message = models.TextField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'recommendations_ai_usage_tracking'
        indexes = [
            models.Index(fields=['model_type', 'created_at']),
            models.Index(fields=['user', 'created_at']),
        ]
    
    def __str__(self):
        return f"{self.get_model_type_display()} usage by {self.user.username}"

class DocumentUploadPattern(models.Model):
    """Track document upload patterns for better recommendations"""
    
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    document_type = models.CharField(max_length=100)
    upload_count = models.IntegerField(default=1)
    
    # Pattern analysis
    common_upload_sequences = models.JSONField(
        default=list,
        help_text="Common sequences of document types this user uploads"
    )
    
    project_associations = models.JSONField(
        default=dict,
        help_text="Projects this user commonly uploads this document type for"
    )
    
    # Timing patterns
    last_upload = models.DateTimeField()
    average_upload_frequency = models.FloatField(
        default=0.0,
        help_text="Average days between uploads of this document type"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'recommendations_upload_patterns'
        unique_together = ['user', 'document_type']
        indexes = [
            models.Index(fields=['document_type', 'upload_count']),
            models.Index(fields=['last_upload']),
        ]
    
    def __str__(self):
        return f"{self.user.username} - {self.document_type} ({self.upload_count} uploads)"