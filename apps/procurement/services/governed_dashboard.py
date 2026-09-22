"""Governed procurement command-centre calculations.

All dashboard values are produced here so the UI never invents financial,
workflow, risk, or performance measures.
"""

from collections import defaultdict
from datetime import date, datetime, time, timezone as dt_timezone
from decimal import Decimal
from statistics import median

from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from apps.core.project_models import Project as EnterpriseProject
from apps.procurement.models import (
    ProcurementCalculationAudit,
    ProcurementReportingSnapshot,
    PurchaseOrder,
    PurchaseRequisition,
    Receipt,
    Vendor,
)
from apps.procurement.services.purchase_order_approvals import _entry_matches_user
from apps.procurement.services.requisition_workflow import RequisitionWorkflowService
from apps.invoice_tracker.services.finance_engine import FINANCE_RULES


DEFINITION_VERSION = '2026.2'
REPORTING_CURRENCY = 'AED'
VENDOR_REQUIRED_FIELDS = (
    ('tax_id', 'Tax information'),
    ('trade_license_number', 'Trade licence'),
    ('contact_person', 'Contact person'),
    ('email', 'Email'),
    ('phone', 'Phone'),
    ('address', 'Address'),
    ('country', 'Country'),
)
TERMINOLOGY = {
    'approved_requisition_value': 'Value of approved or PO-converted purchase requisitions in their transaction currency.',
    'po_commitment': 'Gross value of issued, acknowledged, in-progress, partially received or completed purchase orders; drafts are excluded.',
    'po_commitment_aed': 'PO commitments converted to AED using the controlled Finance FX-to-AED rate table; the total is unavailable if any source currency has no configured rate.',
    'invoiced_value': 'Value of PO-linked invoices with a Verified match status, grouped by invoice currency.',
    'received_value': 'Unavailable until receipt lines carry governed accepted quantity and unit-value data.',
    'paid_value': 'Paid amount on PO-linked Finance invoices recorded as Partially Paid or Paid, grouped by currency and payment date.',
    'realized_savings': 'Unavailable until Procurement and Finance approve a baseline-based savings register.',
    'overdue_requisition': 'Submitted or in-review requisition whose review deadline or required date is before today.',
    'overdue_delivery': 'Non-completed, non-cancelled purchase order whose expected delivery date is before today.',
    'pending_inspection': 'Goods receipt with Pending Inspection status.',
    'on_time_delivery': 'Completed orders with actual delivery on or before expected delivery, divided by completed orders having both dates.',
    'supplier_watch': 'Factual supplier master-data and compliance exceptions; this is not a predictive risk score.',
}


def _decimal(value):
    return Decimal(str(value or 0))


def _money_rows(queryset, amount_field, currency_field='currency'):
    rows = queryset.values(currency_field).annotate(amount=Sum(amount_field)).order_by(currency_field)
    return [
        {'currency': (row[currency_field] or 'UNSPECIFIED').upper(), 'amount': str(_decimal(row['amount']).quantize(Decimal('0.01')))}
        for row in rows
    ]


def _to_aed(rows):
    """Convert grouped transaction-currency rows with the Finance rule book."""
    rates = FINANCE_RULES['fx_to_aed']
    total = Decimal('0')
    conversions = []
    missing = []
    for row in rows:
        currency = (row.get('currency') or 'UNSPECIFIED').upper()
        amount = _decimal(row.get('amount'))
        rate = rates.get(currency)
        if rate is None:
            missing.append(currency)
            conversions.append({'currency': currency, 'amount': str(amount), 'rate': None, 'amount_aed': None})
            continue
        amount_aed = amount * rate
        total += amount_aed
        conversions.append({
            'currency': currency,
            'amount': str(amount),
            'rate': str(rate),
            'amount_aed': str(amount_aed.quantize(Decimal('0.01'))),
        })
    complete = not missing
    return {
        'currency': REPORTING_CURRENCY,
        'amount': str(total.quantize(Decimal('0.01'))) if complete else None,
        'conversion_complete': complete,
        'missing_currencies': sorted(set(missing)),
        'conversions': conversions,
        'rate_source': 'Finance controlled configuration (FINANCE_RULES.fx_to_aed)',
        'definition_version': DEFINITION_VERSION,
    }


