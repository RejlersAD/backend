"""One editable planning canvas with explicit submission and baseline approval.

Document findings and durations are draft inputs. Only the existing business
authority and schedule-assurance guards can approve and publish a baseline.
"""
from collections import deque
from copy import deepcopy
from datetime import date, timedelta
from math import ceil, isfinite
import re
import hashlib

from django.core import signing
from django.db import transaction
from django.http import Http404
from django.utils import timezone

from apps.rbac.action_policy import module_action_allowed
from apps.core.task_assignment_policy import manages_project_tasks
from apps.core.project_models import ProjectTask
from ..access import can_write_project, proposal_approver_users
from ..models import PlanningProject, ScheduleBaseline, ScheduleReview, ScheduleReviewDecision, ScheduleVersion
from .audit import record_event
from .cpm import WorkdayCalendar, calculate_schedule_version, calculate_backward_pass, _edge_weight, _finish_date
from .document_intelligence import run_document_intelligence
from .operational_jobs import canonical_fingerprint
from .schedule_approval import (
    ScheduleApprovalError, can_baseline_schedule, can_decide_schedule_review,
    current_schedule_version, decide_schedule_review, require_schedule_authority,
)
from .trustworthy_scheduling import approve_schedule_assurance, current_assurance, run_schedule_assurance
from .work_assignments import hydrate_assignments, normalize_assignment_fields, sync_workspace_assignments, source_key
from .work_breakdown import _initial_tasks, materialize_work_breakdown
from .simple_schedule_proposal import proposal_context, schedule_status, retain_supported_dependencies
from .source_duration_review import reconcile_source_durations
from .fixed_horizon_proposal import protected_work_ids
from .document_plan import project_document_plan, simple_tasks
from .source_date_read_model import enrich_source_dates
from .source_schedule_preview import document_schedule_summary
from .draft_source_reconciliation import invalidate_stale_source_evidence
from .source_timing_constraints import source_timing_evidence
from .simple_workflow_expansion import expand_workflow_deliverables
from .source_schedule_verification import reference_schedule_blocker, verify_plan_sources
from .schedule_check_details import schedule_timing_blockers


WORKFLOW_FIELDS = ('parent_deliverable_id', 'deliverable', 'workflow_stage_code', 'workflow_stage_name',
                   'workflow_stage_sequence', 'workflow_template_id', 'workflow_template_code', 'workflow_template_version',
                   'responsible_role', 'workflow_responsible_party', 'activity_type', 'is_milestone',
                   'workflow_progress_weight', 'workflow_release_gate', 'source_parent_values', 'source_deliverable')
SOURCE_FIELDS = ('source_activity_id', 'source_evidence', 'dependency_status', 'source_missing_fields', 'duration_unit',
                 'evidence_policy', 'duration_policy', 'evidence_entity_id', 'identity_review',
                 'source_evidence_history', 'source_evidence_review', 'sequence_review_required', 'dependency_review_reason',
                 'planned_start_date_source', 'planned_finish_date_source', 'date_authority')


def _milestone(task):
    return bool(task.get('is_milestone') or task.get('activity_type') in {'start_milestone', 'finish_milestone'})


class SimplePlanningError(ScheduleApprovalError):
    pass


def _error(message, code='simple_plan_conflict', **details):
    raise SimplePlanningError(message, code=code, **details)


def _fingerprint(project):
    return canonical_fingerprint({
        'name': project.name, 'scope': project.scope_summary, 'phase': project.phase,
        'exclusions': project.exclusions, 'start': project.effective_date,
        'finish': project.planned_end_date, 'calendar': project.calendar_overrides,
        'files': list(project.files.filter(is_deleted=False).order_by('id').values(
            'id', 'category', 'parse_status', 'updated_at', 'size_bytes',
        )),
    })


def _seed_task(task):
    task = deepcopy(task)
    task.setdefault('owner', '')
    task.setdefault('effort_hours', None)
    task.setdefault('depends_on', [])
    task.setdefault('acceptance_criteria', '')
    task.setdefault('reviewer', '')
    task.setdefault('source_references', [])
    task.setdefault('document_number', '')
    task.setdefault('document_revision', '')
    if _milestone(task) and task.get('evidence_policy') != 'document_driven':
        task['duration_days'] = 0
        task.setdefault('duration_source', 'planner')
    elif task.get('duration_days') is None:
        task['duration_days'] = None
        task.setdefault('duration_source', 'missing_source')
    else:
        task['duration_days'] = float(task['duration_days'])
        task.setdefault('duration_source', 'planner')
    normalize_assignment_fields([task], {})
    task.setdefault('due_date_source', 'explicit' if task.get('due_date') else 'schedule')
    return task


def _legacy_seed(project):
    """Read existing saved work without modifying any legacy planning record."""
    manual = project.manual_work_breakdown or {}
    if project.planning_mode == 'manual' and manual.get('tasks'):
        return {**deepcopy(manual), 'assignment_token': f'manual:{project.pk}', 'intelligence_run_id': None}
    run = project.intelligence_runs.filter(is_deleted=False, status='succeeded').first()
    confirmation = ((run.summary or {}).get('preview_confirmation') or {}) if run else {}
    token = confirmation.get('confirmed_at')
    saved = ((run.summary or {}).get('work_breakdown_drafts') or {}).get(token) if run else None
    if saved and saved.get('tasks'):
        labels = ((run.summary or {}).get('base_intelligence') or {}).get('disciplines') or {}
        return {**deepcopy(saved), 'assignment_token': token, 'intelligence_run_id': run.pk,
                'disciplines': saved.get('disciplines') or [
                    {'code': code, 'name': labels.get(code, {}).get('name') or code.replace('_', ' ').title()}
                    for code in dict.fromkeys(task['discipline'] for task in saved['tasks'])]}
    return None


def _version_tasks(version):
    """Read every persisted activity and relationship; never reinterpret them."""
    activities = list(version.activities.filter(is_deleted=False).select_related('wbs_node').order_by('sort_order', 'id'))
    task_ids = {row.pk: row.external_id for row in activities}
    dependencies = {row.pk: [] for row in activities}
    details = {row.pk: [] for row in activities}
    for link in version.relationships.filter(is_deleted=False):
        if link.predecessor_id in task_ids and link.successor_id in task_ids:
            dependencies[link.successor_id].append(task_ids[link.predecessor_id])
            details[link.successor_id].append({'task_id': task_ids[link.predecessor_id],
                                             'type': link.relationship_type, 'lag_days': float(link.lag_days)})
    tasks = [{
        'id': row.external_id, 'title': row.name, 'discipline': row.discipline or 'general',
        'owner': (row.metadata or {}).get('owner', row.responsible_role), 'effort_hours': (row.metadata or {}).get('planned_effort_hours'),
        'responsible_role': row.responsible_role or None,
        'duration_days': float(row.duration_days), 'duration_source': (row.metadata or {}).get('duration_source', 'unknown'),
        'depends_on': dependencies[row.pk], 'dependency_details': details[row.pk],
        'acceptance_criteria': (row.metadata or {}).get('acceptance_criteria', ''),
        'reviewer': (row.metadata or {}).get('reviewer', ''),
        'assignee_id': (row.metadata or {}).get('assignee_id'), 'reviewer_id': (row.metadata or {}).get('reviewer_id'),
        'task_type': (row.metadata or {}).get('task_type', 'task'), 'priority': (row.metadata or {}).get('priority', 'medium'),
        'due_date': (row.metadata or {}).get('due_date'),
        'source_references': (row.metadata or {}).get('source_references') or [],
        'document_number': (row.metadata or {}).get('document_number', ''),
        'document_revision': (row.metadata or {}).get('document_revision', ''),
        'planned_start_date': row.planned_start.isoformat() if row.planned_start else None,
        'planned_finish_date': row.planned_finish.isoformat() if row.planned_finish else None,
        'is_milestone': row.is_milestone, 'activity_type': row.activity_type,
        'workflow_stage': (row.metadata or {}).get('workflow_stage_code') or (row.metadata or {}).get('workflow_stage'), 'activity_id': row.pk,
        **{key: (row.metadata or {})[key] for key in (*WORKFLOW_FIELDS, *SOURCE_FIELDS) if key in (row.metadata or {})},
        'external_id': row.external_id, 'wbs_node_id': row.wbs_node_id,
        'activity_code': row.external_id, 'activity_code_source': 'schedule_activity',
        'sort_order': row.sort_order,
        'wbs_code': row.wbs_node.code if row.wbs_node else '',
        'constraint_type': row.constraint_type, 'calendar_id': row.calendar_id,
        **{key: (row.metadata or {})[key] for key in
           ('property_provenance', 'derivation', 'schedule_rationale', 'schedule_phase', 'schedule_generated_fields', 'dependency_rationales',
            'duration_evidence', 'duration_comparison_evidence', 'duration_review_status', 'duration_review_reason', 'duration_calendar_verified')
           if key in (row.metadata or {})},
    } for row in activities]
    if version.planning_build_id:
        build = version.planning_build
        inputs = build.evidence_snapshot.get('accepted_inputs', {})
        expected = {row['id']: row for row in build.plan.get('activities', [])}
        workflow = build.profile_snapshot.get('definition', {}).get('workflow') or {}
        stages = {row['code']: row for row in workflow.get('stages', [])}
        for task in tasks:
            row = expected.get(task['id'])
            if not row or row.get('kind') != 'workflow_activity':
                continue
            entity = row['source_entity_id']
            values = inputs.get(entity, {})
            stage = stages.get(row.get('workflow_stage_code'), {})
            discipline = values.get('discipline')
            if isinstance(discipline, dict):
                discipline = discipline.get('name') or discipline.get('code')
            source = {'id': entity, 'title': values.get('identity'), 'discipline': discipline or 'general'}
            task.update(parent_deliverable_id=entity, source_deliverable=source, deliverable=source['title'],
                        discipline=discipline or 'general', workflow_stage_sequence=stage.get('sequence'),
                        workflow_stage_name=row.get('workflow_stage_name'), workflow_template_id=workflow.get('id'),
                        workflow_template_code=workflow.get('code'), workflow_template_version=workflow.get('version'))
    return tasks


