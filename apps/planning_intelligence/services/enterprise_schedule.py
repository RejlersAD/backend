"""Scope-based execution networks; WBS and package summaries are never CPM tasks.

Allowances and technical release gates are reviewable planning assumptions. This
module does not import reference schedules or certify resource feasibility.
"""
from collections import defaultdict, deque
from copy import deepcopy
from math import isfinite
import re
from uuid import NAMESPACE_URL, uuid5

from .enterprise_workflows import select_workflow
from .simple_workflow_expansion import WorkflowExpansionError, _add_link, _EDITABLE_FIELDS


VERSION = 'enterprise-workflows-v1'
MAX_TASKS = 2000
START_ID = 'enterprise-start'
FINISH_ID = 'enterprise-finish'
ENTERPRISE_FIELDS = (
    'generation_method', 'duration_basis', 'task_deliverable', 'progress_measurement',
    'network_boundary', 'schedule_phase', 'complexity',
)


def generation_context(project):
    enterprise = getattr(project, 'enterprise_project', None)
    fields = getattr(enterprise, 'custom_fields', None) or {}
    return {'project_type': fields.get('industry') or fields.get('project_type') or getattr(enterprise, 'project_type', ''),
            'complexity': fields.get('complexity', 'standard'),
            'phase': getattr(project, 'phase', ''),
            'project_name': project.name}


def _normal(value):
    return re.sub(r'\W+', ' ', str(value or '').casefold()).strip()


def populate_successors(tasks):
    """Outgoing links are derived from the authoritative predecessor records."""
    by_id = {row['id']: row for row in tasks}
    for row in tasks:
        row['successors'] = []
        row['successor_details'] = []
    for row in tasks:
        details = row.get('dependency_details') or []
        for key in row.get('depends_on') or []:
            if key not in by_id:
                continue
            by_id[key]['successors'].append(row['id'])
            for link in [item for item in details if item['task_id'] == key] or [{'type': 'FS', 'lag_days': 0}]:
                by_id[key]['successor_details'].append({**deepcopy(link), 'task_id': row['id']})
    return tasks


