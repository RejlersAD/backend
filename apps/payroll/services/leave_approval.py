"""Leave-specific routing and authorization shared by queues and decisions."""
from django.db.models import Q
from rest_framework.exceptions import ValidationError
from apps.hr_core.models import EmployeeMaster
from apps.hr_core.workflows import HRWorkflowService
from apps.rbac.approval_eligibility import approval_access, has_business_position, position_users

HR_ROLES = ('hr_manager', 'hr_admin', 'super_admin', 'superadmin', 'admin')
HR_APPROVAL_POSITIONS = ('hr_manager', 'hr_admin', 'head_hr_administration')

def is_hr(user):
    return bool(user.is_active and (user.is_superuser or set(HR_ROLES) & HRWorkflowService._role_codes(user)))

def active_hr_approvers(employee_user_id=None):
    return position_users(HR_APPROVAL_POSITIONS, 'payroll').exclude(pk=employee_user_id)


def employee_for_request(request):
    if request.canonical_employee_id:
        return request.canonical_employee
    if request.employee_id:
        employee = EmployeeMaster.objects.filter(user_id=request.employee_id).first()
        if employee:
            return employee
    if not request.employee_code:
        return None
    matches = list(EmployeeMaster.objects.filter(
        Q(employee_code=request.employee_code) | Q(emp_code=request.employee_code) | Q(employee_number=request.employee_code)
    )[:2])
    return matches[0] if len(matches) == 1 else None


def manager_for_employee(employee, user_id=None):
    """Use the Organization profile assignment first, then employee master."""
    from apps.rbac.models import UserProfile
    profile = UserProfile.objects.select_related('manager__user').filter(
        user_id=user_id or (employee.user_id if employee else None), is_deleted=False,
    ).first()
    if profile and profile.manager_id:
        return profile.manager
    return employee.manager if employee and employee.manager_id else None


def manager_for_request(request):
    return manager_for_employee(employee_for_request(request), request.employee_id)


def can_review(request, user):
    if not approval_access(user, 'payroll'):
        return False
    employee = employee_for_request(request)
    if request.employee_id == user.pk or (employee and employee.user_id == user.pk):
        return False
    if request.status not in ('PENDING', 'RM_APPROVED'):
        return False
    if request.workflow_instance_id:
        task = request.workflow_instance.tasks.filter(
            stage_id=request.workflow_instance.current_stage_id, status='pending',
        ).select_related('stage', 'instance').first()
        if not task or not HRWorkflowService.task_is_current(task):
            return False
    manager = manager_for_request(request)
    if request.status == 'PENDING' and manager:
        if request.workflow_instance_id and request.workflow_instance.current_stage.code != 'manager_review':
            return False
        return bool(manager.user_id == user.pk and user.is_active)
    if request.status == 'PENDING' or not has_business_position(user, HR_APPROVAL_POSITIONS):
        return False
    if request.workflow_instance_id:
        instance = request.workflow_instance
        if instance.status != 'pending':
            return False
        return bool(instance.current_stage_id and instance.current_stage.code == 'hr_review')
    return True


def prepare_review_workflow(request):
    """Reconcile stale tasks on a permitted decision; caller holds the request lock."""
    if not request.workflow_instance_id:
        return
    from apps.hr_core.models import HRWorkflowInstance
    instance = HRWorkflowInstance.objects.select_for_update().get(pk=request.workflow_instance_id)
    if instance.status != 'pending':
        raise ValidationError({'workflow': 'This workflow is no longer pending.'})
    manager = manager_for_request(request)
    if request.status == 'PENDING' and not manager:
        raise ValidationError({'manager': 'Configure the reporting manager before review.'})
    if request.status == 'PENDING' and instance.current_stage.code == 'manager_review':
        instance.tasks.filter(status='pending', stage__code='manager_review').update(assigned_to_id=manager.user_id, assigned_role_code='')
    request.workflow_instance = instance


