"""Soft-coded RBAC policy for employee lifecycle checklist stages."""

from apps.hr_core.models import EmployeeMaster
from apps.rbac.models import UserRole
from apps.rbac.approval_eligibility import approval_access, has_business_position
from apps.rbac.action_policy import module_action_allowed
from django.contrib.auth import get_user_model

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
ONBOARDING_MANAGEMENT_MODULES = ('hr_onboarding', 'hr_management')


def onboarding_action_allowed(user, action):
    """Use current HR action grants, including custom roles and user overrides.

    Onboarding is managed through either the lifecycle module or the HR module.
    Explicit denials for the requested action win over either positive grant.
    Checklist work is an Edit action; it is not a separate business approval.
    """
    if not user or not getattr(user, 'is_authenticated', False) or not getattr(user, 'pk', None):
        return False
    current = get_user_model().objects.filter(pk=user.pk, is_active=True).select_related('rbac_profile').first()
    if current is None:
        return False
    from apps.rbac.models import UserPermissionOverride
    if UserPermissionOverride.objects.filter(
        user_profile__user=current,
        permission__module__code__in=ONBOARDING_MANAGEMENT_MODULES,
        permission__action=action, permission__is_active=True, allowed=False,
    ).exists():
        return False
    return any(module_action_allowed(current, module, action) for module in ONBOARDING_MANAGEMENT_MODULES)

ONBOARDING_STAGE_RBAC = {
    CHECKLIST_STAGE_PRE_HIRE: {
        'label': 'Pre-Hire Initiation',
        'roles': LIFECYCLE_ADMIN_ROLES | LIFECYCLE_HR_ROLES,
        'owner_label': 'HR',
    },
    CHECKLIST_STAGE_IT_PROVISIONING: {
        'label': 'IT Provisioning',
        'roles': LIFECYCLE_ADMIN_ROLES | LIFECYCLE_IT_ROLES | LIFECYCLE_HR_ROLES,
        'start_roles': LIFECYCLE_ADMIN_ROLES | LIFECYCLE_IT_ROLES | LIFECYCLE_HR_ROLES,
        'owner_label': 'HR / ICT',
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


def _stage_unavailable_reason(record, stage, policies):
    if record is None:
        return 'Select an employee lifecycle record to work on this stage.'
    if record.status in {'completed', 'cancelled', 'rejected'}:
        return 'This workflow is closed. Its checklist stages are read-only.'
    stages = list(policies)
    if stage not in stages:
        return 'This checklist stage is not available for this workflow.'
    for prior in stages[:stages.index(stage)]:
        items = record.checklist_items.filter(stage=prior)
        if not items.exists() or items.filter(completed=False).exists():
            return f"Complete {policies[prior]['label']} before starting this stage."
    if policies is OFFBOARDING_STAGE_RBAC and stage != CHECKLIST_STAGE_EXIT_INITIATION:
        if record.project_manager_approval_status not in {'approved', 'not_required'}:
            return 'Project manager approval is required before continuing this exit workflow.'
        if record.exit_approvals.exclude(status='approved').exists():
            return 'Complete the pending exit approvals before continuing this workflow.'
    return None


def _stage_eligible(user, stage, record, policies, *, starting=False):
    if policies is ONBOARDING_STAGE_RBAC:
        return bool(onboarding_action_allowed(user, 'update')
                    and not _stage_unavailable_reason(record, stage, policies))
    if not approval_access(user, 'hr_onboarding') or _stage_unavailable_reason(record, stage, policies):
        return False
    policy = policies[stage]
    stage_roles = policy.get('start_roles', policy['roles']) if starting else policy['roles']
    positions = stage_roles - LIFECYCLE_ADMIN_ROLES - LIFECYCLE_MANAGER_ROLES
    # A lifecycle duty role is an explicit assignment, regardless of job title.
    # Broad administrators and manager roles still need a business assignment;
    # they do not acquire HR, ICT, or Finance ownership through app access alone.
    if get_active_role_codes(user).intersection(positions):
        return True
    if has_business_position(user, positions):
        return True
    return False


def can_manage_onboarding_stage(user, stage, record=None):
    return _stage_eligible(user, stage, record, ONBOARDING_STAGE_RBAC)


def can_start_onboarding_stage(user, stage, record=None):
    return _stage_eligible(user, stage, record, ONBOARDING_STAGE_RBAC, starting=True)


def _stage_permissions(user, record, policies):
    result = {}
    for stage, policy in policies.items():
        can_manage = _stage_eligible(user, stage, record, policies)
        can_start = _stage_eligible(user, stage, record, policies, starting=True)
        disabled_reason = None
        if not can_manage and not can_start:
            disabled_reason = _stage_unavailable_reason(record, stage, policies)
            if not disabled_reason:
                disabled_reason = ('HR Edit permission is required to manage this onboarding stage.'
                    if policies is ONBOARDING_STAGE_RBAC else (
                    "Your current access does not allow changes to this stage. "
                    f"This stage is managed by {policy['owner_label']}."
                ))
        result[stage] = {
            'can_manage': can_manage,
            'can_start': can_start,
            'owner_label': policy['owner_label'],
            'label': policy['label'],
            'disabled_reason': disabled_reason,
        }
    return result


def onboarding_stage_permissions(user, record=None):
    return _stage_permissions(user, record, ONBOARDING_STAGE_RBAC)


def can_manage_offboarding(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    return bool(get_active_role_codes(user).intersection(
        LIFECYCLE_ADMIN_ROLES | LIFECYCLE_HR_ROLES
    ))


def can_manage_offboarding_stage(user, stage, record=None):
    return _stage_eligible(user, stage, record, OFFBOARDING_STAGE_RBAC)


def can_start_offboarding_stage(user, stage, record=None):
    return _stage_eligible(user, stage, record, OFFBOARDING_STAGE_RBAC, starting=True)


def can_decide_exit_project(record, user):
    if not approval_access(user, 'hr_onboarding') or record.status in {'completed', 'cancelled', 'rejected'}:
        return False
    if record.project_manager_approval_status != 'pending' or record.user_id == user.pk:
        return False
    from .project_assignments import get_active_project_assignments
    managers = {manager.pk for project in get_active_project_assignments(record.user)
                if project['source'] == 'core_project' for manager in project['managers']}
    if user.pk not in managers:
        return False
    previous = record.exit_approvals.filter(approval_step='project_manager', approver=user).first()
    return previous is None or previous.status == 'pending'


def offboarding_stage_permissions(user, record=None):
    return _stage_permissions(user, record, OFFBOARDING_STAGE_RBAC)
