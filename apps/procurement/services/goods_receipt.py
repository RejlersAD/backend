"""Transactional PO-to-Goods-Receipt quantity and lifecycle rules."""

from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from ..models import PurchaseOrder, PurchaseOrderLine, Receipt, ReceiptLine
from .goods_receipt_numbering import GoodsReceiptNumberService


ZERO = Decimal('0')


class GoodsReceiptService:
    RECEIVABLE_PO_STATUSES = {'sent', 'acknowledged', 'in_progress', 'partially_received'}
    RECEIPT_RESERVATION_STATUSES = {'pending'}
    RECEIPT_ACCEPTED_STATUSES = {'accepted', 'partial'}

    @staticmethod
    def _decimal(value, field):
        try:
            result = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValidationError({field: 'Enter a valid quantity.'}) from exc
        if result < ZERO:
            raise ValidationError({field: 'Quantity cannot be negative.'})
        return result

    @classmethod
    def ensure_po_lines(cls, po):
        """Materialize legacy JSON items as stable PO lines when necessary."""
        existing = list(po.lines.order_by('line_number'))
        if existing:
            return existing

        items = po.items if isinstance(po.items, list) else []
        created = []
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            quantity = cls._decimal(
                item.get('ordered_quantity', item.get('quantity', item.get('qty', 0))),
                f'items.{index}.quantity',
            )
            if quantity <= ZERO:
                continue
            created.append(PurchaseOrderLine(
                purchase_order=po,
                line_number=index,
                item_code=str(item.get('item_code') or item.get('code') or '').strip(),
                description=str(
                    item.get('description') or item.get('item') or item.get('name') or f'PO line {index}'
                ).strip()[:500],
                line_type=str(item.get('line_type') or 'goods'),
                ordered_quantity=quantity,
                unit_of_measure=str(item.get('unit_of_measure') or item.get('uom') or 'EA')[:30],
                unit_price=cls._decimal(item.get('unit_price', item.get('price', 0)), f'items.{index}.unit_price'),
                receipt_tolerance_percentage=cls._decimal(
                    item.get('receipt_tolerance_percentage', 0),
                    f'items.{index}.receipt_tolerance_percentage',
                ),
            ))
        if created:
            PurchaseOrderLine.objects.bulk_create(created)
        return list(po.lines.order_by('line_number'))

    @classmethod
    def _accepted_by_line(cls, po, exclude_receipt_id=None):
        queryset = ReceiptLine.objects.filter(
            purchase_order_line__purchase_order=po,
            receipt__status__in=cls.RECEIPT_ACCEPTED_STATUSES,
        )
        if exclude_receipt_id:
            queryset = queryset.exclude(receipt_id=exclude_receipt_id)
        return {
            row['purchase_order_line_id']: row['total'] or ZERO
            for row in queryset.values('purchase_order_line_id').annotate(total=Sum('accepted_quantity'))
        }

    @classmethod
    def _reserved_by_line(cls, po, exclude_receipt_id=None):
        queryset = ReceiptLine.objects.filter(
            purchase_order_line__purchase_order=po,
            receipt__status__in=cls.RECEIPT_RESERVATION_STATUSES,
        )
        if exclude_receipt_id:
            queryset = queryset.exclude(receipt_id=exclude_receipt_id)
        return {
            row['purchase_order_line_id']: row['total'] or ZERO
            for row in queryset.values('purchase_order_line_id').annotate(total=Sum('delivered_quantity'))
        }

    @classmethod
    def _validate_lines(cls, po, lines_data, *, include_reservations=True, exclude_receipt_id=None):
        if not isinstance(lines_data, list) or not lines_data:
            raise ValidationError({'lines': 'At least one receipt line is required.'})

        po_lines = {str(line.id): line for line in cls.ensure_po_lines(po)}
        accepted = cls._accepted_by_line(po, exclude_receipt_id)
        reserved = cls._reserved_by_line(po, exclude_receipt_id) if include_reservations else {}
        normalized = []
        seen = set()

        for index, data in enumerate(lines_data, start=1):
            line_value = data.get('purchase_order_line') or data.get('purchase_order_line_id') or ''
            line_id = str(getattr(line_value, 'pk', line_value))
            po_line = po_lines.get(line_id)
            if not po_line:
                raise ValidationError({'lines': f'Line {index} does not belong to this purchase order.'})
            if line_id in seen:
                raise ValidationError({'lines': f'PO line {po_line.line_number} is duplicated.'})
            seen.add(line_id)

            delivered = cls._decimal(data.get('delivered_quantity'), f'lines.{index}.delivered_quantity')
            accepted_now = cls._decimal(data.get('accepted_quantity', delivered), f'lines.{index}.accepted_quantity')
            rejected_now = cls._decimal(data.get('rejected_quantity', 0), f'lines.{index}.rejected_quantity')
            if delivered <= ZERO:
                raise ValidationError({'lines': f'PO line {po_line.line_number} must have a positive delivered quantity.'})
            if accepted_now + rejected_now > delivered:
                raise ValidationError({'lines': f'PO line {po_line.line_number} disposition exceeds delivered quantity.'})

            tolerance = po_line.ordered_quantity * po_line.receipt_tolerance_percentage / Decimal('100')
            allowed = po_line.ordered_quantity + tolerance
            used = accepted.get(po_line.id, ZERO) + reserved.get(po_line.id, ZERO)
            if used + delivered > allowed:
                remaining = max(allowed - used, ZERO)
                raise ValidationError({
                    'lines': (
                        f'PO line {po_line.line_number} exceeds its receivable balance. '
                        f'Maximum available is {remaining} {po_line.unit_of_measure}.'
                    )
                })

            normalized.append((po_line, delivered, accepted_now, rejected_now, data))
        return normalized

    @classmethod
    @transaction.atomic
    def create(cls, validated_data, actor):
        po_value = validated_data.pop('purchase_order')
        po_id = getattr(po_value, 'pk', po_value)
        po = get_object_or_404(PurchaseOrder.objects.select_for_update(), pk=po_id)
        if po.status not in cls.RECEIVABLE_PO_STATUSES:
            raise ValidationError({'purchase_order': f'PO status {po.status} is not receivable.'})

        delivery_note = str(validated_data.get('delivery_note_number') or '').strip()
        if delivery_note and Receipt.objects.filter(
            purchase_order=po,
            delivery_note_number__iexact=delivery_note,
        ).exclude(status='cancelled').exists():
            raise ValidationError({
                'delivery_note_number': 'This delivery note is already recorded for the purchase order.'
            })

        lines_data = validated_data.pop('lines', [])
        normalized = cls._validate_lines(po, lines_data, include_reservations=True)
        receipt = Receipt.objects.create(
            **validated_data,
            purchase_order=po,
            receipt_number=GoodsReceiptNumberService.next_number(),
            received_by=actor,
            status='draft',
            quality_check_passed=None,
        )
        compatibility_items = []
        for po_line, delivered, accepted, rejected, data in normalized:
            ReceiptLine.objects.create(
                receipt=receipt,
                purchase_order_line=po_line,
                delivered_quantity=delivered,
                accepted_quantity=accepted,
                rejected_quantity=rejected,
                rejection_reason=str(data.get('rejection_reason') or ''),
                batch_number=str(data.get('batch_number') or ''),
                heat_number=str(data.get('heat_number') or ''),
                serial_numbers=data.get('serial_numbers') or [],
                inspection_notes=str(data.get('inspection_notes') or ''),
            )
            compatibility_items.append({
                'po_line_id': str(po_line.id),
                'line_number': po_line.line_number,
                'item': po_line.description,
                'ordered_qty': str(po_line.ordered_quantity),
                'received_qty': str(delivered),
                'accepted_qty': str(accepted),
                'rejected_qty': str(rejected),
                'uom': po_line.unit_of_measure,
            })
        receipt.items_received = compatibility_items
        receipt.save(update_fields=['items_received', 'updated_at'])
        return receipt

    @classmethod
    @transaction.atomic
    def submit(cls, receipt_id, actor):
        receipt = get_object_or_404(
            Receipt.objects.select_for_update().select_related('purchase_order'),
            pk=receipt_id,
        )
        if receipt.status != 'draft':
            raise ValidationError({'status': 'Only draft receipts can be submitted.'})
        po = PurchaseOrder.objects.select_for_update().get(pk=receipt.purchase_order_id)
        lines_data = [
            {
                'purchase_order_line': str(line.purchase_order_line_id),
                'delivered_quantity': line.delivered_quantity,
                'accepted_quantity': line.accepted_quantity,
                'rejected_quantity': line.rejected_quantity,
            }
            for line in receipt.lines.select_related('purchase_order_line')
        ]
        cls._validate_lines(po, lines_data, include_reservations=True, exclude_receipt_id=receipt.id)
        receipt.status = 'pending'
        receipt.submitted_at = timezone.now()
        receipt.save(update_fields=['status', 'submitted_at', 'updated_at'])
        if po.status in {'sent', 'acknowledged'}:
            po.status = 'in_progress'
            po.save(update_fields=['status', 'updated_at'])
        return receipt

    @classmethod
    def _recalculate_po_locked(cls, po):
        lines = cls.ensure_po_lines(po)
        accepted = cls._accepted_by_line(po)
        total_accepted = sum((accepted.get(line.id, ZERO) for line in lines), ZERO)
        all_complete = bool(lines) and all(
            accepted.get(line.id, ZERO) >= line.ordered_quantity for line in lines
        )
        if all_complete:
            po.status = 'completed'
            po.actual_delivery = timezone.localdate()
        elif total_accepted > ZERO:
            po.status = 'partially_received'
            po.actual_delivery = None
        elif po.status in {'completed', 'partially_received'}:
            po.status = 'in_progress'
            po.actual_delivery = None
        po.save(update_fields=['status', 'actual_delivery', 'updated_at'])

    @classmethod
    @transaction.atomic
    def accept(cls, receipt_id, actor):
        receipt = get_object_or_404(Receipt.objects.select_for_update(), pk=receipt_id)
        if receipt.status != 'pending':
            raise ValidationError({'status': 'Only pending receipts can be accepted.'})
        po = PurchaseOrder.objects.select_for_update().get(pk=receipt.purchase_order_id)
        lines = list(receipt.lines.select_for_update())
        if not lines:
            raise ValidationError({'lines': 'Receipt has no lines.'})
        for line in lines:
            if line.accepted_quantity + line.rejected_quantity != line.delivered_quantity:
                raise ValidationError({
                    'lines': 'Every delivered quantity must be fully accepted or rejected before inspection is completed.'
                })
            if line.rejected_quantity > ZERO and not line.rejection_reason.strip():
                raise ValidationError({'lines': 'A rejection reason is required for rejected quantities.'})

        has_rejection = any(line.rejected_quantity > ZERO for line in lines)
        receipt.status = 'partial' if has_rejection else 'accepted'
        receipt.quality_check_passed = not has_rejection
        receipt.inspected_by = actor
        receipt.inspected_at = timezone.now()
        receipt.save(update_fields=['status', 'quality_check_passed', 'inspected_by', 'inspected_at', 'updated_at'])
        cls._recalculate_po_locked(po)
        return receipt

    @classmethod
    @transaction.atomic
    def reject(cls, receipt_id, actor, reason):
        receipt = get_object_or_404(Receipt.objects.select_for_update(), pk=receipt_id)
        if receipt.status != 'pending':
            raise ValidationError({'status': 'Only pending receipts can be rejected.'})
        reason = str(reason or '').strip()
        if len(reason) < 10:
            raise ValidationError({'reason': 'Provide a rejection reason of at least 10 characters.'})
        po = PurchaseOrder.objects.select_for_update().get(pk=receipt.purchase_order_id)
        for line in receipt.lines.select_for_update():
            line.accepted_quantity = ZERO
            line.rejected_quantity = line.delivered_quantity
            line.rejection_reason = reason
            line.save(update_fields=['accepted_quantity', 'rejected_quantity', 'rejection_reason', 'updated_at'])
        receipt.status = 'rejected'
        receipt.quality_check_passed = False
        receipt.inspection_notes = reason
        receipt.inspected_by = actor
        receipt.inspected_at = timezone.now()
        receipt.save(update_fields=[
            'status', 'quality_check_passed', 'inspection_notes', 'inspected_by', 'inspected_at', 'updated_at'
        ])
        cls._recalculate_po_locked(po)
        return receipt

    @classmethod
    @transaction.atomic
    def cancel(cls, receipt_id, actor, reason):
        receipt = get_object_or_404(Receipt.objects.select_for_update(), pk=receipt_id)
        if receipt.status not in {'draft', 'pending'}:
            raise ValidationError({'status': 'Only draft or pending receipts can be cancelled.'})
        reason = str(reason or '').strip()
        if len(reason) < 5:
            raise ValidationError({'reason': 'Provide a cancellation reason.'})
        po = PurchaseOrder.objects.select_for_update().get(pk=receipt.purchase_order_id)
        receipt.status = 'cancelled'
        receipt.cancelled_at = timezone.now()
        receipt.cancellation_reason = reason
        receipt.save(update_fields=['status', 'cancelled_at', 'cancellation_reason', 'updated_at'])
        cls._recalculate_po_locked(po)
        return receipt

    @classmethod
    def receiving_summary(cls, po):
        lines = cls.ensure_po_lines(po)
        accepted = cls._accepted_by_line(po)
        reserved = cls._reserved_by_line(po)
        payload_lines = []
        ordered_total = accepted_total = ZERO
        for line in lines:
            accepted_quantity = accepted.get(line.id, ZERO)
            reserved_quantity = reserved.get(line.id, ZERO)
            remaining = max(line.ordered_quantity - accepted_quantity - reserved_quantity, ZERO)
            ordered_total += line.ordered_quantity
            accepted_total += accepted_quantity
            payload_lines.append({
                'id': str(line.id),
                'line_number': line.line_number,
                'item_code': line.item_code,
                'description': line.description,
                'line_type': line.line_type,
                'ordered_quantity': line.ordered_quantity,
                'accepted_quantity': accepted_quantity,
                'reserved_quantity': reserved_quantity,
                'remaining_quantity': remaining,
                'unit_of_measure': line.unit_of_measure,
                'unit_price': line.unit_price,
            })
        progress = (accepted_total / ordered_total * Decimal('100')) if ordered_total else ZERO
        receipts = [
            {
                'id': str(receipt.id),
                'receipt_number': receipt.receipt_number,
                'receipt_date': receipt.receipt_date,
                'status': receipt.status,
                'status_display': receipt.get_status_display(),
                'delivery_note_number': receipt.delivery_note_number,
            }
            for receipt in po.receipts.exclude(status='cancelled').order_by('-receipt_date', '-created_at')
        ]
        return {
            'purchase_order_id': str(po.id),
            'po_number': po.po_number,
            'status': po.status,
            'ordered_quantity': ordered_total,
            'accepted_quantity': accepted_total,
            'remaining_quantity': sum(
                (line['remaining_quantity'] for line in payload_lines), ZERO
            ),
            'receipt_progress': progress.quantize(Decimal('0.01')),
            'can_receive': po.status in cls.RECEIVABLE_PO_STATUSES and any(
                line['remaining_quantity'] > ZERO for line in payload_lines
            ),
            'lines': payload_lines,
            'receipts': receipts,
        }
