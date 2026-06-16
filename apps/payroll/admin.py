from django.contrib import admin
from .models import (
    PayrollValidationLog,
    PayrollAuditAlert,
    ProjectCostAllocation,
    AIInsightSnapshot,
    ChatbotMessage,
)


@admin.register(PayrollValidationLog)
class PayrollValidationLogAdmin(admin.ModelAdmin):
    list_display  = ('rule_label', 'severity', 'payroll_run', 'is_resolved', 'created_at')
    list_filter   = ('severity', 'is_resolved')
    search_fields = ('rule_id', 'rule_label', 'description')
    raw_id_fields = ('payroll_run', 'employee_salary_info', 'resolved_by')


@admin.register(PayrollAuditAlert)
class PayrollAuditAlertAdmin(admin.ModelAdmin):
    list_display  = ('alert_type', 'severity', 'status', 'payroll_run', 'change_percent', 'created_at')
    list_filter   = ('alert_type', 'severity', 'status')
    raw_id_fields = ('payroll_run', 'compared_to_run', 'employee_salary_info', 'acknowledged_by')


@admin.register(ProjectCostAllocation)
class ProjectCostAllocationAdmin(admin.ModelAdmin):
    list_display  = ('project_code', 'project_name', 'cost_center', 'allocation_percent', 'allocated_cost', 'month', 'year')
    list_filter   = ('year', 'month')
    search_fields = ('project_code', 'project_name', 'cost_center')
    raw_id_fields = ('salary_slip',)


@admin.register(AIInsightSnapshot)
class AIInsightSnapshotAdmin(admin.ModelAdmin):
    list_display  = ('insight_type', 'severity', 'title', 'employee_salary_info', 'month', 'year', 'computed_at')
    list_filter   = ('insight_type', 'severity', 'year', 'month')
    raw_id_fields = ('employee_salary_info',)


@admin.register(ChatbotMessage)
class ChatbotMessageAdmin(admin.ModelAdmin):
    list_display  = ('role', 'persona', 'user', 'intent', 'created_at')
    list_filter   = ('role', 'persona')
    search_fields = ('content', 'intent')
    raw_id_fields = ('user',)
