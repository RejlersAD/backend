"""Publish saved WBS assignments into the canonical personal work register."""
from copy import deepcopy
from decimal import Decimal

from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.core.project_models import ProjectTask
from apps.core.task_assignment_policy import eligible_employees, employee_payload, require_task_manager, user_payload
from apps.users.models import User
from .work_assignment_history import record_task_event, task_snapshot


ASSIGNMENT_DEFAULTS = {
    'assignee_id': None, 'reviewer_id': None, 'task_type': 'task',
    'due_date': None, 'priority': 'medium',
}


def source_key(workspace, task_id):
    return f'wbs:{workspace.pk}:{task_id}'


def hydrate_assignments(workspace, tasks, token):
    """Return employee progress live, without copying it into mutable WBS inputs."""
    tasks = deepcopy(tasks)
    keys = [source_key(workspace, task['id']) for task in tasks]
    records = {task.source_key: task for task in ProjectTask.objects.filter(
        source_key__in=keys, is_deleted=False, project_id=workspace.enterprise_project_id,
        metadata__preview_confirmed_at=token,
    ).select_related('assigned_to__employee_master', 'reviewer__employee_master')}
    people = {person.pk: person for person in User.objects.filter(pk__in={
        task.get(field) for task in tasks for field in ('assignee_id', 'reviewer_id') if task.get(field)
    }).select_related('employee_master')}
    for task in tasks:
        for name, default in ASSIGNMENT_DEFAULTS.items():
            task.setdefault(name, default)
        record = records.get(source_key(workspace, task['id']))
        task.update(project_task_id=record.pk if record else None,
                    status=record.status if record else 'todo',
                    progress_percent=record.progress_percent if record else 0,
                    assignee=user_payload(record.assigned_to if record else people.get(task['assignee_id'])),
                    reviewer_user=user_payload(record.reviewer if record else people.get(task['reviewer_id'])))
    return tasks


def normalize_assignment_fields(tasks, known):
    for task in tasks:
        original = known.get(task['id']) or {}
        for name, default in ASSIGNMENT_DEFAULTS.items():
            task.setdefault(name, original.get(name, default))
        for name in ('duration_days', 'planned_start_date'):
            if name in original:
                task.setdefault(name, original[name])
        if task['due_date'] is not None and hasattr(task['due_date'], 'isoformat'):
            task['due_date'] = task['due_date'].isoformat()
        # Status and progress are employee-owned and excluded from WBS writes.
        for key in ('status', 'progress_percent', 'project_task_id', 'assignee', 'reviewer_user'):
            task.pop(key, None)


def sync_assignments(run, tasks, *, actor):
    return sync_workspace_assignments(
        run.project, tasks, actor=actor,
        token=run.summary['preview_confirmation']['confirmed_at'], intelligence_run_id=run.pk,
    )


def sync_workspace_assignments(workspace, tasks, *, actor, token, intelligence_run_id=None):
    """One transaction and stable key make repeated saves/reassignments idempotent."""
    existing = {task.source_key: task for task in ProjectTask.objects.select_for_update().filter(
        source_key__startswith=f'wbs:{workspace.pk}:',
    )}
    selected_ids = {task[field] for task in tasks for field in ('assignee_id', 'reviewer_id') if task[field]}
    if not selected_ids and not any(not task.is_deleted for task in existing.values()):
        return
    if not workspace.enterprise_project_id:
        raise ValidationError({'tasks': 'Connect this workspace to a RADAI project before assigning employees.'})
    require_task_manager(actor, workspace.enterprise_project)
    people = {person.user_id: person for person in eligible_employees(workspace.enterprise_project, actor).filter(user_id__in=selected_ids)}
    if selected_ids - set(people):
        raise ValidationError({'tasks': 'Select current employees from this project\'s eligible employee list.'})
    kept = set()
    for task in tasks:
        key = source_key(workspace, task['id'])
        record = existing.get(key)
        assignee = people.get(task['assignee_id'])
        reviewer = people.get(task['reviewer_id'])
        if assignee and reviewer and assignee.pk == reviewer.pk:
            raise ValidationError({'tasks': 'The assignee and reviewer must be different employees.'})
        if reviewer:
            task['reviewer'] = employee_payload(reviewer)['name'][:120]
        if not assignee:
            # Unassignment withdraws the actionable record; preserve historical
            # status and provenance rather than deleting the completed work.
            if record and record.assigned_to_id:
                task['owner'] = ''
            if record and record.reviewer_id and not reviewer:
                task['reviewer'] = ''
            continue
        kept.add(key)
        task['owner'] = employee_payload(assignee)['name'][:120]
        if reviewer:
            task['reviewer'] = employee_payload(reviewer)['name'][:120]
        elif task.get('reviewer_id') is None and record and record.reviewer_id:
            task['reviewer'] = ''
        metadata = {
            **(record.metadata if record else {}), 'source': 'work_breakdown',
            'planning_project_id': workspace.pk, 'wbs_task_id': task['id'],
            'intelligence_run_id': intelligence_run_id, 'preview_confirmed_at': token,
            'planning_mode': 'document' if intelligence_run_id is not None else 'manual',
            'discipline': task['discipline'], 'depends_on': task['depends_on'],
            'acceptance_criteria': task['acceptance_criteria'],
            'source_references': task['source_references'],
            'employee_id': str(assignee.pk), 'reviewer_employee_id': str(reviewer.pk) if reviewer else None,
            'organization_id': str(assignee.user.rbac_profile.organization_id),
            'assignee_name': employee_payload(assignee)['name'],
            'reviewer_name': employee_payload(reviewer)['name'] if reviewer else '',
        }
        metadata.pop('withdrawn_at', None)
        values = {
            'project_id': workspace.enterprise_project_id, 'title': task['title'],
            'assigned_to_id': assignee.user_id, 'reviewer_id': reviewer.user_id if reviewer else None,
            'task_type': task['task_type'], 'due_date': task['due_date'],
            'priority': task['priority'], 'estimated_hours': Decimal(str(task['effort_hours'])) if task['effort_hours'] is not None else None,
            'description': task['acceptance_criteria'], 'metadata': metadata,
            'is_deleted': False, 'deleted_at': None,
        }
        if record is None:
            record = ProjectTask.objects.create(source_key=key, **values)
            record_task_event(workspace=workspace, actor=actor, task=record, action='created')
        else:
            changes = {name: value for name, value in values.items()
                       if (getattr(record, name).isoformat() if name == 'due_date' and getattr(record, name) else getattr(record, name)) != value}
            if changes:
                before = task_snapshot(record)
                # Keep employee progress across planner edits and reassignment.
                for name, value in changes.items():
                    setattr(record, name, value)
                record.save(update_fields=[*changes, 'updated_at'])
                record_task_event(workspace=workspace, actor=actor, task=record, before=before,
                                  action='changed' if any(name in changes for name in ('assigned_to_id', 'reviewer_id', 'is_deleted')) else 'updated')
    removed = [task.pk for key, task in existing.items() if key not in kept and not task.is_deleted]
    if removed:
        now = timezone.now()
        ProjectTask.objects.filter(pk__in=removed).update(is_deleted=True, deleted_at=now, updated_at=now)
        for record in existing.values():
            if record.pk in removed:
                before = task_snapshot(record)
                record.is_deleted = True
                record_task_event(workspace=workspace, actor=actor, task=record, before=before, action='withdrawn')
