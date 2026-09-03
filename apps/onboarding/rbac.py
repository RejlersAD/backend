"""Soft-coded RBAC policy for employee lifecycle checklist stages."""

from apps.hr_core.models import EmployeeMaster
from apps.rbac.models import UserRole

from .models import (
    CHECKLIST_STAGE_FINAL_VALIDATION,
    CHECKLIST_STAGE_FIRST_DAY,
    CHECKLIST_STAGE_IT_PROVISIONING,
    CHECKLIST_STAGE_PRE_HIRE,
    CHECKLIST_STAGE_EXIT_INITIATION,
    CHECKLIST_STAGE_ACCESS_REVOCATION,
    CHECKLIST_STAGE_ASSET_RETURN,
    CHECKLIST_STAGE_EXIT_CLEARANCE,
    CHECKLIST_STAGE_FINAL_SETTLEMENT,
)


LIFECYCLE_ADMIN_ROLES = {'super_admin', 'admin'}
LIFECYCLE_HR_ROLES = {'hr_admin', 'hr_manager', 'human_resource'}
LIFECYCLE_IT_ROLES = {'ict_admin', 'admin_it'}
LIFECYCLE_MANAGER_ROLES = {'manager', 'project_manager'}
LIFECYCLE_FINANCE_ROLES = {'finance_admin', 'finance_manager', 'payroll_admin'}

ONBOARDING_STAGE_RBAC = {
    CHECKLIST_STAGE_PRE_HIRE: {
        'label': 'Pre-Hire Initiation',
        'roles': LIFECYCLE_ADMIN_ROLES | LIFECYCLE_HR_ROLES,
        'owner_label': 'HR',
    },
    CHECKLIST_STAGE_IT_PROVISIONING: {
        'label': 'IT Provisioning',
        'roles': LIFECYCLE_ADMIN_ROLES | LIFECYCLE_IT_ROLES,
        'start_roles': LIFECYCLE_ADMIN_ROLES | LIFECYCLE_IT_ROLES | LIFECYCLE_HR_ROLES,
        'owner_label': 'ICT',
    },
    CHECKLIST_STAGE_FIRST_DAY: {
        'label': 'First Day Orientation',
        'roles': LIFECYCLE_ADMIN_ROLES | LIFECYCLE_HR_ROLES | LIFECYCLE_MANAGER_ROLES,
        'owner_label': 'HR / Manager',
    },
    CHECKLIST_STAGE_FINAL_VALIDATION: {
        'label': 'Final Checklist Validation',
        'roles': LIFECYCLE_ADMIN_ROLES | LIFECYCLE_HR_ROLES,
        'owner_label': 'HR',
    },
}

OFFBOARDING_STAGE_RBAC = {
    CHECKLIST_STAGE_EXIT_INITIATION: {
        'label': 'Exit Initiation', 'roles': LIFECYCLE_ADMIN_ROLES | LIFECYCLE_HR_ROLES, 'owner_label': 'HR',
    },
    CHECKLIST_STAGE_ACCESS_REVOCATION: {
        'label': 'Access Revocation', 'roles': LIFECYCLE_ADMIN_ROLES | LIFECYCLE_IT_ROLES,
        'start_roles': LIFECYCLE_ADMIN_ROLES | LIFECYCLE_IT_ROLES | LIFECYCLE_HR_ROLES, 'owner_label': 'ICT',
    },
    CHECKLIST_STAGE_ASSET_RETURN: {
        'label': 'Asset Return', 'roles': LIFECYCLE_ADMIN_ROLES | LIFECYCLE_IT_ROLES | LIFECYCLE_HR_ROLES,
        'owner_label': 'ICT / HR',
    },
    CHECKLIST_STAGE_EXIT_CLEARANCE: {
        'label': 'Exit Interview & Clearance', 'roles': LIFECYCLE_ADMIN_ROLES | LIFECYCLE_HR_ROLES,
        'owner_label': 'HR',
    },
    CHECKLIST_STAGE_FINAL_SETTLEMENT: {
        'label': 'Final Settlement', 'roles': LIFECYCLE_ADMIN_ROLES | LIFECYCLE_HR_ROLES | LIFECYCLE_FINANCE_ROLES,
        'owner_label': 'HR / Finance',
    },
}


