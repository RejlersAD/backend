"""Authoritative receiving balances and locked, auditable receipt commands.

No tolerance is assumed. Service acceptance uses only confirmed net PO value;
legacy evidence that cannot be mapped is reported, never silently discarded.
"""
from decimal import Decimal, InvalidOperation
from copy import copy
import hashlib
import json
import re
from uuid import UUID

from django.db import IntegrityError, transaction
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from apps.rbac.action_policy import record_workflow_not_denied, request_action_allowed
from apps.rbac.models import AuditLog
from apps.rbac.utils import create_audit_log
from ..models import Receipt
from .po_rich_content import parse_meaningful_rich_content
from .purchase_order_lifecycle import lock_purchase_order, require_purchase_order_approval
from .receipt_numbering import ReceiptNumberService


OPEN_STATUSES = ('sent', 'acknowledged', 'in_progress', 'partially_received')
SERVICE_CATEGORIES = {'engineering_services', 'maintenance_services'}
INSPECTION_FLAGS = ('quality_check_passed', 'dimensional_check_passed', 'visual_inspection_passed', 'material_verification_passed')


class ReceivingConflict(APIException):
    status_code = 409
    default_detail = 'This record changed. Refresh before continuing.'
    default_code = 'receiving_conflict'


def amount(value):
    text = str(value if value is not None else '').strip()
    if not re.fullmatch(r'\d{1,18}(?:\.\d{1,6})?', text):
        raise ValueError('Use a non-negative decimal with at most six decimal places.')
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError('Invalid decimal.') from exc
    return result


def decimal_text(value):
    return format(value, 'f')


def _basis(po):
    items = po.items
    if not isinstance(items, list):
        raise ValueError('The purchase order item basis needs review.')
    if items:
        lines, ids = [], set()
        for index, item in enumerate(items, 1):
            if not isinstance(item, dict):
                raise ValueError('The purchase order item basis needs review.')
            number = item.get('line_number') or index
            if isinstance(number, (list, dict, bool)) or not str(number).strip():
                raise ValueError('The purchase order line number needs review.')
            reference = str(item.get('po_item_reference') or item.get('item_reference') or item.get('item_no') or number)
            identifier = str(item.get('id') or item.get('po_line_id') or f'line:{number}')
            uom = str(item.get('unit') or item.get('uom') or '').strip()
            ordered = amount(item.get('quantity', item.get('qty', item.get('ordered_qty'))))
            if identifier in ids or not uom or ordered <= 0:
                raise ValueError('Each purchase order line needs a unique identity, unit and positive ordered quantity.')
            ids.add(identifier)
            lines.append({'line_id': identifier, 'line_number': number, 'po_item_reference': reference,
                          'description': str(item.get('description') or item.get('item') or item.get('name') or f'Line {index}'),
                          'uom': uom, 'ordered': ordered})
        return 'quantity', lines
    scope = next((str(value).strip() for value in (po.scope_of_services, po.description)
                  if parse_meaningful_rich_content(value)), '')
    if (po.category not in SERVICE_CATEGORIES or not scope or po.vat_basis == 'unconfirmed'
            or po.net_amount is None or po.net_amount <= 0 or not str(po.currency or '').strip()):
        raise ValueError('Record a valid goods line basis or a service scope with confirmed net value and currency before receiving.')
    return 'service_value', [{'line_id': 'service:total', 'line_number': 1,
                              'po_item_reference': 'service:total',
                              'description': scope, 'uom': po.currency,
                              'ordered': po.net_amount}]


def _line_id(item, lines):
    identifier = item.get('line_id') or item.get('po_line_id')
    if identifier is not None and str(identifier) in lines:
        return str(identifier)
    # Older receipts had a stable PO line number rather than the new identifier.
    number = item.get('line_number')
    matches = [key for key, line in lines.items() if str(line['line_number']) == str(number)]
    if not identifier and len(matches) == 1:
        return matches[0]
    reference = item.get('po_item_reference') or item.get('item_reference') or item.get('item_no')
    matches = [key for key, line in lines.items() if reference is not None and str(line['po_item_reference']) == str(reference)]
    if not identifier and len(matches) == 1:
        return matches[0]
    raise ValueError('Existing receipt lines cannot be matched to this purchase order. Review their evidence.')


