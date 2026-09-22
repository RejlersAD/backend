"""Atomic planner changes to a working draft or selected schedule revision.

The first edit of a selected version creates a planner revision. Its parent,
source assertions, approved baselines and the independent simple draft remain
unchanged. Subsequent cell edits update that same revision, invalidate CPM, and
retain a server-owned input fingerprint for deterministic recalculation.
"""
from copy import deepcopy
from decimal import Decimal
import json

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

from ..models import PlanningProject, ScheduleVersion, ActivityRelationship
from .audit import record_event
from .operational_jobs import canonical_fingerprint, schedule_state_fingerprint
from .planner_timing import apply_timing_edit, expose_planner_timing, validate_planner_network
from .schedule_approval import ScheduleApprovalError, current_schedule_version

SCHEMA = 'planner-schedule-revision/1'


def _error(message, code='gantt_edit_invalid', status_code=409):
    raise ScheduleApprovalError(message, code=code, status_code=status_code)


def _safe(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def _input_fingerprint(version):
    return canonical_fingerprint({
        'activities': list(version.activities.filter(is_deleted=False).order_by('external_id').values(
            'external_id', 'name', 'activity_type', 'duration_days', 'calendar_id', 'constraint_type', 'constraint_date', 'metadata')),
        'relationships': list(version.relationships.filter(is_deleted=False).order_by(
            'predecessor__external_id', 'successor__external_id', 'relationship_type').values(
                'predecessor__external_id', 'successor__external_id', 'relationship_type', 'lag_days', 'metadata')),
    })


def _wbs_fingerprint(version):
    return canonical_fingerprint(list(version.wbs_nodes.filter(is_deleted=False).order_by('code').values(
        'code', 'name', 'parent_id', 'level', 'sort_order', 'discipline')))


def planner_inputs_current(version, snapshot):
    # Existing activity-only revisions predate the optional WBS fingerprint.
    return (_input_fingerprint(version) == snapshot.get('input_fingerprint')
            and ('wbs_input_fingerprint' not in snapshot
                 or _wbs_fingerprint(version) == snapshot['wbs_input_fingerprint']))


def can_edit_gantt(project, actor, version=None):
    from .master_schedule import _write
    if not _write(project, actor):
        return False
    if version is None:
        from .simple_planning import _draft
        state = _draft(project)
        return state.get('state') not in {'baselined', 'submitted'} and not state.get('legacy_version_import')
    return (project.master_schedule_version_id == version.pk and current_schedule_version(version)
            and version.status in {'draft', 'calculated'}
            and not version.baselines.filter(is_deleted=False, approved_at__isnull=False).exists()
            and not version.governance_reviews.filter(is_deleted=False, status='pending').exists())


def _apply_task_patch(task, patch, actor, project):
    original = deepcopy(task)
    if 'duration_days' in patch:
        duration = patch['duration_days']
        milestone = task.get('is_milestone') or task.get('activity_type') in {'start_milestone', 'finish_milestone'}
        if milestone and duration != 0:
            _error('Milestones have zero duration. Change the activity type before entering a working duration.')
        if duration is not None and (not Decimal(str(duration)).is_finite() or duration < 0 or duration > 36525
                                      or (not milestone and duration == 0)):
            _error('Activities need a positive duration of at most 36,525 working days; milestones use zero.')
        task['duration_days'] = float(duration) if duration is not None else None
    if 'timing_edit' in patch:
        task['timing_edit'] = _safe(patch['timing_edit'])
    if 'dependency_details' in patch:
        task['dependency_details'] = [{**row, 'lag_days': float(row['lag_days'])} for row in patch['dependency_details']]
        task['depends_on'] = list(dict.fromkeys(row['task_id'] for row in task['dependency_details']))
    return original


def _duration_metadata(task, original):
    if task.get('duration_days') == original.get('duration_days'):
        return
    task.update(duration_source='planner' if task.get('duration_days') is not None else 'missing_source',
                duration_review_status='planner', duration_review_reason='Edited in the Gantt table')
    # A numeric correction does not verify the original unit or work calendar.
    task['duration_calendar_verified'] = original.get('duration_calendar_verified', False)
    task['source_missing_fields'] = [key for key in original.get('source_missing_fields', []) if key != 'duration']
    if task.get('duration_days') is None:
        task['source_missing_fields'].append('duration')


@transaction.atomic
def edit_gantt(project, actor, data):
    from .master_schedule import _clone, _locked, master_plan_state
    from .simple_planning import _draft, _version_tasks, save_plan
    project = PlanningProject.objects.select_for_update().get(pk=project.pk, is_deleted=False)
    patches = data.get('updates') or [{key: value for key, value in data.items() if key != 'revision'}]
    if not project.master_schedule_version_id:
        if not can_edit_gantt(project, actor):
            _error('Your role or the current schedule state does not permit editing.', 'gantt_read_only', 403)
        state = _draft(project)
        tasks = deepcopy(state['tasks'])
        by_id = {row['id']: row for row in tasks}
        for patch in patches:
            if patch['task_id'] not in by_id:
                _error('Choose an activity from the current project schedule.', 'gantt_activity_missing', 404)
            _apply_task_patch(by_id[patch['task_id']], patch, actor, project)
        try:
            validate_planner_network(tasks)
        except ValueError as exc:
            _error(str(exc))
        return save_plan(project, actor, {'revision': data['revision'], 'tasks': tasks,
                                          'disciplines': state['disciplines']})

    project, version = _locked(project, actor, data['revision'], 'edit-activity')
    if not can_edit_gantt(project, actor, version):
        _error('Create an editable revision before changing an approved or submitted schedule.', 'gantt_read_only')
    prior_snapshot = version.evidence_input_snapshot or {}
    if prior_snapshot.get('schema') == SCHEMA and not planner_inputs_current(version, prior_snapshot):
        _error('The planner revision changed outside the Gantt editor. Reconcile the changed inputs before editing.',
               'planner_revision_inputs_changed')
    tasks = _version_tasks(version)
    by_id = {row['id']: row for row in tasks}
    originals = {}
    for patch in patches:
        task = by_id.get(patch['task_id'])
        if task is None:
            _error('Choose an activity from the current project schedule.', 'gantt_activity_missing', 404)
        originals[task['id']] = _apply_task_patch(task, patch, actor, project)
        try:
            apply_timing_edit(task, originals[task['id']], actor, origin=project.effective_date)
        except (ValueError, KeyError, TypeError) as exc:
            _error(str(exc))
        _duration_metadata(task, originals[task['id']])
    try:
        validate_planner_network(tasks)
    except ValueError as exc:
        _error(str(exc))

    previous_version_id = version.pk
    snapshot = deepcopy(version.evidence_input_snapshot or {})
    if snapshot.get('schema') != SCHEMA:
        parent = version
        parent_fingerprint = schedule_state_fingerprint(parent)
        version = _clone(parent, actor)
        snapshot = {'schema': SCHEMA, 'parent_version_id': parent.pk,
                    'parent_input_fingerprint': parent_fingerprint,
                    'source_snapshot': deepcopy(parent.evidence_input_snapshot), 'edits': {}}
        version.change_summary = 'Planner corrections from the Gantt table'
        project.master_schedule_version = version
    rows = {row.external_id: row for row in version.activities.filter(is_deleted=False)}
    before = {}
    for patch in patches:
        key = patch['task_id']
        row, task, original = rows[key], by_id[key], originals[key]
        before[key] = {field: original.get(field) for field in ('duration_days', 'constraint_type', 'constraint_date', 'dependency_details')}
        metadata = deepcopy(row.metadata or {})
        if 'duration_days' in patch:
            row.duration_days = Decimal(str(task['duration_days'] or 0))
            metadata['duration_pending'] = task['duration_days'] is None
            for field in ('duration_source', 'duration_review_status', 'duration_review_reason',
                          'duration_calendar_verified', 'source_missing_fields'):
                if field in task:
                    metadata[field] = deepcopy(task[field])
        if 'timing_edit' in patch:
            row.constraint_type, row.constraint_date = task['constraint_type'], task['constraint_date']
            for field in ('planner_timing', 'date_authority', 'planned_start_date_source', 'planned_finish_date_source'):
                if field in task:
                    metadata[field] = deepcopy(task[field])
                else:
                    metadata.pop(field, None)
        if 'dependency_details' in patch:
            metadata['dependency_status'] = 'planner'
            existing = list(version.relationships.filter(successor=row))
            wanted = {(item['task_id'], item['type']): item for item in task['dependency_details']}
            retained = set()
            for link in existing:
                identity = (link.predecessor.external_id, link.relationship_type)
                desired = wanted.get(identity)
                if desired is not None:
                    if link.is_deleted or float(link.lag_days) != desired['lag_days']:
                        link.lag_days, link.is_deleted, link.deleted_at = desired['lag_days'], False, None
                        link.metadata = {**(link.metadata or {}), 'source': 'planner', 'status': 'confirmed'}
                        link.save(update_fields=['lag_days', 'is_deleted', 'deleted_at', 'metadata', 'updated_at'])
                    retained.add(identity)
                elif not link.is_deleted:
                    link.is_deleted, link.deleted_at = True, timezone.now()
                    link.save(update_fields=['is_deleted', 'deleted_at', 'updated_at'])
            ActivityRelationship.objects.bulk_create([ActivityRelationship(version=version,
                predecessor=rows[pred], successor=row, relationship_type=kind, lag_days=wanted[(pred, kind)]['lag_days'],
                metadata={'source': 'planner', 'status': 'confirmed'}) for pred, kind in wanted.keys() - retained])
        row.metadata = _safe(metadata)
        row.save(update_fields=['duration_days', 'constraint_type', 'constraint_date', 'metadata', 'updated_at'])
        snapshot['edits'][key] = {**snapshot['edits'].get(key, {}), **_safe(patch),
                                  'edited_by': str(actor.pk), 'edited_at': timezone.now().isoformat()}
    # Clear every previously calculated value; descendants may have moved too.
    version.activities.filter(is_deleted=False).update(planned_start=None, planned_finish=None,
        early_start=None, early_finish=None, late_start=None, late_finish=None,
        total_float_days=None, free_float_days=None, is_critical=False)
    snapshot['input_fingerprint'] = _input_fingerprint(version)
    version.evidence_input_snapshot = snapshot
    version.status, version.calculated_at, version.calculated_finish = 'draft', None, None
    version.save(update_fields=['evidence_input_snapshot', 'status', 'calculated_at', 'calculated_finish', 'change_summary', 'updated_at'])
    project.master_schedule_revision += 1
    project.save(update_fields=['master_schedule_version', 'master_schedule_revision', 'updated_at'])
    record_event(project=project, actor=actor, action='schedule.gantt_edited', entity=version,
                 before={'version_id': previous_version_id, 'activities': before},
                 after={'version_id': version.pk, 'updates': _safe(patches)},
                 metadata={'source_preserved': True, 'calculation_invalidated': True})
    return master_plan_state(project, actor)


def planner_revision_readiness(version):
    from .planning_boundaries import accepted_input_validation, _issue, _whole_number, BOUNDARY_RULE_VERSION
    snapshot = version.evidence_input_snapshot or {}
    issues = []
    parent = version.parent_version
    seen = {version.pk}
    # Reopening a published planner baseline retains the original source
    # manifest. Its source must still be an ancestor in this same schedule.
    while parent and parent.pk != snapshot.get('parent_version_id'):
        if (parent.pk in seen or len(seen) >= 1000 or parent.is_deleted or parent.schedule_id != version.schedule_id
                or (parent.evidence_input_snapshot or {}).get('schema') != SCHEMA):
            parent = None
            break
        seen.add(parent.pk)
        parent = parent.parent_version
    parent_readiness = {}
    if (not parent or parent.is_deleted or parent.pk != snapshot.get('parent_version_id') or parent.schedule_id != version.schedule_id
            or (parent.evidence_input_snapshot or {}).get('schema') == SCHEMA):
        issues.append(_issue('planner_revision_parent_invalid', 'The original schedule revision is unavailable.'))
    else:
        if schedule_state_fingerprint(parent) != snapshot.get('parent_input_fingerprint'):
            issues.append(_issue('planner_revision_source_changed', 'The original schedule inputs changed. Review the current source before calculating.'))
        parent_readiness = accepted_input_validation(parent)
        issues.extend(deepcopy(parent_readiness['issues']))
    if not planner_inputs_current(version, snapshot):
        issues.append(_issue('planner_revision_inputs_changed', 'Planner inputs changed outside the reviewed Gantt edit. Reload and reconcile this revision.'))
    for activity in version.activities.filter(is_deleted=False):
        if (activity.metadata or {}).get('duration_pending'):
            issues.append(_issue('duration_not_specified', 'Enter an activity duration.', entity_id=activity.external_id, field='duration'))
        if not _whole_number(activity.duration_days):
            issues.append(_issue('duration_resolution_unsupported', 'CPM requires whole working-day durations.', entity_id=activity.external_id, field='duration'))
    for link in version.relationships.filter(is_deleted=False):
        if not _whole_number(link.lag_days):
            issues.append(_issue('lag_resolution_unsupported', 'CPM requires whole working-day leads and lags.', field='lag'))
    legacy = parent_readiness.get('policy') == 'legacy_explicit_schedule'
    if not legacy:
        issues.append({'code': 'planner_revision_review_required', 'message': 'Review planner corrections before approving a baseline.',
                       'severity': 'warning', 'blocks': ['approval', 'export']})
    return {'policy': 'planner_revision', 'rule_version': BOUNDARY_RULE_VERSION,
            'ready_for_calculation': not any('calculation' in row.get('blocks', []) for row in issues),
            'ready_for_approval': legacy and not any('approval' in row.get('blocks', []) for row in issues),
            'ready_for_export': legacy and not any('export' in row.get('blocks', []) for row in issues), 'issues': issues}


def enrich_gantt_state(version, state):
    snapshot = version.evidence_input_snapshot or {}
    if snapshot.get('schema') != SCHEMA:
        return state
    state['planner_revision'] = {'parent_version_id': snapshot['parent_version_id'],
                                  'edited_activity_count': len(snapshot['edits'])}
    state['read_only_reason'] = ''
    state['assumptions'] = ['Planner corrections preserve the original source version. Calculate the edited network before relying on dates or float.']
    for task in state['tasks']:
        if task.get('duration_source') == 'planner':
            task['duration_review_status'] = 'planner'
    expose_planner_timing(state['tasks'])
    return state
