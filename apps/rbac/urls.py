"""
RBAC URL Configuration
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    OrganizationViewSet, ModuleViewSet, PermissionViewSet,
    RoleViewSet, UserProfileViewSet, AuditLogViewSet, StorageViewSet,
    AccessRequestViewSet,
    # Analytics ViewSets
    AnalyticsDashboardViewSet, SystemMetricsViewSet, UserActivityAnalyticsViewSet,
    SecurityAlertViewSet, PredictiveInsightViewSet, FeatureUsageAnalyticsViewSet,
    ErrorLogAnalyticsViewSet, SystemHealthCheckViewSet,
    UserExportView,
    # Enhanced Profile ViewSets
    AchievementViewSet, WorkExperienceViewSet, SocialMediaLinkViewSet, ProfileDocumentViewSet,
)
from .dashboard_views import (
    user_dashboard_stats, user_files_list, user_activity_timeline
)
from .ai_champion_views import AIChampionViewSet
from apps.users.views_password import change_password
from .views_admin import provision_all_profiles, check_profile_status

router = DefaultRouter()
# RBAC Core
router.register(r'organizations', OrganizationViewSet, basename='organization')
router.register(r'modules', ModuleViewSet, basename='module')
router.register(r'permissions', PermissionViewSet, basename='permission')
router.register(r'roles', RoleViewSet, basename='role')
router.register(r'users', UserProfileViewSet, basename='user')
router.register(r'audit-logs', AuditLogViewSet, basename='audit-log')
router.register(r'storage', StorageViewSet, basename='storage')
router.register(r'access-requests', AccessRequestViewSet, basename='access-request')

# AI-Powered Analytics
router.register(r'analytics/dashboard', AnalyticsDashboardViewSet, basename='analytics-dashboard')
router.register(r'analytics/system-metrics', SystemMetricsViewSet, basename='system-metrics')
router.register(r'analytics/user-activity', UserActivityAnalyticsViewSet, basename='user-activity')
router.register(r'analytics/security-alerts', SecurityAlertViewSet, basename='security-alerts')
router.register(r'analytics/predictions', PredictiveInsightViewSet, basename='predictions')
router.register(r'analytics/feature-usage', FeatureUsageAnalyticsViewSet, basename='feature-usage')
router.register(r'analytics/error-logs', ErrorLogAnalyticsViewSet, basename='error-logs')
router.register(r'analytics/health-checks', SystemHealthCheckViewSet, basename='health-checks')

# AI Champion of the Month — gamification, tracking, cost analytics
router.register(r'ai-champion', AIChampionViewSet, basename='ai-champion')

# Enhanced User Profile — Achievements, Experience, Social Media Links, Documents
router.register(r'achievements', AchievementViewSet, basename='achievement')
router.register(r'work-experience', WorkExperienceViewSet, basename='work-experience')
router.register(r'social-links', SocialMediaLinkViewSet, basename='social-link')
router.register(r'profile-documents', ProfileDocumentViewSet, basename='profile-document')

urlpatterns = [
    # User Export — must come BEFORE router.urls to prevent users/{pk}/ swallowing 'export' as a pk
    path('users/export/', UserExportView.as_view(), name='user-export-users'),
    path('', include(router.urls)),
    # User Dashboard endpoints
    path('dashboard/stats/', user_dashboard_stats, name='user-dashboard-stats'),
    path('dashboard/files/', user_files_list, name='user-files-list'),
    path('dashboard/activity/', user_activity_timeline, name='user-activity-timeline'),
    # Password management
    path('users/change-password/', change_password, name='rbac-change-password'),
    # Admin endpoints for system maintenance
    path('admin/provision-profiles/', provision_all_profiles, name='admin-provision-profiles'),
    path('admin/profile-status/', check_profile_status, name='admin-profile-status'),
    # Subscription Management (7.3)
    path('subscriptions/', include('apps.rbac.subscription_urls')),
]
