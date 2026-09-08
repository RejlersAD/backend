"""Portfolio management-by-exception read model."""
from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal

from django.db.models import Count, Sum
from django.utils import timezone

from ..config import PORTFOLIO_EXCEPTION_THRESHOLDS
from ..models import (
    BudgetAllocation, ControlAccount, CostLedgerEntry, IntegratedReportingSnapshot,
    ReconciliationRun, ReportingPeriod,
)


SEVERITY_RANK = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}


def _name(user):
    if not user:
        return 'Unassigned'
    return user.get_full_name() or user.email or user.username


def _decimal(value):
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))


def _issue(code, severity, category, title, detail, *, target_view, owner, metric=None, value=None, threshold=None):
    return {
        'code': code, 'severity': severity, 'category': category, 'title': title,
        'detail': detail, 'target_view': target_view,
        'owner': {'id': getattr(owner, 'pk', None), 'name': _name(owner)},
        'metric': metric, 'value': value, 'threshold': threshold,
    }


def _project_exceptions(project, today, data):
    thresholds = PORTFOLIO_EXCEPTION_THRESHOLDS
    issues = []
    project_accounts = data['accounts'].get(project.pk, [])
    active_accounts = [account for account in project_accounts if account.status == 'active']
    submitted_accounts = [account for account in project_accounts if account.status == 'submitted']

    if project.status in {'planning', 'active', 'on_hold'} and not active_accounts:
        issues.append(_issue(
            'missing_active_control_account', 'high', 'governance', 'No active Control Account',
            'Project scope has no approved accountable control point.', target_view='controls-periods',
            owner=project.owner,
        ))
    if submitted_accounts:
        issues.append(_issue(
            'control_accounts_awaiting_approval', 'medium', 'governance',
            f'{len(submitted_accounts)} Control Account(s) awaiting approval',
            'Submitted Control Accounts require independent Project Control or Finance approval.',
            target_view='controls-periods', owner=submitted_accounts[0].manager,
            metric='pending_control_accounts', value=len(submitted_accounts), threshold=0,
        ))
    for account in active_accounts:
        approved_budget = _decimal(data['budgets'].get((project.pk, account.wbs_node_id)))
        if approved_budget <= 0:
            issues.append(_issue(
                'control_account_without_budget', 'critical', 'governance',
                f'{account.code} has no approved budget',
                'An active Control Account must have an approved control budget.',
                target_view='controls-periods', owner=account.manager,
            ))

    periods = data['periods'].get(project.pk, [])
    current_period = next((period for period in periods if period.status in {'open', 'reopened'}), None)
    submitted_period = next((period for period in periods if period.status == 'submitted'), None)
    if submitted_period:
        issues.append(_issue(
            'period_awaiting_lock', 'high', 'reporting', f'{submitted_period.name} awaits lock',
            'Reconciliation passed and entry is frozen, but the management snapshot is not sealed.',
            target_view='controls-periods', owner=project.owner,
        ))
    if current_period and current_period.end_date < today:
        days = (today - current_period.end_date).days
        issues.append(_issue(
            'period_close_overdue', 'high', 'reporting', f'{current_period.name} close is overdue',
            f'The entry window ended {days} day(s) ago and remains {current_period.status}.',
            target_view='controls-periods', owner=project.owner,
            metric='days_overdue', value=days, threshold=0,
        ))
    if project.status in {'planning', 'active', 'on_hold'} and not periods:
        issues.append(_issue(
            'missing_reporting_period', 'medium', 'reporting', 'No reporting period established',
            'Open a governed reporting period before recording progress and actuals.',
            target_view='controls-periods', owner=project.owner,
        ))

    latest_reconciliation = data['reconciliations'].get(project.pk)
    if latest_reconciliation and latest_reconciliation.exception_count:
        issues.append(_issue(
            'reconciliation_exceptions', 'critical', 'data_quality',
            f'{latest_reconciliation.exception_count} reconciliation exception(s)',
            'Actual costs cannot be submitted or locked until all reconciliation exceptions are resolved.',
            target_view='controls-periods', owner=project.owner,
            metric='exception_count', value=latest_reconciliation.exception_count, threshold=0,
        ))
    unmapped_actuals = data['unmapped_actuals'].get(project.pk, 0)
    if unmapped_actuals:
        issues.append(_issue(
            'unmapped_actual_costs', 'critical', 'data_quality',
            f'{unmapped_actuals} actual-cost posting(s) are unmapped',
            'Assign an approved WBS allocation so every actual resolves to an active Control Account.',
            target_view='controls-periods', owner=project.owner,
            metric='unmapped_actuals', value=unmapped_actuals, threshold=0,
        ))

    snapshot = data['snapshots'].get(project.pk)
    if snapshot:
        cpi = float(snapshot.cpi) if snapshot.cpi is not None else None
        spi = float(snapshot.spi) if snapshot.spi is not None else None
        if cpi is not None and cpi < thresholds['cpi_warning_below']:
            severity = 'critical' if cpi < thresholds['cpi_critical_below'] else 'high'
            issues.append(_issue(
                'cpi_below_threshold', severity, 'cost', f'CPI {cpi:.2f} is below threshold',
                'Earned value is lower than actual cost; review cost performance and forecast.',
                target_view='cost-dashboard', owner=project.owner,
                metric='cpi', value=round(cpi, 4), threshold=thresholds[f'cpi_{"critical" if severity == "critical" else "warning"}_below'],
            ))
        if spi is not None and spi < thresholds['spi_warning_below']:
            severity = 'critical' if spi < thresholds['spi_critical_below'] else 'high'
            issues.append(_issue(
                'spi_below_threshold', severity, 'schedule', f'SPI {spi:.2f} is below threshold',
                'Earned value is lower than planned value; review the approved schedule forecast.',
                target_view='plan-baseline', owner=project.owner,
                metric='spi', value=round(spi, 4), threshold=thresholds[f'spi_{"critical" if severity == "critical" else "warning"}_below'],
            ))
        bac = _decimal(snapshot.budget_at_completion)
        eac = _decimal(snapshot.estimate_at_completion)
        if bac > 0 and eac > bac:
            overrun_pct = float((eac - bac) / bac * 100)
            severity = 'critical' if overrun_pct >= thresholds['forecast_overrun_critical_pct'] else 'high'
            issues.append(_issue(
                'forecast_overrun', severity, 'cost', f'Forecast overrun {overrun_pct:.1f}%',
                f'EAC exceeds BAC by {project.currency or "AED"} {eac - bac:,.2f}.',
                target_view='cost-dashboard', owner=project.owner,
                metric='forecast_overrun_pct', value=round(overrun_pct, 2),
                threshold=thresholds['forecast_overrun_critical_pct'],
            ))
        age = (today - snapshot.data_date).days
        if age > thresholds['snapshot_stale_days'] and project.status in {'active', 'on_hold'}:
            issues.append(_issue(
                'stale_reporting_snapshot', 'medium', 'reporting', f'Reporting data is {age} days old',
                'Open and close the next reporting period to refresh the management position.',
                target_view='controls-periods', owner=project.owner,
                metric='snapshot_age_days', value=age, threshold=thresholds['snapshot_stale_days'],
            ))
    elif project.status in {'active', 'on_hold'}:
        issues.append(_issue(
            'missing_reporting_snapshot', 'medium', 'reporting', 'No sealed management snapshot',
            'Complete reconciliation, submit the reporting period and lock it to establish KPI evidence.',
            target_view='controls-periods', owner=project.owner,
        ))

    if project.is_overdue:
        days = (today - project.end_date).days
        issues.append(_issue(
            'project_finish_overdue', 'critical', 'schedule', f'Project finish is overdue by {days} day(s)',
            'The project remains open beyond its current finish date.', target_view='plan-baseline',
            owner=project.owner, metric='days_overdue', value=days, threshold=0,
        ))

    issues.sort(key=lambda row: (SEVERITY_RANK[row['severity']], row['category'], row['title']))
    counts = Counter(row['severity'] for row in issues)
    overall = issues[0]['severity'] if issues else 'clear'
    return {
        'project': {
            'id': project.pk, 'code': project.code, 'name': project.name,
            'status': project.status, 'priority': project.priority,
            'owner': {'id': project.owner_id, 'name': _name(project.owner)},
            'currency': project.currency or 'AED', 'progress_pct': project.progress or 0,
        },
        'overall_severity': overall,
        'exception_count': len(issues),
        'severity_counts': {key: counts.get(key, 0) for key in ('critical', 'high', 'medium', 'low')},
        'latest_snapshot': ({
            'id': str(snapshot.pk), 'version': snapshot.version, 'data_date': snapshot.data_date,
            'cpi': snapshot.cpi, 'spi': snapshot.spi,
            'bac': snapshot.budget_at_completion, 'eac': snapshot.estimate_at_completion,
            'actual_cost': snapshot.actual_cost,
        } if snapshot else None),
        'exceptions': issues,
    }


