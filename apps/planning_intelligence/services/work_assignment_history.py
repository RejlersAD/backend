"""Project-only employee work history, reconstructed from append-only evidence."""
from copy import deepcopy

from django.utils import timezone
from rest_framework.exceptions import NotFound

from apps.core.project_models import ProjectTask
from apps.core.task_assignment_policy import employee_payload, user_payload
from apps.hr_core.models import EmployeeMaster
from apps.users.models import User

from ..models import PlanningAuditEvent
from .audit import record_event


TASK_EVENT_PREFIX = 'work_breakdown.assignment_'


def task_snapshot(task):
    metadata = task.metadata or {}
    return {
        'project_task_id': task.pk, 'wbs_task_id': metadata.get('wbs_task_id'),
        'title': task.title, 'discipline': metadata.get('discipline', ''),
        'task_type': task.task_type, 'status': task.status,
        'progress_percent': task.progress_percent,
        'assignee_id': task.assigned_to_id, 'reviewer_id': task.reviewer_id,
        'assignee_name': metadata.get('assignee_name', ''),
        'reviewer_name': metadata.get('reviewer_name', ''),
        'due_date': task.due_date.isoformat() if hasattr(task.due_date, 'isoformat') else task.due_date,
        'priority': task.priority, 'is_deleted': task.is_deleted,
    }


def record_task_event(*, workspace, actor, task, action, before=None):
    return record_event(
        project=workspace, actor=actor, action=f'{TASK_EVENT_PREFIX}{action}', entity=task,
        before=before, after=task_snapshot(task),
        metadata={'task_audit_version': 1, 'actor_name': actor.get_full_name() or actor.email},
    )


def _role(snapshot, user_id):
    if not snapshot or snapshot.get('is_deleted'):
        return None
    if snapshot.get('assignee_id') == user_id:
        return 'assignee'
    # A planned reviewer on a row without an assignee has no actionable task.
    if snapshot.get('assignee_id') and snapshot.get('reviewer_id') == user_id:
        return 'reviewer'
    return None


def _action(before, after):
    if (before.get('assignee_id') and not before.get('is_deleted')
            and (not after.get('assignee_id') or after.get('is_deleted'))):
        return 'unassigned'
    if after.get('assignee_id') and (not before.get('assignee_id') or before.get('is_deleted')):
        return 'assigned'
    if any(before.get(key) != after.get(key) for key in ('assignee_id', 'reviewer_id')):
        return 'reassigned'
    if before.get('status') != after.get('status'):
        if after.get('status') == 'review':
            return 'submitted_for_review'
        if after.get('status') == 'completed':
            return 'completed'
        if before.get('status') == 'review':
            return 'review_returned'
        return 'status_changed'
    if before.get('progress_percent') != after.get('progress_percent'):
        return 'progress_updated'
    return 'updated'


