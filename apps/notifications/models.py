"""
Notification System Models
Soft-coded notification management with priority levels and channels
"""
from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()


class NotificationCategory(models.Model):
    """Notification categories for organization"""
    CATEGORY_CHOICES = [
        ('SYSTEM', 'System'),
        ('PROJECT', 'Project'),
        ('QHSE', 'QHSE'),
        ('DOCUMENT', 'Document'),
        ('USER', 'User Activity'),
        ('ADMIN', 'Administration'),
        ('AI', 'AI/ML'),
        ('APPROVAL', 'Approval Required'),
        ('ALERT', 'Alert'),
        ('INFO', 'Information'),
    ]
    
    name = models.CharField(max_length=50, choices=CATEGORY_CHOICES, unique=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50, default='📢')
    color = models.CharField(max_length=20, default='blue')
    is_active = models.BooleanField(default=True)
    
    class Meta:
        db_table = 'notification_categories'
        verbose_name_plural = 'Notification Categories'
    
    def __str__(self):
        return f"{self.icon} {self.get_name_display()}"


class Notification(models.Model):
    """
    Universal notification model with multi-channel support
    """
    PRIORITY_CHOICES = [
        ('LOW', 'Low'),
        ('NORMAL', 'Normal'),
        ('HIGH', 'High'),
        ('URGENT', 'Urgent'),
        ('CRITICAL', 'Critical'),
    ]
    
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('SENT', 'Sent'),
        ('READ', 'Read'),
        ('FAILED', 'Failed'),
        ('ARCHIVED', 'Archived'),
    ]
    
    # Core fields
    title = models.CharField(max_length=255)
    message = models.TextField()
    category = models.ForeignKey(
        NotificationCategory,
        on_delete=models.SET_NULL,
        null=True,
        related_name='notifications'
    )
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='NORMAL', db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING', db_index=True)
    
    # Recipients
    recipient = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='notifications_received'
    )
    sender = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notifications_sent'
    )
    
    # Channel flags
    send_in_app = models.BooleanField(default=True)
    send_email = models.BooleanField(default=False)
    send_sms = models.BooleanField(default=False)
    
    # Email tracking
    email_sent = models.BooleanField(default=False)
    email_sent_at = models.DateTimeField(null=True, blank=True)
    email_error = models.TextField(null=True, blank=True)
    
    # Interaction tracking
    is_read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)
    
    # Action link
    action_url = models.CharField(max_length=500, blank=True, null=True)
    action_label = models.CharField(max_length=100, blank=True, null=True)
    
    # Additional metadata
    metadata = models.JSONField(default=dict, blank=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True, blank=True, help_text="Auto-archive after this date")
    
    class Meta:
        db_table = 'notifications'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['recipient', 'is_read', '-created_at']),
            models.Index(fields=['priority', '-created_at']),
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['category', '-created_at']),
        ]
    
    def __str__(self):
        return f"{self.title} - {self.recipient.username}"
    
    def mark_as_read(self):
        """Mark notification as read"""
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.status = 'READ'
            self.save(update_fields=['is_read', 'read_at', 'status', 'updated_at'])
    
    def mark_email_sent(self, success=True, error_message=None):
        """Mark email as sent or failed"""
        self.email_sent = success
        self.email_sent_at = timezone.now()
        if not success:
            self.email_error = error_message
            self.status = 'FAILED'
        else:
            self.status = 'SENT'
        self.save(update_fields=['email_sent', 'email_sent_at', 'email_error', 'status', 'updated_at'])
    
    @property
    def is_expired(self):
        """Check if notification is expired"""
        if self.expires_at:
            return timezone.now() > self.expires_at
        return False
    
    @property
    def age_minutes(self):
        """Get age in minutes"""
        return (timezone.now() - self.created_at).total_seconds() / 60


class NotificationPreference(models.Model):
    """User notification preferences"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='notification_preferences')
    
    # Channel preferences
    enable_email = models.BooleanField(default=True)
    enable_in_app = models.BooleanField(default=True)
    enable_sms = models.BooleanField(default=False)
    
    # Category preferences (JSON)
    category_preferences = models.JSONField(
        default=dict,
        help_text="Per-category notification settings"
    )
    
    # Frequency settings
    digest_frequency = models.CharField(
        max_length=20,
        choices=[
            ('REALTIME', 'Real-time'),
            ('HOURLY', 'Hourly Digest'),
            ('DAILY', 'Daily Digest'),
            ('WEEKLY', 'Weekly Digest'),
        ],
        default='REALTIME'
    )
    
    # Quiet hours
    quiet_hours_enabled = models.BooleanField(default=False)
    quiet_hours_start = models.TimeField(null=True, blank=True)
    quiet_hours_end = models.TimeField(null=True, blank=True)
    
    # Priority filters
    min_priority_email = models.CharField(
        max_length=20,
        choices=Notification.PRIORITY_CHOICES,
        default='NORMAL'
    )
    min_priority_sms = models.CharField(
        max_length=20,
        choices=Notification.PRIORITY_CHOICES,
        default='URGENT'
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'notification_preferences'
    
    def __str__(self):
        return f"Notification Preferences - {self.user.username}"


class NotificationLog(models.Model):
    """Audit log for notification activities"""
    notification = models.ForeignKey(Notification, on_delete=models.CASCADE, related_name='logs')
    action = models.CharField(max_length=50)  # created, sent, read, failed, etc.
    details = models.JSONField(default=dict)
    timestamp = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'notification_logs'
        ordering = ['-timestamp']
    
    def __str__(self):
        return f"{self.notification.title} - {self.action}"
