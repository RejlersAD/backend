"""Approval evidence required before a purchase order can progress."""

from copy import copy
from uuid import UUID

from rest_framework.exceptions import ValidationError

from ..models import PODocument, PurchaseOrder, PurchaseRequisition
from .approval_integrity import purchase_order_signature_issue, stage_signature_issue


PROGRESSED_STATUSES = ('sent', 'acknowledged', 'in_progress', 'partially_received', 'completed')
STATUS_SEQUENCE = ('draft', *PROGRESSED_STATUSES)


def lock_purchase_order(order):
    """Refresh under PR then PO locks inside the caller's atomic transaction.

    An observed relationship may change while waiting for a lock. Reject that
    stale operation instead of acquiring a new PR lock after the PO lock.
    """
    pr_id = order.pr_reference_id
    requisition = None
    if pr_id:
        requisition = PurchaseRequisition.objects.select_for_update().filter(pk=pr_id).first()
    locked = PurchaseOrder.objects.select_for_update().get(pk=order.pk)
    if locked.pr_reference_id != pr_id:
        raise ValidationError({'status': 'The linked recommendation changed. Refresh this order before continuing.'})
    if requisition is not None:
        locked.pr_reference = requisition
    return locked


def _verified_source_row(order, row):
    """An external flag alone is not proof of a reviewed original document."""
    if row.get('signature_verified') is not True or row.get('approval_evidence_complete') is False:
        return False
    try:
        document_id = UUID(str(row.get('evidence_document_id') or ''))
    except (TypeError, ValueError, AttributeError):
        return False
    document = PODocument.objects.filter(pk=document_id, confirmed_po_id=order.pk).first()
    if document is None or document.document_type != 'purchase_order':
        return False
    evidence = document.extracted_data if isinstance(document.extracted_data, dict) else {}
    return (
        evidence.get('signature_verified') is True
        and evidence.get('approval_evidence_complete') is not False
        and not evidence.get('reconciliation_required')
        and not evidence.get('reconciliation_issues')
    )


def require_purchase_order_approval(order):
    """Require every recorded stage to be approved with usable evidence.

    Existing named or assigned approvals remain valid historical evidence when
    they predate signer snapshots. Empty routes and unverified source records
    cannot authorize a lifecycle change, nor can external evidence supersede a
    pending or rejected internal assignment.
    """
    rows = getattr(order, 'approval_log', None)
    if not isinstance(rows, list) or not rows:
        raise ValidationError({'status': 'Complete a purchase order approval route before progressing this order.'})
    for row in rows:
        if not isinstance(row, dict):
            raise ValidationError({'status': 'The purchase order approval evidence is invalid. Approval review is required.'})
        if str(row.get('status') or '').strip().lower() != 'approved':
            raise ValidationError({'status': 'All purchase order approval stages must be approved before progressing this order.'})
        if row.get('external') or row.get('evidence_document_id'):
            if not _verified_source_row(order, row):
                raise ValidationError({'status': 'Verify the signed source document approval before progressing this order.'})
        elif not any(row.get(key) for key in (
            'user_id', 'user_email', 'approver_email', 'email', 'approver', 'approved_by_name',
        )):
            raise ValidationError({'status': 'The recorded approval is missing its approver. Approval review is required.'})
        issue = stage_signature_issue(row)
        if issue:
            raise ValidationError({'status': issue})
    issue = purchase_order_signature_issue(order)
    if issue:
        raise ValidationError({'status': issue})


def validate_purchase_order_transition(order, target_status, *, approval_log=None):
    """Validate an action or a normalized serializer write, without saving.

    Serializer callers also recheck after locking the current database row and
    pass the normalized proposed approval log when changing both fields.
    """
    current_status = str(getattr(order, 'status', 'draft') or 'draft').strip().lower()
    target_status = str(target_status or '').strip().lower()
    if target_status not in (*STATUS_SEQUENCE, 'cancelled'):
        raise ValidationError({'status': 'Choose a valid purchase order status.'})
    if current_status in {'completed', 'cancelled'} and target_status != current_status:
        raise ValidationError({'status': 'Completed or cancelled purchase orders cannot be reopened through a status change.'})
    if current_status in STATUS_SEQUENCE and target_status in STATUS_SEQUENCE:
        if STATUS_SEQUENCE.index(target_status) < STATUS_SEQUENCE.index(current_status):
            raise ValidationError({'status': 'A purchase order cannot move back to an earlier lifecycle status.'})
    if target_status in PROGRESSED_STATUSES:
        proposed = copy(order)
        if approval_log is not None:
            proposed.approval_log = approval_log
        require_purchase_order_approval(proposed)


def purchase_order_transition_issue(order, target_status):
    """Read the same lifecycle decision for UI capability hints."""
    try:
        validate_purchase_order_transition(order, target_status)
    except ValidationError as error:
        detail = error.detail
        if isinstance(detail, dict):
            detail = next(iter(detail.values()))
        if isinstance(detail, (list, tuple)):
            detail = detail[0] if detail else ''
        return str(detail)
    return ''