def _draft(project):
    if project.simple_planning_state:
        return deepcopy(project.simple_planning_state)
    old = _legacy_seed(project)
    tasks = [_seed_task(task) for task in (old or {}).get('tasks', [])]
    state = {
        'state': 'review' if tasks else 'inputs', 'revision': 0,
        'tasks': tasks, 'disciplines': deepcopy((old or {}).get('disciplines') or []),
        'assignment_token': (old or {}).get('assignment_token') or f'simple:{project.pk}',
        'intelligence_run_id': (old or {}).get('intelligence_run_id'),
        'input_fingerprint': _fingerprint(project), 'version_id': None,
        'review_id': None, 'baseline_id': None, 'warnings': [],
        'method': 'existing_plan' if old else 'manual',
    }
    # An existing baseline remains controlled on first opening the new canvas.
    version_id = (old or {}).get('schedule_version_id')
    baseline = ScheduleBaseline.objects.filter(
        schedule__project=project, is_deleted=False,
        **({'source_version_id': version_id} if version_id else {}),
    ).select_related('source_version').first()
    if baseline:
        state.update(state='baselined', baseline_id=baseline.pk, version_id=baseline.source_version_id,
                     legacy_version_import=True, tasks=_version_tasks(baseline.source_version))
    return state


def _calendar_record(project):
    schedule = project.schedules.filter(is_deleted=False, code='MASTER').select_related('default_calendar').first()
    calendar = schedule.default_calendar if schedule else None
    if not calendar:
        calendar = project.work_calendars.filter(is_deleted=False, is_default=True).first()
    return calendar


def _calendar(project):
    calendar = _calendar_record(project)
    return WorkdayCalendar(calendar, project.effective_date) if project.effective_date else None


def _span_summary(tasks, calendar):
    """A summary is its descendants' calendar span, never their summed effort."""
    starts = [task['planned_start_date'] for task in tasks if task.get('planned_start_date')]
    finishes = [task['planned_finish_date'] for task in tasks if task.get('planned_finish_date')]
    complete = bool(tasks) and len(starts) == len(tasks) and len(finishes) == len(tasks)
    start = min(starts) if starts else None
    finish = max(finishes) if finishes else None
    duration = None
    if complete and start <= finish:
        cursor, last = date.fromisoformat(start), date.fromisoformat(finish)
        duration = 0
        while cursor <= last:
            duration += int(calendar.is_working(cursor))
            cursor += timedelta(days=1)
        if start == finish and all(task.get('is_milestone') for task in tasks):
            duration = 0
    floats = [task['total_float_days'] for task in tasks if task.get('total_float_days') is not None]
    critical = [task['is_critical'] for task in tasks if task.get('is_critical') is not None]
    return {
        'planned_start_date': start, 'planned_finish_date': finish, 'duration_days': duration,
        'total_float_days': min(floats) if floats and len(floats) == len(tasks) else None,
        'is_critical': True if any(critical) else False if critical and len(critical) == len(tasks) else None,
        'task_count': len(tasks), 'complete': complete, 'duration_basis': 'working_calendar_span',
    }


def _canvas_metadata(project, state, version, activities):
    """Enrich the read model without creating WBS nodes or rewriting the draft."""
    calendar_record = version.schedule.default_calendar if version else _calendar_record(project)
    origin = version.schedule.planned_start if version else project.effective_date
    calendar = WorkdayCalendar(calendar_record, origin or date.today())
    calendar_data = {
        'id': calendar_record.pk if calendar_record else None,
        'name': calendar_record.name if calendar_record else 'Monday–Friday',
        'working_weekdays': sorted(calendar.weekdays),
        'hours_per_day': float(calendar_record.hours_per_day) if calendar_record else 8,
        'timezone': calendar_record.timezone if calendar_record else None,
        'exceptions': [{
            'date': item.date.isoformat(), 'is_working': item.is_working, 'name': item.name,
            'working_hours': float(item.working_hours) if item.working_hours is not None else None,
        } for item in calendar_record.exceptions.filter(is_deleted=False).order_by('date')] if calendar_record else [],
        'duration_basis': 'working_days',
        'is_fallback': calendar_record is None,
        'source_verified': False,
        'evidence_status': 'Not Specified',
    }
    if version:
        nodes = [{
            'id': node.pk, 'parent_id': node.parent_id, 'code': node.code, 'name': node.name,
            'discipline': node.discipline, 'sort_order': node.sort_order, 'level': node.level,
            'is_derived': False,
        } for node in version.wbs_nodes.filter(is_deleted=False).order_by('sort_order', 'id')]
        state['hierarchy_source'] = 'schedule_version'
    else:
        labels = {row['code']: row['name'] for row in state['disciplines']}
        codes = list(dict.fromkeys(task['discipline'] for task in state['tasks']))
        nodes = [{
            'id': f'draft:{code}', 'parent_id': None, 'code': f'{index + 1}.0',
            'name': labels.get(code) or code.replace('_', ' ').title(), 'discipline': code,
            'sort_order': index, 'level': 0, 'is_derived': True,
        } for index, code in enumerate(codes)]
        state['hierarchy_source'] = 'draft_workstreams'
    draft_nodes = {node['discipline']: node for node in nodes} if not version else {}
    activity_positions = {}
    version_positions = {}
    for activity in sorted(activities.values(), key=lambda row: (row.sort_order, row.pk)):
        version_positions[activity.wbs_node_id] = version_positions.get(activity.wbs_node_id, 0) + 1
        activity_positions[activity.pk] = version_positions[activity.wbs_node_id]
    positions = {}
    for index, task in enumerate(state['tasks']):
        activity = activities.get(task['id'])
        if activity:
            activity_code = activity.external_id
            code_source = 'schedule_activity'
            if re.fullmatch(r'task-(?:[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})', activity_code, flags=re.I):
                document_number = task.get('document_number') or (activity.metadata or {}).get('document_number')
                if document_number:
                    activity_code, code_source = document_number, 'document_number'
                elif activity.wbs_node:
                    prefix = activity.wbs_node.code.removesuffix('.0')
                    activity_code = f'{prefix}.{activity_positions[activity.pk]}'
                    code_source = 'schedule_wbs'
            task.update(
                activity_id=activity.pk, external_id=activity.external_id,
                activity_code=activity_code, activity_code_source=code_source,
                wbs_node_id=activity.wbs_node_id,
                wbs_code=activity.wbs_node.code if activity.wbs_node else '',
                is_milestone=activity.is_milestone, activity_type=activity.activity_type,
                duration_days=float(activity.duration_days),
                sort_order=activity.sort_order, calendar_id=activity.calendar_id,
            )
        elif not version:
            node = draft_nodes[task['discipline']]
            positions[node['id']] = positions.get(node['id'], 0) + 1
            row_code = f"{node['sort_order'] + 1}.{positions[node['id']]}"
            task.update(
                external_id=None, activity_code=task.get('document_number') or row_code,
                activity_code_source='document_number' if task.get('document_number') else 'draft_wbs',
                wbs_node_id=node['id'], wbs_code=row_code, sort_order=index,
                is_milestone=_milestone(task),
                activity_type=task.get('activity_type') or 'task',
            )
    by_id = {node['id']: node for node in nodes}
    task_by_id = {task['id']: task for task in state['tasks']}
    parent_positions = {}
    for parent in state.get('deliverables') or []:
        children = [task_by_id[key] for key in parent.get('workflow_task_ids') or [] if key in task_by_id]
        parent['summary'] = _span_summary(children, calendar)
        if not children:
            continue
        if version:
            node = by_id.get(children[0].get('wbs_node_id'))
            if node:
                parent.update(wbs_node_id=node['id'], parent_wbs_node_id=node['parent_id'], activity_code=node['code'])
        else:
            node = draft_nodes[children[0]['discipline']]
            parent_positions[node['id']] = parent_positions.get(node['id'], 0) + 1
            parent.pop('wbs_node_id', None)
            parent.update(parent_wbs_node_id=node['id'],
                          activity_code=parent.get('document_number') or f"{node['sort_order'] + 1}.{parent_positions[node['id']]}")
        for child in children:
            child['activity_code'] = f"{parent.get('activity_code') or parent['id']}-{child['workflow_stage_code']}"
            child['activity_code_source'] = 'deliverable_workflow'
    state['deliverable_count'] = len(state.get('deliverables') or [])
    descendants = {node['id']: [] for node in nodes}
    for task in state['tasks']:
        node_id = task.get('wbs_node_id')
        visited = set()
        while node_id in by_id and node_id not in visited:
            visited.add(node_id)
            descendants[node_id].append(task)
            node_id = by_id[node_id]['parent_id']
    for node in nodes:
        node['summary'] = _span_summary(descendants[node['id']], calendar)
    start = project.effective_date.isoformat() if project.effective_date else None
    finish = project.planned_end_date.isoformat() if project.planned_end_date else None
    state.update(
        project={'id': project.pk, 'enterprise_project_id': project.enterprise_project_id,
                 'code': project.enterprise_project.code if project.enterprise_project_id else '',
                 'name': project.name, 'phase': project.phase, 'start_date': start, 'end_date': finish,
                 'planned_start_date': start, 'planned_finish_date': finish},
        wbs_nodes=nodes, calendar=calendar_data, work_calendar=calendar_data,
        project_summary=_span_summary(state['tasks'], calendar),
    )