def _requisition_value_rows(queryset):
    grouped = defaultdict(Decimal)
    for row in queryset.values('currency', 'net_total_excl_vat', 'total_price', 'estimated_budget'):
        grouped[(row['currency'] or 'UNSPECIFIED').upper()] += _decimal(
            row['net_total_excl_vat'] or row['total_price'] or row['estimated_budget']
        )
    return [{'currency': currency, 'amount': str(grouped[currency])} for currency in sorted(grouped)]


def _iso(value):
    return value.isoformat() if value else None


def _day_start(value):
    return timezone.make_aware(datetime.combine(value, time.min), dt_timezone.utc)


def _day_end(value):
    return timezone.make_aware(datetime.combine(value, time.max), dt_timezone.utc)


def _user_department(user):
    employee = getattr(user, 'employee_master', None)
    return (getattr(employee, 'department', '') or '').strip()


def _parse_date(value, field):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{field} must use YYYY-MM-DD format.') from exc


def _scope_querysets(user, params):
    scope_type = (params.get('scope') or 'portfolio').strip().lower()
    if scope_type not in {'portfolio', 'my_projects', 'department', 'project'}:
        raise ValueError('scope must be portfolio, my_projects, department, or project.')

    project_id = (params.get('project') or '').strip()
    department = (params.get('department') or '').strip()
    currency = (params.get('currency') or '').strip().upper()
    period_start = _parse_date(params.get('period_start'), 'period_start')
    period_end = _parse_date(params.get('period_end'), 'period_end')
    if period_start and period_end and period_start > period_end:
        raise ValueError('period_start cannot be later than period_end.')

    projects = EnterpriseProject.objects.all()
    prs = PurchaseRequisition.objects.select_related('enterprise_project', 'vendor')
    pos = PurchaseOrder.objects.select_related('enterprise_project', 'vendor', 'created_by')

    if scope_type == 'my_projects':
        projects = projects.filter(Q(owner=user) | Q(team_members=user)).distinct()
        prs = prs.filter(enterprise_project__in=projects)
        pos = pos.filter(enterprise_project__in=projects)
    elif scope_type == 'project':
        if not project_id:
            raise ValueError('project is required when scope=project.')
        projects = projects.filter(pk=project_id)
        if not projects.exists():
            raise ValueError('The selected enterprise project does not exist.')
        prs = prs.filter(enterprise_project_id=project_id)
        pos = pos.filter(enterprise_project_id=project_id)
    elif scope_type == 'department':
        department = department or _user_department(user)
        if not department:
            raise ValueError('department is required because the user has no assigned department.')
        prs = prs.filter(department__iexact=department)
        pos = pos.filter(
            Q(pr_reference__department__iexact=department)
            | Q(enterprise_project__in=projects.filter(custom_fields__department__iexact=department))
        ).distinct()

    if currency:
        prs = prs.filter(currency__iexact=currency)
        pos = pos.filter(currency__iexact=currency)

    orders_in_scope = pos
    receipts = Receipt.objects.select_related(
        'purchase_order', 'purchase_order__enterprise_project', 'purchase_order__vendor'
    ).filter(purchase_order__in=orders_in_scope)
    from apps.finance.models import Invoice
    invoices = Invoice.objects.select_related('vendor').filter(purchase_orders__in=orders_in_scope).distinct()
    if currency:
        invoices = invoices.filter(currency__iexact=currency)
    paid_invoices = invoices.filter(payment_status__in=['partial', 'paid'], paid_amount__gt=0)
    # Apply the period to each document's own accounting/operational date.
    # A receipt in the period must not disappear merely because its PO was
    # raised in an earlier period.
    if period_start:
        prs = prs.filter(created_at__gte=_day_start(period_start))
        pos = pos.filter(po_date__gte=period_start)
        receipts = receipts.filter(receipt_date__gte=period_start)
        invoices = invoices.filter(invoice_date__gte=period_start)
        paid_invoices = paid_invoices.filter(payment_date__gte=period_start)
    if period_end:
        prs = prs.filter(created_at__lte=_day_end(period_end))
        pos = pos.filter(po_date__lte=period_end)
        receipts = receipts.filter(receipt_date__lte=period_end)
        invoices = invoices.filter(invoice_date__lte=period_end)
        paid_invoices = paid_invoices.filter(payment_date__lte=period_end)
    return {
        'scope': {
            'type': scope_type,
            'project_id': project_id or None,
            'department': department or None,
            'period_start': _iso(period_start),
            'period_end': _iso(period_end),
            'currency': currency or None,
        },
        'projects': projects,
        'requisitions': prs,
        'orders': pos,
        'receipts': receipts,
        'invoices': invoices,
        'paid_invoices': paid_invoices,
        'period_start': period_start,
        'period_end': period_end,
    }


