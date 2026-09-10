"""Leave-specific routing and authorization shared by queues and decisions."""
from django.db.models import Q
from rest_framework.exceptions import ValidationError
from apps.hr_core.models import EmployeeMaster
from apps.hr_core.workflows import HRWorkflowService

HR_ROLES = ('hr_manager', 'hr_admin', 'super_admin', 'superadmin', 'admin')

def is_hr(user):
    return user.is_superuser or bool(set(HR_ROLES) & HRWorkflowService._role_codes(user))

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
    employee = employee_for_request(request)
    if request.employee_id == user.pk or (employee and employee.user_id == user.pk):
        return False
    if request.status not in ('PENDING', 'RM_APPROVED'):
        return False
    if request.workflow_instance_id and request.workflow_instance.status != 'pending':
        return False
    manager = manager_for_request(request)
    if request.status == 'PENDING' and manager:
        return bool(manager.user_id == user.pk and user.is_active)
    if not is_hr(user):
        return False
    if request.workflow_instance_id:
        instance = request.workflow_instance
        if instance.status != 'pending':
            return False
        stage = instance.definition.stages.filter(code='hr_review').first()
        return bool(stage and (user.is_superuser or stage.approver_value in HRWorkflowService._role_codes(user)))
    return True


def prepare_review_workflow(request):
    """Reconcile stale tasks on a permitted decision; caller holds the request lock."""
    if not request.workflow_instance_id:
        return
    from apps.hr_core.models import HRWorkflowInstance, HRWorkflowEvent
    instance = HRWorkflowInstance.objects.select_for_update().get(pk=request.workflow_instance_id)
    if instance.status != 'pending':
        raise ValidationError({'workflow': 'This workflow is no longer pending.'})
    manager = manager_for_request(request)
    target = 'manager_review' if request.status == 'PENDING' and manager else 'hr_review'
    if target == 'hr_review' and instance.current_stage.code == 'manager_review':
        instance.tasks.filter(status='pending').update(status='cancelled', decision_note='No manager assigned; routed directly to HR.')
        instance.current_stage = instance.definition.stages.get(code='hr_review')
        instance.save(update_fields=['current_stage', 'updated_at'])
        HRWorkflowEvent.objects.create(instance=instance, event_type='manager_skipped', stage_code='manager_review', metadata={'reason': 'No assigned manager'})
        HRWorkflowService._create_task(instance, instance.current_stage)
    elif target == 'manager_review':
        instance.tasks.filter(status='pending', stage__code='manager_review').update(assigned_to_id=manager.user_id, assigned_role_code='')
    request.workflow_instance = instance


def require_manager(employee):
    if not employee:
        raise ValidationError({'employee': 'An employee master record is required. Ask HR to complete your employee profile.'})
    manager = manager_for_employee(employee)
    if manager is None:
        return None
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
        action_label='View leave request', metadata={'leave_request_id': str(request.pk)},
    )


def notify_legacy_hr(request):
    from django.contrib.auth import get_user_model
    from apps.notifications.services import NotificationService
    for user in get_user_model().objects.filter(rbac_profile__roles__code__in=HR_ROLES, rbac_profile__roles__is_active=True, is_active=True).exclude(pk=request.employee_id).distinct():
        NotificationService.create_notification(
            recipient=user, title='HR leave approval required', category='APPROVAL',
            message=f'Please approve the leave request of {request.employee_name} ? {request.days_requested} days requested.',
            action_url=f'/approvals?leave={request.pk}', action_label='Review leave',
            metadata={'leave_request_id': str(request.pk)},
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
    if not get_user_model().objects.filter(is_active=True, rbac_profile__roles__code=stages[1].approver_value, rbac_profile__roles__is_active=True).exclude(pk=employee.user_id).exists():
        raise ValidationError({'workflow': 'No active HR approver is assigned. Contact HR to configure leave approvals.'})
