"""Advisory checks for PR registration, separate from approval authorization."""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.db.models.functions import Lower

from .approval_eligibility import MODULE_PR, eligible_stage_assignee, is_employee_selected_pr_stage
from .employee_display import normalize_ceo_workflow
from .procurement_vat import decimal_amount
from .requisition_workflow import RequisitionWorkflowService


def requisition_registration_warnings(pr):
    """Return current business omissions without preventing save or submission."""
    workflow = normalize_ceo_workflow(
        getattr(pr, 'approval_workflow_config', []),
        getattr(pr, 'po_number_reference', ''),
        getattr(pr, 'po_applicable', None),
    )
    metadata = getattr(pr, 'price_remarks_data', None) or {}
    verification = metadata.get('signed_document_verification') or {}
    if verification.get('signed_off') or (workflow and all(
        isinstance(stage, dict) and stage.get('external') is True
        and stage.get('source') == 'signed_purchase_requisition_pdf' for stage in workflow
    )):
        return []

    warnings = []
    for field, message in (
        ('product_service', 'Product/service description is missing.'),
        ('description_reason', 'Purchase description is missing.'),
        ('price_description', 'Pricing description is missing.'),
        ('purchase_recommendation', 'Purchase recommendation is missing.'),
    ):
        if not str(getattr(pr, field, '') or '').strip():
            warnings.append(message)
    projects = getattr(pr, 'project_details', None) or []
    if not projects:
        warnings.append('No project or Internal / General department is selected.')
    elif any(isinstance(project, dict) and project.get('source') == 'custom' and (
        not str(project.get('project_number') or '').strip()
        or not str(project.get('project_name') or '').strip()
    ) for project in projects):
        warnings.append('A custom project / department is missing its number or name.')
    total = decimal_amount(getattr(pr, 'total_price', None))
    if total is None or total <= 0:
        warnings.append('A positive total price has not been entered.')
    vendors = getattr(pr, 'selected_vendors', None) or []
    if not vendors:
        warnings.append('No vendors are shortlisted.')
    if not getattr(pr, 'vendor_id', None):
        warnings.append('No supplier is selected from the shortlisted vendors.')
    if len(vendors) == 1 and not str(getattr(pr, 'single_source_justification', '') or '').strip():
        warnings.append('Single source justification is missing.')
    if getattr(pr, 'po_applicable', False) and not str(getattr(pr, 'po_number_reference', '') or '').strip():
        warnings.append('A completed PO number has not been entered.')
    net_total = decimal_amount(getattr(pr, 'net_total_excl_vat', None))
    if getattr(pr, 'currency', '') == 'AED' and (net_total if net_total is not None else total or 0) > 100000:
        if getattr(pr, 'management_approval', None) is not True:
            warnings.append('Management approval is not confirmed for a PR above AED 100,000.')
        if not str(getattr(pr, 'management_approval_remarks', '') or '').strip():
            warnings.append('Management approval remarks are missing.')
        if not getattr(pr, 'management_approval_evidence', None):
            warnings.append('Management approval evidence is missing.')

    if not workflow:
        warnings.append('No approval workflow is configured. Assign approvers to start approval review.')

    stages = [stage for stage in workflow if isinstance(stage, dict)]
    levels = {RequisitionWorkflowService._stage_level(stage, index) for index, stage in enumerate(stages)}
    project_route = getattr(pr, 'requisition_type', '') == 'project'
    expected_levels = {0, 1, 3, 4} if project_route else {0, 1, 2}
    if getattr(pr, 'po_applicable', True) is False:
        expected_levels.add(5)
    for level in sorted(expected_levels - levels):
        warnings.append(f'Level {level} approval is not configured.')
    if project_route and not any(
        RequisitionWorkflowService._stage_level(stage, index) == 4 and any(
            identity in ' '.join(str(stage.get(field) or '').strip().lower() for field in (
                'user_name', 'approver', 'user_email', 'approver_email',
            )) for identity in ('mohamad el-ghawanmeh', 'mohamed el-ghawanmeh', 'moghawanmeh@rejlers.ae')
        ) for index, stage in enumerate(stages)
    ):
        warnings.append('The default Level 4 VP Delivery approver, Mohamad El-Ghawanmeh, is not assigned.')
    if getattr(pr, 'po_applicable', True) is False and not any(
        RequisitionWorkflowService._stage_level(stage, index) == 5
        and str(stage.get('user_name') or '').strip().lower() == 'jarmo suominen'
        and any(label in f"{stage.get('role', '')} {stage.get('stage', '')}".lower() for label in ('general manager', 'ceo'))
        for index, stage in enumerate(stages)
    ):
        warnings.append('PO is not applicable and Level 5 Jarmo Suominen (CEO) is not assigned.')

    identifiers = [str(stage.get('user_id') or stage.get('approver_id') or '') for stage in stages]
    emails = [RequisitionWorkflowService._stage_email(stage) for stage in stages]
    User = get_user_model()
    valid_identifiers = []
    for identifier in identifiers:
        if identifier:
            try:
                valid_identifiers.append(User._meta.pk.to_python(identifier))
            except (ValidationError, ValueError, TypeError):
                pass
    users = User.objects.annotate(registration_email=Lower('email')).filter(
        Q(pk__in=valid_identifiers)
        | Q(registration_email__in=[email for email in emails if email]),
    ).select_related('rbac_profile')
    by_id = {str(user.pk): user for user in users}
    by_email = {str(user.email or '').strip().lower(): user for user in users}
    assigned = set()
    for index, stage in enumerate(stages):
        if stage.get('external'):
            continue
        role = str(stage.get('role') or '').strip()
        level = RequisitionWorkflowService._stage_level(stage, index)
        label = f'Level {level} ({role or "Unnamed approval stage"})'
        if not role:
            warnings.append(f'{label} has no approval role.')
        identifier, email = identifiers[index], emails[index]
        if not identifier and not email:
            warnings.append(f'{label} has no assigned approver.')
            continue
        user = by_email.get(email) if email else by_id.get(identifier)
        identity = str(user.pk) if user else email or identifier
        if identity in assigned:
            warnings.append(f'{label} duplicates an approver already selected in this workflow.')
        assigned.add(identity)
        if user is None or not RequisitionWorkflowService._actor_is_active(user):
            warnings.append(f'{label} does not have an active employee assigned; approval review needs an active approver.')
        elif not eligible_stage_assignee(user, stage, MODULE_PR):
            requirement = ('active RADAI employee approval access' if is_employee_selected_pr_stage(stage)
                           else 'the configured business position and Purchase Requisition approval permission')
            warnings.append(f'{label}: the selected employee does not currently have {requirement}. Update the assignment or access before approval review.')
    return warnings
