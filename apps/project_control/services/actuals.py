"""Governed actual-cost reconciliation and immutable integrated reporting."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Max, Sum
from rest_framework.exceptions import ValidationError

from ..models import (
    ApprovedHourEntry, BudgetAllocation, ControlAccount, CostLedgerEntry,
    IntegratedReportingSnapshot, ReconciliationRun, ReportingPeriod,
)
from .cost_ledger import rebuild_project_ledger
from .financial_scope import require_owned_financial_wbs


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
    ).select_related('version', 'version__schedule').order_by('-data_date', '-created_at', '-id').first()


def _actual_cost_observation(period):
    """Capture project-to-date posted costs and the selected period separately.

    Reversals are excluded by status; a backdated correction is included by its
    effective entry date. Snapshot evidence retains values, not only mutable IDs.
    """
    currency = period.project.currency or 'AED'
    rows = list(CostLedgerEntry.objects.filter(
        project=period.project, entry_type__in=['actual', 'adjustment'],
        status='posted', is_deleted=False, entry_date__lte=period.data_date,
    ).select_related('control_account__wbs_node', 'wbs_node', 'reporting_period').order_by('entry_key'))
    period_totals = dict.fromkeys(['approved_hours', 'labor_actual_cost', 'finance_actual_cost', 'ledger_actual_cost'], ZERO)
    cumulative_totals = period_totals.copy()
    entries = []
    exceptions = []
    for row in rows:
        entries.append({
            'id': row.pk, 'entry_key': row.entry_key, 'entry_type': row.entry_type,
            'amount': str(row.amount), 'currency': row.currency, 'entry_date': str(row.entry_date),
            'control_account_id': row.control_account_id, 'reporting_period_id': row.reporting_period_id,
            'source_type': row.source_type, 'source_id': row.source_id,
            'source_reference': row.source_reference, 'metadata': row.metadata,
        })
        problem = None
        if row.currency != currency:
            problem = ('currency_mismatch', f'Actual cost is {row.currency}; project currency is {currency}.')
        elif not row.control_account_id:
            problem = ('unmapped_control_account', 'Actual cost has no approved Control Account.')
        elif row.control_account.project_id != period.project_id:
            problem = ('project_mismatch', 'Actual cost Control Account belongs to a different project.')
        elif not row.reporting_period_id:
            problem = ('unmapped_reporting_period', 'Actual cost has no reporting period. Assign its effective date to a period.')
        elif (row.reporting_period.project_id != period.project_id
              or not row.reporting_period.start_date <= row.entry_date <= row.reporting_period.end_date):
            problem = ('period_mismatch', 'Actual cost does not match its reporting period project or dates.')
        if not problem and period.project.scope_type == 'detailed_engineering':
            try:
                require_owned_financial_wbs(period.project, row.control_account.wbs_node)
                if row.wbs_node_id != row.control_account.wbs_node_id:
                    raise ValidationError('The ledger WBS differs from its Control Account.')
            except ValidationError:
                problem = ('external_delivery_scope', 'Actual cost must map to owned Engineering WBS and its Control Account. Review the external dependency or inconsistent mapping.')
        if problem:
            exceptions.append({'type': problem[0], 'source': row.source_reference or row.source_id,
                               'ledger_entry': row.pk, 'message': problem[1]})
        if row.currency != currency:
            continue
        labor = row.source_type == 'approved_hour'
        for totals in [cumulative_totals] + ([period_totals] if row.reporting_period_id == period.pk else []):
            totals['ledger_actual_cost'] += row.amount
            totals['labor_actual_cost' if labor else 'finance_actual_cost'] += row.amount
            if labor:
                totals['approved_hours'] += _d((row.metadata or {}).get('hours'))
    observation = {
        'basis': 'cumulative_to_data_date', 'data_date': str(period.data_date), 'currency': currency,
        'period_totals': {key: str(_money(value)) for key, value in period_totals.items()},
        'cumulative_totals': {key: str(_money(value)) for key, value in cumulative_totals.items()},
        'entries': entries,
    }
    return observation, exceptions


@transaction.atomic
def reconcile_reporting_period(period, *, user=None):
    """Post approved labour and map verified finance actuals into one open period."""
    period = ReportingPeriod.objects.select_for_update(of=('self',)).select_related('project').get(pk=period.pk)
    if not period.is_entry_allowed:
        raise ValueError('Reconciliation is allowed only while the reporting period is open or reopened.')

    rebuild_project_ledger(period.project, user=user)
    currency = period.project.currency or 'AED'
    exceptions = []

    hours = list(ApprovedHourEntry.objects.filter(
        reporting_period=period, status='approved', is_deleted=False, work_date__lte=period.data_date,
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
        project=period.project, reporting_period=period, entry_type__in=['actual', 'adjustment'],
        status='posted', is_deleted=False, entry_date__lte=period.data_date,
    ).exclude(source_type='approved_hour').select_related('control_account').order_by('entry_key'))
    observation, cost_exceptions = _actual_cost_observation(period)
    exceptions.extend(cost_exceptions)

    hour_total = sum((_d(row.hours) for row in hours), ZERO)
    labor_cost = sum((_d(row.labor_actual_cost) for row in hours if row.currency == currency), ZERO)
    finance_cost = sum((_d(row.amount) for row in finance_rows if row.currency == currency), ZERO)
    ledger_cost = _d(observation['period_totals']['ledger_actual_cost'])
    if _money(labor_cost + finance_cost) != _money(ledger_cost):
        exceptions.append({
            'type': 'amount_mismatch', 'source': 'period-ledger',
            'message': 'Labour plus finance actuals do not equal the reporting-period ledger actual.',
        })

    source_manifest = {
        'approved_hour_ids': [row.pk for row in hours],
        'finance_ledger_ids': [row.pk for row in finance_rows],
        'currency': currency,
        'actual_cost_observation': observation,
        'actual_cost_checksum': _checksum(observation),
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
    period = ReportingPeriod.objects.select_for_update(of=('self',)).select_related('project').get(pk=period.pk)
    if period.status != 'submitted':
        raise ValueError('A reporting period must be submitted before its snapshot can be sealed.')
    reconciliation = ReconciliationRun.objects.filter(
        reporting_period=period,
    ).order_by('-run_number').first()
    if not reconciliation:
        raise ValueError('Run actual-cost reconciliation before locking this reporting period.')
    if reconciliation.status != 'completed' or reconciliation.exception_count:
        raise ValueError('Resolve all reconciliation exceptions and run reconciliation again before locking.')
    observation, cost_exceptions = _actual_cost_observation(period)
    if (cost_exceptions or reconciliation.source_manifest.get('actual_cost_checksum') != _checksum(observation)):
        raise ValueError('Actual costs changed or require reconciliation. Reopen the submitted period and reconcile again before locking.')

    accounts = list(ControlAccount.objects.filter(
        project=period.project, status__in=['active', 'closed'], is_deleted=False,
    ).select_related('wbs_node').order_by('code'))
    from ..epc_models import IntegratedBaseline
    from ..execution_models import EPCWorkItem
    from apps.planning_intelligence.schedule_models import ScheduleControlSnapshot
    integrated_baseline = IntegratedBaseline.objects.filter(project=period.project,
        data_date__lte=period.data_date).order_by('-data_date', '-revision').first()
    if period.project.scope_type == 'detailed_engineering' and (
            not integrated_baseline or integrated_baseline.manifest.get('control_scope', {}).get('scope_type') != 'detailed_engineering'):
        raise ValueError('Capture an effective Engineering integrated baseline before closing this reporting period.')
    if integrated_baseline:
        if integrated_baseline.currency != (period.project.currency or 'AED'):
            raise ValueError('The integrated baseline currency differs from the reporting project currency.')
        schedule_snapshot = ScheduleControlSnapshot.objects.filter(
            version_id=integrated_baseline.manifest['schedule_baseline']['source_version'],
            data_date__lte=period.data_date, is_deleted=False,
        ).order_by('-data_date', '-revision', '-id').first()
        if not schedule_snapshot:
            raise ValueError('Capture a schedule observation for the selected integrated baseline before closing this period.')
        if integrated_baseline.manifest.get('control_scope', {}).get('scope_type') == 'detailed_engineering':
            scope = schedule_snapshot.payload.get('control_scope', {})
            expected = {str(value) for value in integrated_baseline.manifest.get('owned_activity_ids', [])}
            observed = {str(value) for value in scope.get('owned_activity_ids', [])}
            if (scope.get('ready') is not True or scope.get('scope_type') != 'detailed_engineering'
                    or not expected or observed != expected):
                raise ValueError('Capture an Engineering-only schedule observation matching the integrated baseline ownership before closing this period.')
    else:
        schedule_snapshot = _latest_schedule_control(period)
    progress_pct = _d(schedule_snapshot.progress_pct if schedule_snapshot else period.project.progress)

    account_payload = []
    bac = pv = ZERO
    for account in accounts if not integrated_baseline else []:
        budgets = list(BudgetAllocation.objects.filter(
            project=period.project, wbs_node=account.wbs_node,
            status='approved', is_deleted=False,
        ).order_by('pk'))
        if any(row.currency != (period.project.currency or 'AED') for row in budgets):
            raise ValueError('Approved budget currency must match the reporting project currency.')
        budget = sum((row.amount for row in budgets), ZERO)
        # Once an approved schedule-control snapshot exists it is the governed
        # source for planned progress.  The control-account date curve remains
        # an explicit fallback for projects that have not integrated Planning
        # Intelligence yet.
        planned_pct = (
            _d(schedule_snapshot.planned_progress_pct)
            if schedule_snapshot else _planned_progress(account, period.data_date)
        )
        bac += budget
        pv += budget * planned_pct / Decimal('100')
        account_payload.append({
            'control_account_id': account.pk, 'code': account.code,
            'budget': str(_money(budget)), 'planned_progress_pct': str(planned_pct.quantize(CENT)),
            'actual_progress_pct': str(progress_pct.quantize(CENT)),
            'approved_budget_sources': [{
                'id': row.pk, 'code': row.code, 'amount': str(row.amount), 'currency': row.currency,
                'approved_at': row.approved_at.isoformat() if row.approved_at else None,
            } for row in budgets],
        })
    if integrated_baseline:
        # A later source approval does not silently revise a captured EPC budget.
        planned_pct = _d(schedule_snapshot.planned_progress_pct)
        for account in integrated_baseline.manifest['control_accounts']:
            budgets = [row for row in integrated_baseline.manifest['budgets']
                       if str(row['wbs_node']) == str(account['wbs_node'])]
            budget = sum((_d(row['amount']) for row in budgets), ZERO)
            bac += budget
            pv += budget * planned_pct / Decimal('100')
            account_payload.append({'control_account_id': account['id'], 'code': account['code'],
                'budget': str(_money(budget)), 'planned_progress_pct': str(planned_pct.quantize(CENT)),
                'actual_progress_pct': str(progress_pct.quantize(CENT)), 'approved_budget_sources': budgets})
        if bac != integrated_baseline.budget_total:
            raise ValueError('The integrated baseline budget evidence is inconsistent.')
    pv = _money(pv)
    ev = _money(bac * progress_pct / Decimal('100'))
    actual = _money(observation['cumulative_totals']['ledger_actual_cost'])
    commitment_rows = CostLedgerEntry.objects.filter(
        project=period.project, entry_type='commitment', status='posted', is_deleted=False,
        entry_date__lte=period.data_date, currency=period.project.currency or 'AED',
    )
    if period.project.scope_type == 'detailed_engineering':
        for commitment in commitment_rows.select_related('wbs_node'):
            try:
                if not commitment.wbs_node_id:
                    raise ValidationError('Commitment WBS is not mapped.')
                require_owned_financial_wbs(period.project, commitment.wbs_node)
            except ValidationError as exc:
                raise ValueError('Map posted commitments to owned Engineering WBS before closing this period; external dependency costs require review.') from exc
    commitments = _d(commitment_rows.aggregate(total=Sum('amount'))['total'])
    cpi = (ev / actual).quantize(Decimal('0.0001')) if actual > 0 else None
    spi = (ev / pv).quantize(Decimal('0.0001')) if pv > 0 else None
    # Compute with the unrounded ratio; the four-place CPI is for display only.
    eac = _money(bac * actual / ev) if actual > 0 and ev > 0 else None
    etc = _money(eac - actual) if eac is not None else None
    vac = _money(bac - eac) if eac is not None else None
    version = (IntegratedReportingSnapshot.objects.filter(
        reporting_period=period,
    ).aggregate(value=Max('version'))['value'] or 0) + 1
    manifest = {
        'reconciliation_id': str(reconciliation.pk),
        'reconciliation_checksum': reconciliation.checksum,
        'schedule_control_snapshot_id': schedule_snapshot.pk if schedule_snapshot else None,
        'schedule_control_snapshot_revision': schedule_snapshot.revision if schedule_snapshot else None,
        'schedule_control_snapshot_data_date': str(schedule_snapshot.data_date) if schedule_snapshot else None,
        'schedule_source': 'approved_schedule_snapshot' if schedule_snapshot else 'enterprise_project_progress_fallback',
        'control_accounts': account_payload,
        'actual_cost_observation': observation,
        'actual_cost_checksum': _checksum(observation),
    }
    if integrated_baseline:
        manifest.update({
            'integrated_baseline_id': integrated_baseline.pk,
            'integrated_baseline_revision': integrated_baseline.revision,
            'integrated_baseline_checksum': integrated_baseline.checksum,
            'epc_acceptances': [{
                'id': row.pk, 'code': row.code, 'phase': row.phase, 'data_date': str(row.data_date),
                'accepted_by': row.accepted_by_id, 'accepted_at': row.accepted_at.isoformat(),
                'baseline': row.baseline_id, 'control_snapshot': row.control_snapshot_id,
            } for row in EPCWorkItem.objects.filter(project=period.project, status='accepted',
                control_snapshot__created_at__lte=schedule_snapshot.created_at,
                activity__version_id=schedule_snapshot.version_id, data_date__lte=period.data_date).order_by('id')],
        })
    calculations = {
        'actual_cost_basis': 'cumulative_to_data_date',
        'period_totals': observation['period_totals'],
        'cumulative_totals': observation['cumulative_totals'],
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
