"""Finance overview access, balance integrity, dates and read-only guarantees."""
from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import path
from rest_framework.test import APIClient

from apps.finance.command_center_views import FinanceCommandCenterView
from apps.finance.models import Invoice
from apps.finance.services.command_center import _source_summary, build_command_center
from apps.invoice_tracker.models import CustomerInvoice
from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, RolePermission, UserPermissionOverride, UserProfile, UserRole
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints


URL = '/api/v1/finance/dashboard/command-center/'
urlpatterns = [path('api/v1/finance/dashboard/command-center/', FinanceCommandCenterView.as_view())]
secure_module_endpoints(urlpatterns)
TODAY = date(2026, 9, 14)


@override_settings(ROOT_URLCONF=__name__)
class FinanceCommandCenterTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user('finance-center-reader', email='reader@example.test')
        organization, _ = Organization.objects.get_or_create(code='finance-center-tests', defaults={'name': 'Finance test scope'})
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'organization': organization})
        self.profile.roles.clear()
        self.role = Role.objects.create(code='finance-center-role', name='Finance center reader', level=3)
        UserRole.objects.create(user_profile=self.profile, role=self.role)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def grant(self, *codes):
        for code in codes:
            module, _ = Module.objects.get_or_create(code=code, defaults={'name': code})
            ensure_module_actions(Module, Permission, module_ids=[module.pk])
            RoleModule.objects.get_or_create(role=self.role, module=module)
            for permission in module.permissions.filter(action='read', is_active=True):
                RolePermission.objects.get_or_create(role=self.role, permission=permission)

    def deny(self, code):
        permission = Permission.objects.filter(module__code=code, action='read', is_active=True).first()
        UserPermissionOverride.objects.create(user_profile=self.profile, permission=permission, allowed=False)

    def report(self):
        with patch('apps.finance.services.command_center.timezone.now', return_value=datetime(2026, 9, 14, 9, tzinfo=dt_timezone.utc)):
            response = self.client.get(URL)
        self.assertEqual(response.status_code, 200, getattr(response, 'data', None))
        return response.data

    def ar(self, number, balance='100', currency='AED', due=TODAY, status='pending'):
        item = CustomerInvoice(invoice_number=number, currency=currency, due_date=due,
                               payment_status=status,
                               invoice_amount=Decimal(balance) if balance is not None else None,
                               actual_payment_received=Decimal('0'),
                               balance_to_be_received=Decimal(balance) if balance is not None else None)
        item.save(_skip_recompute=True)
        return item

    def ap(self, number, total='100', paid='0', currency='AED', due=TODAY, **kwargs):
        return Invoice.objects.create(
            invoice_number=number, currency=currency, due_date=due,
            total_amount=Decimal(total) if total is not None else None, paid_amount=Decimal(paid),
            submitted_by=kwargs.pop('submitted_by', self.user),
            original_filename=number + '.pdf', file_path='invoices/' + number + '.pdf', **kwargs,
        )

    def test_authentication_overview_read_and_no_staff_bypass(self):
        self.assertEqual(self.client.get(URL).status_code, 403)
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        self.assertEqual(self.client.get(URL).status_code, 403)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(URL).status_code, [401, 403])

    def test_overview_alone_does_not_read_registers(self):
        self.grant('finance_overview')
        with patch('apps.finance.services.command_center._source_summary') as aggregate:
            data = self.report()
        aggregate.assert_not_called()
        self.assertEqual(data['status'], 'restricted')
        self.assertEqual(data['actions'], [])
        self.assertTrue(all(source['invoice_count'] is None and source['route'] is None for source in data['sources'].values()))

    def test_explicit_denies_win_over_superuser_for_sources_and_endpoint(self):
        self.grant('finance_overview', 'finance_incoming', 'finance_outgoing')
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        self.deny('finance_incoming')
        self.assertEqual(self.report()['sources']['payables']['status'], 'restricted')
        self.deny('finance_overview')
        self.assertEqual(self.client.get(URL).status_code, 403)

    def test_source_grant_alone_does_not_grant_overview(self):
        self.grant('finance_incoming', 'finance_outgoing')
        self.assertEqual(self.client.get(URL).status_code, 403)

    def test_payable_scope_preserves_existing_finance_team_visibility(self):
        self.grant('finance_overview', 'finance_incoming')
        outsider = get_user_model().objects.create_user('outside-finance', email='outside@example.test')
        outsider_profile, _ = UserProfile.objects.get_or_create(user=outsider, defaults={'organization': self.profile.organization})
        outsider_profile.roles.clear()
        self.ap('VISIBLE', total='25')
        self.ap('INVISIBLE', total='999', submitted_by=outsider)
        self.ap('UNOWNED', total='888', submitted_by=None)
        data = self.report()['sources']['payables']
        self.assertEqual(data['invoice_count'], 1)
        self.assertEqual(data['by_currency'][0]['outstanding'], '25.00')

    def test_currencies_are_normalized_without_fx_or_blank_fallback(self):
        self.grant('finance_overview', 'finance_incoming', 'finance_outgoing')
        self.ar('A1', '100', 'AED')
        self.ar('A2', '20', 'aed')
        self.ar('U1', '10', 'USD')
        self.ar('N1', '7', '')
        self.ap('AP', total='40', paid='10', currency='AED')
        data = self.report()
        rows = {row['currency']: row for row in data['sources']['receivables']['by_currency']}
        self.assertEqual(set(rows), {'AED', 'USD', 'UNSPECIFIED'})
        self.assertEqual(rows['AED']['outstanding'], '120.00')
        self.assertIsNone(rows['UNSPECIFIED']['outstanding'])
        self.assertEqual(rows['UNSPECIFIED']['missing_currency_count'], 1)
        net = {row['currency']: row['net_invoice_exposure'] for row in data['by_currency']}
        self.assertTrue(all(value is None for value in net.values()))
        self.assertFalse(data['currency_conversion_applied'])

    def test_net_exposure_uses_same_known_currency_and_empty_source_currency_is_zero(self):
        self.grant('finance_overview', 'finance_incoming', 'finance_outgoing')
        self.ar('AR-AED', '120', 'AED')
        self.ar('AR-USD', '10', 'USD')
        self.ap('AP-AED', total='40', paid='10', currency='AED')
        net = {row['currency']: row['net_invoice_exposure'] for row in self.report()['by_currency']}
        self.assertEqual(net, {'AED': '90.00', 'USD': '10.00'})

    def test_missing_balance_withholds_only_affected_currency_including_buckets(self):
        self.grant('finance_overview', 'finance_incoming', 'finance_outgoing')
        self.ar('KNOWN', '100', 'AED')
        self.ar('MISSING', None, 'aed', due=None)
        self.ar('USD', '40', 'USD')
        source = self.report()['sources']['receivables']
        rows = {row['currency']: row for row in source['by_currency']}
        self.assertEqual(source['status'], 'incomplete')
        self.assertIsNone(rows['AED']['outstanding'])
        self.assertIsNone(rows['AED']['overdue'])
        self.assertTrue(all(row['amount'] is None for row in rows['AED']['buckets']))
        self.assertEqual(rows['USD']['outstanding'], '40.00')
        self.assertEqual(source['balance_coverage']['known_count'], 2)
        self.assertEqual(source['balance_coverage']['total_count'], 3)
        self.assertEqual(source['unknown_due_date_count'], 1)

    def test_missing_payable_total_is_unknown_not_zero(self):
        self.grant('finance_overview', 'finance_incoming')
        self.ap('MISSING-TOTAL', total=None)
        data = self.report()['sources']['payables']
        self.assertEqual(data['missing_balance_count'], 1)
        self.assertIsNone(data['by_currency'][0]['outstanding'])
        self.assertEqual(data['balance_coverage']['percentage'], 0)

    def test_due_date_boundaries_and_unknown_dates_remain_separate(self):
        for age in [0, 1, 30, 31, 60, 61, 90, 91]:
            self.ar('D' + str(age), '10', due=TODAY - timedelta(days=age))
        self.ar('FUTURE30', '10', due=TODAY + timedelta(days=30))
        self.ar('FUTURE31', '10', due=TODAY + timedelta(days=31))
        self.ar('NO-DATE', '10', due=None)
        source = _source_summary('receivables', CustomerInvoice.objects.all(), TODAY)
        row = source['by_currency'][0]
        counts = {bucket['id']: bucket['count'] for bucket in row['buckets']}
        self.assertEqual(counts, {'current': 3, 'days_1_30': 2, 'days_31_60': 2, 'days_61_90': 2, 'over90': 1, 'unknown_due_date': 1})
        self.assertEqual(row['due_30d'], '20.00')
        self.assertEqual(row['overdue'], '70.00')
        self.assertEqual(source['oldest_due_date'], (TODAY - timedelta(days=91)).isoformat())
        self.assertEqual(source['over60_count'], 3)
        self.assertEqual(row['outstanding'], '110.00')

    def test_paid_cancelled_zero_and_credit_balances_do_not_become_open(self):
        self.grant('finance_overview', 'finance_outgoing', 'finance_incoming')
        self.ar('PAID', '100', status='paid')
        self.ar('CANCELLED', '100', status='cancelled')
        self.ar('ZERO', '0')
        self.ar('CREDIT', '-25')
        self.ap('OVERPAID', total='10', paid='20')
        data = self.report()
        self.assertEqual(data['sources']['receivables']['invoice_count'], 4)
        self.assertEqual(data['sources']['receivables']['open_count'], 0)
        self.assertEqual(data['sources']['receivables']['by_currency'], [])
        self.assertEqual(data['sources']['payables']['open_count'], 0)
        self.assertEqual(data['sources']['payables']['by_currency'], [])

    def test_positive_or_unknown_credit_notes_are_not_collectible_in_either_dashboard(self):
        from apps.invoice_tracker.services.collections import build_collections_summary

        self.grant('finance_overview', 'finance_outgoing')
        self.ar('COLLECTIBLE', '100', due=TODAY - timedelta(days=1))
        self.ar('POSITIVE-CREDIT-NOTE', '900', currency='USD', status='credit_note')
        self.ar('UNKNOWN-CREDIT-NOTE', None, due=TODAY - timedelta(days=100), status='credit_note')
        source = self.report()['sources']['receivables']
        self.assertEqual(source['invoice_count'], 3)
        self.assertEqual((source['status'], source['open_count'], source['overdue_count'],
                          source['missing_balance_count']), ('available', 1, 1, 0))
        self.assertEqual([(row['currency'], row['outstanding']) for row in source['by_currency']], [('AED', '100.00')])
        invoices = CustomerInvoice.objects.all()
        collections = build_collections_summary(
            invoices, full_source=invoices, generated_at=datetime(2026, 9, 14, 9, tzinfo=dt_timezone.utc),
        )
        self.assertEqual((collections['counts']['all'], collections['counts']['open']), (3, 1))
        self.assertEqual([(row['currency'], row['outstanding']) for row in collections['collection_health']['by_currency']],
                         [('AED', '100.00')])

    def test_process_denominator_and_queue_overlap_are_explicit(self):
        self.grant('finance_overview', 'finance_incoming')
        self.ap('REVIEW', procurement_status='finance_review', match_status='verified')
        self.ap('READY', procurement_status='approved_for_payment', match_status='verified')
        self.ap('HOLD', procurement_status='approved_for_payment', payment_status='on_hold')
        self.ap('READY-EXCEPTION', procurement_status='approved_for_payment', match_status='exception')
        self.ap('REJECTED', procurement_status='rejected', match_status='exception')
        self.ap('CLOSED', procurement_status='closed')
        self.ap('PAID', payment_status='paid')
        data = self.report()
        process = data['process']
        self.assertEqual(process['denominator'], 4)
        self.assertEqual(process['counts']['review'], 1)
        self.assertEqual(process['counts']['ready_for_payment'], 1)
        self.assertEqual(process['counts']['verified'], 2)
        self.assertEqual(process['counts']['exception'], 1)
        self.assertEqual(data['sources']['payables']['open_count'], 4)
        self.assertEqual(data['sources']['payables']['by_currency'][0]['outstanding'], '400.00')
        self.assertTrue(all(action['owner'] is None and action['due_date'] is None and not action['can_decide'] for action in data['actions']))

    def test_empty_sources_are_zero_but_rates_and_unconnected_financial_metrics_unknown(self):
        self.grant('finance_overview', 'finance_outgoing', 'finance_incoming')
        data = self.report()
        self.assertEqual(data['status'], 'available')
        self.assertEqual(data['currencies'], [])
        self.assertTrue(all(source['invoice_count'] == 0 for source in data['sources'].values()))
        self.assertTrue(all(source['balance_coverage']['percentage'] is None for source in data['sources'].values()))
        self.assertTrue(all(item['percentage'] is None for item in data['process']['metrics']))
        self.assertTrue(all(item['value'] is None for item in data['unavailable_metrics']))
        self.assertEqual(data['trends']['series'], [])

    def test_one_source_failure_preserves_other_without_fallback_values(self):
        self.grant('finance_overview', 'finance_outgoing', 'finance_incoming')
        self.ar('AR', '25')
        with patch('apps.finance.services.command_center._payable_queryset', side_effect=RuntimeError('test unavailable source')):
            data = self.report()
        self.assertEqual(data['status'], 'partial')
        self.assertEqual(data['sources']['receivables']['status'], 'available')
        self.assertEqual(data['sources']['payables']['status'], 'error')
        self.assertIsNone(data['sources']['payables']['open_count'])
        self.assertIsNone(data['by_currency'][0]['net_invoice_exposure'])
        self.assertEqual(data['process']['status'], 'error')

    def test_denied_source_does_not_turn_into_zero_net_exposure(self):
        self.grant('finance_overview', 'finance_outgoing')
        self.ar('AR', '25')
        data = self.report()
        self.assertIsNone(data['by_currency'][0]['net_invoice_exposure'])
        self.assertEqual(data['by_currency'][0]['status'], 'unavailable')
        self.assertIsNone(data['process']['counts'])

    def test_endpoint_is_read_only_noncacheable_and_does_not_return_invoice_identities(self):
        self.grant('finance_overview', 'finance_outgoing', 'finance_incoming')
        self.ar('PRIVATE-AR-IDENTIFIER', '25')
        self.ap('PRIVATE-AP-IDENTIFIER', total='10')
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        mutations = [row['sql'] for row in queries if row['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE '))]
        self.assertEqual(mutations, [])
        self.assertNotIn('PRIVATE-', str(response.data))
        self.assertEqual(self.client.post(URL, {}).status_code, 403)

    def test_as_of_uses_current_local_date_without_accepting_historical_query(self):
        self.grant('finance_overview', 'finance_outgoing')
        with patch('apps.finance.services.command_center.timezone.now', return_value=datetime(2026, 9, 13, 20, 30, tzinfo=dt_timezone.utc)), override_settings(TIME_ZONE='Asia/Dubai'):
            data = build_command_center(self.user)
        self.assertEqual(data['as_of_date'], '2026-09-14')

    def test_receivables_summary_uses_invoice_amount_less_receipts_and_ignores_stored_balance(self):
        self.grant('finance_overview', 'finance_outgoing')
        for number, amount, payment, currency in [
            ('REDUCED', '100', '70', 'AED'), ('NO-RECEIPT', '20', None, 'AED'),
            ('SETTLED', '50', '50', 'AED'), ('OVERPAID', '10', '20', 'AED'),
            ('UNKNOWN-L', None, '10', 'USD'),
        ]:
            item = CustomerInvoice(
                invoice_number=number, currency=currency, payment_status='pending',
                due_date=TODAY - timedelta(days=31),
                invoice_amount=Decimal(amount) if amount is not None else None,
                actual_payment_received=Decimal(payment) if payment is not None else None,
                balance_to_be_received=Decimal('999'), grand_total=Decimal('888'),
            )
            item.save(_skip_recompute=True)
        source = self.report()['sources']['receivables']
        rows = {row['currency']: row for row in source['by_currency']}
        self.assertEqual(source['open_count'], 3)
        self.assertEqual(source['overdue_count'], 3)
        self.assertEqual(rows['AED']['outstanding'], '50.00')
        self.assertEqual(rows['AED']['overdue'], '50.00')
        self.assertEqual(rows['AED']['invoice_count'], 2)
        self.assertEqual(rows['USD']['missing_balance_count'], 1)
        self.assertIsNone(rows['USD']['outstanding'])