def _approval_owned_by(workflow, user, matcher):
    return any(
        str(entry.get('status', '')).lower() == 'pending' and matcher(entry, user)
        for entry in (workflow or [])
    )


def _actions(user, prs, pos, receipts, invoices, today):
    actions = []

    for pr in prs.filter(status__in=['submitted', 'in_review']).order_by('review_due_at', 'required_date'):
        due = pr.review_due_at.date() if pr.review_due_at else pr.required_date
        mine = _approval_owned_by(
            pr.approval_workflow_config,
            user,
            RequisitionWorkflowService._stage_matches_user,
        )
        overdue = bool(due and due < today)
        if mine or overdue:
            actions.append({
                'type': 'pr_approval' if mine else 'overdue_requisition',
                'severity': 'critical' if overdue else 'warning',
                'record': pr.pr_number,
                'title': pr.title or pr.product_service or 'Purchase requisition',
                'project': pr.enterprise_project.code if pr.enterprise_project else None,
                'owner': 'You' if mine else None,
                'due_date': _iso(due),
                'age_days': max((today - due).days, 0) if due else None,
                'currency': (pr.currency or '').upper(),
                'value': str(_decimal(pr.net_total_excl_vat or pr.total_price or pr.estimated_budget)),
                'href': f'/procurement/requisitions/{pr.id}',
            })

    incomplete = pos.exclude(status__in=['draft', 'completed', 'cancelled'])
    for po in incomplete.order_by('expected_delivery', 'po_date'):
        mine = _approval_owned_by(po.approval_log, user, _entry_matches_user)
        overdue = bool(po.expected_delivery and po.expected_delivery < today)
        awaiting_ack = po.status == 'sent' and not po.confirmation_date
        if mine or overdue or awaiting_ack:
            kind = 'po_approval' if mine else ('overdue_delivery' if overdue else 'supplier_acknowledgement')
            due = po.expected_delivery
            actions.append({
                'type': kind,
                'severity': 'critical' if overdue else 'warning',
                'record': po.po_number,
                'title': po.title,
                'project': po.enterprise_project.code if po.enterprise_project else None,
                'supplier': po.vendor.name,
                'owner': 'You' if mine else po.buyer_reference_pe or po.buyer_reference_pm or None,
                'due_date': _iso(due),
                'age_days': max((today - due).days, 0) if due and due < today else (today - po.po_date).days,
                'currency': (po.currency or '').upper(),
                'value': str(_decimal(po.total_amount)),
                'href': f'/procurement/orders/{po.id}',
            })

    for po in pos.filter(status='draft').order_by('po_date'):
        age = max((today - po.po_date).days, 0)
        actions.append({
            'type': 'draft_purchase_order',
            'severity': 'critical' if age > 7 else 'warning',
            'record': po.po_number,
            'title': po.title,
            'project': po.enterprise_project.code if po.enterprise_project else None,
            'supplier': po.vendor.name,
            'owner': 'You' if po.created_by_id == user.id else po.buyer_reference_pe or po.buyer_reference_pm or None,
            'due_date': None,
            'age_days': age,
            'currency': (po.currency or '').upper(),
            'value': str(_decimal(po.total_amount)),
            'href': f'/procurement/orders/{po.id}',
        })

    for receipt in receipts.filter(status='pending').order_by('receipt_date'):
        actions.append({
            'type': 'pending_inspection',
            'severity': 'critical' if (today - receipt.receipt_date).days > 2 else 'warning',
            'record': receipt.receipt_number,
            'title': f'Inspect delivery for {receipt.purchase_order.po_number}',
            'project': receipt.purchase_order.enterprise_project.code if receipt.purchase_order.enterprise_project else None,
            'supplier': receipt.purchase_order.vendor.name,
            'owner': None,
            'due_date': None,
            'age_days': (today - receipt.receipt_date).days,
            'currency': (receipt.purchase_order.currency or '').upper(),
            'value': None,
                'href': f'/procurement/receipts?receipt={receipt.id}',
        })

    for invoice in invoices.filter(match_status='exception').order_by('due_date', 'invoice_date'):
        due = invoice.due_date
        actions.append({
            'type': 'invoice_match_exception',
            'severity': 'critical',
            'record': invoice.invoice_number,
            'title': 'Resolve invoice matching exception',
            'project': None,
            'supplier': invoice.vendor_name or (invoice.vendor.name if invoice.vendor else None),
            'owner': 'Procurement',
            'due_date': _iso(due),
            'age_days': max((today - due).days, 0) if due and due < today else 0,
            'currency': (invoice.currency or '').upper(),
            'value': str(_decimal(invoice.total_amount or invoice.amount)),
            'href': f'/finance/incoming-invoices/{invoice.id}',
        })

    severity_order = {'critical': 0, 'warning': 1, 'info': 2}
    return sorted(actions, key=lambda row: (severity_order[row['severity']], -(row['age_days'] or 0)))[:100]


