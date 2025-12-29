"""
RBAC URL Configuration
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    OrganizationViewSet, ModuleViewSet, PermissionViewSet,
    RoleViewSet, UserProfileViewSet, AuditLogViewSet, StorageViewSet,
    # Analytics ViewSets
    AnalyticsDashboardViewSet, SystemMetricsViewSet, UserActivityAnalyticsViewSet,
    SecurityAlertViewSet, PredictiveInsightViewSet, FeatureUsageAnalyticsViewSet,
    ErrorLogAnalyticsViewSet, SystemHealthCheckViewSet
)
from .dashboard_views import (
    user_dashboard_stats, user_files_list, user_activity_timeline
)

router = DefaultRouter()
# RBAC Core
router.register(r'organizations', OrganizationViewSet, basename='organization')
router.register(r'modules', ModuleViewSet, basename='module')
router.register(r'permissions', PermissionViewSet, basename='permission')
router.register(r'roles', RoleViewSet, basename='role')
router.register(r'users', UserProfileViewSet, basename='user')
router.register(r'audit-logs', AuditLogViewSet, basename='audit-log')
router.register(r'storage', StorageViewSet, basename='storage')

# AI-Powered Analytics
router.register(r'analytics/dashboard', AnalyticsDashboardViewSet, basename='analytics-dashboard')
router.register(r'analytics/system-metrics', SystemMetricsViewSet, basename='system-metrics')
router.register(r'analytics/user-activity', UserActivityAnalyticsViewSet, basename='user-activity')
router.register(r'analytics/security-alerts', SecurityAlertViewSet, basename='security-alerts')
router.register(r'analytics/predictions', PredictiveInsightViewSet, basename='predictions')
router.register(r'analytics/feature-usage', FeatureUsageAnalyticsViewSet, basename='feature-usage')
router.register(r'analytics/error-logs', ErrorLogAnalyticsViewSet, basename='error-logs')
router.register(r'analytics/health-checks', SystemHealthCheckViewSet, basename='health-checks')

urlpatterns = [
    path('', include(router.urls)),
    # User Dashboard endpoints
    path('dashboard/stats/', user_dashboard_stats, name='user-dashboard-stats'),
    path('dashboard/files/', user_files_list, name='user-files-list'),
    path('dashboard/activity/', user_activity_timeline, name='user-activity-timeline'),
]