def require_manager(employee):
    if not employee:
        raise ValidationError({'employee': 'An employee master record is required. Ask HR to complete your employee profile.'})
    manager = manager_for_employee(employee)
    if manager is None:
        raise ValidationError({'manager': 'Assign an active reporting manager before submitting leave.'})
    if not manager.user_id or not manager.user.is_active or manager.user_id == employee.user_id:
        raise ValidationError({'manager': 'An active line manager must be assigned before leave can be submitted. Contact HR.'})
    return manager

def notify_employee(request):
    if not request.employee_id:
        return
    from apps.notifications.services import NotificationService
    NotificationService.create_notification(
        recipient=request.employee,
        title='Leave request updated',
        message=f'{request.days_requested} days requested: {request.get_status_display()}.',
        category='APPROVAL', action_url=f'/approvals?leave={request.pk}',
        action_label='View leave request', metadata={'leave_request_id': str(request.pk), 'requires_action': False},
    )


def notify_legacy_hr(request):
    from django.db import transaction
    transaction.on_commit(lambda: _deliver_legacy_hr(request.pk), robust=True)


def _deliver_legacy_hr(request_id):
    from apps.payroll.models import LeaveRequest
    from apps.notifications.services import NotificationService
    request = LeaveRequest.objects.filter(pk=request_id).first()
    if not request:
        return
    for user in active_hr_approvers(request.employee_id):
        if not can_review(request, user):
            continue
        NotificationService.create_notification(
            recipient=user, title='HR leave approval required', category='APPROVAL',
            message=f'Please approve the leave request of {request.employee_name} ? {request.days_requested} days requested.',
            action_url=f'/approvals?leave={request.pk}', action_label='Review leave',
            metadata={'leave_request_id': str(request.pk), 'requires_action': True},
        )


def working_days_by_year(request):
    from datetime import timedelta
    from decimal import Decimal
    days = {}
    current = request.start_date
    while current <= request.end_date:
        if current.weekday() < 5:
            days[current.year] = days.get(current.year, Decimal('0')) + (Decimal('0.5') if request.half_day else Decimal('1'))
        current += timedelta(days=1)
    return days


def validate_annual_balance(request):
    """Reserve pending days without deducting the ledger until final approval."""
    from decimal import Decimal
    from apps.payroll.models import EmployeeLeaveRecord, LeaveRequest
    if request.leave_type.category != 'annual':
        return
    pending = list(LeaveRequest.objects.filter(
        employee_code=request.employee_code, leave_type__category='annual',
        status__in=['PENDING', 'RM_APPROVED'],
    ).exclude(pk=request.pk))
    for year, days in working_days_by_year(request).items():
        record = EmployeeLeaveRecord.objects.select_for_update().filter(employee_code=request.employee_code, year=year).first()
        if not record:
            raise ValidationError({'balance': f'HR must configure the annual leave balance for {year} before submission.'})
        reserved = sum((working_days_by_year(item).get(year, Decimal('0')) for item in pending), Decimal('0'))
        if days > record.leave_balance - reserved:
            raise ValidationError({'balance': f'Insufficient annual leave balance for {year}, including pending requests.'})


def require_approval_route(employee):
    from django.contrib.auth import get_user_model
    from apps.hr_core.models import HRWorkflowDefinition
    definition = HRWorkflowDefinition.objects.filter(code='leave_request_v1', is_active=True).order_by('-version').first()
    stages = list(definition.stages.order_by('sequence')) if definition else []
    if len(stages) != 2 or [(stage.code, stage.approver_type) for stage in stages] != [('manager_review', 'employee_manager'), ('hr_review', 'role')]:
        raise ValidationError({'workflow': 'HR must configure the manager-to-HR leave approval workflow before submission.'})
    if not active_hr_approvers(employee.user_id).exists():
        raise ValidationError({'workflow': 'No active HR approver is assigned. Contact HR to configure leave approvals.'})
