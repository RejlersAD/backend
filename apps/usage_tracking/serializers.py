"""
Usage Tracking Serializers

Serializers for API responses with smart data aggregation.
"""

from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import UserUsageLog, DepartmentUsageSummary, FeatureUsageSummary

User = get_user_model()


class UserUsageLogSerializer(serializers.ModelSerializer):
    """Detailed usage log serializer"""
    
    username = serializers.CharField(source='user.username', read_only=True)
    user_email = serializers.EmailField(source='user.email', read_only=True)
    
    class Meta:
        model = UserUsageLog
        fields = [
            'id',
            'user',
            'username',
            'user_email',
            'department',
            'feature_name',
            'api_endpoint',
            'request_type',
            'tokens_used',
            'processing_time',
            'status',
            'status_code',
            'error_message',
            'timestamp',
            'ip_address',
            'user_agent',
            'request_data_size',
            'response_data_size',
        ]
        read_only_fields = fields


class DepartmentUsageSummarySerializer(serializers.ModelSerializer):
    """Department usage summary serializer"""
    
    success_rate = serializers.SerializerMethodField()
    avg_tokens_per_request = serializers.SerializerMethodField()
    
    class Meta:
        model = DepartmentUsageSummary
        fields = [
            'department',
            'total_requests',
            'total_tokens',
            'total_users',
            'total_processing_time',
            'avg_processing_time',
            'total_errors',
            'error_rate',
            'success_rate',
            'avg_tokens_per_request',
            'today_requests',
            'this_month_requests',
            'last_updated',
        ]
        read_only_fields = fields
    
    def get_success_rate(self, obj):
        """Calculate success rate percentage"""
        if obj.total_requests > 0:
            return round(100 - obj.error_rate, 2)
        return 100.0
    
    def get_avg_tokens_per_request(self, obj):
        """Calculate average tokens per request"""
        if obj.total_requests > 0:
            return round(obj.total_tokens / obj.total_requests, 2)
        return 0


class FeatureUsageSummarySerializer(serializers.ModelSerializer):
    """Feature usage summary serializer"""
    
    success_rate = serializers.SerializerMethodField()
    avg_tokens_per_request = serializers.SerializerMethodField()
    
    class Meta:
        model = FeatureUsageSummary
        fields = [
            'feature_name',
            'total_requests',
            'total_tokens',
            'total_users',
            'total_processing_time',
            'avg_processing_time',
            'total_errors',
            'error_rate',
            'success_rate',
            'avg_tokens_per_request',
            'today_requests',
            'this_month_requests',
            'popularity_score',
            'last_updated',
        ]
        read_only_fields = fields
    
    def get_success_rate(self, obj):
        """Calculate success rate percentage"""
        if obj.total_requests > 0:
            return round(100 - obj.error_rate, 2)
        return 100.0
    
    def get_avg_tokens_per_request(self, obj):
        """Calculate average tokens per request"""
        if obj.total_requests > 0:
            return round(obj.total_tokens / obj.total_requests, 2)
        return 0


class UserUsageSummarySerializer(serializers.Serializer):
    """Custom serializer for user-specific usage summary"""
    
    user_id = serializers.IntegerField()
    username = serializers.CharField()
    email = serializers.EmailField()
    department = serializers.CharField()
    total_requests = serializers.IntegerField()
    total_tokens = serializers.IntegerField()
    total_processing_time = serializers.FloatField()
    avg_processing_time = serializers.FloatField()
    total_errors = serializers.IntegerField()
    error_rate = serializers.FloatField()
    success_rate = serializers.FloatField()
    today_requests = serializers.IntegerField()
    this_month_requests = serializers.IntegerField()
    most_used_features = serializers.ListField(child=serializers.DictField())
    last_activity = serializers.DateTimeField()


class TopUsersSerializer(serializers.Serializer):
    """Serializer for top users ranking"""
    
    user_id = serializers.IntegerField()
    username = serializers.CharField()
    email = serializers.EmailField()
    department = serializers.CharField()
    total_requests = serializers.IntegerField()
    total_tokens = serializers.IntegerField()
    avg_processing_time = serializers.FloatField()
    last_activity = serializers.DateTimeField()


class UsageSummarySerializer(serializers.Serializer):
    """Global usage summary for management dashboard"""
    
    total_requests = serializers.IntegerField()
    total_users = serializers.IntegerField()
    total_departments = serializers.IntegerField()
    total_features = serializers.IntegerField()
    total_tokens = serializers.IntegerField()
    total_processing_time = serializers.FloatField()
    avg_processing_time = serializers.FloatField()
    total_errors = serializers.IntegerField()
    error_rate = serializers.FloatField()
    success_rate = serializers.FloatField()
    
    # Period-specific
    today_requests = serializers.IntegerField()
    this_week_requests = serializers.IntegerField()
    this_month_requests = serializers.IntegerField()
    
    # Rankings
    top_departments = serializers.ListField(child=serializers.DictField())
    top_features = serializers.ListField(child=serializers.DictField())
    top_users = serializers.ListField(child=serializers.DictField())
    
    # Trends
    daily_trend = serializers.ListField(child=serializers.DictField())
    hourly_distribution = serializers.ListField(child=serializers.DictField())


class SalesReportSerializer(serializers.Serializer):
    """Serializer for sales/management report"""
    
    report_date = serializers.DateField()
    report_type = serializers.CharField()  # 'daily', 'weekly', 'monthly'
    
    # Overview
    total_active_users = serializers.IntegerField()
    total_requests = serializers.IntegerField()
    total_tokens_consumed = serializers.IntegerField()
    total_departments = serializers.IntegerField()
    
    # Growth Metrics
    user_growth = serializers.FloatField()  # % change from previous period
    request_growth = serializers.FloatField()
    token_growth = serializers.FloatField()
    
    # Department Breakdown
    department_stats = serializers.ListField(child=serializers.DictField())
    
    # Feature Adoption
    feature_stats = serializers.ListField(child=serializers.DictField())
    
    # User Engagement
    high_engagement_users = serializers.IntegerField()  # Users with >50 requests
    medium_engagement_users = serializers.IntegerField()  # Users with 10-50 requests
    low_engagement_users = serializers.IntegerField()  # Users with <10 requests
    
    # Performance Metrics
    avg_response_time = serializers.FloatField()
    system_reliability = serializers.FloatField()  # % uptime/success rate
    
    # Recommendations
    insights = serializers.ListField(child=serializers.CharField())


class UsageTimeSeriesSerializer(serializers.Serializer):
    """Time series data for charts"""
    
    date = serializers.DateField()
    hour = serializers.IntegerField(required=False)
    requests = serializers.IntegerField()
    users = serializers.IntegerField()
    tokens = serializers.IntegerField()
    avg_response_time = serializers.FloatField()
    error_rate = serializers.FloatField()
