"""Keep annual leave ledgers aligned with the canonical employee master."""
from __future__ import annotations

from django.db import transaction

from apps.hr_core.models import EmployeeMaster
from apps.payroll.models import EmployeeLeaveRecord
from apps.payroll.services.leave_accrual import compute_accrual_for_record
from apps.timesheet.identity import norm_code


ELIGIBLE_EMPLOYMENT_STATUSES = ('active', 'probation', 'notice_period')
SUPPORTED_BRANCHES = ('RAD', 'RIN')
CANONICAL_SOURCE = 'canonical-workforce'


def _employee_code(employee: EmployeeMaster) -> str:
    """Return the canonical code used by payroll, leave and biometrics."""
    return norm_code(
        employee.employee_code
        or employee.emp_code
        or employee.employee_number
    )


def _employee_name(employee: EmployeeMaster) -> str:
    return ' '.join(
        part.strip()
        for part in (employee.first_name or '', employee.last_name or '')
        if part and part.strip()
    ) or _employee_code(employee)


def ensure_canonical_leave_records(year: int, employee_code: str | None = None) -> int:
    """Create missing annual leave ledgers from ``EmployeeMaster``.

    Existing imported ledgers are never updated, so historical balances and
    workbook-sourced transactions remain authoritative. Employees whose legal
    branch is not yet classified as RAD/RIN are intentionally skipped instead
    of guessing the wrong policy/entity.
    """
    employees = EmployeeMaster.objects.filter(
        employment_status__in=ELIGIBLE_EMPLOYMENT_STATUSES,
        branch__in=SUPPORTED_BRANCHES,
    )
    if employee_code:
        code = norm_code(employee_code)
        employees = employees.filter(employee_code=code)

    employee_rows = [
        (employee, _employee_code(employee))
        for employee in employees.iterator()
    ]
    employee_rows = [(employee, code) for employee, code in employee_rows if code]
    existing_codes = set(
        EmployeeLeaveRecord.objects.filter(
            year=year,
            employee_code__in=[code for _, code in employee_rows],
        ).values_list('employee_code', flat=True)
    )

    created_count = 0
    for employee, code in employee_rows:
        if code in existing_codes:
            continue

        with transaction.atomic():
            record, created = EmployeeLeaveRecord.objects.get_or_create(
                employee_code=code,
                year=year,
                defaults={
                    'employee_name': _employee_name(employee),
                    'department': employee.department or None,
                    'job_title': (
                        employee.job_title_uae
                        or employee.designation
                        or employee.job_title_finland
                        or None
                    ),
                    'joining_date': employee.join_date,
                    'branch': employee.branch,
                    'source_file': CANONICAL_SOURCE,
                },
            )
            if created:
                compute_accrual_for_record(record, target_year=year, dry_run=False)
                created_count += 1
                existing_codes.add(code)

    return created_count