def _dated_tasks(project, tasks):
    tasks = deepcopy(tasks)
    for task in tasks:
        task.update(is_critical=None, total_float_days=None, free_float_days=None,
                    calculated=False, calculation_basis=None)
    if tasks and all(task.get('evidence_policy') == 'document_driven'
                     and task.get('duration_calendar_verified') is False for task in tasks):
        # Imported facts, including an invalid or incomplete source network,
        # must remain reviewable without treating them as executable CPM.
        for task in tasks:
            task.update(planned_start_date=None, planned_finish_date=None,
                        early_start=None, early_finish=None, late_start=None, late_finish=None)
        return tasks
    calendar = _calendar(project)
    if not calendar:
        return tasks
    by_id = {task['id']: task for task in tasks}
    if len(by_id) != len(tasks):
        _error('Each activity must have a unique ID.', 'simple_plan_dependencies')
    outgoing = {key: [] for key in by_id}
    weighted_outgoing = {key: [] for key in by_id}
    incoming = {}
    durations = {}
    for task in tasks:
        value = task.get('duration_days')
        valid = value is not None and isfinite(float(value)) and (float(value) == 0 if _milestone(task) else float(value) > 0)
        # A printed duration alone does not establish a working calendar. Do not
        # recalculate its dates on the application fallback calendar.
        verified_calendar = not (task.get('duration_source') == 'source_document'
                                 and task.get('duration_calendar_verified') is False)
        # Withdrawing obsolete source logic must not turn it into a new,
        # independent activity on the next read of the draft.
        verified_evidence = (task.get('source_evidence_review') or {}).get('status') != 'requires_review'
        durations[task['id']] = ceil(float(value)) if valid and verified_calendar and verified_evidence else None
    duration_complete = all(value is not None for value in durations.values())
    for task in tasks:
        dependencies = task.get('depends_on') or []
        if len(set(dependencies)) != len(dependencies) or any(key not in by_id or key == task['id'] for key in dependencies):
            _error('Dependencies must refer to other activities in this plan.', 'simple_plan_dependencies')
        incoming[task['id']] = len(dependencies)
        for predecessor in dependencies:
            outgoing[predecessor].append(task['id'])
    pending = deque(key for key, count in incoming.items() if count == 0)
    starts, visited = {}, set()
    while pending:
        task = by_id[pending.popleft()]
        visited.add(task['id'])
        links = {}
        for link in task.get('dependency_details') or []:
            links.setdefault(link['task_id'], []).append(link)
        bounds = []
        resolved = durations[task['id']] is not None
        for predecessor in task.get('depends_on') or []:
            for link in links.get(predecessor) or [{}]:
                kind = link.get('type', 'FS')
                if kind not in {'FS', 'SS', 'FF', 'SF'}:
                    _error('Select a supported relationship type.', 'simple_plan_dependencies')
                if predecessor not in starts or durations[task['id']] is None:
                    resolved = False
                    continue
                weight = _edge_weight(kind, durations[predecessor], durations[task['id']], link.get('lag_days', 0))
                bounds.append(starts[predecessor] + weight)
                weighted_outgoing[predecessor].append((task['id'], weight))
        if resolved:
            earliest = max(bounds, default=0)
            requested_start = task.get('planned_start_date')
            if requested_start:
                earliest = max(earliest, calendar.index_of(date.fromisoformat(str(requested_start))))
            task['planned_start_date'] = calendar.date_at(earliest).isoformat()
            task['planned_finish_date'] = _finish_date(calendar, earliest, durations[task['id']]).isoformat()
            starts[task['id']] = earliest
        else:
            # Input constraints remain in the persisted task and source evidence;
            # this read model must not display stale calculated bars or float.
            task.update(planned_start_date=None, planned_finish_date=None,
                        early_start=None, early_finish=None, late_start=None, late_finish=None)
        for successor in outgoing[task['id']]:
            incoming[successor] -= 1
            if incoming[successor] == 0:
                pending.append(successor)
    if len(visited) != len(tasks):
        _error('Dependencies must not form a cycle.', 'simple_plan_dependencies')
    if tasks and duration_complete:
        finish_index = (calendar.index_of(calendar.on_or_before(project.planned_end_date))
                        if project.planned_end_date else max(starts[key] + max(durations[key] - 1, 0) for key in starts))
        late, free_float = calculate_backward_pass(list(starts), durations, starts, weighted_outgoing, finish_index)
        for task in tasks:
            key = task['id']
            total_float = late[key] - starts[key]
            task.update(
                early_start=task['planned_start_date'], early_finish=task['planned_finish_date'],
                late_start=calendar.date_at(late[key]).isoformat(),
                late_finish=_finish_date(calendar, late[key], durations[key]).isoformat(),
                total_float_days=total_float,
                free_float_days=free_float[key],
                is_critical=total_float <= 0, calculated=True, calculation_basis='draft_cpm',
            )
    return list(by_id.values())


def _blockers(project, state):
    blockers = []
    source_blocker = reference_schedule_blocker(project.files.filter(is_deleted=False))
    if source_blocker:
        blockers.append(source_blocker)
    if not state['tasks']:
        blockers.append({'code': 'tasks_required', 'message': 'Add at least one task to the plan.'})
    if not project.effective_date or not project.planned_end_date:
        blockers.append({'code': 'dates_required', 'message': 'Set the project start and end dates.'})
    elif project.planned_end_date < project.effective_date:
        blockers.append({'code': 'dates_invalid', 'message': 'The project end date must follow its start date.'})
    if state.get('input_fingerprint') != _fingerprint(project):
        blockers.append({'code': 'inputs_changed', 'message': 'Project inputs changed. Analyse the updated inputs before submitting.'})
    if project.files.filter(is_deleted=False).exclude(parse_status='done').exists():
        blockers.append({'code': 'documents_processing', 'message': 'Wait for uploaded documents to finish processing, or remove failed uploads.'})
    for task in state['tasks']:
        if task.get('duration_days') is None or (float(task['duration_days']) <= 0 and not _milestone(task)):
            blockers.append({'code': 'duration_required', 'message': f"Missing planned source duration: {task['title']}.",
                             'task_id': task['id'], 'task_ids': [task['id']], 'field': 'duration_days',
                             'resolution': 'Match the activity to an uploaded planned duration. A general review allowance is not an activity-specific planned duration.'})
    unverified_calendar = [task['id'] for task in state['tasks']
                           if task.get('duration_source') == 'source_document'
                           and task.get('duration_calendar_verified') is False]
    if unverified_calendar:
        blockers.append({'code': 'source_calendar_unverified',
                         'message': 'Printed durations are available, but their working calendar has not been verified.',
                         'task_ids': unverified_calendar, 'task_count': len(unverified_calendar),
                         'resolution': 'Verify the source working calendar before calculating or publishing dates from printed durations.'})
    return blockers


def _proposal_review_notes(project, state):
    """Keep proposal caveats visible while deriving date checks from current rows."""
    proposal = state.get('schedule_proposal')
    if not proposal or state.get('viewing_history'):
        return
    duration_review = deepcopy(proposal.get('duration_review'))
    if duration_review:
        # Expose the saved source review on the canvas, refreshing current
        # values so later planner edits cannot keep an obsolete source badge.
        tasks = {task['id']: task for task in state['tasks']}
        rows = []
        count_keys = {'source_verified': 'source_document_count',
                      'requirement_needs_review': 'source_requirement_count',
                      'missing_source': 'missing_source_count',
                      'manual_unverified': 'manual_unverified_count',
                      'started_unverified': 'started_unverified_count',
                      'milestone_definition': 'milestone_definition_count'}
        for key in count_keys.values():
            duration_review[key] = 0
        previous_rows = {row['task_id']: row for row in duration_review.get('rows') or []}
        for task in tasks.values():
            status = task.get('duration_review_status') or ('missing_source' if task.get('duration_days') is None else 'manual_unverified')
            row = {**deepcopy(previous_rows.get(task['id']) or {}),
                   'task_id': task['id'], 'title': task['title'], 'status': status,
                   'duration_days': task.get('duration_days'), 'duration_source': task.get('duration_source'),
                   'reason': task.get('duration_review_reason') or 'Planner edit requires source review.',
                   'source_references': deepcopy((task.get('duration_evidence') or {}).get('source_references') or [])}
            rows.append(row)
            if status in count_keys:
                duration_review[count_keys[status]] += 1
        for package in duration_review.get('package_reviews') or []:
            children = [tasks[key] for key in package['task_ids'] if key in tasks]
            values = [task.get('duration_days') for task in children]
            complete = len(children) == len(package['task_ids']) and all(value is not None for value in values)
            package.update(duration_complete=complete, duration_days=sum(values) if complete else None,
                           missing_duration_count=sum(value is None for value in values))
        duration_review.update(rows=rows, total_count=len(tasks), task_count=len(tasks))
        state['duration_review'] = duration_review
    warnings = list(state.get('warnings') or [])
    assumptions = list(state.get('assumptions') or [])
    assumption_messages = {item if isinstance(item, str) else item.get('message') for item in assumptions}
    for index, message in enumerate(proposal.get('assumptions') or []):
        if message not in assumption_messages:
            assumptions.append({'code': f'schedule_proposal_assumption_{index}', 'message': message})
            assumption_messages.add(message)
    # Static estimate/source caveats survive. Network and overrun observations
    # are recalculated below, rather than retaining an obsolete preview count.
    for item in proposal.get('warnings') or []:
        message = item if isinstance(item, str) else item.get('message', '')
        if any(phrase in message for phrase in ('activities finish after the registered target',
                                                'Source milestones are relative', 'No unambiguous technical gates')):
            continue
        warnings.append({'code': 'schedule_proposal_warning', 'message': message})
    relative = [row for row in proposal.get('source_constraints') or [] if row.get('kind') == 'relative_weeks']
    if relative:
        warnings.append({
            'code': 'schedule_award_anchor_unconfirmed',
            'message': 'Source milestones are relative to contract award/effective date. Confirm that anchor and the applicability of the cited requirements; the registered project start has not been treated as the award date.',
            'source_references': [reference for row in relative for reference in row.get('source_references') or []],
        })
    tasks = state['tasks']
    if len(tasks) > 1 and not any(task.get('depends_on') for task in tasks):
        warnings.append({'code': 'schedule_dependency_review',
                         'message': 'Activities have no predecessor links and run independently. Review the proposed sequence.'})
    target = project.planned_end_date.isoformat() if project.planned_end_date else None
    beyond = [task for task in tasks if target and task.get('planned_finish_date') and task['planned_finish_date'] > target]
    if beyond and not any(isinstance(row, dict) and row.get('code') == 'contract_finish_overrun' for row in warnings):
        warnings.append({
            'code': 'schedule_target_overrun', 'task_count': len(beyond),
            'task_ids': [task['id'] for task in beyond], 'target_finish_date': target,
            'forecast_finish_date': max(task['planned_finish_date'] for task in beyond),
            'message': f'{len(beyond)} activities finish after the registered target of {target}. Review the current dates, durations and dependencies before approval.',
        })
    seen, unique = set(), []
    for item in warnings:
        message = item if isinstance(item, str) else item.get('message') or item.get('code')
        if message not in seen:
            seen.add(message)
            unique.append(item)
    state.update(warnings=unique, assumptions=assumptions)


