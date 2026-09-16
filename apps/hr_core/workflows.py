"""Reusable HR approval, reminder, and escalation workflow engine."""

from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from apps.rbac.approval_eligibility import approval_access, has_business_position, position_users

from .models import (
    HRWorkflowDefinition,
    HRWorkflowEvent,
    HRWorkflowInstance,
    HRWorkflowTask,
)


class HRWorkflowService:
    @classmethod
    def actionable_instance_ids(cls, user):
        tasks = HRWorkflowTask.objects.filter(status='pending', instance__status='pending',
            stage_id=F('instance__current_stage_id')).select_related('stage', 'instance__employee', 'instance__definition')
        return [task.instance_id for task in tasks if cls.can_act(task, user)]

    @staticmethod
    def approval_module(instance):
        return 'payroll' if instance.subject_type == 'payroll.leave_request' else (
            'hr_self_service' if instance.subject_type in {'hr.expense_request', 'hr.travel_request', 'hr.asset_request'}
            else 'hr_management'
        )

    @staticmethod
    def task_is_current(task):
        instance = task.instance
        if (task.status != 'pending' or instance.status != 'pending' or instance.current_stage_id != task.stage_id
                or task.stage.definition_id != instance.definition_id):
            return False
        prior = instance.definition.stages.filter(sequence__lt=task.stage.sequence)
        approved_ids = instance.tasks.filter(status='approved').values_list('stage_id', flat=True)
        return not prior.exclude(pk__in=approved_ids).exists()

    @staticmethod
    def _role_codes(user):
        if not user or not user.is_authenticated:
            return set()
        if user.is_superuser:
            return {'super_admin'}
        try:
            return set(user.rbac_profile.roles.filter(is_active=True).values_list('code', flat=True))
        except Exception:
            return set()

    @classmethod
    def _assignment(cls, stage, instance):
        if stage.approver_type == 'employee_manager':
            manager = instance.employee.manager if instance.employee and instance.employee.manager_id else None
            if instance.subject_type in {'payroll.leave_request', 'hr.overtime_request'}:
                from apps.payroll.services.leave_approval import manager_for_employee
                manager = manager_for_employee(instance.employee)
            return (manager.user if manager else None), ''
        if stage.approver_type == 'requester':
            return instance.requested_by, ''
        if stage.approver_type == 'user':
            from django.contrib.auth import get_user_model

            return get_user_model().objects.filter(pk=stage.approver_value).first(), ''
        return None, stage.approver_value

    @classmethod
    def _create_task(cls, instance, stage):
        assigned_to, role_code = cls._assignment(stage, instance)
        module = cls.approval_module(instance)
        positions = (role_code,)
        if stage.code == 'hr_review' and instance.subject_type == 'payroll.leave_request':
            from apps.payroll.services.leave_approval import HR_APPROVAL_POSITIONS
            positions = HR_APPROVAL_POSITIONS
        elif stage.code == 'hr_review' and instance.subject_type == 'hr.overtime_request':
            from .overtime import FINAL_POSITIONS
            positions = FINAL_POSITIONS
        if assigned_to is not None:
            if not approval_access(assigned_to, module):
                raise ValidationError({'workflow': 'The designated approver needs active approval access before this stage can open.'})
        elif not role_code or not position_users(positions, module).exists():
            raise ValidationError({'workflow': 'Configure an active business approver with approval access before this stage can open.'})
        now = timezone.now()
        task, _ = HRWorkflowTask.objects.get_or_create(
            instance=instance,
            stage=stage,
            defaults={
                'assigned_to': assigned_to,
                'assigned_role_code': role_code,
                'due_at': now + timedelta(hours=stage.due_after_hours),
            },
        )
        HRWorkflowEvent.objects.create(
            instance=instance,
            event_type='task_created',
            stage_code=stage.code,
            metadata={
                'assigned_to': assigned_to.pk if assigned_to else None,
                'assigned_role_code': role_code,
                'due_at': task.due_at.isoformat() if task.due_at else None,
            },
        )
        cls._notify_task(task, reminder=False)
        return task

    @classmethod
    @transaction.atomic
    def start(cls, definition_code, subject_type, subject_id, employee=None, requested_by=None, context=None):
        definition = HRWorkflowDefinition.objects.select_for_update().filter(
            code=definition_code, is_active=True
        ).order_by('-version').first()
        if not definition:
            raise ValidationError({'workflow': f'Active workflow definition {definition_code!r} was not found.'})
        if definition.subject_type != subject_type:
            raise ValidationError({'subject_type': 'Subject type does not match the workflow definition.'})
        first_stage = definition.stages.order_by('sequence').first()
        if not first_stage:
            raise ValidationError({'workflow': 'Workflow definition has no stages.'})
        if subject_type in {'payroll.leave_request', 'hr.overtime_request'}:
            from apps.payroll.services.leave_approval import manager_for_employee
            manager = manager_for_employee(employee)
            if not manager or not manager.user_id or manager.user_id == employee.user_id or not manager.user.is_active:
                raise ValidationError({'manager': 'Assign an active reporting manager before submitting this request.'})
        instance, created = HRWorkflowInstance.objects.get_or_create(
            definition=definition,
            subject_type=subject_type,
            subject_id=str(subject_id),
            defaults={
                'employee': employee,
                'requested_by': requested_by,
                'current_stage': first_stage,
                'context': context or {},
            },
        )
        if created:
            HRWorkflowEvent.objects.create(
                instance=instance, event_type='started', actor=requested_by,
                stage_code=first_stage.code, metadata={'context': context or {}},
            )
            if subject_type == 'payroll.leave_request' and first_stage.code == 'hr_review':
                HRWorkflowEvent.objects.create(instance=instance, event_type='manager_skipped', stage_code='manager_review', metadata={'reason': 'No assigned manager'})
            cls._create_task(instance, first_stage)
        return instance

    @classmethod
    def can_act(cls, task, user):
        if not cls.task_is_current(task) or not approval_access(user, cls.approval_module(task.instance)):
            return False
        if task.instance.subject_type == 'hr.overtime_request':
            from .overtime import can_review
            return can_review(task, user)
        if task.instance.subject_type == 'payroll.leave_request':
            from apps.payroll.models import LeaveRequest
            from apps.payroll.services.leave_approval import can_review
            request = LeaveRequest.objects.filter(pk=task.instance.subject_id).first()
            return bool(request and can_review(request, user))
        if task.stage.approver_type == 'employee_manager':
            manager = task.instance.employee.manager if task.instance.employee else None
            return bool(manager and manager.user_id == user.pk)
        if task.assigned_to_id:
            return task.assigned_to_id == user.id
        return bool(task.assigned_role_code and has_business_position(user, (task.assigned_role_code,)))

    @classmethod
    @transaction.atomic
    def decide(cls, instance, user, decision, note=''):
        instance = HRWorkflowInstance.objects.select_for_update().get(pk=instance.pk)
        if instance.status != 'pending' or not instance.current_stage_id:
            raise ValidationError({'workflow': 'This workflow is no longer actionable.'})
        task = HRWorkflowTask.objects.select_for_update().get(
            instance=instance, stage=instance.current_stage, status='pending'
        )
        if not cls.can_act(task, user):
            raise PermissionDenied('You are not assigned to this workflow stage.')
        if decision not in {'approve', 'reject'}:
            raise ValidationError({'decision': 'Decision must be approve or reject.'})
        if decision == 'reject' and task.stage.require_comment_on_reject and not str(note).strip():
            raise ValidationError({'note': 'A rejection reason is required.'})

        now = timezone.now()
        task.status = 'approved' if decision == 'approve' else 'rejected'
        task.decided_by = user
        task.decided_at = now
        task.decision_note = str(note or '').strip()
        task.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note', 'updated_at'])
        HRWorkflowEvent.objects.create(
            instance=instance, event_type=task.status, actor=user,
            stage_code=task.stage.code, note=task.decision_note,
        )

        if decision == 'reject':
            instance.status = 'rejected'
            instance.completed_at = now
            instance.save(update_fields=['status', 'completed_at', 'updated_at'])
            return instance

        next_stage = instance.definition.stages.filter(
            sequence__gt=task.stage.sequence
        ).order_by('sequence').first()
        if next_stage:
            instance.current_stage = next_stage
            instance.save(update_fields=['current_stage', 'updated_at'])
            cls._create_task(instance, next_stage)
        else:
            instance.status = 'approved'
            instance.current_stage = None
            instance.completed_at = now
            instance.save(update_fields=['status', 'current_stage', 'completed_at', 'updated_at'])
            HRWorkflowEvent.objects.create(
                instance=instance, event_type='completed', actor=user,
            )
        return instance

    @classmethod
    @transaction.atomic
    def cancel(cls, instance, user, note=''):
        instance = HRWorkflowInstance.objects.select_for_update().get(pk=instance.pk)
        if instance.status != 'pending':
            raise ValidationError({'workflow': 'Only pending workflows can be cancelled.'})
        instance.status = 'cancelled'
        instance.current_stage = None
        instance.completed_at = timezone.now()
        instance.save(update_fields=['status', 'current_stage', 'completed_at', 'updated_at'])
        instance.tasks.filter(status='pending').update(status='cancelled')
        HRWorkflowEvent.objects.create(
            instance=instance, event_type='cancelled', actor=user, note=str(note or '').strip()
        )
        return instance

    @classmethod
    def _task_recipients(cls, task):
        if task.instance.subject_type == 'hr.overtime_request':
            from .overtime import recipients
            return recipients(task)
        if task.instance.subject_type == 'payroll.leave_request' and task.stage.code == 'hr_review':
            from apps.payroll.services.leave_approval import active_hr_approvers
            employee_user_id = task.instance.employee.user_id if task.instance.employee else None
            return [user for user in active_hr_approvers(employee_user_id) if cls.can_act(task, user)]
        if task.assigned_to_id:
            if task.stage.approver_type == 'employee_manager':
                manager = cls._assignment(task.stage, task.instance)[0]
                return [manager] if manager and cls.can_act(task, manager) else []
            return [task.assigned_to] if cls.can_act(task, task.assigned_to) else []
        if not task.assigned_role_code:
            return []
        return [user for user in position_users((task.assigned_role_code,), cls.approval_module(task.instance))
                if cls.can_act(task, user)]

    @classmethod
    def _notify_task(cls, task, reminder=False):
        # Request status and workflow linkage are saved by the enclosing
        # business transaction after task creation. Resolve recipients only
        # after commit, using the current stage and current manager.
        transaction.on_commit(lambda: cls._deliver_task(task.pk, reminder=reminder), robust=True)

    @classmethod
    def _deliver_task(cls, task_id, reminder=False):
        task = None
        try:
            from apps.notifications.services import NotificationService
            task = HRWorkflowTask.objects.select_related(
                'stage', 'instance__definition', 'instance__employee', 'assigned_to',
            ).filter(pk=task_id).first()
            if task is None or not cls.task_is_current(task):
                return
            stage = task.stage
            if task.instance.subject_type == 'hr.overtime_request':
                context = task.instance.context
                for recipient in cls._task_recipients(task):
                    NotificationService.create_notification(recipient=recipient, category='APPROVAL',
                        title=('Reminder: ' if reminder else '') + 'Overtime approval',
                        message=f'{context.get("employee_name", "Employee")} / {context.get("requested_hours", "")} hours requested',
                        action_url=f'/hr/leave?view=encashment&request={task.instance.subject_id}', action_label='Review overtime',
                        metadata={'workflow_task_id': str(task.id), 'requires_action': True})
                return
            is_leave = task.instance.subject_type == 'payroll.leave_request'
            context = task.instance.context
            for recipient in cls._task_recipients(task):
                if is_leave and task.instance.employee and recipient.pk == task.instance.employee.user_id:
                    continue
                NotificationService.create_notification(
                    recipient=recipient,
                    title=('Reminder: ' if reminder else '') + stage.name,
                    message=(f'Please approve the leave request of {context.get("employee_name", "employee")} ? {context.get("days_requested", "")} days requested.' if is_leave else f'{task.instance.definition.name} requires your review.'),
                    category='APPROVAL',
                    priority='HIGH' if reminder else 'NORMAL',
                    action_url=f'/approvals?leave={task.instance.subject_id}' if is_leave else '/approvals',
                    action_label='Review request',
                    metadata={'workflow_task_id': str(task.id), 'requires_action': True},
                )
        except Exception:
            if task and task.instance.subject_type == 'payroll.leave_request':
                raise
            return

    @classmethod
    @transaction.atomic
    def process_overdue_tasks(cls, now=None):
        now = now or timezone.now()
        reminded = 0
        escalated = 0
        tasks = HRWorkflowTask.objects.select_for_update(
            skip_locked=True, of=('self',)
        ).select_related(
            'stage', 'instance__definition', 'assigned_to'
        ).filter(status='pending')
        for task in tasks:
            if not cls.task_is_current(task):
                continue
            if task.due_at and task.due_at <= now and not task.reminder_sent_at:
                task.reminder_sent_at = now
                task.save(update_fields=['reminder_sent_at', 'updated_at'])
                HRWorkflowEvent.objects.create(
                    instance=task.instance, event_type='reminder_sent',
                    stage_code=task.stage.code,
                )
                cls._notify_task(task, reminder=True)
                reminded += 1
            escalation_at = task.created_at + timedelta(hours=task.stage.escalate_after_hours)
            if escalation_at <= now and not task.escalated_at:
                task.escalated_at = now
                if task.stage.escalation_role_code and task.instance.subject_type not in {'payroll.leave_request', 'hr.overtime_request'}:
                    task.assigned_to = None
                    task.assigned_role_code = task.stage.escalation_role_code
                task.save(update_fields=['escalated_at', 'assigned_to', 'assigned_role_code', 'updated_at'])
                HRWorkflowEvent.objects.create(
                    instance=task.instance, event_type='escalated',
                    stage_code=task.stage.code,
                    metadata={'assigned_role_code': task.assigned_role_code},
                )
                cls._notify_task(task, reminder=True)
                escalated += 1
        return {'reminded': reminded, 'escalated': escalated}
