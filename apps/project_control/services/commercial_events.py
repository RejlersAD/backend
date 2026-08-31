"""Idempotent commercial-event ingestion shared by signals and backfills."""
import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.project_control.models import CommercialEvent
from apps.project_control.services.cost_ledger import rebuild_project_ledger

logger = logging.getLogger(__name__)


def record_commercial_event(*, project, event_key, event_type, source_type,
                            source_id, source_reference='', amount=None,
                            currency='', event_at=None, actor=None, payload=None):
    event, created = CommercialEvent.objects.get_or_create(
        event_key=event_key,
        defaults={
            'project': project,
            'event_type': event_type,
            'source_type': source_type,
            'source_id': str(source_id),
            'source_reference': str(source_reference or '')[:160],
            'amount': Decimal(str(amount)) if amount is not None else None,
            'currency': str(currency or '')[:8],
            'event_at': event_at or timezone.now(),
            'actor': actor,
            'payload': payload or {},
        },
    )
    if not created or not project:
        return event, created
    try:
        rebuild_project_ledger(project, user=actor)
        CommercialEvent.objects.filter(pk=event.pk).update(ledger_rebuilt=True)
        event.ledger_rebuilt = True
    except Exception as exc:  # event audit must survive a failed projection
        logger.exception('Commercial ledger projection failed for %s', event_key)
        CommercialEvent.objects.filter(pk=event.pk).update(processing_error=str(exc)[:2000])
        event.processing_error = str(exc)
    return event, created


def safe_after_commit(callback):
    def guarded():
        try:
            callback()
        except Exception:
            logger.exception('Commercial event capture failed')
    transaction.on_commit(guarded)


def projects_for_invoice(invoice):
    projects = {}
    allocations = invoice.po_allocations.select_related('purchase_order__enterprise_project').all()
    for allocation in allocations:
        project = allocation.purchase_order.enterprise_project
        if project:
            projects[str(project.pk)] = project
    return list(projects.values())


def capture_historical_commercial_events(*, project=None):
    """Create all presently-provable events. Safe to run repeatedly."""
    from apps.finance.models import Invoice, InvoiceMatchStatus, PayablePayment
    from apps.procurement.models import PurchaseOrder, Receipt

    counts = {'po_approved': 0, 'receipt_accepted': 0, 'invoice_approved': 0,
              'invoice_verified': 0, 'payment': 0}
    pos = PurchaseOrder.objects.select_related('enterprise_project', 'approved_by').filter(approved_at__isnull=False)
    if project:
        pos = pos.filter(enterprise_project=project)
    for po in pos.iterator():
        _, created = record_commercial_event(
            project=po.enterprise_project, event_key=f'po:{po.pk}:approved', event_type='po_approved',
            source_type='purchase_order', source_id=po.pk, source_reference=po.po_number,
            amount=po.total_amount, currency=po.currency, event_at=po.approved_at, actor=po.approved_by,
        )
        counts['po_approved'] += int(created)

    receipts = Receipt.objects.select_related('purchase_order__enterprise_project', 'received_by').filter(status__in=['accepted', 'partial'])
    if project:
        receipts = receipts.filter(purchase_order__enterprise_project=project)
    for receipt in receipts.iterator():
        _, created = record_commercial_event(
            project=receipt.purchase_order.enterprise_project,
            event_key=f'receipt:{receipt.pk}:{receipt.status}', event_type='receipt_accepted',
            source_type='receipt', source_id=receipt.pk, source_reference=receipt.receipt_number,
            event_at=receipt.updated_at, actor=receipt.received_by, payload={'status': receipt.status},
        )
        counts['receipt_accepted'] += int(created)

    invoices = Invoice.objects.prefetch_related('po_allocations__purchase_order__enterprise_project')
    for invoice in invoices:
        projects = projects_for_invoice(invoice)
        if project:
            projects = [item for item in projects if item.pk == project.pk]
        for item in projects:
            if invoice.status in ('approved', 'processed') or invoice.procurement_status in ('approved_for_payment', 'closed'):
                _, created = record_commercial_event(
                    project=item, event_key=f'invoice:{invoice.pk}:approved:{item.pk}', event_type='invoice_approved',
                    source_type='invoice', source_id=invoice.pk, source_reference=invoice.invoice_number,
                    amount=invoice.total_amount, currency=invoice.currency, event_at=invoice.updated_at,
                )
                counts['invoice_approved'] += int(created)
            for allocation in invoice.po_allocations.filter(match_status=InvoiceMatchStatus.VERIFIED, purchase_order__enterprise_project=item):
                _, created = record_commercial_event(
                    project=item, event_key=f'invoice-allocation:{allocation.pk}:verified', event_type='invoice_verified',
                    source_type='invoice_allocation', source_id=allocation.pk, source_reference=invoice.invoice_number,
                    amount=allocation.allocated_amount, currency=allocation.currency,
                    event_at=allocation.verified_at or allocation.updated_at, actor=allocation.verified_by,
                )
                counts['invoice_verified'] += int(created)
            for payment in PayablePayment.objects.filter(invoice=invoice):
                event_type = {'schedule': 'payment_scheduled', 'payment': 'payment_recorded', 'hold': 'payment_held',
                              'release': 'payment_released', 'cancel': 'payment_cancelled'}[payment.operation]
                _, created = record_commercial_event(
                    project=item, event_key=f'payable:{payment.pk}:{item.pk}', event_type=event_type,
                    source_type='payable_payment', source_id=payment.pk, source_reference=payment.reference or invoice.invoice_number,
                    amount=payment.amount, currency=payment.currency, event_at=payment.created_at,
                    actor=payment.created_by, payload={'invoice_id': invoice.pk, 'operation': payment.operation},
                )
                counts['payment'] += int(created)
    return counts