def validate_enterprise_network(tasks):
    """Reject malformed, duplicate, disconnected or incomplete execution networks."""
    if not tasks:
        raise WorkflowExpansionError('Select at least one executable scope deliverable.')
    by_id = {row.get('id'): row for row in tasks}
    if None in by_id or '' in by_id or len(by_id) != len(tasks):
        raise WorkflowExpansionError('Every generated task must have a unique nonempty ID.')
    incoming, outgoing, names, packages = {}, {key: [] for key in by_id}, set(), defaultdict(list)
    relationships = 0
    for row in tasks:
        if not str(row.get('title') or '').strip():
            raise WorkflowExpansionError('Every generated task requires a meaningful name.')
        identity = (_normal(row.get('wbs_phase')), _normal(row.get('wbs_deliverable')),
                    _normal(row.get('discipline')), _normal(row.get('document_number')), _normal(row['title']))
        if identity in names:
            raise WorkflowExpansionError('Duplicate activities within the same WBS scope are not allowed.')
        names.add(identity)
        try:
            duration = float(row.get('duration_days'))
        except (ValueError, TypeError):
            raise WorkflowExpansionError('Every task requires a finite planned duration.') from None
        milestone = row.get('activity_type') in {'start_milestone', 'finish_milestone'}
        if not isfinite(duration) or (duration != 0 if milestone else duration <= 0):
            raise WorkflowExpansionError('Milestones require zero duration; work tasks require positive durations.')
        if not row.get('discipline') or not row.get('deliverable'):
            raise WorkflowExpansionError('Every task requires discipline ownership and a deliverable.')
        deps = row.get('depends_on') or []
        if len(set(deps)) != len(deps):
            raise WorkflowExpansionError('Duplicate predecessor relationships are not allowed.')
        if any(key not in by_id or key == row['id'] for key in deps):
            raise WorkflowExpansionError('Dependencies must reference other tasks within this schedule.')
        links = row.get('dependency_details') or []
        if set(deps) != {link.get('task_id') for link in links}:
            raise WorkflowExpansionError('Every predecessor requires relationship details.')
        keys = set()
        for link in links:
            key = (link.get('task_id'), link.get('type'))
            try:
                lag = float(link.get('lag_days', 0))
            except (TypeError, ValueError):
                raise WorkflowExpansionError('Relationship lag must be finite.') from None
            if key in keys or key[1] not in {'FS', 'SS', 'FF', 'SF'} or not isfinite(lag) or abs(lag) > 365:
                raise WorkflowExpansionError('Duplicate or invalid relationship details.')
            keys.add(key)
        incoming[row['id']] = len(deps)
        relationships += len(links)
        for key in deps:
            outgoing[key].append(row['id'])
        if row.get('parent_deliverable_id'):
            packages[row['parent_deliverable_id']].append(row)
    starts = [key for key, count in incoming.items() if not count]
    finishes = [key for key, values in outgoing.items() if not values]
    queue = deque(starts)
    visited = []
    while queue:
        key = queue.popleft()
        visited.append(key)
        for successor in outgoing[key]:
            incoming[successor] -= 1
            if not incoming[successor]:
                queue.append(successor)
    if len(visited) != len(tasks):
        raise WorkflowExpansionError('Generated dependencies contain a circular relationship.', code='workflow_dependency_cycle')
    if (len(starts) != 1 or len(finishes) != 1
            or by_id[starts[0]].get('activity_type') != 'start_milestone'
            or by_id[finishes[0]].get('activity_type') != 'finish_milestone'):
        raise WorkflowExpansionError('Every work task must connect from the project start to the project finish; orphan or open activities are not allowed.')
    for rows in packages.values():
        rows.sort(key=lambda row: row.get('workflow_stage_sequence', 0))
        if len(rows) < 5:
            raise WorkflowExpansionError('Every activity package requires at least five sequential tasks.')
        if len({row.get('workflow_stage_code') for row in rows}) != len(rows):
            raise WorkflowExpansionError('A package cannot repeat a workflow stage.')
        for predecessor, successor in zip(rows, rows[1:]):
            if not any(link.get('task_id') == predecessor['id'] and link.get('type') == 'FS'
                       for link in successor.get('dependency_details') or []):
                raise WorkflowExpansionError('Each workflow task must follow the previous task with an FS relationship.')
    return {'valid': True, 'task_count': len(tasks), 'relationship_count': relationships,
            'package_count': len(packages), 'start_ids': starts, 'finish_ids': finishes,
            'orphan_count': 0, 'cycle_count': 0, 'duplicate_count': 0,
            'critical_path_supported': True, 'validation_version': VERSION}


def _base_task(key, title, discipline, deliverable):
    return {'id': key, 'title': title, 'discipline': discipline, 'deliverable': deliverable,
            'owner': '', 'assignee_id': None, 'reviewer_id': None, 'reviewer': '',
            'effort_hours': None, 'due_date': None, 'due_date_source': 'schedule',
            'priority': 'medium', 'task_type': 'task', 'acceptance_criteria': '',
            'document_number': '', 'document_revision': '', 'source_references': [],
            'depends_on': [], 'dependency_details': [], 'dependency_rationales': {},
            'duration_source': 'proposed', 'duration_unit': 'working_days',
            'evidence_policy': 'planning_assumptions', 'duration_policy': 'planning_assumptions',
            'dependency_status': 'proposed', 'status': 'pending', 'progress_percent': 0,
            'constraint_type': 'none', 'constraint_date': None,
            'generation_method': VERSION, 'needs_review': True,
            'schedule_generated_fields': ['duration_days', 'depends_on'],
            'review_flags': ['planning_allowances_require_review']}


def _link(successor, predecessor, rationale, **metadata):
    _add_link(successor, predecessor['id'], 'FS', 0, source='enterprise_workflow',
              status='proposed', evidence_type='planning_inference', rationale=rationale, **metadata)