def employee_activity(workspace, user_id):
    """Caller already checked project read access. No history is written here."""
    employee = EmployeeMaster.objects.select_related('user').filter(user_id=user_id).first()
    if employee is None or not workspace.enterprise_project_id:
        raise NotFound('No employee assignment history exists in this project.')
    records = list(ProjectTask.objects.filter(
        project_id=workspace.enterprise_project_id,
        source_key__startswith=f'wbs:{workspace.pk}:', metadata__source='work_breakdown',
    ))
    by_wbs = {record.metadata['wbs_task_id']: record for record in records if record.metadata.get('wbs_task_id')}
    by_pk = {str(record.pk): record for record in records}
    states, selected, activity = {}, {}, []

    def consume(event, wbs_id, before, after):
        before, after = deepcopy(before), deepcopy(after)
        record = by_wbs.get(wbs_id)
        task_id = after.get('project_task_id') or before.get('project_task_id') or (record.pk if record else None)
        for snapshot in (before, after):
            snapshot['project_task_id'] = task_id
            snapshot['wbs_task_id'] = wbs_id
        previous_role, current_role = _role(before, user_id), _role(after, user_id)
        states[wbs_id] = after
        if not previous_role and not current_role:
            return
        actor_name = event.metadata.get('actor_name') or (
            event.actor.get_full_name() or event.actor.email if event.actor else '')
        actor = {'user_id': event.actor_id, 'name': actor_name} if event.actor_id or actor_name else None
        row = selected.setdefault(wbs_id, {'assigned_at': None, 'assigned_by': None})
        if current_role and current_role != previous_role:
            row.update(assigned_at=event.created_at.isoformat(), assigned_by=actor)
        row.update(snapshot=after if current_role else before,
                   role=current_role or previous_role,
                   assignment_state='current' if current_role else 'historical')
        activity.append({
            'id': f'{event.pk}:{wbs_id}', 'event_id': event.pk,
            'project_task_id': task_id, 'wbs_task_id': wbs_id,
            'title': after.get('title') or before.get('title') or '',
            'action': _action(before, after), 'actor': actor,
            'timestamp': event.created_at.isoformat(), 'before': before, 'after': after,
        })

    events = PlanningAuditEvent.objects.filter(
        project=workspace, action__startswith='work_breakdown.',
    ).select_related('actor').order_by('created_at', 'pk')
    for event in events.iterator(chunk_size=100):
        if event.action == 'work_breakdown.saved':
            if event.metadata.get('task_audit_version'):
                continue
            previous = {row['id']: row for row in event.before.get('tasks', []) if row.get('id')}
            following = {row['id']: row for row in event.after.get('tasks', []) if row.get('id')}
            for wbs_id in dict.fromkeys([*previous, *following]):
                old = previous.get(wbs_id, {})
                new = following.get(wbs_id)
                before = {**states.get(wbs_id, {}), **old}
                after = {**before, **(new or {}), 'is_deleted': new is None or not new.get('assignee_id')}
                if new is None:
                    after['assignee_id'] = None
                    after['reviewer_id'] = None
                # Text owners are display evidence only when a canonical ID exists.
                for snapshot in (before, after):
                    if snapshot.get('assignee_id'):
                        snapshot['assignee_name'] = snapshot.get('owner', '')
                    if snapshot.get('reviewer_id'):
                        snapshot['reviewer_name'] = snapshot.get('reviewer', '')
                # Ignore repeat saves and non-task attributes such as effort/provenance.
                keys = ('title', 'discipline', 'task_type', 'assignee_id', 'reviewer_id', 'status',
                        'progress_percent', 'due_date', 'priority', 'is_deleted')
                before = {key: before[key] for key in keys if key in before} | {
                    key: before[key] for key in ('assignee_name', 'reviewer_name') if key in before}
                after = {key: after[key] for key in keys if key in after} | {
                    key: after[key] for key in ('assignee_name', 'reviewer_name') if key in after}
                before.setdefault('is_deleted', not before.get('assignee_id'))
                if before != after:
                    consume(event, wbs_id, before, after)
                else:
                    states[wbs_id] = after
        elif event.action.startswith(TASK_EVENT_PREFIX):
            wbs_id = event.after.get('wbs_task_id') or event.before.get('wbs_task_id')
            if wbs_id:
                consume(event, wbs_id, event.before, event.after)
        elif event.action == 'work_breakdown.progress_updated':
            record = by_pk.get(event.entity_id)
            wbs_id = event.after.get('wbs_task_id') or (record.metadata.get('wbs_task_id') if record else None)
            if wbs_id:
                before = {**states.get(wbs_id, {}), **event.before}
                consume(event, wbs_id, before, {**before, **event.after})

    # Current responsibility can be displayed even if older audit evidence is
    # unavailable. Assignment actor/date stay unknown; created_at is not a guess.
    for wbs_id, record in by_wbs.items():
        snapshot = task_snapshot(record)
        role = _role(snapshot, user_id)
        if role:
            row = selected.setdefault(wbs_id, {'assigned_at': None, 'assigned_by': None})
            row.update(snapshot=snapshot, role=role, assignment_state='current')
        elif wbs_id in selected:
            selected[wbs_id]['assignment_state'] = 'historical'
        elif record.is_deleted and user_id in (record.assigned_to_id, record.reviewer_id):
            selected[wbs_id] = {'snapshot': snapshot, 'role': 'assignee' if record.assigned_to_id == user_id else 'reviewer',
                                'assignment_state': 'historical', 'assigned_at': None, 'assigned_by': None}
    if not selected:
        raise NotFound('No employee assignment history exists in this project.')
    people_ids = {row['snapshot'].get(field) for row in selected.values()
                  for field in ('assignee_id', 'reviewer_id') if row['snapshot'].get(field)}
    people = {person.pk: user_payload(person) for person in User.objects.filter(pk__in=people_ids).select_related('employee_master')}
    tasks = []
    for wbs_id, row in selected.items():
        snapshot = row['snapshot']
        tasks.append({
            **{key: snapshot.get(key) for key in ('project_task_id', 'title', 'discipline', 'task_type', 'status',
                                                'progress_percent', 'due_date', 'priority')},
            'wbs_task_id': wbs_id, **{key: row[key] for key in ('assigned_at', 'assigned_by', 'role', 'assignment_state')},
            'assigned_to': people.get(snapshot.get('assignee_id')), 'reviewer': people.get(snapshot.get('reviewer_id')),
        })
    tasks.sort(key=lambda row: (row['assignment_state'] != 'current', row['due_date'] or '9999', row['title'] or ''))
    current = [task for task in tasks if task['assignment_state'] == 'current']
    today = timezone.localdate().isoformat()
    summary = {'total_tasks': len(tasks), 'current_tasks': len(current), 'historical_tasks': len(tasks) - len(current),
               'open': sum(task['status'] != 'completed' for task in current),
               **{status: sum(task['status'] == status for task in current)
                  for status in ('in_progress', 'review', 'completed', 'blocked')},
               'overdue': sum(bool(task['due_date'] and task['due_date'] < today and task['status'] != 'completed') for task in current)}
    project = workspace.enterprise_project
    return {'employee': employee_payload(employee),
            'project': {'id': project.pk, 'planning_project_id': workspace.pk, 'code': project.code, 'name': project.name},
            'summary': summary, 'tasks': tasks, 'activity': list(reversed(activity))}
