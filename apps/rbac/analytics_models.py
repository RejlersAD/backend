"""
Admin Analytics Models - AI-Powered System Insights
Advanced analytics for comprehensive system management
"""
import uuid
from django.db import models
from django.contrib.auth import get_user_model
from apps.core.models import TimeStampedModel
from django.utils import timezone

User = get_user_model()


class SystemMetrics(TimeStampedModel):
    """
    Real-time system performance metrics
    Tracks API performance, resource usage, and system health
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    
    # Performance Metrics
    avg_response_time_ms = models.FloatField(default=0)
    peak_response_time_ms = models.FloatField(default=0)
    api_requests_count = models.IntegerField(default=0)
    failed_requests_count = models.IntegerField(default=0)
    success_rate_percentage = models.FloatField(default=100.0)
    
    # Resource Usage
    cpu_usage_percentage = models.FloatField(default=0)
    memory_usage_mb = models.FloatField(default=0)
    disk_usage_gb = models.FloatField(default=0)
    active_connections = models.IntegerField(default=0)
    
    # Database Metrics
    db_query_count = models.IntegerField(default=0)
    db_slow_queries_count = models.IntegerField(default=0)
    db_connection_pool_size = models.IntegerField(default=0)
    
    # AI Analysis Metrics
    ai_requests_count = models.IntegerField(default=0)
    ai_avg_processing_time_s = models.FloatField(default=0)
    ai_token_usage = models.IntegerField(default=0)
    ai_cost_usd = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    
    class Meta:
        db_table = 'admin_system_metrics'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['-timestamp']),
            models.Index(fields=['timestamp', 'success_rate_percentage']),
        ]
    
    def __str__(self):
        return f"Metrics {self.timestamp.strftime('%Y-%m-%d %H:%M')}"


class UserActivityAnalytics(TimeStampedModel):
    """
    AI-powered user behavior analytics
    Tracks patterns, anomalies, and engagement metrics
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='activity_analytics')
    date = models.DateField(default=timezone.now, db_index=True)
    
    # Activity Metrics
    login_count = models.IntegerField(default=0)
    total_session_duration_minutes = models.IntegerField(default=0)
    avg_session_duration_minutes = models.FloatField(default=0)
    features_used_count = models.IntegerField(default=0)
    features_used_list = models.JSONField(default=list)
    
    # Engagement Score (AI-calculated)
    engagement_score = models.FloatField(default=0)  # 0-100
    productivity_score = models.FloatField(default=0)  # 0-100
    risk_score = models.FloatField(default=0)  # 0-100 (anomaly detection)
    
    # Action Metrics
    drawings_uploaded = models.IntegerField(default=0)
    analyses_completed = models.IntegerField(default=0)
    reports_generated = models.IntegerField(default=0)
    documents_accessed = models.IntegerField(default=0)
    
    # AI Insights
    usage_pattern = models.CharField(max_length=50, default='normal')  # normal, power_user, irregular
    anomaly_detected = models.BooleanField(default=False)
    anomaly_details = models.JSONField(default=dict, blank=True)
    recommendations = models.JSONField(default=list, blank=True)
    
    class Meta:
        db_table = 'admin_user_activity_analytics'
        ordering = ['-date']
        unique_together = ['user', 'date']
        indexes = [
            models.Index(fields=['user', '-date']),
            models.Index(fields=['-engagement_score']),
            models.Index(fields=['anomaly_detected']),
        ]
    
    def __str__(self):
        return f"{self.user.email} - {self.date}"


class SecurityAlert(TimeStampedModel):
    """
    AI-powered security threat detection
    Real-time monitoring of suspicious activities
    """
    SEVERITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('critical', 'Critical'),
    ]
    
    STATUS_CHOICES = [
        ('new', 'New'),
        ('investigating', 'Investigating'),
        ('resolved', 'Resolved'),
        ('false_positive', 'False Positive'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    alert_type = models.CharField(max_length=100, db_index=True)
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default='low')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new')
    
    # Alert Details
    title = models.CharField(max_length=255)
    description = models.TextField()
    detection_time = models.DateTimeField(default=timezone.now)
    
    # Related User (if applicable)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='security_alerts')
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    
    # AI Analysis
    ai_confidence = models.FloatField(default=0)  # 0-1
    threat_indicators = models.JSONField(default=list)
    recommended_actions = models.JSONField(default=list)
    
    # Resolution
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='resolved_alerts')
    resolution_notes = models.TextField(blank=True)
    
    class Meta:
        db_table = 'admin_security_alerts'
        ordering = ['-detection_time']
        indexes = [
            models.Index(fields=['-detection_time']),
            models.Index(fields=['severity', 'status']),
            models.Index(fields=['alert_type']),
        ]
    
    def __str__(self):
        return f"{self.severity.upper()}: {self.title}"


