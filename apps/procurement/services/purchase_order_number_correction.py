"""Correct a saved PO identifier without rewriting its commercial evidence."""

from copy import deepcopy

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError

from apps.rbac.action_policy import request_action_allowed
from apps.rbac.utils import create_audit_log
from ..models import PurchaseOrder
from .procurement_lifecycle import RETAINED_ATTACHMENTS, attachment_cleanup_keys
from .purchase_order_content import (
    PO_NUMBER_CORRECTIONS, purchase_order_content_fingerprint, purchase_order_content_issue,
)
from .purchase_order_lifecycle import lock_purchase_order
from .purchase_order_numbering import PurchaseOrderNumberService, source_po_number


class PONumberCorrectionConflict(APIException):
    status_code = 409
    default_code = 'po_number_conflict'


def _timestamp(value):
    try:
        parsed = parse_datetime(value) if isinstance(value, str) else None
    except (TypeError, ValueError):
        parsed = None
    if parsed is None or timezone.is_naive(parsed):
        raise ValidationError({'expected_updated_at': 'The exact saved timestamp is required.'})
    return parsed


def _update_requisition_reference(order, previous_number):
    """Update current links only; leave source snapshots and PR decisions intact."""
    if not order.pr_reference_id:
        return
    pr = order.pr_reference  # Already locked by lock_purchase_order.
    changed = []
    if pr.po_number_reference == previous_number:
        pr.po_number_reference = order.po_number
        changed.append('po_number_reference')
    metadata = deepcopy(pr.price_remarks_data or {})
    link = metadata.get('po_link')
    if isinstance(link, dict) and str(link.get('po_id') or '') == str(order.pk):
        link['po_number'] = order.po_number
        if link.get('message') == f'Linked to purchase order {previous_number}.':
            link['message'] = f'Linked to purchase order {order.po_number}.'
        pr.price_remarks_data = metadata
        changed.append('price_remarks_data')
    if changed:
        pr.save(update_fields=[*changed, 'updated_at'])


@transaction.atomic
def correct_purchase_order_number(observed, request):
    """Persist a number-only correction, linked references and audit atomically."""
    if not request_action_allowed(request, 'procurement_orders', 'update'):
        raise PermissionDenied('Purchase order edit access is required.')
    payload = request.data
    if not isinstance(payload, dict):
        raise ValidationError('Submit the PO number and saved timestamp.')
    unknown = set(payload) - {'po_number', 'expected_updated_at'}
    if unknown:
        raise ValidationError({key: 'This field cannot be edited here.' for key in sorted(unknown)})
    supplied = payload.get('po_number')
    number = source_po_number(supplied) if isinstance(supplied, str) else None
    if not number:
        raise ValidationError({'po_number': 'Enter a valid RAD purchase order number.'})
    expected = _timestamp(payload.get('expected_updated_at'))
    order = lock_purchase_order(observed)
    valid, message = PurchaseOrderNumberService.verify(
        number, order.pr_reference.pr_number if order.pr_reference_id else None,
    )
    if not valid:
        raise ValidationError({'po_number': message})
    if order.contact_persons and not isinstance(order.contact_persons, dict):
        raise PONumberCorrectionConflict({'error': 'The PO contact metadata needs review before correcting its number.'})
    contacts = deepcopy(order.contact_persons or {})
    history = contacts.get(PO_NUMBER_CORRECTIONS, [])
    if not isinstance(history, list) or any(not isinstance(row, dict) for row in history):
        raise PONumberCorrectionConflict({'error': 'The PO number history needs review before another correction.'})
    before_fingerprint = purchase_order_content_fingerprint(order)
    last = history[-1] if history else {}
    if (
        number == order.po_number and last.get('new_number') == number
        and last.get('actor_id') == str(request.user.pk)
        and last.get('expected_updated_at') == expected.isoformat()
        and last.get('after_fingerprint') == before_fingerprint
    ):
        return order
    if order.updated_at != expected:
        raise PONumberCorrectionConflict({'error': 'This purchase order changed. Refresh before saving its number.'})
    if number == order.po_number:
        return order
    issue = purchase_order_content_issue(order)
    if issue:
        raise PONumberCorrectionConflict({'error': issue})
    if PurchaseOrder.objects.filter(po_number__iexact=number).exclude(pk=order.pk).exists():
        raise PONumberCorrectionConflict({'po_number': 'This Purchase Order number is already in use.'})

    previous_number = order.po_number
    # Retain exact ownership of existing paths; never move or rewrite source bytes.
    contacts[RETAINED_ATTACHMENTS] = sorted(attachment_cleanup_keys(order))
    order.contact_persons = contacts
    order.po_number = number
    after_fingerprint = purchase_order_content_fingerprint(order)
    history.append({
        'old_number': previous_number, 'new_number': number,
        'before_fingerprint': before_fingerprint, 'after_fingerprint': after_fingerprint,
        'actor_id': str(request.user.pk), 'changed_at': timezone.now().isoformat(),
        'expected_updated_at': expected.isoformat(),
    })
    contacts[PO_NUMBER_CORRECTIONS] = history
    try:
        with transaction.atomic():
            order.save(update_fields=['po_number', 'contact_persons', 'updated_at'])
    except IntegrityError as error:
        raise PONumberCorrectionConflict({'po_number': 'This Purchase Order number is already in use.'}) from error
    _update_requisition_reference(order, previous_number)
    create_audit_log(
        user=request.user, action='update', resource_type='PurchaseOrder',
        resource_id=order.pk, resource_repr=number,
        changes={'po_number': {'before': previous_number, 'after': number}},
        metadata={
            'operation': 'correct_po_number', 'expected_updated_at': expected.isoformat(),
            'before_fingerprint': before_fingerprint, 'after_fingerprint': after_fingerprint,
            'pr_id': str(order.pr_reference_id) if order.pr_reference_id else None,
        },
    )
    return order