def receiving_summary(po, *, request=None, exclude_receipt=None):
    result = {'basis': 'unavailable', 'status': 'blocked', 'lines': [], 'blocked_reason': '',
              'can_record': False, 'can_reconcile': False, 'requires_reconciliation': po.status == 'completed',
              'po_updated_at': po.updated_at.isoformat() if po.updated_at else None, 'value_basis': None}
    try:
        basis, values = _basis(po)
        result['basis'] = basis
        result['value_basis'] = 'net_excluding_vat' if basis == 'service_value' else None
        lines = {line['line_id']: {**line, 'accepted': Decimal(0), 'pending': Decimal(0)} for line in values}
        receipts = po.receipts.all()
        if exclude_receipt:
            receipts = receipts.exclude(pk=exclude_receipt)
        for receipt in receipts:
            if receipt.status == 'rejected':
                continue
            if receipt.status not in {'pending', 'accepted', 'partial'} or not isinstance(receipt.items_received, list) or not receipt.items_received:
                raise ValueError('Existing receipt evidence needs review before remaining balances can be calculated.')
            seen = set()
            for item in receipt.items_received:
                if not isinstance(item, dict):
                    raise ValueError('Existing receipt evidence needs review.')
                key = _line_id(item, lines)
                if key in seen:
                    raise ValueError('Existing receipt contains duplicate line identities.')
                seen.add(key)
                suffix = 'amount' if basis == 'service_value' else 'qty'
                received = amount(item.get(f'received_{suffix}'))
                rejected = amount(item.get(f'rejected_{suffix}', '0'))
                accepted = amount(item.get(f'accepted_{suffix}', received - rejected))
                if rejected > received or accepted != received - rejected:
                    raise ValueError('Existing receipt quantities or values are inconsistent.')
                if str(item.get('uom', lines[key]['uom'])) != lines[key]['uom']:
                    raise ValueError('Existing receipt units do not match the purchase order.')
                lines[key]['pending' if receipt.status == 'pending' else 'accepted'] += accepted
        for line in lines.values():
            line['remaining'] = line['ordered'] - line['accepted']
            line['available'] = line['remaining'] - line['pending']
            if line['available'] < 0:
                raise ValueError('Existing receipts exceed the purchase order balance. Reconcile the evidence first.')
        complete = all(line['remaining'] == 0 for line in lines.values())
        any_accepted = any(line['accepted'] > 0 for line in lines.values())
        pending = any(line['pending'] > 0 for line in lines.values())
        result['status'] = 'complete' if complete else 'partial' if any_accepted else 'pending' if pending else 'none'
        result['lines'] = [{key: decimal_text(value) if isinstance(value, Decimal) else value for key, value in line.items()} for line in lines.values()]
        require_purchase_order_approval(po)
        if po.status not in (*OPEN_STATUSES, 'completed'):
            result['blocked_reason'] = 'The purchase order must be issued before receiving.'
        elif complete:
            result['blocked_reason'] = 'The purchase order is fully received or accepted.'
        elif not any(line['available'] > 0 for line in lines.values()):
            result['blocked_reason'] = 'The remaining balance is reserved by pending inspections.'
        else:
            allowed = request is None or (request_action_allowed(request, 'procurement_orders', 'read')
                                         and request_action_allowed(request, 'procurement_receipts', 'create'))
            result['can_record'] = bool(allowed and po.status in OPEN_STATUSES)
            result['can_reconcile'] = bool(allowed and po.status == 'completed')
            if not allowed:
                result['blocked_reason'] = 'Purchase order read and receipt create access are required.'
    except (ValueError, ValidationError) as exc:
        detail = getattr(exc, 'detail', None)
        if isinstance(detail, dict):
            detail = next(iter(detail.values()))
        if isinstance(detail, list):
            detail = detail[0] if detail else ''
        result['blocked_reason'] = str(detail or exc)
        result['status'] = 'blocked'
    return result


def require_fresh(value, current, field):
    try:
        parsed = parse_datetime(str(value or ''))
    except (ValueError, TypeError):
        parsed = None
    if parsed is None or timezone.is_naive(parsed):
        raise ValidationError({field: 'The exact saved timestamp is required.'})
    if parsed != current:
        raise ReceivingConflict({field: 'The record changed. Refresh before continuing.'})


def _require_access(request):
    if not (request_action_allowed(request, 'procurement_orders', 'read')
            and request_action_allowed(request, 'procurement_receipts', 'create')):
        raise PermissionDenied('Purchase order read and receipt create access are required.')


