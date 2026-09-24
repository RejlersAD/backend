"""Reopen rejected requisitions without carrying decisions into a new review."""

from copy import deepcopy
from datetime import date
import json
from uuid import uuid4

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.fields import empty

from apps.rbac.action_policy import request_action_allowed
from ..models import PurchaseRequisition
from .employee_display import employee_display_name
from .requisition_concurrency import check_requisition_precondition
from .requisition_status import canonicalize_pr_status
from .requisition_workflow import RequisitionWorkflowService


HISTORY_KEY = 'approval_revision_history'
SOURCE_DECISIONS = (
    'signed_document_verification', 'signed_approval_evidence', 'source_approval_reviews',
)
ASSIGNMENT_FIELDS = (
    'level', 'role', 'stage', 'approval_label', 'business_position',
    'approval_group', 'group_mode', 'user_id', 'approver_id',
    'user_name', 'username', 'user_email', 'approver_email',
)


def _authorized(pr, request):
    actor = getattr(request, 'user', None)
    return bool(
        actor and actor.is_authenticated and actor.is_active
        and (str(pr.issued_by_id) == str(actor.pk) or RequisitionWorkflowService._is_super_admin(actor))
        and request_action_allowed(request, 'procurement_requisitions', 'update')
    )


def can_reopen(pr, request):
    return canonicalize_pr_status(pr.status) == 'rejected' and _authorized(pr, request)


def _snapshot(pr):
    snapshot = {field.attname: deepcopy(getattr(pr, field.attname)) for field in pr._meta.concrete_fields}
    # Preserve the full precision of recorded decisions and freshness tokens;
    # DjangoJSONEncoder otherwise truncates datetime values to milliseconds.
    for key, value in snapshot.items():
        if isinstance(value, date):
            snapshot[key] = value.isoformat()
    # Older snapshots are already in the append-only list. Do not recursively
    # copy that list into every new round.
    snapshot['price_remarks_data'] = snapshot.get('price_remarks_data') or {}
    snapshot['price_remarks_data'].pop(HISTORY_KEY, None)
    return json.loads(json.dumps(snapshot, cls=DjangoJSONEncoder))


@transaction.atomic
def reopen_requisition(requisition_id, request, expected_updated_at=empty):
    pr = get_object_or_404(PurchaseRequisition.objects.select_for_update(), pk=requisition_id)
    if not _authorized(pr, request):
        raise PermissionDenied('Only the requisition issuer with Purchase Requisition update permission may reopen this request.')
    if expected_updated_at is empty:
        raise ValidationError({'expected_updated_at': 'Reload the requisition before reopening it.'})
    check_requisition_precondition(pr, expected_updated_at)
    if canonicalize_pr_status(pr.status) != 'rejected':
        raise ValidationError({'error': 'Only rejected requisitions can be reopened for editing.'})

    metadata = deepcopy(pr.price_remarks_data or {})
    history = metadata.get(HISTORY_KEY) or []
    if not isinstance(history, list):
        raise ValidationError({'error': 'The approval revision history must be reconciled before reopening this requisition.'})
    actor = request.user
    history.append({
        'round': len(history) + 1,
        'reopened_at': timezone.now().isoformat(),
        'reopened_by_id': str(actor.pk),
        'reopened_by_name': employee_display_name(actor),
        'reopened_by_email': actor.email,
        'rejection_reason': pr.rejection_reason,
        'approval_workflow_config': deepcopy(pr.approval_workflow_config),
        'snapshot': _snapshot(pr),
    })
    metadata[HISTORY_KEY] = history
    # The original files and complete source verification remain in the
    # archive. They are evidence about that rejected round, not approval of
    # commercial content the requester is now free to change.
    for key in SOURCE_DECISIONS:
        metadata.pop(key, None)

    workflow = []
    for index, previous in enumerate(pr.approval_workflow_config or []):
        if not isinstance(previous, dict):
            raise ValidationError({'error': 'Review the invalid approval route before reopening this requisition.'})
        stage = {key: deepcopy(previous[key]) for key in ASSIGNMENT_FIELDS if key in previous}
        stage.update(step=index + 1, status='pending', approved_at=None, assignment_id=str(uuid4()))
        # Preserve source role authority where a source-only label is abbreviated.
        if previous.get('external') and not stage.get('business_position'):
            from .pr_pdf_semantics import approval_role
            position = {
                'pm': 'project_manager', 'moe': 'engineering_manager',
                'mop': 'manager_projects', 'vp': 'operations',
            }.get(approval_role(previous.get('role_key') or previous.get('role')))
            if position:
                stage['business_position'] = position
        workflow.append(stage)

    pr.price_remarks_data = metadata
    pr.approval_workflow_config = workflow
    pr.current_approval_step = 0
    pr.status = 'draft'
    pr.rejection_reason = ''
    pr.resolution_referral = {}
    pr.review_due_at = None
    pr.approved_by = None
    pr.approved_at = None
    for config in RequisitionWorkflowService.STAGE_CONFIG.values():
        setattr(pr, config['name_field'], None)
        setattr(pr, config['signature_field'], '')
        setattr(pr, config['status_field'], 'pending')
        setattr(pr, config['timestamp_field'], None)
    pr.save()
    return pr
