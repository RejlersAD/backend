from django.contrib import admin
from .models import UserUsageLog, DepartmentUsageSummary, FeatureUsageSummary


@admin.register(UserUsageLog)
class UserUsageLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'department', 'feature_name', 'api_endpoint', 
                    'tokens_used', 'processing_time', 'status', 'timestamp')
    list_filter = ('status', 'feature_name', 'department', 'request_type', 'timestamp')
    search_fields = ('user__username', 'user__email', 'department', 'feature_name', 'api_endpoint')
    readonly_fields = ('timestamp',)
    date_hierarchy = 'timestamp'
    ordering = ('-timestamp',)
    
    def has_add_permission(self, request):
        """Prevent manual creation - logs are auto-generated"""
        return False


@admin.register(DepartmentUsageSummary)
class DepartmentUsageSummaryAdmin(admin.ModelAdmin):
    list_display = ('department', 'total_requests', 'total_tokens', 'total_users', 
                    'avg_processing_time', 'last_updated')
    list_filter = ('department', 'last_updated')
    search_fields = ('department',)
    readonly_fields = ('last_updated',)
    ordering = ('-total_requests',)
    
    def has_add_permission(self, request):
        """Prevent manual creation - summaries are auto-generated"""
        return False


@admin.register(FeatureUsageSummary)
class FeatureUsageSummaryAdmin(admin.ModelAdmin):
    list_display = ('feature_name', 'total_requests', 'total_tokens', 'total_users',
                    'avg_processing_time', 'last_updated')
    list_filter = ('feature_name', 'last_updated')
    search_fields = ('feature_name',)
    readonly_fields = ('last_updated',)
    ordering = ('-total_requests',)
    
    def has_add_permission(self, request):
        """Prevent manual creation - summaries are auto-generated"""
        return False
