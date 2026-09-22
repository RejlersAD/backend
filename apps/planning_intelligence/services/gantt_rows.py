"""Explicit row commands keep hierarchy, workflow membership and links together.

Removing a row withdraws it from the editable plan. Source versions, published
baselines and employee work history are retained; no physical deletes occur.
"""
from copy import deepcopy

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from ..models import ActivityAssignment, PlanningProject
from .audit import record_event
from .gantt_editing import (
    SCHEMA, _error, _input_fingerprint, _safe, _wbs_fingerprint,
    can_edit_gantt, planner_inputs_current,
)
from .manual_wbs import manual_wbs
from .planner_timing import validate_planner_network


def _missing():
    _error('Choose a row from the current project schedule.', 'gantt_row_missing', 404)


def _descendants(nodes, selected):
    children = {}
    for node in nodes:
        children.setdefault(str(node.get('parent_id')), []).append(str(node['id']))
    found, pending = set(), list(selected)
    while pending:
        key = str(pending.pop())
        if key not in found:
            found.add(key)
            pending.extend(children.get(key, []))
    return found


def _common_ancestor(nodes, selected):
    by_id = {str(node['id']): node for node in nodes}
    paths = []
    for key in selected:
        path = []
        while key in by_id and key not in path:
            path.append(key)
            key = str(by_id[key]['parent_id'])
        paths.append(path)
    return next((key for key in paths[0] if all(key in path for path in paths[1:])), None) if paths else None


def _remove_links(tasks, removed):
    for task in tasks:
        if removed.intersection(task.get('depends_on', [])):
            task['dependency_status'] = 'planner'
        task['depends_on'] = [key for key in task.get('depends_on', []) if key not in removed]
        if 'dependency_details' in task:
            task['dependency_details'] = [link for link in task['dependency_details'] if link['task_id'] not in removed]
        if 'dependency_rationales' in task:
            task['dependency_rationales'] = {key: value for key, value in task['dependency_rationales'].items()
                                           if key not in removed}


def _workflow_membership(state, removed):
    kept = {task['id'] for task in state['tasks']}
    parents = []
    deleted_parents = set()
    for parent in state.get('deliverables', []):
        previous = parent.get('workflow_task_ids', [])
        members = [key for key in previous if key in kept]
        if not members:
            deleted_parents.add(parent['id'])
            continue
        parent['workflow_task_ids'] = members
        if set(previous) & removed:
            parent['workflow_structure_edited'] = True
        parents.append(parent)
    state['deliverables'] = parents
    _remove_links(parents, deleted_parents)
    by_id = {str(parent['id']): parent for parent in parents}
    for task in state['tasks']:
        parent = by_id.get(str(task.get('parent_deliverable_id')))
        if parent:
            task['source_deliverable'] = deepcopy(parent)
            task['deliverable'] = parent['title']


def _manual_identity(tasks, nodes, assignments):
    """Keep the existing derived row IDs when its user-visible path is renamed."""
    by_id = {str(node['id']): node for node in nodes}
    for task in tasks:
        node = by_id.get(str(assignments.get(task['id'])))
        if not node:
            continue
        if node['kind'] == 'deliverable':
            task['wbs_deliverable_id'] = node['id']
            task['wbs_phase_id'] = node['parent_id']
        else:
            task['wbs_phase_id'] = node['id']