def _canonical_items(raw, summary, *, decision=False):
    if not isinstance(raw, list) or not raw:
        raise ValidationError({'items_received': 'Record at least one received line.'})
    lines = {line['line_id']: line for line in summary['lines']}
    result, seen = [], set()
    suffix = 'amount' if summary['basis'] == 'service_value' else 'qty'
    for item in raw:
        try:
            if not isinstance(item, dict):
                raise ValueError('Each receipt line must be an object.')
            key = _line_id(item, lines)
            if key in seen:
                raise ValueError('Record each purchase order line only once.')
            seen.add(key)
            line = lines[key]
            received = amount(item.get(f'received_{suffix}'))
            rejected = amount(item.get(f'rejected_{suffix}', '0'))
            consumed = received - rejected if decision else received
            if (received <= 0 or rejected > received or received > amount(line['ordered'])
                    or consumed > amount(line['available'])):
                raise ValueError('Received values must be positive, within the available balance, and at least the rejected value.')
            if summary['basis'] == 'service_value' and (received != received.quantize(Decimal('0.01')) or rejected != rejected.quantize(Decimal('0.01'))):
                raise ValueError('Service values must use at most two decimal places.')
            result.append({'line_id': key, 'line_number': line['line_number'], 'item': line['description'],
                           'po_item_reference': line['po_item_reference'],
                           'uom': line['uom'], 'basis': summary['basis'], f'ordered_{suffix}': line['ordered'],
                           f'received_{suffix}': decimal_text(received), f'rejected_{suffix}': decimal_text(rejected),
                           f'accepted_{suffix}': decimal_text(received - rejected)})
        except (ValueError, InvalidOperation) as exc:
            raise ValidationError({'items_received': str(exc)}) from exc
    return result


def _history(receipt, request, action, **extra):
    receipt.workflow_history = [*(receipt.workflow_history or []), {
        'action': action, 'actor_id': request.user.pk, 'at': timezone.now().isoformat(),
        'status': receipt.status, **extra,
    }]


def lock_receipt(observed):
    """Lock PR, then PO, then receipt inside the caller's transaction.

    Shared by the HTTP approval guard and receipt commands, so no early guard
    can acquire the child row in the opposite order to a concurrent edit.
    """
    po = lock_purchase_order(observed.purchase_order)
    try:
        receipt = Receipt.objects.select_for_update().get(pk=observed.pk)
    except Receipt.DoesNotExist as exc:
        raise NotFound('This goods receipt no longer exists. Refresh the register.') from exc
    if receipt.purchase_order_id != po.pk:
        raise ReceivingConflict()
    return po, receipt


def _acceptance_items(po, receipt):
    """One source/balance gate for inspection and recorder confirmation."""
    require_purchase_order_approval(po)
    if po.status not in (*OPEN_STATUSES, 'completed'):
        raise ValidationError({'purchase_order': 'Only an issued order can be accepted.'})
    current = receiving_summary(po)
    if current['status'] == 'blocked':
        raise ValidationError({'items_received': current['blocked_reason']})
    summary = receiving_summary(po, exclude_receipt=receipt.pk)
    if summary['status'] == 'blocked':
        raise ValidationError({'items_received': summary['blocked_reason']})
    items = _canonical_items(receipt.items_received, summary, decision=True)
    suffix = 'amount' if summary['basis'] == 'service_value' else 'qty'
    if not any(amount(item[f'accepted_{suffix}']) > 0 for item in items):
        raise ValidationError({'items_received': 'Use rejection when no received quantity or value is accepted.'})
    target = 'partial' if any(amount(item[f'rejected_{suffix}']) > 0 for item in items) else 'accepted'
    return items, target


def _require_recorder(request, receipt):
    if not receipt.received_by_id:
        raise PermissionDenied('No recorder is recorded for this receipt. Delivery confirmation is unavailable.')
    if not request or receipt.received_by_id != request.user.pk:
        raise PermissionDenied('Only the person who recorded this receipt can confirm delivery.')
    _require_access(request)
    if not request_action_allowed(request, 'procurement_receipts', 'read'):
        raise PermissionDenied('Receipt read access is required to confirm delivery.')
    # An absent inspection grant does not deny the recorder's narrow command;
    # an explicit approval restriction still prevents accepting receipt evidence.
    if not record_workflow_not_denied(request.user, 'procurement_receipts', 'approve'):
        raise PermissionDenied('Delivery confirmation is explicitly denied for this account.')


def _message(exc):
    detail = getattr(exc, 'detail', None)
    if isinstance(detail, dict):
        detail = next(iter(detail.values()), '')
    if isinstance(detail, list):
        detail = detail[0] if detail else ''
    return str(detail or exc)