def _supplier_watch(vendors, today):
    watch = []
    for vendor in vendors:
        issues = []
        if vendor.status != 'active':
            issues.append(('Supplier status is not active', 'high'))
        if str(vendor.audit_status or '').lower() == 'failed':
            issues.append(('Supplier audit failed', 'high'))
        if vendor.icv_expiry_date and vendor.icv_expiry_date < today:
            issues.append(('ICV certificate expired', 'high'))
        missing = [label for field, label in VENDOR_REQUIRED_FIELDS if not getattr(vendor, field, None)]
        if missing:
            issues.append((f'Missing {", ".join(missing[:2])}' + (' and more' if len(missing) > 2 else ''), 'medium'))
        if issues:
            severity = 'high' if any(level == 'high' for _, level in issues) else 'medium'
            watch.append({
                'id': str(vendor.id),
                'supplier': vendor.name,
                'issue': issues[0][0],
                'issue_count': len(issues),
                'severity': severity,
                'href': f'/procurement/vendors?vendor={vendor.id}',
            })
    return sorted(watch, key=lambda row: (row['severity'] != 'high', row['supplier'].lower()))


def _supplier_spend(commitments):
    rows = commitments.values('currency', 'vendor__id', 'vendor__name').annotate(
        amount=Sum('total_amount')
    ).order_by('currency', '-amount')
    rates = FINANCE_RULES['fx_to_aed']
    grouped = defaultdict(Decimal)
    missing = set()
    for row in rows:
        currency = (row['currency'] or 'UNSPECIFIED').upper()
        rate = rates.get(currency)
        if rate is None:
            missing.add(currency)
            continue
        key = (str(row['vendor__id']) if row['vendor__id'] else None, row['vendor__name'] or 'Unassigned supplier')
        grouped[key] += _decimal(row['amount']) * rate
    suppliers = [
        {'supplier_id': supplier_id, 'supplier': supplier, 'amount': str(amount.quantize(Decimal('0.01')))}
        for (supplier_id, supplier), amount in sorted(grouped.items(), key=lambda item: item[1], reverse=True)[:5]
    ]
    return [{'currency': REPORTING_CURRENCY, 'suppliers': suppliers, 'missing_currencies': sorted(missing)}]


