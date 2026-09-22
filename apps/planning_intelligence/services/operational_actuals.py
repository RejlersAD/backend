"""Read approved labour and posted costs without creating financial records.

Account/activity bridges are scope evidence, not activity allocations. Monetary
values returned here are internal commercial data; callers must mask exports
according to can_view_commercial_actuals before returning them to an actor.
"""
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json

from django.core.serializers.json import DjangoJSONEncoder
from rest_framework.exceptions import NotFound, ValidationError

from apps.project_control.access import accessible_enterprise_projects, has_commercial_module_access
from apps.project_control.epc_models import WBSActivityLink
from apps.project_control.models import ApprovedHourEntry, CostLedgerEntry
from ..access import accessible_projects


ADAPTER_VERSION = 'approved-hours-posted-costs/1'


def _json(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder, allow_nan=False))


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


def can_view_commercial_actuals(actor, project):
    """Combine planning scope with existing commercial-module visibility."""
    return bool(actor and actor.is_authenticated and actor.is_active and project.enterprise_project_id
                and accessible_projects(actor).filter(pk=project.pk).exists()
                and has_commercial_module_access(actor)
                and accessible_enterprise_projects(actor).filter(pk=project.enterprise_project_id).exists())


def _issue(code, message, *, source_type='', source_id=None, count=None, severity='warning'):
    return {'code': code, 'message': message, 'severity': severity,
            **({'source_type': source_type} if source_type else {}),
            **({'source_id': source_id} if source_id is not None else {}),
            **({'count': count} if count is not None else {})}


def _scope_problem(row, enterprise_id, effective_date):
    account = row.control_account
    if account and account.project_id != enterprise_id:
        return 'foreign_control_account'
    if account and account.wbs_node.project_id != enterprise_id:
        return 'foreign_account_wbs'
    period = row.reporting_period
    if period and (period.project_id != enterprise_id or not period.start_date <= effective_date <= period.end_date):
        return 'inconsistent_reporting_period'
    return None


