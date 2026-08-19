"""
Serializers for AI-Powered Analytics Models
"""
from rest_framework import serializers
from .analytics_models import (
    SystemMetrics,
    UserActivityAnalytics,
    SecurityAlert,
    PredictiveInsight,
    FeatureUsageAnalytics,
    ErrorLogAnalytics,
    SystemHealthCheck
)
from django.contrib.auth import get_user_model

User = get_user_model()


class SystemMetricsSerializer(serializers.ModelSerializer):
    """Serializer for system performance metrics"""
    
    class Meta:
        model = SystemMetrics
        fields = [
            'id', 'timestamp', 'avg_response_time_ms', 'peak_response_time_ms',
            'api_requests_count', 'failed_requests_count', 'success_rate_percentage',
            'cpu_usage_percentage', 'memory_usage_mb', 'disk_usage_gb', 'active_connections',
            'db_query_count', 'db_slow_queries_count', 'db_connection_pool_size',
            'ai_requests_count', 'ai_avg_processing_time_s', 'ai_token_usage', 'ai_cost_usd',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class UserActivityAnalyticsSerializer(serializers.ModelSerializer):
    """Serializer for user behavior analytics"""
    user_email = serializers.EmailField(source='user.email', read_only=True)
    user_name = serializers.SerializerMethodField()
    
    class Meta:
        model = UserActivityAnalytics
        fields = [
            'id', 'user', 'user_email', 'user_name', 'date',
            'login_count', 'total_session_duration_minutes', 'avg_session_duration_minutes',
            'features_used_count', 'features_used_list',
            'engagement_score', 'productivity_score', 'risk_score',
            'drawings_uploaded', 'analyses_completed', 'reports_generated', 'documents_accessed',
            'usage_pattern', 'anomaly_detected', 'anomaly_details', 'recommendations',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def get_user_name(self, obj):
        return f"{obj.user.first_name} {obj.user.last_name}".strip() or obj.user.email


class SecurityAlertSerializer(serializers.ModelSerializer):
    """Serializer for security alerts"""
    user_email = serializers.EmailField(source='user.email', read_only=True, allow_null=True)
    resolved_by_email = serializers.EmailField(source='resolved_by.email', read_only=True, allow_null=True)
    
    class Meta:
        model = SecurityAlert
        fields = [
            'id', 'alert_type', 'severity', 'status', 'title', 'description', 'detection_time',
            'user', 'user_email', 'ip_address', 'user_agent',
            'ai_confidence', 'threat_indicators', 'recommended_actions',
            'resolved_at', 'resolved_by', 'resolved_by_email', 'resolution_notes',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'detection_time', 'created_at', 'updated_at']


class PredictiveInsightSerializer(serializers.ModelSerializer):
    """Serializer for AI predictions"""
    acknowledged_by_email = serializers.EmailField(source='acknowledged_by.email', read_only=True, allow_null=True)
    
    class Meta:
        model = PredictiveInsight
        fields = [
            'id', 'insight_type', 'title', 'description',
            'prediction_date', 'confidence_score', 'predicted_values',
            'impact_level', 'affected_area',
            'recommendations', 'action_items', 'estimated_benefit',
            'ml_model_used', 'training_data_period', 'last_retrained',
            'is_active', 'is_acknowledged', 'acknowledged_by', 'acknowledged_by_email', 'acknowledged_at',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class FeatureUsageAnalyticsSerializer(serializers.ModelSerializer):
    """Serializer for feature usage analytics"""
    
    class Meta:
        model = FeatureUsageAnalytics
        fields = [
            'id', 'feature_name', 'date',
            'total_users', 'active_users', 'new_users', 'returning_users',
            'total_usage_count', 'avg_usage_per_user', 'success_rate', 'avg_completion_time_s',
            'adoption_rate_percentage', 'growth_rate_percentage', 'retention_rate_percentage',
            'health_score', 'trend', 'insights',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class ErrorLogAnalyticsSerializer(serializers.ModelSerializer):
    """Serializer for error analytics"""
    
    class Meta:
        model = ErrorLogAnalytics
        fields = [
            'id', 'error_type', 'error_message',
            'first_occurrence', 'last_occurrence', 'occurrence_count', 'affected_users_count',
            'endpoint', 'feature', 'stack_trace', 'request_data',
            'severity', 'root_cause_analysis', 'suggested_fix', 'similar_issues',
            'status', 'resolution_notes', 'resolved_at',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'first_occurrence', 'created_at', 'updated_at']


class SystemHealthCheckSerializer(serializers.ModelSerializer):
    """Serializer for system health checks"""
    
    class Meta:
        model = SystemHealthCheck
        fields = [
            'id', 'check_time',
            'database_status', 'redis_status', 'celery_status', 'storage_status', 'api_status',
            'overall_status', 'health_score',
            'response_times', 'error_rates', 'resource_usage',
            'issues_found', 'warnings',
            'recommendations', 'automated_fixes_applied',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'check_time', 'created_at', 'updated_at']


# Dashboard Summary Serializers
class DashboardStatsSerializer(serializers.Serializer):
    """Comprehensive dashboard statistics"""
    # System Overview
    total_users = serializers.IntegerField()
    active_users_today = serializers.IntegerField()
    total_api_requests_today = serializers.IntegerField()
    system_health_score = serializers.FloatField()
    
    # Performance
    avg_response_time_ms = serializers.FloatField()
    success_rate_percentage = serializers.FloatField()
    active_connections = serializers.IntegerField()
    
    # Security
    active_alerts_count = serializers.IntegerField()
    critical_alerts_count = serializers.IntegerField()
    
    # AI Insights
    active_predictions_count = serializers.IntegerField()
    high_impact_insights_count = serializers.IntegerField()
    
    # Errors
    errors_today = serializers.IntegerField()
    critical_errors_count = serializers.IntegerField()
    
    # Usage Trends
    user_growth_percentage = serializers.FloatField()
    engagement_trend = serializers.CharField()


class RealTimeActivitySerializer(serializers.Serializer):
    """Real-time activity feed"""
    activity_type = serializers.CharField()
    user_email = serializers.EmailField()
    description = serializers.CharField()
    timestamp = serializers.DateTimeField()
    severity = serializers.CharField()
    metadata = serializers.JSONField()
