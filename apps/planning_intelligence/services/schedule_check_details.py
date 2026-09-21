"""Read-only, actionable findings shared by draft and authoritative assurance."""
from copy import deepcopy
from datetime import date


TIMING_WARNING_CODES = frozenset({'negative_float', 'contract_finish_overrun', 'contractual_finish_overrun'})


def apply_timing_warning_policy(finding):
    """Timing feasibility is advisory, including findings saved by older code."""
    result = deepcopy(finding)
    if (result.get('code') or result.get('rule')) in TIMING_WARNING_CODES and result.get('severity') != 'pass':
        result.update(severity='warning', blocking=False)
        result['resolution'] = (
            'Keep the registered project dates. Review source durations, calendars and dependency links with Project Control. '
            'This timing warning does not prevent submission, approval or baseline publication.'
        )
    return result


def activity_check_rows(activities):
    """Keep external IDs usable after a rejected submission rolls back its rows."""
    return [{
        'id': row.external_id, 'activity_id': row.pk, 'title': row.name,
        'planned_start_date': row.planned_start.isoformat() if row.planned_start else None,
        'planned_finish_date': row.planned_finish.isoformat() if row.planned_finish else None,
        'duration_days': float(row.duration_days),
        'total_float_days': float(row.total_float_days) if row.total_float_days is not None else None,
        **{key: (row.metadata or {}).get(key) for key in ('parent_deliverable_id', 'workflow_stage_code')},
    } for row in activities]


def relationship_check_rows(relationships):
    return [{
        'relationship_id': row.pk, 'predecessor_task_id': row.predecessor.external_id,
        'successor_task_id': row.successor.external_id, 'type': row.relationship_type,
        'lag_days': float(row.lag_days),
    } for row in relationships]


def _affected(tasks, task_ids):
    ids = set(task_ids)
    fields = ('title', 'activity_id', 'planned_start_date', 'planned_finish_date',
              'duration_days', 'total_float_days', 'parent_deliverable_id', 'workflow_stage_code')
    return [{'task_id': task['id'], **{key: task[key] for key in fields if key in task}}
            for task in tasks if task['id'] in ids]


def schedule_timing_blockers(tasks, target_finish, calendar, *, relationships=None):
    """Report CPM findings; timing is advisory and integrity errors still block.

    The historical function name is retained for callers. Classify by severity,
    rather than treating every returned finding as a submission blocker.
    """
    findings = []
    negative = [task for task in tasks if task.get('total_float_days') is not None and task['total_float_days'] < 0]
    if negative:
        minimum = min(task['total_float_days'] for task in negative)
        findings.append({
            'code': 'negative_float', 'severity': 'warning',
            'message': f'{len(negative)} activities have negative float against the project finish date.',
            'task_ids': [task['id'] for task in negative], 'minimum_float_days': minimum,
            'field': 'duration_days',
            'resolution': 'Review the highlighted activities and their predecessors. Correct their start dates, durations or dependency links against the approved plan, then save and check again.',
        })
    target = target_finish.isoformat() if target_finish else None
    beyond = [task for task in tasks if target and task.get('planned_finish_date') and task['planned_finish_date'] > target]
    if beyond:
        forecast = max(task['planned_finish_date'] for task in beyond)
        variance = (date.fromisoformat(forecast) - target_finish).days
        working_variance = (max(0, calendar.index_of(date.fromisoformat(forecast))
                                - calendar.index_of(calendar.on_or_before(target_finish))) if calendar else None)
        findings.append({
            'code': 'contract_finish_overrun', 'severity': 'warning',
            'message': f'Forecast finish {forecast} exceeds the project finish {target} by {variance} calendar days.',
            'task_ids': [task['id'] for task in beyond], 'field': 'planned_start_date',
            'target_finish_date': target, 'forecast_finish_date': forecast,
            'variance_calendar_days': variance, 'variance_working_days': working_variance,
            'resolution': 'Review the activities finishing after the target and their predecessor chains. Correct the planned timing or approved logic, then save. Change the project finish only through an approved project change.',
        })
    if relationships is None:
        relationships = [{
            'predecessor_task_id': link['task_id'], 'successor_task_id': task['id'],
            'type': link.get('type', 'FS'), 'lag_days': link.get('lag_days', 0),
        } for task in tasks for link in task.get('dependency_details') or []
            if link.get('task_id') in (task.get('depends_on') or [])]
    sf = [link for link in relationships if link['type'] == 'SF']
    if sf:
        findings.append({
            'code': 'start_to_finish', 'severity': 'critical',
            'message': f'{len(sf)} Start-to-Finish relationships require correction.',
            'task_ids': list(dict.fromkeys(link['successor_task_id'] for link in sf)),
            'relationships': deepcopy(sf), 'field': 'depends_on',
            'resolution': 'Review these predecessor links in Logic / Schedule Controls and replace Start-to-Finish with the relationship required by the approved plan. Save and check the resulting sequence.',
        })
    for finding in findings:
        finding['task_count'] = len(finding['task_ids'])
        finding['activities'] = list(finding['task_ids'])
        finding['affected_activities'] = _affected(tasks, finding['task_ids'])
    return [apply_timing_warning_policy(finding) for finding in findings]


def enrich_schedule_findings(findings, tasks, relationships, target_finish, calendar):
    """Attach stable navigation targets to authoritative assurance findings."""
    timing = {row['code']: row for row in schedule_timing_blockers(
        tasks, target_finish, calendar, relationships=relationships,
    )}
    result = []
    for original in findings:
        finding = apply_timing_warning_policy(original)
        code = finding['code']
        if code in timing:
            finding.update(timing[code])
        else:
            ids = list(finding.get('task_ids') or finding.get('activities') or [])
            if finding.get('resources'):
                finding['resource_ids'] = [row['resource_id'] for row in finding['resources']]
                ids.extend(key for row in finding['resources'] for key in row.get('task_ids') or [])
            if finding.get('relationship_id'):
                ids.extend(key for row in relationships if row['relationship_id'] == finding['relationship_id']
                           for key in (row['predecessor_task_id'], row['successor_task_id']))
            finding['task_ids'] = list(dict.fromkeys(ids))
            finding['task_count'] = len(finding['task_ids'])
            finding['affected_activities'] = _affected(tasks, finding['task_ids'])
            if code == 'resource_overallocation':
                finding.update(
                    field='assignee_id',
                    resolution='Review the listed resources and overlapping activities in Resources. Reassign work, adjust planned effort or timing, or correct approved capacity, then save and check again.',
                )
            elif code == 'zero_duration_tasks':
                finding.update(field='duration_days', resolution='Enter a positive working-day duration for each task. Use a milestone only for a zero-duration event.')
            elif code in {'dependency_cycle', 'duplicate_relationships', 'relationship_outside_version'}:
                finding.update(field='depends_on', resolution='Review the highlighted relationships in Logic and correct the predecessor links, then calculate and check again.')
        result.append(finding)
    return result
