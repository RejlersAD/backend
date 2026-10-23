"""Leave-specific routing and authorization shared by queues and decisions."""
import logging

from decouple import config
from django.db.models import Q
from rest_framework.exceptions import ValidationError
from apps.hr_core.models import EmployeeMaster
from apps.hr_core.workflows import HRWorkflowService
from apps.rbac.approval_eligibility import approval_access, has_business_position, position_users

logger = logging.getLogger(__name__)

HR_ROLES = ('hr_manager', 'hr_admin', 'super_admin', 'superadmin', 'admin')
HR_APPROVAL_POSITIONS = ('hr_manager', 'hr_admin', 'head_hr_administration')

# SOFT-CODED fallback (env: LEAVE_HR_APPROVER_ROLE_FALLBACK, default on).
# When NO active user currently holds an HR approval designation (EmployeeMaster
# designation) with a payroll approve grant, leave submission dead-blocks with
# "No active HR approver is assigned". The fallback promotes active HR-role /
# superuser accounts with payroll approve access so leave keeps flowing while
# HR completes the designation assignment. Set the env var to false to restore
# strict designation-only routing.
LEAVE_HR_APPROVER_ROLE_FALLBACK = config('LEAVE_HR_APPROVER_ROLE_FALLBACK', default=True, cast=bool)

def is_hr(user):
    return bool(user.is_active and (user.is_superuser or set(HR_ROLES) & HRWorkflowService._role_codes(user)))