def _draft_edit(project, actor, data):
    from .master_schedule import master_plan_state
    from .simple_planning import _locked, _persist, _cancel_review, _schedule_due_dates
    from .work_assignments import sync_workspace_assignments

    project, state = _locked(project, actor, data['revision'])
    if not can_edit_gantt(project, actor):
        _error('Your role or the current schedule state does not permit editing.', 'gantt_read_only', 403)
    before = deepcopy(state)
    tasks, kind, key = state['tasks'], data['kind'], data['id']
    by_id = {str(task['id']): task for task in tasks}
    parents = {str(parent['id']): parent for parent in state.get('deliverables', [])}
    groups = {str(group['code']): group for group in state.get('disciplines', [])}
    nodes, assignments = manual_wbs(tasks) if project.planning_mode == 'manual' else ([], {})
    node = next((node for node in nodes if str(node['id']) == key), None)
    selected = set()
    if kind == 'activity':
        if key not in by_id:
            _missing()
        selected.add(key)
    elif kind == 'deliverable':
        if key not in parents:
            _missing()
        selected.update(str(task['id']) for task in tasks if str(task.get('parent_deliverable_id')) == key)
    elif kind == 'discipline' or kind == 'wbs' and key.startswith('draft:'):
        code = key.removeprefix('draft:') if kind == 'wbs' else key
        if code not in groups and not any(task['discipline'] == code for task in tasks):
            _missing()
        selected.update(str(task['id']) for task in tasks if task['discipline'] == code
                        and (kind == 'discipline' or task['id'] not in assignments))
        kind, key = 'discipline', code
    elif kind == 'wbs' and node:
        descendant_ids = _descendants(nodes, [key])
        selected.update(str(task_id) for task_id, node_id in assignments.items() if str(node_id) in descendant_ids)
    else:
        _missing()

    if data['action'] == 'rename':
        title = data['title']
        if kind == 'activity':
            by_id[key]['title'] = title
        elif kind == 'deliverable':
            parents[key]['title'] = title
        elif kind == 'discipline':
            if len(title) > 120:
                _error('Workstream names cannot exceed 120 characters.')
            if key not in groups:
                state.setdefault('disciplines', []).append({'code': key, 'name': title})
            else:
                groups[key]['name'] = title
        else:
            field = 'wbs_phase' if node['kind'] == 'phase' else 'wbs_deliverable'
            if field == 'wbs_phase' and len(title) > 120:
                _error('Phase names cannot exceed 120 characters.')
            # A duplicate sibling name would merge two derived paths.
            if any(other['id'] != node['id'] and other['parent_id'] == node['parent_id']
                   and other['name'] == title for other in nodes):
                _error('Another group in this phase already uses that name.', 'gantt_row_name_conflict')
            _manual_identity(tasks, nodes, assignments)
            for task in tasks:
                if str(task['id']) in selected:
                    task[field] = title
        _workflow_membership(state, set())
    else:
        state['tasks'] = [task for task in tasks if str(task['id']) not in selected]
        _remove_links(state['tasks'], selected)
        _workflow_membership(state, selected)
        if kind == 'discipline' and not any(task['discipline'] == key for task in state['tasks']):
            state['disciplines'] = [group for group in state.get('disciplines', []) if group['code'] != key]

    validate_planner_network(state['tasks'])
    _schedule_due_dates(project, state['tasks'])
    managed = set(state.get('managed_task_ids', [])) | set(by_id)
    sync_workspace_assignments(project, state['tasks'], actor=actor, token=state['assignment_token'],
                               intelligence_run_id=state.get('intelligence_run_id'), managed_task_ids=managed)
    _cancel_review(state, actor)
    state.update(state='review', revision=state['revision'] + 1, version_id=None, review_id=None,
                 managed_task_ids=sorted(managed))
    state.pop('schedule_proposal', None)
    if data['action'] == 'delete':
        state['warnings'] = [row for row in state.get('warnings', []) if row.get('code') != 'planner_rows_removed']
        state['warnings'].append({'code': 'planner_rows_removed', 'severity': 'warning',
            'message': 'The selected rows and their incident links were removed. Review the remaining workflow and logic.'})
    _persist(project, state, actor, 'simple_plan.row_edited', before)
    return master_plan_state(project, actor)


