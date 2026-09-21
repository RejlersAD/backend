"""Customer invoice table permissions, paging and explicit monetary bases."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import path

from apps.finance import tests_command_center as command_center_tests
from apps.finance.command_center_views import FinanceCustomerInvoiceRegisterView
from apps.invoice_tracker.models import CustomerInvoice
from apps.rbac.route_guard import secure_module_endpoints


URL = '/api/v1/finance/dashboard/customer-invoices/'
urlpatterns = [path('api/v1/finance/dashboard/customer-invoices/', FinanceCustomerInvoiceRegisterView.as_view())]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class CustomerInvoiceRegisterTests(TestCase):
    setUp = command_center_tests.FinanceCommandCenterTests.setUp
    grant = command_center_tests.FinanceCommandCenterTests.grant
    deny = command_center_tests.FinanceCommandCenterTests.deny

    def invoice(self, number, *, amount='100', home='100', due=None, **fields):
        defaults = {
            'invoice_number': number, 'account': 'Customer', 'company': 'Entity',
            'invoice_date': date(2026, 9, 21), 'due_date': date(2026, 10, 21),
            'currency': 'AED', 'payment_status': 'pending',
            'invoice_amount': Decimal(amount) if amount is not None else None,
            'invoice_amount_aed': Decimal(home) if home is not None else None,
            'balance_to_be_received': Decimal(due) if due is not None else None,
            'actual_payment_received': Decimal(amount) - Decimal(due) if amount is not None and due is not None else None,
        }
        item = CustomerInvoice(**{**defaults, **fields})
        item.save(_skip_recompute=True)
        return item

    def report(self, **params):
        response = self.client.get(URL, params)
        self.assertEqual(response.status_code, 200, getattr(response, 'data', None))
        return response.data

    def test_overview_and_independent_source_read_grants_are_required(self):
        self.grant('finance_outgoing')
        self.assertEqual(self.client.get(URL).status_code, 403)
        self.grant('finance_overview')
        self.deny('finance_outgoing')
        with patch('apps.finance.services.customer_invoice_register._selected') as select:
            data = self.report()
        select.assert_not_called()
        self.assertEqual(data['status'], 'restricted')
        self.assertEqual(data['rows'], [])
        self.assertIsNone(data['pagination']['count'])
        self.assertIsNone(data['pagination']['pages'])
        self.assertIsNone(data['source']['route'])
        self.assertTrue(all(metric['amount'] is None and metric['known_amount'] is None and metric['count'] is None for metric in data['totals'].values()))
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(URL).status_code, [401, 403])

    def test_explicit_denies_apply_to_superuser_and_no_staff_bypass(self):
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        self.assertEqual(self.client.get(URL).status_code, 403)
        self.grant('finance_overview', 'finance_outgoing')
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        self.deny('finance_outgoing')
        self.assertEqual(self.report()['status'], 'restricted')
        self.deny('finance_overview')
        self.assertEqual(self.client.get(URL).status_code, 403)

    def test_totals_cover_all_pages_and_paging_has_stable_order(self):
        self.grant('finance_overview', 'finance_outgoing')
        records = [self.invoice(f'INV-{index}', amount=str(index + 1), home=str(index + 1), due=str(index + 1)) for index in range(9)]
        first = self.report()
        second = self.report(page=2)
        self.assertEqual(first['pagination'], {'page': 1, 'page_size': 8, 'count': 9, 'pages': 2, 'has_next': True, 'has_previous': False})
        self.assertEqual([row['id'] for row in first['rows']], [row.pk for row in records[:8]])
        self.assertEqual([row['id'] for row in second['rows']], [records[-1].pk])
        self.assertEqual(first['totals'], second['totals'])
        self.assertEqual(first['totals']['amount']['amount'], '45.00')
        self.assertEqual(first['totals']['amount']['count'], 9)
        self.assertEqual(second['pagination']['has_next'], False)
        self.assertEqual(self.report(page_size=20)['pagination']['pages'], 1)
        self.assertEqual(self.client.get(URL, {'page': 3}).status_code, 404)

    def test_register_includes_settled_invoices_excludes_cancelled_credit_and_ignores_chart_dates(self):
        self.grant('finance_overview', 'finance_outgoing')
        self.invoice('PAID', amount='200', home='200', due='0', payment_status='paid', invoice_date=date(2024, 1, 1))
        self.invoice('PENDING', amount='30', home='30', due='30', payment_status='pending', due_date=date(2020, 1, 1))
        self.invoice('CANCELLED', amount='999', payment_status='cancelled')
        self.invoice('CREDIT', amount='999', payment_status='credit_note')
        data = self.report(months=6, as_of='2025-01-01')
        self.assertEqual(data['pagination']['count'], 2)
        self.assertEqual(data['totals']['amount']['amount'], '230.00')
        self.assertEqual(data['totals']['amount_due_home']['amount'], '30.00')
        self.assertEqual({row['payment_status'] for row in data['rows']}, {'paid', 'pending'})
        self.assertEqual({row['payment_status_label'] for row in data['rows']}, {'Paid', 'Pending'})
        self.assertIn('do not filter this register', data['definitions']['period'])

    def test_original_amount_uses_invoice_amount_only_and_preserves_zero(self):
        self.grant('finance_overview', 'finance_outgoing')
        self.invoice('PRIMARY', amount='50', grand_total=Decimal('999'))
        self.invoice('FALLBACK', amount=None, grand_total=Decimal('25'))
        self.invoice('ZERO', amount='0', grand_total=Decimal('800'))
        self.invoice('UNKNOWN', amount=None, grand_total=None)
        data = self.report()
        rows = {row['invoice_number']: row for row in data['rows']}
        self.assertEqual(rows['PRIMARY']['amount'], '50.00')
        self.assertEqual(rows['PRIMARY']['amount_basis'], 'invoice_amount')
        self.assertIsNone(rows['FALLBACK']['amount'])
        self.assertEqual(rows['FALLBACK']['amount_basis'], 'not_recorded')
        self.assertEqual(rows['ZERO']['amount'], '0.00')
        self.assertIsNone(rows['UNKNOWN']['amount'])
        self.assertEqual(data['totals']['amount'], {'amount': None, 'known_amount': '50.00', 'count': 4, 'missing_count': 2, 'partial': True, 'currency': 'AED'})

    def test_foreign_home_due_is_unknown_and_persisted_home_invoice_amount_is_not_recomputed(self):
        self.grant('finance_overview', 'finance_outgoing')
        self.invoice('USD', amount='10', home='99.99', due='5', currency='usd')
        self.invoice('AED', amount='500', currency='AED')
        with patch.object(CustomerInvoice, 'recompute_all') as recompute:
            data = self.report(currency='USD')
        recompute.assert_not_called()
        row = data['rows'][0]
        self.assertEqual(row['currency'], 'USD')
        self.assertEqual(row['amount'], '10.00')
        self.assertEqual(row['amount_home'], '99.99')
        self.assertIsNone(row['amount_due_home'])
        self.assertEqual(row['amount_due_home_basis'], 'not_recorded_for_foreign_currency')
        self.assertEqual(data['totals']['amount_due_home'], {'amount': None, 'known_amount': None, 'count': 1, 'missing_count': 1, 'partial': True, 'currency': 'AED'})
        self.assertFalse(data['currency_conversion_applied'])

    def test_missing_home_fields_stay_unknown_including_aed_and_unknown_original_units(self):
        self.grant('finance_overview', 'finance_outgoing')
        self.invoice('AED-NO-HOME', amount='10', home=None, due=None)
        data = self.report()
        self.assertEqual(data['rows'][0]['amount'], '10.00')
        self.assertIsNone(data['rows'][0]['amount_home'])
        self.assertEqual(data['rows'][0]['amount_due_home'], '10.00')
        self.assertIsNone(data['totals']['amount_home']['known_amount'])
        self.invoice('UNKNOWN-CURRENCY', amount='900', home='70', due='900', currency='')
        unknown = self.report(currency='UNSPECIFIED')
        self.assertIsNone(unknown['rows'][0]['amount'])
        self.assertIsNone(unknown['totals']['amount']['known_amount'])
        self.assertEqual(unknown['totals']['amount_home']['amount'], '70.00')
        self.assertIsNone(unknown['rows'][0]['amount_due_home'])

    def test_foreign_calculated_zero_is_known_home_zero_but_paid_status_does_not_invent_zero(self):
        self.grant('finance_overview', 'finance_outgoing')
        self.invoice('SETTLED', currency='USD', due='0', payment_status='paid')
        self.invoice('ZERO-PENDING', currency='USD', due='0', payment_status='pending')
        self.invoice('PAID-UNKNOWN', currency='USD', due=None, payment_status='paid')
        self.invoice('POSITIVE', currency='USD', due='1', payment_status='pending')
        data = self.report(currency='USD')
        rows = {row['invoice_number']: row for row in data['rows']}
        for number in ['SETTLED', 'ZERO-PENDING']:
            self.assertEqual(rows[number]['amount_due_home'], '0.00')
            self.assertEqual(rows[number]['amount_due_home_basis'], 'zero_calculated_balance')
        self.assertIsNone(rows['PAID-UNKNOWN']['amount_due_home'])
        self.assertIsNone(rows['POSITIVE']['amount_due_home'])
        self.assertEqual(data['totals']['amount_due_home'], {'amount': None, 'known_amount': '0.00', 'count': 4, 'missing_count': 2, 'partial': True, 'currency': 'AED'})

    def test_company_and_currency_filters_use_same_normalized_scope_as_dashboard(self):
        self.grant('finance_overview', 'finance_outgoing')
        self.invoice('TARGET', company=' Entity A ', currency='aed')
        self.invoice('BRANCH', company='Entity A branch')
        self.invoice('OTHER-CURRENCY', company='Entity A', currency='EUR')
        data = self.report(company='Entity A', currency='aed')
        self.assertEqual(data['pagination']['count'], 1)
        self.assertEqual(data['rows'][0]['invoice_number'], 'TARGET')
        self.assertEqual(data['rows'][0]['company'], 'Entity A')
        self.assertEqual(data['totals']['amount']['amount'], '100.00')

    def test_ordering_in_both_directions_uses_recorded_fields_and_nulls_last(self):
        self.grant('finance_overview', 'finance_outgoing')
        self.invoice('LOW', amount='10', invoice_date=date(2026, 1, 1), company=' Alpha ', account='Zeta')
        self.invoice('HIGH', amount='90', invoice_date=date(2026, 9, 1), company='Zeta', account='Alpha')
        self.invoice('UNKNOWN', amount=None, invoice_date=None, company='Middle', account='Different')
        self.assertEqual([row['invoice_number'] for row in self.report(ordering='amount')['rows']], ['LOW', 'HIGH', 'UNKNOWN'])
        self.assertEqual([row['invoice_number'] for row in self.report(ordering='-amount')['rows']], ['HIGH', 'LOW', 'UNKNOWN'])
        self.assertEqual([row['invoice_number'] for row in self.report(ordering='account')['rows']], ['LOW', 'UNKNOWN', 'HIGH'])
        self.assertEqual([row['invoice_number'] for row in self.report(ordering='company')['rows']], ['LOW', 'UNKNOWN', 'HIGH'])
        self.assertEqual([row['invoice_number'] for row in self.report(ordering='-company')['rows']], ['HIGH', 'UNKNOWN', 'LOW'])
        self.assertEqual([row['invoice_number'] for row in self.report(ordering='-account')['rows']], ['HIGH', 'UNKNOWN', 'LOW'])
        self.assertEqual([row['invoice_number'] for row in self.report(ordering='-invoice_date')['rows']], ['HIGH', 'LOW', 'UNKNOWN'])

    def test_customer_labels_use_company_preserve_original_account_and_sort_missing_companies_last(self):
        self.grant('finance_overview', 'finance_outgoing')
        self.invoice('BLANK-ACCOUNT', company=' Alpha Company ', account='')
        self.invoice('CONFLICTING', company='Beta Company', account=' Ledger account ')
        self.invoice('NO-COMPANY', company='   ', account='Recorded account only')
        ascending = self.report(ordering='company')
        descending = self.report(ordering='-company')
        self.assertEqual([row['invoice_number'] for row in ascending['rows']], ['BLANK-ACCOUNT', 'CONFLICTING', 'NO-COMPANY'])
        self.assertEqual([row['invoice_number'] for row in descending['rows']], ['CONFLICTING', 'BLANK-ACCOUNT', 'NO-COMPANY'])
        rows = {row['invoice_number']: row for row in ascending['rows']}
        self.assertEqual(rows['BLANK-ACCOUNT']['customer'], 'Alpha Company')
        self.assertEqual(rows['BLANK-ACCOUNT']['company'], 'Alpha Company')
        self.assertEqual(rows['BLANK-ACCOUNT']['account'], '')
        self.assertEqual(rows['CONFLICTING']['customer'], 'Beta Company')
        self.assertEqual(rows['CONFLICTING']['account'], ' Ledger account ')
        self.assertEqual(rows['NO-COMPANY']['customer'], 'Customer not recorded')
        self.assertEqual(rows['NO-COMPANY']['company'], '')
        self.assertEqual(rows['NO-COMPANY']['account'], 'Recorded account only')
        self.assertEqual(ascending['totals']['amount']['amount'], '300.00')
        self.assertEqual(ascending['totals'], descending['totals'])

    def test_empty_success_is_zero_and_read_failures_are_unknown_without_row_leaks(self):
        self.grant('finance_overview', 'finance_outgoing')
        data = self.report()
        self.assertEqual(data['pagination']['count'], 0)
        self.assertTrue(all(metric['amount'] == '0.00' and not metric['partial'] for metric in data['totals'].values()))
        self.invoice('PRIVATE-IDENTIFIER')
        with patch('apps.finance.services.customer_invoice_register._selected', side_effect=RuntimeError('test source unavailable')), self.assertLogs('apps.finance.services.customer_invoice_register', level='ERROR'):
            failed = self.report()
        self.assertEqual(failed['status'], 'error')
        self.assertEqual(failed['rows'], [])
        self.assertIsNone(failed['pagination']['count'])
        self.assertIsNone(failed['totals']['amount']['known_amount'])
        self.assertNotIn('PRIVATE-', str(failed))

    def test_invalid_paging_ordering_and_currency_are_rejected(self):
        self.grant('finance_overview', 'finance_outgoing')
        for params in [{'page': 0}, {'page': 'bad'}, {'page_size': 100}, {'page_size': 0}, {'ordering': 'id'}, {'ordering': 'account,-id'}, {'currency': 'AED USD'}]:
            with self.subTest(params=params):
                self.assertEqual(self.client.get(URL, params).status_code, 400)

    def test_endpoint_is_read_only_and_private_with_source_timestamp(self):
        self.grant('finance_overview', 'finance_outgoing')
        invoice = self.invoice('READ-ONLY')
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response.data['source']['source_updated_at'], invoice.updated_at.isoformat())
        self.assertEqual([row['sql'] for row in queries if row['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE '))], [])
        self.assertEqual(self.client.post(URL, {}).status_code, 403)

    def test_formula_controls_home_due_totals_and_sort_without_stored_balance_fallback(self):
        self.grant('finance_overview', 'finance_outgoing')
        self.invoice('TEN', amount='100', actual_payment_received=Decimal('90'),
                     balance_to_be_received=Decimal('999'), grand_total=Decimal('9000'))
        self.invoice('FIFTY', amount='60', actual_payment_received=Decimal('10'),
                     balance_to_be_received=Decimal('0'))
        self.invoice('OVERPAID', amount='40', actual_payment_received=Decimal('60'),
                     balance_to_be_received=Decimal('900'))
        self.invoice('UNKNOWN', amount=None, actual_payment_received=Decimal('5'),
                     balance_to_be_received=Decimal('700'), grand_total=Decimal('500'))
        ascending = self.report(ordering='amount_due_home')
        descending = self.report(ordering='-amount_due_home')
        self.assertEqual([row['invoice_number'] for row in ascending['rows']],
                         ['OVERPAID', 'TEN', 'FIFTY', 'UNKNOWN'])
        self.assertEqual([row['invoice_number'] for row in descending['rows']],
                         ['FIFTY', 'TEN', 'OVERPAID', 'UNKNOWN'])
        self.assertEqual([row['amount_due_home'] for row in ascending['rows']],
                         ['-20.00', '10.00', '50.00', None])
        self.assertEqual(ascending['totals']['amount_due_home'], {
            'amount': None, 'known_amount': '40.00', 'count': 4,
            'missing_count': 1, 'partial': True, 'currency': 'AED',
        })
        self.assertEqual(ascending['totals'], descending['totals'])
        self.assertEqual(ascending['totals']['actual_payment_received']['amount'], '165.00')

    def test_receipt_totals_cover_all_pages_and_preserve_blank_rows_without_inventing_fx(self):
        self.grant('finance_overview', 'finance_outgoing')
        self.invoice('BLANK-RECEIPT', amount='100', actual_payment_received=None,
                     balance_to_be_received=Decimal('9000'), currency='USD')
        for index in range(8):
            self.invoice(f'RECEIPT-{index}', actual_payment_received=Decimal('5'), currency='USD')
        first = self.report(currency='USD', page=1)
        second = self.report(currency='USD', page=2)
        self.assertIsNone(first['rows'][0]['actual_payment_received'])
        self.assertIsNone(first['rows'][0]['amount_due_home'])
        self.assertEqual(first['totals'], second['totals'])
        self.assertEqual(first['totals']['actual_payment_received'], {
            'amount': '40.00', 'known_amount': '40.00', 'count': 9,
            'missing_count': 0, 'partial': False, 'currency': 'USD',
        })
        self.assertIn('blank receipts as zero', first['definitions']['actual_payment_received'])

    def test_extended_columns_and_overdue_day_sort_use_selected_date_without_writes(self):
        self.grant('finance_overview', 'finance_outgoing')
        reference = date(2026, 9, 21)
        recorded = self.invoice(
            'OVERDUE', amount='100', home='367.25', due_date=reference - timedelta(days=40),
            invoice_sent_date=date(2026, 7, 2), project_name='Recorded project',
            payment_terms='30 days', pm='Recorded PM', days_overdue=999,
            payment_date=date(2026, 9, 3), actual_payment_received=Decimal('25'),
            remarks='Recorded note',
        )
        self.invoice('PAID', payment_status='paid', due_date=reference - timedelta(days=80))
        self.invoice('FUTURE', due_date=reference + timedelta(days=5))
        self.invoice('NO-DUE', due_date=None)
        with CaptureQueriesContext(connection) as queries:
            descending = self.report(as_of=reference.isoformat(), ordering='-days_overdue')
        rows = {row['invoice_number']: row for row in descending['rows']}
        row = rows['OVERDUE']
        for key, expected in {
            'invoice_sent_date': '2026-07-02', 'project_name': 'Recorded project',
            'payment_terms': '30 days', 'pm': 'Recorded PM', 'days_overdue': 40,
            'payment_date': '2026-09-03', 'actual_payment_received': '25.00',
            'remarks': 'Recorded note', 'invoice_amount': '100.00', 'invoice_amount_aed': '367.25',
        }.items():
            self.assertEqual(row[key], expected, key)
        self.assertEqual(rows['PAID']['days_overdue'], 0)
        self.assertEqual(rows['FUTURE']['days_overdue'], 0)
        self.assertIsNone(rows['NO-DUE']['days_overdue'])
        self.assertEqual(descending['as_of_date'], '2026-09-21')
        self.assertEqual([item['invoice_number'] for item in descending['rows']],
                         ['OVERDUE', 'PAID', 'FUTURE', 'NO-DUE'])
        ascending = self.report(as_of='2026-09-21', ordering='days_overdue')
        self.assertEqual([item['invoice_number'] for item in ascending['rows']],
                         ['PAID', 'FUTURE', 'OVERDUE', 'NO-DUE'])
        earlier = self.report(as_of='2026-08-01')
        self.assertEqual(earlier['pagination']['count'], 4)
        self.assertEqual(earlier['totals'], descending['totals'])
        self.assertEqual(next(item for item in earlier['rows'] if item['invoice_number'] == 'OVERDUE')['days_overdue'], 0)
        self.assertEqual([q['sql'] for q in queries if q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))], [])
        recorded.refresh_from_db()
        self.assertEqual(recorded.days_overdue, 999)

    def test_additional_columns_support_server_ordering_and_date_validation(self):
        from django.utils import timezone

        self.grant('finance_overview', 'finance_outgoing')
        self.invoice('A', invoice_sent_date=date(2026, 1, 1), project_name='Alpha',
                     payment_terms='10 days', pm='Alpha', payment_date=date(2026, 1, 1),
                     actual_payment_received=Decimal('1'), remarks='Alpha')
        self.invoice('Z', invoice_sent_date=date(2026, 2, 1), project_name='Zeta',
                     payment_terms='20 days', pm='Zeta', payment_date=date(2026, 2, 1),
                     actual_payment_received=Decimal('2'), remarks='Zeta')
        for field in ('invoice_sent_date', 'project_name', 'payment_terms', 'pm',
                      'payment_date', 'actual_payment_received', 'remarks'):
            with self.subTest(field=field):
                self.assertEqual([row['invoice_number'] for row in self.report(ordering=field)['rows']], ['A', 'Z'])
                self.assertEqual([row['invoice_number'] for row in self.report(ordering='-' + field)['rows']], ['Z', 'A'])
        for as_of in ('bad', '1901-01-01', (timezone.localdate() + timedelta(days=1)).isoformat()):
            self.assertEqual(self.client.get(URL, {'as_of': as_of}).status_code, 400)