def get_active_role_codes(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return set()
    return set(UserRole.objects.filter(
        user_profile__user=user,
        user_profile__is_deleted=False,
        role__is_active=True,
    ).values_list('role__code', flat=True))


def can_manage_probation_report(user, employee_user=None):
    """Allow HR/admin roles globally and direct line managers for their reports."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    if get_active_role_codes(user).intersection(LIFECYCLE_ADMIN_ROLES | LIFECYCLE_HR_ROLES):
        return True
    if employee_user is None:
        return False
    return EmployeeMaster.objects.filter(
        user=employee_user,
        manager__user=user,
    ).exists()


def can_manage_onboarding_stage(user, stage, record=None):
    policy = ONBOARDING_STAGE_RBAC.get(stage)
    if not policy or not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True

    role_codes = get_active_role_codes(user)
    if role_codes.intersection(policy['roles']):
        return True
    if record and record.assigned_to_id == user.id:
        return True
    if stage == CHECKLIST_STAGE_FIRST_DAY and record and record.user_id:
        return EmployeeMaster.objects.filter(
            user_id=record.user_id,
            manager__user_id=user.id,
        ).exists()
    return False


def can_start_onboarding_stage(user, stage, record=None):
    policy = ONBOARDING_STAGE_RBAC.get(stage)
    if not policy or not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    if get_active_role_codes(user).intersection(policy.get('start_roles', policy['roles'])):
        return True
    return bool(record and record.assigned_to_id == user.id)


def onboarding_stage_permissions(user, record=None):
    authenticated = bool(user and getattr(user, 'is_authenticated', False))
    is_superuser = authenticated and getattr(user, 'is_superuser', False)
    role_codes = get_active_role_codes(user) if authenticated and not is_superuser else set()
    is_assignee = bool(record and authenticated and record.assigned_to_id == user.id)
    is_reporting_manager = bool(
        record and authenticated and record.user_id
        and EmployeeMaster.objects.filter(
            user_id=record.user_id,
            manager__user_id=user.id,
        ).exists()
    )
    return {
        stage: {
            'can_manage': bool(
                is_superuser
                or role_codes.intersection(policy['roles'])
                or is_assignee
                or (stage == CHECKLIST_STAGE_FIRST_DAY and is_reporting_manager)
            ),
            'can_start': bool(
                is_superuser
                or role_codes.intersection(policy.get('start_roles', policy['roles']))
                or is_assignee
            ),
            'owner_label': policy['owner_label'],
            'label': policy['label'],
        }
        for stage, policy in ONBOARDING_STAGE_RBAC.items()
    }


def can_manage_offboarding(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    return bool(get_active_role_codes(user).intersection(
        LIFECYCLE_ADMIN_ROLES | LIFECYCLE_HR_ROLES
    ))


def can_manage_offboarding_stage(user, stage, record=None):
    policy = OFFBOARDING_STAGE_RBAC.get(stage)
    if not policy or not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    if get_active_role_codes(user).intersection(policy['roles']):
        return True
    return bool(record and record.assigned_to_id == user.id)


def can_start_offboarding_stage(user, stage, record=None):
    policy = OFFBOARDING_STAGE_RBAC.get(stage)
    if not policy or not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    if get_active_role_codes(user).intersection(policy.get('start_roles', policy['roles'])):
        return True
    return bool(record and record.assigned_to_id == user.id)


def offboarding_stage_permissions(user, record=None):
    return {
        stage: {
            'can_manage': can_manage_offboarding_stage(user, stage, record),
            'can_start': can_start_offboarding_stage(user, stage, record),
            'owner_label': policy['owner_label'],
            'label': policy['label'],
        }
        for stage, policy in OFFBOARDING_STAGE_RBAC.items()
    }
