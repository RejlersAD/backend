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
from .subscription_models import (
    SubscriptionPlan, SubscriptionFeature, UserSubscription,
    UsageTracking, SubscriptionHistory, SubscriptionInvoice
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


# ============================================================================
# SUBSCRIPTION MODELS ADMIN - DISABLED FOR IN-HOUSE DEPLOYMENT
# SOFT-CODED: Subscription feature not needed for internal use
# ============================================================================

# @admin.register(SubscriptionPlan)
# class SubscriptionPlanAdmin(admin.ModelAdmin):
#     list_display = ['display_name', 'code', 'plan_type', 'price', 'billing_cycle', 'is_active', 'is_public', 'sort_order']
#     list_filter = ['plan_type', 'billing_cycle', 'is_active', 'is_public']
#     search_fields = ['name', 'code', 'display_name', 'description']
#     ordering = ['sort_order', 'price']
#     fieldsets = (
#         ('Basic Information', {
#             'fields': ('name', 'code', 'display_name', 'description', 'plan_type')
#         }),
#         ('Pricing', {
#             'fields': ('billing_cycle', 'price', 'currency', 'trial_days')
#         }),
#         ('Limits', {
#             'fields': ('max_users', 'max_storage_gb', 'max_api_calls_per_day', 'max_projects', 'max_documents')
#         }),
#         ('Support & Priority', {
#             'fields': ('priority_level', 'support_level')
#         }),
#         ('Features & Modules', {
#             'fields': ('features', 'allowed_modules')
#         }),
#         ('Display', {
#             'fields': ('badge', 'color_scheme', 'icon', 'sort_order')
#         }),
#         ('Status', {
#             'fields': ('is_active', 'is_public', 'is_default')
#         }),
#     )


# @admin.register(SubscriptionFeature)
# class SubscriptionFeatureAdmin(admin.ModelAdmin):
#     list_display = ['name', 'code', 'feature_type', 'category', 'is_highlighted', 'is_active', 'sort_order']
#     list_filter = ['feature_type', 'category', 'is_highlighted', 'is_active']
#     search_fields = ['name', 'code', 'description']
#     ordering = ['category', 'sort_order', 'name']


# @admin.register(UserSubscription)
# class UserSubscriptionAdmin(admin.ModelAdmin):
#     list_display = ['user_email', 'plan_name', 'status', 'start_date', 'end_date', 'days_remaining_display', 'auto_renew']
#     list_filter = ['status', 'plan', 'auto_renew', 'start_date']
#     search_fields = ['user__email', 'user__first_name', 'user__last_name', 'plan__name']
#     readonly_fields = ['created_at', 'updated_at', 'days_remaining_display', 'is_trial_display', 'is_expired_display']
#     ordering = ['-created_at']
#     
#     fieldsets = (
#         ('User & Plan', {
#             'fields': ('user', 'plan')
#         }),
#         ('Subscription Period', {
#             'fields': ('status', 'start_date', 'end_date', 'trial_end_date', 'days_remaining_display', 'is_trial_display', 'is_expired_display')
#         }),
#         ('Billing', {
#             'fields': ('is_paid', 'payment_method', 'last_payment_date', 'next_billing_date', 'auto_renew')
#         }),
#         ('Custom Overrides', {
#             'fields': ('custom_limits', 'custom_features'),
#             'classes': ('collapse',)
#         }),
#         ('Management', {
#             'fields': ('granted_by', 'notes', 'metadata'),
#             'classes': ('collapse',)
#         }),
#         ('Cancellation', {
#             'fields': ('cancelled_at', 'cancellation_reason'),
#             'classes': ('collapse',)
#         }),
#     )
#     
#     def user_email(self, obj):
#         return obj.user.email
#     user_email.short_description = 'User'
#     
#     def plan_name(self, obj):
#         return obj.plan.display_name
#     plan_name.short_description = 'Plan'
#     
#     def days_remaining_display(self, obj):
#         return f"{obj.days_remaining} days" if obj.days_remaining else "N/A"
#     days_remaining_display.short_description = 'Days Remaining'
#     
#     def is_trial_display(self, obj):
#         return obj.is_trial
#     is_trial_display.boolean = True
#     is_trial_display.short_description = 'Trial'
#     
#     def is_expired_display(self, obj):
#         return obj.is_expired
#     is_expired_display.boolean = True
#     is_expired_display.short_description = 'Expired'


# @admin.register(UsageTracking)
# class UsageTrackingAdmin(admin.ModelAdmin):
#     list_display = ['subscription_user', 'metric_type', 'period', 'period_start', 'usage_count', 'limit_value', 'usage_percentage_display', 'is_over_limit']
#     list_filter = ['metric_type', 'period', 'is_over_limit', 'period_start']
#     search_fields = ['subscription__user__email']
#     readonly_fields = ['usage_percentage_display']
#     ordering = ['-period_start']
#     
#     def subscription_user(self, obj):
#         return obj.subscription.user.email
#     subscription_user.short_description = 'User'
#     
#     def usage_percentage_display(self, obj):
#         return f"{obj.usage_percentage:.1f}%"
#     usage_percentage_display.short_description = 'Usage %'


# @admin.register(SubscriptionHistory)
# class SubscriptionHistoryAdmin(admin.ModelAdmin):
#     list_display = ['subscription_user', 'action', 'old_plan_name', 'new_plan_name', 'performed_by_email', 'created_at']
#     list_filter = ['action', 'created_at']
#     search_fields = ['subscription__user__email', 'performed_by__email']
#     readonly_fields = ['created_at', 'updated_at', 'subscription', 'action', 'old_plan', 'new_plan', 'performed_by', 'changes', 'ip_address']
#     ordering = ['-created_at']
#     
#     def subscription_user(self, obj):
#         return obj.subscription.user.email
#     subscription_user.short_description = 'User'
#     
#     def old_plan_name(self, obj):
#         return obj.old_plan.display_name if obj.old_plan else 'N/A'
#     old_plan_name.short_description = 'Old Plan'
#     
#     def new_plan_name(self, obj):
#         return obj.new_plan.display_name if obj.new_plan else 'N/A'
#     new_plan_name.short_description = 'New Plan'
#     
#     def performed_by_email(self, obj):
#         return obj.performed_by.email if obj.performed_by else 'System'
#     performed_by_email.short_description = 'Performed By'
#     
#     def has_add_permission(self, request):
#         return False


# @admin.register(SubscriptionInvoice)
# class SubscriptionInvoiceAdmin(admin.ModelAdmin):
#     list_display = ['invoice_number', 'subscription_user', 'plan_name', 'total', 'currency', 'status', 'issue_date', 'due_date', 'paid_date']
#     list_filter = ['status', 'issue_date', 'due_date', 'payment_gateway']
#     search_fields = ['invoice_number', 'subscription__user__email', 'transaction_id']
#     readonly_fields = ['created_at', 'updated_at']
#     ordering = ['-issue_date']
#     
#     fieldsets = (
#         ('Invoice Information', {
#             'fields': ('invoice_number', 'subscription', 'status')
#         }),
#         ('Amounts', {
#             'fields': ('subtotal', 'tax', 'discount', 'total', 'currency')
#         }),
#         ('Dates', {
#             'fields': ('issue_date', 'due_date', 'paid_date')
#         }),
#         ('Payment Details', {
#             'fields': ('payment_method', 'transaction_id', 'payment_gateway')
#         }),
#         ('Line Items & Notes', {
#             'fields': ('line_items', 'notes', 'metadata'),
#             'classes': ('collapse',)
#         }),
#     )
#     
#     def subscription_user(self, obj):
#         return obj.subscription.user.email
#     subscription_user.short_description = 'User'
#     
#     def plan_name(self, obj):
#         return obj.subscription.plan.display_name
#     plan_name.short_description = 'Plan'

