"""Persist explicit parallel-work assumptions without modifying the schedule."""
from copy import deepcopy

from django.db import transaction
from django.utils import timezone

from ..models import PlanningProject, ScheduleLogicReview, ScheduleVersion
from .audit import record_event
from .schedule_approval import ScheduleApprovalError
from .schedule_logic_quality import analyze_schedule_logic


def _error(message, code='schedule_logic_review_invalid', status_code=409):
    raise ScheduleApprovalError(message, code=code, status_code=status_code)


def _review_data(review):
    actor = review.reviewed_by
    return {'id': review.pk, 'reviewed_by_id': review.reviewed_by_id,
            'reviewed_by': (actor.get_full_name() or actor.email or actor.username) if actor else 'Former user',
            'reviewed_at': review.created_at.isoformat(), 'rationale': review.rationale,
            'capacity_basis': review.capacity_basis, 'duration_basis': review.duration_basis,
            'max_parallel_deliverables': review.max_parallel_deliverables}


def logic_quality(project, tasks, deliverables=None, version=None):
    """A GET is pure apart from reading matching, scoped review records."""
    result = analyze_schedule_logic(tasks, deliverables)
    reviews = {}
    for review in ScheduleLogicReview.objects.filter(project=project, version=version,
            fingerprint=result['fingerprint']).select_related('reviewed_by'):
        reviews.setdefault(review.group_id, review)
    blockers, warnings = [], []
    for group in result['groups']:
        review = reviews.get(group['id'])
        if group['requires_review'] and review and review.max_parallel_deliverables >= group['deliverable_count']:
            group.update(status='reviewed', requires_review=False, review=_review_data(review))
        task_ids = group.get('first_task_ids') or group['task_ids']
        finding = {'code': 'parallel_workflow_review_required' if group['requires_review'] else group['kind'],
                   'severity': 'critical' if group['requires_review'] else 'warning',
                   'field': 'depends_on', 'task_id': task_ids[0] if task_ids else None,
                   'task_ids': task_ids, 'group_id': group['id'], 'message': group['message'],
                   'resolution': 'Review the actual release gates, duration basis and available capacity in Schedule logic review.'}
        if group['requires_review']:
            blockers.append(finding)
        elif group['kind'] != 'parallel_workflow':
            warnings.append(finding)
    result.update(blockers=blockers, warnings=warnings)
    result['summary'].update(unreviewed_group_count=len(blockers), requires_review_count=len(blockers),
                             reviewed_group_count=sum(group['status'] == 'reviewed' for group in result['groups']))
    return result


def _version_inputs(version):
    from .simple_planning import _version_tasks
    from .planning_boundaries import calculation_inputs_current
    from .planner_timing import expose_planner_timing
    from .source_date_read_model import enrich_source_dates
    tasks = _version_tasks(version)
    calculated = bool(version.calculated_at and calculation_inputs_current(version))
    for task in tasks:
        task['calculated'] = calculated
        if not calculated:
            task['planned_start_date'] = task['planned_finish_date'] = None
    expose_planner_timing(tasks)
    parents = {}
    for task in tasks:
        parent = task.get('source_deliverable')
        if task.get('parent_deliverable_id') and parent:
            parents.setdefault(str(task['parent_deliverable_id']), deepcopy(parent))
    enrich_source_dates({'tasks': tasks, 'deliverables': list(parents.values())})
    return tasks, list(parents.values())


def version_logic_quality(version):
    tasks, deliverables = _version_inputs(version)
    return logic_quality(version.schedule.project, tasks, deliverables, version=version)


