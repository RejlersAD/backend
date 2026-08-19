"""
Real-time Activity Tracking Models
Track all user and system activities in real-time
"""
from django.db import models
from django.conf import settings
from django.utils import timezone
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
import json


class SystemActivity(models.Model):
    """
    Track all system activities in real-time
    Soft-coded activity types and categories
    """
    
    ACTIVITY_TYPES = [
        ('user_login', 'User Login'),
        ('user_logout', 'User Logout'),
        ('user_created', 'User Created'),
        ('user_updated', 'User Updated'),
        ('user_deleted', 'User Deleted'),
        ('role_assigned', 'Role Assigned'),
        ('role_removed', 'Role Removed'),
        ('permission_granted', 'Permission Granted'),
        ('permission_revoked', 'Permission Revoked'),
        ('document_uploaded', 'Document Uploaded'),
        ('document_processed', 'Document Processed'),
        ('document_deleted', 'Document Deleted'),
        ('project_created', 'Project Created'),
        ('project_updated', 'Project Updated'),
        ('project_deleted', 'Project Deleted'),
        ('api_request', 'API Request'),
        ('system_error', 'System Error'),
        ('security_event', 'Security Event'),
        ('data_export', 'Data Export'),
        ('data_import', 'Data Import'),
        ('backup_created', 'Backup Created'),
        ('settings_changed', 'Settings Changed'),
        ('notification_sent', 'Notification Sent'),
        ('report_generated', 'Report Generated'),
        ('ai_analysis', 'AI Analysis Completed'),
        ('ml_prediction', 'ML Prediction Made'),
        ('database_query', 'Database Query'),
        ('cache_hit', 'Cache Hit'),
        ('cache_miss', 'Cache Miss'),
        ('webhook_triggered', 'Webhook Triggered'),
    ]
    
    ACTIVITY_CATEGORIES = [
        ('authentication', 'Authentication'),
        ('authorization', 'Authorization'),
        ('data_management', 'Data Management'),
        ('system_operation', 'System Operation'),
        ('security', 'Security'),
        ('api', 'API'),
        ('ml_ai', 'ML/AI'),
        ('communication', 'Communication'),
        ('maintenance', 'Maintenance'),
    ]
    
    SEVERITY_LEVELS = [
        ('info', 'Info'),
        ('low', 'Low'),
        ('normal', 'Normal'),
        ('high', 'High'),
        ('critical', 'Critical'),
    ]
    
    # Core fields
    activity_type = models.CharField(max_length=50, choices=ACTIVITY_TYPES, db_index=True)
    category = models.CharField(max_length=50, choices=ACTIVITY_CATEGORIES, default='system_operation')
    severity = models.CharField(max_length=20, choices=SEVERITY_LEVELS, default='normal')
    
    # User and context
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='activities')
    user_email = models.EmailField(blank=True)
    user_full_name = models.CharField(max_length=255, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    
    # Activity details
    description = models.TextField()
    details = models.JSONField(default=dict, help_text="Additional activity details")
    
    # Related object (generic foreign key)
    content_type = models.ForeignKey(ContentType, on_delete=models.SET_NULL, null=True, blank=True)
    object_id = models.PositiveIntegerField(null=True, blank=True)
    content_object = GenericForeignKey('content_type', 'object_id')
    
    # Status
    success = models.BooleanField(default=True)
    error_message = models.TextField(blank=True)
    
    # Performance metrics
    duration_ms = models.IntegerField(null=True, blank=True, help_text="Duration in milliseconds")
    
    # Metadata
    metadata = models.JSONField(default=dict)
    tags = models.JSONField(default=list, help_text="Tags for categorization")
    
    # Timestamps
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'system_activity'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['-timestamp', 'activity_type']),
            models.Index(fields=['user', '-timestamp']),
            models.Index(fields=['category', '-timestamp']),
            models.Index(fields=['severity', '-timestamp']),
        ]
        verbose_name = 'System Activity'
        verbose_name_plural = 'System Activities'
    
    def __str__(self):
        return f"{self.activity_type} by {self.user_email or 'System'} at {self.timestamp}"
    
    @property
    def time_ago(self):
        """Human-readable time since activity"""
        now = timezone.now()
        diff = now - self.timestamp
        
        seconds = diff.total_seconds()
        if seconds < 60:
            return f"{int(seconds)}s ago"
        elif seconds < 3600:
            return f"{int(seconds/60)}m ago"
        elif seconds < 86400:
            return f"{int(seconds/3600)}h ago"
        else:
            return f"{int(seconds/86400)}d ago"


class ActivityStream(models.Model):
    """
    Aggregated activity stream for dashboard
    Pre-processed for fast retrieval
    """
    
    activity = models.OneToOneField(SystemActivity, on_delete=models.CASCADE, related_name='stream')
    
    # Display fields (denormalized for performance)
    display_title = models.CharField(max_length=500)
    display_subtitle = models.CharField(max_length=500, blank=True)
    icon = models.CharField(max_length=50, default='info')
    color = models.CharField(max_length=50, default='blue')
    
    # Grouping
    is_grouped = models.BooleanField(default=False)
    group_key = models.CharField(max_length=200, blank=True, db_index=True)
    group_count = models.IntegerField(default=1)
    
    # Visibility
    is_public = models.BooleanField(default=True)
    is_pinned = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'activity_stream'
        ordering = ['-created_at']


class ActivityStatistics(models.Model):
    """
    Real-time activity statistics
    Updated periodically for dashboard display
    """
    
    # Time period
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    period_type = models.CharField(max_length=20, choices=[
        ('minute', 'Minute'),
        ('hour', 'Hour'),
        ('day', 'Day'),
    ])
    
    # Activity counts by type
    total_activities = models.IntegerField(default=0)
    user_activities = models.IntegerField(default=0)
    system_activities = models.IntegerField(default=0)
    api_requests = models.IntegerField(default=0)
    
    # Activities by category
    activities_by_category = models.JSONField(default=dict)
    activities_by_type = models.JSONField(default=dict)
    
    # Top users
    top_users = models.JSONField(default=list)
    
    # Performance
    avg_duration_ms = models.FloatField(null=True, blank=True)
    success_rate = models.FloatField(default=100.0)
    
    # Metadata
    metadata = models.JSONField(default=dict)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'activity_statistics'
        ordering = ['-period_start']
        indexes = [
            models.Index(fields=['period_type', '-period_start']),
        ]


class UserSession(models.Model):
    """
    Track active user sessions for real-time presence
    """
    
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='sessions')
    session_key = models.CharField(max_length=255, unique=True, db_index=True)
    
    # Session info
    ip_address = models.GenericIPAddressField()
    user_agent = models.TextField()
    device_type = models.CharField(max_length=50, blank=True)
    browser = models.CharField(max_length=100, blank=True)
    os = models.CharField(max_length=100, blank=True)
    
    # Activity
    last_activity = models.DateTimeField(default=timezone.now)
    current_page = models.CharField(max_length=500, blank=True)
    
    # Status
    is_active = models.BooleanField(default=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    
    class Meta:
        db_table = 'user_session'
        ordering = ['-last_activity']
        indexes = [
            models.Index(fields=['user', '-last_activity']),
            models.Index(fields=['is_active', '-last_activity']),
        ]
    
    @property
    def is_expired(self):
        return timezone.now() > self.expires_at
    
    @property
    def duration(self):
        return (self.last_activity - self.created_at).total_seconds()
