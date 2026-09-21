"""Read-only overview authorization and current-balance financial semantics."""
from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import path

from apps.finance.command_center_views import FinanceReceivablesDashboardView
from apps.finance import tests_command_center as command_center_tests
from apps.rbac.route_guard import secure_module_endpoints


URL = '/api/v1/finance/dashboard/receivables/'
TODAY = date(2026, 9, 21)
urlpatterns = [path('api/v1/finance/dashboard/receivables/', FinanceReceivablesDashboardView.as_view())]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class ReceivablesDashboardTests(TestCase):
    setUp = command_center_tests.FinanceCommandCenterTests.setUp
    grant = command_center_tests.FinanceCommandCenterTests.grant
    deny = command_center_tests.FinanceCommandCenterTests.deny
    ap = command_center_tests.FinanceCommandCenterTests.ap

    def ar(self, number, balance='100', currency='AED', due=TODAY, status='pending', **fields):
        item = command_center_tests.FinanceCommandCenterTests.ar(self, number, balance, currency, due, status)
        for field, value in fields.items():
            setattr(item, field, value)
        if fields:
            item.save(_skip_recompute=True, update_fields=list(fields))
        return item

    def report(self, **params):
        with patch('django.utils.timezone.now', return_value=datetime(2026, 9, 21, 9, tzinfo=dt_timezone.utc)):
            response = self.client.get(URL, params)
        self.assertEqual(response.status_code, 200, getattr(response, 'data', None))
        return response.data

    def test_endpoint_requires_overview_read_without_staff_bypass(self):
        self.assertEqual(self.client.get(URL).status_code, 403)
        self.grant('finance_outgoing', 'finance_incoming')
        self.assertEqual(self.client.get(URL).status_code, 403)
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        self.assertEqual(self.client.get(URL).status_code, 403)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(URL).status_code, [401, 403])

    def test_overview_only_does_not_query_sources_or_leak_filter_options(self):
        self.grant('finance_overview')
        self.ar('SECRET', company='Secret entity', account='Secret customer', currency='EUR')
        with patch('apps.finance.services.receivables_dashboard._summary') as summary, patch('apps.finance.services.receivables_dashboard._available_filters') as filters:
            data = self.report()
        summary.assert_not_called()
        filters.assert_not_called()
        self.assertEqual(data['status'], 'restricted')
        self.assertEqual(data['filters']['companies'], [])
        self.assertEqual(data['filters']['currencies'], [])
        self.assertEqual(data['customers'], [])
        self.assertEqual(data['priority_invoices'], [])
        self.assertIsNone(data['kpis']['unpaid']['known_amount'])
        self.assertTrue(all(source['route'] is None and source['open_count'] is None for source in data['sources'].values()))
        self.assertNotIn('Secret', str(data))

    def test_explicit_deny_wins_over_superuser_for_sources_and_overview(self):
        self.grant('finance_overview', 'finance_outgoing', 'finance_incoming')
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        self.deny('finance_outgoing')
        data = self.report()
        self.assertEqual(data['sources']['receivables']['status'], 'restricted')
        self.assertEqual(data['sources']['payables']['status'], 'available')
        self.deny('finance_overview')
        self.assertEqual(self.client.get(URL).status_code, 403)

    def test_ageing_boundaries_keep_unknown_amounts_and_dates_separate(self):
        self.grant('finance_overview', 'finance_outgoing')
        for age in [0, 1, 30, 31, 60, 61, 90, 91]:
            self.ar(f'D{age}', '10', due=TODAY - timedelta(days=age), account='Known')
        self.ar('FUTURE', '10', due=TODAY + timedelta(days=1), account='Known')
        self.ar('UNKNOWN', None, due=TODAY - timedelta(days=10), account='Unknown')
        self.ar('NO-DUE', '30', due=None, account='Known')
        data = self.report()
        unpaid = data['kpis']['unpaid']
        self.assertEqual(unpaid, {'amount': None, 'known_amount': '120.00', 'count': 11, 'missing_count': 1, 'partial': True})
        self.assertEqual(data['kpis']['overdue']['known_amount'], '70.00')
        self.assertIsNone(data['kpis']['overdue']['amount'])
        self.assertEqual(data['kpis']['over30']['amount'], '50.00')
        self.assertEqual(data['kpis']['over90']['amount'], '10.00')
        buckets = {row['id']: row['receivables'] for row in data['ageing']}
        self.assertEqual(buckets['current']['amount'], '20.00')
        self.assertEqual(buckets['days_1_30']['known_amount'], '20.00')
        self.assertEqual(buckets['unknown_due_date']['amount'], '30.00')
        self.assertEqual(data['sources']['receivables']['unknown_due_date_count'], 1)
        self.assertEqual(data['sources']['receivables']['status'], 'incomplete')

    def test_paid_cancelled_credit_zero_and_negative_are_not_open(self):
        self.grant('finance_overview', 'finance_outgoing')
        for state in ['paid', 'cancelled', 'credit_note']:
            self.ar(state, '500', status=state)
        self.ar('zero', '0')
        self.ar('negative', '-1')
        data = self.report()
        self.assertEqual(data['kpis']['unpaid']['amount'], '0.00')
        self.assertEqual(data['kpis']['unpaid']['count'], 0)
        self.assertEqual(data['customers'], [])
        self.assertEqual(data['priority_invoices'], [])

    def test_unknown_only_group_is_null_while_empty_group_is_zero(self):
        self.grant('finance_overview', 'finance_outgoing')
        self.ar('unknown', None, account='Missing')
        data = self.report()
        self.assertIsNone(data['kpis']['unpaid']['known_amount'])
        self.assertIsNone(data['customers'][0]['share_percentage'])
        self.assertEqual(data['kpis']['overdue']['amount'], '0.00')
        self.assertEqual(data['kpis']['overdue']['missing_count'], 0)

    def test_currency_and_recorded_company_filters_never_convert_or_include_other_sources(self):
        self.grant('finance_overview', 'finance_outgoing', 'finance_incoming')
        self.ar('AED', '25', currency=' aed', company=' Branch ', account='One')
        self.ar('OTHER-BRANCH', '800', currency='AED', company='Other', account='Two')
        self.ar('USD', '900', currency='USD', company='Branch', account='Three')
        self.ar('UNKNOWN-CCY', '999', currency='', company='Branch', account='Four')
        data = self.report(currency='aed', company='Branch')
        self.assertEqual(data['currency'], 'AED')
        self.assertEqual(data['kpis']['unpaid']['amount'], '25.00')
        self.assertEqual(data['filters']['companies'], ['Branch', 'Other'])
        self.assertEqual(data['filters']['currencies'], ['AED', 'UNSPECIFIED', 'USD'])
        self.assertEqual(data['sources']['payables']['status'], 'unavailable')
        self.assertIsNone(data['ageing'][0]['payables']['amount'])
        self.assertFalse(data['currency_conversion_applied'])
        unspecified = self.report(currency='UNSPECIFIED')
        self.assertEqual(unspecified['kpis']['unpaid']['count'], 1)
        self.assertIsNone(unspecified['kpis']['unpaid']['known_amount'])
        self.assertIsNone(unspecified['priority_invoices'][0]['balance'])

    def test_chart_period_and_reference_date_do_not_fake_historical_balances(self):
        self.grant('finance_overview', 'finance_outgoing')
        self.ar('OLD', '500', due=date(2024, 1, 1), invoice_date=date(2023, 12, 1))
        self.ar('CURRENT', '200', due=date(2026, 8, 1), invoice_date=date(2026, 7, 2), actual_payment_received=Decimal('80'))
        data = self.report(months=6)
        self.assertEqual(data['kpis']['unpaid']['amount'], '700.00')
        self.assertEqual(len(data['overdue_by_month']), 6)
        self.assertEqual(data['overdue_by_month'][0]['month'], '2026-04')
        self.assertEqual(data['overdue_by_month'][4]['receivables']['amount'], '200.00')
        self.assertEqual(data['chart_exclusions']['receivables']['overdue_outside_window'], 1)
        historical = self.report(as_of='2026-07-15', months=6)
        self.assertEqual(historical['kpis']['unpaid']['amount'], '700.00')
        self.assertEqual(historical['kpis']['overdue']['amount'], '500.00')
        self.assertIn('Current recorded invoice balances', historical['definitions']['balance_basis'])
        self.assertEqual(historical['as_of_date'], '2026-07-15')

    def test_paid_unpaid_cohorts_use_invoice_issue_month_and_recorded_receipts(self):
        self.grant('finance_overview', 'finance_outgoing')
        self.ar('PARTIAL', '60', due=date(2026, 8, 1), invoice_date=date(2026, 7, 15), payment_date=date(2026, 9, 1), actual_payment_received=Decimal('40'))
        self.ar('PAID', '0', status='paid', invoice_date=date(2026, 7, 1), actual_payment_received=Decimal('100'))
        self.ar('MISSING-PAID', '20', invoice_date=date(2026, 7, 1))
        self.ar('NO-DATE', '15', actual_payment_received=Decimal('5'))
        self.ar('CANCELLED', '999', status='cancelled', invoice_date=date(2026, 7, 1), actual_payment_received=Decimal('999'))
        data = self.report()
        cohort = next(row for row in data['paid_unpaid_by_month'] if row['month'] == '2026-07')
        self.assertEqual(cohort['paid'], {'amount': None, 'known_amount': '140.00', 'count': 3, 'missing_count': 1, 'partial': True})
        self.assertEqual(cohort['unpaid']['amount'], '80.00')
        self.assertEqual(data['chart_exclusions']['receivables']['invoice_date_unknown'], 1)
        self.assertEqual(data['paid_unpaid_by_month'][-1]['paid']['amount'], '0.00')
        self.assertIn('not monthly cash flow', data['definitions']['paid_unpaid_by_month'])

    def test_customer_shares_include_all_customers_and_priority_rows_use_recorded_pm(self):
        self.grant('finance_overview', 'finance_outgoing')
        for index in range(7):
            self.ar(f'INV-{index}', str((index + 1) * 10), company=f'Company {index}', account=f'Account {index}', pm='Recorded PM' if index == 6 else '', due=TODAY - timedelta(days=index))
        data = self.report()
        self.assertEqual(len(data['customers']), 7)
        self.assertEqual(data['customers'][0]['company'], 'Company 6')
        self.assertEqual(data['customers'][0]['customer'], 'Company 6')
        self.assertEqual(data['customers'][0]['account'], 'Company 6')
        self.assertEqual(data['customers'][0]['overdue']['amount'], '70.00')
        self.assertEqual(data['customers'][0]['share_percentage'], 25.0)
        self.assertEqual(len(data['priority_invoices']), 5)
        self.assertEqual(data['priority_invoice_count'], 7)
        self.assertEqual(data['priority_invoices'][0]['owner'], 'Recorded PM')
        self.assertEqual(data['priority_invoices'][0]['days_overdue'], 6)
        self.assertEqual(data['priority_invoices'][0]['company'], 'Company 6')
        self.assertEqual(data['priority_invoices'][0]['account'], 'Account 6')
        self.assertIsNone(data['priority_invoices'][1]['owner'])

    def test_company_is_authoritative_customer_identity_and_grouping_without_account_fallback(self):
        self.grant('finance_overview', 'finance_outgoing')
        due = TODAY - timedelta(days=10)
        self.ar('BLANK-ACCOUNT', '100', company=' Acme ', account='', due=due)
        self.ar('CONFLICTING-ACCOUNT', '25', company='Acme', account=' Shared ledger ', due=due)
        self.ar('SAME-ACCOUNT-OTHER-COMPANY', '75', company='Beta', account=' Shared ledger ', due=due)
        self.ar('MISSING-COMPANY', '40', company='', account='Account is not a customer', due=due)
        self.ar('BLANK-COMPANY', None, company='   ', account='Another account', due=due)
        data = self.report()
        customers = {row['company']: row for row in data['customers']}
        self.assertEqual(set(customers), {'Acme', 'Beta', ''})
        self.assertEqual(customers['Acme']['customer'], 'Acme')
        self.assertEqual(customers['Acme']['account'], 'Acme')
        self.assertEqual(customers['Acme']['amount'], '125.00')
        self.assertEqual(customers['Acme']['count'], 2)
        self.assertEqual(customers['Acme']['overdue']['amount'], '125.00')
        self.assertEqual(customers['Beta']['amount'], '75.00')
        self.assertEqual(customers['']['customer'], 'Customer not recorded')
        self.assertEqual(customers['']['known_amount'], '40.00')
        self.assertEqual(customers['']['missing_count'], 1)
        self.assertEqual(customers['']['count'], 2)
        self.assertEqual(data['kpis']['unpaid']['known_amount'], '240.00')
        rows = {row['invoice_number']: row for row in data['priority_invoices']}
        self.assertEqual(rows['BLANK-ACCOUNT']['customer'], 'Acme')
        self.assertEqual(rows['BLANK-ACCOUNT']['account'], '')
        self.assertEqual(rows['CONFLICTING-ACCOUNT']['account'], ' Shared ledger ')
        self.assertEqual(rows['MISSING-COMPANY']['customer'], 'Customer not recorded')
        self.assertEqual(self.report(company='Acme')['kpis']['unpaid']['amount'], '125.00')

    def test_payables_retain_team_visibility_and_fail_independently(self):
        from django.contrib.auth import get_user_model
        from apps.rbac.models import UserProfile

        self.grant('finance_overview', 'finance_outgoing', 'finance_incoming')
        outsider = get_user_model().objects.create_user('outside-receivables', email='outside@example.test')
        profile, _ = UserProfile.objects.get_or_create(user=outsider, defaults={'organization': self.profile.organization})
        profile.roles.clear()
        self.ar('AR', '25')
        self.ap('VISIBLE', total='50', paid='5', due=TODAY)
        self.ap('INVISIBLE', total='999', submitted_by=outsider)
        self.ap('REJECTED', total='100', procurement_status='rejected')
        data = self.report()
        self.assertEqual(data['sources']['payables']['open_count'], 1)
        self.assertEqual(data['ageing'][0]['payables']['amount'], '45.00')
        with patch('apps.finance.services.receivables_dashboard._payable_queryset', side_effect=RuntimeError('unavailable test source')):
            failed = self.report()
        self.assertEqual(failed['sources']['payables']['status'], 'error')
        self.assertIsNone(failed['ageing'][0]['payables']['amount'])
        self.assertEqual(failed['kpis']['unpaid']['amount'], '25.00')

    def test_invalid_filters_and_future_reference_dates_are_rejected(self):
        self.grant('finance_overview', 'finance_outgoing')
        with patch('django.utils.timezone.now', return_value=datetime(2026, 9, 21, 9, tzinfo=dt_timezone.utc)):
            for params in [{'months': 7}, {'months': 'bad'}, {'as_of': 'bad'}, {'as_of': '2026-09-22'}, {'as_of': '0001-01-01'}, {'currency': 'AED USD'}]:
                self.assertEqual(self.client.get(URL, params).status_code, 400, params)

    def test_endpoint_is_read_only_noncacheable_and_uses_source_timestamp(self):
        self.grant('finance_overview', 'finance_outgoing', 'finance_incoming')
        invoice = self.ar('READ-ONLY', '25')
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        mutations = [row['sql'] for row in queries if row['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE '))]
        self.assertEqual(mutations, [])
        self.assertEqual(response.data['source_updated_at'], invoice.updated_at.isoformat())
        self.assertEqual(self.client.post(URL, {}).status_code, 403)
