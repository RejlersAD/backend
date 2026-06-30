"""DRF serializers for the Payroll Engine."""
from __future__ import annotations
from rest_framework import serializers

from . import catalog
from .models import (
    PayrollAdjustment, PayrollEmployee, PayrollRun, Payslip, PayslipLineItem,
    PayrollWorkflowLog,
)


class PayrollEmployeeSerializer(serializers.ModelSerializer):
    default_gross = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = PayrollEmployee
        fields = [
            'id', 'employee_no', 'user', 'full_name', 'emirates_id', 'mol_no',
            'iban', 'bank_name', 'routing_code',
            'department', 'discipline', 'designation', 'grade', 'nationality_group',
            'joining_date', 'leaving_date',
            'basic', 'housing', 'transport', 'home_leave', 'default_gross',
            'default_payment_mode',
            'is_active', 'effective_from', 'effective_to',
            'notes', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'default_gross', 'created_at', 'updated_at']


class PayslipLineItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayslipLineItem
        fields = [
            'id', 'payslip', 'kind', 'component_code', 'label', 'description',
            'amount', 'source', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class PayslipSerializer(serializers.ModelSerializer):
    line_items = PayslipLineItemSerializer(many=True, read_only=True)
    employee_no = serializers.CharField(source='employee.employee_no', read_only=True)
    run_cycle = serializers.CharField(source='run.cycle_code', read_only=True)
    run_status = serializers.CharField(source='run.status', read_only=True)

    class Meta:
        model = Payslip
        fields = [
            'id', 'run', 'run_cycle', 'run_status', 'employee', 'employee_no',
            'basic', 'housing', 'transport', 'home_leave',
            'other_earnings', 'gross_earnings', 'total_deductions', 'net_payable',
            'payment_mode',
            'snapshot_full_name', 'snapshot_department', 'snapshot_designation',
            'snapshot_iban', 'snapshot_joining_date',
            'status', 'notes', 'line_items',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'run_cycle', 'run_status', 'employee_no', 'other_earnings',
            'gross_earnings', 'total_deductions', 'net_payable',
            'snapshot_full_name', 'snapshot_department', 'snapshot_designation',
            'snapshot_iban', 'snapshot_joining_date',
            'created_at', 'updated_at',
        ]


class PayrollRunSerializer(serializers.ModelSerializer):
    status_label = serializers.SerializerMethodField()

    class Meta:
        model = PayrollRun
        fields = [
            'id', 'year', 'month', 'cycle_code',
            'status', 'status_label',
            'employee_count', 'total_gross', 'total_deductions', 'total_net',
            'generated_at', 'hr_approved_at', 'finance_approved_at', 'released_at',
            'hr_approved_by', 'finance_approved_by', 'released_by',
            'notes', 'created_at', 'updated_at', 'created_by',
        ]
        read_only_fields = [
            'id', 'cycle_code', 'status', 'status_label',
            'employee_count', 'total_gross', 'total_deductions', 'total_net',
            'generated_at', 'hr_approved_at', 'finance_approved_at', 'released_at',
            'hr_approved_by', 'finance_approved_by', 'released_by',
            'created_at', 'updated_at', 'created_by',
        ]

    def get_status_label(self, obj):
        return catalog.status_meta(obj.status).get('label', obj.status)


class PayrollAdjustmentSerializer(serializers.ModelSerializer):
    employee_no = serializers.CharField(source='employee.employee_no', read_only=True)
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)

    class Meta:
        model = PayrollAdjustment
        fields = [
            'id', 'employee', 'employee_no', 'employee_name',
            'target_year', 'target_month',
            'kind', 'component_code', 'label', 'description', 'amount',
            'status', 'applied_to', 'applied_at',
            'created_at', 'updated_at', 'created_by',
        ]
        read_only_fields = [
            'id', 'employee_no', 'employee_name', 'applied_to', 'applied_at',
            'created_at', 'updated_at', 'created_by',
        ]


class PayrollWorkflowLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = PayrollWorkflowLog
        fields = [
            'id', 'run', 'from_status', 'to_status', 'actor', 'actor_name', 'note', 'at',
        ]
        read_only_fields = fields

    def get_actor_name(self, obj):
        if not obj.actor:
            return ''
        return getattr(obj.actor, 'get_full_name', lambda: '')() or getattr(obj.actor, 'username', '')


# ── Catalog serializer (one big read-only blob for the frontend) ────
class CatalogSerializer(serializers.Serializer):
    currency = serializers.CharField()
    workflow_statuses = serializers.ListField()
    workflow_transitions = serializers.DictField()
    payment_modes = serializers.ListField()
    fixed_earnings = serializers.ListField()
    earning_components = serializers.ListField()
    deduction_components = serializers.ListField()
    line_item_kinds = serializers.ListField()
    line_item_sources = serializers.ListField()
    adjustment_statuses = serializers.ListField()
    grade_options = serializers.ListField()
    nationality_groups = serializers.ListField()
