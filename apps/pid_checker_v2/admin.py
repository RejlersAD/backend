from django.contrib import admin

from .models import PidCheckerV2Project


@admin.register(PidCheckerV2Project)
class PidCheckerV2ProjectAdmin(admin.ModelAdmin):
    list_display = ('project_id', 'name', 'status', 'created_by', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('name', 'description', 'project_id')
    readonly_fields = ('project_id', 'created_at', 'updated_at')