@transaction.atomic
def confirm_parallel_logic(project, actor, data):
    from .gantt_editing import can_edit_gantt
    from .master_schedule import _locked as lock_master, master_plan_state
    from .simple_planning import _locked as lock_draft, _persist
    project = PlanningProject.objects.select_for_update().get(pk=project.pk, is_deleted=False)
    if data.get('viewing_history') or data.get('version_id'):
        _error('Return to the current editable schedule before reviewing parallel work.', 'schedule_logic_read_only')
    if project.master_schedule_version_id:
        project, version = lock_master(project, actor, data['revision'], 'confirm-parallel-logic')
        draft = None
    else:
        project, draft = lock_draft(project, actor, data['revision'])
        version = None
    if not can_edit_gantt(project, actor, version):
        _error('Create an editable correction draft before reviewing an approved or submitted schedule.', 'schedule_logic_read_only')
    current = master_plan_state(project, actor)
    analysis = analyze_schedule_logic(current['tasks'], current.get('deliverables'))
    if data.get('fingerprint') != analysis['fingerprint']:
        _error('The schedule changed. Refresh the logic review before recording a decision.', 'schedule_logic_review_stale')
    group = next((item for item in analysis['groups'] if item['id'] == data.get('group_id')
                  and item['kind'] == 'parallel_workflow'), None)
    if not group:
        _error('Select a current parallel-work group.', 'schedule_logic_group_missing')
    fields = {}
    for field in ('rationale', 'capacity_basis', 'duration_basis'):
        value = data.get(field)
        if not isinstance(value, str) or not 20 <= len(value.strip()) <= 5000:
            _error(f'Provide 20 to 5,000 characters for {field.replace("_", " ")}.', status_code=400)
        fields[field] = value.strip()
    maximum = data.get('max_parallel_deliverables')
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < group['deliverable_count'] or maximum > 100000:
        _error('The stated capacity must cover every deliverable in this parallel group. Correct the sequence if it does not.', status_code=400)
    review = ScheduleLogicReview.objects.create(project=project, version=version,
        fingerprint=analysis['fingerprint'], group_id=group['id'], reviewed_by=actor,
        max_parallel_deliverables=maximum, **fields)
    if version:
        # An assumption changes assurance, not CPM inputs or calculated dates.
        version.assurance_reviews.filter(status__in=['draft', 'ready', 'approved']).update(status='superseded')
        version.updated_at = timezone.now()
        version.save(update_fields=['updated_at'])
    else:
        before = deepcopy(draft)
        draft['revision'] += 1
        _persist(project, draft, actor, 'schedule.logic_review_recorded', before)
    record_event(project=project, actor=actor, action='schedule.parallel_work_reviewed', entity=review,
                 after={'version_id': version.pk if version else None, 'fingerprint': review.fingerprint,
                        'group_id': review.group_id, **_review_data(review)},
                 metadata={'schedule_unchanged': True, 'assurance_invalidated': bool(version)})
    return master_plan_state(project, actor)


@transaction.atomic
def carry_logic_reviews(project, source_tasks, source_deliverables, target_version):
    """Carry a draft review only when materialization preserves its exact inputs.

    Review records retain their original reviewer and text; the audit identifies
    the original review. A mismatch leaves the new version requiring review.
    """
    project = PlanningProject.objects.select_for_update().get(pk=project.pk, is_deleted=False)
    target_version = ScheduleVersion.objects.select_for_update().select_related('schedule').get(pk=target_version.pk)
    if target_version.schedule.project_id != project.pk:
        _error('The target schedule belongs to another project.', status_code=403)
    if target_version.status not in {'draft', 'calculated'} or target_version.baselines.filter(is_deleted=False).exists():
        _error('Logic reviews can be carried only into an editable schedule revision.', 'schedule_logic_read_only')
    source = analyze_schedule_logic(source_tasks, source_deliverables)
    tasks, deliverables = _version_inputs(target_version)
    target = analyze_schedule_logic(tasks, deliverables)
    if source['fingerprint'] != target['fingerprint']:
        from .simple_planning import _calendar_record
        calendar = _calendar_record(project)
        # Working drafts use the project default without a per-task FK.
        # Materialization records that same FK. Permit only this identity
        # assignment; all dates, units, constraints and typed links still have
        # to match the exact snapshot the planner reviewed.
        if not calendar or calendar.pk != target_version.schedule.default_calendar_id:
            return 0
        materialized_source = deepcopy(source_tasks)
        for task in materialized_source:
            if not task.get('calendar_id') and not (task.get('metadata') or {}).get('calendar_id'):
                task['calendar_id'] = calendar.pk
        if analyze_schedule_logic(materialized_source, source_deliverables)['fingerprint'] != target['fingerprint']:
            return 0
    groups = {item['id']: item for item in target['groups'] if item['requires_review']}
    carried, seen = 0, set()
    for previous in ScheduleLogicReview.objects.filter(project=project, version=None,
            fingerprint=source['fingerprint']).select_related('reviewed_by'):
        group = groups.get(previous.group_id)
        if not group or previous.group_id in seen or previous.max_parallel_deliverables < group['deliverable_count']:
            continue
        seen.add(previous.group_id)
        if ScheduleLogicReview.objects.filter(project=project, version=target_version,
                fingerprint=target['fingerprint'], group_id=previous.group_id).exists():
            continue
        review = ScheduleLogicReview.objects.create(project=project, version=target_version,
            fingerprint=target['fingerprint'], group_id=previous.group_id, reviewed_by=previous.reviewed_by,
            rationale=previous.rationale, capacity_basis=previous.capacity_basis, duration_basis=previous.duration_basis,
            max_parallel_deliverables=previous.max_parallel_deliverables)
        record_event(project=project, actor=previous.reviewed_by, action='schedule.logic_review_carried', entity=review,
            after={'source_review_id': previous.pk, 'source_reviewed_at': previous.created_at.isoformat(),
                   'version_id': target_version.pk, 'fingerprint': target['fingerprint'], 'group_id': previous.group_id},
            metadata={'schedule_unchanged': True, 'original_reviewer_preserved': True})
        carried += 1
    return carried