def expand_enterprise_deliverables(parents, context, previous_tasks=None):
    """Expand only supplied scope; preserve WBS paths and prior work on stable IDs."""
    parents = deepcopy(parents)
    if not parents:
        raise WorkflowExpansionError('No executable deliverables were identified. Add actual scope before generating a schedule.')
    parent_ids = {row.get('id') for row in parents}
    if len(parent_ids) != len(parents) or not all(parent_ids) or parent_ids & {START_ID, FINISH_ID}:
        raise WorkflowExpansionError('Source deliverables require distinct nonempty IDs.')
    previous = {row['id']: row for row in previous_tasks or []}
    tasks, chains, warnings, identities = [], {}, [], set()
    for parent in parents:
        identity = (_normal(parent.get('wbs_phase') or parent.get('source_heading')),
                    _normal(parent.get('wbs_deliverable')), _normal(parent.get('discipline')),
                    _normal(parent.get('document_number')), _normal(parent.get('title')))
        if identity in identities:
            raise WorkflowExpansionError('Duplicate source deliverables need distinct WBS scope or document identity.')
        identities.add(identity)
        try:
            workflow = select_workflow(parent.get('title', ''), parent.get('discipline', ''),
                                       context.get('project_type', ''), parent.get('complexity') or context.get('complexity', 'standard'))
        except ValueError as error:
            raise WorkflowExpansionError(str(error), code='enterprise_scope_requires_review') from error
        if _normal(context.get('phase')) in {'feed', 'front end engineering design'} and workflow['phase'] in {'engineering', 'detailed_design'}:
            workflow['phase'] = 'feed'
        if len(workflow['stages']) < 5:
            raise WorkflowExpansionError('An execution workflow requires at least five tasks.')
        supplied_discipline = parent.get('discipline') or ''
        parent['discipline'] = (supplied_discipline if supplied_discipline not in {'', 'general', 'requirements', 'unknown'}
                                else re.sub(r'[^a-z0-9]+', '_', workflow['discipline'].lower()).strip('_'))
        if not parent.get('wbs_phase'):
            parent['wbs_phase'] = parent.get('source_heading') or workflow['phase'].replace('_', ' ').title()
        if not parent.get('wbs_deliverable'):
            parent['wbs_deliverable'] = parent['title']
        blockers = []
        original = previous.get(parent['id']) or parent
        expanded = original.get('parent_deliverable_id') == parent['id']
        if not expanded and (original.get('actual_start') or original.get('actual_finish')
                             or float(original.get('progress_percent') or 0) > 0
                             or original.get('status') in {'completed', 'in_progress', 'in_review'}):
            blockers.append({'code': 'workflow_started_parent', 'task_id': parent['id'],
                             'message': 'Retain started work history; review this package before decomposition.'})
        chain = []
        for index, stage in enumerate(workflow['stages']):
            key = parent['id'] if index == 0 else str(uuid5(NAMESPACE_URL, f'radai:enterprise:{parent["id"]}:{stage["code"]}'))
            title = stage['name'] + (f" [{parent['document_number']}]" if parent.get('document_number') else '')
            if len(title) > 500:
                raise WorkflowExpansionError('A generated task name exceeds 500 characters; shorten the source package name.')
            task = _base_task(key, title, parent['discipline'], parent['title'])
            for field in ('wbs_phase', 'wbs_deliverable', 'wbs_phase_id', 'wbs_deliverable_id',
                          'source_references', 'document_number', 'document_revision', 'source_title',
                          'requirement_id', 'requirement_value', 'requirement_status', 'selection_basis'):
                if field in parent:
                    task[field] = deepcopy(parent[field])
            task.update(parent_deliverable_id=parent['id'], workflow_stage_code=stage['code'],
                        workflow_stage_name=stage['name'], workflow_stage_sequence=index + 1,
                        workflow_template_code=workflow['code'], workflow_template_version=1,
                        workflow_progress_weight=stage['progress_weight'],
                        workflow_release_gate=index == len(workflow['stages']) - 1,
                        responsible_role=stage['responsible_role'], workflow_responsible_party=stage['responsible_role'],
                        duration_days=stage['duration_days'], duration_basis=deepcopy(stage['duration_basis']),
                        task_deliverable=stage['deliverable'], acceptance_criteria=stage['deliverable'],
                        activity_type='task', is_milestone=False, schedule_phase=workflow['phase'],
                        complexity=parent.get('complexity') or context.get('complexity', 'standard'),
                        progress_measurement={'method': 'physical', 'completion_evidence': stage['deliverable'],
                                              'package_weight_percent': stage['progress_weight']},
                        activity_name_original=title, activity_name_basis='scope_workflow',
                        activity_naming_version=VERSION,
                        schedule_rationale='Scope-specific workflow allowance in working days; validate quantities, resources and review cycles before baselining.')
            if chain:
                _link(task, chain[-1], 'Complete the preceding workflow task before starting this task.')
            old = previous.get(key)
            if old and old.get('parent_deliverable_id') == parent['id'] and old.get('workflow_stage_code') == stage['code']:
                preserved = _EDITABLE_FIELDS | {'constraint_type', 'constraint_date', 'actual_start', 'actual_finish', 'planner_timing'}
                preserved -= {'depends_on', 'dependency_details', 'dependency_rationales'}
                started = (old.get('actual_start') or old.get('actual_finish')
                           or float(old.get('progress_percent') or 0) > 0
                           or old.get('status') in {'in_progress', 'in_review', 'completed'})
                if old.get('duration_source') == 'proposed' and not started:
                    preserved -= {'duration_days', 'duration_source'}
                else:
                    # A retained planner/source value must keep its own basis.
                    task['duration_basis'] = deepcopy(old.get('duration_basis') or {
                        'source': old.get('duration_source', 'planner'), 'requires_planner_review': True})
                    if old.get('duration_source') != 'proposed' and task['duration_basis'].get('source') == 'planning_allowance':
                        task['duration_basis'] = {'source': old.get('duration_source', 'planner'),
                                                  'previous_allowance': task['duration_basis']}
                if old.get('title') == old.get('activity_name_original'):
                    preserved.discard('title')
                for field in preserved:
                    if field in old:
                        task[field] = deepcopy(old[field])
                for predecessor in old.get('depends_on') or []:
                    links = [row for row in old.get('dependency_details') or [] if row.get('task_id') == predecessor]
                    for link in links or [{'task_id': predecessor, 'type': 'FS', 'lag_days': 0, 'source': 'planner'}]:
                        if link.get('source') in {'enterprise_workflow', 'parent_dependency'} or link.get('parent_predecessor_id'):
                            continue
                        # A task-level planner/source edit overrides this generated
                        # relationship; package-derived links are rebuilt below.
                        kind = link.get('type', 'FS')
                        task['dependency_details'] = [row for row in task['dependency_details']
                                                      if (row['task_id'], row['type']) != (predecessor, kind)]
                        _add_link(task, predecessor, kind, link.get('lag_days', 0),
                                  **{name: deepcopy(value) for name, value in link.items()
                                     if name not in {'task_id', 'type', 'lag_days'}})
            elif index == 0:
                for field in ('owner', 'assignee_id', 'reviewer_id', 'reviewer', 'effort_hours', 'due_date', 'priority'):
                    if field in original:
                        task[field] = deepcopy(original[field])
            chain.append(task)
        chains[parent['id']] = chain
        removed = [row for row in previous.values() if row.get('parent_deliverable_id') == parent['id']
                   and row['id'] not in {task['id'] for task in chain}]
        if removed:
            blockers.append({'code': 'workflow_replacement_requires_review', 'task_id': parent['id'],
                             'affected_task_ids': [row['id'] for row in removed],
                             'message': 'This scope change replaces existing workflow tasks. Reconcile their assignments and work history before applying.'})
        tasks.extend(chain)
        parent.update(workflow_task_ids=[row['id'] for row in chain], workflow_template_code=workflow['code'],
                      workflow_template_version=1, expansion_blockers=blockers)
    if len(tasks) + 2 > MAX_TASKS:
        raise WorkflowExpansionError('The decomposed schedule exceeds 2,000 tasks. Split the scope into smaller schedules.', code='workflow_activity_limit')
    # Explicit package relationships always take precedence over inferred gates.
    for parent in parents:
        if any(row.get('task_id') not in (parent.get('depends_on') or []) for row in parent.get('dependency_details') or []):
            raise WorkflowExpansionError('Package relationship details must match the selected predecessors.')
        for key in parent.get('depends_on') or []:
            if key not in chains or key == parent['id']:
                raise WorkflowExpansionError('A deliverable predecessor must reference a different package in this schedule.')
            details = [row for row in parent.get('dependency_details') or [] if row.get('task_id') == key]
            for detail in details or [{'type': 'FS', 'lag_days': 0}]:
                kind = detail.get('type', 'FS')
                if kind not in {'FS', 'SS', 'FF', 'SF'}:
                    raise WorkflowExpansionError('Invalid package relationship type.')
                predecessor = chains[key][-1 if kind[0] == 'F' else 0]
                successor = chains[parent['id']][-1 if kind[1] == 'F' else 0]
                _add_link(successor, predecessor['id'], kind, detail.get('lag_days', 0),
                          source=detail.get('source', 'parent_dependency'), status=detail.get('status', 'proposed'),
                          parent_predecessor_id=key, parent_successor_id=parent['id'],
                          rationale=detail.get('rationale', 'Preserved package dependency mapped to its workflow endpoints.'))
    # Phase gates are inferred only inside a shared explicit deliverable WBS.
    # Different areas/packages stay parallel unless supplied scope links them.
    order = {'survey': 0, 'feed': 1, 'design': 2, 'detailed_design': 2, 'engineering': 2, 'procurement': 3,
             'construction': 4, 'pre_commissioning': 5, 'precommissioning': 5, 'commissioning': 6, 'testing': 6}
    for parent in parents:
        first = chains[parent['id']][0]
        if parent.get('depends_on') or first.get('depends_on'):
            continue
        candidates = [other for other in parents if other['id'] != parent['id']
                      and parent.get('wbs_deliverable') and _normal(other.get('wbs_deliverable')) == _normal(parent['wbs_deliverable'])
                      and order.get(chains[other['id']][0]['schedule_phase'], 99) < order.get(first['schedule_phase'], -1)]
        if candidates:
            prior = max(order.get(chains[row['id']][0]['schedule_phase'], -1) for row in candidates)
            for row in candidates:
                if order.get(chains[row['id']][0]['schedule_phase']) == prior:
                    _link(first, chains[row['id']][-1], 'Release the preceding execution phase within the same WBS deliverable before this phase begins.')
    start = _base_task(START_ID, 'Authorize scoped work to commence', 'project_controls', 'Work commencement authorization')
    finish = _base_task(FINISH_ID, 'Accept completed scope and handover records', 'project_controls', 'Scope completion acceptance')
    for row, kind, boundary in ((start, 'start_milestone', 'start'), (finish, 'finish_milestone', 'finish')):
        row.update(duration_days=0, activity_type=kind, is_milestone=True, network_boundary=boundary,
                   responsible_role='Project Manager', acceptance_criteria=row['deliverable'],
                   task_deliverable=row['deliverable'], progress_measurement={'method': '0/100', 'completion_evidence': row['deliverable']})
        old = previous.get(row['id']) or {}
        for field in ('owner', 'assignee_id', 'reviewer_id', 'reviewer', 'progress_percent', 'status',
                      'actual_start', 'actual_finish', 'constraint_type', 'constraint_date', 'planner_timing'):
            if field in old:
                row[field] = deepcopy(old[field])
    # Remove only our prior boundary links; rebuilding cannot discard planner links.
    for row in tasks:
        row['depends_on'] = [key for key in row['depends_on'] if key not in {START_ID, FINISH_ID}]
        row['dependency_details'] = [link for link in row['dependency_details'] if link['task_id'] not in {START_ID, FINISH_ID}]
    outgoing = {key for row in tasks for key in row['depends_on']}
    for row in tasks:
        if not row['depends_on']:
            _link(row, start, 'Project authorization releases the scoped work package.')
        if row['id'] not in outgoing:
            _link(finish, row, 'Complete this scope branch before project acceptance.')
    tasks = [start, *tasks, finish]
    populate_successors(tasks)
    validate_enterprise_network(tasks)
    warnings.append('Durations and generated release gates are planning allowances. Review resources, quantities and technical interfaces before approval; target dates do not compress task durations.')
    return parents, tasks, warnings