def plan_state(project, actor, *, version_id=None, state_override=None):
    state = deepcopy(state_override) if state_override is not None else _draft(project)
    # Older saved JSON drafts can predate these UI keys. Supply read-model
    # defaults without rewriting the user's preserved working draft.
    for key, value in {'state': 'review', 'tasks': [], 'disciplines': [], 'revision': 0,
                       'assignment_token': f'simple:{project.pk}'}.items():
        state.setdefault(key, value)
    state['evidence_policy'] = 'document_driven'
    state['duration_policy'] = 'source_only'
    state['current_version_id'] = state.get('version_id')
    viewing_history = version_id is not None
    if viewing_history:
        state.pop('schedule_proposal', None)
        selected = ScheduleVersion.objects.filter(pk=version_id, schedule__project=project, schedule__is_deleted=False, is_deleted=False).first()
        if not selected:
            raise Http404
        labels = {}
        for node in selected.wbs_nodes.filter(is_deleted=False).exclude(discipline='').order_by('sort_order', 'id'):
            labels.setdefault(node.discipline, node.name)
        state.update(version_id=selected.pk, tasks=_version_tasks(selected), disciplines=[
            {'code': code, 'name': name} for code, name in labels.items()],
            baseline_id=selected.baselines.filter(is_deleted=False).values_list('pk', flat=True).first())
        if selected.planning_build_id:
            state['assignment_token'] = f'planning-build:{selected.planning_build_id}'
        parents = {}
        for task in state['tasks']:
            if task.get('parent_deliverable_id') and task.get('source_deliverable'):
                parents.setdefault(task['parent_deliverable_id'], deepcopy(task['source_deliverable']))
        state['deliverables'] = list(parents.values())
        for parent in state['deliverables']:
            parent['workflow_task_ids'] = [task['id'] for task in state['tasks'] if task.get('parent_deliverable_id') == parent['id']]
        state['workflow_mode'] = 'standard_five' if parents else None
    state['viewing_history'] = viewing_history
    state['scheduling_status'] = schedule_status(state['tasks'], applied=bool(state.get('schedule_proposal')) and not viewing_history)
    state['legacy_read_only'] = bool(state.get('legacy_version_import'))
    state['read_only_reason'] = (
        'This existing published schedule retains its activity, milestone and dependency settings. Open Schedule Controls to create a revision without losing those settings.'
        if state['legacy_read_only'] else
        'You are viewing a saved schedule version. Return to the current draft to make changes.' if viewing_history else ''
    )
    version_rows = viewing_history or state.get('legacy_version_import')
    state['tasks'] = hydrate_assignments(project, state['tasks'] if version_rows else _dated_tasks(project, state['tasks']), state['assignment_token'])
    for task in state['tasks']:
        if not version_rows and task.get('due_date_source') == 'schedule':
            task['due_date'] = task.get('planned_finish_date')
    version = ScheduleVersion.objects.filter(
        pk=state.get('version_id'), schedule__project=project, schedule__is_deleted=False, is_deleted=False,
    ).select_related('schedule__default_calendar').first()
    activities = {row.external_id: row for row in version.activities.filter(is_deleted=False).select_related('wbs_node')} if version else {}
    for task in state['tasks']:
        activity = activities.get(task['id'])
        if version_rows:
            task.update(is_critical=None, total_float_days=None, free_float_days=None, calculated=False, calculation_basis=None)
        if activity and version.calculated_at:
            task.update(
                planned_start_date=activity.planned_start.isoformat() if activity.planned_start else None,
                planned_finish_date=activity.planned_finish.isoformat() if activity.planned_finish else None,
                early_start=activity.early_start.isoformat() if activity.early_start else None,
                early_finish=activity.early_finish.isoformat() if activity.early_finish else None,
                is_critical=activity.is_critical, calculated=True, calculation_basis='saved_version_cpm',
                total_float_days=float(activity.total_float_days) if activity.total_float_days is not None else None,
                free_float_days=float(activity.free_float_days) if activity.free_float_days is not None else None,
                late_start=activity.late_start.isoformat() if activity.late_start else None,
                late_finish=activity.late_finish.isoformat() if activity.late_finish else None,
            )
    state['version_number'] = version.version if version else None
    state['calculation_available'] = bool(state['tasks']) and all(task.get('calculated') for task in state['tasks'])
    state['calculation_basis'] = ('saved_version_cpm' if version and version.calculated_at else 'draft_cpm') if state['calculation_available'] else None
    state['schedule_versions'] = [{
        'id': item.pk, 'schedule_id': item.schedule_id, 'version': item.version, 'status': item.status,
        'version_number': item.version, 'created_at': item.created_at.isoformat(),
        'label': f'{item.schedule.name} · v{item.version} · {item.get_status_display()}',
    } for item in ScheduleVersion.objects.filter(
        schedule__project=project, schedule__is_deleted=False, is_deleted=False,
    ).select_related('schedule').order_by('-created_at', '-pk')[:100]]
    state['versions'] = state['schedule_versions']
    codes = {row['code'] for row in state['disciplines']}
    for task in state['tasks']:
        if task['discipline'] not in codes:
            state['disciplines'].append({'code': task['discipline'], 'name': task['discipline'].replace('_', ' ').title()})
            codes.add(task['discipline'])
    _canvas_metadata(project, state, version, activities)
    enrich_source_dates(state)
    blockers = _blockers(project, state)
    # These checks use the exact dates and float shown on the canvas. Reading a
    # draft never materializes a schedule or persists an assurance review.
    check_calendar = (WorkdayCalendar(version.schedule.default_calendar, version.schedule.planned_start)
                      if version and version.schedule.planned_start else _calendar(project))
    timing_findings = schedule_timing_blockers(state['tasks'], project.planned_end_date, check_calendar)
    blockers.extend(row for row in timing_findings if row.get('severity') != 'warning')
    # Recalculate advisory timing findings on every read; persisted submission
    # warnings must not keep obsolete dates after the draft has been repaired.
    timing_codes = {'negative_float', 'contract_finish_overrun', 'contractual_finish_overrun'}
    state['warnings'] = [row for row in state.get('warnings') or []
                         if not isinstance(row, dict) or row.get('code') not in timing_codes]
    state['warnings'].extend(row for row in timing_findings if row.get('severity') == 'warning')
    review = ScheduleReview.objects.filter(pk=state.get('review_id'), is_deleted=False).first()
    approvers = list(proposal_approver_users(project))
    edit = not viewing_history and can_write_project(actor, project) and module_action_allowed(actor, 'planning_package', 'update')
    can_approve = bool(review and (can_decide_schedule_review(review, actor)
                                  or (review.status == 'approved' and can_baseline_schedule(review.version, actor))))
    state.update({
        'project_id': project.pk, 'blockers': blockers,
        'stale_inputs': state.get('input_fingerprint') != _fingerprint(project),
        'assumptions': ([{'code': 'proposed_durations', 'message': 'Activity durations are proposed estimates. Review the schedule assumptions before approval.' if state.get('schedule_proposal') or any(task.get('schedule_generated_fields') for task in state['tasks']) else 'Unestimated tasks use a proposed five working days; effort-based durations assume eight hours per day. Review these estimates.'}]
                        if any(task.get('duration_source') == 'proposed' for task in state['tasks']) else [])
                       + ([{'code': 'dependency_review', 'message': 'Tasks without dependencies run independently. Review the intended sequence.'}]
                          if len(state['tasks']) > 1 and any(not task['depends_on'] for task in state['tasks']) else []),
        'permissions': {'can_edit': edit and state['state'] != 'baselined',
                        'can_assign': edit and state['state'] != 'baselined' and manages_project_tasks(actor, project.enterprise_project),
                        'can_submit': edit and state['state'] == 'review' and not blockers,
                        'can_approve_publish': not viewing_history and can_approve and not blockers,
                        'can_reopen': edit and state['state'] == 'baselined' and not state.get('legacy_version_import')},
        'approvers': [{'id': user.pk, 'name': user.get_full_name() or user.email or user.username} for user in approvers],
        'review': {'id': review.pk, 'status': review.status} if review else None,
    })
    baseline = ScheduleBaseline.objects.filter(pk=state.get('baseline_id'), is_deleted=False).first()
    state['baseline'] = {'id': baseline.pk, 'name': baseline.name, 'version_id': baseline.source_version_id,
                         'approved_at': baseline.approved_at.isoformat() if baseline.approved_at else None} if baseline else None
    state['source_documents'] = list(project.files.filter(is_deleted=False).order_by('id').values('id', 'original_filename', 'category', 'parse_status'))
    _proposal_review_notes(project, state)
    state['source_verification'] = verify_plan_sources(project, state)
    reference = state['source_verification'].get('schedule_reference') or {}
    state['source_preview_available'] = bool(
        (state.get('document_schedule_summary') or {}).get('activity_count')
        or reference.get('files'))
    state.pop('input_fingerprint', None)
    return state


def _require_schedule_proposal(project, actor, state, revision, workflow_mode=None):
    if project.master_schedule_version_id:
        _error('Open the preserved working draft before changing it.', 'master_schedule_accepted_inputs_read_only')
    if not can_write_project(actor, project) or not module_action_allowed(actor, 'planning_package', 'update'):
        _error('You do not have permission to propose this project schedule.', 'simple_plan_forbidden', status_code=403)
    if state['revision'] != revision:
        _error('The plan changed. Refresh before building a schedule.', 'simple_plan_revision_conflict', revision=state['revision'])
    if state['state'] == 'baselined' or state.get('legacy_version_import'):
        _error('Create an editable draft revision before building a schedule.', 'simple_plan_baselined')
    if workflow_mode not in {'standard_five', 'source_only'} and any(link.get('type', 'FS') != 'FS' or float(link.get('lag_days') or 0) != 0
           for task in state['tasks'] for link in task.get('dependency_details') or []):
        _error('This plan contains typed or lagged relationships. Use Schedule Controls to preserve its advanced network.', 'simple_plan_advanced_logic_required')
    if state.get('input_fingerprint') != _fingerprint(project):
        _error('Project inputs changed. Analyse the current inputs before building a schedule.', 'simple_plan_inputs_changed')
    if not state['tasks']:
        _error('Add activities or analyse the project documents first.', 'simple_plan_tasks_required')
    if not project.effective_date or not project.planned_end_date or project.planned_end_date < project.effective_date:
        _error('Set valid project start and finish dates first.', 'simple_plan_dates_required')
    if (project.planned_end_date - project.effective_date).days > 36525:
        _error('The project planning horizon cannot exceed 100 years.', 'simple_plan_dates_invalid')
    if project.files.filter(is_deleted=False).exclude(parse_status='done').exists():
        _error('Wait for document processing before building a schedule.', 'simple_plan_documents_processing')
    source_blocker = reference_schedule_blocker(project.files.filter(is_deleted=False))
    # An explicit template proposal is not a source import. Submission and
    # baseline publication keep the source-verification blocker unchanged.
    if source_blocker and workflow_mode not in {'standard_five', 'source_only'}:
        _error(source_blocker['message'], source_blocker['code'], files=source_blocker['files'])


