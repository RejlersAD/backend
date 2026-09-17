"""Explicit pending-assignment changes without rewriting approval evidence."""

from copy import deepcopy
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.rbac.action_policy import request_action_allowed
from .employee_display import employee_display_name
from .requisition_status import canonicalize_pr_status
from .requisition_workflow import RequisitionWorkflowService


HISTORY_KEY = 'approval_reassignment_history'
EDITABLE_STATUSES = {'draft', 'submitted', 'in_review'}


def reassignable_indices(pr):
    status = canonicalize_pr_status(pr.status)
    if status not in EDITABLE_STATUSES:
        return []
    result = []
    for index, row in enumerate(pr.approval_workflow_config or []):
        if not isinstance(row, dict):
            continue
        state = str(row.get('status') or 'pending').strip().lower()
        if state not in {'pending', 'in_review', 'not_recorded', 'unrecorded'}:
            continue
        if any(row.get(key) for key in (
            'approved_at', 'approved_by_id', 'approved_by_email', 'approved_by_name', 'signature',
            'signature_verified', 'rejected_at', 'rejected_by_id', 'rejected_by_email', 'rejected_by_name',
        )):
            continue
        # A source-only draft needs evidence review, not an implicit new live
        # route. Active review records may replace their unrecorded source row.
        if (row.get('external') or row.get('evidence_document_id')) and status == 'draft':
            continue
        result.append(index)
    return result


def can_reassign(pr, request):
    return bool(request and getattr(getattr(request, 'user', None), 'is_authenticated', False)
                and reassignable_indices(pr)
                and request_action_allowed(request, 'procurement_requisitions', 'update'))


def reassigned_workflow(pr, commands, request):
    """Called only while the current PR is locked by serializer.update."""
    if not request or not request_action_allowed(request, 'procurement_requisitions', 'update'):
        raise PermissionDenied('Purchase Requisition update permission is required to reassign approvers.')
    eligible = set(reassignable_indices(pr))
    workflow = deepcopy(pr.approval_workflow_config or [])
    history = []
    User = get_user_model()
    for command in commands:
        index = command['stage_index']
        if index not in eligible:
            raise ValidationError({'approval_reassignments': 'Only pending approvals on a draft or active review may be reassigned. Recorded decisions and source evidence must be retained.'})
        previous = workflow[index]
        expected_email = str(command.get('expected_user_email') or '').strip().lower()
        current_email = RequisitionWorkflowService._stage_email(previous)
        # Migrated records are displayed/routed by stable email, even where an
        # obsolete database ID remains in their stored JSON.
        identity_matches = (expected_email == current_email) if current_email else (
            str(command.get('expected_user_id') or '') == str(previous.get('user_id') or previous.get('approver_id') or '')
        )
        if (not identity_matches
                or str(command.get('expected_assignment_id') or '') != str(previous.get('assignment_id') or '')
                or command['expected_role'] != str(previous.get('role') or '')
                or command['expected_level'] != previous.get('level')
                or str(command['expected_status']).strip().lower() != str(previous.get('status') or 'pending').strip().lower()):
            raise ValidationError({'approval_reassignments': 'An approval assignment changed. Refresh the recommendation before saving.'})
        try:
            approver = User.objects.get(pk=User._meta.pk.to_python(command['user_id']))
        except (User.DoesNotExist, ValueError, TypeError, DjangoValidationError):
            raise ValidationError({'approval_reassignments': 'Select an existing employee for each approval assignment.'})
        if RequisitionWorkflowService._stage_matches_user(previous, approver) and not previous.get('external'):
            continue
        updated = {key: deepcopy(previous[key]) for key in (
            'step', 'level', 'role', 'stage', 'approval_label', 'business_position',
            'approval_group', 'group_mode',
        ) if key in previous}
        updated.update(
            level=RequisitionWorkflowService._stage_level(previous, index),
            user_id=str(approver.pk), user_name=employee_display_name(approver),
            username=approver.get_username(), user_email=approver.email,
            status='pending', approved_at=None, assignment_id=str(uuid4()),
        )
        if previous.get('external') and not updated.get('business_position'):
            # Original PDFs abbreviate fixed roles (MoE, MoP, VP). Carry that
            # same authority into the new live assignment, never infer it from
            # the selected employee or the row's ordinal position.
            from .pr_pdf_semantics import approval_role
            source_role = {'pm': ('project_manager', 'Project Manager'),
                           'moe': ('engineering_manager', 'Engineering Review'),
                           'mop': ('manager_projects', 'Manager of Projects'),
                           'vp': ('operations', 'VP Operations')}.get(
                approval_role(previous.get('role_key') or previous.get('role')),
            )
            if source_role:
                updated['business_position'] = source_role[0]
                if not updated.get('stage'):
                    updated['stage'] = source_role[1]
        workflow[index] = updated
        history.append({
            'stage_index': index, 'before': previous, 'after': deepcopy(updated),
            'changed_at': timezone.now().isoformat(),
            'changed_by_id': str(request.user.pk),
            'changed_by_name': employee_display_name(request.user),
            'changed_by_email': request.user.email,
        })
    return workflow, history
