"""Project an approved baseline without consulting mutable planning inputs.

Legacy snapshots may be incomplete. Their missing fields remain unavailable;
today's calendar, names, tasks and source decisions are never substituted.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation


def _number(value):
    if value is None:
        return None
    try:
        number = Decimal(str(value))
        return float(number) if number.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _summary(tasks, calendar):
    starts = [row['planned_start_date'] for row in tasks if row.get('planned_start_date')]
    finishes = [row['planned_finish_date'] for row in tasks if row.get('planned_finish_date')]
    complete = bool(tasks) and len(starts) == len(tasks) and len(finishes) == len(tasks)
    start, finish = min(starts) if starts else None, max(finishes) if finishes else None
    duration = None
    if complete and start <= finish and calendar and calendar.get('working_weekdays'):
        first, last = date.fromisoformat(start), date.fromisoformat(finish)
        exceptions = {row['date']: row['is_working'] for row in calendar.get('exceptions') or []}
        weekdays = set(calendar['working_weekdays'])
        duration = sum(bool(exceptions.get((first + timedelta(days=offset)).isoformat(),
                                           (first + timedelta(days=offset)).weekday() in weekdays))
                       for offset in range((last - first).days + 1))
        if start == finish and all(row.get('is_milestone') for row in tasks):
            duration = 0
    floats = [row['total_float_days'] for row in tasks if row.get('total_float_days') is not None]
    critical = [row['is_critical'] for row in tasks if row.get('is_critical') is not None]
    return {'planned_start_date': start, 'planned_finish_date': finish, 'duration_days': duration,
            'total_float_days': min(floats) if floats and len(floats) == len(tasks) else None,
            'is_critical': True if any(critical) else False if critical and len(critical) == len(tasks) else None,
            'task_count': len(tasks), 'complete': complete, 'duration_basis': 'frozen_working_calendar_span'}


def published_plan_state(baseline):
    """Pure projection from the persisted snapshot; the caller supplies permissions."""
    from .simple_planning import WORKFLOW_FIELDS, SOURCE_FIELDS
    from .planning_provenance import annotate_published_provenance
    frozen = deepcopy(baseline.snapshot)
    inputs = frozen.get('accepted_inputs') or {}
    version = frozen.get('version') or {}
    build = inputs.get('planning_build') or {}
    build_plan = build.get('plan') or {}
    expected = {row['id']: row for row in build_plan.get('activities') or []}
    profile = build.get('profile_snapshot') or {}
    workflow = (profile.get('definition') or {}).get('workflow') or {}
    stages = {row['code']: row for row in workflow.get('stages') or []}
    build_inputs = (build.get('evidence_snapshot') or {}).get('accepted_inputs') or {}
    calendar = next((deepcopy(row) for row in inputs.get('calendars') or []
                     if row.get('id') == inputs.get('default_calendar_id')), None)
    nodes = [{**row, 'parent_id': row.get('parent', row.get('parent_id')), 'is_derived': False}
             for row in frozen.get('wbs') or []]
    by_node = {row['id']: row for row in nodes}
    activities = frozen.get('activities') or []
    task_ids = {row['id']: row['external_id'] for row in activities}
    dependencies, details = defaultdict(list), defaultdict(list)
    for link in frozen.get('relationships') or []:
        predecessor, successor = link.get('predecessor', link.get('predecessor_id')), link.get('successor', link.get('successor_id'))
        if predecessor in task_ids and successor in task_ids:
            dependencies[successor].append(task_ids[predecessor])
            details[successor].append({'task_id': task_ids[predecessor], 'type': link['relationship_type'],
                                       'lag_days': _number(link.get('lag_days'))})
    tasks, deliverables = [], {}
    for row in activities:
        metadata = row.get('metadata') or {}
        key = row['id']
        node_id = row.get('wbs_node', row.get('wbs_node_id'))
        kind = row.get('activity_type')
        task = {name: deepcopy(metadata[name]) for name in (*WORKFLOW_FIELDS, *SOURCE_FIELDS,
                'property_provenance', 'derivation', 'duration_evidence', 'document_number', 'document_revision',
                'duration_calendar_verified') if name in metadata}
        task.update(id=row['external_id'], external_id=row['external_id'], activity_id=key,
            title=row['name'], discipline=row.get('discipline') or 'general', owner=metadata.get('owner', row.get('responsible_role')),
            responsible_role=row.get('responsible_role') or None, effort_hours=metadata.get('planned_effort_hours'),
            duration_days=_number(row.get('duration_days')), duration_source=metadata.get('duration_source', 'unknown'),
            depends_on=dependencies[key], dependency_details=details[key],
            acceptance_criteria=metadata.get('acceptance_criteria', ''), reviewer=metadata.get('reviewer', ''),
            assignee_id=metadata.get('assignee_id'), reviewer_id=metadata.get('reviewer_id'),
            task_type=metadata.get('task_type', 'task'), priority=metadata.get('priority'), due_date=metadata.get('due_date'),
            source_references=deepcopy(metadata.get('source_references') or []),
            planned_start_date=row.get('planned_start'), planned_finish_date=row.get('planned_finish'),
            activity_type=kind, is_milestone=kind in {'start_milestone', 'finish_milestone'},
            workflow_stage=metadata.get('workflow_stage_code') or metadata.get('workflow_stage'),
            wbs_node_id=node_id, wbs_code=by_node.get(node_id, {}).get('code', ''),
            activity_code=row['external_id'], activity_code_source='frozen_schedule_activity',
            constraint_type=row.get('constraint_type'), constraint_date=row.get('constraint_date'),
            calendar_id=row.get('calendar', row.get('calendar_id')), sort_order=row.get('sort_order', 0),
            early_start=row.get('early_start'), early_finish=row.get('early_finish'),
            late_start=row.get('late_start'), late_finish=row.get('late_finish'),
            total_float_days=_number(row.get('total_float_days')), free_float_days=_number(row.get('free_float_days')),
            is_critical=row.get('is_critical'), calculated=bool(row.get('planned_start') and row.get('planned_finish')),
            calculation_basis='published_baseline')
        planned = expected.get(task['id'])
        if planned and planned.get('kind') == 'workflow_activity':
            entity = planned['source_entity_id']
            source = build_inputs.get(entity, {})
            discipline = source.get('discipline')
            if isinstance(discipline, dict):
                discipline = discipline.get('name') or discipline.get('code')
            deliverable = {'id': entity, 'title': source.get('identity'), 'discipline': discipline or 'general'}
            task.update(parent_deliverable_id=entity, source_deliverable=deliverable,
                deliverable=deliverable['title'], discipline=discipline or 'general',
                workflow_stage_sequence=stages.get(planned.get('workflow_stage_code'), {}).get('sequence'),
                workflow_stage_name=planned.get('workflow_stage_name'), workflow_stage_code=planned.get('workflow_stage_code'),
                workflow_template_id=workflow.get('id'), workflow_template_code=workflow.get('code'),
                workflow_template_version=workflow.get('version'))
        parent_id = task.get('parent_deliverable_id')
        if parent_id and task.get('source_deliverable'):
            parent = deliverables.setdefault(parent_id, deepcopy(task['source_deliverable']))
            parent.setdefault('workflow_task_ids', [])
            if task['id'] not in parent['workflow_task_ids']:
                parent['workflow_task_ids'].append(task['id'])
        tasks.append(task)
    by_task = {task['id']: task for task in tasks}
    for parent in deliverables.values():
        children = [by_task[key] for key in parent['workflow_task_ids'] if key in by_task]
        parent['summary'] = _summary(children, calendar)
        if children:
            node = by_node.get(children[0].get('wbs_node_id'))
            if node:
                parent.update(wbs_node_id=node['id'], parent_wbs_node_id=node['parent_id'], activity_code=node['code'])
            for child in children:
                child['activity_code'] = f"{parent.get('activity_code') or parent['id']}-{child.get('workflow_stage_code') or child['external_id']}"
    descendants = defaultdict(list)
    for task in tasks:
        node_id, seen = task['wbs_node_id'], set()
        while node_id in by_node and node_id not in seen:
            seen.add(node_id)
            descendants[node_id].append(task)
            node_id = by_node[node_id]['parent_id']
    for node in nodes:
        node['summary'] = _summary(descendants[node['id']], calendar)
    calendar_data = {**(calendar or {}), 'duration_basis': 'working_days', 'is_fallback': False,
                     'source_verified': bool(calendar), 'evidence_status': 'Frozen baseline input' if calendar else 'Not retained in this baseline'}
    identity = deepcopy(inputs.get('project_identity') or {'id': inputs.get('project_id'), 'name': None})
    identity.update(start_date=inputs.get('project_start'), end_date=inputs.get('project_finish'),
                    planned_start_date=inputs.get('project_start'), planned_finish_date=inputs.get('project_finish'))
    warnings = deepcopy((frozen.get('schedule_assurance') or {}).get('warnings') or [])
    if not calendar or not inputs.get('project_identity'):
        warnings.append({'code': 'legacy_baseline_manifest_incomplete',
                         'message': 'This older baseline did not retain all project/calendar details. Current values were not substituted.'})
    disciplines = {row['discipline']: row['discipline'].replace('_', ' ').title() for row in tasks}
    baseline_data = {'id': baseline.pk, 'name': baseline.name, 'version_id': baseline.source_version_id,
                     'approved_at': baseline.approved_at.isoformat()}
    state = {'state': 'baselined', 'version_id': baseline.source_version_id, 'current_version_id': baseline.source_version_id,
             'version_number': version.get('version'), 'schedule_id': baseline.schedule_id,
             'baseline_id': baseline.pk, 'baseline': baseline_data,
             'project': identity, 'schedule_identity': deepcopy(inputs.get('schedule_identity') or {}),
             'tasks': tasks, 'disciplines': [{'code': code, 'name': name} for code, name in disciplines.items()],
             'wbs_nodes': nodes, 'calendar': calendar_data, 'work_calendar': deepcopy(calendar_data),
             'hierarchy_source': 'published_baseline', 'project_summary': _summary(tasks, calendar),
             'deliverables': list(deliverables.values()), 'deliverable_count': len(deliverables),
             'workflow_mode': 'standard_five' if deliverables else None,
             'calculation_available': bool(tasks) and all(task['calculated'] for task in tasks),
             'calculation_basis': 'published_baseline', 'stale_inputs': False, 'warnings': warnings, 'blockers': [], 'assumptions': [],
             'source_documents': deepcopy(inputs.get('source_documents') or []),
             'source_verification': {'status': 'verified' if inputs else 'unverified', 'policy': 'approved_baseline', 'issues': [],
                                      'label': 'Frozen approved baseline'},
             'scheduling_status': {'state': 'baselined', 'message': 'Published baseline. New inputs require a new revision.'},
             'resource_requirements': deepcopy(build_plan.get('resources') or []),
             'baseline_risk_register': deepcopy(frozen.get('risk_register') or []),
             'planning_build_id': build.get('id'), 'planning_profile': {
                 'profile_id': build.get('profile_id'), 'profile_version': profile.get('profile_version'),
                 'revision': build.get('profile_selection_revision', 0), 'content_fingerprint': build.get('profile_fingerprint'),
                 'snapshot': deepcopy(profile) or None, 'valid': bool(profile.get('approval')), 'frozen': True},
             'review': None, 'review_id': None, 'approvers': [],
             'assignment_token': f'published-baseline:{baseline.pk}', 'published_snapshot': True,
             'read_only_reason': 'This view preserves the approved baseline. Create a revision to use changed inputs.'}
    annotate_published_provenance(state, frozen)
    return state
