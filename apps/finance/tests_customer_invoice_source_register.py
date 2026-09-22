"""Register and reconciliation totals preserve the active workbook's facts."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from apps.finance import tests_command_center as command_center_tests
from apps.finance.receivables_source_models import ReceivablesSourceRow, ReceivablesSourceSnapshot
from apps.finance.tests_customer_invoice_register import URL
from apps.invoice_tracker.models import CustomerInvoice


@override_settings(ROOT_URLCONF='apps.finance.tests_customer_invoice_register')
class CustomerInvoiceSourceRegisterTests(TestCase):
    setUp = command_center_tests.FinanceCommandCenterTests.setUp
    grant = command_center_tests.FinanceCommandCenterTests.grant
    deny = command_center_tests.FinanceCommandCenterTests.deny

    def snapshot(self, **fields):
        return ReceivablesSourceSnapshot.objects.create(**{
            'sha256': 'a' * 64, 'file_name': 'Finance.xlsx', 'sheet_name': 'External Invoice ',
            'last_row': 100, 'row_count': 95, 'is_active': True, **fields,
        })

    def row(self, snapshot, number, **fields):
        return ReceivablesSourceRow.objects.create(**{
            'snapshot': snapshot, 'row_number': number, 'invoice_number': f'INV-{number}',
            'account': 'Recorded account', 'company': ' Entity A ', 'currency': 'AED',
            'invoice_date': date(2026, 9, 1), 'due_date': date(2026, 9, 2),
            'payment_status': 'overdue', 'raw_payment_status': 'Overdue',
            'invoice_amount': Decimal('100'), 'invoice_amount_aed': Decimal('100'),
            'balance_to_be_received': Decimal('27'), 'balance_currency': 'AED',
            'actual_payment_received': Decimal('3'), 'actual_payment_currency': 'AED',
            **fields,
        })

    def report(self, **params):
        self.grant('finance_overview', 'finance_outgoing')
        response = self.client.get(URL, {'as_of': '2026-09-22', **params})
        self.assertEqual(response.status_code, 200, getattr(response, 'data', None))
        return response.data

    def test_overdue_reconciliation_uses_all_recorded_home_amounts_and_original_statuses(self):
        snapshot = self.snapshot()
        self.row(snapshot, 6, invoice_amount=Decimal('13650'), invoice_amount_aed=Decimal('50095.50'), currency='USD')
        self.row(snapshot, 7, invoice_amount=Decimal('250'), invoice_amount_aed=Decimal('250'))
        self.row(snapshot, 8, invoice_amount=Decimal('14900'), invoice_amount_aed=Decimal('59600'), currency='EUR')
        self.row(snapshot, 9, payment_status='pending', raw_payment_status='Pending', invoice_amount_aed=Decimal('9000'))
        operational = CustomerInvoice(invoice_number='INV-6', payment_status='overdue',
                                      invoice_amount=Decimal('999999'), currency='AED')
        operational.save(_skip_recompute=True)
        with patch.object(CustomerInvoice, 'recompute_all') as recompute:
            report = self.report(payment_status='overdue')
        recompute.assert_not_called()
        self.assertEqual(report['pagination']['count'], 3)
        self.assertEqual(report['totals']['amount_home']['amount'], '109945.50')
        self.assertEqual(report['source']['mode'], 'workbook')
        self.assertEqual(report['source']['snapshot_id'], snapshot.pk)
        self.assertEqual(report['source']['sha256'], 'a' * 64)
        self.assertEqual(report['source']['file_name'], 'Finance.xlsx')
        self.assertEqual(report['source']['sheet_name'], 'External Invoice ')
        self.assertEqual(report['source']['source_updated_at'], snapshot.imported_at.isoformat())
        self.assertEqual(report['filters']['payment_status'], 'overdue')
        self.assertTrue(all(row['payment_status_label'] == 'Overdue' for row in report['rows']))
        self.assertEqual({row['currency'] for row in report['rows']}, {'AED', 'USD', 'EUR'})

    def test_source_rows_are_distinct_and_cannot_link_to_operational_invoices(self):
        snapshot = self.snapshot()
        first = self.row(snapshot, 6, invoice_number='DUPLICATE')
        second = self.row(snapshot, 7, invoice_number='DUPLICATE')
        report = self.report()
        self.assertEqual([row['id'] for row in report['rows']], [f'source:{first.pk}', f'source:{second.pk}'])
        self.assertEqual([row['source_row'] for row in report['rows']], [6, 7])
        for row in report['rows']:
            self.assertTrue(row['source_snapshot'])
            self.assertIsNone(row['invoice_route'])

    def test_mixed_invoice_and_receipt_currencies_are_not_mislabeled_as_aed(self):
        snapshot = self.snapshot()
        self.row(snapshot, 6, currency='USD', invoice_amount=Decimal('10'),
                 actual_payment_received=Decimal('1'), actual_payment_currency='EUR')
        self.row(snapshot, 7, currency='AED', invoice_amount=Decimal('20'),
                 actual_payment_received=Decimal('2'), actual_payment_currency='USD')
        report = self.report()
        for key in ('amount', 'actual_payment_received'):
            self.assertIsNone(report['totals'][key]['amount'])
            self.assertIsNone(report['totals'][key]['known_amount'])
            self.assertEqual(report['totals'][key]['currency'], 'MIXED')
            self.assertTrue(report['totals'][key]['partial'])
        self.assertEqual(report['totals']['amount']['by_currency']['USD']['amount'], '10.00')
        self.assertEqual(report['totals']['amount']['by_currency']['AED']['amount'], '20.00')
        self.assertEqual(report['totals']['actual_payment_received']['by_currency']['EUR']['amount'], '1.00')
        self.assertEqual(report['rows'][0]['actual_payment_currency'], 'EUR')
        self.assertEqual(report['totals']['amount_home']['amount'], '200.00')

    def test_recorded_balance_and_receipts_are_preserved_without_recomputation(self):
        snapshot = self.snapshot()
        self.row(snapshot, 6, invoice_amount=Decimal('100'), balance_to_be_received=Decimal('27'),
                 actual_payment_received=Decimal('3'), raw_payment_status='Paid (partial)', payment_status='partial')
        self.row(snapshot, 7, currency='USD', balance_currency='USD', balance_to_be_received=Decimal('0'))
        self.row(snapshot, 8, actual_payment_received=None)
        report = self.report()
        rows = {row['source_row']: row for row in report['rows']}
        self.assertEqual(rows[6]['amount_due_home'], '27.00')
        self.assertEqual(rows[6]['amount_due_home_basis'], 'recorded_balance_to_be_received')
        self.assertEqual(rows[6]['payment_status_label'], 'Paid (partial)')
        self.assertIsNone(rows[7]['amount_due_home'])
        self.assertIsNone(rows[8]['actual_payment_received'])
        self.assertIsNone(report['totals']['actual_payment_received']['amount'])
        self.assertEqual(report['totals']['actual_payment_received']['known_amount'], '6.00')
        self.assertEqual(report['totals']['amount_due_home']['missing_count'], 1)

    def test_company_native_currency_and_status_filters_are_exact_and_keep_new(self):
        snapshot = self.snapshot()
        self.row(snapshot, 6, currency='USD', payment_status='new', raw_payment_status='New')
        self.row(snapshot, 7, currency='USD', company='Entity A branch')
        self.row(snapshot, 8, currency='EUR')
        self.row(snapshot, 9, currency='USD', payment_status='pending', raw_payment_status='Pending')
        report = self.report(currency='usd', company='Entity A', payment_status='new')
        self.assertEqual(report['pagination']['count'], 1)
        self.assertEqual(report['rows'][0]['source_row'], 6)
        self.assertEqual(report['rows'][0]['payment_status_label'], 'New')
        self.assertEqual(report['totals']['amount']['currency'], 'USD')
        self.assertEqual(report['totals']['actual_payment_received']['currency'], 'AED')
        self.assertEqual(self.client.get(URL, {'payment_status': 'over'}).status_code, 400)

    def test_ageing_uses_due_date_but_never_rewrites_status_or_filters_current_rows(self):
        snapshot = self.snapshot()
        self.row(snapshot, 6, due_date=date(2026, 7, 24), invoice_sent_date=date(2026, 7, 1),
                 payment_date=date(2026, 9, 1), project_name='Project', payment_terms='60',
                 remarks='Finance note', pm='PM')
        self.row(snapshot, 7, due_date=date(2026, 1, 1), payment_status='paid', raw_payment_status='Paid')
        self.row(snapshot, 8, due_date=None)
        report = self.report(ordering='-days_overdue')
        self.assertEqual([row['source_row'] for row in report['rows']], [6, 7, 8])
        self.assertEqual([row['days_overdue'] for row in report['rows']], [60, 0, None])
        self.assertEqual(report['rows'][0]['invoice_sent_date'], '2026-07-01')
        self.assertEqual(report['rows'][0]['payment_date'], '2026-09-01')
        self.assertEqual(report['rows'][0]['remarks'], 'Finance note')
        earlier = self.report(as_of='2026-01-01')
        self.assertEqual(earlier['pagination']['count'], 3)
        self.assertEqual(earlier['rows'][0]['payment_status'], 'overdue')
        self.assertEqual(earlier['totals'], report['totals'])

    def test_pagination_totals_and_null_last_ordering_cover_complete_source(self):
        snapshot = self.snapshot()
        for number in range(6, 15):
            self.row(snapshot, number, invoice_amount_aed=Decimal(number))
        self.row(snapshot, 15, invoice_amount_aed=None)
        first = self.report(ordering='-invoice_amount_aed')
        second = self.report(page=2, ordering='-invoice_amount_aed')
        self.assertEqual(first['pagination']['count'], 10)
        self.assertEqual(first['pagination']['pages'], 2)
        self.assertEqual(first['totals'], second['totals'])
        self.assertIsNone(first['totals']['amount_home']['amount'])
        self.assertEqual(first['totals']['amount_home']['known_amount'], '90.00')
        self.assertEqual(first['rows'][0]['source_row'], 14)
        self.assertEqual(second['rows'][-1]['source_row'], 15)
        self.assertEqual(self.client.get(URL, {'page': 3}).status_code, 404)

    def test_empty_filtered_source_reports_known_zero_without_operational_fallback(self):
        snapshot = self.snapshot()
        self.row(snapshot, 6, payment_status='paid')
        operational = CustomerInvoice(invoice_number='OPERATIONAL', invoice_amount=Decimal('999'),
                                      currency='AED', payment_status='overdue')
        operational.save(_skip_recompute=True)
        report = self.report(payment_status='overdue')
        self.assertEqual(report['pagination']['count'], 0)
        self.assertEqual(report['rows'], [])
        self.assertTrue(all(metric['amount'] == '0.00' for metric in report['totals'].values()))
        self.assertEqual(report['source']['mode'], 'workbook')

    def test_source_reads_are_authorized_and_never_modify_records(self):
        snapshot = self.snapshot()
        self.row(snapshot, 6)
        self.grant('finance_overview')
        with patch('apps.finance.services.receivables_source.get_active_receivables_source') as select:
            response = self.client.get(URL)
        self.assertEqual(response.data['status'], 'restricted')
        select.assert_not_called()
        self.grant('finance_outgoing')
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual([row['sql'] for row in queries if row['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE '))], [])

    def test_unknown_original_currency_does_not_hide_recorded_aed_or_invent_units(self):
        snapshot = self.snapshot()
        self.row(snapshot, 6, currency='', actual_payment_currency='', balance_currency='')
        report = self.report(currency='UNSPECIFIED')
        self.assertEqual(report['rows'][0]['currency'], 'UNSPECIFIED')
        self.assertIsNone(report['rows'][0]['amount'])
        self.assertIsNone(report['rows'][0]['actual_payment_received'])
        self.assertIsNone(report['totals']['amount']['known_amount'])
        self.assertEqual(report['totals']['amount_home']['amount'], '100.00')
        self.assertIsNone(report['rows'][0]['amount_due_home'])
