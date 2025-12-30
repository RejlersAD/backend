"""
CRS Multiple Revision System - AI-Powered Revision Management
Tracks document revisions, links comments across revisions, provides AI insights
"""

from django.db import models
from django.contrib.auth import get_user_model
from .models import CRSDocument, CRSComment

User = get_user_model()


class CRSRevisionChain(models.Model):
    """
    Represents a chain of document revisions for the same base document
    Groups all revisions together (Rev 1, Rev 2, Rev 3, etc.)
    """
    
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('on_hold', 'On Hold'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]
    
    RISK_LEVEL_CHOICES = [
        ('low', 'Low Risk'),
        ('medium', 'Medium Risk'),
        ('high', 'High Risk'),
        ('critical', 'Critical - Near Rejection'),
    ]
    
    chain_id = models.CharField(max_length=100, unique=True, help_text='Unique identifier for this revision chain')
    project_name = models.CharField(max_length=300)
    document_number = models.CharField(max_length=100)
    document_title = models.CharField(max_length=500)
    contractor_name = models.CharField(max_length=300, blank=True, null=True)
    department = models.CharField(max_length=100, blank=True, null=True)
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    risk_level = models.CharField(max_length=20, choices=RISK_LEVEL_CHOICES, default='low')
    
    total_revisions = models.IntegerField(default=0)
    current_revision_number = models.IntegerField(default=1)
    max_allowed_revisions = models.IntegerField(default=5, help_text='Maximum revisions before rejection')
    
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_revision_chains')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # AI Insights
    ai_risk_score = models.FloatField(default=0.0, help_text='AI-calculated risk score (0-100)')
    ai_recommendation = models.TextField(blank=True, null=True, help_text='AI-generated recommendation')
    ai_predicted_completion_date = models.DateField(null=True, blank=True)
    
    notes = models.TextField(blank=True, null=True)
    
    class Meta:
        db_table = 'crs_revision_chains'
        ordering = ['-created_at']
        verbose_name = 'CRS Revision Chain'
        verbose_name_plural = 'CRS Revision Chains'
        indexes = [
            models.Index(fields=['chain_id']),
            models.Index(fields=['project_name', 'document_number']),
        ]
    
    def __str__(self):
        return f"{self.chain_id} - {self.document_title} (Rev {self.current_revision_number}/{self.max_allowed_revisions})"
    
    @property
    def is_near_rejection(self):
        """Check if chain is approaching maximum revisions"""
        return self.current_revision_number >= (self.max_allowed_revisions - 1)
    
    @property
    def rejection_risk_percentage(self):
        """Calculate rejection risk based on revision count"""
        if self.max_allowed_revisions == 0:
            return 0
        return round((self.current_revision_number / self.max_allowed_revisions) * 100, 2)


class CRSRevision(models.Model):
    """
    Individual revision of a document within a revision chain
    """
    
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('under_review', 'Under Review'),
        ('comments_received', 'Comments Received'),
        ('responses_pending', 'Responses Pending'),
        ('completed', 'Completed'),
        ('superseded', 'Superseded'),
    ]
    
    chain = models.ForeignKey(CRSRevisionChain, on_delete=models.CASCADE, related_name='revisions')
    document = models.OneToOneField(CRSDocument, on_delete=models.CASCADE, related_name='revision_info')
    
    revision_number = models.IntegerField()
    revision_label = models.CharField(max_length=50, help_text='e.g., Rev 1, Rev 2, Rev 2A')
    
    parent_revision = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='child_revisions')
    
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='draft')
    
    submitted_date = models.DateTimeField(null=True, blank=True)
    received_date = models.DateTimeField(null=True, blank=True)
    completed_date = models.DateTimeField(null=True, blank=True)
    
    total_new_comments = models.IntegerField(default=0, help_text='New comments in this revision')
    total_carryover_comments = models.IntegerField(default=0, help_text='Comments from previous revisions')
    total_resolved_comments = models.IntegerField(default=0)
    
    # AI Insights for this revision
    ai_complexity_score = models.FloatField(default=0.0, help_text='AI-calculated complexity (0-100)')
    ai_estimated_resolution_time_hours = models.FloatField(default=0.0)
    ai_critical_issues_count = models.IntegerField(default=0)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    notes = models.TextField(blank=True, null=True)
    
    class Meta:
        db_table = 'crs_revisions'
        ordering = ['chain', 'revision_number']
        verbose_name = 'CRS Revision'
        verbose_name_plural = 'CRS Revisions'
        unique_together = [['chain', 'revision_number']]
        indexes = [
            models.Index(fields=['chain', 'revision_number']),
        ]
    
    def __str__(self):
        return f"{self.chain.chain_id} - {self.revision_label}"
    
    @property
    def total_comments(self):
        """Total comments in this revision"""
        return self.total_new_comments + self.total_carryover_comments


