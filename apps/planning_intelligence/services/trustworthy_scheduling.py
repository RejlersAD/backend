"""Phase 3 schedule assurance: validate, simulate, compare, and gate approval."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict, deque
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from ..models import ScheduleAssuranceReview


def _finding(code, severity, message, **details):
    return {'code': code, 'severity': severity, 'message': message, **details}


def _network_validation(version, activities, relationships):
    findings = []
    ids = {row.id for row in activities}
    incoming = defaultdict(list)
    outgoing = defaultdict(list)
    indegree = {row.id: 0 for row in activities}
    duplicates = set()
    seen = set()
    for link in relationships:
        key = (link.predecessor_id, link.successor_id, link.relationship_type)
        if key in seen:
            duplicates.add(key)
        seen.add(key)
        if link.predecessor_id not in ids or link.successor_id not in ids:
            findings.append(_finding('relationship_outside_version', 'critical', 'A relationship references an activity outside this version.', relationship_id=link.id))
            continue
        outgoing[link.predecessor_id].append(link.successor_id)
        incoming[link.successor_id].append(link.predecessor_id)
        indegree[link.successor_id] += 1
    if duplicates:
        findings.append(_finding('duplicate_relationships', 'critical', f'{len(duplicates)} duplicate relationship keys were detected.'))

    queue = deque(key for key, degree in indegree.items() if degree == 0)
    visited = 0
    while queue:
        key = queue.popleft()
        visited += 1
        for successor in outgoing[key]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                queue.append(successor)
    if visited != len(activities):
        cyclic = [next(row.external_id for row in activities if row.id == key) for key, degree in indegree.items() if degree]
        findings.append(_finding('dependency_cycle', 'critical', 'The network contains a dependency cycle.', activities=cyclic[:25]))

    starts = [row for row in activities if not incoming[row.id]]
    finishes = [row for row in activities if not outgoing[row.id]]
    if len(starts) != 1:
        findings.append(_finding('open_starts', 'warning', f'{len(starts)} open network starts were detected.', activities=[row.external_id for row in starts[:25]]))
    if len(finishes) != 1:
        findings.append(_finding('open_finishes', 'warning', f'{len(finishes)} open network finishes were detected.', activities=[row.external_id for row in finishes[:25]]))

    sf = [link.id for link in relationships if link.relationship_type == 'SF']
    negative_lag = [link.id for link in relationships if link.lag_days < 0]
    if sf:
        findings.append(_finding('start_to_finish', 'critical', f'{len(sf)} Start-to-Finish relationships require correction.', relationship_ids=sf[:25]))
    if negative_lag:
        findings.append(_finding('negative_lag', 'warning', f'{len(negative_lag)} relationships use negative lag.', relationship_ids=negative_lag[:25]))

    long_rows = [row.external_id for row in activities if not row.is_milestone and row.duration_days > 20]
    zero_rows = [row.external_id for row in activities if not row.is_milestone and row.duration_days <= 0]
    hard_constraints = [row.external_id for row in activities if row.constraint_type in {'must_start', 'must_finish'}]
    missing_evidence = [row.external_id for row in activities if not row.is_milestone and not (row.metadata or {}).get('source_references')]
    negative_float = [row.external_id for row in activities if row.total_float_days is not None and row.total_float_days < 0]
    if long_rows:
        findings.append(_finding('long_activities', 'warning', f'{len(long_rows)} activities exceed 20 working days.', activities=long_rows[:25]))
    if zero_rows:
        findings.append(_finding('zero_duration_tasks', 'critical', f'{len(zero_rows)} non-milestone activities have zero duration.', activities=zero_rows[:25]))
    if hard_constraints:
        findings.append(_finding('hard_constraints', 'warning', f'{len(hard_constraints)} activities use hard constraints.', activities=hard_constraints[:25]))
    if missing_evidence:
        findings.append(_finding('missing_activity_evidence', 'warning', f'{len(missing_evidence)} activities have no source-reference metadata.', activities=missing_evidence[:25]))
    if negative_float:
        findings.append(_finding('negative_float', 'critical', f'{len(negative_float)} activities have negative float against the contractual finish.', activities=negative_float[:25]))
    if not findings:
        findings.append(_finding('network_integrity', 'pass', 'Network integrity checks passed.'))
    return {
        'findings': findings,
        'activity_count': len(activities),
        'relationship_count': len(relationships),
        'open_start_count': len(starts),
        'open_finish_count': len(finishes),
    }


def _contract_scenarios(version, activities, calculation_run):
    contract_finish = version.schedule.project.planned_end_date
    forecast = calculation_run.project_finish or version.calculated_finish
    if not contract_finish or not forecast:
        return {'available': False, 'reason': 'Contractual and calculated finish dates are required.', 'scenarios': []}
    calendar_days = (forecast - contract_finish).days
    latest_issue = next((item for item in reversed(calculation_run.issues or []) if item.get('code') == 'contractual_finish_overrun'), {})
    working_days = max(0, int(latest_issue.get('variance_working_days') or 0))
    critical_duration = sum(float(row.duration_days) for row in activities if row.is_critical and not row.is_milestone)
    compression = round((working_days / critical_duration * 100), 2) if critical_duration else 0
    fit = calendar_days <= 0
    scenarios = [
        {
            'code': 'current_network', 'label': 'Current approved logic', 'forecast_finish': forecast.isoformat(),
            'variance_calendar_days': calendar_days, 'variance_working_days': working_days,
            'scope_change': False, 'automatic_change': False,
        },
        {
            'code': 'contract_fit_target', 'label': 'Contract-fit target', 'forecast_finish': contract_finish.isoformat(),
            'required_reduction_working_days': working_days, 'critical_duration_days': round(critical_duration, 2),
            'required_critical_path_compression_pct': compression, 'feasibility': 'met' if fit else ('high_risk' if compression > 20 else 'review_required'),
            'scope_change': False, 'automatic_change': False,
            'note': 'Analytical target only. Durations, logic, calendars, resources, or scope must be changed through an approved revision.',
        },
    ]
    return {
        'available': True, 'contractual_finish': contract_finish.isoformat(), 'forecast_finish': forecast.isoformat(),
        'fits_contract': fit, 'variance_calendar_days': calendar_days, 'variance_working_days': working_days,
        'scenarios': scenarios,
    }


def _working_dates(activity):
    if not activity.planned_start or not activity.planned_finish:
        return []
    calendar = activity.calendar or activity.version.schedule.default_calendar
    weekdays = set((calendar.working_weekdays if calendar else [0, 1, 2, 3, 4]))
    exceptions = {row.date: row.is_working for row in calendar.exceptions.all() if not row.is_deleted} if calendar else {}
    value = activity.planned_start
    dates = []
    while value <= activity.planned_finish:
        if exceptions.get(value, value.weekday() in weekdays):
            dates.append(value)
        value += dt.timedelta(days=1)
    return dates


def _resource_validation(version, activities):
    loads = defaultdict(lambda: defaultdict(Decimal))
    resource_by_id = {}
    assigned_activity_ids = set()
    for activity in activities:
        dates = _working_dates(activity)
        if not dates:
            continue
        for assignment in activity.assignments.all():
            if assignment.is_deleted:
                continue
            assigned_activity_ids.add(activity.id)
            resource_by_id[assignment.resource_id] = assignment.resource
            total = assignment.planned_units or assignment.budgeted_hours
            daily = Decimal(total) / Decimal(len(dates)) if total else Decimal('0')
            for date in dates:
                loads[assignment.resource_id][date] += daily
    overloads = []
    for resource_id, daily_load in loads.items():
        resource = resource_by_id[resource_id]
        capacity = resource.capacity_units_per_day
        overloaded = [(date, demand) for date, demand in daily_load.items() if demand > capacity]
        if overloaded:
            peak_date, peak = max(overloaded, key=lambda item: item[1])
            overloads.append({
                'resource_id': resource.id, 'resource_code': resource.code, 'resource_name': resource.name,
                'capacity_per_day': float(capacity), 'overloaded_day_count': len(overloaded),
                'peak_date': peak_date.isoformat(), 'peak_demand': round(float(peak), 2),
            })
    unassigned = [row.external_id for row in activities if not row.is_milestone and row.id not in assigned_activity_ids]
    findings = []
    if overloads:
        findings.append(_finding('resource_overallocation', 'critical', f'{len(overloads)} resources exceed daily capacity.', resources=overloads))
    if unassigned:
        findings.append(_finding('unassigned_activities', 'warning', f'{len(unassigned)} activities have no resource assignment.', activities=unassigned[:25]))
    if not findings:
        findings.append(_finding('resource_capacity', 'pass', 'Assigned resources remain within stored daily capacity.'))
    return {'findings': findings, 'overloads': overloads, 'unassigned_activity_count': len(unassigned), 'resource_count': len(resource_by_id)}


def _change_comparison(version, activities, relationships):
    reference = version.parent_version
    reference_type = 'parent_version'
    if not reference:
        baseline = version.schedule.baselines.filter(is_deleted=False).exclude(source_version=version).first()
        reference = baseline.source_version if baseline else None
        reference_type = 'baseline' if reference else 'none'
    if not reference:
        return {'available': False, 'reference_type': 'none', 'message': 'No parent version or earlier baseline is available.'}
    current = {row.external_id: row for row in activities}
    previous = {row.external_id: row for row in reference.activities.filter(is_deleted=False)}
    added = sorted(set(current) - set(previous))
    removed = sorted(set(previous) - set(current))
    duration_changes = [
        {'activity_id': key, 'before': float(previous[key].duration_days), 'after': float(current[key].duration_days),
         'delta': float(current[key].duration_days - previous[key].duration_days)}
        for key in sorted(set(current) & set(previous)) if current[key].duration_days != previous[key].duration_days
    ]
    def links(rows):
        return {(row.predecessor.external_id, row.successor.external_id, row.relationship_type, float(row.lag_days)) for row in rows}
    current_links = links(relationships)
    previous_links = links(reference.relationships.filter(is_deleted=False).select_related('predecessor', 'successor'))
    finish_delta = None
    if version.calculated_finish and reference.calculated_finish:
        finish_delta = (version.calculated_finish - reference.calculated_finish).days
    return {
        'available': True, 'reference_type': reference_type, 'reference_version_id': reference.id,
        'reference_version': reference.version, 'added_activities': added, 'removed_activities': removed,
        'duration_changes': duration_changes, 'added_relationships': [list(row) for row in sorted(current_links - previous_links)],
        'removed_relationships': [list(row) for row in sorted(previous_links - current_links)],
        'finish_variance_calendar_days': finish_delta,
        'summary': {
            'added_activity_count': len(added), 'removed_activity_count': len(removed),
            'duration_change_count': len(duration_changes), 'added_relationship_count': len(current_links - previous_links),
            'removed_relationship_count': len(previous_links - current_links),
        },
    }


@transaction.atomic
def run_schedule_assurance(version, *, requested_by=None):
    # Lock only the version row. parent_version is nullable and PostgreSQL
    # rejects FOR UPDATE when it is included through an outer join.
    version = type(version).objects.select_for_update().get(pk=version.pk)
    if version.status != 'calculated' or not version.calculated_at:
        raise ValueError('Calculate the schedule before running Phase 3 assurance.')
    calculation_run = version.calculation_runs.filter(status='succeeded', is_deleted=False).first()
    if not calculation_run:
        raise ValueError('No successful CPM calculation exists for this version.')
    activities = list(version.activities.filter(is_deleted=False).select_related(
        'calendar', 'version__schedule__default_calendar',
    ).prefetch_related(
        'assignments__resource', 'calendar__exceptions', 'version__schedule__default_calendar__exceptions',
    ))
    relationships = list(version.relationships.filter(is_deleted=False).select_related('predecessor', 'successor'))
    from .operational_jobs import assurance_state_fingerprint
    input_fingerprint = assurance_state_fingerprint(version)
    network = _network_validation(version, activities, relationships)
    contract = _contract_scenarios(version, activities, calculation_run)
    resources = _resource_validation(version, activities)
    comparison = _change_comparison(version, activities, relationships)
    findings = network['findings'] + resources['findings']
    if contract.get('available') and not contract.get('fits_contract'):
        findings.append(_finding('contract_finish_overrun', 'critical', f"Forecast exceeds contractual finish by {contract['variance_calendar_days']} calendar days."))
    blockers = [row for row in findings if row['severity'] == 'critical']
    warnings = [row for row in findings if row['severity'] == 'warning']
    version.assurance_reviews.filter(status__in=['draft', 'ready', 'approved']).update(status='superseded')
    return ScheduleAssuranceReview.objects.create(
        version=version, calculation_run=calculation_run, status='ready' if not blockers else 'draft',
        network_validation=network, contract_scenarios=contract, resource_validation=resources,
        change_comparison=comparison, blockers=blockers, warnings=warnings,
        calculated_state_at=version.calculated_at, input_fingerprint=input_fingerprint,
    )


def current_assurance(version):
    from .operational_jobs import assurance_state_fingerprint
    review = version.assurance_reviews.filter(
        status__in=['draft', 'ready', 'approved'], calculated_state_at=version.calculated_at,
        input_fingerprint=assurance_state_fingerprint(version),
    ).first()
    return review


@transaction.atomic
def approve_schedule_assurance(version, user):
    review = current_assurance(version)
    if not review:
        raise ValueError('Run Phase 3 assurance for the latest CPM calculation first.')
    if review.blockers:
        raise ValueError('Resolve all critical Phase 3 assurance blockers before approval.')
    if review.status not in {'ready', 'approved'}:
        raise ValueError('The Phase 3 assurance review is not ready for approval.')
    review.status = 'approved'
    review.approved_by = user
    review.approved_at = timezone.now()
    review.save(update_fields=['status', 'approved_by', 'approved_at', 'updated_at'])
    return review
