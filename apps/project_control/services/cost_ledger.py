"""Canonical project cost ledger built from approved structured sources."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from ..models import BudgetAllocation, CostAllocation, CostLedgerEntry


def _decimal(value):
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))


def source_record(source_type, source_id):
    if source_type == 'purchase_requisition':
        from apps.procurement.models import PurchaseRequisition
        return PurchaseRequisition.objects.filter(pk=source_id).first()
    if source_type == 'purchase_order':
        from apps.procurement.models import PurchaseOrder
        return PurchaseOrder.objects.filter(pk=source_id).first()
    if source_type == 'invoice_allocation':
        from apps.finance.models import InvoicePurchaseOrderAllocation
        return InvoicePurchaseOrderAllocation.objects.select_related('invoice', 'purchase_order').filter(pk=source_id).first()
    return None


def source_value(source_type, row):
    if row is None:
        return Decimal('0'), '', ''
    if source_type == 'purchase_requisition':
        return (
            _decimal(row.total_price or row.estimated_budget),
            row.currency,
            row.pr_number,
        )
    if source_type == 'purchase_order':
        return _decimal(row.total_amount), row.currency, row.po_number
    if source_type == 'invoice_allocation':
        return _decimal(row.allocated_amount), row.currency, row.invoice.invoice_number
    return Decimal('0'), '', ''


def allocation_totals(source_type, source_id, *, exclude_id=None):
    rows = CostAllocation.objects.filter(
        source_type=source_type, source_id=str(source_id),
        is_deleted=False,
    ).exclude(status='rejected')
    if exclude_id:
        rows = rows.exclude(pk=exclude_id)
    return _decimal(rows.aggregate(total=Sum('amount'))['total'])


def _post_entry(project, active_keys, *, entry_key, defaults):
    active_keys.add(entry_key)
    CostLedgerEntry.objects.update_or_create(
        entry_key=entry_key,
        defaults={**defaults, 'project': project, 'status': 'posted', 'is_deleted': False},
    )


@transaction.atomic
def rebuild_project_ledger(project, *, user=None):
    """Idempotently rebuild posted budget, commitment, and actual entries."""
    from apps.finance.models import InvoiceMatchStatus, InvoicePurchaseOrderAllocation
    from apps.procurement.models import PurchaseOrder

    today = timezone.localdate()
    active_keys = set()
    project_currency = project.currency or 'AED'
    currency_exceptions = []

    for budget in BudgetAllocation.objects.filter(
        project=project, status='approved', is_deleted=False,
    ).select_related('wbs_node'):
        if budget.currency != project_currency:
            currency_exceptions.append({'source': budget.code, 'currency': budget.currency})
            continue
        _post_entry(project, active_keys, entry_key=f'budget:{budget.pk}', defaults={
            'wbs_node': budget.wbs_node,
            'budget_allocation': budget,
            'cost_allocation': None,
            'entry_type': 'budget',
            'amount': budget.amount,
            'currency': budget.currency,
            'source_type': 'budget_allocation',
            'source_id': str(budget.pk),
            'source_reference': budget.code,
            'entry_date': budget.approved_at.date() if budget.approved_at else today,
            'metadata': {'name': budget.name, 'category': budget.category},
            'created_by': user,
        })

    approved_allocations = CostAllocation.objects.filter(
        project=project, status='approved', is_deleted=False,
    ).select_related('wbs_node', 'budget_allocation')
    allocated_source_keys = set()
    for allocation in approved_allocations:
        allocated_source_keys.add((allocation.source_type, allocation.source_id))
        entry_type = {
            'purchase_order': 'commitment',
            'invoice_allocation': 'actual',
        }.get(allocation.source_type)
        if not entry_type:
            continue
        row = source_record(allocation.source_type, allocation.source_id)
        if row is None:
            continue
        if allocation.source_type == 'invoice_allocation' and row.match_status != InvoiceMatchStatus.VERIFIED:
            continue
        if allocation.currency != project_currency:
            currency_exceptions.append({'source': allocation.source_reference, 'currency': allocation.currency})
            continue
        _post_entry(project, active_keys, entry_key=f'allocation:{allocation.pk}:{entry_type}', defaults={
            'wbs_node': allocation.wbs_node,
            'budget_allocation': allocation.budget_allocation,
            'cost_allocation': allocation,
            'entry_type': entry_type,
            'amount': allocation.amount,
            'currency': allocation.currency,
            'source_type': allocation.source_type,
            'source_id': allocation.source_id,
            'source_reference': allocation.source_reference,
            'entry_date': allocation.approved_at.date() if allocation.approved_at else today,
            'metadata': {'allocation_status': allocation.status},
            'created_by': user,
        })

    po_statuses = ['sent', 'acknowledged', 'in_progress', 'partially_received', 'completed']
    for order in PurchaseOrder.objects.filter(
        enterprise_project=project, status__in=po_statuses,
    ):
        if ('purchase_order', str(order.pk)) in allocated_source_keys or CostAllocation.objects.filter(
            source_type='purchase_order', source_id=str(order.pk),
            status='approved', is_deleted=False,
        ).exists():
            continue
        if order.currency != project_currency:
            currency_exceptions.append({'source': order.po_number, 'currency': order.currency})
            continue
        _post_entry(project, active_keys, entry_key=f'po:{order.pk}:{project.pk}', defaults={
            'wbs_node': None, 'budget_allocation': None, 'cost_allocation': None,
            'entry_type': 'commitment', 'amount': order.total_amount,
            'currency': order.currency, 'source_type': 'purchase_order',
            'source_id': str(order.pk), 'source_reference': order.po_number,
            'entry_date': order.po_date or order.created_at.date(),
            'metadata': {'allocation': 'project_unallocated'}, 'created_by': user,
        })

    verified = InvoicePurchaseOrderAllocation.objects.filter(
        match_status=InvoiceMatchStatus.VERIFIED,
    ).select_related('invoice', 'purchase_order')
    for invoice_allocation in verified:
        if invoice_allocation.currency != project_currency:
            currency_exceptions.append({
                'source': invoice_allocation.invoice.invoice_number,
                'currency': invoice_allocation.currency,
            })
            continue
        explicit = CostAllocation.objects.filter(
            source_type='invoice_allocation', source_id=str(invoice_allocation.pk),
            status='approved', is_deleted=False,
        )
        if explicit.exists():
            continue
        order = invoice_allocation.purchase_order
        po_splits = list(CostAllocation.objects.filter(
            source_type='purchase_order', source_id=str(order.pk),
            status='approved', is_deleted=False,
        ).select_related('wbs_node', 'budget_allocation'))
        if po_splits:
            total_split = sum((_decimal(item.amount) for item in po_splits), Decimal('0'))
            for split in po_splits:
                if split.project_id != project.pk or total_split <= 0:
                    continue
                amount = (_decimal(invoice_allocation.allocated_amount) * split.amount / total_split).quantize(Decimal('0.01'))
                _post_entry(project, active_keys, entry_key=f'invoice:{invoice_allocation.pk}:{split.pk}', defaults={
                    'wbs_node': split.wbs_node, 'budget_allocation': split.budget_allocation,
                    'cost_allocation': split, 'entry_type': 'actual', 'amount': amount,
                    'currency': invoice_allocation.currency, 'source_type': 'invoice_allocation',
                    'source_id': str(invoice_allocation.pk),
                    'source_reference': invoice_allocation.invoice.invoice_number,
                    'entry_date': invoice_allocation.invoice.invoice_date or invoice_allocation.created_at.date(),
                    'metadata': {'distributed_from_po': order.po_number}, 'created_by': user,
                })
        elif order.enterprise_project_id == project.pk:
            _post_entry(project, active_keys, entry_key=f'invoice:{invoice_allocation.pk}:{project.pk}', defaults={
                'wbs_node': None, 'budget_allocation': None, 'cost_allocation': None,
                'entry_type': 'actual', 'amount': invoice_allocation.allocated_amount,
                'currency': invoice_allocation.currency, 'source_type': 'invoice_allocation',
                'source_id': str(invoice_allocation.pk),
                'source_reference': invoice_allocation.invoice.invoice_number,
                'entry_date': invoice_allocation.invoice.invoice_date or invoice_allocation.created_at.date(),
                'metadata': {'purchase_order': order.po_number}, 'created_by': user,
            })

    CostLedgerEntry.objects.filter(
        project=project, status='posted', is_deleted=False,
    ).exclude(entry_key__in=active_keys).exclude(entry_type='adjustment').update(status='reversed')

    totals = defaultdict(lambda: Decimal('0'))
    for row in CostLedgerEntry.objects.filter(
        project=project, status='posted', is_deleted=False,
    ).values('entry_type').annotate(total=Sum('amount')):
        totals[row['entry_type']] = _decimal(row['total'])
    result = {key: str(totals[key]) for key in ('budget', 'commitment', 'actual', 'adjustment')}
    result['currency'] = project_currency
    result['currency_exceptions'] = currency_exceptions
    return result


def ledger_summary(project):
    rows = CostLedgerEntry.objects.filter(project=project, status='posted', is_deleted=False)
    totals = {
        row['entry_type']: _decimal(row['total'])
        for row in rows.values('entry_type').annotate(total=Sum('amount'))
    }
    budget = totals.get('budget', Decimal('0'))
    committed = totals.get('commitment', Decimal('0'))
    actual = totals.get('actual', Decimal('0')) + totals.get('adjustment', Decimal('0'))
    remaining = budget - actual
    available = budget - committed
    return {
        'budget': budget,
        'committed': committed,
        'spent': actual,
        'remaining': remaining,
        'available_to_commit': available,
        'commitment_remaining': max(committed - actual, Decimal('0')),
        'utilisation_pct': float((actual / budget) * 100) if budget > 0 else 0.0,
        'entry_count': rows.count(),
    }