class PredictiveInsight(TimeStampedModel):
    """
    AI-generated predictions and recommendations
    Machine learning insights for proactive management
    """
    INSIGHT_TYPE_CHOICES = [
        ('usage_forecast', 'Usage Forecast'),
        ('capacity_planning', 'Capacity Planning'),
        ('user_churn_risk', 'User Churn Risk'),
        ('performance_optimization', 'Performance Optimization'),
        ('cost_optimization', 'Cost Optimization'),
        ('security_risk', 'Security Risk Prediction'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    insight_type = models.CharField(max_length=50, choices=INSIGHT_TYPE_CHOICES, db_index=True)
    title = models.CharField(max_length=255)
    description = models.TextField()
    
    # Prediction Data
    prediction_date = models.DateField()
    confidence_score = models.FloatField(default=0)  # 0-1
    predicted_values = models.JSONField(default=dict)
    
    # Impact Analysis
    impact_level = models.CharField(max_length=20, choices=[
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
    ], default='medium')
    affected_area = models.CharField(max_length=100)
    
    # Recommendations
    recommendations = models.JSONField(default=list)
    action_items = models.JSONField(default=list)
    estimated_benefit = models.TextField(blank=True)
    
    # Model Information
    ml_model_used = models.CharField(max_length=100)
    training_data_period = models.CharField(max_length=100)
    last_retrained = models.DateTimeField(null=True, blank=True)
    
    # Status
    is_active = models.BooleanField(default=True)
    is_acknowledged = models.BooleanField(default=False)
    acknowledged_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'admin_predictive_insights'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['insight_type', '-created_at']),
            models.Index(fields=['prediction_date']),
            models.Index(fields=['is_active', 'is_acknowledged']),
        ]
    
    def __str__(self):
        return f"{self.insight_type}: {self.title}"


class FeatureUsageAnalytics(TimeStampedModel):
    """
    Track feature adoption and usage patterns
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    feature_name = models.CharField(max_length=100, db_index=True)
    date = models.DateField(default=timezone.now, db_index=True)
    
    # Usage Metrics
    total_users = models.IntegerField(default=0)
    active_users = models.IntegerField(default=0)
    new_users = models.IntegerField(default=0)
    returning_users = models.IntegerField(default=0)
    
    # Performance
    total_usage_count = models.IntegerField(default=0)
    avg_usage_per_user = models.FloatField(default=0)
    success_rate = models.FloatField(default=100.0)
    avg_completion_time_s = models.FloatField(default=0)
    
    # Adoption Metrics
    adoption_rate_percentage = models.FloatField(default=0)
    growth_rate_percentage = models.FloatField(default=0)
    retention_rate_percentage = models.FloatField(default=0)
    
    # AI Insights
    health_score = models.FloatField(default=100)  # 0-100
    trend = models.CharField(max_length=20, default='stable')  # growing, stable, declining
    insights = models.JSONField(default=list)
    
    class Meta:
        db_table = 'admin_feature_usage_analytics'
        ordering = ['-date']
        unique_together = ['feature_name', 'date']
        indexes = [
            models.Index(fields=['feature_name', '-date']),
            models.Index(fields=['-adoption_rate_percentage']),
        ]
    
    def __str__(self):
        return f"{self.feature_name} - {self.date}"


class ErrorLogAnalytics(TimeStampedModel):
    """
    Aggregated error analytics with AI-powered root cause analysis
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    error_type = models.CharField(max_length=100, db_index=True)
    error_message = models.TextField()
    
    # Occurrence Data
    first_occurrence = models.DateTimeField(default=timezone.now)
    last_occurrence = models.DateTimeField(default=timezone.now)
    occurrence_count = models.IntegerField(default=1)
    affected_users_count = models.IntegerField(default=0)
    
    # Context
    endpoint = models.CharField(max_length=255, blank=True)
    feature = models.CharField(max_length=100, blank=True)
    stack_trace = models.TextField(blank=True)
    request_data = models.JSONField(default=dict, blank=True)
    
    # AI Analysis
    severity = models.CharField(max_length=20, choices=[
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('critical', 'Critical'),
    ], default='medium')
    root_cause_analysis = models.TextField(blank=True)
    suggested_fix = models.TextField(blank=True)
    similar_issues = models.JSONField(default=list)
    
    # Status
    status = models.CharField(max_length=20, choices=[
        ('open', 'Open'),
        ('investigating', 'Investigating'),
        ('resolved', 'Resolved'),
        ('wont_fix', 'Won\'t Fix'),
    ], default='open')
    resolution_notes = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'admin_error_log_analytics'
        ordering = ['-last_occurrence']
        indexes = [
            models.Index(fields=['error_type', '-last_occurrence']),
            models.Index(fields=['severity', 'status']),
            models.Index(fields=['-occurrence_count']),
        ]
    
    def __str__(self):
        return f"{self.error_type} ({self.occurrence_count}x)"


class SystemHealthCheck(TimeStampedModel):
    """
    Automated system health monitoring
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    check_time = models.DateTimeField(default=timezone.now, db_index=True)
    
    # Component Status
    database_status = models.CharField(max_length=20, default='healthy')
    redis_status = models.CharField(max_length=20, default='healthy')
    celery_status = models.CharField(max_length=20, default='healthy')
    storage_status = models.CharField(max_length=20, default='healthy')
    api_status = models.CharField(max_length=20, default='healthy')
    
    # Overall Health
    overall_status = models.CharField(max_length=20, default='healthy')  # healthy, degraded, critical
    health_score = models.FloatField(default=100)  # 0-100
    
    # Detailed Metrics
    response_times = models.JSONField(default=dict)
    error_rates = models.JSONField(default=dict)
    resource_usage = models.JSONField(default=dict)
    
    # Issues Detected
    issues_found = models.JSONField(default=list)
    warnings = models.JSONField(default=list)
    
    # AI Recommendations
    recommendations = models.JSONField(default=list)
    automated_fixes_applied = models.JSONField(default=list)
    
    class Meta:
        db_table = 'admin_system_health_checks'
        ordering = ['-check_time']
        indexes = [
            models.Index(fields=['-check_time']),
            models.Index(fields=['overall_status']),
        ]
    
    def __str__(self):
        return f"Health Check {self.check_time.strftime('%Y-%m-%d %H:%M')} - {self.overall_status}"
