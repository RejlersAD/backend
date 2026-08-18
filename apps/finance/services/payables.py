"""A/P reconciliation, three-way matching, and payment ledger operations."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
import re

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.exceptions import ValidationError

from apps.finance.models import (
    AuditLog,
    Invoice,
    InvoiceMatchStatus,
    InvoicePaymentStatus,
    InvoicePurchaseOrderAllocation,
    InvoiceStatus,
    PayablePayment,
    ProcurementInvoiceStatus,
)


def _decimal(value, default=Decimal('0')):
    try:
        return Decimal(str(value)) if value not in (None, '') else default
    except (InvalidOperation, TypeError, ValueError):
        return default


def _key(value):
    return re.sub(r'[^a-z0-9]', '', str(value or '').lower())


def _item_reference(item):
    return _key(
        item.get('po_item_reference') or item.get('item_reference') or
        item.get('item_no') or item.get('line_number') or item.get('item') or
        item.get('description')
    )


def _quantity(item, *names):
    for name in names:
        if item.get(name) not in (None, ''):
            return _decimal(item.get(name))
    return Decimal('0')


def evaluate_three_way_match(allocation: InvoicePurchaseOrderAllocation, *, user=None):
    """Evaluate PO ↔ invoice lines ↔ accepted receipt quantities and values."""
    invoice = allocation.invoice
    po = allocation.purchase_order
    tolerance = allocation.tolerance_percentage or Decimal('0')
    accepted_receipts = list(po.receipts.filter(status__in=('accepted', 'partial')))

    po_items = po.items if isinstance(po.items, list) else []
    invoice_items = list(invoice.structured_line_items.all())
    received_by_ref = defaultdict(Decimal)
    for receipt in accepted_receipts:
        for item in receipt.items_received if isinstance(receipt.items_received, list) else []:
            received_by_ref[_item_reference(item)] += _quantity(
                item, 'accepted_qty', 'received_qty', 'quantity', 'qty'
            )

    po_by_ref = {_item_reference(item): item for item in po_items if _item_reference(item)}
    line_checks = []
    line_items_matched = bool(invoice_items and po_items)
    receipt_quantities_matched = bool(invoice_items and accepted_receipts)
    for line in invoice_items:
        ref = _key(line.po_item_reference or line.line_number or line.description)
        po_item = po_by_ref.get(ref)
        invoice_qty = line.quantity or Decimal('0')
        ordered_qty = _quantity(po_item or {}, 'quantity', 'qty', 'ordered_qty')
        received_qty = received_by_ref.get(ref, Decimal('0'))
        po_line_found = po_item is not None
        quantity_within_po = po_line_found and (not invoice_qty or invoice_qty <= ordered_qty)
        quantity_received = bool(accepted_receipts) and (not invoice_qty or invoice_qty <= received_qty)
        line_items_matched = line_items_matched and po_line_found and quantity_within_po
        receipt_quantities_matched = receipt_quantities_matched and quantity_received
        line_checks.append({
            'invoice_line': line.line_number,
            'reference': line.po_item_reference or str(line.line_number),
            'description': line.description,
            'invoice_quantity': str(invoice_qty),
            'ordered_quantity': str(ordered_qty),
            'accepted_quantity': str(received_qty),
            'po_line_found': po_line_found,
            'quantity_within_po': quantity_within_po,
            'quantity_received': quantity_received,
        })

    prior_allocated = po.invoice_allocations.exclude(pk=allocation.pk).aggregate(
        total=Sum('allocated_amount')
    )['total'] or Decimal('0')
    available = max(Decimal('0'), po.total_amount - prior_allocated)
    amount_variance = allocation.allocated_amount - available
    amount_within_tolerance = allocation.allocated_amount <= (
        available * (Decimal('1') + tolerance / Decimal('100'))
    )
    vendor_matched = invoice.vendor_id == po.vendor_id
    currency_matched = invoice.currency.upper() == po.currency.upper()
    receipt_required = allocation.receipt_required

    exceptions = []
    if not vendor_matched:
        exceptions.append('vendor_mismatch')
    if not currency_matched:
        exceptions.append('currency_mismatch')
    if not amount_within_tolerance:
        exceptions.append('amount_exceeds_po_tolerance')
    if receipt_required and not accepted_receipts:
        exceptions.append('missing_accepted_receipt')
    if invoice_items and not line_items_matched:
        exceptions.append('invoice_po_line_mismatch')
    if receipt_required and invoice_items and accepted_receipts and not receipt_quantities_matched:
        exceptions.append('invoice_quantity_exceeds_receipt')

    verified = not exceptions and (not receipt_required or bool(accepted_receipts))
    allocation.po_amount_at_match = po.total_amount
    allocation.invoice_amount_at_match = invoice.total_amount
    allocation.amount_variance = amount_variance
    allocation.amount_within_tolerance = amount_within_tolerance
    allocation.vendor_matched = vendor_matched
    allocation.currency_matched = currency_matched
    allocation.line_items_matched = line_items_matched
    allocation.receipt_quantities_matched = receipt_quantities_matched
    allocation.exception_codes = exceptions
    allocation.match_evidence = {
        'evaluated_at': timezone.now().isoformat(),
        'po_total': str(po.total_amount),
        'available_po_value': str(available),
        'invoice_total': str(invoice.total_amount or 0),
        'accepted_receipts': [r.receipt_number for r in accepted_receipts],
        'line_checks': line_checks,
    }
    allocation.match_status = InvoiceMatchStatus.VERIFIED if verified else InvoiceMatchStatus.EXCEPTION
    if verified:
        allocation.verified_by = user
        allocation.verified_at = timezone.now()
    allocation.save()
    allocation.receipts.set(accepted_receipts)
    reconcile_invoice_status(invoice, user=user, audit=False)
    return allocation


def reconcile_invoice_status(invoice: Invoice, *, user=None, audit=True, persist=True):
    """Make legacy, procurement, match, and payment states agree with evidence."""
    changed = []
    total = invoice.total_amount or Decimal('0')
    paid = invoice.paid_amount or Decimal('0')
    allocations = list(invoice.po_allocations.all())

    if invoice.payment_status not in (InvoicePaymentStatus.CANCELLED, InvoicePaymentStatus.ON_HOLD):
        payment_status = (
            InvoicePaymentStatus.PAID if total > 0 and paid >= total else
            InvoicePaymentStatus.PARTIAL if paid > 0 else
            InvoicePaymentStatus.SCHEDULED if invoice.scheduled_payment_date else
            InvoicePaymentStatus.NOT_SCHEDULED
        )
        if invoice.payment_status != payment_status:
            invoice.payment_status = payment_status
            changed.append('payment_status')

    match_status = (
        InvoiceMatchStatus.UNMATCHED if not allocations else
        InvoiceMatchStatus.EXCEPTION if any(a.match_status == InvoiceMatchStatus.EXCEPTION for a in allocations) else
        InvoiceMatchStatus.VERIFIED if all(a.match_status == InvoiceMatchStatus.VERIFIED for a in allocations) else
        InvoiceMatchStatus.MANUAL_MATCHED
    )
    if invoice.match_status != match_status:
        invoice.match_status = match_status
        changed.append('match_status')

    if invoice.status == InvoiceStatus.REJECTED:
        procurement_status = ProcurementInvoiceStatus.REJECTED
    elif invoice.payment_status == InvoicePaymentStatus.PAID:
        procurement_status = ProcurementInvoiceStatus.CLOSED
    elif (
        invoice.status in (InvoiceStatus.APPROVED, InvoiceStatus.PROCESSED) or
        invoice.procurement_status == ProcurementInvoiceStatus.APPROVED_FOR_PAYMENT
    ):
        procurement_status = ProcurementInvoiceStatus.APPROVED_FOR_PAYMENT
    elif match_status == InvoiceMatchStatus.VERIFIED:
        procurement_status = ProcurementInvoiceStatus.FINANCE_REVIEW
    elif allocations:
        procurement_status = ProcurementInvoiceStatus.PROCUREMENT_REVIEW
    elif invoice.manual_review_required:
        procurement_status = ProcurementInvoiceStatus.OCR_REVIEW
    else:
        procurement_status = ProcurementInvoiceStatus.READY_FOR_MATCHING
    if invoice.procurement_status != procurement_status:
        invoice.procurement_status = procurement_status
        changed.append('procurement_status')

    if changed and persist:
        invoice.save(update_fields=[*changed, 'updated_at'])
        if audit:
            AuditLog.objects.create(
                invoice=invoice, user=user, action='ap_status_reconciled',
                description='A/P lifecycle statuses reconciled from persisted evidence.',
                metadata={'changed_fields': changed},
            )
    return changed


@transaction.atomic
def record_payment_operation(invoice: Invoice, payload: dict, user):
    operation = payload.get('operation')
    valid = {choice for choice, _ in PayablePayment.Operation.choices}
    if operation not in valid:
        raise ValidationError({'operation': f'Select one of: {", ".join(sorted(valid))}.'})
    amount = _decimal(payload.get('amount'), None)
    if operation in (PayablePayment.Operation.SCHEDULE, PayablePayment.Operation.PAYMENT):
        if invoice.procurement_status not in (
            ProcurementInvoiceStatus.APPROVED_FOR_PAYMENT,
            ProcurementInvoiceStatus.CLOSED,
        ):
            raise ValidationError({'operation': 'Invoice must be approved for payment first.'})
        if invoice.payment_status in (InvoicePaymentStatus.ON_HOLD, InvoicePaymentStatus.CANCELLED):
            raise ValidationError({'operation': 'Release the hold/cancellation before scheduling or paying.'})
    if operation == PayablePayment.Operation.PAYMENT and (amount is None or amount <= 0):
        raise ValidationError({'amount': 'A positive payment amount is required.'})
    remaining = max(Decimal('0'), (invoice.total_amount or Decimal('0')) - (invoice.paid_amount or Decimal('0')))
    if operation == PayablePayment.Operation.PAYMENT and amount > remaining:
        raise ValidationError({'amount': f'Payment exceeds the remaining payable amount ({remaining}).'})
    raw_effective_date = payload.get('effective_date')
    effective_date = parse_date(raw_effective_date) if isinstance(raw_effective_date, str) else raw_effective_date
    effective_date = effective_date or timezone.localdate()
    if raw_effective_date and isinstance(raw_effective_date, str) and parse_date(raw_effective_date) is None:
        raise ValidationError({'effective_date': 'Use a valid YYYY-MM-DD date.'})
    if operation == PayablePayment.Operation.SCHEDULE and not payload.get('effective_date'):
        raise ValidationError({'effective_date': 'A scheduled payment date is required.'})
    reference = str(payload.get('reference') or '').strip()
    if operation == PayablePayment.Operation.PAYMENT and not reference:
        raise ValidationError({'reference': 'A bank or payment reference is required.'})

    entry = PayablePayment.objects.create(
        invoice=invoice, operation=operation, amount=amount,
        currency=invoice.currency, effective_date=effective_date,
        reference=reference[:150], notes=str(payload.get('notes') or ''), created_by=user,
    )
    if operation == PayablePayment.Operation.PAYMENT:
        invoice.paid_amount = (invoice.paid_amount or Decimal('0')) + amount
        invoice.payment_date = effective_date
        invoice.payment_reference = reference[:150]
    elif operation == PayablePayment.Operation.SCHEDULE:
        invoice.scheduled_payment_date = effective_date
        invoice.payment_status = InvoicePaymentStatus.SCHEDULED
    elif operation == PayablePayment.Operation.HOLD:
        invoice.payment_status = InvoicePaymentStatus.ON_HOLD
    elif operation == PayablePayment.Operation.RELEASE:
        invoice.payment_status = InvoicePaymentStatus.NOT_SCHEDULED
    elif operation == PayablePayment.Operation.CANCEL:
        invoice.payment_status = InvoicePaymentStatus.CANCELLED
    invoice.save()
    reconcile_invoice_status(invoice, user=user, audit=False)
    AuditLog.objects.create(
        invoice=invoice, user=user, action=f'payable_{operation}',
        description=f'Payable operation recorded: {operation}.',
        metadata={'operation_id': str(entry.id), 'amount': str(amount) if amount else None, 'reference': reference},
    )
    return entry
