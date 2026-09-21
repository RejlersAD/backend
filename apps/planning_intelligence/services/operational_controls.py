"""Baseline-bound operational reporting with explicit earning policy and immutable publication.

The existing Project Control reporting period remains the financial boundary.
Publishing this observation neither posts money nor locks a financial period.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Max
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.rbac.action_policy import module_action_allowed
from apps.project_control.access import can_approve_commercial
from apps.project_control.models import ReportingPeriod, ReportingPeriodAudit
from ..access import can_write_project, can_final_approve_defaults
from ..models import OperationalControlReport, OperationalEarningPolicy, PlanningAuditEvent, PlanningProject, ScheduleBaseline
from .audit import record_event
from .operational_jobs import canonical_fingerprint
from .project_controls import _json_safe
from .schedule_approval import ScheduleApprovalError


MONEY_FIELDS = {'currency', 'bac', 'planned_value', 'earned_value', 'actual_cost', 'spi', 'cpi',
                'schedule_variance', 'cost_variance', 'eac', 'etc', 'vac', 'budget', 'budgeted_cost',
                'pv', 'ev', 'ac', 'costs', 'costs_by_currency', 'planned_curve'}
MONEY_FIELDS.update({'hourly_cost_rate', 'labor_actual_cost'})


def _conflict(message, code='operational_control_conflict'):
    raise ScheduleApprovalError(message, code=code)


def _write(project, actor):
    return can_write_project(actor, project) and module_action_allowed(actor, 'planning_package', 'update')


def _commercial(project, actor):
    from .operational_actuals import can_view_commercial_actuals
    return can_view_commercial_actuals(actor, project)


def _required(data, *keys):
    absent = [key for key in keys if key not in data]
    if absent:
        raise ValidationError({key: 'This field is required.' for key in absent})


def _baseline(project, key):
    return get_object_or_404(ScheduleBaseline, pk=key, schedule__project=project,
        schedule__is_deleted=False, is_deleted=False, approved_at__isnull=False, approved_by__isnull=False)


def _activities(baseline):
    return {row['id']: row for row in baseline.snapshot.get('activities', [])}


def _validate_policy(baseline, definition):
    activities = _activities(baseline)
    seen = set()
    for row in definition['activities']:
        key = row['activity_id']
        if key not in activities or key in seen:
            raise ValidationError('Each earning rule must reference one distinct activity in this frozen baseline.')
        seen.add(key)
        if row['method'] == 'quantity' and (not row.get('planned_quantity') or not row.get('quantity_unit')):
            raise ValidationError('Quantity earning requires a positive planned quantity and its unit.')
        budget = row.get('budget')
        if budget is not None and not definition.get('currency'):
            raise ValidationError('Declare the currency for every monetary budget.')
        points = row.get('planned_value') or []
        if row['pv_method'] != 'explicit_points' and points:
            raise ValidationError('Planned value points require the explicit points method.')
        if row['pv_method'] == 'explicit_points':
            if budget is None or not points:
                raise ValidationError('Explicit planned value requires a budget and cumulative dated points.')
            last_date, last_value = None, Decimal('-1')
            for point in points:
                if ((last_date and point['date'] <= last_date) or point['value'] < last_value
                        or point['value'] > budget):
                    raise ValidationError('Planned values must increase in date, never decrease, and stay within budget.')
                last_date, last_value = point['date'], point['value']
            if last_value != budget:
                raise ValidationError('The final planned value point must equal the activity budget.')
    return _json_safe(definition)


def _period_snapshot(period):
    return {'id': period.pk, 'project_id': period.project_id, 'name': period.name,
            'start_date': period.start_date.isoformat(), 'end_date': period.end_date.isoformat(),
            'data_date': period.data_date.isoformat()}


def _period_current(report):
    if report.period_snapshot != _period_snapshot(report.reporting_period):
        _conflict('The reporting dates changed. Return to draft and save the update against the current period.',
                  'operational_period_changed')


def _validate_observations(report, observations):
    activities = _activities(report.baseline)
    data_date = date.fromisoformat(report.period_snapshot['data_date'])
    seen = set()
    methods = {row['activity_id']: row for row in report.policy.definition['activities']}
    for row in observations:
        key = row['activity_id']
        if key not in activities or key in seen:
            raise ValidationError('Update only distinct activities belonging to this frozen baseline.')
        seen.add(key)
        start, finish = row.get('actual_start'), row.get('actual_finish')
        if (start and start > data_date) or (finish and finish > data_date):
            raise ValidationError('Actual dates cannot be later than the reporting data date.')
        if finish and (not start or finish < start):
            raise ValidationError('Actual finish requires an actual start on or before it.')
        if finish and row.get('remaining_duration_days') not in (None, Decimal('0')):
            raise ValidationError('Completed work cannot have a positive remaining duration.')
        if finish and row.get('physical_progress_pct') not in (None, Decimal('100')):
            raise ValidationError('Completed work cannot have a physical progress below 100%.')
        if (row.get('physical_progress_pct') or row.get('installed_quantity')) and not start:
            raise ValidationError('Reported earned work requires its actual start date.')
        planned = methods.get(key, {}).get('planned_quantity')
        if planned is not None and row.get('installed_quantity') is not None and row['installed_quantity'] > Decimal(str(planned)):
            raise ValidationError('Installed quantity exceeds the approved earning basis. Revise the policy first.')
        values = [value for field, value in row.items() if field not in {'activity_id', 'notes', 'evidence'}]
        if any(value is not None for value in values) and not row.get('evidence', '').strip():
            raise ValidationError('Add a report, document or other supporting reference for each reported activity.')
    return _json_safe(observations)


def _report_preview(report):
    from .operational_actuals import read_operational_actuals
    from .operational_calculations import calculate_operational_report
    if report.status == 'published':
        return deepcopy(report.publication)
    sources = read_operational_actuals(report.project, report.baseline,
                                      date.fromisoformat(report.period_snapshot['data_date']))
    preview = calculate_operational_report(report.baseline.snapshot, report.policy.definition,
        report.observations, report.period_snapshot['data_date'], sources,
        cost_coverage_confirmed=report.cost_coverage_confirmed)
    fingerprint = canonical_fingerprint({'baseline': report.baseline.snapshot, 'policy': report.policy.definition,
        'policy_id': report.policy_id, 'period': report.period_snapshot, 'sources': sources['fingerprint'],
        'observations': report.observations, 'cost_coverage_confirmed': report.cost_coverage_confirmed})
    return {'preview': preview, 'source_actuals': sources, 'source_fingerprint': fingerprint,
            'baseline_fingerprint': canonical_fingerprint(report.baseline.snapshot),
            'policy_snapshot': deepcopy(report.policy.definition), 'rule_version': 'operational-controls/1.0'}


def _money_policy(policy):
    return any(row.get('budget') is not None for row in policy.definition.get('activities', []))


def _can_approve_policy(policy, actor):
    return bool(policy.status == 'draft' and actor.pk != policy.created_by_id
        and can_final_approve_defaults(actor, policy.project)
        and (not _money_policy(policy) or can_approve_commercial(actor, policy.project.enterprise_project)))


def _can_publish(report, actor):
    return bool(report.status == 'submitted' and actor.pk not in {report.submitted_by_id, report.created_by_id, report.edited_by_id}
        and can_final_approve_defaults(actor, report.project)
        and (not _money_policy(report.policy) or _commercial(report.project, actor))
        and not PlanningAuditEvent.objects.filter(project=report.project, entity_type='OperationalControlReport',
            entity_id=str(report.pk), action='operational.save_report', actor=actor).exists())


def _redact_money(value):
    if isinstance(value, list):
        return [_redact_money(row) for row in value]
    if isinstance(value, dict):
        return {key: None if key in MONEY_FIELDS else _redact_money(item)
                for key, item in value.items() if key not in {'manifest', 'policy_snapshot'}}
    return value


def _report_summary(report, actor):
    writable = _write(report.project, actor)
    return {'revision': report.revision, 'status': report.status,
        'baseline_id': report.baseline_id, 'policy_id': report.policy_id,
        'reporting_period_id': report.reporting_period_id, **report.period_snapshot,
        'id': report.pk, 'submitted_by_id': report.submitted_by_id,
        'published_by_id': report.published_by_id, 'published_at': report.published_at,
        'supersedes_id': report.supersedes_id, 'created_by_id': report.created_by_id,
        'permissions': {'can_save': writable and report.status == 'draft',
            'can_submit': writable and report.status == 'draft', 'can_publish': _can_publish(report, actor),
            'can_return': report.status == 'submitted' and (writable or can_final_approve_defaults(actor, report.project)),
            'can_correct': writable and report.status == 'published'}}


def operational_control_state(project, actor, *, baseline_id=None, report_id=None):
    baselines = list(ScheduleBaseline.objects.filter(schedule__project=project, is_deleted=False,
        schedule__is_deleted=False, approved_at__isnull=False, approved_by__isnull=False).order_by('-approved_at', '-pk'))
    report = None
    if report_id:
        report = get_object_or_404(OperationalControlReport.objects.select_related(
            'project__enterprise_project', 'baseline', 'policy', 'reporting_period'), pk=report_id, project=project)
        if baseline_id and int(baseline_id) != report.baseline_id:
            raise ValidationError('The selected report belongs to a different baseline.')
        baseline_id = report.baseline_id
    baseline = _baseline(project, baseline_id) if baseline_id else next(
        (row for row in baselines if row.source_version_id == project.master_schedule_version_id),
        baselines[0] if baselines else None)
    policies = list(OperationalEarningPolicy.objects.filter(project=project, baseline=baseline).select_related('project__enterprise_project')) if baseline else []
    reports = list(OperationalControlReport.objects.filter(project=project, baseline=baseline)
                   .select_related('policy', 'project__enterprise_project').order_by('-created_at', '-pk')) if baseline else []
    if report is None and reports:
        report = reports[0]
    money = _commercial(project, actor)
    report_data = None
    if report:
        report_data = {**_report_summary(report, actor), 'observations': deepcopy(report.observations),
            'cost_coverage_confirmed': report.cost_coverage_confirmed, 'notes': report.notes,
            **_report_preview(report)}
        report_data.pop('policy_snapshot', None)
        # Provenance rows are returned, but the internal source manifest is not a public API.
        report_data.get('source_actuals', {}).pop('manifest', None)
    latest = {}
    for item in reports:
        if item.status == 'published':
            latest.setdefault(item.reporting_period_id, item)
    curves = []
    for item in sorted(latest.values(), key=lambda row: (row.period_snapshot['data_date'], row.pk)):
        metrics = item.publication['preview']['metrics']
        curves.append({'date': item.period_snapshot['data_date'], 'report_id': item.pk, 'revision': item.revision,
            'policy_id': item.policy_id, 'currency': metrics.get('currency'),
            'pv': metrics.get('planned_value'), 'ev': metrics.get('earned_value'), 'ac': metrics.get('actual_cost'),
            'progress_pct': metrics.get('progress_pct')})
    data = {'baseline': {'id': baseline.pk, 'name': baseline.name, 'version_id': baseline.source_version_id} if baseline else None,
        'baselines': [{'id': row.pk, 'name': row.name, 'version_id': row.source_version_id} for row in baselines],
        'activities': [{key: row.get(key) for key in ('id', 'external_id', 'name', 'planned_start', 'planned_finish')}
                       for row in _activities(baseline).values()] if baseline else [],
        'policies': [{'id': row.pk, 'revision': row.revision, 'status': row.status, 'name': row.name,
            'definition': row.definition, 'created_by_id': row.created_by_id, 'approved_by_id': row.approved_by_id,
            'can_approve': _can_approve_policy(row, actor)} for row in policies],
        'reports': [_report_summary(row, actor) for row in reports], 'report': report_data, 'curves': curves,
        'reporting_periods': [{**_period_snapshot(row), 'status': row.status} for row in
            ReportingPeriod.objects.filter(project_id=project.enterprise_project_id, is_deleted=False).order_by('-sequence')]
            if project.enterprise_project_id else [],
        'permissions': {'can_write': _write(project, actor), 'can_approve': can_final_approve_defaults(actor, project),
                        'can_view_costs': money}, 'current_user_id': actor.pk,
        'issues': ([{'code': 'baseline_required', 'message': 'Publish an approved baseline before recording operational controls.'}] if not baseline else [])
            + ([{'code': 'enterprise_project_required', 'message': 'Link this planning workspace to its enterprise project for reporting periods and actuals.'}]
               if not project.enterprise_project_id else []),
        'financial_period_policy': 'Operational publication preserves an observation; financial reconciliation and locking remain in Cost & Commercial.'}
    return _json_safe(data if money else _redact_money(data))


def _create_period(project, actor, data):
    if not project.enterprise_project_id:
        raise ValidationError('Link an enterprise project before starting operational reporting.')
    if data.get('reporting_period_id'):
        if data.get('period'):
            raise ValidationError('Select an existing period or define a new one, not both.')
        return get_object_or_404(ReportingPeriod, pk=data['reporting_period_id'],
                                  project_id=project.enterprise_project_id, is_deleted=False)
    _required(data, 'period')
    from apps.core.project_models import Project
    Project.objects.select_for_update().get(pk=project.enterprise_project_id)
    periods = ReportingPeriod.objects.filter(project_id=project.enterprise_project_id, is_deleted=False)
    if periods.filter(status__in=['open', 'reopened']).exists():
        raise ValidationError('Select the existing open reporting period. Financial periods are managed in Cost & Commercial.')
    values = data['period']
    if not values['start_date'] <= values['data_date'] <= values['end_date']:
        raise ValidationError('The data date must be within the reporting period.')
    if periods.filter(start_date__lte=values['end_date'], end_date__gte=values['start_date']).exists():
        raise ValidationError('These dates overlap an existing project reporting period; select it instead.')
    period = ReportingPeriod(project_id=project.enterprise_project_id,
        sequence=(periods.aggregate(value=Max('sequence'))['value'] or 0) + 1, created_by=actor, **values)
    period.full_clean()
    period.save()
    ReportingPeriodAudit.objects.create(period=period, actor=actor, action='created', to_status='open')
    return period


@transaction.atomic
def operational_control_command(project, actor, data):
    project = PlanningProject.objects.select_for_update().get(pk=project.pk, is_deleted=False)
    action = data['action']
    approval = action in {'approve_policy', 'publish_report', 'return_report'}
    if not _write(project, actor) and not (approval and can_final_approve_defaults(actor, project)):
        raise PermissionDenied('You cannot change operational controls for this project.')
    entity, before = None, {}
    if action == 'create_policy':
        _required(data, 'baseline_id', 'name', 'definition')
        baseline = _baseline(project, data['baseline_id'])
        definition = _validate_policy(baseline, data['definition'])
        if any(row.get('budget') is not None for row in definition['activities']) and not _commercial(project, actor):
            raise PermissionDenied('Commercial access is required to define a monetary earning basis.')
        entity = OperationalEarningPolicy.objects.create(project=project, baseline=baseline, name=data['name'],
            definition=definition, baseline_fingerprint=canonical_fingerprint(baseline.snapshot), created_by=actor)
    elif action == 'approve_policy':
        _required(data, 'policy_id', 'revision', 'reason')
        entity = get_object_or_404(OperationalEarningPolicy.objects.select_for_update(), project=project, pk=data['policy_id'])
        if not _can_approve_policy(entity, actor):
            raise PermissionDenied('An independent project authority must approve this earning policy; monetary budgets also require commercial approval authority.')
        if data['revision'] != entity.revision:
            _conflict('The earning policy changed. Refresh before approval.')
        if entity.baseline_fingerprint != canonical_fingerprint(entity.baseline.snapshot):
            _conflict('The frozen baseline changed. Create a policy against the verified baseline.')
        if not data['reason'].strip():
            raise ValidationError('Record the basis for approving the earning policy.')
        entity.status, entity.approved_by, entity.approved_at = 'approved', actor, timezone.now()
        entity.approval_reason = data['reason']
        entity.revision += 1
        entity.save()
    elif action == 'create_report':
        _required(data, 'baseline_id', 'policy_id')
        baseline = _baseline(project, data['baseline_id'])
        policy = get_object_or_404(OperationalEarningPolicy, project=project, baseline=baseline,
                                   pk=data['policy_id'], status='approved')
        period = _create_period(project, actor, data)
        if OperationalControlReport.objects.filter(baseline=baseline, reporting_period=period).exists():
            raise ValidationError('This period already has a report. Open it or create a correction from its published report.')
        entity = OperationalControlReport.objects.create(project=project, baseline=baseline, policy=policy,
            reporting_period=period, period_snapshot=_period_snapshot(period), created_by=actor)
    else:
        _required(data, 'report_id')
        entity = get_object_or_404(OperationalControlReport.objects.select_for_update(), pk=data['report_id'], project=project)
        before = {'status': entity.status, 'revision': entity.revision, 'observations': entity.observations}
        if action == 'correction_report':
            if entity.status != 'published' or entity.corrections.exists():
                _conflict('Create a correction from the latest published report only.')
            if not data.get('reason', '').strip():
                raise ValidationError('Explain the reporting correction.')
            entity = OperationalControlReport.objects.create(project=project, baseline=entity.baseline,
                policy=entity.policy, reporting_period=entity.reporting_period, period_snapshot=entity.period_snapshot,
                observations=entity.observations, cost_coverage_confirmed=entity.cost_coverage_confirmed,
                notes=entity.notes, reason=data['reason'], supersedes=entity, created_by=actor)
        else:
            _required(data, 'revision')
            if data['revision'] != entity.revision:
                _conflict('This report changed. Refresh before saving or deciding it.', 'operational_revision_stale')
            if entity.status == 'published':
                _conflict('Published reports are immutable. Create a correction.')
            if action == 'save_report':
                if entity.status != 'draft':
                    _conflict('Return this submitted report to draft before editing.')
                _required(data, 'observations')
                entity.period_snapshot = _period_snapshot(entity.reporting_period)
                entity.observations = _validate_observations(entity, data['observations'])
                entity.edited_by = actor
                if data.get('cost_coverage_confirmed') and not _commercial(project, actor):
                    raise PermissionDenied('Commercial access is required to confirm cost coverage.')
                entity.cost_coverage_confirmed = data.get('cost_coverage_confirmed', entity.cost_coverage_confirmed)
                entity.notes = data.get('notes', entity.notes)
            elif action == 'return_report':
                if entity.status != 'submitted' or not data.get('reason', '').strip():
                    raise ValidationError('A submitted report and a return reason are required.')
                entity.status, entity.reason, entity.submission_fingerprint = 'draft', data['reason'], ''
            elif action in {'submit_report', 'publish_report'}:
                _required(data, 'source_fingerprint')
                _period_current(entity)
                if entity.policy.status != 'approved' or entity.policy.baseline_fingerprint != canonical_fingerprint(entity.baseline.snapshot):
                    _conflict('The earning policy or approved baseline is no longer valid.')
                snapshot = _report_preview(entity)
                if snapshot['source_fingerprint'] != data['source_fingerprint']:
                    _conflict('Source actuals or report inputs changed. Refresh and review the new figures.', 'operational_sources_changed')
                if action == 'submit_report':
                    if entity.status != 'draft':
                        _conflict('Only draft reports can be submitted.')
                    measurements = {'actual_start', 'actual_finish', 'physical_progress_pct',
                                    'installed_quantity', 'remaining_duration_days'}
                    if not any(row.get('evidence') and any(row.get(key) is not None for key in measurements)
                               for row in entity.observations):
                        raise ValidationError('Record at least one activity observation before submitting.')
                    entity.status, entity.submitted_by, entity.submitted_at = 'submitted', actor, timezone.now()
                    entity.submission_fingerprint = snapshot['source_fingerprint']
                else:
                    if not _can_publish(entity, actor):
                        raise PermissionDenied('An independent project authority must publish this submitted report.')
                    if snapshot['source_fingerprint'] != entity.submission_fingerprint:
                        _conflict('Sources changed since submission. Return to draft, review and submit again.', 'operational_submission_stale')
                    if not data.get('reason', '').strip():
                        raise ValidationError('Record the review decision before publication.')
                    entity.publication = snapshot
                    entity.status, entity.published_by, entity.published_at = 'published', actor, timezone.now()
                    entity.reason = data['reason']
            else:
                raise ValidationError('Unsupported operational control action.')
            entity.revision += 1
            entity.save()
    record_event(project=project, actor=actor, action='operational.' + action, entity=entity,
        before=before, after={'status': entity.status, 'revision': entity.revision,
            'definition_fingerprint': canonical_fingerprint(entity.definition)} if isinstance(entity, OperationalEarningPolicy) else
            {'status': entity.status, 'revision': entity.revision, 'observations': entity.observations},
        metadata={'reason': data.get('reason', '')})
    return operational_control_state(project, actor, baseline_id=entity.baseline_id,
        report_id=entity.pk if isinstance(entity, OperationalControlReport) else None)
