"""Read-only financial summary across the separate A/R and A/P ledgers."""

from decimal import Decimal

from django.db.models import Case, Count, DecimalField, ExpressionWrapper, F, Q, Sum, Value, When
from django.db.models.functions import Coalesce, Greatest
from django.utils import timezone

from apps.finance.models import (
    Invoice,
    InvoiceMatchStatus,
    InvoicePaymentStatus,
    InvoiceStatus,
    ProcurementInvoiceStatus,
)
from apps.invoice_tracker.models import CustomerInvoice, PaymentStatus
from apps.invoice_tracker.services.finance_engine import FINANCE_RULES


MONEY_FIELD = DecimalField(max_digits=24, decimal_places=2)
ZERO = Value(Decimal('0.00'), output_field=MONEY_FIELD)


def _money(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))


def build_combined_invoice_summary(*, payables=None, receivables=None):
    """Return normalized totals without combining unlike currencies."""
    payables = payables if payables is not None else Invoice.objects.all()
    receivables = receivables if receivables is not None else CustomerInvoice.objects.all()

    active_receivables = receivables.exclude(payment_status=PaymentStatus.CANCELLED)
    receivable_invoice_value = Case(
        When(Q(grand_total__isnull=True) | Q(grand_total=0), then=Coalesce(F('invoice_amount'), ZERO)),
        default=F('grand_total'),
        output_field=MONEY_FIELD,
    )
    receivable_rows = active_receivables.values('currency').annotate(
        invoice_count=Count('id'),
        invoice_total=Coalesce(Sum(receivable_invoice_value), ZERO),
        outstanding=Coalesce(Sum('balance_to_be_received'), ZERO),
        received=Coalesce(Sum('actual_payment_received'), ZERO),
    )

    active_payables = payables.exclude(payment_status=InvoicePaymentStatus.CANCELLED)
    payable_outstanding = Greatest(
        Coalesce(F('total_amount'), ZERO) - Coalesce(F('paid_amount'), ZERO),
        ZERO,
    )
    payable_rows = active_payables.values('currency').annotate(
        invoice_count=Count('id'),
        invoice_total=Coalesce(Sum('total_amount'), ZERO),
        outstanding=Coalesce(
            Sum(ExpressionWrapper(payable_outstanding, output_field=MONEY_FIELD)),
            ZERO,
        ),
        paid=Coalesce(Sum('paid_amount'), ZERO),
    )

    ar = {row['currency'] or 'AED': row for row in receivable_rows}
    ap = {row['currency'] or 'AED': row for row in payable_rows}
    currencies = sorted(set(ar) | set(ap))
    by_currency = []
    for currency in currencies:
        ar_row, ap_row = ar.get(currency, {}), ap.get(currency, {})
        receivable_outstanding = _money(ar_row.get('outstanding'))
        payable_outstanding_value = _money(ap_row.get('outstanding'))
        by_currency.append({
            'currency': currency,
            'receivable_count': ar_row.get('invoice_count', 0),
            'receivable_total': str(_money(ar_row.get('invoice_total'))),
            'receivable_outstanding': str(receivable_outstanding),
            'receivable_received': str(_money(ar_row.get('received'))),
            'payable_count': ap_row.get('invoice_count', 0),
            'payable_total': str(_money(ap_row.get('invoice_total'))),
            'payable_outstanding': str(payable_outstanding_value),
            'payable_paid': str(_money(ap_row.get('paid'))),
            'net_outstanding': str(receivable_outstanding - payable_outstanding_value),
        })

    fx_rates = FINANCE_RULES['fx_to_aed']
    missing_fx_currencies = []
    receivables_aed = Decimal('0')
    receivables_received_aed = Decimal('0')
    payables_aed = Decimal('0')
    for row in by_currency:
        rate = fx_rates.get(row['currency'])
        if rate is None:
            missing_fx_currencies.append(row['currency'])
            continue
        receivables_aed += _money(row['receivable_outstanding']) * rate
        receivables_received_aed += _money(row['receivable_received']) * rate
        payables_aed += _money(row['payable_outstanding']) * rate

    collection_denominator = receivables_received_aed + receivables_aed
    collection_rate = (
        receivables_received_aed / collection_denominator * Decimal('100')
        if collection_denominator else Decimal('0')
    )
    approval_bottlenecks = payables.filter(
        Q(status=InvoiceStatus.PENDING_APPROVAL)
        | Q(procurement_status__in=(
            ProcurementInvoiceStatus.PROCUREMENT_REVIEW,
            ProcurementInvoiceStatus.FINANCE_REVIEW,
        ))
    ).distinct().count()

    return {
        'generated_at': timezone.now().isoformat(),
        'currency_conversion_applied': False,
        'executive_kpis': {
            'base_currency': 'AED',
            'fx_conversion_applied': True,
            'fx_rates': {currency: str(rate) for currency, rate in fx_rates.items()},
            'missing_fx_currencies': missing_fx_currencies,
            'total_receivables': str(receivables_aed.quantize(Decimal('0.01'))),
            'total_payables': str(payables_aed.quantize(Decimal('0.01'))),
            'net_exposure': str((receivables_aed - payables_aed).quantize(Decimal('0.01'))),
            'collection_rate': str(collection_rate.quantize(Decimal('0.1'))),
            'overdue_receivables': receivables.filter(payment_status=PaymentStatus.OVERDUE).count(),
            'approval_bottlenecks': approval_bottlenecks,
        },
        'counts': {
            'receivable_total': receivables.count(),
            'receivable_overdue': receivables.filter(payment_status=PaymentStatus.OVERDUE).count(),
            'payable_total': payables.count(),
            'payable_exceptions': payables.filter(match_status=InvoiceMatchStatus.EXCEPTION).count(),
            'payable_pending_payment': active_payables.exclude(payment_status=InvoicePaymentStatus.PAID).count(),
        },
        'by_currency': by_currency,
    }
