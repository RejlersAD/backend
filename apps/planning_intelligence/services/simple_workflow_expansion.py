"""Pure, reviewable expansion of source deliverables into configured workflows.

This module neither queries nor writes a database. Template timings and links
are planning proposals, never an import or verification of an original schedule.
Callers must reject returned expansion_blockers before applying the proposal.
"""
from collections import deque
from copy import deepcopy
from math import isfinite
from uuid import NAMESPACE_URL, uuid5


_MILESTONES = {'start_milestone', 'finish_milestone'}
_ACTIVITY_TYPES = {'task', 'level_of_effort', *_MILESTONES}
_RELATIONSHIPS = {'FS', 'SS', 'FF', 'SF'}
_STANDARD_CODES = ['IFR', 'COMPANY_REVIEW', 'IFA', 'COMPANY_APPROVAL', 'FINAL_ISSUE']
MAX_WORKFLOW_ACTIVITIES = 2000
_EDITABLE_FIELDS = {
    'title', 'owner', 'effort_hours', 'assignee_id', 'reviewer', 'reviewer_id',
    'acceptance_criteria', 'priority', 'task_type', 'due_date', 'due_date_source',
    'duration_days', 'duration_source', 'planned_start_date', 'activity_type',
    'is_milestone', 'depends_on', 'dependency_details', 'dependency_rationales',
    'schedule_generated_fields', 'schedule_rationale', 'status', 'progress_percent',
    'project_task_id', 'assignee', 'reviewer_user',
}


class WorkflowExpansionError(ValueError):
    def __init__(self, message, *, code='workflow_expansion_invalid'):
        super().__init__(message)
        self.code = code


