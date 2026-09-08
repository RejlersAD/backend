"""Governed actual-cost reconciliation and immutable integrated reporting."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Max, Sum

from ..models import (
    ApprovedHourEntry, BudgetAllocation, ControlAccount, CostLedgerEntry,
    IntegratedReportingSnapshot, ReconciliationRun,
)
from .cost_ledger import rebuild_project_ledger


ZERO = Decimal('0')
CENT = Decimal('0.01')


def _d(value):
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))


def _money(value):
    return _d(value).quantize(CENT, rounding=ROUND_HALF_UP)


def _checksum(payload):
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _latest_schedule_control(period):
    from apps.planning_intelligence.schedule_models import ScheduleControlSnapshot

    return ScheduleControlSnapshot.objects.filter(
        version__schedule__project__enterprise_project=period.project,
        version__status__in=['approved', 'baselined'],
        data_date__lte=period.data_date,
        is_deleted=False,
    ).select_related('version', 'version__schedule').order_by('-data_date', '-created_at').first()


@transaction.atomic
def reconcile_reporting_period(period, *, user=None):
    """Post approved labour and map verified finance actuals into one open period."""
    if not period.is_entry_allowed:
        raise ValueError('Reconciliation is allowed only while the reporting period is open or reopened.')

    rebuild_project_ledger(period.project, user=user)
    currency = period.project.currency or 'AED'
    exceptions = []

    hours = list(ApprovedHourEntry.objects.filter(
        reporting_period=period, status='approved', is_deleted=False,
    ).select_related('control_account', 'control_account__wbs_node').order_by('work_date', 'id'))
    active_hour_keys = {f'approved-hour:{entry.pk}' for entry in hours}
    CostLedgerEntry.objects.filter(
        project=period.project, reporting_period=period, source_type='approved_hour',
        status='posted', is_deleted=False,
    ).exclude(entry_key__in=active_hour_keys).update(status='reversed')
    for entry in hours:
        if entry.project_id != period.project_id or entry.control_account.project_id != period.project_id:
            exceptions.append({
                'type': 'project_mismatch', 'source': entry.source_reference,
                'message': 'Approved hour does not belong to the reporting-period project.',
            })
            continue
        if entry.currency != currency:
            exceptions.append({
                'type': 'currency_mismatch', 'source': entry.source_reference,
                'message': f'Hour cost is {entry.currency}; project currency is {currency}.',
            })
            continue
        CostLedgerEntry.objects.update_or_create(
            entry_key=f'approved-hour:{entry.pk}',
            defaults={
                'project': period.project, 'wbs_node': entry.control_account.wbs_node,
                'control_account': entry.control_account, 'reporting_period': period,
                'budget_allocation': None, 'cost_allocation': None, 'entry_type': 'actual',
                'amount': entry.labor_actual_cost, 'currency': entry.currency,
                'source_type': 'approved_hour', 'source_id': str(entry.pk),
                'source_reference': entry.source_reference, 'entry_date': entry.work_date,
                'status': 'posted', 'metadata': {
                    'employee_code': entry.employee_code, 'hours': str(entry.hours),
                    'hourly_cost_rate': str(entry.hourly_cost_rate),
                }, 'created_by': user, 'is_deleted': False,
            },
        )

    finance_rows = list(CostLedgerEntry.objects.filter(
        project=period.project, reporting_period=period, entry_type='actual',
        status='posted', is_deleted=False,
    ).exclude(source_type='approved_hour').select_related('control_account').order_by('entry_key'))
    for row in finance_rows:
        if not row.control_account_id:
            exceptions.append({
                'type': 'unmapped_control_account', 'source': row.source_reference or row.source_id,
                'ledger_entry': row.pk,
                'message': 'Verified finance actual has no active Control Account. Approve a WBS allocation.',
            })
        if row.currency != currency:
            exceptions.append({
                'type': 'currency_mismatch', 'source': row.source_reference or row.source_id,
                'ledger_entry': row.pk,
                'message': f'Finance actual is {row.currency}; project currency is {currency}.',
            })

    hour_total = sum((_d(row.hours) for row in hours), ZERO)
    labor_cost = sum((_d(row.labor_actual_cost) for row in hours if row.currency == currency), ZERO)
    finance_cost = sum((_d(row.amount) for row in finance_rows if row.currency == currency), ZERO)
    ledger_cost = _d(CostLedgerEntry.objects.filter(
        project=period.project, reporting_period=period, entry_type='actual',
        status='posted', is_deleted=False, currency=currency,
    ).aggregate(total=Sum('amount'))['total'])
    if _money(labor_cost + finance_cost) != _money(ledger_cost):
        exceptions.append({
            'type': 'amount_mismatch', 'source': 'period-ledger',
            'message': 'Labour plus finance actuals do not equal the reporting-period ledger actual.',
        })

    source_manifest = {
        'approved_hour_ids': [row.pk for row in hours],
        'finance_ledger_ids': [row.pk for row in finance_rows],
        'currency': currency,
        'totals': {
            'approved_hours': str(hour_total), 'labor_actual_cost': str(_money(labor_cost)),
            'finance_actual_cost': str(_money(finance_cost)), 'ledger_actual_cost': str(_money(ledger_cost)),
        },
    }
    run_number = (ReconciliationRun.objects.filter(
        reporting_period=period,
    ).aggregate(value=Max('run_number'))['value'] or 0) + 1
    return ReconciliationRun.objects.create(
        project=period.project, reporting_period=period, run_number=run_number,
        status='exceptions' if exceptions else 'completed', approved_hours=hour_total,
        labor_actual_cost=_money(labor_cost), finance_actual_cost=_money(finance_cost),
        ledger_actual_cost=_money(ledger_cost), exception_count=len(exceptions), exceptions=exceptions,
        source_manifest=source_manifest, checksum=_checksum(source_manifest), created_by=user,
    )


def _planned_progress(account, data_date):
    if data_date <= account.baseline_start:
        return ZERO
    if data_date >= account.baseline_finish:
        return Decimal('100')
    duration = (account.baseline_finish - account.baseline_start).days
    return Decimal((data_date - account.baseline_start).days) * Decimal('100') / Decimal(max(duration, 1))


@transaction.atomic
def create_integrated_snapshot(period, *, user=None):
    """Seal a versioned KPI snapshot from the latest exception-free reconciliation."""
    if period.status != 'submitted':
        raise ValueError('A reporting period must be submitted before its snapshot can be sealed.')
    reconciliation = ReconciliationRun.objects.filter(
        reporting_period=period,
    ).order_by('-run_number').first()
    if not reconciliation:
        raise ValueError('Run actual-cost reconciliation before locking this reporting period.')
    if reconciliation.status != 'completed' or reconciliation.exception_count:
        raise ValueError('Resolve all reconciliation exceptions and run reconciliation again before locking.')

    accounts = list(ControlAccount.objects.filter(
        project=period.project, status__in=['active', 'closed'], is_deleted=False,
    ).select_related('wbs_node').order_by('code'))
    schedule_snapshot = _latest_schedule_control(period)
    progress_pct = _d(schedule_snapshot.progress_pct if schedule_snapshot else period.project.progress)

    account_payload = []
    bac = pv = ZERO
    for account in accounts:
        budget = _d(BudgetAllocation.objects.filter(
            project=period.project, wbs_node=account.wbs_node,
            status='approved', is_deleted=False,
        ).aggregate(total=Sum('amount'))['total'])
        planned_pct = _planned_progress(account, period.data_date)
        bac += budget
        pv += budget * planned_pct / Decimal('100')
        account_payload.append({
            'control_account_id': account.pk, 'code': account.code,
            'budget': str(_money(budget)), 'planned_progress_pct': str(planned_pct.quantize(CENT)),
            'actual_progress_pct': str(progress_pct.quantize(CENT)),
        })
    pv = _money(pv)
    ev = _money(bac * progress_pct / Decimal('100'))
    actual = _money(reconciliation.ledger_actual_cost)
    commitments = _d(CostLedgerEntry.objects.filter(
        project=period.project, entry_type='commitment', status='posted', is_deleted=False,
        entry_date__lte=period.data_date, currency=period.project.currency or 'AED',
    ).aggregate(total=Sum('amount'))['total'])
    cpi = (ev / actual).quantize(Decimal('0.0001')) if actual > 0 else None
    spi = (ev / pv).quantize(Decimal('0.0001')) if pv > 0 else None
    eac = _money(bac / cpi) if cpi and cpi > 0 else None
    etc = _money(eac - actual) if eac is not None else None
    vac = _money(bac - eac) if eac is not None else None
    version = (IntegratedReportingSnapshot.objects.filter(
        reporting_period=period,
    ).aggregate(value=Max('version'))['value'] or 0) + 1
    manifest = {
        'reconciliation_id': str(reconciliation.pk),
        'reconciliation_checksum': reconciliation.checksum,
        'schedule_control_snapshot_id': schedule_snapshot.pk if schedule_snapshot else None,
        'schedule_source': 'approved_schedule_snapshot' if schedule_snapshot else 'enterprise_project_progress_fallback',
        'control_accounts': account_payload,
    }
    calculations = {
        'formulas': {'EV': 'BAC × progress %', 'CPI': 'EV ÷ AC', 'SPI': 'EV ÷ PV', 'EAC': 'BAC ÷ CPI'},
        'values': {'BAC': str(_money(bac)), 'PV': str(pv), 'EV': str(ev), 'AC': str(actual)},
    }
    sealed = {**manifest, 'calculations': calculations, 'version': version, 'data_date': str(period.data_date)}
    return IntegratedReportingSnapshot.objects.create(
        project=period.project, reporting_period=period, reconciliation_run=reconciliation,
        version=version, data_date=period.data_date, currency=period.project.currency or 'AED',
        budget_at_completion=_money(bac), planned_value=pv, earned_value=ev, actual_cost=actual,
        commitments=_money(commitments), approved_hours=reconciliation.approved_hours,
        labor_actual_cost=reconciliation.labor_actual_cost,
        finance_actual_cost=reconciliation.finance_actual_cost,
        progress_pct=progress_pct, planned_progress_pct=(pv / bac * 100 if bac > 0 else ZERO),
        cost_variance=_money(ev - actual), schedule_variance=_money(ev - pv), cpi=cpi, spi=spi,
        estimate_at_completion=eac, estimate_to_complete=etc, variance_at_completion=vac,
        source_manifest=manifest, calculation_payload=calculations,
        checksum=_checksum(sealed), sealed_by=user,
    )
