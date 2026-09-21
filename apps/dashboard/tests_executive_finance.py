"""Executive finance adapters preserve source grants and recorded-value semantics."""
from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from django.urls import path

from apps.finance.command_center_views import FinanceReceivablesDashboardView
from apps.finance.models import Invoice
from apps.finance.services.customer_invoice_register import build_customer_invoice_register
from apps.finance.services.receivables_dashboard import build_receivables_dashboard
from apps.invoice_tracker.models import CustomerInvoice
from apps.rbac.action_policy import module_action_allowed
from apps.rbac.models import (
    Module, Organization, Permission, Role, RoleModule, RolePermission,
    UserPermissionOverride, UserProfile, UserRole,
)
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints
from config.urls_executive_test import urlpatterns as executive_urlpatterns


RECEIVABLES_URL = '/api/v1/dashboard/executive/receivables/'
REGISTER_URL = '/api/v1/dashboard/executive/customer-invoices/'
TODAY = date(2026, 9, 21)
NOW = datetime(2026, 9, 21, 9, tzinfo=dt_timezone.utc)
FINANCE_RECEIVABLES_URL = '/api/v1/finance/dashboard/receivables/'
finance_urlpatterns = [path('api/v1/finance/dashboard/receivables/', FinanceReceivablesDashboardView.as_view())]
secure_module_endpoints(finance_urlpatterns)
urlpatterns = [*executive_urlpatterns, *finance_urlpatterns]