def _spend_trend(commitments, period_start, period_end, today):
    """Distribute governed commitments by PO month, without inventing forecasts."""
    grouped = defaultdict(list)
    order_counts = defaultdict(int)
    for row in commitments.annotate(month=TruncMonth('po_date')).values('month', 'currency').annotate(
        amount=Sum('total_amount'), order_count=Count('id')
    ).order_by('month', 'currency'):
        month = row['month'].strftime('%Y-%m')
        grouped[month].append({'currency': row['currency'], 'amount': row['amount']})
        order_counts[month] += row['order_count']

    # Unfiltered reports include historical commitments so their trend reconciles
    # to the headline total. The current year still starts with January zeroes.
    first = (period_start or today.replace(month=1, day=1)).replace(day=1)
    last = (period_end or today).replace(day=1)
    if not period_start and grouped:
        first = min(first, date.fromisoformat(f'{min(grouped)}-01'))
    if not period_end and grouped:
        last = max(last, date.fromisoformat(f'{max(grouped)}-01'))
    months = []
    missing = set()
    cumulative = Decimal('0')
    current = first
    while current <= last:
        month = current.strftime('%Y-%m')
        converted = _to_aed(grouped[month])
        missing.update(converted['missing_currencies'])
        if converted['amount'] is not None:
            cumulative += sum(
                (_decimal(row['amount']) * FINANCE_RULES['fx_to_aed'][(row['currency'] or 'UNSPECIFIED').upper()]
                 for row in grouped[month]),
                Decimal('0'),
            )
        months.append({
            'month': month,
            'amount': converted['amount'],
            'cumulative_amount': str(cumulative.quantize(Decimal('0.01'))) if not missing else None,
            'order_count': order_counts[month],
            'missing_currencies': converted['missing_currencies'],
        })
        if current.year == 9999 and current.month == 12:
            break
        current = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
    return {
        'currency': REPORTING_CURRENCY,
        'months': months,
        'conversion_complete': not missing,
        'missing_currencies': sorted(missing),
        'date_basis': 'Purchase order date',
    }


def _order_status(pos):
    counts = dict(pos.values('status').annotate(count=Count('id')).values_list('status', 'count'))
    total = sum(counts.values())
    return {
        'total': total,
        'statuses': [
            {
                'status': status,
                'label': label,
                'count': counts.get(status, 0),
                'percent': round(counts.get(status, 0) / total * 100, 1) if total else None,
            }
            for status, label in PurchaseOrder.STATUS_CHOICES
        ],
    }


def _supplier_readiness(vendors, watch):
    total = len(vendors)
    incomplete_master_data = sum(
        any(not getattr(vendor, field, None) for field, _ in VENDOR_REQUIRED_FIELDS)
        for vendor in vendors
    )
    complete = total - len(watch)
    return {
        'total': total,
        'complete': complete,
        'incomplete': len(watch),
        'complete_percent': round(complete / total * 100, 1) if total else None,
        'master_data_complete': total - incomplete_master_data,
        'master_data_incomplete': incomplete_master_data,
        'master_data_complete_percent': round((total - incomplete_master_data) / total * 100, 1) if total else None,
    }


def _purchasing_flow(prs, approved_prs, pos, commitments, receipts):
    requisitions = prs.count()
    approved = approved_prs.count()
    issued = commitments.count()
    acknowledged = commitments.filter(Q(confirmation_date__isnull=False) | Q(status='acknowledged')).count()
    # Coverage uses the same issued-order cohort for numerator and denominator;
    # several accepted receipts for one order still represent one covered PO.
    received_orders = commitments.filter(receipts__status='accepted').distinct().count()
    return {
        'requisitions': requisitions,
        'approved_requisitions': approved,
        'purchase_orders': pos.count(),
        'accepted_receipts': receipts.filter(status='accepted').count(),
        'issued_purchase_orders': issued,
        'supplier_acknowledged_orders': acknowledged,
        'orders_with_accepted_receipts': received_orders,
        'approval_percent': round(approved / requisitions * 100, 1) if requisitions else None,
        'acknowledgement_percent': round(acknowledged / issued * 100, 1) if issued else None,
        'receipt_coverage_percent': round(received_orders / issued * 100, 1) if issued else None,
        'cohort_description': 'Requisitions by creation date; purchase orders by PO date within the selected period.',
        'receipt_cohort_description': 'Acknowledgement and accepted-receipt coverage use issued POs in the selected period, including their subsequent receipts.',
    }


