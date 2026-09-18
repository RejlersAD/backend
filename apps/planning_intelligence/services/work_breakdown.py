"""Planner-owned WBS drafts tied to an exact confirmed intelligence preview.

Drafts live alongside, not inside, the extracted evidence in a run's summary.
Reading never creates records. Advancing materializes a separate editable
schedule version and does not calculate, approve, or publish a baseline.
"""
from copy import deepcopy
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from django.db.models import Max
from django.utils import timezone

from ..config import DISCIPLINE_NAME_BY_CODE
from ..models import (
    ActivityAssignment, ActivityRelationship, Schedule, ScheduleActivity,
    ScheduleResource, ScheduleVersion, ScheduleWBSNode,
)
from .audit import record_event
from .preview_confirmation import current_confirmed_preview
from .schedule_basis import _as_date, _deliverable_rows


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
        tasks.append({
            'id': f'task-{uuid5(NAMESPACE_URL, key).hex}',
            'discipline': row['discipline'] or 'general',
            'title': row['original_title'] or row['canonical_name'],
            'owner': '', 'effort_hours': None, 'depends_on': [],
            'acceptance_criteria': '', 'reviewer': '',
            'source_references': deepcopy(row['references']),
            'document_number': row['document_number'], 'document_revision': row['document_revision'],
        })
    return tasks


def work_breakdown_state(run):
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
    return {
        **draft, 'intelligence_run_id': run.pk, 'preview_confirmed_at': token,
        'disciplines': [{'code': code, 'name': DISCIPLINE_NAME_BY_CODE.get(code, code.replace('_', ' ').title())}
                        for code in codes],
        'source_documents': [{
            'id': source.pk, 'name': source.original_filename, 'category': source.category, 'status': 'reviewed',
        } for source in run.project.files.filter(is_deleted=False, pk__in=run.source_file_ids).order_by('id')],
    }


def save_work_breakdown(run, data, *, actor):
    """Caller holds project and run row locks in one atomic transaction."""
    current = work_breakdown_state(run)
    if data['preview_confirmed_at'] != current['preview_confirmed_at']:
        raise WorkBreakdownConflict('The confirmed preview has changed. Reopen work breakdown before saving.')
    if data['revision'] != current['revision']:
        raise WorkBreakdownConflict(
            'This work breakdown was updated by another session. Refresh it before saving.',
            code='work_breakdown_revision_conflict',
        )
    known = {task['id']: task for task in current['tasks']}
    tasks = deepcopy(data['tasks'])
    for task in tasks:
        # Provenance belongs to server-held evidence, never client-supplied text.
        original = known.get(task['id']) or {}
        task['source_references'] = deepcopy(original.get('source_references') or [])
        task['document_number'] = original.get('document_number', '')
        task['document_revision'] = original.get('document_revision', '')
    changed = tasks != current['tasks']
    draft = {
        'tasks': tasks,
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
        metadata={'preview_confirmed_at': current['preview_confirmed_at'], 'advanced': data['advance']},
    )
    return work_breakdown_state(run)


def _materialize(run, draft, *, actor):
    existing = ScheduleVersion.objects.filter(
        pk=draft.get('schedule_version_id'), is_deleted=False, schedule__is_deleted=False,
        schedule__project=run.project, status__in=['draft', 'calculated'],
    ).first()
    if existing:
        return existing
    preview = run.summary['preview_confirmation']['preview']
    start = _as_date(preview.get('detected_effective_date_text')) or run.project.effective_date
    if not start:
        raise WorkBreakdownConflict('Set and confirm the project start date before continuing to schedule.', 'work_breakdown_start_required')
    schedule, _ = Schedule.objects.get_or_create(
        project=run.project, code='MASTER',
        defaults={'name': f'{run.project.name} Master Schedule'[:255], 'planned_start': start, 'created_by': actor},
    )
    if schedule.is_deleted:
        raise WorkBreakdownConflict('The master schedule is archived. Restore it before continuing.', 'work_breakdown_schedule_archived')
    parent = schedule.versions.filter(is_deleted=False).order_by('-version').first()
    version = ScheduleVersion.objects.create(
        schedule=schedule, version=(schedule.versions.aggregate(value=Max('version'))['value'] or 0) + 1,
        parent_version=parent, created_by=actor,
        change_summary=f'Work breakdown draft {draft["revision"]} from confirmed intelligence {run.pk}',
    )
    nodes = {}
    activities = {}
    resources = {}
    for index, task in enumerate(draft['tasks']):
        discipline = task['discipline']
        if discipline not in nodes:
            nodes[discipline] = ScheduleWBSNode.objects.create(
                version=version, code=f'{len(nodes) + 1}.0',
                name=DISCIPLINE_NAME_BY_CODE.get(discipline, discipline.replace('_', ' ').title())[:255],
                discipline=discipline, sort_order=len(nodes),
            )
        activity = ScheduleActivity.objects.create(
            version=version, wbs_node=nodes[discipline], external_id=task['id'],
            name=task['title'], discipline=discipline, responsible_role=task['owner'],
            duration_days=0, activity_type='task', calendar=schedule.default_calendar,
            sort_order=index, metadata={
                'source': 'confirmed_work_breakdown', 'work_breakdown_revision': draft['revision'],
                'intelligence_run_id': run.pk,
                'preview_confirmation_at': run.summary['preview_confirmation']['confirmed_at'],
                'duration_pending': True, 'planned_effort_hours': task['effort_hours'],
                'acceptance_criteria': task['acceptance_criteria'], 'reviewer': task['reviewer'],
                'source_references': task['source_references'],
                'document_number': task['document_number'], 'document_revision': task['document_revision'],
            },
        )
        activities[task['id']] = activity
        if task['owner'] and task['effort_hours'] is not None:
            resource = resources.get(task['owner'])
            if resource is None:
                code = f'WBS-{uuid5(NAMESPACE_URL, task["owner"]).hex}'
                resource, _ = ScheduleResource.objects.get_or_create(
                    project=run.project, code=code,
                    defaults={'name': task['owner'], 'role': task['owner'], 'resource_type': 'labor'},
                )
                resources[task['owner']] = resource
            hours = Decimal(str(task['effort_hours']))
            ActivityAssignment.objects.create(
                activity=activity, resource=resource, planned_units=hours, budgeted_hours=hours,
                budgeted_cost=hours * resource.unit_cost,
            )
    ActivityRelationship.objects.bulk_create([
        ActivityRelationship(
            version=version, predecessor=activities[predecessor], successor=activities[task['id']],
            relationship_type='FS', metadata={'source': 'confirmed_work_breakdown'},
        ) for task in draft['tasks'] for predecessor in task['depends_on']
    ])
    record_event(
        project=run.project, actor=actor, action='work_breakdown.schedule_created', entity=version,
        after={'schedule_id': schedule.pk, 'schedule_version_id': version.pk, 'task_count': len(draft['tasks'])},
    )
    return version
