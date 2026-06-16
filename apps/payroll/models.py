"""
Payroll Intelligence Platform — Database Models
================================================
Intelligence layer on top of apps.finance payroll models.
Adds Validation Logs, Audit Alerts, Project Cost Allocation,
AI Insight Snapshots, and Chatbot Message history.

All monetary fields use Decimal (never float).
All PKs are UUID for portability.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import models

from apps.finance.salary_models import EmployeeSalaryInfo, PayrollRun, SalarySlip

User = get_user_model()


# ─────────────────────────────────────────────────────────────────────────────
# Shared choices — soft-coded as TextChoices so they appear in the admin and
# are never magic strings scattered across the codebase.
# ─────────────────────────────────────────────────────────────────────────────

class Severity(models.TextChoices):
    ERROR   = 'error',    'Error'
    WARNING = 'warning',  'Warning'
    INFO    = 'info',     'Info'


class AlertType(models.TextChoices):
    SALARY_SPIKE       = 'salary_spike',       'Salary Spike'
    NEW_EMPLOYEE       = 'new_employee',        'New Employee'
    MISSING_EMPLOYEE   = 'missing_employee',    'Missing Employee'
    OVERTIME_EXCESS    = 'overtime_excess',     'Overtime Excess'
    NEGATIVE_SALARY    = 'negative_salary',     'Negative Salary'
    DUPLICATE_PAYMENT  = 'duplicate_payment',   'Duplicate Payment Risk'


class AlertSeverity(models.TextChoices):
    LOW      = 'low',      'Low'
    MEDIUM   = 'medium',   'Medium'
    HIGH     = 'high',     'High'
    CRITICAL = 'critical', 'Critical'


class AlertStatus(models.TextChoices):
    OPEN         = 'open',         'Open'
    ACKNOWLEDGED = 'acknowledged', 'Acknowledged'
    RESOLVED     = 'resolved',     'Resolved'


class InsightType(models.TextChoices):
    ATTENDANCE_ANOMALY  = 'attendance_anomaly',  'Attendance Anomaly'
    MISSING_TIMESHEET   = 'missing_timesheet',   'Missing Timesheet'
    OVERTIME_ALERT      = 'overtime_alert',      'Overtime Alert'
    PAYROLL_RISK        = 'payroll_risk',         'Payroll Risk Score'
    BURNOUT_RISK        = 'burnout_risk',         'Burnout Risk'
    PRODUCTIVITY_TREND  = 'productivity_trend',  'Productivity Trend'
    PAYROLL_FORECAST    = 'payroll_forecast',     'Payroll Forecast'


class ChatPersona(models.TextChoices):
    EMPLOYEE = 'employee', 'Employee'
    MANAGER  = 'manager',  'Manager'
    FINANCE  = 'finance',  'Finance'
    HR       = 'hr',       'HR'


class ChatRole(models.TextChoices):
    USER      = 'user',      'User'
    ASSISTANT = 'assistant', 'Assistant'


# ─────────────────────────────────────────────────────────────────────────────
# 1. PayrollValidationLog
# ─────────────────────────────────────────────────────────────────────────────

class PayrollValidationLog(models.Model):
    """
    Stores rule-engine findings for a given payroll run.
    Created by the ValidationEngine (both client-side rules surfaced via API
    and server-side aggregate checks). Resolved findings are kept for audit.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payroll_run = models.ForeignKey(
        PayrollRun,
        on_delete=models.CASCADE,
        related_name='validation_logs',
    )
    # Nullable — a finding may be run-level (e.g. "missing employees") rather
    # than per-employee.
    employee_salary_info = models.ForeignKey(
        EmployeeSalaryInfo,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='validation_logs',
    )
    rule_id      = models.CharField(max_length=64)
    rule_label   = models.CharField(max_length=255)
    severity     = models.CharField(max_length=20, choices=Severity.choices, default=Severity.WARNING)
    description  = models.TextField()
    suggested_action = models.TextField(blank=True)
    is_resolved  = models.BooleanField(default=False)
    resolved_by  = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='resolved_validations',
    )
    resolved_at  = models.DateTimeField(null=True, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table    = 'payroll_validation_log'
        ordering    = ['-created_at', 'severity']
        indexes     = [
            models.Index(fields=['payroll_run', 'severity']),
            models.Index(fields=['payroll_run', 'is_resolved']),
        ]

    def __str__(self):
        return f'{self.rule_label} [{self.severity}] — run {self.payroll_run_id}'


# ─────────────────────────────────────────────────────────────────────────────
# 2. PayrollAuditAlert
# ─────────────────────────────────────────────────────────────────────────────

class PayrollAuditAlert(models.Model):
    """
    Detected anomalies produced by the Payroll Auditor engine.
    Compares payroll_run with compared_to_run (previous cycle) and flags
    any change that exceeds soft-coded thresholds.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payroll_run = models.ForeignKey(
        PayrollRun,
        on_delete=models.CASCADE,
        related_name='audit_alerts',
    )
    compared_to_run = models.ForeignKey(
        PayrollRun,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='compared_alerts',
    )
    employee_salary_info = models.ForeignKey(
        EmployeeSalaryInfo,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_alerts',
    )
    alert_type      = models.CharField(max_length=32, choices=AlertType.choices)
    severity        = models.CharField(max_length=20, choices=AlertSeverity.choices, default=AlertSeverity.MEDIUM)
    change_percent  = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    previous_value  = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    current_value   = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    root_cause      = models.TextField(blank=True)
    suggested_action = models.TextField(blank=True)
    status          = models.CharField(max_length=20, choices=AlertStatus.choices, default=AlertStatus.OPEN)
    acknowledged_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='acknowledged_alerts',
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'payroll_audit_alert'
        ordering = ['-created_at', 'severity']
        indexes  = [
            models.Index(fields=['payroll_run', 'status']),
            models.Index(fields=['payroll_run', 'alert_type']),
        ]

    def __str__(self):
        return f'{self.get_alert_type_display()} [{self.severity}] — run {self.payroll_run_id}'


# ─────────────────────────────────────────────────────────────────────────────
# 3. ProjectCostAllocation
# ─────────────────────────────────────────────────────────────────────────────

class ProjectCostAllocation(models.Model):
    """
    Maps a fraction of a SalarySlip's cost to a project / cost center.
    Supports multi-project allocation per employee per period.
    allocation_percent values for one slip should sum to 100.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    salary_slip = models.ForeignKey(
        SalarySlip,
        on_delete=models.CASCADE,
        related_name='cost_allocations',
    )
    project_code   = models.CharField(max_length=64)
    project_name   = models.CharField(max_length=255, blank=True)
    cost_center    = models.CharField(max_length=64, blank=True)
    allocated_hours  = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0'))
    allocation_percent = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        help_text='Percentage of salary allocated to this project (0–100)',
    )
    allocated_cost = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    currency       = models.CharField(max_length=3, default='AED')
    month          = models.PositiveSmallIntegerField()
    year           = models.PositiveSmallIntegerField()
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'payroll_project_cost_allocation'
        ordering = ['year', 'month', 'project_code']
        indexes  = [
            models.Index(fields=['project_code', 'year', 'month']),
            models.Index(fields=['salary_slip']),
        ]

    def __str__(self):
        return f'{self.project_code} — {self.allocation_percent}% — {self.month}/{self.year}'


