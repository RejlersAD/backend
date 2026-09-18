"""Record-scoped personal task details and progress; no project access grant."""
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.project_models import ProjectTask
from apps.core.task_assignment_policy import is_wbs_task, manages_project_tasks, may_use_assigned_work, user_payload
from apps.rbac.action_policy import module_action_allowed, record_workflow_not_denied
from apps.rbac.approval_eligibility import active_approval_user


class TaskProgressSerializer(serializers.Serializer):
    expected_updated_at = serializers.DateTimeField()
    status = serializers.ChoiceField(choices=[choice[0] for choice in ProjectTask.STATUS_CHOICES], required=False)
    progress_percent = serializers.IntegerField(min_value=0, max_value=100, required=False)

    def validate(self, attrs):
        if set(self.initial_data) - set(self.fields):
            raise serializers.ValidationError('Only status and progress may be updated from My Work.')
        if not {'status', 'progress_percent'} & set(attrs):
            raise serializers.ValidationError('Update the task status or progress.')
        return attrs


def task_role(task, user):
    if not active_approval_user(user) or not record_workflow_not_denied(user, 'project_control', 'read'):
        raise NotFound()
    if manages_project_tasks(user, task.project):
        return 'manager'
    if task.assigned_to_id != user.pk and task.reviewer_id != user.pk:
        raise NotFound()
    if is_wbs_task(task):
        if (not may_use_assigned_work(user) or str(user.rbac_profile.organization_id)
                != str((task.metadata or {}).get('organization_id'))):
            raise NotFound()
    elif not module_action_allowed(user, 'project_control', 'read'):
        raise NotFound()
    return 'assignee' if task.assigned_to_id == user.pk else 'reviewer'


def allowed_statuses(task, role):
    if role == 'manager':
        return [choice[0] for choice in ProjectTask.STATUS_CHOICES]
    if role == 'reviewer':
        return ['in_progress', 'completed'] if task.status == 'review' else []
    if task.status == 'completed':
        return []
    choices = ['todo', 'in_progress', 'blocked', 'review']
    if task.task_type != 'deliverable':
        choices.append('completed')
    return choices


def task_detail(task, user):
    role = task_role(task, user)
    writable = (module_action_allowed(user, 'planning_package', 'update') if role == 'manager'
                else record_workflow_not_denied(user, 'project_control', 'update'))
    return {
        'id': task.pk, 'title': task.title, 'description': task.description,
        'task_type': task.task_type, 'project': {'id': task.project_id, 'code': task.project.code, 'name': task.project.name},
        'due_date': task.due_date.isoformat() if task.due_date else None,
        'priority': task.priority, 'status': task.status, 'progress_percent': task.progress_percent,
        'assigned_to': user_payload(task.assigned_to), 'reviewer': user_payload(task.reviewer),
        'acceptance_criteria': (task.metadata or {}).get('acceptance_criteria', task.description),
        'allowed_statuses': allowed_statuses(task, role) if writable else [],
        'can_update_progress': writable and role in {'manager', 'assignee'} and task.status != 'completed',
        'role': role, 'updated_at': task.updated_at.isoformat(),
    }


class WorkHubTaskView(APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'patch', 'head', 'options']

    def _task(self, task_id, *, lock=False):
        queryset = ProjectTask.objects.filter(is_deleted=False, project__is_deleted=False)
        if lock:
            queryset = queryset.select_for_update(of=('self',))
        return get_object_or_404(queryset.select_related('project', 'assigned_to__employee_master', 'reviewer__employee_master'), pk=task_id)

    def get(self, request, task_id):
        return Response(task_detail(self._task(task_id), request.user))

    @transaction.atomic
    def patch(self, request, task_id):
        serializer = TaskProgressSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        preliminary = self._task(task_id)
        task_role(preliminary, request.user)
        if is_wbs_task(preliminary):
            from apps.planning_intelligence.models import PlanningProject
            # WBS saves lock workspace -> run -> task. Use the same order so
            # the progress audit's workspace FK cannot deadlock a planner save.
            workspace = PlanningProject.objects.select_for_update().filter(
                pk=preliminary.metadata.get('planning_project_id'),
                enterprise_project_id=preliminary.project_id, is_deleted=False,
            ).first()
            if workspace is None:
                raise NotFound()
        task = self._task(task_id, lock=True)
        current = task_detail(task, request.user)
        data = serializer.validated_data
        if task.updated_at != data['expected_updated_at']:
            return Response({'error': 'This task changed or was reassigned. Reload it before saving.', 'code': 'task_changed'}, status=409)
        target = data.get('status', task.status)
        if target != task.status and target not in current['allowed_statuses']:
            raise PermissionDenied('This task transition is not available for your current responsibility.')
        if 'progress_percent' in data and not current['can_update_progress']:
            raise PermissionDenied('Only the assignee or project manager can update progress.')
        if not current['allowed_statuses'] and not current['can_update_progress']:
            raise PermissionDenied('This task is currently read only.')
        before = {'status': task.status, 'progress_percent': task.progress_percent}
        from apps.planning_intelligence.services.work_assignment_history import task_snapshot
        audit_before = task_snapshot(task)
        task.status = target
        task.progress_percent = 100 if target == 'completed' else data.get('progress_percent', task.progress_percent)
        changed = before != {'status': task.status, 'progress_percent': task.progress_percent}
        if changed:
            task.save(update_fields=['status', 'progress_percent', 'updated_at'])
            if is_wbs_task(task):
                from apps.planning_intelligence.models import PlanningProject
                from apps.planning_intelligence.services.audit import record_event
                workspace = PlanningProject.objects.filter(
                    pk=task.metadata.get('planning_project_id'), enterprise_project_id=task.project_id, is_deleted=False,
                ).first()
                if workspace:
                    record_event(project=workspace, actor=request.user, action='work_breakdown.progress_updated',
                                 entity=task, before=audit_before, after=task_snapshot(task),
                                 metadata={'task_audit_version': 1,
                                           'actor_name': request.user.get_full_name() or request.user.email})
        return Response(task_detail(task, request.user))