def _number(value, label, *, minimum=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise WorkflowExpansionError(f'{label} must be a number.') from None
    if not isfinite(number) or (minimum is not None and number < minimum):
        raise WorkflowExpansionError(f'{label} must be a valid number' + (f' of at least {minimum}.' if minimum is not None else '.'))
    return number


def _select_template(parent, context):
    templates = context.get('templates') or []
    if context.get('workflow_mode', 'standard_five') != 'standard_five':
        raise WorkflowExpansionError('Select the standard five-stage deliverable workflow.')
    # The requested uniform mode deliberately overrides discipline/family rules.
    # A selected custom template remains usable only with the same five gates.
    def standard_codes(row):
        ordered = sorted(enumerate(row.get('stages') or []), key=lambda pair: pair[1].get('sequence', pair[0] + 1))
        return [stage.get('code') for _, stage in ordered] == _STANDARD_CODES
    selected_id = context.get('default_template_id')
    selected = next((row for row in templates if selected_id is not None and str(row.get('id')) == str(selected_id)), None)
    template = selected if selected and standard_codes(selected) else None
    if template is None:
        options = [row for row in templates if row.get('code') == 'STANDARD_5_STAGE']
        options.sort(key=lambda row: (row.get('project_id') == context.get('project_id')
                                     and row.get('project_id') is not None,
                                     row.get('version', 0)), reverse=True)
        template = next(iter(options), None)
    if template is None:
        raise WorkflowExpansionError('Configure an active STANDARD_5_STAGE workflow before expanding deliverables.', code='workflow_template_unavailable')
    stages = deepcopy(template.get('stages') or [])
    if len(stages) != 5:
        raise WorkflowExpansionError(
            f"Selected workflow {template.get('code', '')} has {len(stages)} stages. Select an explicit five-stage workflow; stages will not be added or removed automatically.",
            code='workflow_five_stages_required',
        )
    for index, stage in enumerate(stages):
        stage.setdefault('sequence', index + 1)
    stages.sort(key=lambda row: row['sequence'])
    if len({stage.get('code') for stage in stages}) != 5 or any(not stage.get('code') for stage in stages):
        raise WorkflowExpansionError('Each workflow stage must have its own nonempty code.')
    if len({stage['sequence'] for stage in stages}) != 5:
        raise WorkflowExpansionError('Each workflow stage must have its own sequence.')
    if [stage['code'] for stage in stages] != _STANDARD_CODES:
        raise WorkflowExpansionError('The standard workflow must contain IFR, COMPANY_REVIEW, IFA, COMPANY_APPROVAL and FINAL_ISSUE in that order.', code='workflow_stage_codes_invalid')
    return template, stages


def _stage_id(parent_id, stage_code, index):
    return parent_id if index == 0 else str(uuid5(NAMESPACE_URL, f'radai:workflow:{parent_id}:{stage_code}'))


def _add_link(task, predecessor_id, kind, lag, **metadata):
    kind = str(kind or 'FS').upper()
    if kind not in _RELATIONSHIPS:
        raise WorkflowExpansionError(f'Unsupported workflow relationship {kind}.')
    lag = _number(lag, 'Relationship lag')
    if abs(lag) > 365:
        raise WorkflowExpansionError('Relationship lag must be between -365 and 365 working days.')
    if predecessor_id not in task['depends_on']:
        task['depends_on'].append(predecessor_id)
    detail = {'task_id': predecessor_id, 'type': kind, 'lag_days': lag, **deepcopy(metadata)}
    if not any(row['task_id'] == predecessor_id and row['type'] == kind for row in task['dependency_details']):
        task['dependency_details'].append(detail)
    task['dependency_rationales'][predecessor_id] = {
        key: deepcopy(value) for key, value in metadata.items()
        if key in {'source', 'status', 'rationale', 'source_references', 'evidence_type'}
    }


def _validate_network(tasks):
    by_id = {task['id']: task for task in tasks}
    if len(by_id) != len(tasks):
        raise WorkflowExpansionError('Expanded activities must have distinct IDs.')
    incoming, outgoing = {}, {key: [] for key in by_id}
    for task in tasks:
        duration = _number(task.get('duration_days'), 'Stage duration', minimum=0)
        milestone = task.get('activity_type') in _MILESTONES
        if (milestone and duration != 0) or (not milestone and duration <= 0):
            raise WorkflowExpansionError('Milestones require zero duration; other workflow tasks require positive durations.')
        dependencies = list(dict.fromkeys(task.get('depends_on') or []))
        if any(key not in by_id for key in dependencies):
            raise WorkflowExpansionError('An existing dependency points outside the expanded plan.')
        if task['id'] in dependencies:
            raise WorkflowExpansionError('An activity cannot depend on itself.')
        incoming[task['id']] = len(dependencies)
        for key in dependencies:
            outgoing[key].append(task['id'])
        for detail in task.get('dependency_details') or []:
            if detail.get('task_id') not in dependencies or detail.get('type') not in _RELATIONSHIPS:
                raise WorkflowExpansionError('Typed relationships must match the activity predecessor list.')
            _number(detail.get('lag_days', 0), 'Relationship lag')
    ready = deque(key for key, count in incoming.items() if not count)
    visited = 0
    while ready:
        visited += 1
        for successor in outgoing[ready.popleft()]:
            incoming[successor] -= 1
            if not incoming[successor]:
                ready.append(successor)
    if visited != len(tasks):
        raise WorkflowExpansionError('Workflow dependencies contain a cycle.', code='workflow_dependency_cycle')


def _remap_new_parent_dependencies(task, newly_expanded, chains):
    """A prior whole-task finish becomes Final Issue, not the reused IFR ID."""
    if not newly_expanded.intersection(task.get('depends_on') or []):
        return
    old_details = task.get('dependency_details') or []
    old_rationales = task.get('dependency_rationales') or {}
    dependencies, details, rationales = [], [], {}
    for predecessor in task.get('depends_on') or []:
        links = [row for row in old_details if row.get('task_id') == predecessor]
        if predecessor not in newly_expanded:
            dependencies.append(predecessor)
            details.extend(deepcopy(links))
            if predecessor in old_rationales:
                rationales[predecessor] = deepcopy(old_rationales[predecessor])
            continue
        for old_link in links or [{'task_id': predecessor, 'type': 'FS', 'lag_days': 0}]:
            kind = str(old_link.get('type') or 'FS').upper()
            if kind not in _RELATIONSHIPS:
                raise WorkflowExpansionError(f'Unsupported deliverable relationship {kind}.')
            endpoint = chains[predecessor][-1 if kind[0] == 'F' else 0]['id']
            if endpoint not in dependencies:
                dependencies.append(endpoint)
            details.append({**deepcopy(old_link), 'task_id': endpoint, 'type': kind,
                            'parent_predecessor_id': predecessor})
            if predecessor in old_rationales:
                rationales[endpoint] = deepcopy(old_rationales[predecessor])
    task.update(depends_on=list(dict.fromkeys(dependencies)), dependency_details=details,
                dependency_rationales=rationales)


def expand_workflow_deliverables(parents, context, previous_tasks=None):
    """Return source parents, executable children and warnings, without mutation.

    A parent's first stage retains its ID and assignment history. Remaining stage
    IDs depend on the stable parent ID and stage code, not title or row order.
    Explicitly configured workflows must contain exactly five stages.
    """
    parents = deepcopy(parents)
    if len(parents) * 5 > MAX_WORKFLOW_ACTIVITIES:
        raise WorkflowExpansionError('The expanded workflow would exceed 2,000 activities.', code='workflow_activity_limit')
    if len({parent.get('id') for parent in parents}) != len(parents) or any(not parent.get('id') for parent in parents):
        raise WorkflowExpansionError('Each source deliverable must have a distinct nonempty ID.')
    previous = {task['id']: task for task in previous_tasks or []}
    existing_stages = {key: task for key, task in previous.items() if task.get('parent_deliverable_id')}
    newly_expanded = set()
    deliverables, tasks, chains = [], [], {}
    warnings = []
    assignment_count = completed_count = 0
    for parent in parents:
        if not isinstance(parent.get('title'), str) or not parent['title'].strip():
            raise WorkflowExpansionError('Each source deliverable must have a title.')
        template, stages = _select_template(parent, context)
        parent_id = parent['id']
        first_previous = previous.get(parent_id) or {}
        already_expanded = first_previous.get('parent_deliverable_id') == parent_id
        if not already_expanded:
            newly_expanded.add(parent_id)
        original = {**parent, **({} if already_expanded else first_previous)}
        blockers = []
        if not already_expanded and (original.get('status') == 'completed' or float(original.get('progress_percent') or 0) >= 100):
            completed_count += 1
            blockers.append({'code': 'workflow_completed_parent', 'task_id': parent_id,
                             'message': f"Completed deliverable {parent['title']} must be reviewed before converting its existing task into the first workflow stage."})
        if not already_expanded and (original.get('assignee_id') or original.get('project_task_id')
                                     or original.get('effort_hours') is not None or original.get('due_date')):
            assignment_count += 1
        source_values = {key: deepcopy(value) for key, value in parent.items()
                         if key.startswith('source_') or key in {'id', 'title', 'discipline', 'document_number', 'document_revision'}}
        chain = []
        for index, stage in enumerate(stages):
            activity_type = stage.get('activity_type') or 'task'
            if activity_type not in _ACTIVITY_TYPES:
                raise WorkflowExpansionError(f'Unsupported workflow activity type {activity_type}.')
            duration = _number(stage.get('duration_days'), 'Stage duration', minimum=0)
            if activity_type in _MILESTONES and duration != 0:
                raise WorkflowExpansionError('A workflow milestone must have zero duration.')
            if activity_type not in _MILESTONES and duration <= 0:
                raise WorkflowExpansionError('A workflow task must have a positive duration.')
            name = stage.get('name') or stage['code']
            try:
                title = (stage.get('activity_name_template') or '{deliverable} - {stage}').format(
                    deliverable=parent['title'], stage=name, discipline=parent.get('discipline', ''),
                )
            except (KeyError, ValueError, IndexError, AttributeError):
                raise WorkflowExpansionError('A stage title template contains an unsupported placeholder.') from None
            if len(title) > 500:
                raise WorkflowExpansionError(
                    f'Generated {name} activity exceeds 500 characters. Use a shorter activity name template; the exact source deliverable title is retained.',
                    code='workflow_activity_title_limit',
                )
            role = stage.get('responsible_party') or ''
            task = {
                'id': _stage_id(parent_id, stage['code'], index), 'title': title,
                'discipline': parent.get('discipline') or 'general', 'owner': '',
                'assignee_id': None, 'reviewer_id': None, 'reviewer': '',
                'effort_hours': None, 'due_date': None, 'due_date_source': 'schedule',
                'priority': parent.get('priority') or 'medium', 'task_type': 'task',
                'acceptance_criteria': '', 'planned_start_date': parent.get('planned_start_date') if index == 0 else None,
                'duration_days': duration, 'duration_source': 'proposed',
                'activity_type': activity_type, 'is_milestone': activity_type in _MILESTONES,
                'depends_on': [], 'dependency_details': [], 'dependency_rationales': {},
                'parent_deliverable_id': parent_id, 'deliverable': parent['title'],
                'workflow_stage_code': stage['code'], 'workflow_stage_name': name,
                'workflow_stage_sequence': stage['sequence'], 'workflow_template_id': template.get('id'),
                'workflow_template_code': template['code'], 'workflow_template_version': template.get('version'),
                'responsible_role': role, 'workflow_responsible_party': role,
                'workflow_progress_weight': stage.get('progress_weight'),
                'workflow_release_gate': bool(stage.get('is_release_gate', True)),
                'source_references': deepcopy(parent.get('source_references') or []),
                'source_title': parent.get('source_title') or parent['title'],
                'source_parent_values': deepcopy(source_values),
                'document_number': parent.get('document_number') or '',
                'document_revision': parent.get('document_revision') or '',
                'schedule_rationale': f"Proposed stage from {template['code']} v{template.get('version')}; template timings are not verified original schedule values.",
                'schedule_generated_fields': ['duration_days', 'depends_on'],
            }
            if index == 0:
                if task['planned_start_date'] and 'planned_start_date' in (parent.get('schedule_generated_fields') or []):
                    task['schedule_generated_fields'].append('planned_start_date')
                for field in ('owner', 'assignee_id', 'reviewer_id', 'reviewer', 'effort_hours',
                              'due_date', 'due_date_source', 'priority', 'task_type', 'acceptance_criteria',
                              'status', 'progress_percent', 'project_task_id', 'assignee', 'reviewer_user'):
                    if field in original:
                        task[field] = deepcopy(original[field])
                if task.get('due_date') and not original.get('due_date_source'):
                    task['due_date_source'] = 'explicit'
            if chain:
                relationship = stage.get('relationship', stage.get('relationship_to_previous', 'FS'))
                if not relationship:
                    raise WorkflowExpansionError('Every stage after the first must link to its previous stage.')
                _add_link(task, chain[-1]['id'], relationship, stage.get('lag_days', 0),
                          source='workflow_template', status='proposed', evidence_type='planning_inference',
                          rationale=f"Configured {template['code']} stage sequence; review before approval.")
            chain.append(task)
        chains[parent_id] = chain
        tasks.extend(chain)
        parent.update(workflow_task_ids=[task['id'] for task in chain],
                      workflow_template_id=template.get('id'), workflow_template_code=template['code'],
                      workflow_template_version=template.get('version'), expansion_blockers=blockers)
        deliverables.append(parent)

    expanded_by_id = {task['id']: task for task in tasks}
    for parent in parents:
        details = parent.get('dependency_details') or []
        references = list(dict.fromkeys([*(parent.get('depends_on') or []), *(row.get('task_id') for row in details)]))
        for predecessor_id in references:
            # New tasks in an already expanded workspace select actual stages.
            # Existing source-parent snapshots still describe whole deliverables.
            explicit_stage = (predecessor_id in existing_stages and
                              (parent['id'] in newly_expanded or predecessor_id not in chains))
            if predecessor_id not in chains and not explicit_stage:
                raise WorkflowExpansionError('A deliverable predecessor is missing from this expansion.')
            if explicit_stage and predecessor_id not in expanded_by_id:
                raise WorkflowExpansionError('An existing stage predecessor is missing from the expanded plan.')
            links = [row for row in details if row.get('task_id') == predecessor_id] or [{'type': 'FS', 'lag_days': 0}]
            for detail in links:
                kind = str(detail.get('type') or 'FS').upper()
                if kind not in _RELATIONSHIPS:
                    raise WorkflowExpansionError(f'Unsupported deliverable relationship {kind}.')
                predecessor = (expanded_by_id[predecessor_id] if explicit_stage else
                               chains[predecessor_id][-1 if kind[0] == 'F' else 0])
                successor = chains[parent['id']][-1 if kind[1] == 'F' else 0]
                rationale = deepcopy((parent.get('dependency_rationales') or {}).get(predecessor_id) or {})
                if not isinstance(rationale, dict):
                    rationale = {'rationale': str(rationale)}
                _add_link(successor, predecessor['id'], kind, detail.get('lag_days', 0),
                          source=detail.get('source', rationale.get('source', 'parent_dependency')),
                          status=detail.get('status', rationale.get('status', 'proposed')),
                          rationale=detail.get('rationale', rationale.get('rationale', 'Preserved deliverable dependency mapped to workflow endpoints.')),
                          source_references=detail.get('source_references', rationale.get('source_references', [])),
                          parent_predecessor_id=(existing_stages[predecessor_id]['parent_deliverable_id']
                                                 if explicit_stage else predecessor_id),
                          parent_successor_id=parent['id'])

    for task in tasks:
        old = previous.get(task['id'])
        if old and old.get('parent_deliverable_id') == task['parent_deliverable_id'] and old.get('workflow_stage_code') == task['workflow_stage_code']:
            for key in _EDITABLE_FIELDS & old.keys():
                task[key] = deepcopy(old[key])
            _remap_new_parent_dependencies(task, newly_expanded, chains)
            # Employee/planner edits win; source parent identity remains separate.
            task['is_milestone'] = task['activity_type'] in _MILESTONES
    _validate_network(tasks)
    if tasks:
        warnings.append('Workflow stages, durations and internal links are configured planning proposals; they do not reproduce or verify an uploaded original schedule.')
    if assignment_count:
        warnings.append(f'{assignment_count} existing deliverable assignment(s), effort estimates or due dates will belong to the first stage only. Review stage responsibility before applying.')
    if completed_count:
        warnings.append(f'{completed_count} completed deliverable(s) cannot be converted until their existing work history is reviewed. Applying this expansion must be blocked.')
    return deliverables, tasks, warnings
