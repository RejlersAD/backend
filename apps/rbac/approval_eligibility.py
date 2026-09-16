"""Business approval eligibility, independent of broad application access.

An effective RBAC grant is necessary, but never supplies a business assignment
or advances a workflow. Resolve positions only from the canonical employee
record maintained by HR; display profiles and access-role names are not proof.
"""
import re
import json
import os
from functools import wraps

from django.contrib.auth import get_user_model
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from .action_policy import module_action_allowed
from .organization_catalog import ORGANIZATIONAL_ROLES


def _title(value):
    value = str(value or '').casefold().replace('&', ' and ')
    value = re.sub(r'\bacting\b|\brejlers abu dhabi\b', ' ', value)
    return re.sub(r'[^a-z0-9]+', ' ', value).strip()


# Explicit title aliases for existing business stages, not permission roles.
# Generic Admin, Manager and VP deliberately confer no unrelated authority.
POSITION_TITLES = {
    role['code']: {role['label']} for role in ORGANIZATIONAL_ROLES
}
POSITION_TITLES.update({
    'hr_manager': {'HR Manager', 'Human Resources Manager', 'Head of Human Resources',
                   'Head of HR', 'Head of HR and Administration'},
    'hr_admin': {'HR Administrator', 'Human Resources Administrator', 'HR Admin',
                 'Human Resources Admin', 'HR and Administration Manager'},
    'hr_coordinator': {'HR Coordinator', 'Human Resources Coordinator'},
    'administration_manager': {'Administration Manager', 'Administrative Manager',
                               'Head of Administration', 'Head of HR and Administration'},
    'administrator': {'Administrator', 'Office Administrator', 'Office Admin'},
    'finance_manager': {'Finance Manager', 'Financial Manager', 'Head of Finance',
                        'Head of Finance and ICT', 'VP and Head of Finance and ICT',
                        'CFO', 'Chief Financial Officer'},
    'finance_admin': {'Finance Administrator', 'Finance Admin', 'Financial Administrator'},
    'accounting_manager': {'Accounting Manager', 'Accounts Manager', 'Chief Accountant'},
    'accountant': {'Accountant', 'Senior Accountant', 'Accounts Officer', 'Accounting Officer'},
    'payroll_admin': {'Payroll Administrator', 'Payroll Admin', 'Payroll Manager', 'Payroll Officer'},
    'ict_admin': {'ICT Administrator', 'IT Administrator', 'ICT Admin', 'IT Admin',
                  'ICT Manager', 'IT Manager', 'Head of ICT', 'Head of IT'},
    'project_manager': {'Project Manager', 'Senior Project Manager', 'Project Director'},
    'procurement_manager': {'Procurement Manager', 'Head of Procurement'},
    'procurement_officer': {'Procurement Officer', 'Procurement Specialist', 'Procurement Engineer',
                            'Purchasing Officer', 'Buyer'},
    'engineering_manager': {'Engineering Manager', 'Manager of Engineering'},
    'manager_engineering': {'Manager of Engineering', 'Engineering Manager'},
    'manager_projects': {'Manager of Projects', 'Projects Manager', 'Head of Projects'},
    'head_operations_project_delivery': {'Head of Operations and Project Delivery',
                                         'VP Operations', 'VP Delivery', 'Vice President Operations',
                                         'Vice President Project Delivery'},
    'ceo': {'CEO', 'Chief Executive Officer'},
})
POSITION_GROUPS = {
    'human_resource': ('hr_manager', 'hr_admin', 'hr_coordinator', 'head_hr_administration'),
    'hr': ('hr_manager', 'hr_admin', 'head_hr_administration'),
    'finance': ('finance_manager', 'finance_admin', 'accounting_manager', 'accountant', 'cfo'),
    'accounting': ('accounting_manager', 'accountant'),
    'procurement': ('procurement_manager', 'procurement_officer'),
    'engineering': ('manager_engineering', 'engineering_manager', 'engineer', 'designer',
                    'hod_process', 'hod_civil_structural', 'hod_instrumentation_control',
                    'hod_electrical', 'hod_piping_mechanical_pipeline'),
    'operations': ('head_operations_project_delivery',),
    'project_controls': ('senior_manager_project_controls', 'project_manager', 'manager_projects'),
}


def active_approval_user(user):
    if not user or not user.is_authenticated or not user.is_active:
        return False
    from .models import UserProfile
    profile = UserProfile.objects.filter(user_id=user.pk, user__is_active=True,
                                         is_deleted=False, status='active').first()
    return bool(profile and not (profile.locked_until and profile.locked_until > timezone.now()))


def approval_access(user, module):
    """Require a current, positive effective approve grant; never absence of deny."""
    if not user or not user.is_authenticated or not getattr(user, 'pk', None):
        return False
    current = get_user_model().objects.filter(pk=user.pk, is_active=True).select_related('rbac_profile').first()
    return bool(current and module and module_action_allowed(current, module, 'approve'))


def canonical_employee(user):
    if not active_approval_user(user):
        return None
    from apps.hr_core.models import EmployeeMaster
    return EmployeeMaster.objects.filter(
        user_id=user.pk, employment_status__in=('active', 'probation', 'notice_period'),
    ).first()


def _position_titles(positions):
    values = (positions,) if isinstance(positions, str) else (positions or ())
    titles = set()
    for value in values:
        key = str(value or '').strip()
        if key in POSITION_GROUPS:
            titles.update(_position_titles(POSITION_GROUPS[key]))
        else:
            titles.update(_title(title) for title in POSITION_TITLES.get(key, (key,)))
    return titles - {''}