def delivery_confirmation(receipt, request):
    """Server-owned responsibility and capability, never inferred inspection."""
    recorder = receipt.received_by
    result = {
        'can_confirm': False, 'blocked_reason': '',
        'responsible_user_id': receipt.received_by_id,
        'responsible_user_name': (recorder.get_full_name() or recorder.get_username()) if recorder else None,
        'confirmed_by_id': None, 'confirmed_by_name': None, 'confirmed_at': None,
    }
    for event in reversed(receipt.workflow_history or []):
        if isinstance(event, dict) and event.get('action') == 'confirm_delivery':
            result.update(confirmed_by_id=event.get('actor_id'), confirmed_by_name=event.get('actor_name'),
                          confirmed_at=event.get('at'))
            break
    if receipt.status != 'pending':
        result['blocked_reason'] = 'Delivery is already confirmed.' if result['confirmed_at'] else 'Only pending receipts can be confirmed.'
        return result
    try:
        _require_recorder(request, receipt)
        _acceptance_items(receipt.purchase_order, receipt)
    except (PermissionDenied, ValidationError) as exc:
        result['blocked_reason'] = _message(exc)
    else:
        result['can_confirm'] = True
    return result


def _require_receipt_delete_access(request):
    if not request or not (
        request_action_allowed(request, 'procurement_receipts', 'read')
        and request_action_allowed(request, 'procurement_receipts', 'delete')
        and request_action_allowed(request, 'procurement_orders', 'read')
    ):
        raise PermissionDenied('Receipt read/delete and purchase order read access are required to delete a receipt.')


def _require_pending_deletion(receipt):
    history = receipt.workflow_history
    if (receipt.status != 'pending' or not isinstance(history, list)
            or any(not isinstance(event, dict)
                   or event.get('action') in {'accept', 'reject_delivery', 'confirm_delivery'}
                   or event.get('status') in {'accepted', 'partial', 'rejected'} for event in history)):
        raise ReceivingConflict('Only unconfirmed pending receipts can be deleted. Confirmed or inspected evidence must be retained.')


def receipt_deletion(receipt, request):
    result = {'can_delete': False, 'blocked_reason': ''}
    try:
        _require_receipt_delete_access(request)
        _require_pending_deletion(receipt)
    except (PermissionDenied, ReceivingConflict) as exc:
        result['blocked_reason'] = _message(exc)
    else:
        result['can_delete'] = True
    return result


@transaction.atomic
def delete_pending_receipt(observed, request):
    """Remove a pending reservation while retaining its evidence in the audit store."""
    po, receipt = lock_receipt(observed)
    _require_receipt_delete_access(request)
    _require_pending_deletion(receipt)
    if not isinstance(request.data, dict):
        raise ValidationError('Submit a JSON object containing the saved timestamp.')
    unknown = set(request.data) - {'expected_updated_at'}
    if unknown:
        raise ValidationError({field: 'Receipt deletion accepts only its saved timestamp.' for field in sorted(unknown)})
    require_fresh(request.data.get('expected_updated_at'), receipt.updated_at, 'expected_updated_at')
    # This bounded snapshot contains receipt evidence only, no expanded people,
    # PO terms or file bytes. Attachments remain available to the retained audit.
    snapshot = {field.attname: getattr(receipt, field.attname) for field in receipt._meta.concrete_fields}
    snapshot = json.loads(json.dumps(snapshot, cls=DjangoJSONEncoder))
    ReceiptNumberService.retain_number(receipt.receipt_number)
    receipt.delete()
    po.save(update_fields=['updated_at'])
    create_audit_log(
        user=request.user, action='delete',
        resource_type='Receipt', resource_id=snapshot['id'], resource_repr=receipt.receipt_number,
        changes={'before': snapshot, 'after': None},
        metadata={'command': 'delete_pending_receipt', 'purchase_order_id': str(po.pk),
                  'receipt_number': receipt.receipt_number,
                  'operation_key': str(receipt.operation_key) if receipt.operation_key else None,
                  'actor_name': request.user.get_full_name() or request.user.get_username(),
                  'expected_updated_at': request.data['expected_updated_at']},
    )


