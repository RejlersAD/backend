"""Monthly run generator.

Auto-generates a fresh Draft PayrollRun for a given (year, month) using
the PayrollEmployee master roster. Materialises pending PayrollAdjustment
rows into PayslipLineItem entries. Optionally carries forward last
month's free-form line items.

After generation, HR uploads an optional "Adjustments Only" XLSX (handled
by services.excel_import.import_adjustments) which is also materialised
on the next call to refresh_run_from_adjustments.
"""
from __future__ import annotations
from decimal import Decimal
from typing import Optional

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from ..catalog import (
    AdjustmentStatus, LineItemSource, Status,
)
from ..config import CARRY_FORWARD_LINE_ITEMS, HOURS_FROM_TIMESHEET
from ..models import (
    PayrollAdjustment, PayrollEmployee, PayrollRun, Payslip, PayslipLineItem,
)
from .attendance import compute_monthly_hours
from .calculator import recompute_payslip_totals, recompute_run_totals


class GenerationError(Exception):
    """Raised when a run can't be generated (e.g. already exists)."""


def _attendance_snapshot_payload(summary: dict, year: int, month: int) -> dict:
    """Keep the auditable payroll facts while excluding punch-level detail."""
    if int(summary.get('year') or 0) != int(year) or int(summary.get('month') or 0) != int(month):
        raise GenerationError('Attendance snapshot period does not match the payroll run period.')
    employees = [{
        'employee_code': str(item.get('employee_code') or '').strip(),
        'full_name': item.get('full_name') or '',
        'department': item.get('department') or '',
        'present_days': str(item.get('present_days') or '0.00'),
        'absent_days': str(item.get('absent_days') or '0.00'),
        'ot_hours': str(item.get('ot_hours') or '0.00'),
        'attendance_status': item.get('attendance_status') or 'missing',
        'open_shift': bool(item.get('open_shift')),
    } for item in (summary.get('employees') or []) if item.get('employee_code')]
    return {
        'year': int(year),
        'month': int(month),
        'last_synced_at': summary.get('last_synced_at'),
        'last_event_time': summary.get('last_event_time'),
        'employee_count': summary.get('employee_count', len(employees)),
        'complete_count': summary.get('complete_count', 0),
        'partial_count': summary.get('partial_count', 0),
        'missing_count': summary.get('missing_count', 0),
        'attendance_complete_pct': str(summary.get('attendance_complete_pct') or '0.00'),
        'employees': employees,
        'anomalies': list(summary.get('anomalies') or []),
        'captured_at': timezone.now().isoformat(),
    }


def _validate_unique_active_identities() -> None:
    """Refuse to materialise two payslips for one canonical person/account."""
    active = PayrollEmployee.objects.filter(is_active=True, employee_id__isnull=False)
    duplicate_canonical = (
        active.filter(employee_id__isnull=False)
        .values('employee_id').annotate(total=Count('id')).filter(total__gt=1)
    )
    duplicate_users = (
        active.filter(user_id__isnull=False)
        .values('user_id').annotate(total=Count('id')).filter(total__gt=1)
    )
    if duplicate_canonical.exists() or duplicate_users.exists():
        raise GenerationError(
            'Payroll generation stopped: multiple active payroll records are linked '
            'to the same canonical employee or user account. Run the payroll identity '
            'audit and resolve duplicates before generating the run.'
        )


def _cycle_code(year: int, month: int) -> str:
    return f'{year:04d}-{month:02d}'


def _previous_period(year: int, month: int):
    if month == 1:
        return year - 1, 12
    return year, month - 1


