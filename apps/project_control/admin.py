from django.contrib import admin

from .models import (
    ApprovedHourEntry,
    ChangeEvent,
    ControlAccount,
    IntegratedReportingSnapshot,
    ReconciliationRun,
    CostSnapshot,
    Estimate,
    EstimateLineItem,
    ProjectDocument,
    ReportingPeriod,
    ReportingPeriodAudit,
    WBSNode,
)


@admin.register(Estimate)
class EstimateAdmin(admin.ModelAdmin):
    list_display = ('project', 'kind', 'version', 'status', 'source', 'total_amount', 'currency', 'snapshot_date')
    list_filter = ('kind', 'status', 'source')
    search_fields = ('project__code', 'project__name', 'title')
    autocomplete_fields = ('project',)


@admin.register(EstimateLineItem)
class EstimateLineItemAdmin(admin.ModelAdmin):
    list_display = ('estimate', 'wbs_code', 'discipline', 'quantity', 'unit_rate', 'line_total')
    search_fields = ('estimate__project__code', 'wbs_code', 'description')
    list_filter = ('discipline',)


@admin.register(WBSNode)
class WBSNodeAdmin(admin.ModelAdmin):
    list_display = ('project', 'code', 'name', 'level', 'parent')
    search_fields = ('project__code', 'code', 'name')


@admin.register(ControlAccount)
class ControlAccountAdmin(admin.ModelAdmin):
    list_display = ('project', 'code', 'name', 'manager', 'status', 'baseline_start', 'baseline_finish')
    list_filter = ('status', 'earned_value_method')
    search_fields = ('project__code', 'code', 'name', 'manager__email')
    readonly_fields = ('created_by', 'submitted_by', 'submitted_at', 'approved_by', 'approved_at', 'closed_by', 'closed_at')


@admin.register(ReportingPeriod)
class ReportingPeriodAdmin(admin.ModelAdmin):
    list_display = ('project', 'sequence', 'name', 'data_date', 'status', 'submitted_by', 'locked_by')
    list_filter = ('status',)
    search_fields = ('project__code', 'name')
    readonly_fields = ('created_by', 'submitted_by', 'submitted_at', 'locked_by', 'locked_at', 'reopened_by', 'reopened_at', 'reopen_reason')


@admin.register(ReportingPeriodAudit)
class ReportingPeriodAuditAdmin(admin.ModelAdmin):
    list_display = ('period', 'action', 'from_status', 'to_status', 'actor', 'created_at')
    list_filter = ('action', 'to_status')
    search_fields = ('period__project__code', 'period__name', 'reason')
    readonly_fields = ('period', 'actor', 'action', 'from_status', 'to_status', 'reason', 'created_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ApprovedHourEntry)
class ApprovedHourEntryAdmin(admin.ModelAdmin):
    list_display = ('project', 'reporting_period', 'control_account', 'employee_code', 'work_date', 'hours', 'labor_actual_cost', 'status')
    list_filter = ('status', 'source_type', 'currency')
    search_fields = ('project__code', 'employee_code', 'employee_name', 'source_reference')


@admin.register(ReconciliationRun)
class ReconciliationRunAdmin(admin.ModelAdmin):
    list_display = ('project', 'reporting_period', 'run_number', 'status', 'ledger_actual_cost', 'exception_count', 'created_at')
    readonly_fields = [field.name for field in ReconciliationRun._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(IntegratedReportingSnapshot)
class IntegratedReportingSnapshotAdmin(admin.ModelAdmin):
    list_display = ('project', 'reporting_period', 'version', 'data_date', 'actual_cost', 'cpi', 'spi', 'sealed_at')
    readonly_fields = [field.name for field in IntegratedReportingSnapshot._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ProjectDocument)
class ProjectDocumentAdmin(admin.ModelAdmin):
    list_display = ('project', 'kind', 'original_filename', 'size_bytes', 'parse_status', 'uploaded_by', 'created_at')
    list_filter = ('kind', 'parse_status')
    search_fields = ('project__code', 'original_filename', 'title')
    readonly_fields = ('size_bytes', 'parse_status', 'parsed_data', 'parse_error')


@admin.register(CostSnapshot)
class CostSnapshotAdmin(admin.ModelAdmin):
    list_display = ('project', 'period_end', 'planned_value', 'earned_value', 'actual_cost', 'cpi', 'spi')
    list_filter = ('source',)
    search_fields = ('project__code',)


@admin.register(ChangeEvent)
class ChangeEventAdmin(admin.ModelAdmin):
    list_display = ('project', 'summary', 'severity', 'status', 'delta_amount', 'detected_at')
    list_filter = ('severity', 'status')
    search_fields = ('project__code', 'summary')
