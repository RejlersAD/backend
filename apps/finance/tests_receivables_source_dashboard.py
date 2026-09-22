"""Finance's workbook example reconciles without rewriting operational invoices."""
from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from apps.finance.models import ReceivablesSourceRow, ReceivablesSourceSnapshot
from apps.finance.tests_receivables_dashboard import ReceivablesDashboardTests, URL


TODAY = date(2026, 9, 22)


@override_settings(ROOT_URLCONF='apps.finance.tests_receivables_dashboard')
class ReceivablesSourceDashboardTests(TestCase):
    setUp = ReceivablesDashboardTests.setUp
    grant = ReceivablesDashboardTests.grant
    ar = ReceivablesDashboardTests.ar

    def source(self):
        self.snapshot = ReceivablesSourceSnapshot.objects.create(
            sha256='a' * 64, file_name='Finance.xlsx', sheet_name='External Invoice ',
            last_row=4409, row_count=4404, is_active=True,
        )

    def row(self, number, **values):
        defaults = dict(snapshot=self.snapshot, row_number=number, invoice_number=str(number),
                        company='Finance customer', currency='AED', currency_status='recorded',
                        invoice_amount=Decimal('100'), invoice_amount_aed=Decimal('100'),
                        invoice_date=TODAY, due_date=TODAY, payment_status='pending',
                        balance_currency='AED', actual_payment_currency='AED')
        defaults.update(values)
        return ReceivablesSourceRow.objects.create(**defaults)

    def report(self, **params):
        with patch('django.utils.timezone.now', return_value=datetime(2026, 9, 22, 9, tzinfo=dt_timezone.utc)):
            response = self.client.get(URL, params)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotEqual(response.data['sources']['receivables']['status'], 'error')
        return response.data

    def test_finance_screenshot_eighteen_overdue_rows_reconcile_to_stored_aed(self):
        self.grant('finance_overview', 'finance_outgoing')
        self.source()
        # Independent values from Finance's 22-Sep-2026 screenshot, columns L/M/N.
        entries = [
            ('USD', '13650', '50095.50', '2026-02-13'),
            ('USD', '8400', '30828', '2026-02-13'),
            ('USD', '40906.18', '150125.6806', '2026-06-24'),
            ('AED', '57750', '57750', '2026-07-03'),
            ('AED', '31500', '31500', '2026-07-26'),
            ('AED', '255834.26', '255834.26', '2026-07-11'),
            ('AED', '60540.48', '60540.48', '2026-08-14'),
            ('AED', '59450.24', '59450.24', '2026-08-16'),
            ('AED', '59209.92', '59209.92', '2026-08-16'),
            ('AED', '526819.03', '526819.03', '2026-08-27'),
            ('AED', '39358.81', '39358.81', '2026-09-07'),
            ('AED', '21905.10', '21905.10', '2026-08-26'),
            ('AED', '25955.18', '25955.18', '2026-08-26'),
            ('AED', '180526.61', '180526.61', '2026-09-13'),
            ('AED', '36209.25', '36209.25', '2026-09-06'),
            ('AED', '36855', '36855', '2026-09-16'),
            ('EUR', '14900', '59600', '2026-09-19'),
            ('AED', '163800', '163800', '2026-09-04'),
        ]
        for number, (currency, original, aed, due) in enumerate(entries, 6):
            self.row(number, currency=currency, invoice_amount=Decimal(original),
                     invoice_amount_aed=Decimal(aed), due_date=date.fromisoformat(due),
                     payment_status='overdue', raw_payment_status='Overdue')
        operational = self.ar('WRONG-IMPORT', '4650327.76', status='overdue', due=date(2013, 4, 16))
        with CaptureQueriesContext(connection) as queries:
            data = self.report(currency='AED')
        self.assertEqual(data['amount_basis'], 'recorded_aed')
        self.assertEqual(data['kpis']['overdue']['amount'], '1846363.06')
        self.assertEqual(data['kpis']['overdue']['count'], 18)
        self.assertEqual(data['kpis']['unpaid'], data['kpis']['overdue'])
        self.assertEqual(data['kpis']['over30']['amount'], '755334.08')
        self.assertEqual(data['kpis']['over60']['amount'], '544633.44')
        self.assertEqual(data['kpis']['over90']['amount'], '80923.50')
        self.assertEqual(data['customers'][0]['overdue'], data['kpis']['overdue'])
        self.assertEqual(data['sources']['receivables']['snapshot_id'], self.snapshot.pk)
        self.assertTrue(all(row['source_snapshot'] and row['invoice_route'] is None for row in data['priority_invoices']))
        self.assertFalse(any(row['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for row in queries))
        operational.refresh_from_db()
        self.assertEqual(operational.invoice_amount, Decimal('4650327.76'))
        usd = self.report(currency='USD')
        self.assertEqual(usd['amount_basis'], 'original_currency')
        self.assertEqual(usd['kpis']['overdue']['amount'], '62956.18')
        self.assertEqual(usd['kpis']['overdue']['count'], 3)

    def test_partial_uses_recorded_aed_balance_and_never_invents_missing_fx_or_balance(self):
        self.grant('finance_overview', 'finance_outgoing')
        self.source()
        self.row(6, payment_status='partial', balance_to_be_received=Decimal('12.25'))
        self.row(7, payment_status='partial', balance_to_be_received=None,
                 actual_payment_received=Decimal('80'))
        self.row(8, payment_status='partial', currency='USD', balance_currency='USD',
                 balance_to_be_received=Decimal('10'), invoice_amount_aed=Decimal('367'))
        self.row(9, payment_status='new', currency='USD', invoice_amount_aed=Decimal('367'))
        self.row(10, payment_status='written off', invoice_amount_aed=Decimal('999999'))
        data = self.report()
        self.assertEqual(data['kpis']['unpaid'], {
            'amount': None, 'known_amount': '379.25', 'count': 4,
            'missing_count': 2, 'partial': True,
        })
        self.assertEqual(data['kpis']['overdue']['amount'], '0.00')

    def test_source_retains_duplicate_invoice_numbers_and_signed_amounts(self):
        self.grant('finance_overview', 'finance_outgoing')
        self.source()
        self.row(6, invoice_number='DUPLICATE', payment_status='overdue', invoice_amount_aed=Decimal('100'))
        self.row(7, invoice_number='DUPLICATE', payment_status='overdue', invoice_amount_aed=Decimal('-25'))
        data = self.report()
        self.assertEqual(data['kpis']['overdue']['amount'], '75.00')
        self.assertEqual(data['kpis']['overdue']['count'], 2)
        self.assertEqual(len({row['id'] for row in data['priority_invoices']}), 2)

    def test_sixty_day_card_uses_due_date_independently_of_payment_status(self):
        self.grant('finance_overview', 'finance_outgoing')
        self.source()
        for number, age in enumerate([30, 31, 60, 61, 90, 91], 6):
            self.row(number, payment_status='pending', invoice_amount_aed=Decimal('10'),
                     due_date=TODAY - timedelta(days=age))
        data = self.report()
        self.assertEqual(data['kpis']['overdue']['amount'], '0.00')
        self.assertEqual(data['kpis']['over30']['amount'], '50.00')
        self.assertEqual(data['kpis']['over60']['amount'], '30.00')
        self.assertEqual(data['kpis']['over90']['amount'], '10.00')

    def test_source_receipts_keep_unknown_values_and_foreign_currencies_separate(self):
        self.grant('finance_overview', 'finance_outgoing')
        self.source()
        self.row(6, actual_payment_received=None)
        self.row(7, actual_payment_received=Decimal('0'))
        self.row(8, actual_payment_received=Decimal('25'), actual_payment_currency='USD')
        self.row(9, actual_payment_received=Decimal('30'), actual_payment_currency='AED')
        paid = self.report()['paid_unpaid_by_month'][-1]['paid']
        self.assertEqual(paid, {'amount': None, 'known_amount': '30.00', 'count': 4,
                                'missing_count': 2, 'partial': True})