def _role_based_hr_approvers():
    """Active HR-role / superuser accounts with payroll approve access."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    ids = [u.pk for u in User.objects.filter(is_active=True)
           if (u.is_superuser or is_hr(u)) and approval_access(u, 'payroll')]
    if not ids:
        # Last resort: any active superuser, so submission is never dead-blocked
        ids = list(User.objects.filter(is_active=True, is_superuser=True).values_list('pk', flat=True))
    return User.objects.filter(pk__in=ids)


def active_hr_approvers(employee_user_id=None):
    approvers = position_users(HR_APPROVAL_POSITIONS, 'payroll')
    if not approvers.exists() and LEAVE_HR_APPROVER_ROLE_FALLBACK:
        logger.warning(
            'Leave approvals: no designation-matched HR approver found — '
            'using role-based fallback (set LEAVE_HR_APPROVER_ROLE_FALLBACK=false to disable)'
        )
        approvers = _role_based_hr_approvers()
    return approvers.exclude(pk=employee_user_id)


def is_hr_approver(user):
    """Designation-based HR approver, with the soft-coded role fallback."""
    if has_business_position(user, HR_APPROVAL_POSITIONS):
        return True
    if not LEAVE_HR_APPROVER_ROLE_FALLBACK:
        return False
    return bool(
        user and user.is_active
        and (user.is_superuser or is_hr(user))
        and approval_access(user, 'payroll')
    )


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
    if request.selected_manager_id:
        return request.selected_manager
    return manager_for_employee(employee_for_request(request), request.employee_id)


def task_was_referred(task):
    return task.instance.events.filter(event_type='referred', stage_code=task.stage.code).exists()


def referral_eligible(request, user):
    employee = employee_for_request(request)
    if not approval_access(user, 'payroll') or user.pk == request.employee_id or (employee and employee.user_id == user.pk):
        return False
    stage = request.workflow_instance.current_stage if request.workflow_instance_id else None
    return bool(stage and (stage.code != 'hr_review' or is_hr_approver(user)))


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
        if task_was_referred(task):
            return task.assigned_to_id == user.pk and referral_eligible(request, user)
        if request.leave_type.code == 'FU':
            if task.stage.code == 'hr_review':
                return is_hr_approver(user) and request.status == 'RM_APPROVED'
            return bool(task.stage.approver_type == 'user' and task.assigned_to_id == user.pk and user.is_active)
    manager = manager_for_request(request)
    if request.status == 'PENDING' and manager:
        if request.workflow_instance_id and request.workflow_instance.current_stage.code != 'manager_review':
            return False
        return bool(manager.user_id == user.pk and user.is_active)
    if request.status == 'PENDING' or not is_hr_approver(user):
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
    task = instance.tasks.filter(stage=instance.current_stage, status='pending').first()
    if task and task_was_referred(task):
        request.workflow_instance = instance
        return
    if request.leave_type.code == 'FU':
        request.workflow_instance = instance
        return
    manager = manager_for_request(request)
    if request.status == 'PENDING' and not manager:
        raise ValidationError({'manager': 'Configure the reporting manager before review.'})
    if request.status == 'PENDING' and instance.current_stage.code == 'manager_review':
        instance.tasks.filter(status='pending', stage__code='manager_review').update(assigned_to_id=manager.user_id, assigned_role_code='')
    request.workflow_instance = instance


def require_manager(employee, selected_manager=None):
    if not employee:
        raise ValidationError({'employee': 'An employee master record is required. Ask HR to complete your employee profile.'})
    manager = manager_for_employee(employee)
    if selected_manager:
        if manager is not None or not allows_manual_manager(employee):
            raise ValidationError({'selected_manager': 'Manual selection is only available to Sales/Finance employees without an assigned manager.'})
        manager = selected_manager
        if manager.employment_status not in {'active', 'probation', 'notice_period'}:
            raise ValidationError({'selected_manager': 'Select an active employee as the approving manager.'})
    if manager is None:
        raise ValidationError({'manager': 'Assign an active reporting manager before submitting leave.'})
    if not manager.user_id or not manager.user.is_active or manager.user_id == employee.user_id:
        raise ValidationError({'manager': 'An active line manager must be assigned before leave can be submitted. Contact HR.'})
    return manager


def allows_manual_manager(employee):
    department = (employee.department or employee.business_area or '').strip().casefold()
    return department in {'sales', 'finance', 'finance & it', 'business development & sales', 'business development and sales'}


def validate_leave_eligibility(employee, leave_type):
    if leave_type.code == 'NSL' and (not employee or employee.nationality.strip().casefold() not in {
        'ae', 'are', 'uae', 'emirati', 'united arab emirates', 'uae national',
    }):
        raise ValidationError({'leave_type': 'National Service Leave is available only to employees with verified UAE nationality. Ask HR to update nationality.'})


def workflow_code(leave_type):
    return 'flex_utilization_v1' if leave_type.code == 'FU' else 'leave_request_v1'


HR_OVERRIDE_FIELDS = {'approved_start_date', 'approved_end_date', 'allow_negative_balance', 'override_reason'}


def prepare_hr_approval(request, payload, actor):
    """Validate final HR dates and an explicit balance exception under employee/ledger locks."""
    from decimal import Decimal
    from rest_framework import serializers
    from apps.payroll.models import LeaveRequest
    employee = employee_for_request(request)
    if employee:
        employee = EmployeeMaster.objects.select_for_update().get(pk=employee.pk)
    validate_leave_eligibility(employee, request.leave_type)
    original_start, original_end = request.start_date, request.end_date
    request.requested_start_date = request.requested_start_date or original_start
    request.requested_end_date = request.requested_end_date or original_end
    if request.requested_days is None:
        request.requested_days = request.days_requested
    request.start_date = serializers.DateField().run_validation(payload.get('approved_start_date', original_start))
    request.end_date = serializers.DateField().run_validation(payload.get('approved_end_date', original_end))
    allow_negative = serializers.BooleanField().run_validation(payload.get('allow_negative_balance', False))
    changed = (request.start_date, request.end_date) != (original_start, original_end)
    reason = str(payload.get('override_reason', '')).strip()
    if (changed or allow_negative) and not reason:
        raise ValidationError({'override_reason': 'HR must record a reason for changing approved dates or allowing a negative balance.'})
    if request.end_date < request.start_date or (request.half_day and request.start_date != request.end_date):
        raise ValidationError({'approved_end_date': 'Select a valid approved date range; half-day leave must cover one working day.'})
    days = working_days_by_year(request)
    request.days_requested = sum(days.values(), Decimal('0'))
    if request.days_requested <= 0:
        raise ValidationError({'approved_start_date': 'The approved dates contain no working days.'})
    identity = Q(canonical_employee_id=request.canonical_employee_id) if request.canonical_employee_id else Q(employee_code=request.employee_code)
    if changed and LeaveRequest.objects.filter(identity, status__in=['PENDING', 'RM_APPROVED', 'APPROVED'],
            start_date__lte=request.end_date, end_date__gte=request.start_date).exclude(pk=request.pk).exists():
        raise ValidationError({'approved_start_date': 'The approved dates overlap another pending or approved request.'})
    if allow_negative and request.leave_type.category != 'annual':
        raise ValidationError({'allow_negative_balance': 'The balance exception applies to annual leave only.'})
    validate_annual_balance(request, allow_negative=allow_negative)
    request.hr_balance_override = allow_negative
    request.hr_override_reason = reason if changed or allow_negative else ''
    if (changed or allow_negative) and request.workflow_instance_id:
        from apps.hr_core.models import HRWorkflowEvent
        HRWorkflowEvent.objects.create(instance=request.workflow_instance, actor=actor,
            event_type='hr_leave_override', stage_code='hr_review', note=reason,
            metadata={'requested_start_date': str(request.requested_start_date),
                'requested_end_date': str(request.requested_end_date), 'requested_days': str(request.requested_days),
                'approved_start_date': str(request.start_date), 'approved_end_date': str(request.end_date),
                'approved_days': str(request.days_requested), 'allow_negative_balance': allow_negative})

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


def validate_annual_balance(request, allow_negative=False):
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
        if not allow_negative and days > record.leave_balance - reserved:
            raise ValidationError({'balance': f'Insufficient annual leave balance for {year}, including pending requests.'})


def require_approval_route(employee, leave_type=None):
    from django.contrib.auth import get_user_model
    from apps.hr_core.models import HRWorkflowDefinition
    code = workflow_code(leave_type) if leave_type else 'leave_request_v1'
    definition = HRWorkflowDefinition.objects.filter(code=code, is_active=True).order_by('-version').first()
    stages = list(definition.stages.order_by('sequence')) if definition else []
    expected = [('jemal_review', 'user'), ('rafat_review', 'user'), ('mohammed_review', 'user'), ('hr_review', 'role')] if code == 'flex_utilization_v1' else [('manager_review', 'employee_manager'), ('hr_review', 'role')]
    if [(stage.code, stage.approver_type) for stage in stages] != expected:
        raise ValidationError({'workflow': 'HR must configure the manager-to-HR leave approval workflow before submission.'})
    if code == 'flex_utilization_v1':
        from django.contrib.auth import get_user_model
        ids = []
        for stage in stages[:-1]:
            user = get_user_model().objects.filter(pk=stage.approver_value).first() if str(stage.approver_value).isdigit() else None
            if not user or user.pk == employee.user_id or not approval_access(user, 'payroll'):
                raise ValidationError({'workflow': 'Configure active, distinct Jemal, Rafat and Mohammed approvers with payroll approval access.'})
            ids.append(user.pk)
        if len(set(ids)) != 3:
            raise ValidationError({'workflow': 'The three FU approvers must be distinct accounts.'})
    if not active_hr_approvers(employee.user_id).exists():
        raise ValidationError({'workflow': 'No active HR approver is assigned. Contact HR to configure leave approvals.'})


def notify_department_head(request):
    """Informational in-app notification, delivered only after final commit."""
    import json
    from django.db import transaction
    from django.contrib.auth import get_user_model
    from apps.notifications.services import NotificationService
    from apps.hr_core.models import HRWorkflowDefinition
    definition = HRWorkflowDefinition.objects.filter(code='leave_request_v1', is_active=True).order_by('-version').first()
    mapping = (definition.configuration or {}).get('department_heads', {}) if definition else {}
    try:
        mapping = mapping or json.loads(config('LEAVE_DEPARTMENT_HEADS', default='{}'))
    except (ValueError, TypeError):
        logger.error('Invalid LEAVE_DEPARTMENT_HEADS configuration; no recipient selected')
        return
    if not isinstance(mapping, dict):
        logger.error('Department-head configuration must be a department-to-email mapping')
        return
    email = next((value for department, value in mapping.items()
                  if isinstance(department, str) and department.strip().casefold() == request.department.strip().casefold()), None)
    recipient = None
    if email:
        matches = list(get_user_model().objects.filter(email__iexact=email, is_active=True)[:2])
        recipient = matches[0] if len(matches) == 1 else None
    else:
        # The catalog identifies the department's head position. Resolve its
        # actual current holder from employee records, never the chart's sample names.
        from apps.rbac.organization_catalog import DEPARTMENTS, resolve_department_code
        code = resolve_department_code(request.department)
        employee = employee_for_request(request)
        area = employee.business_area if employee else ''
        code = code or resolve_department_code(area) or {
            'Business Development & Sales': 'sales', 'Finance & IT': 'finance', 'Project Delivery': 'operations',
        }.get(area)
        role = next((item['head_role_code'] for item in DEPARTMENTS if item['code'] == code), None)
        if role:
            candidates = [user for user in get_user_model().objects.filter(is_active=True, employee_master__isnull=False)
                          if has_business_position(user, (role,))]
            recipient = candidates[0] if len(candidates) == 1 else None
    from apps.hr_core.models import HRWorkflowEvent
    event = HRWorkflowEvent.objects.create(instance=request.workflow_instance, event_type='department_head_notice',
        stage_code='hr_review', metadata={'status': 'scheduled' if recipient else 'unconfigured',
                                         'recipient_id': recipient.pk if recipient else None}) if request.workflow_instance_id else None
    if not recipient:
        logger.warning('Leave department-head recipient is missing or ambiguous for department %s', request.department)
        return
    if recipient:
        def deliver():
            try:
                NotificationService.create_notification(
                    recipient=recipient, title='Leave approved — for information', category='APPROVAL',
                    message=f'{request.employee_name}: {request.leave_type.name}, {request.start_date} to {request.end_date}, {request.days_requested} days. HR final approval recorded.',
                    action_url='/hr/leave', action_label='Leave calendar',
                    metadata={'leave_request_id': str(request.pk), 'requires_action': False})
                if event:
                    HRWorkflowEvent.objects.create(instance=request.workflow_instance, event_type='department_head_notice_result',
                        stage_code='hr_review', metadata={'status': 'created', 'recipient_id': recipient.pk, 'source_event_id': str(event.pk)})
            except Exception:
                if event:
                    HRWorkflowEvent.objects.create(instance=request.workflow_instance, event_type='department_head_notice_result',
                        stage_code='hr_review', metadata={'status': 'failed', 'recipient_id': recipient.pk, 'source_event_id': str(event.pk)})
                logger.exception('Unable to create department-head leave notification')
        transaction.on_commit(deliver, robust=True)