def has_business_position(user, positions):
    """Match the official designation, not a profile label or RBAC role.

    Secondary country titles only fill a missing designation; they must not
    restore obsolete authority after a person's primary position changes.
    """
    employee = canonical_employee(user)
    if employee is None:
        return False
    designation = employee.designation or employee.job_title_uae or employee.job_title_finland
    return bool(_title(designation) in _position_titles(positions))


def position_users(positions, module):
    User = get_user_model()
    candidates = User.objects.filter(is_active=True, employee_master__isnull=False)
    ids = [user.pk for user in candidates if has_business_position(user, positions)
           and approval_access(user, module)]
    return User.objects.filter(pk__in=ids)


def eligible_approver(user, module, *, assigned, current, positions=None):
    return bool(assigned and current and approval_access(user, module)
                and (positions is None or has_business_position(user, positions)))


def require_approval(user, module, *, assigned, current, positions=None):
    if not approval_access(user, module):
        raise PermissionDenied('Your current access does not permit this approval action.')
    if not assigned or (positions is not None and not has_business_position(user, positions)):
        raise PermissionDenied('You do not hold the designated approval position or assignment for this request.')
    if not current:
        raise ValidationError({'approval': 'This request is not pending at your approval stage.'})


def project_approval_assignment(user, project):
    """A current, controlled project responsibility; no administrator shortcut."""
    if not active_approval_user(user) or project is None:
        return False
    return bool(project.owner_id == user.pk or project.memberships.filter(
        user_id=user.pk, is_active=True, role='project_manager',
    ).exists())


PROFILE_REVIEW_POSITIONS = ('hr_manager', 'hr_admin', 'hr_coordinator', 'head_hr_administration', 'administrator')


def can_review_profile_document(user, document=None):
    return eligible_approver(
        user, 'user_mgmt', assigned=has_business_position(user, PROFILE_REVIEW_POSITIONS),
        current=document is None or (document.verification_status == 'pending'
                                    and document.is_active
                                    and document.user_profile.user_id != user.pk),
    )


def self_organization_changes(profile, data):
    """Self-service cannot alter facts used to decide business authority."""
    current = {'department': profile.department, 'job_title': profile.job_title,
               'manager_id': profile.manager_id, 'manager': profile.manager_id}
    return [key for key, value in current.items() if key in data
            and str(data[key] or '') != str(value or '')]


def require_configured_approval(user, module, obj, operation):
    """Older single-step modules need an explicit business route before use.

    Routes are deployment-controlled policy, never accepted from request data.
    Example key: ``electrical_datasheet.ElectricalDatasheet.approve``.
    No policy means no decision, including for administrators.
    """
    routes = getattr(settings, 'RADAI_BUSINESS_APPROVAL_ROUTES', None)
    if routes is None:
        try:
            routes = json.loads(os.environ.get('RADAI_BUSINESS_APPROVAL_ROUTES', '{}'))
        except (TypeError, ValueError):
            routes = {}
    key = f'{module}.{type(obj).__name__}.{operation}'
    route = routes.get(key) if isinstance(routes, dict) else None
    if not isinstance(route, dict) or not route.get('positions') or not route.get('pending_states'):
        raise PermissionDenied(f'No business approval route is configured for {key}.')
    positions = route['positions']
    states = route['pending_states']
    if not isinstance(positions, list) or not isinstance(states, list):
        raise PermissionDenied('The business approval route is invalid.')
    assigned = has_business_position(user, positions)
    assignee_field = route.get('assignee_field')
    if assignee_field:
        assigned = assigned and str(getattr(obj, assignee_field, '')) == str(user.pk)
    department_field = route.get('department_field')
    if department_field:
        employee = canonical_employee(user)
        target = str(getattr(obj, department_field, '') or '').strip().casefold()
        assigned = bool(assigned and employee and target
                        and target == employee.department.strip().casefold())
    require_approval(user, module, assigned=assigned,
                     current=getattr(obj, route.get('state_field', 'status'), None) in states)
    if route.get('submitter_field') and str(getattr(obj, route['submitter_field'], '')) == str(user.pk):
        raise PermissionDenied('The submitter cannot approve their own request.')


def guarded_business_approval(module, *, reporting_manager=False):
    """Bind legacy detail commands to a locked, explicit business route."""
    def decorate(command):
        @wraps(command)
        def guarded(view, request, *args, **kwargs):
            with transaction.atomic():
                obj = view.get_object()
                obj = type(obj).objects.select_for_update().get(pk=obj.pk)
                if reporting_manager:
                    from apps.hr_core.models import EmployeeMaster
                    from apps.payroll.services.leave_approval import manager_for_employee
                    employee_id = getattr(obj, 'employee_id', None)
                    employee = EmployeeMaster.objects.filter(user_id=employee_id).first() if employee_id else None
                    manager = manager_for_employee(employee, employee_id) if employee_id else None
                    require_approval(request.user, module,
                                     assigned=bool(manager and manager.user_id == request.user.pk
                                                   and request.user.pk != employee_id),
                                     current=getattr(obj, 'status', None) == 'PENDING')
                else:
                    require_configured_approval(request.user, module, obj, command.__name__)
                response = command(view, request, *args, **kwargs)
                if response.status_code >= 400:
                    transaction.set_rollback(True)
                return response
        return guarded
    return decorate