# ─────────────────────────────────────────────────────────────────────────────
# 4. AIInsightSnapshot
# ─────────────────────────────────────────────────────────────────────────────

class AIInsightSnapshot(models.Model):
    """
    Cached result of a rule-based AI insight computation for one employee,
    one month/year, one insight type. Expires after TTL so the client knows
    to trigger a refresh.  The intelligence is computed in the frontend rule
    engine and POSTed here for persistence and manager visibility.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee_salary_info = models.ForeignKey(
        EmployeeSalaryInfo,
        on_delete=models.CASCADE,
        related_name='ai_insights',
    )
    insight_type = models.CharField(max_length=32, choices=InsightType.choices)
    severity     = models.CharField(max_length=20, choices=Severity.choices, default=Severity.INFO)
    title        = models.CharField(max_length=255)
    description  = models.TextField()
    # Optional numeric value (e.g. risk score 0-100, hours, percentage)
    value        = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    metadata     = models.JSONField(default=dict, blank=True)
    month        = models.PositiveSmallIntegerField()
    year         = models.PositiveSmallIntegerField()
    computed_at  = models.DateTimeField(auto_now_add=True)
    expires_at   = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'payroll_ai_insight_snapshot'
        unique_together = ('employee_salary_info', 'month', 'year', 'insight_type')
        ordering = ['-year', '-month', 'insight_type']
        indexes  = [
            models.Index(fields=['employee_salary_info', 'year', 'month']),
        ]

    def __str__(self):
        return f'{self.get_insight_type_display()} — {self.employee_salary_info_id} {self.month}/{self.year}'


# ─────────────────────────────────────────────────────────────────────────────
# 5. ChatbotMessage
# ─────────────────────────────────────────────────────────────────────────────

class ChatbotMessage(models.Model):
    """
    Payroll AI Assistant conversation history.
    Grouped by session_id (UUID generated client-side per conversation).
    Persona controls which data scope is available to the rule engine.
    """
    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='payroll_chat_messages')
    session_id = models.UUIDField(db_index=True)
    role       = models.CharField(max_length=16, choices=ChatRole.choices)
    content    = models.TextField()
    intent     = models.CharField(max_length=64, blank=True, help_text='Matched intent key from rule engine')
    data_payload = models.JSONField(default=dict, blank=True, help_text='Structured data returned with response')
    persona    = models.CharField(max_length=16, choices=ChatPersona.choices, default=ChatPersona.EMPLOYEE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'payroll_chatbot_message'
        ordering = ['session_id', 'created_at']
        indexes  = [
            models.Index(fields=['user', 'session_id']),
            models.Index(fields=['session_id', 'created_at']),
        ]

    def __str__(self):
        return f'[{self.role}] {self.content[:60]}'


# ─────────────────────────────────────────────────────────────────────────────
# 6. EmployeeLeaveRecord  — annual summary per employee per year
# ─────────────────────────────────────────────────────────────────────────────

class EmployeeLeaveRecord(models.Model):
    """
    One row per employee per year imported from the HR leave Excel.
    Stores: employee identity, annual entitlement, and year-to-date totals.
    Uniqueness is on (employee_code, year) so re-importing updates in place.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Employee identity — kept as plain text because these employees may not
    # all have RAD AI user accounts.
    employee_code       = models.CharField(max_length=30, null=True, blank=True, db_index=True)
    employee_name       = models.CharField(max_length=255, db_index=True)
    department          = models.CharField(max_length=100, null=True, blank=True)
    job_title           = models.CharField(max_length=255, null=True, blank=True)
    joining_date        = models.DateField(null=True, blank=True)

    # Leave entitlement — configurable per company policy
    annual_entitlement  = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('22'))

    # Year this record covers
    year                = models.PositiveSmallIntegerField(default=2026)

    # Year-to-date aggregates (from the "Total Leave Balance" row in the Excel)
    total_earned        = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal('0'))
    total_taken         = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal('0'))
    total_encashed      = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal('0'))
    leave_balance       = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal('0'))

    # Carryforward from previous year
    carryforward        = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal('0'))

    # Source tracking
    source_file         = models.CharField(max_length=500, blank=True)
    imported_at         = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'payroll_employee_leave_record'
        unique_together = ('employee_code', 'year')
        ordering        = ['employee_name']
        indexes         = [
            models.Index(fields=['year', 'department']),
            models.Index(fields=['employee_code']),
        ]

    def __str__(self):
        return f'{self.employee_name} ({self.year}) — bal:{self.leave_balance}'


# ─────────────────────────────────────────────────────────────────────────────
# 7. EmployeeLeaveMonthly  — per-month breakdown
# ─────────────────────────────────────────────────────────────────────────────

MONTH_CHOICES = [
    (1,'January'),(2,'February'),(3,'March'),(4,'April'),
    (5,'May'),(6,'June'),(7,'July'),(8,'August'),
    (9,'September'),(10,'October'),(11,'November'),(12,'December'),
]

class EmployeeLeaveMonthly(models.Model):
    """
    Monthly leave breakdown for one EmployeeLeaveRecord.
    One row per (record, month).
    """
    id       = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    record   = models.ForeignKey(
        EmployeeLeaveRecord,
        on_delete=models.CASCADE,
        related_name='monthly_breakdown',
    )
    month    = models.PositiveSmallIntegerField(choices=MONTH_CHOICES)
    earned   = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal('0'))
    taken    = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal('0'))
    encashed = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal('0'))
    balance  = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal('0'))

    class Meta:
        db_table        = 'payroll_employee_leave_monthly'
        unique_together = ('record', 'month')
        ordering        = ['record', 'month']

    def __str__(self):
        return f'{self.record.employee_name} — {self.get_month_display()} {self.record.year}'