def read_operational_actuals(project, baseline, data_date, actor=None):
    """Capture effective-to-date sources; absence is unknown, never zero.

    The cutoff applies to work_date/entry_date, not approval timestamps: late
    approvals are retained as captured evidence for a restated observation.
    actor=None is for internal calls. A supplied actor must access the planning
    project, while the outer controls service separately masks commercial data.
    """
    if actor is not None and not accessible_projects(actor).filter(pk=project.pk).exists():
        raise NotFound('Planning project not found.')
    if isinstance(data_date, str):
        try:
            data_date = date.fromisoformat(data_date)
        except ValueError as exc:
            raise ValidationError({'data_date': 'Supply an ISO calendar date.'}) from exc
    if not isinstance(data_date, date) or isinstance(data_date, datetime):
        raise ValidationError({'data_date': 'Supply a calendar date.'})
    if (baseline is None or baseline.is_deleted or baseline.schedule.project_id != project.pk
            or baseline.source_version.schedule_id != baseline.schedule_id
            or baseline.source_version.is_deleted or baseline.schedule.is_deleted):
        raise ValidationError({'baseline': 'Select a current, project-owned baseline record.'})
    if not baseline.approved_by_id or not baseline.approved_at:
        raise ValidationError({'baseline': 'The baseline requires recorded approval evidence.'})

    issues, hours, costs = [], [], []
    coverage = {'hours': Counter(), 'costs': Counter()}
    manifest = {'adapter_version': ADAPTER_VERSION, 'planning_project_id': project.pk,
        'enterprise_project_id': project.enterprise_project_id, 'data_date': data_date.isoformat(),
        'cutoff_basis': 'effective_work_or_entry_date',
        'baseline': {'id': baseline.pk, 'source_version_id': baseline.source_version_id,
                     'approved_by_id': str(baseline.approved_by_id), 'approved_at': baseline.approved_at.isoformat(),
                     'snapshot_fingerprint': _fingerprint(_json(baseline.snapshot))},
        'hours': [], 'costs': [], 'activity_bridges': []}
    enterprise = project.enterprise_project if project.enterprise_project_id else None
    if enterprise is None or enterprise.is_deleted:
        issues.append(_issue('enterprise_project_not_linked', 'Link an active enterprise project to read its approved actuals.', severity='error'))
    else:
        manifest['project_currency'] = enterprise.currency
        frozen_ids = {str(row['id']) for row in (baseline.snapshot or {}).get('activities', [])
                      if isinstance(row, dict) and row.get('id') is not None}
        bridge_by_wbs = defaultdict(list)
        links = WBSActivityLink.objects.filter(project_id=enterprise.pk, is_deleted=False,
            wbs_node__project_id=enterprise.pk, wbs_node__is_deleted=False, activity__is_deleted=False,
            activity__version_id=baseline.source_version_id).select_related('activity').order_by('pk')
        for link in links:
            if str(link.activity_id) not in frozen_ids:
                continue
            value = _json({'id': link.pk, 'project_id': link.project_id, 'wbs_node_id': link.wbs_node_id,
                'activity_id': link.activity_id, 'activity_external_id': link.activity.external_id,
                'link_type': link.link_type, 'created_by_id': link.created_by_id, 'updated_at': link.updated_at,
                'basis': 'explicit_current_wbs_activity_link', 'allocates_actuals': False})
            manifest['activity_bridges'].append(value)
            bridge_by_wbs[link.wbs_node_id].append(value)
        if not frozen_ids:
            issues.append(_issue('baseline_activity_scope_missing', 'The baseline has no frozen activity IDs for exact account links.'))

        def bridge(row):
            account = row.control_account
            wbs_id = account.wbs_node_id if account else getattr(row, 'wbs_node_id', None)
            values = bridge_by_wbs.get(wbs_id, []) if not account or not account.is_deleted else []
            return {'account_activity_ids': [item['activity_id'] for item in values],
                    'activity_bridge_ids': [item['id'] for item in values],
                    'activity_allocation_status': 'not_allocated'}

        hour_rows = ApprovedHourEntry.objects.filter(project_id=enterprise.pk).select_related(
            'control_account__wbs_node', 'reporting_period').order_by('pk')
        for row in hour_rows.iterator(chunk_size=500):
            value = _json({key: getattr(row, key) for key in (
                'id', 'project_id', 'control_account_id', 'reporting_period_id', 'employee_code',
                'work_date', 'hours', 'hourly_cost_rate', 'labor_actual_cost', 'currency',
                'source_type', 'source_reference', 'status', 'submitted_by_id', 'submitted_at',
                'approved_by_id', 'approved_at', 'reversed_by_id', 'reversed_at', 'is_deleted', 'created_at', 'updated_at')})
            value.update(wbs_node_id=row.control_account.wbs_node_id, **bridge(row))
            reason = ('deleted' if row.is_deleted else 'future' if row.work_date > data_date
                      else row.status if row.status != 'approved'
                      else 'missing_approval' if not row.approved_by_id or not row.approved_at
                      else 'self_approval' if row.submitted_by_id and row.submitted_by_id == row.approved_by_id
                      else _scope_problem(row, enterprise.pk, row.work_date))
            coverage['hours']['seen'] += 1
            coverage['hours'][reason or 'included'] += 1
            manifest['hours'].append({**value, 'included': reason is None, 'exclusion_reason': reason})
            if reason:
                if reason not in {'deleted', 'future', 'draft', 'submitted', 'reversed'}:
                    issues.append(_issue('hour_' + reason, 'This hour entry lacks valid approval or project scope evidence.',
                                         source_type='approved_hour', source_id=row.pk))
                continue
            value['rate_status'] = 'recorded_positive_rate' if row.hourly_cost_rate > 0 else 'zero_rate_requires_confirmation'
            if row.hourly_cost_rate == 0:
                issues.append(_issue('labor_rate_unconfirmed', 'The recorded rate is zero; no rate or labour cost was inferred from attendance or salary.',
                                     source_type='approved_hour', source_id=row.pk))
            if row.labor_actual_cost != (row.hours * row.hourly_cost_rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):
                issues.append(_issue('labor_cost_inconsistent', 'Approved labour cost differs from its recorded hours and rate; reconcile the source.',
                                     source_type='approved_hour', source_id=row.pk))
            hours.append(value)

        eligible_hour_ids = {str(row['id']) for row in hours}
        cost_rows = CostLedgerEntry.objects.filter(project_id=enterprise.pk).select_related(
            'control_account__wbs_node', 'wbs_node', 'reporting_period').order_by('entry_key', 'pk')
        for row in cost_rows.iterator(chunk_size=500):
            value = _json({key: getattr(row, key) for key in (
                'id', 'project_id', 'entry_key', 'entry_type', 'entry_date', 'amount', 'currency',
                'control_account_id', 'wbs_node_id', 'reporting_period_id', 'source_type', 'source_id',
                'source_reference', 'status', 'metadata', 'created_by_id', 'is_deleted', 'created_at', 'updated_at')})
            value.update(bridge(row))
            reason = ('deleted' if row.is_deleted else 'future' if row.entry_date > data_date
                      else row.status if row.status != 'posted' else 'not_actual' if row.entry_type not in {'actual', 'adjustment'}
                      else _scope_problem(row, enterprise.pk, row.entry_date))
            if reason is None and row.wbs_node_id and row.wbs_node.project_id != enterprise.pk:
                reason = 'foreign_wbs'
            if reason is None and row.control_account_id and row.wbs_node_id != row.control_account.wbs_node_id:
                reason = 'inconsistent_account_wbs'
            if reason is None and row.source_type == 'approved_hour' and row.source_id not in eligible_hour_ids:
                reason = 'labor_source_not_approved'
            coverage['costs']['seen'] += 1
            coverage['costs'][reason or 'included'] += 1
            manifest['costs'].append({**value, 'included': reason is None, 'exclusion_reason': reason})
            if reason:
                if reason not in {'deleted', 'future', 'reversed', 'not_actual'}:
                    issues.append(_issue('cost_' + reason, 'This posted cost has inconsistent project or approved labour evidence; reconcile its source.',
                                         source_type='cost_ledger', source_id=row.pk))
                continue
            if not row.control_account_id or not row.reporting_period_id:
                issues.append(_issue('cost_mapping_incomplete', 'This project cost needs an explicit control-account and reporting-period mapping.',
                                     source_type='cost_ledger', source_id=row.pk))
            if not row.currency:
                issues.append(_issue('cost_currency_missing', 'The recorded cost has no currency; it is not included in a monetary total.',
                                     source_type='cost_ledger', source_id=row.pk))
            costs.append(value)

        posted_hour_ids = {row['source_id'] for row in costs if row['source_type'] == 'approved_hour'}
        unposted = eligible_hour_ids - posted_hour_ids
        if unposted:
            issues.append(_issue('approved_hours_not_posted', 'Approved hours have no eligible labour ledger posting; cost was not calculated or added.', count=len(unposted)))

    for kind in ('hours', 'costs'):
        for reason, count in sorted(coverage[kind].items()):
            if reason not in {'seen', 'included'} and count:
                issues.append(_issue('excluded_' + kind, f'{count} {kind} source record(s) excluded: {reason}.', source_type=kind, count=count))
    if not hours:
        issues.append(_issue('approved_hours_unavailable', 'No eligible approved hours are recorded through the data date; actual hours are unknown.'))
    if not costs:
        issues.append(_issue('posted_costs_unavailable', 'No eligible posted actual costs are recorded through the data date; actual cost is unknown.'))
    totals = defaultdict(Decimal)
    for row in costs:
        if row['currency']:
            totals[row['currency']] += Decimal(row['amount'])
    if len(totals) > 1:
        issues.append(_issue('multiple_cost_currencies', 'Costs are reported separately by recorded currency; no exchange rate or consolidated amount was assumed.'))
    unmapped = sum(not row['account_activity_ids'] for row in hours + costs)
    if unmapped:
        issues.append(_issue('activity_bridge_missing', 'Source records lack an exact WBS link to this baseline; actuals remain at source/account level.', count=unmapped))
    manifest['coverage'] = {kind: dict(sorted(values.items())) for kind, values in coverage.items()}
    manifest['issues'] = issues
    return {'hours': hours, 'costs': costs,
            'total_hours': str(sum((Decimal(row['hours']) for row in hours), Decimal('0'))) if hours else None,
            'costs_by_currency': {currency: str(amount) for currency, amount in sorted(totals.items())},
            'coverage': manifest['coverage'], 'issues': issues, 'fingerprint': _fingerprint(manifest), 'manifest': manifest}