def build_portfolio_exception_dashboard(projects, *, severity=None, category=None, search=None):
    today = timezone.localdate()
    project_rows = list(projects.select_related('owner'))
    project_ids = [project.pk for project in project_rows]
    accounts = defaultdict(list)
    for account in ControlAccount.objects.filter(
        project_id__in=project_ids, status__in=['active', 'submitted'], is_deleted=False,
    ).select_related('manager', 'wbs_node'):
        accounts[account.project_id].append(account)
    budgets = {
        (row['project_id'], row['wbs_node_id']): row['total']
        for row in BudgetAllocation.objects.filter(
            project_id__in=project_ids, status='approved', is_deleted=False,
        ).values('project_id', 'wbs_node_id').annotate(total=Sum('amount'))
    }
    periods = defaultdict(list)
    for period in ReportingPeriod.objects.filter(
        project_id__in=project_ids, is_deleted=False,
    ).order_by('project_id', '-sequence'):
        periods[period.project_id].append(period)
    reconciliations = {}
    for run in ReconciliationRun.objects.filter(project_id__in=project_ids).order_by('project_id', '-created_at'):
        reconciliations.setdefault(run.project_id, run)
    unmapped_actuals = {
        row['project_id']: row['total']
        for row in CostLedgerEntry.objects.filter(
            project_id__in=project_ids, entry_type='actual', status='posted',
            control_account__isnull=True, is_deleted=False,
        ).values('project_id').annotate(total=Count('id'))
    }
    snapshots = {}
    for snapshot in IntegratedReportingSnapshot.objects.filter(project_id__in=project_ids).order_by(
        'project_id', '-data_date', '-version', '-sealed_at',
    ):
        snapshots.setdefault(snapshot.project_id, snapshot)
    data = {
        'accounts': accounts, 'budgets': budgets, 'periods': periods,
        'reconciliations': reconciliations, 'unmapped_actuals': unmapped_actuals,
        'snapshots': snapshots,
    }
    rows = [_project_exceptions(project, today, data) for project in project_rows]
    accessible_project_count = len(rows)
    if severity:
        rows = [row for row in rows if any(issue['severity'] == severity for issue in row['exceptions'])]
    if category:
        rows = [row for row in rows if any(issue['category'] == category for issue in row['exceptions'])]
    if search:
        needle = search.strip().lower()
        rows = [row for row in rows if needle in f"{row['project']['code']} {row['project']['name']} {row['project']['owner']['name']}".lower()]
    rows.sort(key=lambda row: (SEVERITY_RANK.get(row['overall_severity'], 9), -row['exception_count'], row['project']['code']))
    all_issues = [issue for row in rows for issue in row['exceptions']]
    project_counts = Counter(row['overall_severity'] for row in rows)
    return {
        'generated_at': timezone.now(), 'thresholds': PORTFOLIO_EXCEPTION_THRESHOLDS,
        'summary': {
            'total_projects': len(rows),
            'accessible_project_count': accessible_project_count,
            'projects_needing_attention': sum(1 for row in rows if row['exception_count']),
            'clear_projects': project_counts.get('clear', 0),
            'critical_projects': project_counts.get('critical', 0),
            'high_projects': project_counts.get('high', 0),
            'medium_projects': project_counts.get('medium', 0),
            'exception_count': len(all_issues),
        },
        'category_counts': dict(Counter(issue['category'] for issue in all_issues)),
        'projects': rows,
    }