class CRSCommentLink(models.Model):
    """
    Links comments across revisions to track evolution
    Enables tracking of how comments change/persist across revisions
    """
    
    LINK_TYPE_CHOICES = [
        ('identical', 'Identical Comment'),
        ('modified', 'Modified Comment'),
        ('related', 'Related Comment'),
        ('resolved_reopened', 'Resolved but Reopened'),
    ]
    
    source_revision = models.ForeignKey(CRSRevision, on_delete=models.CASCADE, related_name='comment_links_from')
    target_revision = models.ForeignKey(CRSRevision, on_delete=models.CASCADE, related_name='comment_links_to')
    
    source_comment = models.ForeignKey(CRSComment, on_delete=models.CASCADE, related_name='links_as_source')
    target_comment = models.ForeignKey(CRSComment, on_delete=models.CASCADE, related_name='links_as_target')
    
    link_type = models.CharField(max_length=30, choices=LINK_TYPE_CHOICES)
    similarity_score = models.FloatField(default=0.0, help_text='AI-calculated similarity (0-100)')
    
    # AI Analysis
    ai_detected = models.BooleanField(default=False, help_text='Was this link detected by AI?')
    ai_confidence = models.FloatField(default=0.0, help_text='AI confidence level (0-100)')
    
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    notes = models.TextField(blank=True, null=True)
    
    class Meta:
        db_table = 'crs_comment_links'
        ordering = ['source_revision', 'target_revision']
        verbose_name = 'CRS Comment Link'
        verbose_name_plural = 'CRS Comment Links'
        unique_together = [['source_comment', 'target_comment']]
        indexes = [
            models.Index(fields=['source_revision', 'target_revision']),
        ]
    
    def __str__(self):
        return f"Link: {self.source_comment.serial_number} ({self.source_revision.revision_label}) → {self.target_comment.serial_number} ({self.target_revision.revision_label})"


class CRSAIInsight(models.Model):
    """
    AI-generated insights for revisions and comments
    Provides recommendations, predictions, and analysis
    """
    
    INSIGHT_TYPE_CHOICES = [
        ('risk_assessment', 'Risk Assessment'),
        ('response_suggestion', 'Response Suggestion'),
        ('pattern_detection', 'Pattern Detection'),
        ('resolution_prediction', 'Resolution Prediction'),
        ('priority_recommendation', 'Priority Recommendation'),
        ('escalation_alert', 'Escalation Alert'),
    ]
    
    SEVERITY_CHOICES = [
        ('info', 'Information'),
        ('warning', 'Warning'),
        ('critical', 'Critical'),
    ]
    
    chain = models.ForeignKey(CRSRevisionChain, on_delete=models.CASCADE, null=True, blank=True, related_name='ai_insights')
    revision = models.ForeignKey(CRSRevision, on_delete=models.CASCADE, null=True, blank=True, related_name='ai_insights')
    comment = models.ForeignKey(CRSComment, on_delete=models.CASCADE, null=True, blank=True, related_name='ai_insights')
    
    insight_type = models.CharField(max_length=30, choices=INSIGHT_TYPE_CHOICES)
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default='info')
    
    title = models.CharField(max_length=300)
    description = models.TextField()
    
    # AI Data
    confidence_score = models.FloatField(default=0.0, help_text='AI confidence (0-100)')
    ai_model_used = models.CharField(max_length=100, default='gpt-4', help_text='AI model used for this insight')
    
    # Actionable items
    recommended_action = models.TextField(blank=True, null=True)
    suggested_response = models.TextField(blank=True, null=True, help_text='AI-suggested response text')
    
    # Feedback loop
    was_helpful = models.BooleanField(null=True, blank=True)
    user_feedback = models.TextField(blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    is_active = models.BooleanField(default=True)
    dismissed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='dismissed_insights')
    dismissed_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'crs_ai_insights'
        ordering = ['-created_at']
        verbose_name = 'CRS AI Insight'
        verbose_name_plural = 'CRS AI Insights'
        indexes = [
            models.Index(fields=['chain', 'insight_type']),
            models.Index(fields=['severity', 'is_active']),
        ]
    
    def __str__(self):
        return f"{self.get_insight_type_display()} - {self.title}"


class CRSRevisionActivity(models.Model):
    """
    Track all activities within revision chains
    """
    
    ACTION_CHOICES = [
        ('chain_created', 'Chain Created'),
        ('revision_added', 'Revision Added'),
        ('comments_imported', 'Comments Imported'),
        ('comment_linked', 'Comment Linked'),
        ('ai_insight_generated', 'AI Insight Generated'),
        ('risk_level_changed', 'Risk Level Changed'),
        ('status_changed', 'Status Changed'),
        ('revision_superseded', 'Revision Superseded'),
    ]
    
    chain = models.ForeignKey(CRSRevisionChain, on_delete=models.CASCADE, related_name='activities')
    revision = models.ForeignKey(CRSRevision, on_delete=models.CASCADE, null=True, blank=True, related_name='activities')
    
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    description = models.TextField()
    
    performed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    performed_at = models.DateTimeField(auto_now_add=True)
    
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    
    class Meta:
        db_table = 'crs_revision_activities'
        ordering = ['-performed_at']
        verbose_name = 'CRS Revision Activity'
        verbose_name_plural = 'CRS Revision Activities'
    
    def __str__(self):
        return f"{self.get_action_display()} - {self.chain.chain_id} - {self.performed_at.strftime('%Y-%m-%d %H:%M')}"
