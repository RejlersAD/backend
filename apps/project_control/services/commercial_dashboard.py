"""Shared Project/Procurement/Finance commercial read model."""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, Sum

from apps.finance.models import InvoicePurchaseOrderAllocation, InvoiceMatchStatus, PayablePayment
from apps.procurement.models import PurchaseOrder, Receipt
from apps.project_control.models import CommercialEvent, CostLedgerEntry
from apps.project_control.services.cost_ledger import ledger_summary


ZERO = Decimal('0')


def _money(value):
    return str((value or ZERO).quantize(Decimal('0.01')))


def project_commercial_dashboard(project):
    ledger = ledger_summary(project)
    project_currency = (project.currency or 'AED').upper()
    entries = CostLedgerEntry.objects.filter(project=project, status='posted', is_deleted=False)
    po_ids = set(entries.filter(entry_type='commitment', source_type='purchase_order').values_list('source_id', flat=True))
    po_ids.update(PurchaseOrder.objects.filter(enterprise_project=project).values_list('id', flat=True))

    pos = PurchaseOrder.objects.filter(pk__in=po_ids)
    receipts = Receipt.objects.filter(purchase_order_id__in=po_ids)
    actual_entries = list(entries.filter(entry_type='actual'))
    allocation_ids = [row.source_id for row in actual_entries if row.source_type == 'invoice_allocation']
    invoice_allocations = {
        str(row.pk): row for row in InvoicePurchaseOrderAllocation.objects.filter(pk__in=allocation_ids).select_related('invoice')
    }
    paid = ZERO
    scheduled = ZERO
    invoice_ids = set()
    for entry in actual_entries:
        allocation = invoice_allocations.get(str(entry.source_id))
        if not allocation or not allocation.invoice.total_amount:
            continue
        invoice_ids.add(allocation.invoice_id)
        ratio = min(Decimal('1'), (allocation.invoice.paid_amount or ZERO) / allocation.invoice.total_amount)
        paid += entry.amount * ratio
    for operation in PayablePayment.objects.filter(invoice_id__in=invoice_ids, operation='schedule'):
        scheduled += operation.amount or ZERO

    by_wbs = defaultdict(lambda: {'budget': ZERO, 'committed': ZERO, 'actual': ZERO})
    wbs_names = {}
    for entry in entries.select_related('wbs_node'):
        key = entry.wbs_node.code if entry.wbs_node else 'UNALLOCATED'
        wbs_names[key] = entry.wbs_node.name if entry.wbs_node else 'Unallocated'
        field = {'budget': 'budget', 'commitment': 'committed', 'actual': 'actual'}.get(entry.entry_type)
        if field:
            by_wbs[key][field] += entry.amount

    events = CommercialEvent.objects.filter(project=project).select_related('actor')[:30]
    return {
        'project': {'id': str(project.pk), 'code': project.code, 'name': project.name},
        'currency': project_currency,
        'contract_value': _money(project.contract_value),
        'budget': _money(ledger['budget']),
        'committed': _money(ledger['committed']),
        'actual': _money(ledger['spent']),
        'remaining_budget': _money(ledger['remaining']),
        'outstanding_commitment': _money(max(ZERO, ledger['committed'] - ledger['spent'])),
        'paid': _money(paid),
        'unpaid_actual': _money(max(ZERO, ledger['spent'] - paid)),
        'scheduled_payments': _money(scheduled),
        'current_margin': _money((project.contract_value or ZERO) - ledger['spent']),
        'counts': {
            'purchase_orders': pos.count(),
            'approved_purchase_orders': pos.filter(approved_at__isnull=False).count(),
            'receipts': receipts.count(),
            'accepted_receipts': receipts.filter(status__in=['accepted', 'partial']).count(),
            'verified_invoices': InvoicePurchaseOrderAllocation.objects.filter(
                pk__in=allocation_ids, match_status=InvoiceMatchStatus.VERIFIED,
            ).values('invoice_id').distinct().count(),
            'payments': PayablePayment.objects.filter(invoice_id__in=invoice_ids, operation='payment').count(),
        },
        'wbs': [
            {'code': code, 'name': wbs_names[code], **{key: _money(value) for key, value in totals.items()}}
            for code, totals in sorted(by_wbs.items())
        ],
        'recent_events': [
            {
                'id': str(event.pk), 'event_type': event.event_type,
                'event_type_display': event.get_event_type_display(),
                'source_type': event.source_type, 'source_reference': event.source_reference,
                'amount': _money(event.amount) if event.amount is not None else None,
                'currency': event.currency, 'event_at': event.event_at,
                'actor': event.actor.get_full_name() or event.actor.email if event.actor else 'System',
                'ledger_rebuilt': event.ledger_rebuilt, 'processing_error': event.processing_error,
            } for event in events
        ],
        'controls': {
            'currency_exceptions': [],
            'calculation_source': 'posted_cost_ledger',
            'event_delivery': 'idempotent',
        },
    }
