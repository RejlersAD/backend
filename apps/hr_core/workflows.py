"""Reusable HR approval, reminder, and escalation workflow engine."""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from .models import (
    HRWorkflowDefinition,
    HRWorkflowEvent,
    HRWorkflowInstance,
    HRWorkflowTask,
)


class HRWorkflowService:
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
            return (manager.user if manager else None), ('' if manager else stage.escalation_role_code)
        if stage.approver_type == 'requester':
            return instance.requested_by, ''
        if stage.approver_type == 'user':
            from django.contrib.auth import get_user_model

            return get_user_model().objects.filter(pk=stage.approver_value).first(), ''
        return None, stage.approver_value

    @classmethod
    def _create_task(cls, instance, stage):
        assigned_to, role_code = cls._assignment(stage, instance)
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
        definition = HRWorkflowDefinition.objects.filter(
            code=definition_code, is_active=True
        ).order_by('-version').first()
        if not definition:
            raise ValidationError({'workflow': f'Active workflow definition {definition_code!r} was not found.'})
        if definition.subject_type != subject_type:
            raise ValidationError({'subject_type': 'Subject type does not match the workflow definition.'})
        first_stage = definition.stages.order_by('sequence').first()
        if not first_stage:
            raise ValidationError({'workflow': 'Workflow definition has no stages.'})
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
            cls._create_task(instance, first_stage)
        return instance

    @classmethod
    def can_act(cls, task, user):
        if user.is_superuser:
            return True
        if task.assigned_to_id:
            return task.assigned_to_id == user.id
        return bool(task.assigned_role_code and task.assigned_role_code in cls._role_codes(user))

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
        if task.assigned_to_id:
            return [task.assigned_to]
        if not task.assigned_role_code:
            return []
        from django.contrib.auth import get_user_model

        return list(get_user_model().objects.filter(
            rbac_profile__roles__code=task.assigned_role_code,
            rbac_profile__roles__is_active=True,
            is_active=True,
        ).distinct())

    @classmethod
    def _notify_task(cls, task, reminder=False):
        try:
            from apps.notifications.services import NotificationService

            stage = task.stage
            for recipient in cls._task_recipients(task):
                NotificationService.create_notification(
                    recipient=recipient,
                    title=('Reminder: ' if reminder else '') + stage.name,
                    message=f'{task.instance.definition.name} requires your review.',
                    category='APPROVAL',
                    priority='HIGH' if reminder else 'NORMAL',
                    action_url='/approvals',
                    action_label='Review request',
                    metadata={'workflow_task_id': str(task.id)},
                )
        except Exception:
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
                if task.stage.escalation_role_code:
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
