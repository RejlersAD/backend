"""Canonical employee identities and narrow project-task assignment authority."""
from django.db.models import F, Q
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.hr_core.models import EmployeeMaster
from apps.rbac.action_policy import module_action_allowed, record_workflow_not_denied
from apps.rbac.approval_eligibility import active_approval_user, canonical_employee, project_approval_assignment


def manages_project_tasks(user, project):
    return bool(project and not project.is_deleted and active_approval_user(user)
                and module_action_allowed(user, 'planning_package', 'update')
                and (user.is_staff or user.is_superuser or project_approval_assignment(user, project)))


def require_task_manager(user, project):
    if not manages_project_tasks(user, project):
        raise PermissionDenied('Only this project\'s manager, owner or authorized administrator can assign its work.')


def eligible_employees(project, actor):
    """Limit identity search to the project's organization; expose no HR records."""
    from apps.rbac.models import UserProfile

    owner_org = UserProfile.objects.filter(user_id=project.owner_id, is_deleted=False).values_list('organization_id', flat=True).first()
    organization = owner_org or UserProfile.objects.filter(user=actor, is_deleted=False).values_list('organization_id', flat=True).first()
    if not organization:
        return EmployeeMaster.objects.none()
    now = timezone.now()
    return EmployeeMaster.objects.filter(
        user__is_active=True, employment_status__in=['active', 'probation', 'notice_period'],
        user__rbac_profile__organization_id=organization,
        user__rbac_profile__is_deleted=False, user__rbac_profile__status='active',
    ).filter(
        Q(user__rbac_profile__locked_until__isnull=True) | Q(user__rbac_profile__locked_until__lte=now),
    ).filter(Q(exit_date__isnull=True) | Q(exit_date__gte=timezone.localdate(now))).filter(
        Q(user__rbac_profile__canonical_employee__isnull=True) | Q(user__rbac_profile__canonical_employee_id=F('pk')),
    ).select_related('user__rbac_profile').order_by('first_name', 'last_name', 'employee_code')


def employee_payload(employee):
    if not employee:
        return None
    return {
        'user_id': employee.user_id, 'employee_id': str(employee.pk),
        'employee_code': employee.employee_code or employee.employee_number,
        'name': employee.get_full_name() or employee.user.get_full_name() or employee.user.email,
        'email': employee.email or employee.user.email,
        'department': employee.department or employee.division or '',
        'job_title': employee.job_title_uae or employee.job_title_finland or '',
    }


def user_payload(user):
    if not user:
        return None
    try:
        return employee_payload(user.employee_master)
    except EmployeeMaster.DoesNotExist:
        return {'user_id': user.pk, 'employee_id': None, 'employee_code': '',
                'name': user.get_full_name() or user.email, 'email': user.email, 'department': '', 'job_title': ''}


def may_use_assigned_work(user, action='read'):
    employee = canonical_employee(user)
    return bool(employee and (not employee.exit_date or employee.exit_date >= timezone.localdate())
                and record_workflow_not_denied(user, 'project_control', action))


def is_wbs_task(task):
    return bool(task.source_key and task.source_key.startswith('wbs:')
                and (task.metadata or {}).get('source') == 'work_breakdown')
