"""Bridge between the Time Sheet module and the Payroll Engine.

Provides ``get_monthly_hours_map(year, month) -> {employee_code: Decimal}``
which mirrors the **Total** column shown on the HR ▸ Attendance ▸ Summary
tab (``Time Sheet Summary — All Branches``).

Computation parity with the Summary tab:

* Sum of biometric daily hours from
  :pyfunc:`apps.timesheet.services.monthly_report` (already capped at
  ``max_daily_hours``).
* HR overrides from :class:`apps.payroll.models.AttendanceOverride`
  **replace** the biometric value for the matching ``(employee_code,
  date)`` cell — only the most-recent active record is used.

Soft-coded knobs (all overrideable via env vars):

* ``PAYROLL_HOURS_FROM_TIMESHEET`` — master toggle (default ``True``)
* ``PAYROLL_HOURS_FALLBACK_TO_EMPLOYEE`` — when an employee_no has no
  biometric data, fall back to ``PayrollEmployee.hours`` instead of 0
  (default ``True``).
"""
from __future__ import annotations
import logging
from collections import defaultdict
from decimal import Decimal
from typing import Optional

from ..config import (
    DEFAULT_EMPLOYEE_HOURS,
    HOURS_FALLBACK_TO_EMPLOYEE,
    HOURS_FROM_TIMESHEET,
)

logger = logging.getLogger(__name__)


ZERO = Decimal('0.00')


class AttendanceUnavailable(RuntimeError):
    """The attendance source could not be read reliably."""


def _to_decimal(value) -> Decimal:
    if value is None:
        return ZERO
    try:
        return Decimal(str(value))
    except Exception:
        return ZERO


def _safe_monthly_report(year: int, month: int) -> dict:
    """Wrap timesheet.monthly_report so payroll never blows up if the
    biometric DB is unreachable (Railway, network, etc.).
    """
    try:
        # Honour the same manual/biometric and SQL Server/mirror selection as
        # the Time Sheet API. Production cannot connect to the office LAN.
        from apps.timesheet import get_service  # local import — soft dep
        return get_service().monthly_report(year, month) or {}
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(
            '[payroll_engine.attendance] timesheet.monthly_report(%s, %s) failed: %s',
            year, month, exc,
        )
        return {}


def _overrides_for_month(year: int, month: int) -> dict[str, dict[str, Decimal]]:
    """Return ``{employee_code: {YYYY-MM-DD: override_hours_decimal}}`` for
    the most-recent active override per (employee_code, date)."""
    try:
        from apps.payroll.models import AttendanceOverride  # local import
    except Exception as exc:  # pragma: no cover
        logger.warning('[payroll_engine.attendance] AttendanceOverride unavailable: %s', exc)
        return {}

    import calendar as _cal
    last_day = _cal.monthrange(int(year), int(month))[1]
    start = f'{year:04d}-{month:02d}-01'
    end = f'{year:04d}-{month:02d}-{last_day:02d}'

    qs = (
        AttendanceOverride.objects
        .filter(is_active=True, date__gte=start, date__lte=end)
        .order_by('employee_code', 'date', '-created_at')
        .values('employee_code', 'date', 'override_hours')
    )
    out: dict[str, dict[str, Decimal]] = defaultdict(dict)
    seen: set[tuple[str, str]] = set()
    for row in qs:
        code = str(row.get('employee_code') or '').strip()
        if not code:
            continue
        date_iso = row['date'].isoformat() if hasattr(row['date'], 'isoformat') else str(row['date'])
        key = (code, date_iso)
        if key in seen:
            continue  # earlier row already captured the most-recent active record
        seen.add(key)
        out[code][date_iso] = _to_decimal(row.get('override_hours'))
    return dict(out)


def compute_monthly_hours(year: int, month: int) -> dict[str, Decimal]:
    """Build ``{employee_code: Decimal(total_hours)}`` for the given month.

    Mirrors the **Total** column on the Attendance ▸ Summary view:
    biometric daily hours, with HR overrides replacing the cell when present.
    Empty when ``PAYROLL_HOURS_FROM_TIMESHEET=False``.
    """
    if not HOURS_FROM_TIMESHEET:
        return {}

    report = _safe_monthly_report(year, month)
    rows = report.get('rows') or []
    overrides_by_emp = _overrides_for_month(year, month)

    result: dict[str, Decimal] = {}
    for r in rows:
        code = str(r.get('employee_code') or '').strip()
        if not code:
            continue

        # Map biometric days for this employee
        biometric_days: dict[str, Decimal] = {}
        for d in (r.get('days_detail') or []):
            day_iso = d.get('date')
            if day_iso:
                biometric_days[str(day_iso)] = _to_decimal(d.get('hours'))

        # Overlay HR overrides — replace cell value (not add)
        emp_overrides = overrides_by_emp.get(code, {})
        for day_iso, ov_hours in emp_overrides.items():
            biometric_days[day_iso] = ov_hours

        total = sum(biometric_days.values(), ZERO)
        result[code] = total.quantize(Decimal('0.01'))

    # Include override-only employees (no biometric data this month)
    for code, days in overrides_by_emp.items():
        if code in result:
            continue
        total = sum(days.values(), ZERO)
        if total > 0:
            result[code] = total.quantize(Decimal('0.01'))

    return result