def _recent_decisions(prs, pos):
    rows = []
    for pr in prs.filter(approved_at__isnull=False).select_related('approved_by').order_by('-approved_at')[:10]:
        rows.append({
            'record': pr.pr_number,
            'title': pr.title or pr.product_service or 'Purchase requisition',
            'type': 'PR approved',
            'supplier': pr.vendor.name if pr.vendor else None,
            'status': 'approved',
            'owner': pr.approved_by.get_full_name() if pr.approved_by else None,
            'decided_at': pr.approved_at.isoformat(),
            'href': f'/procurement/requisitions/{pr.id}',
        })
    for po in pos.filter(approved_at__isnull=False).select_related('approved_by').order_by('-approved_at')[:10]:
        rows.append({
            'record': po.po_number,
            'title': po.title,
            'type': 'PO approved',
            'supplier': po.vendor.name,
            'status': 'approved',
            'owner': po.approved_by.get_full_name() if po.approved_by else po.approved_by_name or None,
            'decided_at': po.approved_at.isoformat(),
            'href': f'/procurement/orders/{po.id}',
        })
    return sorted(rows, key=lambda row: row['decided_at'], reverse=True)[:5]


def _cycle_metrics(prs, pos, receipts):
    approval_days = [
        (pr.approved_at - pr.created_at).total_seconds() / 86400
        for pr in prs.filter(approved_at__isnull=False)
        if pr.created_at and pr.approved_at and pr.approved_at >= pr.created_at
    ]
    acknowledgement_days = [
        (po.confirmation_date - po.po_date).days
        for po in pos.filter(confirmation_date__isnull=False)
        if po.confirmation_date >= po.po_date
    ]
    inspected = receipts.exclude(status='pending')
    inspection_days = [
        (receipt.updated_at.date() - receipt.receipt_date).days
        for receipt in inspected
        if receipt.updated_at
    ]
    delivery_population = pos.filter(
        status='completed', expected_delivery__isnull=False, actual_delivery__isnull=False
    )
    completed_orders = pos.filter(status='completed')
    on_time = delivery_population.filter(actual_delivery__lte=F('expected_delivery')).count()
    delivery_count = delivery_population.count()
    return {
        'pr_approval_median_days': round(median(approval_days), 1) if approval_days else None,
        'supplier_ack_median_days': round(median(acknowledgement_days), 1) if acknowledgement_days else None,
        'inspection_median_days': round(median(inspection_days), 1) if inspection_days else None,
        'on_time_delivery_percent': round(on_time / delivery_count * 100, 1) if delivery_count else None,
        'on_time_delivery_numerator': on_time,
        'on_time_delivery_population': delivery_count,
        'completed_order_population': completed_orders.count(),
        'completed_with_expected_delivery': completed_orders.filter(expected_delivery__isnull=False).count(),
        'completed_with_actual_delivery': completed_orders.filter(actual_delivery__isnull=False).count(),
        'delivery_reliability_formula': 'On-time completed POs / completed POs with expected and actual delivery dates',
    }