def _version_edit(project, actor, data):
    from .master_schedule import _clone, _locked, master_plan_state
    from .operational_jobs import schedule_state_fingerprint
    from .simple_planning import _version_tasks

    project, version = _locked(project, actor, data['revision'], 'edit-row')
    if not can_edit_gantt(project, actor, version):
        _error('Create an editable revision before changing an approved or submitted schedule.', 'gantt_read_only')
    snapshot = deepcopy(version.evidence_input_snapshot or {})
    if snapshot.get('schema') == SCHEMA and not planner_inputs_current(version, snapshot):
        _error('The planner revision changed outside the editor. Reconcile the changed inputs before editing.',
               'planner_revision_inputs_changed')
    tasks = _version_tasks(version)
    by_id = {str(task['id']): task for task in tasks}
    nodes = list(version.wbs_nodes.filter(is_deleted=False).values('id', 'parent_id', 'code', 'name'))
    node_by_id = {str(node['id']): node for node in nodes}
    kind, key = data['kind'], data['id']
    protected_code = None
    if kind in {'wbs', 'deliverable'}:
        # A persisted source WBS may be reused as the displayed project root.
        displayed = master_plan_state(project, actor, version_id=(
            snapshot.get('parent_version_id') if snapshot.get('schema') == SCHEMA else None))
        displayed_nodes = displayed.get('wbs_nodes', [])
        all_ids = {str(node['id']) for node in displayed_nodes}
        roots = [node for node in displayed_nodes if str(node.get('parent_id')) not in all_ids]
        identity = displayed.get('project', {})
        normalize = lambda value: ' '.join(str(value or '').lower().split())
        if len(roots) == 1 and (
                roots[0].get('is_source_project')
                or identity.get('code') and normalize(roots[0]['code']) == normalize(identity['code'])
                or identity.get('name') and normalize(roots[0]['name']) == normalize(identity['name'])):
            protected_code = roots[0]['code']
    selected, node_ids, parent_ids = set(), set(), set()
    if kind == 'activity':
        if key not in by_id:
            _missing()
        selected.add(key)
    elif kind == 'deliverable':
        selected.update(str(task['id']) for task in tasks if str(task.get('parent_deliverable_id')) == key)
        if not selected:
            _missing()
        parent_ids.add(key)
        # Only remove/rename a dedicated deliverable node, never a shared group.
        for candidate in {str(by_id[task_id]['wbs_node_id']) for task_id in selected}:
            if candidate in node_by_id and all(str(task['id']) in selected for task in tasks
                                              if str(task['wbs_node_id']) == candidate):
                if not any(str(node['parent_id']) == candidate for node in nodes):
                    node_ids.add(candidate)
        common = _common_ancestor(nodes, {str(by_id[task_id]['wbs_node_id']) for task_id in selected})
        if common and node_by_id[common]['parent_id'] is not None:
            subtree = _descendants(nodes, [common])
            if all(str(task['id']) in selected for task in tasks if str(task['wbs_node_id']) in subtree):
                node_ids = subtree if data['action'] == 'delete' else {common}
    elif kind == 'wbs' and key in node_by_id:
        if node_by_id[key]['code'] == protected_code:
            _error('The project summary cannot be renamed or deleted as a schedule row.', 'gantt_project_root_read_only')
        node_ids = _descendants(nodes, [key]) if data['action'] == 'delete' else {key}
        selected.update(str(task['id']) for task in tasks if str(task['wbs_node_id']) in node_ids)
    else:
        _missing()
    # Removing the final deliverable must not also remove its project wrapper.
    node_ids = {node_id for node_id in node_ids if node_by_id[node_id]['code'] != protected_code}

    previous_version_id = version.pk
    if snapshot.get('schema') != SCHEMA:
        parent = version
        fingerprint = schedule_state_fingerprint(parent)
        version = _clone(parent, actor)
        snapshot = {'schema': SCHEMA, 'parent_version_id': parent.pk,
                    'parent_input_fingerprint': fingerprint, 'source_snapshot': deepcopy(parent.evidence_input_snapshot),
                    'edits': {}}
        project.master_schedule_version = version
    # IDs change when a source version is cloned; source codes are version-unique.
    target_codes = {node_by_id[node_id]['code'] for node_id in node_ids}
    current_nodes = {node.code: node for node in version.wbs_nodes.filter(is_deleted=False)}
    rows = {row.external_id: row for row in version.activities.filter(is_deleted=False)}
    if data['action'] == 'rename':
        if kind == 'activity':
            row = rows[key]
            row.name = data['title']
            row.save(update_fields=['name', 'updated_at'])
        else:
            for code in target_codes:
                row = current_nodes[code]
                row.name = data['title']
                row.save(update_fields=['name', 'updated_at'])
            for parent_id in parent_ids:
                snapshot.setdefault('deliverable_titles', {})[parent_id] = data['title']
    else:
        now = timezone.now()
        ids = [rows[task_id].pk for task_id in selected]
        version.relationships.filter(Q(predecessor_id__in=ids) | Q(successor_id__in=ids),
                                     is_deleted=False).update(is_deleted=True, deleted_at=now, updated_at=now)
        ActivityAssignment.objects.filter(activity_id__in=ids, is_deleted=False).update(
            is_deleted=True, deleted_at=now, updated_at=now)
        version.activities.filter(pk__in=ids).update(is_deleted=True, deleted_at=now, updated_at=now)
        version.wbs_nodes.filter(code__in=target_codes).update(is_deleted=True, deleted_at=now, updated_at=now)
        # Builds may put each stage in a leaf WBS. Remove newly empty wrappers
        # up that removed workflow branch, stopping at a live branch or root.
        if kind == 'deliverable' or any(by_id[task_id].get('parent_deliverable_id') for task_id in selected):
            active = set(version.wbs_nodes.filter(is_deleted=False).values_list('pk', flat=True))
            occupied = set(version.activities.filter(is_deleted=False).values_list('wbs_node_id', flat=True))
            current_by_pk = {node.pk: node for node in current_nodes.values()}
            children = {}
            for node in current_nodes.values():
                if node.pk in active:
                    children.setdefault(node.parent_id, set()).add(node.pk)
            candidates = [current_nodes[node_by_id[str(by_id[task_id]['wbs_node_id'])]['code']].pk
                          for task_id in selected if str(by_id[task_id]['wbs_node_id']) in node_by_id]
            pruned, visited = set(), set()
            while candidates:
                candidate_id = candidates.pop()
                node = current_by_pk[candidate_id]
                if node.parent_id is None or candidate_id in visited:
                    continue
                if candidate_id in occupied or children.get(candidate_id):
                    continue
                visited.add(candidate_id)
                if candidate_id in active:
                    pruned.add(candidate_id)
                    active.remove(candidate_id)
                    children.get(node.parent_id, set()).discard(candidate_id)
                if node.parent_id in current_by_pk:
                    candidates.append(node.parent_id)
            version.wbs_nodes.filter(pk__in=pruned).update(is_deleted=True, deleted_at=now, updated_at=now)
        remaining = _version_tasks(version)
        members = {}
        for task in remaining:
            if task.get('parent_deliverable_id'):
                members.setdefault(str(task['parent_deliverable_id']), []).append(task['id'])
        changed_rows = []
        changed_parents = {str(by_id[task_id].get('parent_deliverable_id')) for task_id in selected}
        for task in remaining:
            row = rows[task['id']]
            metadata = deepcopy(row.metadata or {})
            if any(field in metadata for field in ('depends_on', 'dependency_details', 'dependency_rationales')):
                _remove_links([metadata], selected)
            parent_id = str(task.get('parent_deliverable_id'))
            if parent_id in members and parent_id in changed_parents:
                metadata['source_deliverable'] = {**task.get('source_deliverable', {}),
                    'workflow_task_ids': members[parent_id], 'workflow_structure_edited': True}
            if metadata != row.metadata:
                row.metadata, row.updated_at = metadata, now
                changed_rows.append(row)
        version.activities.model.objects.bulk_update(changed_rows, ['metadata', 'updated_at'], batch_size=500)
        for parent_id in list(snapshot.get('deliverable_titles', {})):
            if parent_id not in members:
                snapshot['deliverable_titles'].pop(parent_id)
        validate_planner_network(remaining)
    snapshot.setdefault('row_edits', []).append({**data, 'edited_by': str(actor.pk),
                                                'edited_at': timezone.now().isoformat()})
    version.activities.filter(is_deleted=False).update(planned_start=None, planned_finish=None,
        early_start=None, early_finish=None, late_start=None, late_finish=None,
        total_float_days=None, free_float_days=None, is_critical=False)
    snapshot['input_fingerprint'] = _input_fingerprint(version)
    snapshot['wbs_input_fingerprint'] = _wbs_fingerprint(version)
    version.evidence_input_snapshot = _safe(snapshot)
    version.status, version.calculated_at, version.calculated_finish = 'draft', None, None
    version.change_summary = 'Planner row changes from the Gantt table'
    version.save(update_fields=['evidence_input_snapshot', 'status', 'calculated_at', 'calculated_finish',
                                'change_summary', 'updated_at'])
    project.master_schedule_revision += 1
    project.save(update_fields=['master_schedule_version', 'master_schedule_revision', 'updated_at'])
    record_event(project=project, actor=actor, action='schedule.row_edited', entity=version,
                 before={'version_id': previous_version_id}, after=_safe(data),
                 metadata={'source_preserved': True, 'calculation_invalidated': True,
                           'removed_activity_ids': sorted(selected) if data['action'] == 'delete' else []})
    return master_plan_state(project, actor)


@transaction.atomic
def edit_gantt_row(project, actor, data):
    project = PlanningProject.objects.select_for_update().get(pk=project.pk, is_deleted=False)
    if data['id'].startswith('project:'):
        _error('The project summary cannot be renamed or deleted as a schedule row.', 'gantt_project_root_read_only')
    return _version_edit(project, actor, data) if project.master_schedule_version_id else _draft_edit(project, actor, data)
