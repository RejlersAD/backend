"""
Usage Tracking Models

SOFT-CODED DESIGN:
- Automatically captures usage data via middleware
- No modifications to existing business logic required
- Async aggregation for performance optimization
"""

from django.db import models
from django.contrib.auth import get_user_model
from django.core.validators import MinValueValidator
from django.utils import timezone

User = get_user_model()


class UserUsageLog(models.Model):
    """
    Detailed log of every API request made by users.
    
    Automatically populated by UsageTrackingMiddleware.
    """
    
    REQUEST_TYPE_CHOICES = [
        ('GET', 'GET Request'),
        ('POST', 'POST Request'),
        ('PUT', 'PUT Request'),
        ('PATCH', 'PATCH Request'),
        ('DELETE', 'DELETE Request'),
    ]
    
    STATUS_CHOICES = [
        ('success', 'Success'),
        ('error', 'Error'),
        ('timeout', 'Timeout'),
    ]
    
    # User Information
    user = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='usage_logs',
        db_index=True,
        help_text="User who made the request"
    )
    department = models.CharField(
        max_length=100, 
        db_index=True,
        blank=True,
        null=True,
        help_text="User's department (from user profile)"
    )
    
    # Request Information
    feature_name = models.CharField(
        max_length=200,
        db_index=True,
        help_text="Feature/module name (e.g., 'PID Analysis', 'Process Datasheet')"
    )
    api_endpoint = models.CharField(
        max_length=500,
        help_text="Full API endpoint path"
    )
    request_type = models.CharField(
        max_length=10,
        choices=REQUEST_TYPE_CHOICES,
        default='GET'
    )
    
    # Resource Usage
    tokens_used = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Number of AI tokens consumed (for AI features)"
    )
    processing_time = models.FloatField(
        validators=[MinValueValidator(0)],
        help_text="Processing time in seconds"
    )
    
    # Response Information
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='success',
        db_index=True
    )
    status_code = models.IntegerField(
        default=200,
        help_text="HTTP status code"
    )
    error_message = models.TextField(
        blank=True,
        null=True,
        help_text="Error details if request failed"
    )
    
    # Metadata
    timestamp = models.DateTimeField(
        default=timezone.now,
        db_index=True,
        help_text="When the request was made"
    )
    ip_address = models.GenericIPAddressField(
        blank=True,
        null=True,
        help_text="Client IP address"
    )
    user_agent = models.CharField(
        max_length=500,
        blank=True,
        null=True,
        help_text="Browser/client user agent"
    )
    request_data_size = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Request payload size in bytes"
    )
    response_data_size = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Response payload size in bytes"
    )
    
    class Meta:
        db_table = 'usage_tracking_user_log'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['user', 'timestamp']),
            models.Index(fields=['department', 'timestamp']),
            models.Index(fields=['feature_name', 'timestamp']),
            models.Index(fields=['timestamp', 'status']),
        ]
        verbose_name = 'User Usage Log'
        verbose_name_plural = 'User Usage Logs'
    
    def __str__(self):
        return f"{self.user.username} - {self.feature_name} - {self.timestamp.strftime('%Y-%m-%d %H:%M:%S')}"


class DepartmentUsageSummary(models.Model):
    """
    Aggregated usage statistics per department.
    
    Updated periodically by async tasks for performance.
    """
    
    department = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        help_text="Department name"
    )
    
    # Aggregate Metrics
    total_requests = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Total number of API requests"
    )
    total_tokens = models.BigIntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Total AI tokens consumed"
    )
    total_users = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Number of unique users in department"
    )
    total_processing_time = models.FloatField(
        default=0.0,
        validators=[MinValueValidator(0)],
        help_text="Total processing time in seconds"
    )
    avg_processing_time = models.FloatField(
        default=0.0,
        validators=[MinValueValidator(0)],
        help_text="Average processing time per request"
    )
    
    # Error Tracking
    total_errors = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Total number of failed requests"
    )
    error_rate = models.FloatField(
        default=0.0,
        validators=[MinValueValidator(0)],
        help_text="Error rate percentage"
    )
    
    # Timestamps
    last_updated = models.DateTimeField(
        auto_now=True,
        help_text="Last time this summary was updated"
    )
    
    # Period Tracking (for daily/monthly stats)
    today_requests = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Requests made today"
    )
    this_month_requests = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Requests made this month"
    )
    
    class Meta:
        db_table = 'usage_tracking_department_summary'
        ordering = ['-total_requests']
        verbose_name = 'Department Usage Summary'
        verbose_name_plural = 'Department Usage Summaries'
    
    def __str__(self):
        return f"{self.department} - {self.total_requests} requests"
    
    def update_metrics(self):
        """Recalculate all metrics from UserUsageLog"""
        from django.db.models import Count, Sum, Avg, Q
        
        logs = UserUsageLog.objects.filter(department=self.department)
        
        stats = logs.aggregate(
            total_requests=Count('id'),
            total_tokens=Sum('tokens_used'),
            unique_users=Count('user', distinct=True),
            total_time=Sum('processing_time'),
            avg_time=Avg('processing_time'),
            total_errors=Count('id', filter=Q(status='error')),
        )
        
        self.total_requests = stats['total_requests'] or 0
        self.total_tokens = stats['total_tokens'] or 0
        self.total_users = stats['unique_users'] or 0
        self.total_processing_time = stats['total_time'] or 0.0
        self.avg_processing_time = stats['avg_time'] or 0.0
        self.total_errors = stats['total_errors'] or 0
        
        if self.total_requests > 0:
            self.error_rate = (self.total_errors / self.total_requests) * 100
        
        # Today's requests
        today = timezone.now().date()
        self.today_requests = logs.filter(timestamp__date=today).count()
        
        # This month's requests
        this_month = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        self.this_month_requests = logs.filter(timestamp__gte=this_month).count()
        
        self.save()


