"""Shared overtime routing, using the Organization reporting assignment."""
from django.contrib.auth import get_user_model
from django.db.models import Q
from .models import EmployeeMaster
from .workflows import HRWorkflowService
from apps.payroll.services.leave_approval import manager_for_employee

FINAL_ROLES = {'hr_manager', 'hr_admin', 'human_resource', 'finance_manager', 'finance_admin', 'finance', 'admin', 'super_admin', 'superadmin'}


def is_final_reviewer(user):
    return bool(user.is_active and (user.is_superuser or FINAL_ROLES & HRWorkflowService._role_codes(user)))


def direct_reports(user):
    candidates = EmployeeMaster.objects.filter(Q(manager__user=user) | Q(user__rbac_profile__manager__user=user)).select_related('user', 'manager__user')
    return [e for e in candidates if e.user_id != user.pk and getattr(manager_for_employee(e), 'user_id', None) == user.pk]


def can_review(task, user):
    instance = task.instance
    if not user.is_active or task.status != 'pending' or instance.status != 'pending':
        return False
    if user.pk in {instance.requested_by_id, instance.employee.user_id}:
        return False
    if task.stage.code == 'manager_review':
        return getattr(manager_for_employee(instance.employee), 'user_id', None) == user.pk
    return task.stage.code == 'hr_review' and is_final_reviewer(user)


def recipients(task):
    if task.stage.code == 'manager_review':
        manager = manager_for_employee(task.instance.employee)
        return [manager.user] if manager and manager.user_id and can_review(task, manager.user) else []
    users = get_user_model().objects.filter(Q(is_superuser=True) | Q(rbac_profile__roles__code__in=FINAL_ROLES, rbac_profile__roles__is_active=True), is_active=True).distinct()
    return [u for u in users if can_review(task, u)]


def notify_result(overtime):
    from apps.notifications.models import Notification, NotificationCategory
    category = NotificationCategory.objects.filter(name='APPROVAL').first()
    ids = {overtime.requested_by_id, overtime.employee.user_id} - {None}
    for user in get_user_model().objects.filter(pk__in=ids, is_active=True):
        Notification.objects.create(recipient=user, category=category, title='Overtime request updated',
            message=f'{overtime.work_date} / {overtime.requested_hours} hours / {overtime.status}',
            action_url=f'/profile?tab=requests&request_type=overtime&request={overtime.pk}', action_label='View overtime', send_in_app=True,
            metadata={'overtime_request_id': str(overtime.pk)})
