"""Employee assignments and role-specific eligibility for procurement decisions."""

import re

from apps.rbac.approval_eligibility import approval_access, has_business_position
from apps.rbac.action_policy import record_workflow_not_denied
from apps.rbac.organization_catalog import ORGANIZATIONAL_ROLES


MODULE_PR = 'procurement_requisitions'
MODULE_PO = 'procurement_orders'


def _key(value):
    return re.sub(r'[^a-z0-9]+', ' ', str(value or '').casefold()).strip()


# A numeric level is an ordering label, never a business position. Recognized
# business labels preserve legacy routes; explicit catalog codes handle routes
# whose visible labels are simply Level 0, Level 1, etc.
STAGE_POSITION_RULES = (
    (('chief executive', 'ceo', 'final management sign off'), ('ceo',)),
    (('chief financial', 'cfo', 'financial approval', 'finance approval'), ('finance',)),
    (('procurement', 'purchasing'), ('procurement',)),
    (('manager of engineering', 'engineering manager', 'engineering review'), ('engineering_manager',)),
    (('manager of projects', 'projects manager'), ('manager_projects',)),
    (('project manager',), ('project_manager',)),
    (('vp operations', 'vp delivery', 'vice president operations', 'vice president project delivery', 'operations project delivery'), ('operations',)),
    (('technical approval', 'technical review'), ('engineering',)),
)


def stage_positions(stage):
    explicit = str(stage.get('business_position') or '').strip()
    catalog_codes = {role['code'] for role in ORGANIZATIONAL_ROLES}
    if explicit and explicit not in catalog_codes:
        return ()
    label = _key(f"{stage.get('role', '')} {stage.get('stage', '')}")
    for aliases, positions in STAGE_POSITION_RULES:
        if any(re.search(r'\b' + re.escape(alias) + r'\b', label) for alias in aliases):
            return positions
    if explicit:
        return (explicit,)
    ambiguous_titles = {'department manager', 'vice president', 'vp', 'vp rejlers abu dhabi'}
    if label in ambiguous_titles:
        return ()
    matching_codes = {
        role['code'] for role in ORGANIZATIONAL_ROLES
        if any(_key(title) == label for title in [role['label'], *role.get('additional_titles', [])])
    }
    if len(matching_codes) == 1:
        return tuple(matching_codes)
    return ()


def position_matches_stage(user, stage):
    positions = stage_positions(stage)
    return bool(positions and has_business_position(user, positions))


def is_employee_selected_pr_stage(stage):
    """Recognize the configurable Level 1 employee group, not fixed-role stages."""
    try:
        if int(stage.get('level', 1)) != 1:
            return False
    except (TypeError, ValueError):
        return False
    if _key(stage.get('role')) != 'level 1 approver':
        return False
    label = _key(stage.get('stage'))
    return not label or bool(re.fullmatch(r'level 1(?: approver(?: \d+(?: of \d+)?)?)?', label))


def _eligible_selected_employee(user, module):
    """Match the active RADAI directory without granting module-wide access."""
    from django.contrib.auth import get_user_model
    from apps.hr_core.models import EmployeeMaster

    if not user or not user.is_authenticated or not getattr(user, 'pk', None):
        return False
    current = get_user_model().objects.filter(pk=user.pk, is_active=True).select_related('rbac_profile').first()
    if not current or not record_workflow_not_denied(current, module, 'approve'):
        return False
    employment = EmployeeMaster.objects.filter(user_id=current.pk).values_list('employment_status', flat=True).first()
    return employment is None or employment in {'active', 'probation', 'notice_period'}


def eligible_stage_assignee(user, stage, module):
    """Assignment identity and current sequence are additionally checked by callers."""
    if module == MODULE_PR and is_employee_selected_pr_stage(stage):
        return _eligible_selected_employee(user, module)
    return approval_access(user, module) and position_matches_stage(user, stage)
