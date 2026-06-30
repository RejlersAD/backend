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
from typing import Optional

from django.db import transaction
from django.utils import timezone

from ..catalog import (
    AdjustmentStatus, LineItemSource, Status,
)
from ..config import CARRY_FORWARD_LINE_ITEMS
from ..models import (
    PayrollAdjustment, PayrollEmployee, PayrollRun, Payslip, PayslipLineItem,
)
from .calculator import recompute_payslip_totals, recompute_run_totals


class GenerationError(Exception):
    """Raised when a run can't be generated (e.g. already exists)."""


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
) -> PayrollRun:
    """Create a Draft PayrollRun for (year, month) with one Payslip per
    active PayrollEmployee. Apply any pending PayrollAdjustments.

    If a run for the period already exists:
      - ``overwrite=False`` (default) → raises GenerationError
      - ``overwrite=True`` AND the existing run is Draft → wipes & re-runs
      - ``overwrite=True`` AND the existing run is NOT Draft → raises
    """
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
        # Wipe slips so we can regenerate
        existing.payslips.all().delete()
        run = existing
        run.notes = note or run.notes
    else:
        run = PayrollRun.objects.create(
            year=year,
            month=month,
            cycle_code=_cycle_code(year, month),
            status=Status.DRAFT,
            created_by=user if (user and getattr(user, 'is_authenticated', False)) else None,
            notes=note,
        )

    # Active employees only
    employees = PayrollEmployee.objects.filter(is_active=True).order_by('full_name')

    # Carry-forward source = previous month's payslips keyed by employee_id
    carry_map = {}
    if CARRY_FORWARD_LINE_ITEMS:
        prev_y, prev_m = _previous_period(year, month)
        prev_run = PayrollRun.objects.filter(year=prev_y, month=prev_m).first()
        if prev_run:
            for slip in prev_run.payslips.prefetch_related('line_items'):
                carry_map[slip.employee_id] = list(slip.line_items.all())

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
        slip = Payslip(
            run=run,
            employee=emp,
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
