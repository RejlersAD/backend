"""Financial posting belongs to owned delivery scope, never external dependencies."""
from rest_framework.exceptions import ValidationError


def require_owned_financial_wbs(project, node, *, field='wbs_node'):
    if not project or not node:
        return  # Required-field errors belong to the input serializer.
    if project.is_deleted or node.is_deleted or node.project_id != project.pk:
        raise ValidationError({field: 'Financial scope must use an active WBS from this project.'})
    if project.scope_type != 'detailed_engineering':
        return
    from .epc import wbs_options, wbs_phase
    phase = wbs_phase(node.pk, {row['id']: row for row in wbs_options(project)})
    if phase != 'engineering':
        raise ValidationError({field: 'Detailed Engineering budgets and actuals may cover only owned Engineering WBS. Other EPC phases are external dependencies.'})


def require_owned_financial_record(row):
    account = getattr(row, 'control_account', None)
    if account is not None:
        if account.is_deleted or account.project_id != row.project_id:
            raise ValidationError({'control_account': 'Select an active Control Account from this project.'})
        require_owned_financial_wbs(row.project, account.wbs_node, field='control_account')
    else:
        require_owned_financial_wbs(row.project, row.wbs_node)