def build_dashboard(user, params):
    scoped = _scope_querysets(user, params)
    prs, pos, receipts = scoped['requisitions'], scoped['orders'], scoped['receipts']
    invoices, paid_invoices = scoped['invoices'], scoped['paid_invoices']
    today = timezone.localdate()
    approved_prs = prs.filter(status__in=['approved', 'converted'])
    commitments = pos.exclude(status__in=['draft', 'cancelled'])
    actions = _actions(user, prs, pos, receipts, invoices, today)
    verified_invoices = invoices.filter(match_status='verified').exclude(procurement_status='rejected')
    invoice_values = _money_rows(verified_invoices, 'total_amount')
    paid_values = _money_rows(paid_invoices, 'paid_amount')
    commitment_values = _money_rows(commitments, 'total_amount')
    commitment_aed = _to_aed(commitment_values)
    approved_values = _requisition_value_rows(approved_prs)
    related_vendor_ids = set(pos.values_list('vendor_id', flat=True)) | set(prs.values_list('vendor_id', flat=True))
    supplier_queryset = (
        Vendor.objects.all()
        if not related_vendor_ids and scoped['scope']['type'] == 'portfolio'
        else Vendor.objects.filter(id__in=related_vendor_ids)
    )
    suppliers = list(supplier_queryset)
    supplier_watch = _supplier_watch(suppliers, today)
    supplier_spend = _supplier_spend(commitments)
    total_aed = _decimal(commitment_aed['amount'])
    supplier_spend_aed = {
        'currency': REPORTING_CURRENCY,
        'conversion_complete': commitment_aed['conversion_complete'],
        'missing_currencies': commitment_aed['missing_currencies'],
        'suppliers': [
            {**supplier, 'percent': round(float(_decimal(supplier['amount']) / total_aed * 100), 1) if total_aed else None}
            for supplier in supplier_spend[0]['suppliers']
        ] if commitment_aed['conversion_complete'] else [],
    }

    project_links = {
        'requisitions_unlinked': prs.filter(enterprise_project__isnull=True).count(),
        'orders_unlinked': pos.filter(enterprise_project__isnull=True).count(),
    }
    metrics = {
        'approved_requisition_value': approved_values,
        'po_commitment': commitment_values,
        'po_commitment_aed': commitment_aed,
        'invoiced_value': invoice_values,
        'received_value': None,
        'paid_value': paid_values,
        'realized_savings': None,
        'counts': {
            'requisitions': prs.count(),
            'pending_approvals': prs.filter(status__in=['submitted', 'in_review']).count(),
            'purchase_orders': pos.count(),
            'open_purchase_orders': pos.exclude(status__in=['completed', 'cancelled']).count(),
            'pending_inspections': receipts.filter(status='pending').count(),
            'invoice_match_exceptions': invoices.filter(match_status='exception').count(),
            'overdue_deliveries': commitments.filter(expected_delivery__lt=today).exclude(status='completed').count(),
            'my_actions': sum(1 for row in actions if row.get('owner') == 'You'),
            'all_exceptions': len(actions),
            'active_suppliers': Vendor.objects.filter(status='active').count(),
            'draft_purchase_orders': pos.filter(status='draft').count(),
            'supplier_watch': len(supplier_watch),
        },
        'cycle_time': _cycle_metrics(prs, pos, receipts),
    }
    return {
        'schema_version': '1.0',
        'definition_version': DEFINITION_VERSION,
        'generated_at': timezone.now().isoformat(),
        'as_of_date': today.isoformat(),
        'scope': scoped['scope'],
        'terminology': TERMINOLOGY,
        'metrics': metrics,
        'actions': actions,
        'supplier_spend': supplier_spend,
        'supplier_spend_aed': supplier_spend_aed,
        'spend_trend': _spend_trend(commitments, scoped['period_start'], scoped['period_end'], today),
        'purchase_order_status': _order_status(pos),
        'supplier_readiness': _supplier_readiness(suppliers, supplier_watch),
        'purchasing_flow': _purchasing_flow(prs, approved_prs, pos, commitments, receipts),
        'recent_decisions': _recent_decisions(prs, pos),
        'supplier_watch': supplier_watch[:20],
        'data_quality': {
            **project_links,
            'canonical_project_link_percent': round(
                100 * (prs.count() + pos.count() - sum(project_links.values())) / (prs.count() + pos.count())
            ) if prs.count() + pos.count() else 100,
            'limitations': [
                'Executive commitment and supplier-spend totals use the controlled Finance FX-to-AED rate table.',
                'AED totals are withheld when a source currency has no configured Finance conversion rate.',
                'Received value remains unavailable until receipt lines have governed accepted quantity and unit-value data.',
                'Invoiced value includes only linked invoices with Verified match status; paid value uses recorded Finance payment postings.',
                'Predictive supplier risk and savings opportunity are intentionally excluded.',
            ],
        },
    }


@transaction.atomic
def create_snapshot(user, params):
    payload = build_dashboard(user, params)
    scope = payload['scope']
    snapshot = ProcurementReportingSnapshot.objects.create(
        definition_version=DEFINITION_VERSION,
        scope=scope,
        period_start=scope['period_start'],
        period_end=scope['period_end'],
        reporting_currency=REPORTING_CURRENCY,
        payload=payload,
        created_by=user,
    )
    metric_rows = []
    for metric_key, result in payload['metrics'].items():
        metric_rows.append(ProcurementCalculationAudit(
            snapshot=snapshot,
            metric_key=metric_key,
            definition_version=DEFINITION_VERSION,
            inputs={'scope': scope, 'as_of_date': payload['as_of_date']},
            result={'value': result},
        ))
    ProcurementCalculationAudit.objects.bulk_create(metric_rows)
    return snapshot