def get_hours_for_employee(
    employee_no: str,
    year: int,
    month: int,
    *,
    fallback: Optional[Decimal] = None,
) -> Decimal:
    """Convenience accessor for a single employee. Returns ``fallback``
    (or the soft-coded default) when no biometric/override data exists.
    """
    code = str(employee_no or '').strip()
    if not code:
        return _to_decimal(fallback if fallback is not None else DEFAULT_EMPLOYEE_HOURS)
    hours_map = compute_monthly_hours(year, month)
    if code in hours_map:
        return hours_map[code]
    if HOURS_FALLBACK_TO_EMPLOYEE and fallback is not None:
        return _to_decimal(fallback)
    return _to_decimal(DEFAULT_EMPLOYEE_HOURS)


# ══════════════════════════════════════════════════════════════════════
# Attendance Summary — payroll-ready attendance facts per employee per month
# ══════════════════════════════════════════════════════════════════════
def get_attendance_summary(year: int, month: int) -> dict:
    """Payroll-ready attendance facts for every active employee in the month."""
    import calendar as _cal
    from collections import defaultdict
    from decimal import Decimal

    from ..models import PayrollEmployee

    last_day = _cal.monthrange(int(year), int(month))[1]
    start_date = f'{year:04d}-{month:02d}-01'
    end_date = f'{year:04d}-{month:02d}-{last_day:02d}'

    try:
        from apps.timesheet.models import TimesheetMirrorHeartbeat
        heartbeat = TimesheetMirrorHeartbeat.objects.filter(key='default').first()
        last_synced_at = (
            heartbeat.last_seen_at.isoformat() if heartbeat and heartbeat.last_seen_at else None
        )
        last_event_time = (
            heartbeat.last_event_time.isoformat() if heartbeat and heartbeat.last_event_time else None
        )
    except Exception:
        last_synced_at = None
        last_event_time = None

    try:
        from apps.timesheet.models import DailyAttendanceSummary
        daily_rows = list(
            DailyAttendanceSummary.objects
            .filter(date__gte=start_date, date__lte=end_date)
            .values('employee_code', 'employee_name', 'department', 'date',
                    'attendance_status', 'time_in', 'time_out', 'overtime_hours',
                    'paired_hours', 'elapsed_hours', 'effective_hours',
                    'first_in', 'last_out', 'punch_count_in', 'punch_count_out',
                    'open_shift', 'is_late', 'is_full_day')
            .order_by('employee_code', 'date')
        )
    except Exception as exc:
        logger.warning('[payroll_engine.attendance] daily summary query failed: %s', exc)
        raise AttendanceUnavailable('Attendance data is currently unavailable.') from exc

    emp_data: dict[str, dict] = defaultdict(lambda: {
        'employee_code': '', 'full_name': '', 'department': '',
        'present_days': Decimal('0'), 'absent_days': Decimal('0'),
        'ot_hours': Decimal('0'), 'days_detail': [],
        'first_in': None, 'last_out': None,
        'punch_count_in': 0, 'punch_count_out': 0,
        'open_shift': False, 'is_late': False, 'is_full_day': False,
    })

    for row in daily_rows:
        code = str(row.get('employee_code') or '').strip()
        if not code:
            continue
        d = emp_data[code]
        if not d['employee_code']:
            d['employee_code'] = code
            d['full_name'] = row.get('employee_name') or ''
            d['department'] = row.get('department') or ''
        status = (row.get('attendance_status') or '').lower()
        hours = _to_decimal(
            row.get('effective_hours') or row.get('paired_hours') or row.get('elapsed_hours') or 0
        )
        ot = _to_decimal(row.get('overtime_hours') or 0)
        if status == 'present' or hours > 0:
            d['present_days'] += Decimal('1')
            d['ot_hours'] += ot
        elif status == 'absent':
            d['absent_days'] += Decimal('1')
        d['days_detail'].append({
            'date': row.get('date').isoformat() if hasattr(row.get('date'), 'isoformat') else str(row.get('date')),
            'status': status,
            'hours': str(hours),
            'ot_hours': str(ot),
        })
        fi = row.get('first_in')
        lo = row.get('last_out')
        if fi:
            d['first_in'] = fi.isoformat() if hasattr(fi, 'isoformat') else str(fi)
        if lo:
            d['last_out'] = lo.isoformat() if hasattr(lo, 'isoformat') else str(lo)
        if row.get('punch_count_in'):
            d['punch_count_in'] = int(row.get('punch_count_in') or 0)
        if row.get('punch_count_out'):
            d['punch_count_out'] = int(row.get('punch_count_out') or 0)
        if row.get('open_shift'):
            d['open_shift'] = True
        if row.get('is_late'):
            d['is_late'] = True
        if row.get('is_full_day'):
            d['is_full_day'] = True

    employees = []
    complete_count = 0
    partial_count = 0
    missing_count = 0

    try:
        from apps.hr_core.organization import payroll_eligible
        active_emps = {
            str(e.employee_no).strip(): e
            for e in payroll_eligible(PayrollEmployee.objects.filter(
                is_active=True, employee_id__isnull=False,
            ))
        }
    except Exception as exc:
        raise AttendanceUnavailable('Payroll-eligible employees could not be loaded.') from exc

    # Use exactly the same eligible population as payroll run generation.
    # Rows belonging to inactive or profile-only employees must not affect the
    # completeness percentage or block release of a run that excludes them.
    for code, emp in sorted(active_emps.items()):
        d = emp_data.get(code)
        if not d:
            employees.append({
                'employee_code': code,
                'full_name': emp.full_name,
                'department': emp.department,
                'present_days': '0.00',
                'absent_days': '0.00',
                'ot_hours': '0.00',
                'attendance_status': 'missing',
            })
            missing_count += 1
            continue
        present = d['present_days'].quantize(Decimal('0.01'))
        absent = d['absent_days'].quantize(Decimal('0.01'))
        ot = d['ot_hours'].quantize(Decimal('0.01'))
        total_relevant = present + absent

        if total_relevant <= 0:
            status = 'missing'
            missing_count += 1
        elif d['open_shift'] or any(day['status'] == 'incomplete' for day in d['days_detail']):
            status = 'partial'
            partial_count += 1
        else:
            # Recorded absences are resolved attendance facts, not incomplete
            # data. Open/incomplete shifts are what make the basis partial.
            status = 'complete'
            complete_count += 1

        employees.append({
            'employee_code': d['employee_code'],
            'full_name': d['full_name'],
            'department': d['department'],
            'present_days': str(present),
            'absent_days': str(absent),
            'ot_hours': str(ot),
            'attendance_status': status,
            'open_shift': d['open_shift'],
        })

    total = complete_count + partial_count + missing_count
    complete_pct = (
        (Decimal(complete_count) / Decimal(total) * 100).quantize(Decimal('0.01'))
        if total > 0 else Decimal('0.00')
    )

    anomalies = _detect_attendance_anomalies(year, month, employees, active_emps)

    return {
        'year': int(year),
        'month': int(month),
        'last_synced_at': last_synced_at,
        'last_event_time': last_event_time,
        'employee_count': total,
        'complete_count': complete_count,
        'partial_count': partial_count,
        'missing_count': missing_count,
        'attendance_complete_pct': str(complete_pct),
        'employees': employees,
        'anomalies': anomalies,
    }


def _detect_attendance_anomalies(
    year: int, month: int,
    employees: list[dict],
    active_emps: dict,
) -> list[dict]:
    """Detect critical attendance anomalies that should block payroll release."""
    from decimal import Decimal

    anomalies = []

    for emp in employees:
        code = emp['employee_code']
        absent = Decimal(str(emp.get('absent_days', '0') or '0'))
        present = Decimal(str(emp.get('present_days', '0') or '0'))

        if absent > 5:
            anomalies.append({
                'employee_code': code,
                'rule': 'high_absence',
                'severity': 'high',
                'message': f'{code}: {absent} absent days exceeds threshold of 5.',
            })

        if present == 0 and absent == 0 and code in active_emps:
            emp_rec = active_emps[code]
            if (emp_rec.basic or 0) > 0:
                anomalies.append({
                    'employee_code': code,
                    'rule': 'zero_attendance',
                    'severity': 'warning',
                    'message': f'{code}: no attendance data is available for the payroll period.',
                })

    return anomalies