class FeatureUsageSummary(models.Model):
    """
    Aggregated usage statistics per feature/module.
    
    Updated periodically by async tasks for performance.
    """
    
    feature_name = models.CharField(
        max_length=200,
        unique=True,
        db_index=True,
        help_text="Feature/module name"
    )
    
    # Aggregate Metrics
    total_requests = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Total number of requests to this feature"
    )
    total_tokens = models.BigIntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Total AI tokens consumed by this feature"
    )
    total_users = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Number of unique users using this feature"
    )
    total_processing_time = models.FloatField(
        default=0.0,
        validators=[MinValueValidator(0)],
        help_text="Total processing time in seconds"
    )
    avg_processing_time = models.FloatField(
        default=0.0,
        validators=[MinValueValidator(0)],
        help_text="Average processing time per request"
    )
    
    # Performance Tracking
    total_errors = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Total number of failed requests"
    )
    error_rate = models.FloatField(
        default=0.0,
        validators=[MinValueValidator(0)],
        help_text="Error rate percentage"
    )
    
    # Timestamps
    last_updated = models.DateTimeField(
        auto_now=True,
        help_text="Last time this summary was updated"
    )
    
    # Period Tracking
    today_requests = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Requests made today"
    )
    this_month_requests = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Requests made this month"
    )
    
    # Popularity Score (calculated field)
    popularity_score = models.FloatField(
        default=0.0,
        help_text="Weighted score based on requests, users, and recency"
    )
    
    class Meta:
        db_table = 'usage_tracking_feature_summary'
        ordering = ['-total_requests']
        verbose_name = 'Feature Usage Summary'
        verbose_name_plural = 'Feature Usage Summaries'
    
    def __str__(self):
        return f"{self.feature_name} - {self.total_requests} requests"
    
    def update_metrics(self):
        """Recalculate all metrics from UserUsageLog"""
        from django.db.models import Count, Sum, Avg, Q
        
        logs = UserUsageLog.objects.filter(feature_name=self.feature_name)
        
        stats = logs.aggregate(
            total_requests=Count('id'),
            total_tokens=Sum('tokens_used'),
            unique_users=Count('user', distinct=True),
            total_time=Sum('processing_time'),
            avg_time=Avg('processing_time'),
            total_errors=Count('id', filter=Q(status='error')),
        )
        
        self.total_requests = stats['total_requests'] or 0
        self.total_tokens = stats['total_tokens'] or 0
        self.total_users = stats['unique_users'] or 0
        self.total_processing_time = stats['total_time'] or 0.0
        self.avg_processing_time = stats['avg_time'] or 0.0
        self.total_errors = stats['total_errors'] or 0
        
        if self.total_requests > 0:
            self.error_rate = (self.total_errors / self.total_requests) * 100
        
        # Today's requests
        today = timezone.now().date()
        self.today_requests = logs.filter(timestamp__date=today).count()
        
        # This month's requests
        this_month = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        self.this_month_requests = logs.filter(timestamp__gte=this_month).count()
        
        # Calculate popularity score
        # More weight to recent activity
        recent_logs = logs.filter(timestamp__gte=timezone.now() - timezone.timedelta(days=7))
        recent_count = recent_logs.count()
        
        self.popularity_score = (
            (self.total_requests * 0.3) +
            (self.total_users * 10) +
            (recent_count * 2)
        )
        
        self.save()