def _proposal_fingerprint(context_hash, state, workflow_mode):
    return canonical_fingerprint({'context': context_hash, 'tasks': state['tasks'],
                                  'disciplines': state['disciplines'],
                                  'deliverables': state.get('deliverables') or [], 'workflow_mode': workflow_mode})


def _schedule_proposal(project, state, workflow_mode=None):
    context, context_hash = proposal_context(project, state, _calendar_record(project))
    fingerprint = _proposal_fingerprint(context_hash, state, workflow_mode)
    deliverables = []
    sequence_summary = None
    previous = hydrate_assignments(project, deepcopy(state['tasks']), state['assignment_token'])
    if workflow_mode == 'standard_five':
        context['workflow_mode'] = workflow_mode
        parents = deepcopy(state.get('deliverables') or previous)
        if state.get('deliverables'):
            parents.extend(task for task in previous if not task.get('parent_deliverable_id'))
        parents, removed_parent_links = retain_supported_dependencies(parents)
        deliverables, tasks, warnings = expand_workflow_deliverables(parents, context, previous)
        tasks, removed_links = retain_supported_dependencies(tasks)
        if removed_links + removed_parent_links:
            warnings.append(f'{removed_links + removed_parent_links} unconfirmed inferred predecessor links were removed from this proposal. The five-stage workflow and source/planner links are retained.')
        assumptions = ['Every deliverable uses IFR → Company Review → IFA → Company Approval → Final Issue.',
                       'The selected workflow defines stage names and internal links only. It supplies no duration or cross-package logic.']
        if reference_schedule_blocker(project.files.filter(is_deleted=False)):
            warnings.append('The uploaded reference schedule is not imported. This preview proposes a five-stage draft only; source timing, calendar and logic still require verification before approval.')
    else:
        tasks, removed_links = retain_supported_dependencies(previous)
        deliverables = deepcopy(state.get('deliverables') or [])
        warnings = [f'{removed_links} unsupported automatic links removed.'] if removed_links else []
        assumptions = []
    source_evidence = source_timing_evidence(tasks, context)
    constraints = source_evidence['source_constraints']
    tasks, duration_rows, duration_summary, duration_warnings = reconcile_source_durations(tasks, context)
    # Stored source-parent rows must not retain their old five-day seed after
    # child durations have been reviewed. Printed package totals remain source
    # facts, distinct from the calculated descendant span shown by the canvas.
    package_rows = []
    if deliverables:
        deliverables, package_rows, _, _ = reconcile_source_durations(deliverables, context)
    warnings.extend(duration_warnings)
    assumptions.append('Project dates remain unchanged. Only uniquely matched uploaded durations are copied; missing values remain unspecified. No template estimates, effort conversion or duration compression is used.')
    dated = _dated_tasks(project, tasks)
    existing_dated = {task['id']: task for task in _dated_tasks(project, state['tasks'])}
    changes = []
    fields = ('duration_days', 'planned_start_date', 'planned_finish_date', 'depends_on')
    for task in dated:
        before = {key: existing_dated.get(task['id'], {}).get(key) for key in fields}
        after = {key: task.get(key) for key in fields}
        if before != after or (workflow_mode and not existing_dated.get(task['id'], {}).get('parent_deliverable_id')):
            changes.append({'task_id': task['id'], 'title': task['title'], 'before': before, 'after': after,
                            'rationale': task.get('schedule_rationale', '')})
    beyond = [task for task in dated if task.get('planned_finish_date') and task['planned_finish_date'] > project.planned_end_date.isoformat()]
    if beyond:
        warnings.append(f'{len(beyond)} activities finish after the registered target because preserved constraints cannot fit. The project finish is unchanged; this timing warning does not prevent submission.')
    if not any(task.get('depends_on') for task in tasks) and len(tasks) > 1:
        warnings.append('No unambiguous technical gates were found. Activities remain parallel; review the dependency network.')
    proposal = {
        'revision': state['revision'], 'task_count': len(tasks), 'changed_count': len(changes),
        'relationship_count': sum(len(task.get('depends_on') or []) for task in tasks),
        'start_date': min((task['planned_start_date'] for task in dated if task.get('planned_start_date')), default=None),
        'finish_date': max((task['planned_finish_date'] for task in dated if task.get('planned_finish_date')), default=None),
        'horizon_start_date': project.effective_date.isoformat(), 'target_finish_date': project.planned_end_date.isoformat(),
        'assumptions': assumptions, 'warnings': warnings, 'source_constraints': constraints, 'changes': changes,
        'workflow_mode': workflow_mode, 'deliverable_count': len(deliverables),
        'sequence_summary': sequence_summary,
        'duration_review': {**duration_summary, 'total_count': len(tasks), 'rows': duration_rows,
                            'package_rows': package_rows},
        'source_timing_evidence': source_evidence,
        'evidence_policy': 'document_driven', 'duration_policy': 'source_only',
        'extraction_reports': source_evidence.get('extraction_reports') or [],
        'expansion_blockers': [issue for parent in deliverables for issue in parent.get('expansion_blockers') or []],
    }
    return tasks, deliverables, proposal, fingerprint, canonical_fingerprint({'tasks': tasks, 'deliverables': deliverables, 'proposal': proposal})


def propose_schedule(project, actor, *, revision, workflow_mode=None):
    project = PlanningProject.objects.get(pk=project.pk, is_deleted=False)
    state = _draft(project)
    workflow_mode = workflow_mode or 'source_only'
    _require_schedule_proposal(project, actor, state, revision, workflow_mode)
    tasks, deliverables, proposal, fingerprint, proposal_hash = _schedule_proposal(project, state, workflow_mode)
    proposal['token'] = signing.dumps({'project_id': project.pk, 'actor_id': actor.pk, 'revision': revision,
                                      'fingerprint': fingerprint, 'proposal_hash': proposal_hash, 'workflow_mode': workflow_mode},
                                     salt='planning.simple-schedule-proposal', compress=True)
    preview = deepcopy(state)
    preview.update(tasks=tasks, deliverables=deliverables, workflow_mode=workflow_mode, state='review', version_id=None, review_id=None,
                   schedule_proposal={key: value for key, value in proposal.items() if key not in {'token', 'changes'}})
    plan = plan_state(project, actor, state_override=preview)
    plan['is_schedule_preview'] = True
    plan['permissions'] = {key: False for key in plan['permissions']}
    return {'proposal': proposal, 'plan': plan}


@transaction.atomic
def apply_schedule_proposal(project, actor, *, revision, proposal_token):
    try:
        signed = signing.loads(proposal_token, salt='planning.simple-schedule-proposal', max_age=3600)
    except signing.BadSignature:
        _error('The schedule proposal expired or is invalid. Build a fresh preview.', 'simple_plan_proposal_invalid')
    if signed.get('project_id') != project.pk or signed.get('actor_id') != actor.pk or signed.get('revision') != revision:
        _error('This proposal does not belong to the current project, user and revision.', 'simple_plan_proposal_invalid')
    project = PlanningProject.objects.select_for_update().get(pk=project.pk, is_deleted=False)
    state = _draft(project)
    workflow_mode = signed.get('workflow_mode')
    token_hash = hashlib.sha256(proposal_token.encode()).hexdigest()
    applied = state.get('schedule_proposal') or {}
    if applied.get('token_hash') == token_hash and state['revision'] == revision + 1:
        _require_schedule_proposal(project, actor, state, state['revision'], workflow_mode)
        _, context_hash = proposal_context(project, state, _calendar_record(project))
        if context_hash != applied.get('context_hash'):
            _error('Schedule inputs changed after this proposal was applied. Build a fresh preview.', 'simple_plan_proposal_stale')
        return plan_state(project, actor)
    _require_schedule_proposal(project, actor, state, revision, workflow_mode)
    # Both flat and five-stage proposals preserve work already under way.
    # Lock before rehydrating so employee progress cannot race the signed preview.
    list(ProjectTask.objects.select_for_update().filter(
        project_id=project.enterprise_project_id,
        source_key__in=[source_key(project, task['id']) for task in state['tasks']],
    ).order_by('pk').values_list('pk', flat=True))
    tasks, deliverables, proposal, fingerprint, proposal_hash = _schedule_proposal(project, state, workflow_mode)
    if fingerprint != signed.get('fingerprint') or proposal_hash != signed.get('proposal_hash'):
        _error('Schedule inputs or workflow settings changed. Review a fresh proposal before applying.', 'simple_plan_proposal_stale')
    if proposal.get('expansion_blockers'):
        _error('Review existing work history before converting deliverables to workflow stages.',
               'workflow_expansion_blocked', blockers=proposal['expansion_blockers'])
    before = deepcopy(state)
    _, captured_context = proposal_context(project, state, _calendar_record(project))
    if _proposal_fingerprint(captured_context, state, workflow_mode) != fingerprint:
        _error('Schedule inputs changed while applying the proposal. Build a fresh preview.', 'simple_plan_proposal_stale')
    _schedule_due_dates(project, tasks)
    sync_workspace_assignments(project, tasks, actor=actor, token=state['assignment_token'],
                               intelligence_run_id=state.get('intelligence_run_id'),
                               managed_task_ids=set(state.get('managed_task_ids') or []) | {task['id'] for task in tasks})
    _cancel_review(state, actor)
    _, final_context = proposal_context(project, state, _calendar_record(project))
    if final_context != captured_context:
        _error('Schedule inputs changed while applying the proposal. Build a fresh preview.', 'simple_plan_proposal_stale')
    state.update(tasks=tasks, deliverables=deliverables, workflow_mode=workflow_mode,
                 state='review', revision=state['revision'] + 1, version_id=None, review_id=None,
                 schedule_proposal={**proposal, 'token_hash': token_hash, 'context_hash': captured_context,
                                    'applied_by': actor.pk, 'applied_at': timezone.now().isoformat()})
    _persist(project, state, actor, 'simple_plan.schedule_proposal_applied', before)
    return plan_state(project, actor)