@transaction.atomic
def confirm_receipt_delivery(observed, request):
    """The saved recorder explicitly confirms delivery, without inspecting it."""
    po, receipt = lock_receipt(observed)
    _require_recorder(request, receipt)
    if not isinstance(request.data, dict):
        raise ValidationError('Submit a JSON object containing the saved timestamp and optional notes.')
    unknown = set(request.data) - {'expected_updated_at', 'notes'}
    if unknown:
        raise ValidationError({field: 'Delivery confirmation accepts only its saved timestamp and optional notes.'
                               for field in sorted(unknown)})
    notes = request.data.get('notes', '')
    if not isinstance(notes, str) or len(notes) > 4000:
        raise ValidationError({'notes': 'Enter confirmation notes of at most 4000 characters.'})
    token = request.data.get('expected_updated_at')
    fingerprint = hashlib.sha256(json.dumps({'notes': notes}, sort_keys=True).encode()).hexdigest()
    last = (receipt.workflow_history or [{}])[-1]
    if (receipt.status in {'accepted', 'partial'} and isinstance(last, dict)
            and last.get('action') == 'confirm_delivery' and last.get('actor_id') == request.user.pk
            and last.get('expected_updated_at') == token):
        if last.get('decision_fingerprint') != fingerprint:
            raise ReceivingConflict('The confirmed delivery cannot be changed by replaying its confirmation.')
        return receipt
    if receipt.status != 'pending':
        raise ValidationError({'status': 'Only pending receipts can be confirmed.'})
    require_fresh(token, receipt.updated_at, 'expected_updated_at')
    receipt.items_received, receipt.status = _acceptance_items(po, receipt)
    _history(receipt, request, 'confirm_delivery', reason=notes.strip(), expected_updated_at=token,
             decision_fingerprint=fingerprint, actor_name=request.user.get_full_name() or request.user.get_username())
    receipt.save(update_fields=['status', 'items_received', 'workflow_history', 'updated_at'])
    po.save(update_fields=['updated_at'])
    receipt.purchase_order = po
    return receipt


@transaction.atomic
def record_receipt(validated, request, *, reconciliation=False):
    _require_access(request)
    data = dict(validated)
    token = data.pop('expected_po_updated_at', None)
    data.pop('expected_updated_at', None)
    reason = str(data.pop('reason', '') or '').strip()
    try:
        key = UUID(str(data.pop('operation_key', '') or ''))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError({'operation_key': 'A request UUID is required.'}) from exc
    if reconciliation and not reason:
        raise ValidationError({'reason': 'Record why completed-order receipt evidence is being reconciled.'})
    po = lock_purchase_order(data.pop('purchase_order'))
    fingerprint = hashlib.sha256(json.dumps({'payload': data, 'po': str(po.pk), 'reason': reason,
                                             'reconciliation': reconciliation}, sort_keys=True, default=str).encode()).hexdigest()
    existing = Receipt.objects.filter(operation_key=key).first()
    if existing:
        if existing.purchase_order_id != po.pk or existing.received_by_id != request.user.pk or existing.command_fingerprint != fingerprint:
            raise ReceivingConflict('This request identifier has already been used for a different operation.')
        return existing
    if AuditLog.objects.filter(resource_type='Receipt', action='delete',
                               metadata__command='delete_pending_receipt',
                               metadata__operation_key=str(key)).exists():
        raise ReceivingConflict('This request recorded a receipt that was deleted. Start a new receipt to record replacement evidence.')
    require_fresh(token, po.updated_at, 'expected_po_updated_at')
    summary = receiving_summary(po, request=request)
    capability = 'can_reconcile' if reconciliation else 'can_record'
    if not summary[capability]:
        raise ValidationError({'purchase_order': summary['blocked_reason'] or 'Use completed-order reconciliation for this purchase order.'})
    data['items_received'] = _canonical_items(data.get('items_received'), summary)
    data['status'] = 'pending'
    receipt = Receipt(purchase_order=po, received_by=request.user, operation_key=key,
                      command_fingerprint=fingerprint, **data)
    _history(receipt, request, 'reconciled' if reconciliation else 'recorded',
             reason=reason, po_status=po.status, po_updated_at=po.updated_at.isoformat(),
             basis=summary['basis'], items_received=data['items_received'], receipt_date=receipt.receipt_date.isoformat())
    receipt.receipt_number = ReceiptNumberService.next_number()
    try:
        with transaction.atomic():
            receipt.save()
    except IntegrityError as exc:
        if Receipt.objects.filter(operation_key=key).exists():
            raise ReceivingConflict('This request identifier has already been used.') from exc
        raise
    po.save(update_fields=['updated_at'])
    return receipt