@transaction.atomic
def generate_monthly_run(
    year: int,
    month: int,
    *,
    user=None,
    overwrite: bool = False,
    note: str = '',
    working_days: Optional[int] = None,
    source_type: str = 'system',
    attendance_snapshot: Optional[dict] = None,
) -> PayrollRun:
    """Create a Draft PayrollRun for (year, month) with one Payslip per
    active PayrollEmployee. Apply any pending PayrollAdjustments.

    When ``attendance_snapshot`` is provided it is stored on the Draft run
    and used to populate each Payslip's attendance fields
    (present_days, absent_days, ot_hours, attendance_status).

    Args:
        working_days: Total working days for this month (HR-supplied).
                      Defaults to catalog.DEFAULT_WORKING_DAYS_PER_MONTH (22).
        attendance_snapshot: Payroll-ready attendance facts from
            ``services.attendance.get_attendance_summary()``.

    If a run for the period already exists:
      - ``overwrite=False`` (default) → raises GenerationError
      - ``overwrite=True`` AND the existing run is Draft → wipes & re-runs
      - ``overwrite=True`` AND the existing run is NOT Draft → raises
    """
    from ..catalog import DEFAULT_WORKING_DAYS_PER_MONTH
    _validate_unique_active_identities()
    effective_working_days = int(working_days) if working_days is not None else DEFAULT_WORKING_DAYS_PER_MONTH

    if PayrollAdjustment.objects.filter(target_year=year, target_month=month, status=AdjustmentStatus.PENDING).exists():
        from .adjustment_period import require_current_or_future
        require_current_or_future(year, month)

    existing = PayrollRun.objects.filter(year=year, month=month).first()
    if existing:
        if not overwrite:
            raise GenerationError(
                f"PayrollRun for {_cycle_code(year, month)} already exists "
                f"(status={existing.status}). Pass overwrite=True to regenerate."
            )
        if existing.status != Status.DRAFT:
            raise GenerationError(
                f"Cannot overwrite a {existing.status} run. Revert to Draft first."
            )
        PayrollAdjustment.objects.filter(applied_to__run=existing, ot_conversion__isnull=False).update(
            status=AdjustmentStatus.PENDING, applied_to=None, applied_at=None)
        # Wipe slips so we can regenerate
        existing.payslips.all().delete()
        run = existing
        run.notes = note or run.notes
        run.working_days_in_month = effective_working_days
        run.source_type = source_type
    # Capture the auditable attendance basis. Draft regeneration may replace
    # it; approved/released runs cannot be overwritten by this service.
    snapshot_payload = None
    if attendance_snapshot is not None:
        snapshot_payload = _attendance_snapshot_payload(attendance_snapshot, year, month)
    elif existing and existing.attendance_snapshot:
        snapshot_payload = existing.attendance_snapshot

    if snapshot_payload is None:
        # Internal callers that do not supply a snapshot still receive a
        # server-captured basis. Source failures abort the transaction.
        try:
            from .attendance import get_attendance_summary
            snapshot_payload = _attendance_snapshot_payload(
                get_attendance_summary(year, month), year, month,
            )
        except GenerationError:
            raise
        except Exception as exc:
            raise GenerationError('Attendance snapshot could not be captured.') from exc

    # Index snapshot employees by employee_code for payslip lookup
    snapshot_by_code = {
        str(e.get('employee_code') or '').strip(): e
        for e in snapshot_payload.get('employees', [])
    }

    if not existing:
        run = PayrollRun.objects.create(
            year=year,
            month=month,
            cycle_code=_cycle_code(year, month),
            status=Status.DRAFT,
            created_by=user if (user and getattr(user, 'is_authenticated', False)) else None,
            notes=note,
            working_days_in_month=effective_working_days,
            source_type=source_type,
            attendance_snapshot=snapshot_payload,
            attendance_snapshot_at=timezone.now(),
            attendance_complete_pct=(
                Decimal(str(snapshot_payload.get('attendance_complete_pct') or '0'))
                if snapshot_payload.get('attendance_complete_pct') is not None
                else Decimal('0')
            ),
        )
    else:
        run.notes = note or run.notes
        run.working_days_in_month = effective_working_days
        run.source_type = source_type
        run.attendance_snapshot = snapshot_payload
        run.attendance_snapshot_at = timezone.now()
        run.attendance_complete_pct = (
            Decimal(str(snapshot_payload.get('attendance_complete_pct') or '0'))
            if snapshot_payload.get('attendance_complete_pct') is not None
            else Decimal('0')
        )

    # Auto-compute public holidays for this month from the PH register.
    # Regions to count are soft-coded in catalog.DEFAULT_PH_REGIONS.
    try:
        from apps.payroll.models import PublicHoliday
        from ..catalog import DEFAULT_PH_REGIONS
        ph_count = PublicHoliday.objects.filter(
            date__year=year,
            date__month=month,
            is_active=True,
            region__in=DEFAULT_PH_REGIONS,
        ).count()
    except Exception:
        ph_count = 0
    run.public_holidays_in_month = ph_count
    run.save(update_fields=['working_days_in_month', 'public_holidays_in_month'])

    # Active employees only
    from apps.hr_core.organization import payroll_eligible
    employees = payroll_eligible(
        PayrollEmployee.objects.filter(is_active=True, employee_id__isnull=False)
    ).order_by('full_name')

    # Pull live "Total Hours" from the biometric Time Sheet Summary.
    # Keyed by employee_code which matches PayrollEmployee.employee_no.
    hours_map = compute_monthly_hours(year, month) if HOURS_FROM_TIMESHEET else {}

    # ── Leave days per employee for this month ────────────────────────────────
    # Sourced from approved LeaveRequests (apps.payroll) linked by employee_code.
    # Categories are soft-coded in catalog.LEAVE_CATEGORIES_FOR_PAYROLL.
    from decimal import Decimal as _D
    from apps.payroll.services.leave_sync import monthly_payroll_leave
    leave_lookup = monthly_payroll_leave(year, month)

    # Carry-forward source = previous month's payslips keyed by employee_id
    carry_map = {}
    if CARRY_FORWARD_LINE_ITEMS:
        prev_y, prev_m = _previous_period(year, month)
        prev_run = PayrollRun.objects.filter(year=prev_y, month=prev_m).first()
        if prev_run:
            for slip in prev_run.payslips.prefetch_related('line_items'):
                carry_map[slip.employee_id] = list(slip.line_items.exclude(component_code='overtime'))

    # Pending adjustments for this period, grouped by employee_id
    adj_map: dict[int, list[PayrollAdjustment]] = {}
    pending = PayrollAdjustment.objects.filter(
        target_year=year,
        target_month=month,
        status=AdjustmentStatus.PENDING,
    ).select_related('employee')
    for adj in pending:
        adj_map.setdefault(adj.employee_id, []).append(adj)

    created_slips: list[Payslip] = []
    for emp in employees:
        # Use live biometric total when available; fall back to the
        # employee's contracted hours (or the engine default) otherwise.
        live_hours = hours_map.get(str(emp.employee_no).strip())
        resolved_hours = live_hours if live_hours is not None else emp.hours

        # Leave days for this employee this month (from the lookup built above)
        from decimal import Decimal as _D
        _emp_leave = leave_lookup.get(str(emp.employee_no).strip(), {})

        # ── Attendance facts from the immutable snapshot ─────────────────────
        # Attendance is the key input to payroll. Each payslip records the
        # present/absent/ot days/hours from the snapshot so the calculation
        # basis is auditable and reproducible.
        _snap = snapshot_by_code.get(str(emp.employee_no).strip(), {})
        _present = Decimal(str(_snap.get('present_days') or '0.00'))
        _absent = Decimal(str(_snap.get('absent_days') or '0.00'))
        _ot = Decimal(str(_snap.get('ot_hours') or '0.00'))
        _att_status = _snap.get('attendance_status') or 'missing'

        slip = Payslip(
            run=run,
            employee=emp,
            hours=resolved_hours,
            basic=emp.basic,
            housing=emp.housing,
            transport=emp.transport,
            home_leave=emp.home_leave,
            payment_mode=emp.default_payment_mode,
            snapshot_full_name=emp.full_name,
            snapshot_department=emp.department,
            snapshot_designation=emp.designation,
            snapshot_iban=emp.iban,
            snapshot_joining_date=emp.joining_date,
            present_days=_present,
            absent_days=_absent,
            ot_hours=_ot,
            attendance_status=_att_status,
            annual_leave_days=_emp_leave.get('annual_leave_days', _D('0.00')),
            unpaid_leave_days=_emp_leave.get('unpaid_leave_days', _D('0.00')),
            # Seed public holiday days from the run-level count; HR can override per payslip
            public_holiday_days=_D(str(run.public_holidays_in_month)),
            status=Status.DRAFT,
        )
        slip.save()

        # Carry-forward line items from prior period
        if emp.id in carry_map:
            for src in carry_map[emp.id]:
                PayslipLineItem.objects.create(
                    payslip=slip,
                    kind=src.kind,
                    component_code=src.component_code,
                    label=src.label,
                    description=src.description,
                    amount=src.amount,
                    source=LineItemSource.AUTO,
                )

        # Apply pending adjustments for this employee/period
        for adj in adj_map.get(emp.id, []):
            PayslipLineItem.objects.create(
                payslip=slip,
                kind=adj.kind,
                component_code=adj.component_code,
                label=adj.label,
                description=adj.description,
                amount=adj.amount,
                source=LineItemSource.ADJUSTMENT,
            )
            adj.status = AdjustmentStatus.APPLIED
            adj.applied_to = slip
            adj.applied_at = timezone.now()
            adj.save(update_fields=['status', 'applied_to', 'applied_at', 'updated_at'])

        recompute_payslip_totals(slip)
        slip.save()
        created_slips.append(slip)

    run.generated_at = timezone.now()
    recompute_run_totals(run)
    run.save()
    return run