def _locked(project, actor, revision, *, repeated_state=None):
    project = PlanningProject.objects.select_for_update().get(pk=project.pk, is_deleted=False)
    if project.master_schedule_version_id:
        _error('Open the preserved working draft before changing it.', 'master_schedule_accepted_inputs_read_only')
    if not can_write_project(actor, project) or not module_action_allowed(actor, 'planning_package', 'update'):
        _error('You do not have permission to edit this project plan.', 'simple_plan_forbidden', status_code=403)
    state = _draft(project)
    if int(revision) != state['revision'] and not (state['state'] == repeated_state and int(revision) + 1 == state['revision']):
        _error('The plan changed in another session. Refresh before saving.', 'simple_plan_revision_conflict', revision=state['revision'])
    return project, state


def _persist(project, state, actor, action, before=None):
    state['updated_at'] = timezone.now().isoformat()
    state['updated_by'] = actor.pk
    project.simple_planning_state = state
    # This is not a change to project scope or the legacy intelligence fingerprint.
    project.save(update_fields=['simple_planning_state'])
    record_event(project=project, actor=actor, action=action, entity=project, before=before or {}, after=state)


def _source_identity(task):
    from .identity_policy import occurrence_key

    identities = set()
    for reference in task.get('source_references') or []:
        locator = reference.get('locator') or {}
        if not reference.get('file_id') or not any(reference.get(key) for key in
                ('document_version', 'checksum_sha256', 'extracted_text_sha256')):
            return None
        if not any(locator.get(key) is not None for key in
                   ('line', 'row', 'cell', 'cell_range', 'character_start', 'paragraph')):
            return None
        identities.add(occurrence_key(reference, identifier=task.get('source_activity_id') or task.get('document_number')))
    return next(iter(identities)) if len(identities) == 1 else None


def _retain_saved_work(previous, proposed, *, additional_task_ids=()):
    """A new intelligence-run ID is not a new employee task or a lost edit."""
    from collections import Counter, defaultdict

    source_candidates, id_candidates = defaultdict(list), defaultdict(list)
    for task in previous:
        identity = _source_identity(task)
        if identity is not None:
            source_candidates[identity].append(task)
        id_candidates[task['id']].append(task)
    proposed_sources = Counter(_source_identity(task) for task in proposed if _source_identity(task) is not None)
    proposed_ids = Counter(task['id'] for task in proposed)
    protected = protected_work_ids(previous)
    remapped_ids = {}
    for task in proposed:
        task['source_title'] = task['title']
        identity = _source_identity(task)
        candidates = id_candidates.get(task['id'], []) if proposed_ids[task['id']] == 1 else []
        if candidates and (any(_source_identity(row) != identity for row in candidates)
                           or (identity is None and task.get('source_references'))):
            candidates = []  # Reusing an internal ID cannot cross source versions.
        if not candidates and identity is not None and proposed_sources[identity] == 1:
            candidates = source_candidates.get(identity, [])
        original = candidates[0] if len(candidates) == 1 else None
        if not original:
            if candidates or (identity is not None and proposed_sources[identity] > 1):
                task['identity_review'] = {
                    'status': 'requires_review', 'code': 'duplicate_source_identity',
                    'message': 'Saved work was not copied across ambiguous source records.',
                    'blocks': ['calculation', 'approval'],
                }
            continue
        remapped_ids[task['id']] = original['id']
        for key in ('id', 'owner', 'assignee_id', 'reviewer', 'reviewer_id', 'task_type', 'priority', 'due_date',
                    'due_date_source', 'effort_hours', 'acceptance_criteria'):
            if key in original:
                task[key] = deepcopy(original[key])
        source_derived = (original.get('evidence_policy') == 'document_driven'
                          or original.get('duration_source') in {'source_document', 'source_requirement',
                              'proposed', 'template', 'default', 'estimated', 'estimate', 'ai', 'workflow_template', 'missing_source'})
        keep_duration = (original['id'] in protected or original.get('duration_confirmed')
                         or original.get('duration_source') in {'planner', 'manual', 'user', 'confirmed'}
                         or not source_derived)
        keep_logic = original['id'] in protected or not source_derived or original.get('dependency_status') == 'planner'
        if keep_duration:
            fresh = deepcopy(task.get('duration_evidence') or {})
            different = original.get('duration_days') != task.get('duration_days')
            for key in ('duration_days', 'duration_source', 'duration_unit', 'duration_evidence',
                        'duration_review_status', 'duration_review_reason', 'duration_calendar_verified',
                        'duration_confirmed', 'duration_confirmed_at', 'planned_start_date',
                        'schedule_rationale', 'schedule_phase', 'schedule_generated_fields'):
                if key in original:
                    task[key] = deepcopy(original[key])
                else:
                    task.pop(key, None)
            if different and fresh:
                task.update(duration_comparison_evidence=fresh, duration_review_status='conflict',
                            duration_review_reason='Saved planner or started-work duration differs from the current source. Review both values.')
        if keep_logic:
            task['depends_on'] = deepcopy(original.get('depends_on') or [])
            for key in ('dependency_details', 'dependency_rationales', 'dependency_status'):
                task[key] = deepcopy(original.get(key) or ([] if key == 'dependency_details' else {} if key == 'dependency_rationales' else 'not_specified'))
        if original.get('source_title') and original['title'] != original['source_title']:
            task['title'] = original['title']
    ids = {task['id'] for task in proposed} | set(additional_task_ids)
    removed_dependencies = 0
    for task in proposed:
        task['depends_on'] = [remapped_ids.get(key, key) for key in task['depends_on']]
        valid = [key for key in task['depends_on'] if key in ids]
        removed = len(task['depends_on']) - len(valid)
        removed_dependencies += removed
        task['depends_on'] = valid
        task['dependency_details'] = [{**link, 'task_id': remapped_ids.get(link['task_id'], link['task_id'])}
                                      for link in task.get('dependency_details') or []
                                      if remapped_ids.get(link['task_id'], link['task_id']) in valid]
        task['dependency_rationales'] = {remapped_ids.get(key, key): value
            for key, value in (task.get('dependency_rationales') or {}).items()
            if remapped_ids.get(key, key) in valid}
        if removed:
            task['dependency_status'] = 'not_specified'
    return removed_dependencies


def _rebuild_workflow_rows(state, current_work, proposed):
    """Keep only workflows with an exact, unique source-version identity.

    Reuse existing stages, never expand templates during document analysis.
    Stage identities cannot be treated as parent identities: IFR may share its
    parent's ID and all stages may cite the same original register row.
    """
    from collections import Counter, defaultdict

    parents_by_source, children = defaultdict(list), defaultdict(list)
    for parent in state.get('deliverables') or []:
        identity = _source_identity(parent)
        if identity is not None:
            parents_by_source[identity].append(parent)
    for task in current_work:
        if task.get('parent_deliverable_id'):
            children[task['parent_deliverable_id']].append(task)
    source_counts = Counter(_source_identity(row) for row in proposed)
    groups, flat, source_order = [], [], []
    for row in proposed:
        identity = _source_identity(row)
        matches = parents_by_source.get(identity, []) if identity is not None and source_counts[identity] == 1 else []
        parent = matches[0] if len(matches) == 1 else None
        members = children.get(parent['id'], []) if parent else []
        expected = (parent.get('workflow_task_ids') or []) if parent else []
        if parent and len(set(expected)) == len(expected) == len(members) == 5 and set(expected) == {item['id'] for item in members}:
            by_id = {item['id']: item for item in members}
            groups.append((deepcopy(parent), [deepcopy(by_id[key]) for key in expected]))
            source_order.append(groups[-1][1])
        else:
            flat.append(row)
            source_order.append([row])

    old_stage_ids = {task['id'] for tasks in children.values() for task in tasks}
    remapped = {}
    for row in flat:
        if row['id'] in old_stage_ids:
            original_id = row['id']
            row['id'] = 'task-' + canonical_fingerprint({
                'rule': 'rebuild_source_parent/1', 'workspace': state['assignment_token'],
                'source': _source_identity(row), 'original_id': original_id,
                'references': row.get('source_references') or [],
            })[:32]
            remapped[original_id] = row['id']
    for row in flat:
        row['depends_on'] = [remapped.get(key, key) for key in row.get('depends_on') or []]
        row['dependency_details'] = [{**link, 'task_id': remapped.get(link['task_id'], link['task_id'])}
                                     for link in row.get('dependency_details') or []]
        row['dependency_rationales'] = {remapped.get(key, key): value
            for key, value in (row.get('dependency_rationales') or {}).items()}
    retained_tasks = [task for _, tasks in groups for task in tasks]
    removed_links = _retain_saved_work([task for task in current_work if not task.get('parent_deliverable_id')], flat,
                                       additional_task_ids={task['id'] for task in retained_tasks})
    tasks = [task for rows in source_order for task in rows]
    parents = [parent for parent, _ in groups]
    active_ids = {task['id'] for task in tasks}
    parent_ids = {parent['id'] for parent in parents}
    for rows, valid in ((tasks, active_ids), (parents, active_ids | parent_ids)):
        for row in rows:
            references = set(row.get('depends_on') or []) | {link['task_id'] for link in row.get('dependency_details') or []}
            removed = references - valid
            row['depends_on'] = [key for key in row.get('depends_on') or [] if key in valid]
            row['dependency_details'] = [link for link in row.get('dependency_details') or [] if link['task_id'] in valid]
            row['dependency_rationales'] = {key: value for key, value in (row.get('dependency_rationales') or {}).items() if key in valid}
            if removed:
                row['dependency_status'] = 'not_specified'
                row['rebuild_removed_dependencies'] = sorted(removed)
                removed_links += len(removed)
    for parent, members in groups:
        parent.pop('summary', None)
        for task in members:
            task['source_deliverable'] = deepcopy(parent)
    for task in tasks:
        task.update(evidence_policy='document_driven', duration_policy='source_only')
    previous_ids = {task['id'] for task in current_work}
    summary = {
        'preserved_workflow_deliverables': len(parents),
        'replaced_workflow_deliverables': len(state.get('deliverables') or []) - len(parents),
        'archived_tasks': len(previous_ids - active_ids), 'new_tasks': len(active_ids - previous_ids),
    }
    return tasks, parents, removed_links, summary