@override_settings(ROOT_URLCONF='config.urls_executive_test')
class ExecutiveFinanceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            'executive-finance-reader', email='executive-finance@example.test',
        )
        organization, _ = Organization.objects.get_or_create(
            code='executive-finance-tests', defaults={'name': 'Executive finance tests'},
        )
        self.profile, _ = UserProfile.objects.get_or_create(
            user=self.user, defaults={'organization': organization},
        )
        self.profile.roles.clear()
        self.role = Role.objects.create(
            code='executive-finance-reader', name='Executive finance reader', level=3,
        )
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
        permission = Permission.objects.get(module__code=code, action='read', is_active=True)
        UserPermissionOverride.objects.create(
            user_profile=self.profile, permission=permission, allowed=False,
        )

    def invoice(self, number, balance='100', **fields):
        amount = Decimal(balance) if balance is not None else None
        received = fields.get('actual_payment_received') or Decimal('0')
        invoice_amount = None if amount is None else amount + received
        item = CustomerInvoice(**{
            'invoice_number': number, 'company': 'Customer company', 'account': 'Account code',
            'invoice_date': TODAY, 'due_date': TODAY - timedelta(days=31),
            'currency': 'AED', 'payment_status': 'pending',
            'invoice_amount': invoice_amount, 'invoice_amount_aed': invoice_amount,
            'balance_to_be_received': amount, **fields,
        })
        item.save(_skip_recompute=True)
        return item

    def report(self, url=RECEIVABLES_URL, **params):
        with patch('django.utils.timezone.now', return_value=NOW):
            response = self.client.get(url, params)
        self.assertEqual(response.status_code, 200, getattr(response, 'data', None))
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        return response.data

    def test_executive_and_source_grants_work_without_finance_overview(self):
        self.grant('executive_dashboard', 'finance_outgoing')
        self.invoice('EXEC-ONLY', '25')
        self.assertFalse(module_action_allowed(self.user, 'finance_overview', 'read'))
        self.assertEqual(self.report()['kpis']['unpaid']['amount'], '25.00')
        self.assertEqual(self.report(REGISTER_URL)['pagination']['count'], 1)
        self.grant('finance_overview')
        self.deny('finance_overview')
        self.assertEqual(self.report()['sources']['receivables']['status'], 'available')
        self.assertEqual(self.report(REGISTER_URL)['status'], 'available')

    def test_finance_grants_do_not_replace_executive_read_or_authentication(self):
        self.grant('finance_overview', 'finance_outgoing', 'finance_incoming')
        for url in (RECEIVABLES_URL, REGISTER_URL):
            self.assertEqual(self.client.get(url).status_code, 403)
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        for url in (RECEIVABLES_URL, REGISTER_URL):
            self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_authenticate(None)
        for url in (RECEIVABLES_URL, REGISTER_URL):
            self.assertIn(self.client.get(url).status_code, (401, 403))

    def test_executive_only_cannot_query_sources_or_reveal_register_metadata(self):
        self.grant('executive_dashboard')
        self.invoice('SECRET', company='Private company', currency='EUR')
        with patch('apps.finance.services.receivables_dashboard._summary') as summary, \
                patch('apps.finance.services.receivables_dashboard._available_filters') as filters, \
                patch('apps.finance.services.customer_invoice_register._selected') as selected:
            dashboard = self.report()
            register = self.report(REGISTER_URL)
        summary.assert_not_called()
        filters.assert_not_called()
        selected.assert_not_called()
        self.assertEqual(dashboard['status'], 'restricted')
        self.assertEqual(dashboard['workbook_summary']['status'], 'restricted')
        self.assertEqual(set(dashboard['workbook_summary']), {'schema_version', 'status', 'reason'})
        self.assertEqual(dashboard['filters']['companies'], [])
        self.assertEqual(dashboard['filters']['currencies'], [])
        self.assertEqual(dashboard['customers'], [])
        self.assertEqual(dashboard['priority_invoices'], [])
        self.assertIsNone(dashboard['source_updated_at'])
        self.assertIsNone(dashboard['kpis']['unpaid']['known_amount'])
        for source in dashboard['sources'].values():
            self.assertIsNone(source['invoice_count'])
            self.assertIsNone(source['route'])
        self.assertEqual(register['status'], 'restricted')
        self.assertEqual(register['rows'], [])
        self.assertIsNone(register['pagination']['count'])
        self.assertIsNone(register['source']['route'])
        self.assertIsNone(register['totals']['amount']['known_amount'])
        self.assertNotIn('Private company', str((dashboard, register)))

    def test_explicit_source_and_executive_denies_apply_even_to_superusers(self):
        self.grant('executive_dashboard', 'finance_outgoing', 'finance_incoming')
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        self.deny('finance_outgoing')
        dashboard = self.report()
        self.assertEqual(dashboard['sources']['receivables']['status'], 'restricted')
        self.assertEqual(dashboard['workbook_summary']['status'], 'restricted')
        self.assertEqual(dashboard['sources']['payables']['status'], 'available')
        self.assertEqual(self.report(REGISTER_URL)['status'], 'restricted')
        self.deny('finance_incoming')
        self.assertEqual(self.report()['sources']['payables']['status'], 'restricted')
        self.deny('executive_dashboard')
        for url in (RECEIVABLES_URL, REGISTER_URL):
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_incoming_grant_preserves_supplier_visibility_and_does_not_grant_receivables(self):
        self.grant('executive_dashboard', 'finance_incoming')
        other = get_user_model().objects.create_user('other-invoice-owner')
        for number, owner, total in [('OWN', self.user, '25'), ('HIDDEN', other, '900')]:
            Invoice.objects.create(
                invoice_number=number, submitted_by=owner, total_amount=Decimal(total),
                paid_amount=0, currency='AED', due_date=TODAY,
            )
        data = self.report()
        self.assertEqual(data['sources']['receivables']['status'], 'restricted')
        self.assertEqual(data['sources']['payables']['invoice_count'], 1)
        current = next(row for row in data['ageing'] if row['id'] == 'current')
        self.assertEqual(current['payables']['amount'], '25.00')
        self.assertEqual(self.report(REGISTER_URL)['status'], 'restricted')

    def test_dashboard_filters_and_company_identity_match_finance_builder(self):
        self.grant('executive_dashboard', 'finance_outgoing', 'finance_incoming')
        self.invoice('COMPANY-ONE', '10', company=' Acme ', account='Wrong identity', currency='usd')
        self.invoice('COMPANY-TWO', '20', company='Acme', account='', currency='USD')
        self.invoice('OTHER', '999', company='Other customer', currency='USD')
        self.invoice('OTHER-CURRENCY', '888', company='Acme', currency='AED')
        with patch('django.utils.timezone.now', return_value=NOW):
            expected = build_receivables_dashboard(
                self.user, currency='USD', company='Acme', months=6, as_of=date(2026, 8, 1),
            )
            actual = self.report(currency='usd', company=' Acme ', months=6, as_of='2026-08-01')
        self.assertEqual(actual, expected)
        self.assertEqual(actual['workbook_summary']['invoice_count'], 4404)
        self.assertEqual(actual['workbook_summary']['totals']['invoice_amount_aed'], '466151390.16')
        self.assertEqual(actual['kpis']['unpaid']['amount'], '30.00')
        self.assertEqual(actual['kpis']['overdue']['amount'], '0.00')
        self.assertEqual(len(actual['customers']), 1)
        self.assertEqual(actual['customers'][0]['customer'], 'Acme')
        self.assertEqual(actual['customers'][0]['company'], 'Acme')
        self.assertEqual(actual['sources']['payables']['status'], 'unavailable')
        self.assertEqual(len(actual['overdue_by_month']), 6)
        self.assertIn('Invoice Amount (L) minus Actual Payment Received (AA)', actual['definitions']['balance_basis'])
        self.assertFalse(actual['currency_conversion_applied'])

    @override_settings(ROOT_URLCONF=__name__)
    def test_executive_and_finance_overdue_use_l_less_aa_with_stale_y(self):
        self.grant('executive_dashboard', 'finance_overview', 'finance_outgoing')
        self.invoice('PARTIAL', '9999', invoice_amount=Decimal('1000'),
                     actual_payment_received=Decimal('300'), company='Acme')
        self.invoice('BLANK-RECEIPT', '0', invoice_amount=Decimal('200'), company='Acme')
        self.invoice('UNKNOWN-INVOICE', '8888', invoice_amount=None,
                     grand_total=Decimal('8888'), company='Acme')
        self.invoice('SETTLED', '7777', invoice_amount=Decimal('50'),
                     actual_payment_received=Decimal('50'), company='Acme')
        self.invoice('OTHER-CURRENCY', '50', currency='USD', company='Acme')
        self.invoice('OTHER-COMPANY', '60', company='Other')

        executive = self.report(company='Acme')
        finance = self.report(FINANCE_RECEIVABLES_URL, company='Acme')
        self.assertEqual(executive, finance)
        self.assertEqual(executive['kpis']['overdue'], {
            'amount': None, 'known_amount': '900.00', 'count': 3,
            'missing_count': 1, 'partial': True,
        })
        self.assertEqual(executive['kpis']['over30']['known_amount'], '900.00')
        self.assertEqual(executive['kpis']['over90']['amount'], '0.00')
        self.assertEqual(executive['customers'][0]['known_amount'], '900.00')
        self.assertEqual({row['invoice_number']: row['balance'] for row in executive['priority_invoices']}, {
            'PARTIAL': '700.00', 'BLANK-RECEIPT': '200.00', 'UNKNOWN-INVOICE': None,
        })
        self.assertFalse(executive['currency_conversion_applied'])

    def test_register_pagination_sorting_and_totals_match_finance_builder(self):
        self.grant('executive_dashboard', 'finance_outgoing')
        records = [
            self.invoice(f'INV-{index}', str(index + 1), company=' Acme ', account=f'Code {index}')
            for index in range(9)
        ]
        self.invoice('OTHER', '999', company='Other company')
        params = {'currency': 'AED', 'company': 'Acme', 'page': 2, 'page_size': 8, 'ordering': 'company'}
        actual = self.report(REGISTER_URL, **params)
        self.assertEqual(actual, build_customer_invoice_register(self.user, **params))
        self.assertEqual(actual['pagination']['count'], 9)
        self.assertEqual(actual['pagination']['pages'], 2)
        self.assertEqual([row['id'] for row in actual['rows']], [records[-1].pk])
        self.assertEqual(actual['rows'][0]['customer'], 'Acme')
        self.assertEqual(actual['rows'][0]['account'], 'Code 8')
        self.assertEqual(actual['totals']['amount']['amount'], '45.00')
        self.assertEqual(self.client.get(REGISTER_URL, {**params, 'page': 3}).status_code, 404)

    def test_missing_balances_preserve_known_subtotals_in_both_endpoints(self):
        self.grant('executive_dashboard', 'finance_outgoing')
        self.invoice('KNOWN', '40')
        self.invoice('UNKNOWN', None)
        metric = self.report()['kpis']['unpaid']
        self.assertEqual(metric, {
            'amount': None, 'known_amount': '40.00', 'count': 2,
            'missing_count': 1, 'partial': True,
        })
        register = self.report(REGISTER_URL)
        self.assertEqual(register['status'], 'incomplete')
        self.assertEqual(register['totals']['amount'], {**metric, 'currency': 'AED'})
        self.assertEqual(register['totals']['amount_due_home'], {**metric, 'currency': 'AED'})

    def test_dashboard_source_failure_keeps_other_source_and_unknown_totals(self):
        self.grant('executive_dashboard', 'finance_outgoing', 'finance_incoming')
        with self.assertLogs('apps.finance.services.receivables_dashboard', level='ERROR'), \
                patch('apps.finance.services.receivables_dashboard._available_filters',
                      side_effect=RuntimeError('private database details')):
            data = self.report()
        self.assertEqual(data['sources']['receivables']['status'], 'error')
        self.assertEqual(data['sources']['payables']['status'], 'available')
        self.assertIsNone(data['kpis']['unpaid']['amount'])
        self.assertIsNone(data['kpis']['unpaid']['known_amount'])
        self.assertEqual(data['filters']['companies'], [])
        self.assertNotIn('private database details', str(data))

    def test_register_source_failure_returns_no_rows_or_invented_total(self):
        self.grant('executive_dashboard', 'finance_outgoing')
        with self.assertLogs('apps.finance.services.customer_invoice_register', level='ERROR'), \
                patch('apps.finance.services.customer_invoice_register._selected',
                      side_effect=RuntimeError('private database details')):
            data = self.report(REGISTER_URL)
        self.assertEqual(data['status'], 'error')
        self.assertEqual(data['rows'], [])
        self.assertIsNone(data['pagination']['count'])
        self.assertIsNone(data['totals']['amount']['known_amount'])
        self.assertNotIn('private database details', str(data))

    def test_shared_validators_reject_invalid_filters(self):
        self.grant('executive_dashboard', 'finance_outgoing')
        cases = [
            (RECEIVABLES_URL, {'currency': '$'}),
            (RECEIVABLES_URL, {'company': 'x' * 257}),
            (RECEIVABLES_URL, {'months': 5}),
            (RECEIVABLES_URL, {'as_of': 'invalid'}),
            (RECEIVABLES_URL, {'as_of': '2026-09-22'}),
            (RECEIVABLES_URL, {'as_of': '1901-01-01'}),
            (REGISTER_URL, {'currency': '$'}),
            (REGISTER_URL, {'company': 'x' * 257}),
            (REGISTER_URL, {'page': 0}),
            (REGISTER_URL, {'page_size': 100}),
            (REGISTER_URL, {'ordering': 'unknown_field'}),
        ]
        with patch('django.utils.timezone.now', return_value=NOW):
            for url, params in cases:
                with self.subTest(url=url, params=params):
                    response = self.client.get(url, params)
                    self.assertEqual(response.status_code, 400, response.data)

    def test_endpoints_reject_writes_and_read_requests_do_not_modify_invoices(self):
        self.grant('executive_dashboard', 'finance_outgoing')
        self.invoice('READ-ONLY', '25')
        for url in (RECEIVABLES_URL, REGISTER_URL):
            for method in ('post', 'put', 'patch', 'delete'):
                with self.subTest(url=url, method=method):
                    self.assertEqual(getattr(self.client, method)(url, {}).status_code, 405)
        with patch.object(CustomerInvoice, 'recompute_all') as recompute, \
                CaptureQueriesContext(connection) as queries:
            self.report()
            self.report(REGISTER_URL)
        recompute.assert_not_called()
        self.assertEqual([
            query['sql'] for query in queries
            if query['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))
        ], [])
