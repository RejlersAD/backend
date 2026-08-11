"""
Payroll Audit Alert generation.

Compares a just-processed PayrollRun against the immediately preceding run
and writes PayrollAuditAlert rows (apps.payroll.models, table
`payroll_audit_alert`) for:
    1. Basic salary changed by more than 10% between runs
    2. New employee added to payroll
    3. Employee removed from payroll
    4. Basic salary is 0 or negative

This is a pure post-processing step, called after a PayrollRun's SalarySlips
have already been created — it never modifies payroll data and never raises,
so a failure here can never break payroll generation itself.

Severity mapping for salary changes (per spec): low <5%, medium 5-15%,
high >15%. New/missing-employee and negative-salary severities aren't
specified by the spec; low/medium/critical respectively were chosen as
reasonable defaults using the existing AlertSeverity choices.
"""
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)

SALARY_CHANGE_THRESHOLD_PERCENT = Decimal('10')


def _severity_for_change(change_percent):
    from apps.payroll.models import AlertSeverity
    magnitude = abs(change_percent)
    if magnitude > Decimal('15'):
        return AlertSeverity.HIGH
    if magnitude >= Decimal('5'):
        return AlertSeverity.MEDIUM
    return AlertSeverity.LOW


def generate_audit_alerts(payroll_run) -> int:
    """
    Create PayrollAuditAlert rows for `payroll_run` vs. its preceding run.
    Returns the number of alerts created. Never raises.
    """
    try:
        from apps.finance.salary_models import PayrollRun, SalarySlip
        from apps.payroll.models import AlertSeverity, AlertType, PayrollAuditAlert

        previous_run = (
            PayrollRun.objects
            .filter(period_start__lt=payroll_run.period_start)
            .order_by('-period_start')
            .first()
        )

        current_slips = {
            slip.employee_salary_info_id: slip
            for slip in SalarySlip.objects
            .filter(payroll_run=payroll_run)
            .select_related('employee_salary_info')
        }

        alerts = []

        # 4. Zero/negative basic salary — independent of a comparison run
        for emp_id, slip in current_slips.items():
            if slip.basic_salary is not None and slip.basic_salary <= 0:
                alerts.append(PayrollAuditAlert(
                    payroll_run=payroll_run,
                    compared_to_run=previous_run,
                    employee_salary_info_id=emp_id,
                    alert_type=AlertType.NEGATIVE_SALARY,
                    severity=AlertSeverity.CRITICAL,
                    current_value=slip.basic_salary,
                    root_cause=f'Basic salary is {slip.basic_salary} on run {payroll_run.run_code}.',
                    suggested_action="Verify the employee's salary structure before releasing this run.",
                ))

        if previous_run is not None:
            previous_slips = {
                slip.employee_salary_info_id: slip
                for slip in SalarySlip.objects
                .filter(payroll_run=previous_run)
                .select_related('employee_salary_info')
            }

            # 1. Salary changed by more than 10% vs. previous run
            for emp_id, slip in current_slips.items():
                prev_slip = previous_slips.get(emp_id)
                if not prev_slip or not prev_slip.basic_salary:
                    continue
                change = (slip.basic_salary - prev_slip.basic_salary) / prev_slip.basic_salary * Decimal('100')
                if abs(change) > SALARY_CHANGE_THRESHOLD_PERCENT:
                    alerts.append(PayrollAuditAlert(
                        payroll_run=payroll_run,
                        compared_to_run=previous_run,
                        employee_salary_info_id=emp_id,
                        alert_type=AlertType.SALARY_SPIKE,
                        severity=_severity_for_change(change),
                        change_percent=change.quantize(Decimal('0.01')),
                        previous_value=prev_slip.basic_salary,
                        current_value=slip.basic_salary,
                        root_cause=f'Basic salary changed by {change:.2f}% vs previous run ({previous_run.run_code}).',
                        suggested_action='Confirm this change is intentional before releasing.',
                    ))

            # 2. New employee (on current run, not on previous run)
            for emp_id, slip in current_slips.items():
                if emp_id not in previous_slips:
                    alerts.append(PayrollAuditAlert(
                        payroll_run=payroll_run,
                        compared_to_run=previous_run,
                        employee_salary_info_id=emp_id,
                        alert_type=AlertType.NEW_EMPLOYEE,
                        severity=AlertSeverity.LOW,
                        current_value=slip.basic_salary,
                        root_cause='Employee appears on this payroll run but not on the previous run.',
                        suggested_action='Confirm this is a new hire or re-activation.',
                    ))

            # 3. Missing employee (on previous run, not on current run)
            for emp_id, prev_slip in previous_slips.items():
                if emp_id not in current_slips:
                    alerts.append(PayrollAuditAlert(
                        payroll_run=payroll_run,
                        compared_to_run=previous_run,
                        employee_salary_info_id=emp_id,
                        alert_type=AlertType.MISSING_EMPLOYEE,
                        severity=AlertSeverity.MEDIUM,
                        previous_value=prev_slip.basic_salary,
                        root_cause='Employee was on the previous payroll run but is missing from this run.',
                        suggested_action='Confirm this is an intentional exit/offboarding, not a data omission.',
                    ))

        if alerts:
            PayrollAuditAlert.objects.bulk_create(alerts)
            logger.info(
                'generate_audit_alerts: created %d alert(s) for run %s',
                len(alerts), payroll_run.run_code,
            )

        return len(alerts)

    except Exception:
        # Alert generation must never break payroll processing.
        logger.exception('generate_audit_alerts: failed for run %s', getattr(payroll_run, 'run_code', payroll_run))
        return 0
