"""
RBAC Admin Configuration
"""
from django.contrib import admin
from .models import (
    Organization, Module, Permission, Role, RolePermission, RoleModule,
    UserProfile, UserRole, UserStorage, AuditLog
)
from .analytics_models import (
    SystemMetrics, UserActivityAnalytics, SecurityAlert, PredictiveInsight,
    FeatureUsageAnalytics, ErrorLogAnalytics, SystemHealthCheck
)


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'is_active', 'created_at']
    list_filter = ['is_active']
    search_fields = ['name', 'code']


@admin.register(Module)
class ModuleAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'is_active', 'order']
    list_filter = ['is_active']
    search_fields = ['name', 'code']
    ordering = ['order', 'name']


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'module', 'action', 'is_active']
    list_filter = ['module', 'action', 'is_active']
    search_fields = ['name', 'code']


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'level', 'is_active', 'is_system_role']
    list_filter = ['level', 'is_active', 'is_system_role']
    search_fields = ['name', 'code']


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'organization', 'status', 'employee_id', 'created_at']
    list_filter = ['organization', 'status', 'is_deleted']
    search_fields = ['user__email', 'employee_id']


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ['user_email', 'action', 'resource_type', 'timestamp', 'success']
    list_filter = ['action', 'resource_type', 'success', 'timestamp']
    search_fields = ['user_email', 'resource_type']
    readonly_fields = list_display + ['changes', 'metadata']
    
    def has_add_permission(self, request):
        return False


# Analytics Models Admin
@admin.register(SystemMetrics)
class SystemMetricsAdmin(admin.ModelAdmin):
    list_display = ['timestamp', 'success_rate_percentage', 'avg_response_time_ms', 'active_connections', 'cpu_usage_percentage']
    list_filter = ['timestamp']
    readonly_fields = ['id', 'created_at', 'updated_at']
    ordering = ['-timestamp']


@admin.register(UserActivityAnalytics)
class UserActivityAnalyticsAdmin(admin.ModelAdmin):
    list_display = ['user', 'date', 'engagement_score', 'login_count', 'anomaly_detected']
    list_filter = ['date', 'anomaly_detected', 'usage_pattern']
    search_fields = ['user__email']
    readonly_fields = ['id', 'created_at', 'updated_at']
    ordering = ['-date']


@admin.register(SecurityAlert)
class SecurityAlertAdmin(admin.ModelAdmin):
    list_display = ['title', 'severity', 'status', 'detection_time', 'user']
    list_filter = ['severity', 'status', 'alert_type', 'detection_time']
    search_fields = ['title', 'description', 'user__email']
    readonly_fields = ['id', 'detection_time', 'created_at', 'updated_at']
    ordering = ['-detection_time']


@admin.register(PredictiveInsight)
class PredictiveInsightAdmin(admin.ModelAdmin):
    list_display = ['title', 'insight_type', 'confidence_score', 'impact_level', 'is_acknowledged']
    list_filter = ['insight_type', 'impact_level', 'is_active', 'is_acknowledged']
    search_fields = ['title', 'description']
    readonly_fields = ['id', 'created_at', 'updated_at']
    ordering = ['-created_at']


@admin.register(FeatureUsageAnalytics)
class FeatureUsageAnalyticsAdmin(admin.ModelAdmin):
    list_display = ['feature_name', 'date', 'active_users', 'adoption_rate_percentage', 'health_score']
    list_filter = ['date', 'trend']
    search_fields = ['feature_name']
    readonly_fields = ['id', 'created_at', 'updated_at']
    ordering = ['-date']


@admin.register(ErrorLogAnalytics)
class ErrorLogAnalyticsAdmin(admin.ModelAdmin):
    list_display = ['error_type', 'severity', 'occurrence_count', 'last_occurrence', 'status']
    list_filter = ['severity', 'status', 'first_occurrence']
    search_fields = ['error_type', 'error_message']
    readonly_fields = ['id', 'first_occurrence', 'created_at', 'updated_at']
    ordering = ['-last_occurrence']


@admin.register(SystemHealthCheck)
class SystemHealthCheckAdmin(admin.ModelAdmin):
    list_display = ['check_time', 'overall_status', 'health_score', 'database_status', 'api_status']
    list_filter = ['overall_status', 'check_time']
    readonly_fields = ['id', 'check_time', 'created_at', 'updated_at']
    ordering = ['-check_time']

    
    def has_change_permission(self, request, obj=None):
        return False