def _schedule_due_dates(project, tasks):
    dates = {task['id']: task.get('planned_finish_date') for task in _dated_tasks(project, tasks)}
    for task in tasks:
        if task.get('due_date_source') == 'schedule':
            task['due_date'] = dates.get(task['id'])


@transaction.atomic
def analyse_plan(project, actor, *, revision=0, rebuild=False):
    project, state = _locked(project, actor, revision)
    if state['state'] == 'baselined':
        _error('Create a revision before changing the published baseline.', 'simple_plan_baselined')
    if project.simple_planning_state and not rebuild:
        if state['input_fingerprint'] != _fingerprint(project):
            _error('The inputs changed. Confirm rebuilding the draft; saved work remains in the audit history.', 'simple_plan_rebuild_required')
        return plan_state(project, actor)
    before = deepcopy(state)
    captured_inputs = _fingerprint(project)
    files = list(project.files.filter(is_deleted=False).order_by('id'))
    if any(source.parse_status != 'done' for source in files):
        _error('Wait for document processing to finish before analysing.', 'simple_plan_documents_processing')
    if rebuild and not files and any(task.get('source_references') for task in state['tasks']):
        _error('No reference documents are available to rebuild this document draft. Upload the current source documents; the saved draft and assignments have been retained.',
               'source_activities_not_recovered', extraction_reports=[])
    # Reference documents can be extracted and reviewed even when the full
    # native calendar/network has not been imported. Publication stays distinct.
    # First import keeps assigned IDs and saved edits. Rebuild is an explicit
    # choice; it never edits the prior WBS record or an existing baseline.
    if files and (not state['tasks'] or rebuild):
        register = any(source.category in {'mdr', 'eddr'} for source in files)
        run, preview = run_document_intelligence(project, user=actor, files=files, allow_ai=not register)
        if _fingerprint(project) != captured_inputs:
            _error('The source documents changed during analysis. Analyse the current inputs again.', 'simple_plan_inputs_changed_during_analysis')
        selected = deepcopy(preview)
        if preview.get('deliverable_source') != 'register':
            for info in selected.get('disciplines', {}).values():
                names = info.get('mentioned_in_source') or []
                if info.get('ai_discovered'):
                    names = list(dict.fromkeys([*names, *info['ai_discovered']]))
                info['deliverables'] = names
                info['in_scope'] = bool(names)
            selected['hse_studies'] = list(run.facts.filter(fact_type='hse_study', is_deleted=False).values_list('value', flat=True))
        proposed = [_seed_task(task) for task in _initial_tasks(run, selected)]
        document_plan = project_document_plan(project, preview)
        if not register and document_plan['activities']:
            proposed = [_seed_task(task) for task in simple_tasks(document_plan)]
        if not proposed and state['tasks']:
            _error('No supported source activities were recovered. The saved draft has been retained; review extraction coverage before rebuilding.',
                   'source_activities_not_recovered', extraction_reports=document_plan['extraction_reports'])
        if rebuild and state['tasks']:
            old_sources = {ref['file_id'] for row in [*(state.get('deliverables') or []),
                *(task for task in state['tasks'] if not task.get('parent_deliverable_id'))]
                for ref in row.get('source_references') or [] if ref.get('file_id') is not None}
            recovered_sources = {ref['file_id'] for row in proposed for ref in row.get('source_references') or []
                                 if ref.get('file_id') is not None}
            missing_sources = (old_sources & {source.pk for source in files}) - recovered_sources
            if missing_sources:
                _error('No supported activities were recovered from some existing source documents. Review their extraction before rebuilding; the saved draft and assignments have been retained.',
                       'source_activities_not_recovered', source_file_ids=sorted(missing_sources),
                       extraction_reports=document_plan['extraction_reports'])
        # Employee progress is independently editable. Lock before reading the
        # work records, as for applying a reviewed schedule proposal.
        if rebuild:
            list(ProjectTask.objects.select_for_update().filter(project_id=project.enterprise_project_id,
                source_key__in=[source_key(project, task['id']) for task in state['tasks']]
            ).order_by('pk').values_list('pk', flat=True))
        current_work = hydrate_assignments(project, deepcopy(state['tasks']), state['assignment_token'])
        if rebuild and state.get('deliverables'):
            proposed, parents, removed_dependencies, summary = _rebuild_workflow_rows(state, current_work, proposed)
            state.update(deliverables=parents, workflow_mode=state.get('workflow_mode') if parents else None,
                         rebuild_summary=summary)
        else:
            removed_dependencies = _retain_saved_work(current_work, proposed)
            if rebuild:
                old_ids, new_ids = {task['id'] for task in current_work}, {task['id'] for task in proposed}
                state['rebuild_summary'] = {
                    'preserved_workflow_deliverables': 0, 'replaced_workflow_deliverables': 0,
                    'archived_tasks': len(old_ids - new_ids), 'new_tasks': len(new_ids - old_ids),
                }
        stale_evidence_rows = 0
        if rebuild:
            parents_by_id = {parent['id']: parent for parent in state.get('deliverables') or []}
            stale_evidence_rows = invalidate_stale_source_evidence([*proposed, *parents_by_id.values()], files)
            for task in proposed:
                parent = parents_by_id.get(task.get('parent_deliverable_id'))
                if parent:
                    task['source_deliverable'] = deepcopy(parent)
        previous_ids = set(state.get('managed_task_ids') or []) | {task['id'] for task in state['tasks']}
        state['tasks'] = proposed
        state['managed_task_ids'] = sorted(previous_ids | {task['id'] for task in proposed})
        state['disciplines'] = [{'code': code, 'name': info.get('name') or code.replace('_', ' ').title()}
                                for code, info in selected.get('disciplines', {}).items() if info.get('in_scope')]
        state['intelligence_run_id'] = run.pk
        state['processing_coverage'] = preview.get('processing_coverage') or {}
        state['extraction_reports'] = document_plan['extraction_reports']
        state['document_schedule_summary'] = document_schedule_summary(document_plan)
        state['evidence_policy'] = 'document_driven'
        state['duration_policy'] = 'source_only'
        state['method'] = 'ai_assisted' if preview.get('ai_augmented') else 'document_extraction'
        _schedule_due_dates(project, proposed)
        if rebuild:
            sync_workspace_assignments(
                project, proposed, actor=actor, token=state['assignment_token'], intelligence_run_id=run.pk,
                managed_task_ids=state['managed_task_ids'],
            )
        state['warnings'] = ([{'code': 'removed_dependencies', 'message': f'{removed_dependencies} dependencies referenced removed source rows. Review the revised sequence.'}]
                             if removed_dependencies else [])
        if stale_evidence_rows:
            state['warnings'].append({'code': 'stale_source_evidence',
                'message': f'{stale_evidence_rows} source rows used outdated or unverifiable timing or relationship evidence. Review the current documents; previous evidence is retained in history.'})
        if rebuild:
            for key in ('schedule_proposal', 'duration_review', 'assumptions', 'calculation_run_id'):
                state.pop(key, None)
    _cancel_review(state, actor)
    if _fingerprint(project) != captured_inputs:
        _error('The source documents changed during analysis. Analyse the current inputs again.', 'simple_plan_inputs_changed_during_analysis')
    state.update(state='review', revision=state['revision'] + 1, input_fingerprint=captured_inputs,
                 version_id=None, review_id=None)
    _persist(project, state, actor, 'simple_plan.analysed', before)
    return plan_state(project, actor)


def _cancel_review(state, actor):
    review = ScheduleReview.objects.select_for_update().filter(pk=state.get('review_id'), status='pending', is_deleted=False).first()
    if review:
        review.status, review.completed_at = 'cancelled', timezone.now()
        review.save(update_fields=['status', 'completed_at', 'updated_at'])
        record_event(project=review.version.schedule.project, actor=actor, action='simple_plan.review_cancelled', entity=review,
                     after={'reason': 'Draft edited after submission'})