@transaction.atomic
def decide_receipt(observed, request, *, accept):
    from apps.rbac.approval_eligibility import require_configured_approval

    po, receipt = lock_receipt(observed)
    operation, target = ('accept', 'accepted') if accept else ('reject_delivery', 'rejected')
    # Check authority against the actual pending snapshot; replay is handled only
    # after the original actor and exact original freshness token are verified.
    token = request.data.get('expected_updated_at')
    decision_fingerprint = hashlib.sha256(json.dumps({key: value for key, value in request.data.items()
                                                     if key != 'expected_updated_at'}, sort_keys=True, default=str).encode()).hexdigest()
    last = (receipt.workflow_history or [{}])[-1]
    replay_states = {'accepted', 'partial'} if accept else {'rejected'}
    if receipt.status in replay_states and last.get('action') == operation and last.get('actor_id') == request.user.pk and last.get('expected_updated_at') == token:
        if not request_action_allowed(request, 'procurement_receipts', 'approve'):
            raise PermissionDenied('Receipt approval access is required.')
        pending_snapshot = copy(receipt)
        pending_snapshot.status = 'pending'
        require_configured_approval(request.user, 'procurement_receipts', pending_snapshot, operation)
        if last.get('decision_fingerprint') != decision_fingerprint:
            raise ReceivingConflict('The inspected record cannot be changed by replaying its decision.')
        return receipt
    if receipt.status != 'pending':
        raise ValidationError({'status': 'Only pending receipts can be inspected.'})
    require_configured_approval(request.user, 'procurement_receipts', receipt, operation)
    require_fresh(token, receipt.updated_at, 'expected_updated_at')
    reason = request.data.get('reason') or request.data.get('notes') or ''
    if not isinstance(reason, str) or len(reason) > 4000:
        raise ValidationError({'reason': 'Enter a reason of at most 4000 characters.'})
    reason = reason.strip()
    if not accept and not reason:
        raise ValidationError({'reason': 'Record the rejection reason.'})
    if accept:
        receipt.items_received, target = _acceptance_items(po, receipt)
    inspection = {}
    for field in INSPECTION_FLAGS:
        if field in request.data:
            value = request.data[field]
            if value is not None and type(value) is not bool:
                raise ValidationError({field: 'Use true, false or null.'})
            inspection[field] = {'before': getattr(receipt, field), 'after': value}
            setattr(receipt, field, value)
    if 'inspection_notes' in request.data:
        notes = request.data['inspection_notes']
        if not isinstance(notes, str) or len(notes) > 4000:
            raise ValidationError({'inspection_notes': 'Enter inspection notes of at most 4000 characters.'})
        inspection['inspection_notes'] = {'before': receipt.inspection_notes, 'after': notes}
        receipt.inspection_notes = notes
    receipt.status = target
    if not accept:
        receipt.inspection_notes = reason
        receipt.quality_check_passed = False
    _history(receipt, request, operation, reason=reason, expected_updated_at=token,
             decision_fingerprint=decision_fingerprint, inspection=inspection)
    receipt.save(update_fields=['status', 'items_received', 'inspection_notes', *INSPECTION_FLAGS, 'workflow_history', 'updated_at'])
    # Receiving state is derived separately. A receipt never closes the entire PO.
    po.save(update_fields=['updated_at'])
    receipt.purchase_order = po
    return receipt


@transaction.atomic
def update_pending_receipt(observed, validated, request):
    data = dict(validated)
    token = data.pop('expected_updated_at', None)
    po, receipt = lock_receipt(observed)
    if receipt.status != 'pending':
        raise ValidationError({'status': 'Inspected receipt evidence is read-only.'})
    require_fresh(token, receipt.updated_at, 'expected_updated_at')
    if 'receipt_date' in data:
        raise ValidationError({'receipt_date': 'The recorded receipt date cannot be changed. Reject incorrect evidence and record its replacement.'})
    if any(key in data for key in ('purchase_order', 'items_received', 'operation_key', 'expected_po_updated_at', 'reason')):
        raise ValidationError('Order and recorded receipt lines cannot be replaced. Reject incorrect evidence and record its replacement.')
    data.pop('status', None)
    changes = {key: {'before': getattr(receipt, key), 'after': value} for key, value in data.items()}
    for key, value in data.items():
        setattr(receipt, key, value)
    _history(receipt, request, 'updated', changes=changes)
    receipt.save(update_fields=[*data, 'workflow_history', 'updated_at'])
    return receipt
