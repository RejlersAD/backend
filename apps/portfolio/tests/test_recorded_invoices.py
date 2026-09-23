"""Exact, authorized connections to current outgoing invoice records."""
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import DatabaseError, connection
from django.db.models.expressions import Col
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from apps.core.project_models import Project, ProjectMember
from apps.invoice_tracker.models import CustomerInvoice
from apps.invoice_tracker import tests_collections as collection_helpers
from apps.portfolio.recorded_invoices import build_recorded_invoices
from apps.rbac.models import Permission, UserPermissionOverride
from . import test_executive as executive_fixtures


class RecordedInvoiceTests(TestCase):
    grant = collection_helpers.CollectionsTests.grant

    def setUp(self):
        collection_helpers.CollectionsTests.setUp(self)
        self.rows = [self.row('P', 'P-1'), self.row('P', 'P-2')]

    @staticmethod
    def row(project, subproject):
        return {'project_code': project, 'subproject_code': subproject, 'title': 'Synthetic workbook item'}

    def invoice(self, number, project='P-1', **changes):
        values = {
            'invoice_number': number, 'rad_project_no': project, 'invoice_amount': Decimal('100'),
            'invoice_amount_aed': Decimal('100'), 'currency': 'AED', 'payment_status': 'pending',
            'actual_payment_received': Decimal('20'), 'invoice_date': date(2026, 9, 1),
        }
        values.update(changes)
        invoice = CustomerInvoice(**values)
        invoice.save(_skip_recompute=True)
        return invoice

    def report(self, **options):
        return build_recorded_invoices(self.user, options.pop('rows', self.rows),
                                       full_source=options.pop('full_source', True), **options)

    @staticmethod
    def currency(report, currency='AED'):
        return next(group for group in report['totals_by_currency'] if group['currency'] == currency)

    @contextmanager
    def legacy_invoice_table(self):
        """A private copy reproduces physical duplicates without changing application tables."""
        original = CustomerInvoice._meta.db_table
        copied = 'portfolio_invoice_test_' + uuid4().hex
        quote = connection.ops.quote_name
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE TABLE {quote(copied)} AS SELECT * FROM {quote(original)}')
        for field in CustomerInvoice._meta.concrete_fields:
            _ = field.cached_col
        patches = [patch.object(CustomerInvoice._meta, 'db_table', copied)] + [
            patch.object(field, 'cached_col', Col(copied, field)) for field in CustomerInvoice._meta.concrete_fields
        ]
        try:
            for replacement in patches:
                replacement.start()
            yield quote(copied)
        finally:
            for replacement in reversed(patches):
                replacement.stop()
            with connection.cursor() as cursor:
                cursor.execute(f'DROP TABLE {quote(copied)}')

    def test_finance_read_is_independent_and_denials_do_not_leak_invoice_data(self):
        self.invoice('PRIVATE')
        self.grant('project_control')
        self.grant('finance_overview')
        with CaptureQueriesContext(connection) as queries:
            report = self.report()
        self.assertEqual(report['status'], 'restricted')
        self.assertIsNone(report['coverage'])
        self.assertIsNone(report['total_rows'])
        self.assertFalse(any('invoice_tracker_customerinvoice' in row['sql'] for row in queries))
        self.grant()
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        permission = Permission.objects.get(module__code='finance_outgoing', action='read')
        UserPermissionOverride.objects.create(user_profile=self.profile, permission=permission, allowed=False)
        self.assertEqual(self.report()['status'], 'restricted')

    def test_exact_subproject_and_parent_links_count_each_invoice_once(self):
        self.grant()
        exact = self.invoice('INV-EXACT', ' p-1 ')
        parent = self.invoice('INV-PARENT', 'p')
        self.invoice('PREFIX', 'P-10')
        self.invoice('TEXT-ONLY', 'UNRELATED', project_id='P-2', project_name='P-2')
        report = self.report(rows=[*self.rows, self.rows[0]])
        self.assertEqual(report['total_rows'], 2)
        self.assertEqual(self.currency(report)['invoice_amount']['value'], '200.00')
        self.assertEqual(report['coverage']['source_identity_count'], 2)
        self.assertEqual(report['coverage']['matched_source_identity_count'], 1)
        self.assertEqual(report['coverage']['parent_only_project_count'], 1)
        by_number = {row['invoice_number']: row for row in report['rows']}
        self.assertEqual(by_number['INV-EXACT']['detail_route'], f'/finance/outgoing-invoices/{exact.pk}')
        self.assertEqual(by_number['INV-PARENT']['detail_route'], f'/finance/outgoing-invoices/{parent.pk}')
        self.assertIsNone(by_number['INV-PARENT']['subproject_code'])
        group = next(group for group in report['project_groups'] if group['match_level'] == 'parent')
        self.assertEqual(group['invoice_count'], 1)
        self.assertEqual(group['register_route'], '/finance/outgoing-invoices?queue=all&project_exact=P')

    def test_limited_child_access_never_authorizes_unregistered_or_inaccessible_parent(self):
        self.grant()
        other = get_user_model().objects.create_user('parent-owner', email='parent@example.test')
        parent = Project.objects.create(code='P', name='Parent', owner=other)
        Project.objects.create(code='P-1', name='Visible child', owner=self.user)
        self.invoice('CHILD', 'P-1')
        self.invoice('PARENT', 'P')
        self.invoice('UNREGISTERED', 'U')
        rows = [self.rows[0], self.row('U', 'U-1')]
        report = self.report(rows=rows, full_source=False)
        self.assertEqual([row['invoice_number'] for row in report['rows']], ['CHILD'])
        self.assertEqual(report['coverage']['withheld_parent_project_count'], 2)
        ProjectMember.objects.create(project=parent, user=self.user, role='viewer')
        report = self.report(rows=rows, full_source=False)
        self.assertEqual({row['invoice_number'] for row in report['rows']}, {'CHILD', 'PARENT'})
        parent.is_deleted = True
        parent.save(update_fields=['is_deleted'])
        self.assertEqual([row['invoice_number'] for row in self.report(rows=rows, full_source=False)['rows']], ['CHILD'])

    def test_shared_and_normalization_colliding_project_keys_require_review(self):
        self.grant()
        self.invoice('SHARED', 'DUP')
        rows = [self.row('P', 'DUP'), self.row('Q', 'DUP')]
        report = self.report(rows=rows)
        self.assertIn('ambiguous_project_identity', report['rows'][0]['conflict_codes'])
        self.assertIsNone(report['rows'][0]['detail_route'])
        self.assertIsNone(self.currency(report)['invoice_amount']['value'])
        self.assertEqual(report['project_groups'], [])
        self.assertEqual(self.report(rows=rows, full_source=False)['total_rows'], 0)
        report = self.report(rows=[self.row('P', 'DUP'), self.row('p', 'dup')])
        self.assertEqual(report['coverage']['conflicting_invoice_count'], 1)

    def test_normalized_invoice_number_and_project_pair_must_be_unique(self):
        self.grant()
        self.invoice(' inv-duplicate ', 'P-1')
        self.invoice('INV-DUPLICATE', ' p-1 ')
        self.invoice('SAFE', 'P-2')
        report = self.report()
        self.assertEqual(report['coverage']['conflicting_invoice_count'], 2)
        self.assertEqual(self.currency(report)['invoice_amount']['value'], '100.00')
        conflicts = [row for row in report['rows'] if row['conflict_codes']]
        self.assertTrue(all('duplicate_invoice_project' in row['conflict_codes'] for row in conflicts))
        self.assertTrue(all(row['detail_route'] is None for row in conflicts))

    def test_duplicate_id_on_another_project_blocks_detail_and_financial_totals(self):
        self.grant()
        selected = self.invoice('VISIBLE', 'P-1')
        hidden = self.invoice('OUTSIDE-PORTFOLIO', 'HIDDEN')
        with self.legacy_invoice_table() as table:
            with connection.cursor() as cursor:
                cursor.execute(f'UPDATE {table} SET id=%s WHERE id=%s', [selected.pk, hidden.pk])
            report = self.report()
        self.assertEqual(report['total_rows'], 1)
        self.assertEqual(report['rows'][0]['conflict_codes'], ['duplicate_invoice_id'])
        self.assertIsNone(report['rows'][0]['id'])
        self.assertIsNone(report['rows'][0]['detail_route'])
        self.assertEqual(self.currency(report)['included_invoice_count'], 0)
        self.assertIsNone(self.currency(report)['invoice_amount']['value'])

    def test_same_invoice_number_on_different_projects_is_not_collapsed(self):
        self.grant()
        self.invoice('FIRST', 'P-1')
        second = self.invoice('SECOND', 'P-2')
        with self.legacy_invoice_table() as table:
            with connection.cursor() as cursor:
                cursor.execute(f'UPDATE {table} SET invoice_number=%s WHERE id=%s', ['FIRST', second.pk])
            report = self.report()
        self.assertEqual(report['total_rows'], 2)
        self.assertEqual(report['coverage']['conflicting_invoice_count'], 0)
        self.assertEqual(self.currency(report)['invoice_amount']['value'], '200.00')

    def test_currency_null_and_zero_preserve_recorded_financial_basis(self):
        self.grant()
        self.invoice('ZERO', invoice_amount=Decimal('0'), invoice_amount_aed=Decimal('0'), actual_payment_received=None)
        self.invoice('MISSING', invoice_amount=None, invoice_amount_aed=None, actual_payment_received=None,
                     grand_total=Decimal('999'), balance_to_be_received=Decimal('999'))
        self.invoice('FOREIGN', currency='USD', invoice_amount=Decimal('20'), invoice_amount_aed=None,
                     actual_payment_received=Decimal('5'))
        self.invoice('UNKNOWN-CURRENCY', currency='', invoice_amount=Decimal('7'), actual_payment_received=None)
        report = self.report()
        rows = {row['invoice_number']: row for row in report['rows']}
        self.assertEqual(rows['ZERO']['calculated_receivable_balance'], '0.00')
        self.assertIsNone(rows['ZERO']['actual_payment_received'])
        self.assertIsNone(rows['MISSING']['calculated_receivable_balance'])
        aed = self.currency(report)
        self.assertIsNone(aed['invoice_amount']['value'])
        self.assertEqual(aed['invoice_amount']['known_value'], '0.00')
        self.assertEqual(aed['invoice_amount']['missing_count'], 1)
        self.assertEqual(aed['actual_payment_received']['value'], '0.00')
        usd = self.currency(report, 'USD')
        self.assertEqual(usd['calculated_receivable_balance']['value'], '15.00')
        self.assertIsNone(usd['recorded_aed_invoice_amount']['value'])
        self.assertIsNone(self.currency(report, 'UNSPECIFIED')['invoice_amount']['value'])
        self.assertIsNone(self.currency(report, 'UNSPECIFIED')['invoice_amount']['known_value'])
        self.assertFalse(report['source']['currency_conversion_applied'])

    def test_cancelled_credit_and_missing_invoice_identity_are_excluded_from_totals(self):
        self.grant()
        self.invoice('OPEN')
        self.invoice('CANCELLED', payment_status='cancelled')
        self.invoice('CREDIT', payment_status='credit_note', category='internal', invoice_amount=Decimal('-100'))
        self.invoice('   ')
        report = self.report()
        self.assertEqual(report['total_rows'], 4)
        self.assertEqual(report['coverage']['excluded_invoice_count'], 2)
        self.assertEqual(report['coverage']['conflicting_invoice_count'], 1)
        self.assertEqual(self.currency(report)['invoice_amount']['value'], '100.00')

    def test_pagination_does_not_change_totals_and_get_never_saves_or_recomputes(self):
        self.grant()
        for index in range(3):
            self.invoice(f'CURRENT-{index}', invoice_date=None if index == 0 else date(2027, 1, 1))
        before = list(CustomerInvoice.objects.order_by('pk').values())
        with patch.object(CustomerInvoice, 'save', side_effect=AssertionError('Read must not save')), \
                CaptureQueriesContext(connection) as queries:
            first = self.report(limit=1)
            second = self.report(limit=1, offset=1)
        self.assertEqual(first['total_rows'], 3)
        self.assertTrue(first['truncated'])
        self.assertEqual(first['totals_by_currency'], second['totals_by_currency'])
        self.assertNotEqual(first['rows'][0]['invoice_number'], second['rows'][0]['invoice_number'])
        self.assertEqual(first['source']['date_basis'], 'current_recorded_values')
        self.assertFalse(any(row['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for row in queries))
        self.assertEqual(before, list(CustomerInvoice.objects.order_by('pk').values()))

    def test_database_failure_is_explicit_and_empty_authorized_scope_is_not_global(self):
        self.grant()
        self.invoice('PRIVATE')
        self.assertEqual(self.report(rows=[])['total_rows'], 0)
        with patch('apps.portfolio.recorded_invoices.CustomerInvoice.objects.annotate', side_effect=DatabaseError('test')):
            report = self.report()
        self.assertEqual(report['status'], 'error')
        self.assertIsNone(report['coverage'])
        self.assertIsNone(report['total_rows'])


@override_settings(ROOT_URLCONF='apps.invoice_tracker.tests_collections')
class RecordedInvoiceProjectFilterTests(TestCase):
    setUp = collection_helpers.CollectionsTests.setUp
    grant = collection_helpers.CollectionsTests.grant
    invoice = collection_helpers.CollectionsTests.invoice

    def test_exact_project_route_preserves_other_filters_and_collection_scope(self):
        self.grant()
        self.invoice('MATCH', rad_project_no=' p-1 ', company='Target')
        self.invoice('OTHER-COMPANY', rad_project_no='P-1', company='Other')
        self.invoice('PREFIX', rad_project_no='P-10', company='Target')
        self.invoice('TEXT-ONLY', rad_project_no='OTHER', project_id='P-1', company='Target')
        params = {'queue': 'all', 'project_exact': 'P-1', 'company': 'Target'}
        response = self.client.get(collection_helpers.BASE, params)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row['invoice_number'] for row in response.data['results']], ['MATCH'])
        summary = self.client.get(collection_helpers.SUMMARY, params)
        self.assertEqual(summary.status_code, 200, summary.data)
        self.assertEqual(summary.data['counts']['all'], 1)
        self.assertEqual(self.client.get(collection_helpers.BASE, {**params, 'search': 'PREFIX'}).data['count'], 0)

    def test_exact_project_filter_does_not_grant_access_and_rejects_invalid_values(self):
        self.assertEqual(self.client.get(collection_helpers.BASE, {'project_exact': 'P-1'}).status_code, 403)
        self.grant()
        for value in ('   ', 'P' * 129):
            with self.subTest(value=value):
                self.assertEqual(self.client.get(collection_helpers.BASE, {'project_exact': value}).status_code, 400)


class RecordedInvoiceAPITests(TestCase):
    setUp = executive_fixtures.RevenueDashboardTests.setUp
    grant = executive_fixtures.RevenueDashboardTests.grant
    full_access = executive_fixtures.RevenueDashboardTests.full_access
    source = executive_fixtures.RevenueDashboardTests.source
    row = executive_fixtures.RevenueDashboardTests.row
    invoice = RecordedInvoiceTests.invoice
    legacy_invoice_table = RecordedInvoiceTests.legacy_invoice_table
    url = '/api/v1/dashboard/executive/portfolio-workbook/outgoing-invoices/'

    def authorize(self):
        from rest_framework.test import APIClient

        self.full_access()
        self.grant('finance_outgoing', 'read')
        client = APIClient()
        client.force_authenticate(self.user)
        return client

    def test_real_api_returns_exact_invoice_identity_current_dates_and_decimal_values(self):
        client = self.authorize()
        snapshot = self.source()
        self.row(snapshot)
        invoice = self.invoice('API-EXACT', ' p-1 ', currency='USD', invoice_amount=Decimal('12.50'),
                               actual_payment_received=Decimal('2.25'), invoice_amount_aed=None,
                               due_date=date(2026, 10, 5), payment_date=date(2026, 9, 21))
        self.invoice('OTHER-PROJECT', 'P-10')
        response = client.get(self.url, {'snapshot_id': snapshot.pk, 'limit': 1})
        self.assertEqual(response.status_code, 200, response.data)
        report = response.json()
        self.assertEqual(report['source_snapshot_id'], snapshot.pk)
        self.assertEqual(report['total_rows'], 1)
        self.assertEqual(report['coverage']['matched_source_identity_count'], 1)
        record = report['rows'][0]
        self.assertEqual(record['id'], str(invoice.pk))
        self.assertEqual(record['invoice_amount'], '12.50')
        self.assertEqual(record['actual_payment_received'], '2.25')
        self.assertEqual(record['calculated_receivable_balance'], '10.25')
        self.assertIsNone(record['invoice_amount_aed'])
        self.assertEqual(record['due_date'], '2026-10-05')
        self.assertEqual(record['payment_date'], '2026-09-21')
        self.assertEqual(record['detail_route'], f'/finance/outgoing-invoices/{invoice.pk}')
        self.assertEqual(report['totals_by_currency'][0]['currency'], 'USD')
        self.assertEqual(response['Cache-Control'], 'private, no-store')

    def test_real_api_handles_legacy_table_without_primary_key_and_duplicate_ids(self):
        client = self.authorize()
        self.row(self.source())
        invoice = self.invoice('VISIBLE')
        hidden = self.invoice('OUTSIDE', 'OUTSIDE')
        with self.legacy_invoice_table() as table:
            with connection.cursor() as cursor:
                cursor.execute(f'UPDATE {table} SET id=%s WHERE id=%s', [invoice.pk, hidden.pk])
            response = client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        report = response.json()
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['total_rows'], 1)
        self.assertEqual(report['rows'][0]['conflict_codes'], ['duplicate_invoice_id'])
        self.assertIsNone(report['rows'][0]['detail_route'])
        self.assertIsNone(report['totals_by_currency'][0]['invoice_amount']['value'])

    def test_source_identity_collision_outside_filter_cannot_become_a_safe_invoice_link(self):
        client = self.authorize()
        snapshot = self.source()
        self.row(snapshot, 'SHARED', pm='Selected PM')
        self.row(snapshot, 'SHARED', project_code='OTHER', pm='Outside filter')
        self.invoice('CANNOT-ALLOCATE', 'SHARED')
        response = client.get(self.url, {'pm': 'Selected PM'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['coverage']['source_identity_count'], 1)
        self.assertEqual(response.data['coverage']['conflicting_invoice_count'], 1)
        self.assertEqual(response.data['rows'][0]['conflict_codes'], ['ambiguous_project_identity'])
        self.assertIsNone(response.data['rows'][0]['project_code'])
        self.assertIsNone(response.data['rows'][0]['detail_route'])
        self.assertIsNone(response.data['totals_by_currency'][0]['invoice_amount']['value'])

        # Ordinary readers must receive neither the competing identity nor
        # invoices that cannot be safely attributed to their visible row.
        self.user.is_staff = False
        self.user.save(update_fields=['is_staff'])
        Project.objects.create(code='SHARED', name='Visible registered subproject', owner=self.user)
        response = client.get(self.url, {'pm': 'Selected PM'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['coverage']['source_identity_count'], 1)
        self.assertEqual(response.data['total_rows'], 0)
        self.assertEqual(response.data['rows'], [])
        self.assertEqual(response.data['project_groups'], [])