@transaction.atomic
def save_plan(project, actor, data):
    project, state = _locked(project, actor, data['revision'])
    if state['state'] == 'baselined':
        _error('Create a revision before editing a published baseline.', 'simple_plan_baselined')
    before = deepcopy(state)
    known = {task['id']: task for task in state['tasks']}
    source_parents = {parent['id']: parent for parent in state.get('deliverables') or []}
    dated = {task['id']: task for task in _dated_tasks(project, state['tasks'])}
    tasks = deepcopy(data['tasks'])
    submitted_ids = {task['id'] for task in tasks}
    if any(task.get('parent_deliverable_id') and task['id'] not in submitted_ids for task in state['tasks']):
        _error('Keep all five stages of each deliverable. Individual workflow stages cannot be removed.', 'workflow_five_stages_required')
    normalize_assignment_fields(tasks, known)
    for task in tasks:
        original = known.get(task['id']) or {}
        if original.get('parent_deliverable_id'):
            parent = source_parents.get(original['parent_deliverable_id']) or original
            if task['discipline'] != parent['discipline']:
                _error('Workflow stages must keep their source deliverable discipline. Change the stage assignment without moving an individual stage to another discipline.',
                       'workflow_discipline_mismatch')
        for key in (*WORKFLOW_FIELDS, *SOURCE_FIELDS):
            if key in original:
                task[key] = deepcopy(original[key])
        if original.get('dependency_details'):
            details = original['dependency_details']
            task['dependency_details'] = [deepcopy(link) for key in task['depends_on']
                                          for link in ([row for row in details if row['task_id'] == key]
                                                       or [{'task_id': key, 'type': 'FS', 'lag_days': 0, 'source': 'planner', 'status': 'confirmed'}])]
        if set(task.get('depends_on') or []) != set(original.get('depends_on') or []):
            task['dependency_status'] = 'planner'
        if _milestone(task) and task.get('evidence_policy') != 'document_driven':
            task['duration_days'] = 0
        for key, default in [('source_references', []), ('document_number', ''), ('document_revision', ''), ('source_title', '')]:
            task[key] = deepcopy(original.get(key, default))
        for key, default in [('schedule_rationale', ''), ('schedule_phase', ''), ('schedule_generated_fields', []), ('dependency_rationales', {})]:
            if key in original:
                task[key] = deepcopy(original.get(key, default))
        task['duration_source'] = (original.get('duration_source', 'planner')
                                   if task.get('duration_days') == original.get('duration_days') else 'planner')
        if task.get('duration_days') == original.get('duration_days'):
            for key in ('duration_evidence', 'duration_comparison_evidence', 'duration_review_status', 'duration_review_reason', 'duration_calendar_verified'):
                if key in original:
                    task[key] = deepcopy(original[key])
        elif task.get('duration_days') is None:
            task['duration_source'] = 'missing_source'
        original_due = original.get('due_date')
        incoming_due = task.get('due_date')
        # Explicit dates from an existing project stay independent of schedule
        # estimates. New tasks follow the schedule unless a date is entered.
        if incoming_due and incoming_due != original_due:
            shown_due = dated.get(task['id'], {}).get('planned_finish_date')
            task['due_date_source'] = ('schedule' if original.get('due_date_source') == 'schedule' and incoming_due == shown_due else 'explicit')
        else:
            task['due_date_source'] = original.get('due_date_source') or ('explicit' if original_due else 'schedule')
        if task.get('planned_start_date') == dated.get(task['id'], {}).get('planned_start_date'):
            task['planned_start_date'] = original.get('planned_start_date')
        if 'schedule_generated_fields' in task:
            task['schedule_generated_fields'] = [field for field in task['schedule_generated_fields'] if task.get(field) == original.get(field)]
            task['dependency_rationales'] = {key: value for key, value in task.get('dependency_rationales', {}).items() if key in task['depends_on']}
    task_by_id = {task['id']: task for task in tasks}
    for parent in state.get('deliverables') or []:
        chain = parent.get('workflow_task_ids') or []
        if len(chain) != 5 or any(key not in task_by_id for key in chain):
            _error('Each deliverable must retain its five workflow stages.', 'workflow_five_stages_required')
        for previous, current in zip(chain, chain[1:]):
            if previous not in task_by_id[current]['depends_on']:
                _error('Keep the predecessor connection between consecutive workflow stages.', 'workflow_sequence_required')
    _schedule_due_dates(project, tasks)
    sync_workspace_assignments(
        project, tasks, actor=actor, token=state['assignment_token'], intelligence_run_id=state.get('intelligence_run_id'),
        managed_task_ids=set(state.get('managed_task_ids') or []) | set(known) | {task['id'] for task in tasks},
    )
    _cancel_review(state, actor)
    state.update(tasks=tasks, disciplines=deepcopy(data.get('disciplines', state['disciplines'])),
                 state='review', revision=state['revision'] + 1, version_id=None, review_id=None, warnings=[],
                 managed_task_ids=sorted(set(state.get('managed_task_ids') or []) | set(known) | {task['id'] for task in tasks}))
    _persist(project, state, actor, 'simple_plan.saved', before)
    return plan_state(project, actor)


@transaction.atomic
def submit_plan(project, actor, *, revision, approver_id=None):
    project, state = _locked(project, actor, revision, repeated_state='submitted')
    if state['state'] == 'submitted':
        return plan_state(project, actor)
    if state['state'] != 'review':
        _error('Review the draft before submitting it.', 'simple_plan_state')
    blockers = _blockers(project, state)
    if blockers:
        _error('Complete the plan before submitting.', 'simple_plan_incomplete', blockers=blockers)
    timing_blockers = [row for row in schedule_timing_blockers(
        _dated_tasks(project, state['tasks']), project.planned_end_date, _calendar(project),
    ) if row.get('severity') != 'warning']
    if timing_blockers:
        _error('Resolve the schedule checks before submitting.', 'simple_plan_schedule_blocked', blockers=timing_blockers)
    approvers = list(proposal_approver_users(project))
    approver = next((user for user in approvers if user.pk == approver_id), None) if approver_id else next(iter(approvers), None)
    if not approver:
        _error('Assign an eligible project manager with approval access to the project.', 'simple_plan_approver_required')
    before = deepcopy(state)
    draft = {**state, 'schedule_version_id': None}
    version = materialize_work_breakdown(project, draft, actor=actor, start=project.effective_date,
                                       token=state['assignment_token'])
    source_tasks = {task['id']: task for task in state['tasks']}
    activities = list(version.activities.filter(is_deleted=False))
    for activity in activities:
        activity.metadata = {**activity.metadata,
                             'planning_workflow': 'simple_planning',
                             'duration_source': source_tasks[activity.external_id].get('duration_source', 'planner'),
                             'due_date_source': source_tasks[activity.external_id].get('due_date_source', 'explicit'),
                             **{key: source_tasks[activity.external_id][key] for key in
                                ('schedule_rationale', 'schedule_phase', 'schedule_generated_fields', 'dependency_rationales',
                                 'duration_evidence', 'duration_comparison_evidence', 'duration_review_status', 'duration_review_reason', 'duration_calendar_verified')
                                if key in source_tasks[activity.external_id]}}
    type(activities[0]).objects.bulk_update(activities, ['metadata'])
    calculation = calculate_schedule_version(version, requested_by=actor)
    version.refresh_from_db()
    assurance = run_schedule_assurance(version, requested_by=actor)
    if assurance.blockers:
        _error('Resolve the schedule checks before submitting.', 'simple_plan_schedule_blocked', blockers=assurance.blockers)
    review = ScheduleReview.objects.create(version=version, title=f'{project.name} — Plan approval'[:255],
                                           requested_by=actor, requested_at=timezone.now())
    ScheduleReviewDecision.objects.create(review=review, reviewer=approver)
    state.update(state='submitted', revision=state['revision'] + 1, version_id=version.pk,
                 schedule_id=version.schedule_id, review_id=review.pk,
                 calculation_run_id=calculation.pk, warnings=assurance.warnings)
    _persist(project, state, actor, 'simple_plan.submitted', before)
    return plan_state(project, actor)


@transaction.atomic
def approve_publish_plan(project, actor, *, revision, name=''):
    project = PlanningProject.objects.select_for_update().get(pk=project.pk, is_deleted=False)
    if project.master_schedule_version_id:
        _error('The current schedule changed. Refresh before approving.', 'master_schedule_revision_conflict')
    state = _draft(project)
    version = ScheduleVersion.objects.select_for_update().filter(pk=state.get('version_id'), schedule__project=project, is_deleted=False).first()
    if not version:
        _error('Submit the plan before publishing a baseline.', 'simple_plan_not_submitted')
    require_schedule_authority(version, actor)
    if state['state'] == 'baselined':
        return plan_state(project, actor)
    if int(revision) != state['revision']:
        _error('The plan changed. Refresh before approving.', 'simple_plan_revision_conflict')
    if state['state'] != 'submitted' or not current_schedule_version(version):
        _error('Only the current submitted schedule can be approved.', 'simple_plan_not_current')
    blockers = _blockers(project, state)
    if blockers:
        _error('The submitted plan no longer matches its inputs.', 'simple_plan_incomplete', blockers=blockers)
    baseline_name = (name or f'{project.name} — Baseline v{version.version}')[:255]
    if ScheduleBaseline.objects.filter(schedule=version.schedule, name=baseline_name).exists():
        _error('A baseline with this name already exists. Choose another name.', 'simple_plan_baseline_name_exists')
    review = ScheduleReview.objects.select_for_update().get(pk=state['review_id'], version=version, is_deleted=False)
    already_approved = review.status == 'approved' and version.status == 'approved'
    if not already_approved and not can_decide_schedule_review(review, actor):
        _error('Only an eligible assigned approver may publish this plan; all other reviewers must respond first.',
               'simple_plan_approval_forbidden', status_code=403)
    assurance = current_assurance(version)
    if not assurance or assurance.blockers:
        _error('Recalculate and review this schedule before approval.', 'simple_plan_assurance_required')
    if not already_approved:
        if assurance.status != 'approved':
            assurance = approve_schedule_assurance(version, actor)
        decide_schedule_review(version, review.pk, actor, decision='approved')
    version.refresh_from_db()
    if not can_baseline_schedule(version, actor):
        _error('Complete all schedule reviews and resolve critical findings before publishing.', 'simple_plan_baseline_blocked')
    from ..schedule_serializers import (
        ActivityRelationshipSerializer, ScheduleActivitySerializer, ScheduleAssuranceReviewSerializer,
        ScheduleVersionSerializer, ScheduleWBSNodeSerializer,
    )
    from .planning_boundaries import freeze_schedule_inputs
    from .planning_registers import risk_snapshot
    baseline = ScheduleBaseline.objects.create(
        schedule=version.schedule, source_version=version,
        name=baseline_name, data_date=version.schedule.data_date,
        approved_by=actor, approved_at=timezone.now(), snapshot={
            'version': ScheduleVersionSerializer(version).data,
            'wbs': ScheduleWBSNodeSerializer(version.wbs_nodes.filter(is_deleted=False), many=True).data,
            'activities': ScheduleActivitySerializer(version.activities.filter(is_deleted=False), many=True).data,
            'relationships': ActivityRelationshipSerializer(version.relationships.filter(is_deleted=False), many=True).data,
            'schedule_assurance': ScheduleAssuranceReviewSerializer(assurance).data,
            'accepted_inputs': freeze_schedule_inputs(version),
            'risk_register': risk_snapshot(version),
        },
    )
    version.status = 'baselined'
    version.save(update_fields=['status', 'updated_at'])
    before = deepcopy(state)
    state.update(state='baselined', revision=state['revision'] + 1, baseline_id=baseline.pk)
    _persist(project, state, actor, 'simple_plan.baselined', before)
    record_event(project=project, actor=actor, action='schedule.baselined', entity=baseline,
                 after={'version': version.version}, metadata={'source': 'simple_planning'})
    return plan_state(project, actor)


@transaction.atomic
def reopen_plan(project, actor, *, revision):
    project, state = _locked(project, actor, revision)
    if state['state'] != 'baselined':
        _error('Only a published baseline needs a new revision.', 'simple_plan_state')
    if state.get('legacy_version_import'):
        _error('This existing baseline retains advanced activity and relationship fields. Create its revision in Schedule Controls to preserve them.',
               'simple_plan_legacy_revision_required')
    before = deepcopy(state)
    state.update(state='review', revision=state['revision'] + 1,
                 previous_baseline_id=state.get('baseline_id'), baseline_id=None,
                 version_id=None, review_id=None, input_fingerprint=_fingerprint(project))
    _persist(project, state, actor, 'simple_plan.reopened', before)
    return plan_state(project, actor)
