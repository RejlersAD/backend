from django.contrib import admin
from .models import UsageLog


@admin.register(UsageLog)
class UsageLogAdmin(admin.ModelAdmin):
    list_display = [
        'timestamp', 'user_email', 'discipline_label',
        'request_method', 'request_path', 'response_status', 'response_time_ms',
    ]
    list_filter  = ['discipline_key', 'request_method', 'success']
    search_fields = ['user_email', 'user_full_name', 'request_path']
    ordering = ['-timestamp']
    readonly_fields = [f.name for f in UsageLog._meta.fields]

    def has_add_permission(self, request):
        return False  # Read-only in admin
