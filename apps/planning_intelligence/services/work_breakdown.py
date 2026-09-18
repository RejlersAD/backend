"""Planner-owned WBS drafts tied to an exact confirmed intelligence preview.

Drafts live alongside, not inside, the extracted evidence in a run's summary.
Reading never creates records. Advancing materializes a separate editable
schedule version and does not calculate, approve, or publish a baseline.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from uuid import NAMESPACE_URL, uuid5

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from ..config import DISCIPLINE_NAME_BY_CODE
from ..models import (
    ActivityAssignment, ActivityRelationship, Schedule, ScheduleActivity,
    ScheduleResource, ScheduleVersion, ScheduleWBSNode, WorkCalendar,
)
from .audit import record_event
from .preview_confirmation import current_confirmed_preview
from .schedule_basis import _as_date, _deliverable_rows
from .work_assignments import (
    hydrate_assignments, normalize_assignment_fields, sync_assignments, sync_workspace_assignments,
)


class WorkBreakdownConflict(ValueError):
    def __init__(self, message, code='work_breakdown_preview_changed'):
        super().__init__(message)
        self.code = code


def require_current_preview(run):
    preview = current_confirmed_preview(run) if run else None
    if preview is None:
        raise WorkBreakdownConflict(
            'Review, confirm and save the current Document Intelligence Preview before editing work breakdown.',
        )
    return preview


def _initial_tasks(run, preview):
    tasks = []
    for row in _deliverable_rows(run, preview):
        if row.get('excluded') or not row.get('confirmed'):
            continue
        key = f"{run.pk}:{row['discipline']}:{row['canonical_name']}:{row['document_number']}"
        if row.get('source_identity'):
            key += f":{row['source_identity']}"
        tasks.append({
            'id': f'task-{uuid5(NAMESPACE_URL, key).hex}',
            'discipline': row['discipline'] or 'general',
            'title': row['original_title'] or row['canonical_name'],
            'task_type': 'deliverable',
            'owner': '', 'effort_hours': None, 'depends_on': [],
            'acceptance_criteria': '', 'reviewer': '',
            'source_references': deepcopy(row['references']),
            'document_number': row['document_number'], 'document_revision': row['document_revision'],
        })
    return tasks


def work_breakdown_state(run, *, actor=None):
    from apps.core.task_assignment_policy import manages_project_tasks
    if run is not None and run.project.planning_mode != 'document':
        raise WorkBreakdownConflict(
            'This project uses direct planning. Open its direct work breakdown to preserve assigned work.',
            'work_breakdown_mode_required',
        )
    preview = require_current_preview(run)
    token = run.summary['preview_confirmation']['confirmed_at']
    saved = (run.summary.get('work_breakdown_drafts') or {}).get(token)
    draft = deepcopy(saved) if saved else {
        'revision': 0, 'saved_at': None, 'saved_by': None,
        'tasks': _initial_tasks(run, preview),
        'schedule_id': None, 'schedule_version_id': None,
    }
    codes = list(dict.fromkeys([
        *DISCIPLINE_NAME_BY_CODE, *(task['discipline'] for task in draft['tasks']),
    ]))
    source_disciplines = ((run.summary or {}).get('base_intelligence') or {}).get('disciplines') or {}
    return {
        **draft, 'planning_mode': 'document', 'intelligence_run_id': run.pk, 'preview_confirmed_at': token,
        'tasks': hydrate_assignments(run.project, draft['tasks'], token),
        'permissions': {'can_assign': manages_project_tasks(actor, run.project.enterprise_project)},
        'disciplines': [{'code': code, 'name': source_disciplines.get(code, {}).get('name')
                        or DISCIPLINE_NAME_BY_CODE.get(code, code.replace('_', ' ').title())}
                        for code in codes],
        'source_documents': [{
            'id': source.pk, 'name': source.original_filename, 'category': source.category, 'status': 'reviewed',
        } for source in run.project.files.filter(is_deleted=False, pk__in=run.source_file_ids).order_by('id')],
    }


def save_work_breakdown(run, data, *, actor):
    """Caller holds project and run row locks in one atomic transaction."""
    current = work_breakdown_state(run, actor=actor)
    if data['preview_confirmed_at'] != current['preview_confirmed_at']:
        raise WorkBreakdownConflict('The confirmed preview has changed. Reopen work breakdown before saving.')
    if data['revision'] != current['revision']:
        raise WorkBreakdownConflict(
            'This work breakdown was updated by another session. Refresh it before saving.',
            code='work_breakdown_revision_conflict',
        )
    known = {task['id']: task for task in current['tasks']}
    tasks = deepcopy(data['tasks'])
    normalize_assignment_fields(tasks, known)
    for task in tasks:
        # Provenance belongs to server-held evidence, never client-supplied text.
        original = known.get(task['id']) or {}
        task['source_references'] = deepcopy(original.get('source_references') or [])
        task['document_number'] = original.get('document_number', '')
        task['document_revision'] = original.get('document_revision', '')
    sync_assignments(run, tasks, actor=actor)
    previous_tasks = deepcopy(current['tasks'])
    normalize_assignment_fields(previous_tasks, known)
    changed = tasks != previous_tasks
    draft = {
        'tasks': tasks,
        'disciplines': current['disciplines'],
        'revision': current['revision'] + int(changed or not current['saved_at']),
        'saved_at': timezone.now().isoformat(), 'saved_by': actor.pk,
        'schedule_id': None if changed else current.get('schedule_id'),
        'schedule_version_id': None if changed else current.get('schedule_version_id'),
    }
    if data['advance']:
        if not tasks:
            raise WorkBreakdownConflict('Add at least one task before continuing to schedule.', 'work_breakdown_empty')
        version = _materialize(run, draft, actor=actor)
        draft['schedule_id'] = version.schedule_id
        draft['schedule_version_id'] = version.pk
    drafts = {**(run.summary.get('work_breakdown_drafts') or {}), current['preview_confirmed_at']: draft}
    run.summary = {**run.summary, 'work_breakdown_drafts': drafts}
    run.save(update_fields=['summary', 'updated_at'])
    record_event(
        project=run.project, actor=actor, action='work_breakdown.saved', entity=run,
        before={'revision': current['revision'], 'tasks': current['tasks']}, after=draft,
        metadata={'preview_confirmed_at': current['preview_confirmed_at'], 'advanced': data['advance'], 'task_audit_version': 1},
    )
    return work_breakdown_state(run, actor=actor)


def _materialize(run, draft, *, actor):
    preview = run.summary['preview_confirmation']['preview']
    start = _as_date(preview.get('detected_effective_date_text')) or run.project.effective_date
    return materialize_work_breakdown(
        run.project, draft, actor=actor, start=start,
        token=run.summary['preview_confirmation']['confirmed_at'], intelligence_run_id=run.pk,
    )


def _workflow_relationships(tasks):
    """Validate typed links before creating any expanded schedule records."""
    ids = {task['id'] for task in tasks}
    if len(ids) != len(tasks):
        raise WorkBreakdownConflict('Expanded activities need unique IDs.', 'workflow_activity_ids_invalid')
    result = []
    for task in tasks:
        details = task.get('dependency_details') or []
        predecessors = list(dict.fromkeys([*(task.get('depends_on') or []), *(row.get('task_id') for row in details)]))
        for predecessor in predecessors:
            if predecessor not in ids or predecessor == task['id']:
                raise WorkBreakdownConflict('Workflow dependencies must connect distinct activities in this plan.', 'workflow_dependencies_invalid')
            rows = [row for row in details if row.get('task_id') == predecessor] or [{'type': 'FS', 'lag_days': 0}]
            seen = set()
            for detail in rows:
                kind = str(detail.get('type') or 'FS').upper()
                try:
                    lag = Decimal(str(detail.get('lag_days', 0)))
                except (InvalidOperation, TypeError, ValueError):
                    raise WorkBreakdownConflict('Workflow relationship lag must be numeric.', 'workflow_dependencies_invalid') from None
                if kind not in {'FS', 'SS', 'FF', 'SF'} or not lag.is_finite() or abs(lag) > 365:
                    raise WorkBreakdownConflict('Workflow relationship type or lag is invalid.', 'workflow_dependencies_invalid')
                if kind in seen:
                    raise WorkBreakdownConflict('Workflow relationships must not be duplicated.', 'workflow_dependencies_invalid')
                seen.add(kind)
                rationale = (task.get('dependency_rationales') or {}).get(predecessor) or {}
                metadata = deepcopy(rationale) if isinstance(rationale, dict) else {'rationale': str(rationale)}
                metadata.update({key: deepcopy(value) for key, value in detail.items() if key not in {'task_id', 'type', 'lag_days'}})
                result.append((predecessor, task['id'], kind, lag, metadata))
    return result


@transaction.atomic
def materialize_work_breakdown(project, draft, *, actor, start, token, intelligence_run_id=None):
    existing = ScheduleVersion.objects.filter(
        pk=draft.get('schedule_version_id'), is_deleted=False, schedule__is_deleted=False,
        schedule__project=project, status__in=['draft', 'calculated'],
    ).first()
    if existing:
        return existing
    if not start:
        raise WorkBreakdownConflict('Set and confirm the project start date before continuing to schedule.', 'work_breakdown_start_required')
    source_parents = {row['id']: deepcopy(row) for row in draft.get('deliverables') or []}
    expanded = bool(source_parents)
    parent_tasks = {key: [] for key in source_parents}
    typed_links = _workflow_relationships(draft['tasks']) if expanded else []
    if expanded:
        if len(source_parents) != len(draft['deliverables']):
            raise WorkBreakdownConflict('Source deliverables need unique IDs.', 'workflow_parent_ids_invalid')
        for task in draft['tasks']:
            parent_id = task.get('parent_deliverable_id')
            if parent_id is not None:
                if parent_id not in source_parents:
                    raise WorkBreakdownConflict('A workflow activity has no source deliverable.', 'workflow_parent_missing')
                parent_tasks[parent_id].append(task)
            activity_type = task.get('activity_type') or 'task'
            if activity_type not in {'task', 'level_of_effort', 'start_milestone', 'finish_milestone'}:
                raise WorkBreakdownConflict('Workflow activity type is invalid.', 'workflow_activity_type_invalid')
            try:
                duration = Decimal(str(task.get('duration_days')))
            except (InvalidOperation, TypeError, ValueError):
                raise WorkBreakdownConflict('Set a valid duration for each workflow activity.', 'workflow_duration_invalid') from None
            milestone = activity_type in {'start_milestone', 'finish_milestone'}
            if not duration.is_finite() or duration < 0 or (milestone and duration != 0) or (not milestone and duration == 0):
                raise WorkBreakdownConflict('Milestones require zero duration; other workflow activities require a positive duration.', 'workflow_duration_invalid')
        if any(not rows for rows in parent_tasks.values()):
            raise WorkBreakdownConflict('Every source deliverable must retain its workflow activities.', 'workflow_parent_missing_tasks')
    schedule, _ = Schedule.objects.get_or_create(
        project=project, code='MASTER',
        defaults={'name': f'{project.name} Master Schedule'[:255], 'planned_start': start, 'created_by': actor},
    )
    if schedule.is_deleted:
        raise WorkBreakdownConflict('The master schedule is archived. Restore it before continuing.', 'work_breakdown_schedule_archived')
    if intelligence_run_id is None and schedule.planned_start != start:
        schedule.planned_start = start
        schedule.save(update_fields=['planned_start', 'updated_at'])
    if intelligence_run_id is None and schedule.default_calendar_id is None:
        calendar = project.work_calendars.filter(is_deleted=False, is_default=True).first()
        if calendar is None:
            calendar, _ = WorkCalendar.objects.get_or_create(
                project=project, name='Project working calendar',
                defaults={'working_weekdays': [0, 1, 2, 3, 4], 'hours_per_day': 8, 'is_default': True},
            )
            if calendar.is_deleted:
                raise WorkBreakdownConflict('Restore the project working calendar before continuing.', 'work_breakdown_calendar_archived')
        schedule.default_calendar = calendar
        schedule.save(update_fields=['default_calendar', 'updated_at'])
    parent = schedule.versions.filter(is_deleted=False).order_by('-version').first()
    version = ScheduleVersion.objects.create(
        schedule=schedule, version=(schedule.versions.aggregate(value=Max('version'))['value'] or 0) + 1,
        parent_version=parent, created_by=actor,
        change_summary=(f'Work breakdown draft {draft["revision"]} from confirmed intelligence {intelligence_run_id}'
                        if intelligence_run_id is not None else f'Direct work breakdown draft {draft["revision"]}'),
    )
    source = 'confirmed_work_breakdown' if intelligence_run_id is not None else 'manual_work_breakdown'
    discipline_names = {row['code']: row['name'] for row in draft.get('disciplines', [])}
    nodes = {}
    deliverable_nodes = {}
    parent_counts = {}
    activities = {}
    resources = {}
    for index, task in enumerate(draft['tasks']):
        discipline = task['discipline']
        if discipline not in nodes:
            nodes[discipline] = ScheduleWBSNode.objects.create(
                version=version, code=f'{len(nodes) + 1}.0',
                name=discipline_names.get(discipline, DISCIPLINE_NAME_BY_CODE.get(discipline, discipline.replace('_', ' ').title()))[:255],
                discipline=discipline, sort_order=len(nodes),
            )
        node = nodes[discipline]
        source_parent = source_parents.get(task.get('parent_deliverable_id'))
        if source_parent is not None:
            parent_id = source_parent['id']
            if parent_id not in deliverable_nodes:
                if source_parent.get('discipline') and source_parent['discipline'] != discipline:
                    raise WorkBreakdownConflict('Workflow stages must retain their source deliverable discipline.', 'workflow_discipline_mismatch')
                parent_counts[discipline] = parent_counts.get(discipline, 0) + 1
                deliverable_node = ScheduleWBSNode.objects.create(
                    version=version, parent=node, code=f'{node.code.rsplit(".", 1)[0]}.{parent_counts[discipline]}',
                    name=source_parent['title'][:255], discipline=discipline,
                    level=node.level + 1, sort_order=index,
                )
                deliverable_nodes[parent_id] = deliverable_node
                source_parent.update(
                    wbs_node_id=deliverable_node.pk, parent_wbs_node_id=node.pk, wbs_code=deliverable_node.code,
                    workflow_task_ids=[row['id'] for row in sorted(parent_tasks[parent_id], key=lambda row: row.get('workflow_stage_sequence', 0))],
                )
            node = deliverable_nodes[parent_id]
            if node.discipline != discipline:
                raise WorkBreakdownConflict('Workflow stages must retain their source deliverable discipline.', 'workflow_discipline_mismatch')
        expansion_metadata = {
            key: deepcopy(value) for key, value in task.items()
            if key.startswith('workflow_') or key in {
                'parent_deliverable_id', 'deliverable', 'source_title', 'source_parent_values',
                'responsible_role', 'duration_source', 'due_date_source', 'schedule_rationale',
                'schedule_phase', 'schedule_generated_fields', 'dependency_rationales',
                'activity_type', 'is_milestone',
            }
        } if expanded else {}
        if expanded:
            expansion_metadata['owner'] = task['owner']
        if source_parent is not None:
            expansion_metadata['source_deliverable'] = deepcopy(source_parent)
        activity = ScheduleActivity.objects.create(
            version=version, wbs_node=node, external_id=task['id'],
            name=task['title'], discipline=discipline,
            responsible_role=(task.get('responsible_role') or '') if source_parent is not None else task['owner'],
            duration_days=task.get('duration_days') or 0,
            activity_type=(task.get('activity_type') or 'task') if expanded else 'task', calendar=schedule.default_calendar,
            constraint_type='start_no_earlier' if task.get('planned_start_date') else 'none',
            constraint_date=task.get('planned_start_date') or None,
            sort_order=index, metadata={
                'source': source, 'work_breakdown_revision': draft['revision'],
                'intelligence_run_id': intelligence_run_id,
                'preview_confirmation_at': token if intelligence_run_id is not None else None,
                'duration_pending': task.get('duration_days') is None, 'planned_effort_hours': task['effort_hours'],
                'acceptance_criteria': task['acceptance_criteria'], 'reviewer': task['reviewer'],
                'assignee_id': task.get('assignee_id'), 'reviewer_id': task.get('reviewer_id'),
                'task_type': task.get('task_type', 'task'), 'due_date': task.get('due_date'),
                'priority': task.get('priority', 'medium'),
                'source_references': task['source_references'],
                'document_number': task['document_number'], 'document_revision': task['document_revision'],
                **expansion_metadata,
            },
        )
        activities[task['id']] = activity
        if task['owner'] and task['effort_hours'] is not None:
            resource = resources.get(task['owner'])
            if resource is None:
                code = f'WBS-{uuid5(NAMESPACE_URL, task["owner"]).hex}'
                resource, _ = ScheduleResource.objects.get_or_create(
                    project=project, code=code,
                    defaults={'name': task['owner'], 'role': task['owner'], 'resource_type': 'labor'},
                )
                resources[task['owner']] = resource
            hours = Decimal(str(task['effort_hours']))
            ActivityAssignment.objects.create(
                activity=activity, resource=resource, planned_units=hours, budgeted_hours=hours,
                budgeted_cost=hours * resource.unit_cost,
            )
    if expanded:
        ActivityRelationship.objects.bulk_create([
            ActivityRelationship(
                version=version, predecessor=activities[predecessor], successor=activities[successor],
                relationship_type=kind, lag_days=lag, metadata={'source': source, **metadata},
            ) for predecessor, successor, kind, lag, metadata in typed_links
        ], batch_size=500)
    else:
        ActivityRelationship.objects.bulk_create([
            ActivityRelationship(
                version=version, predecessor=activities[predecessor], successor=activities[task['id']],
                relationship_type='FS', metadata={'source': source},
            ) for task in draft['tasks'] for predecessor in task['depends_on']
        ])
    record_event(
        project=project, actor=actor, action='work_breakdown.schedule_created', entity=version,
        after={'schedule_id': schedule.pk, 'schedule_version_id': version.pk, 'task_count': len(draft['tasks'])},
    )
    return version


MANUAL_WORKSTREAMS = [
    {'code': 'general', 'name': 'General'},
    {'code': 'project_management', 'name': 'Project management'},
    {'code': 'development', 'name': 'Development'},
    {'code': 'testing', 'name': 'Testing'},
    {'code': 'operations', 'name': 'Operations'},
    {'code': 'launch', 'name': 'Launch'},
]


def manual_work_breakdown_state(project, *, actor=None):
    """Direct plans contain planner inputs, never claimed document findings."""
    from apps.core.task_assignment_policy import manages_project_tasks

    if project.planning_mode != 'manual':
        raise WorkBreakdownConflict(
            'Select direct planning in Scope & inputs before creating work without documents.',
            'work_breakdown_mode_required',
        )
    draft = deepcopy(project.manual_work_breakdown) or {
        'revision': 0, 'saved_at': None, 'saved_by': None, 'tasks': [],
        'schedule_id': None, 'schedule_version_id': None,
    }
    disciplines = deepcopy(draft.get('disciplines', MANUAL_WORKSTREAMS))
    existing_codes = {row['code'] for row in disciplines}
    for task in draft['tasks']:
        if task['discipline'] not in existing_codes:
            disciplines.append({'code': task['discipline'], 'name': task['discipline'].replace('_', ' ').title()})
            existing_codes.add(task['discipline'])
    token = f'manual:{project.pk}'
    return {
        **draft, 'planning_mode': 'manual', 'intelligence_run_id': None,
        'preview_confirmed_at': token, 'disciplines': disciplines,
        'tasks': hydrate_assignments(project, draft['tasks'], token),
        'permissions': {'can_assign': manages_project_tasks(actor, project.enterprise_project)},
        'source_documents': [{
            'id': source.pk, 'name': source.original_filename,
            'category': source.category, 'status': 'reference',
        } for source in project.files.filter(is_deleted=False).order_by('id')],
    }


def save_manual_work_breakdown(project, data, *, actor):
    """Caller holds the workspace row lock in an atomic transaction."""
    current = manual_work_breakdown_state(project, actor=actor)
    if data['revision'] != current['revision']:
        raise WorkBreakdownConflict(
            'This work breakdown was updated by another session. Refresh it before saving.',
            'work_breakdown_revision_conflict',
        )
    if data.get('advance'):
        missing = [name for name in ('scope_summary', 'phase', 'effective_date', 'planned_end_date')
                   if not getattr(project, name)]
        if missing:
            raise WorkBreakdownConflict(
                'Complete the project scope, phase, start date and end date before continuing to schedule.',
                'work_breakdown_inputs_required',
            )
        if project.planned_end_date <= project.effective_date:
            raise WorkBreakdownConflict('Project end date must be after its start date.', 'work_breakdown_dates_invalid')
        if not data['tasks']:
            raise WorkBreakdownConflict('Add at least one task before continuing to schedule.', 'work_breakdown_empty')
    known = {task['id']: task for task in current['tasks']}
    tasks = deepcopy(data['tasks'])
    normalize_assignment_fields(tasks, known)
    for task in tasks:
        task.update(source_references=[], document_number='', document_revision='')
    sync_workspace_assignments(project, tasks, actor=actor, token=current['preview_confirmed_at'])
    previous_tasks = deepcopy(current['tasks'])
    normalize_assignment_fields(previous_tasks, known)
    disciplines = deepcopy(data.get('disciplines', current['disciplines']))
    scope_inputs = {
        'scope_summary': project.scope_summary, 'phase': project.phase,
        'effective_date': project.effective_date.isoformat() if project.effective_date else None,
        'planned_end_date': project.planned_end_date.isoformat() if project.planned_end_date else None,
    }
    changed = (tasks != previous_tasks or disciplines != current['disciplines']
               or scope_inputs != current.get('scope_inputs'))
    draft = {
        'tasks': tasks, 'disciplines': disciplines, 'scope_inputs': scope_inputs,
        'revision': current['revision'] + int(changed or not current['saved_at']),
        'saved_at': timezone.now().isoformat(), 'saved_by': actor.pk,
        'schedule_id': None if changed else current.get('schedule_id'),
        'schedule_version_id': None if changed else current.get('schedule_version_id'),
    }
    if data.get('advance'):
        version = materialize_work_breakdown(
            project, draft, actor=actor, start=project.effective_date,
            token=current['preview_confirmed_at'],
        )
        draft.update(schedule_id=version.schedule_id, schedule_version_id=version.pk)
    project.manual_work_breakdown = draft
    project.save(update_fields=['manual_work_breakdown', 'updated_at'])
    record_event(
        project=project, actor=actor, action='work_breakdown.saved', entity=project,
        before={'revision': current['revision'], 'tasks': current['tasks']}, after=draft,
        metadata={'planning_mode': 'manual', 'advanced': data.get('advance', False), 'task_audit_version': 1},
    )
    return manual_work_breakdown_state(project, actor=actor)
