"""Read-time customer balances from Invoice Amount (L) less receipts (AA).

These calculations never replace stored workbook values. A missing invoice
amount stays unknown; a blank receipt is zero, matching Excel subtraction.
The signed result lets each caller exclude settled and overpaid exposure.
"""
from decimal import Decimal


def receivable_balance(invoice_amount, actual_payment_received):
    if invoice_amount is None:
        return None
    received = Decimal('0') if actual_payment_received is None else Decimal(str(actual_payment_received))
    return Decimal(str(invoice_amount)) - received


def annotate_receivable_balance(queryset, alias='calculated_receivable_balance'):
    """Annotate the same difference for database filtering, ordering and totals."""
    from django.db.models import DecimalField, ExpressionWrapper, F, Value
    from django.db.models.functions import Coalesce

    amount_field = DecimalField(max_digits=20, decimal_places=2)
    expression = ExpressionWrapper(
        F('invoice_amount') - Coalesce(
            F('actual_payment_received'), Value(Decimal('0')), output_field=amount_field,
        ),
        output_field=amount_field,
    )
    return queryset.annotate(**{alias: expression})