@transaction.atomic
def refresh_run_totals(run: PayrollRun) -> PayrollRun:
    """Re-compute every payslip's totals and the run's aggregates.
    Use after manual edits or bulk adjustment uploads.
    """
    for slip in run.payslips.prefetch_related('line_items'):
        recompute_payslip_totals(slip)
        slip.save()
    recompute_run_totals(run)
    run.save()
    return run


@transaction.atomic
def refresh_run_hours_from_timesheet(run: PayrollRun, *, zero_missing: bool | None = None) -> dict:
    """Re-pull the live "Total" hours from the Time Sheet Summary and
    update every Payslip on this run.

    Args:
        run: The PayrollRun whose payslips should be refreshed.
        zero_missing: When True, employees absent from the attendance map
            (no biometric punches and no HR overrides) have their hours
            set to 0 so the run mirrors the Attendance ▸ Summary "Total"
            column exactly. When False the previous snapshot is kept.
            When ``None`` (default), the soft-coded
            ``REFRESH_ZERO_MISSING_HOURS`` config flag decides.

    Returns ``{'updated': n, 'unchanged': n, 'missing': [...], 'zeroed': n}``.
    Caller is responsible for gating to DRAFT runs.
    """
    from decimal import Decimal
    from ..config import hours_to_days, REFRESH_ZERO_MISSING_HOURS
    from .calculator import recompute_run_totals

    if zero_missing is None:
        zero_missing = REFRESH_ZERO_MISSING_HOURS

    hours_map = compute_monthly_hours(run.year, run.month)
    updated = 0
    unchanged = 0
    zeroed = 0
    missing: list[str] = []
    zero_dec = Decimal('0')
    for slip in run.payslips.select_related('employee'):
        emp_code = str(slip.employee.employee_no).strip()
        live = hours_map.get(emp_code)
        if live is None:
            missing.append(emp_code)
            if zero_missing and slip.hours != zero_dec:
                slip.hours = zero_dec
                slip.days = hours_to_days(zero_dec)
                slip.save(update_fields=['hours', 'days', 'updated_at'])
                zeroed += 1
            continue
        live_dec = live if isinstance(live, Decimal) else Decimal(str(live))
        if slip.hours == live_dec:
            unchanged += 1
            continue
        slip.hours = live_dec
        slip.days = hours_to_days(live_dec)
        slip.save(update_fields=['hours', 'days', 'updated_at'])
        updated += 1
    # Roll the new hours/days totals up to the run aggregates so the
    # Monthly Runs table reflects the refresh immediately.
    if updated or zeroed:
        recompute_run_totals(run)
        run.save(update_fields=[
            'total_gross', 'total_deductions', 'total_net',
            'total_hours', 'total_days', 'employee_count', 'updated_at',
        ])
    return {
        'updated': updated,
        'unchanged': unchanged,
        'missing': missing,
        'zeroed': zeroed,
    }
