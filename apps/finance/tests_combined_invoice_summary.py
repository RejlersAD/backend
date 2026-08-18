from decimal import Decimal

from django.test import TestCase

from apps.finance.models import Invoice, InvoicePaymentStatus
from apps.finance.services.combined_invoice_summary import build_combined_invoice_summary
from apps.invoice_tracker.models import CustomerInvoice, PaymentStatus


class CombinedInvoiceSummaryTests(TestCase):
    def _receivable(self, number, currency, total, balance, received='0', status=PaymentStatus.PENDING):
        invoice = CustomerInvoice(
            invoice_number=number,
            currency=currency,
            grand_total=Decimal(total),
            invoice_amount=Decimal(total),
            balance_to_be_received=Decimal(balance),
            actual_payment_received=Decimal(received),
            payment_status=status,
        )
        invoice.save(_skip_recompute=True)
        return invoice

    def _payable(self, number, currency, total, paid='0', status=InvoicePaymentStatus.NOT_SCHEDULED):
        return Invoice.objects.create(
            invoice_number=number,
            currency=currency,
            total_amount=Decimal(total),
            paid_amount=Decimal(paid),
            payment_status=status,
            original_filename=f'{number}.pdf',
            file_path=f'invoices/{number}.pdf',
        )

    def test_ledgers_are_combined_for_reporting_but_not_cross_currency(self):
        self._receivable('AR-AED-1', 'AED', '100', '60', '40')
        self._receivable('AR-USD-1', 'USD', '50', '50')
        self._payable('AP-AED-1', 'AED', '30', '10')

        summary = build_combined_invoice_summary()
        rows = {row['currency']: row for row in summary['by_currency']}

        self.assertFalse(summary['currency_conversion_applied'])
        self.assertEqual(set(rows), {'AED', 'USD'})
        self.assertEqual(Decimal(rows['AED']['receivable_outstanding']), Decimal('60'))
        self.assertEqual(Decimal(rows['AED']['payable_outstanding']), Decimal('20'))
        self.assertEqual(Decimal(rows['AED']['net_outstanding']), Decimal('40'))
        self.assertEqual(Decimal(rows['USD']['net_outstanding']), Decimal('50'))
        self.assertEqual(
            Decimal(summary['executive_kpis']['total_receivables']),
            (Decimal('60') + Decimal('50') * Decimal('3.6725')).quantize(Decimal('0.01')),
        )
        self.assertEqual(Decimal(summary['executive_kpis']['total_payables']), Decimal('20'))
        self.assertEqual(summary['executive_kpis']['base_currency'], 'AED')

    def test_cancelled_rows_are_counted_but_excluded_from_financial_totals(self):
        self._receivable('AR-CANCELLED', 'AED', '900', '900', status=PaymentStatus.CANCELLED)
        self._payable('AP-CANCELLED', 'AED', '800', status=InvoicePaymentStatus.CANCELLED)

        summary = build_combined_invoice_summary()

        self.assertEqual(summary['counts']['receivable_total'], 1)
        self.assertEqual(summary['counts']['payable_total'], 1)
        self.assertEqual(summary['by_currency'], [])
