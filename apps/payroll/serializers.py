"""
Payroll Intelligence — Serializers
"""
from rest_framework import serializers
from django.contrib.auth import get_user_model

from .models import (
    PayrollValidationLog,
    PayrollAuditAlert,
    ProjectCostAllocation,
    AIInsightSnapshot,
    ChatbotMessage,
)

User = get_user_model()


class PayrollValidationLogSerializer(serializers.ModelSerializer):
    employee_name = serializers.SerializerMethodField()
    resolved_by_name = serializers.SerializerMethodField()

    class Meta:
        model  = PayrollValidationLog
        fields = [
            'id', 'payroll_run', 'employee_salary_info', 'employee_name',
            'rule_id', 'rule_label', 'severity', 'description',
            'suggested_action', 'is_resolved', 'resolved_by',
            'resolved_by_name', 'resolved_at', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']

    def get_employee_name(self, obj):
        if obj.employee_salary_info:
            u = obj.employee_salary_info.user
            return f'{u.first_name} {u.last_name}'.strip() or u.email
        return None

    def get_resolved_by_name(self, obj):
        if obj.resolved_by:
            return f'{obj.resolved_by.first_name} {obj.resolved_by.last_name}'.strip() or obj.resolved_by.email
        return None


class PayrollAuditAlertSerializer(serializers.ModelSerializer):
    employee_name       = serializers.SerializerMethodField()
    alert_type_display  = serializers.CharField(source='get_alert_type_display', read_only=True)
    severity_display    = serializers.CharField(source='get_severity_display', read_only=True)
    status_display      = serializers.CharField(source='get_status_display', read_only=True)
    acknowledged_by_name = serializers.SerializerMethodField()

    class Meta:
        model  = PayrollAuditAlert
        fields = [
            'id', 'payroll_run', 'compared_to_run', 'employee_salary_info',
            'employee_name', 'alert_type', 'alert_type_display',
            'severity', 'severity_display', 'change_percent',
            'previous_value', 'current_value', 'root_cause',
            'suggested_action', 'status', 'status_display',
            'acknowledged_by', 'acknowledged_by_name', 'acknowledged_at',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']

    def get_employee_name(self, obj):
        if obj.employee_salary_info:
            u = obj.employee_salary_info.user
            return f'{u.first_name} {u.last_name}'.strip() or u.email
        return None

    def get_acknowledged_by_name(self, obj):
        if obj.acknowledged_by:
            return f'{obj.acknowledged_by.first_name} {obj.acknowledged_by.last_name}'.strip()
        return None


class ProjectCostAllocationSerializer(serializers.ModelSerializer):
    class Meta:
        model  = ProjectCostAllocation
        fields = [
            'id', 'salary_slip', 'project_code', 'project_name',
            'cost_center', 'allocated_hours', 'allocation_percent',
            'allocated_cost', 'currency', 'month', 'year',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class AIInsightSnapshotSerializer(serializers.ModelSerializer):
    insight_type_display = serializers.CharField(source='get_insight_type_display', read_only=True)
    severity_display     = serializers.CharField(source='get_severity_display', read_only=True)
    employee_name        = serializers.SerializerMethodField()

    class Meta:
        model  = AIInsightSnapshot
        fields = [
            'id', 'employee_salary_info', 'employee_name',
            'insight_type', 'insight_type_display',
            'severity', 'severity_display',
            'title', 'description', 'value', 'metadata',
            'month', 'year', 'computed_at', 'expires_at',
        ]
        read_only_fields = ['id', 'computed_at']

    def get_employee_name(self, obj):
        u = obj.employee_salary_info.user
        return f'{u.first_name} {u.last_name}'.strip() or u.email


class ChatbotMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model  = ChatbotMessage
        fields = [
            'id', 'user', 'session_id', 'role', 'content',
            'intent', 'data_payload', 'persona', 'created_at',
        ]
        read_only_fields = ['id', 'user', 'created_at']


# ──────────────────────────────────────────────────────────────────────────────
# Leave Record serializers
# ──────────────────────────────────────────────────────────────────────────────
from .models import EmployeeLeaveRecord, EmployeeLeaveMonthly  # noqa: E402


class EmployeeLeaveMonthlySerializer(serializers.ModelSerializer):
    month_label = serializers.CharField(source='get_month_display', read_only=True)

    class Meta:
        model  = EmployeeLeaveMonthly
        fields = ['id', 'month', 'month_label', 'earned', 'taken', 'encashed', 'balance']
        read_only_fields = ['id']


class EmployeeLeaveRecordSerializer(serializers.ModelSerializer):
    monthly_breakdown = EmployeeLeaveMonthlySerializer(many=True, read_only=True)
    branch_display    = serializers.CharField(source='get_branch_display', read_only=True)

    class Meta:
        model  = EmployeeLeaveRecord
        fields = [
            'id', 'employee_code', 'employee_name', 'department',
            'job_title', 'joining_date', 'annual_entitlement', 'year',
            'branch', 'branch_display',
            'total_earned', 'total_taken', 'total_encashed', 'leave_balance',
            'carryforward', 'source_file', 'imported_at', 'monthly_breakdown',
        ]
        read_only_fields = ['id', 'imported_at']


class EmployeeLeaveRecordListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list view (no monthly breakdown)."""
    branch_display = serializers.CharField(source='get_branch_display', read_only=True)

    class Meta:
        model  = EmployeeLeaveRecord
        fields = [
            'id', 'employee_code', 'employee_name', 'department',
            'job_title', 'joining_date', 'annual_entitlement', 'year',
            'branch', 'branch_display',
            'total_earned', 'total_taken', 'total_encashed', 'leave_balance',
            'carryforward', 'imported_at',
        ]
        read_only_fields = ['id', 'imported_at']


# ────────────────────────────────────────────────────────────────────────────────
# Leave Type + Leave Request serializers
# ────────────────────────────────────────────────────────────────────────────────
from .models import LeaveType, LeaveRequest  # noqa: E402


class LeaveTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model  = LeaveType
        fields = [
            'id', 'code', 'name', 'color_hex',
            'badge_bg', 'badge_text', 'badge_border',
            'is_paid', 'requires_approval', 'requires_document',
            'is_active', 'display_order',
        ]
        read_only_fields = ['id']


class LeaveRequestSerializer(serializers.ModelSerializer):
    leave_type_detail = LeaveTypeSerializer(source='leave_type', read_only=True)
    status_display    = serializers.CharField(source='get_status_display', read_only=True)
    reviewed_by_name  = serializers.SerializerMethodField()

    class Meta:
        model  = LeaveRequest
        fields = [
            'id', 'employee', 'employee_code', 'employee_name', 'department',
            'leave_type', 'leave_type_detail',
            'start_date', 'end_date', 'days_requested', 'reason',
            'status', 'status_display',
            'reviewed_by', 'reviewed_by_name', 'reviewed_at', 'reviewer_note',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'days_requested', 'status', 'status_display',
            'reviewed_by', 'reviewed_by_name', 'reviewed_at',
            'created_at', 'updated_at', 'leave_type_detail',
        ]

    def get_reviewed_by_name(self, obj):
        if obj.reviewed_by:
            return (
                f'{obj.reviewed_by.first_name} {obj.reviewed_by.last_name}'.strip()
                or obj.reviewed_by.email
            )
        return None
