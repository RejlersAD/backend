"""Collection filter scope, permission boundaries and financial data coverage."""
from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import include, path
from rest_framework.test import APIClient

from apps.invoice_tracker.models import CustomerInvoice
from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, RolePermission, UserPermissionOverride, UserProfile, UserRole
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints


urlpatterns = [path('api/v1/invoice-tracker/', include('apps.invoice_tracker.urls'))]
secure_module_endpoints(urlpatterns)
BASE = '/api/v1/invoice-tracker/invoices/'
SUMMARY = BASE + 'collections-summary/'
TODAY = date(2026, 9, 14)


@override_settings(ROOT_URLCONF=__name__, TIME_ZONE='Asia/Dubai')
class CollectionsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user('collections-reader', email='collections@example.test')
        org, _ = Organization.objects.get_or_create(code='collections-tests', defaults={'name': 'Collections tests'})
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'organization': org})
        self.profile.roles.clear()
        self.role = Role.objects.create(code='collections-reader', name='Collections reader', level=3)
        UserRole.objects.create(user_profile=self.profile, role=self.role)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.clock = patch('django.utils.timezone.now', return_value=datetime(2026, 9, 14, 9, tzinfo=dt_timezone.utc))
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def grant(self, code='finance_outgoing'):
        module, _ = Module.objects.get_or_create(code=code, defaults={'name': code})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        RoleModule.objects.get_or_create(role=self.role, module=module)
        for permission in module.permissions.filter(action='read', is_active=True):
            RolePermission.objects.get_or_create(role=self.role, permission=permission)

    def invoice(self, number, balance='100', **values):
        defaults = {'invoice_number': number, 'currency': 'AED', 'due_date': TODAY - timedelta(days=1),
                    'invoice_date': TODAY, 'grand_total': Decimal('100'), 'payment_status': 'pending',
                    'invoice_amount': (Decimal(balance) + Decimal(values.get('actual_payment_received') or 0)
                                       if balance is not None else None),
                    'balance_to_be_received': Decimal(balance) if balance is not None else None}
        defaults.update(values)
        item = CustomerInvoice(**defaults)
        item.save(_skip_recompute=True)
        return item

    def get(self, params=None, *, summary=True):
        response = self.client.get(SUMMARY if summary else BASE, params or {})
        self.assertEqual(response.status_code, 200, getattr(response, 'data', None))
        return response.data

    def test_authentication_and_outgoing_read_required_without_overview_grant(self):
        self.assertEqual(self.client.get(SUMMARY).status_code, 403)
        self.grant('finance_overview')
        self.assertEqual(self.client.get(SUMMARY).status_code, 403)
        self.grant()
        self.assertEqual(self.client.get(SUMMARY).status_code, 200)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(SUMMARY).status_code, [401, 403])

    def test_explicit_deny_wins_over_superuser_and_read_does_not_grant_writes(self):
        self.grant()
        self.assertEqual(self.client.post(BASE, {'invoice_number': 'NOT-CREATED'}, format='json').status_code, 403)
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        permission = Permission.objects.filter(module__code='finance_outgoing', action='read').first()
        UserPermissionOverride.objects.create(user_profile=self.profile, permission=permission, allowed=False)
        self.assertEqual(self.client.get(SUMMARY).status_code, 403)
        self.assertEqual(self.client.get(BASE).status_code, 403)

    def test_shared_register_visibility_matches_existing_list(self):
        self.grant()
        outsider = get_user_model().objects.create_user('other-recorder', email='other@example.test')
        self.invoice('OWN', created_by=self.user)
        self.invoice('OTHER', created_by=outsider)
        self.invoice('UNLINKED', created_by=None)
        self.assertEqual(self.get()['counts']['all'], 3)
        self.assertEqual(self.get(summary=False)['count'], 3)

    def test_summary_counts_ignore_queue_but_list_honors_queue_and_search(self):
        self.grant()
        self.invoice('MATCH-OVERDUE', account='Target')
        self.invoice('MATCH-PAID', '0', account='Target', payment_status='paid')
        self.invoice('OTHER', account='Elsewhere')
        params = {'search': 'MATCH', 'queue': 'overdue', 'page_size': 1}
        data = self.get(params)
        register = self.get(params, summary=False)
        self.assertEqual(data['counts']['all'], 2)
        self.assertEqual(data['counts']['paid'], 1)
        self.assertEqual(data['counts']['overdue'], 1)
        self.assertEqual(data['filtered_count'], register['count'])
        self.assertEqual(register['results'][0]['invoice_number'], 'MATCH-OVERDUE')

    def test_week_queue_ends_sunday_including_today_and_not_following_monday(self):
        self.grant()
        for day in [-1, 0, 6, 7]:
            self.invoice('WEEK' + str(day), due_date=TODAY + timedelta(days=day))
        self.invoice('UNKNOWN-DUE', due_date=None)
        data = self.get({'queue': 'due_soon'})
        self.assertEqual(data['filtered_count'], 2)
        self.assertEqual(data['due_soon_through'], '2026-09-20')
        numbers = {row['invoice_number'] for row in self.get({'queue': 'due_soon'}, summary=False)['results']}
        self.assertEqual(numbers, {'WEEK0', 'WEEK6'})

    def test_local_midnight_changes_week_without_utc_date_fallback(self):
        self.grant()
        self.invoice('MONDAY', due_date=date(2026, 9, 21))
        with patch('django.utils.timezone.now', return_value=datetime(2026, 9, 20, 20, 30, tzinfo=dt_timezone.utc)):
            data = self.get({'queue': 'due_soon'})
        self.assertEqual(data['as_of_date'], '2026-09-21')
        self.assertEqual(data['due_soon_through'], '2026-09-27')
        self.assertEqual(data['filtered_count'], 1)

    def test_partial_and_settled_use_recorded_values_with_clear_exclusions(self):
        self.grant()
        self.invoice('PARTIAL-STATUS', payment_status='partial')
        self.invoice('PARTIAL-PAYMENT', actual_payment_received=Decimal('10'))
        self.invoice('PARTIAL-UNKNOWN', None, payment_status='partial')
        self.invoice('ZERO-KNOWN', '0', invoice_amount=Decimal('100'), actual_payment_received=Decimal('100'))
        self.invoice('PAID-STATUS', None, payment_status='paid')
        self.invoice('ZERO-TOTAL', '0', grand_total=Decimal('0'))
        self.invoice('CANCELLED', '0', payment_status='cancelled')
        self.invoice('CREDIT', '100', payment_status='credit_note', actual_payment_received=Decimal('10'))
        data = self.get()
        self.assertEqual(data['counts']['partial'], 3)
        self.assertEqual(data['counts']['paid'], 2)
        self.assertEqual(data['counts']['open'], 3)
        self.assertEqual(data['collection_health']['partial_count'], 3)
        self.assertEqual(data['collection_health']['open_count'], 3)

    def test_company_pm_currency_dates_and_existing_category_filters_intersect(self):
        self.grant()
        self.invoice('TARGET', company='Entity A', pm='PM one', category='internal', currency='usd')
        self.invoice('COMPANY', company='Entity A branch', pm='PM one', category='internal', currency='USD')
        self.invoice('PM', company='Entity A', pm='PM two', category='internal', currency='USD')
        self.invoice('DATE', company='Entity A', pm='PM one', category='internal', currency='USD', due_date=TODAY)
        filters = {'company': 'Entity A', 'pm': 'PM one', 'category': 'internal', 'currency': 'USD',
                   'date_from': '2026-09-14', 'date_to': '2026-09-14', 'due_to': '2026-09-13'}
        self.assertEqual(self.get(filters)['counts']['all'], 1)
        self.assertEqual(self.get(filters, summary=False)['results'][0]['invoice_number'], 'TARGET')

    def test_age_filters_use_actual_due_dates_not_stale_cached_days(self):
        self.grant()
        for age in [0, 1, 30, 31, 60, 61, 90, 91]:
            self.invoice('AGE' + str(age), due_date=TODAY - timedelta(days=age), days_overdue=999)
        self.invoice('NO-DATE', due_date=None, days_overdue=999)
        expected = {'current': 1, 'days_1_30': 2, 'days_31_60': 2, 'days_61_90': 2, 'over90': 1, 'unknown_due_date': 1}
        for age, count in expected.items():
            with self.subTest(age=age):
                self.assertEqual(self.get({'ageing': age}, summary=False)['count'], count)
                self.assertEqual(self.get({'ageing': age})['counts']['all'], count)

    def test_dashboard_company_drilldown_matches_trimmed_import_values_exactly(self):
        self.grant()
        padded = self.invoice('PADDED-COMPANY', company='  Entity A  ', pm='PM one')
        self.invoice('NORMAL-COMPANY', company='Entity A', pm='PM one')
        self.invoice('DIFFERENT-COMPANY', company='Entity A branch', pm='PM one')
        self.invoice('DIFFERENT-PM', company='Entity A', pm=' PM one ')
        for company in ['Entity A', '  Entity A  ']:
            with self.subTest(company=company):
                filters = {'company': company, 'pm': 'PM one', 'queue': 'open'}
                self.assertEqual(self.get(filters)['filtered_count'], 2)
                self.assertEqual({row['invoice_number'] for row in self.get(filters, summary=False)['results']},
                                 {'PADDED-COMPANY', 'NORMAL-COMPANY'})
        padded.refresh_from_db()
        self.assertEqual(padded.company, '  Entity A  ')

    def test_invalid_filters_are_400_and_do_not_silently_broaden_scope(self):
        self.grant()
        for params in [{'queue': 'disputed'}, {'ageing': 'unsupported'}, {'date_from': 'bad'},
                       {'due_to': '2026-02-30'}, {'due_from': '2026-09-14', 'due_to': '2026-09-13'},
                       {'date_from': '2026-9-1'}]:
            with self.subTest(params=params):
                self.assertEqual(self.client.get(SUMMARY, params).status_code, 400)
                self.assertEqual(self.client.get(BASE, params).status_code, 400)

    def test_currency_totals_withhold_missing_balances_and_unknown_units(self):
        self.grant()
        self.invoice('AED-KNOWN', '10', currency='AED')
        self.invoice('AED-MISSING', None, currency='aed')
        self.invoice('USD', '20', currency='USD', actual_payment_received=Decimal('5'))
        self.invoice('UNKNOWN', '7', currency='')
        self.invoice('SETTLED-EUR', '0', currency='EUR', payment_status='paid')
        data = self.get()
        rows = {row['currency']: row for row in data['collection_health']['by_currency']}
        self.assertEqual(set(data['currencies']), {'AED', 'USD', 'UNSPECIFIED', 'EUR'})
        self.assertNotIn('EUR', rows)
        self.assertIsNone(rows['AED']['outstanding'])
        self.assertIsNone(rows['UNSPECIFIED']['outstanding'])
        self.assertEqual(rows['USD']['outstanding'], '20.00')
        self.assertEqual(rows['USD']['partial_count'], 1)
        self.assertEqual(rows['USD']['overdue_count'], 1)
        self.assertFalse(data['currency_conversion_applied'])
        self.assertEqual(self.get({'currency': 'UNSPECIFIED'}, summary=False)['count'], 1)

    def test_empty_success_has_zero_counts_but_no_invented_collection_measures(self):
        self.grant()
        data = self.get()
        self.assertTrue(all(value == 0 for value in data['counts'].values()))
        self.assertEqual(data['collection_health']['status'], 'available')
        self.assertEqual(data['collection_health']['by_currency'], [])
        self.assertIsNone(data['collection_health']['balance_coverage']['percentage'])
        self.assertIsNone(data['source_updated_at'])
        self.assertTrue(all(item['status'] == 'unavailable' and item['value'] is None for item in data['unavailable_metrics']))

    def test_pagination_counts_cover_all_rows_and_sort_has_deterministic_tie(self):
        self.grant()
        records = [self.invoice('PAGE' + str(index), '10') for index in range(5)]
        params = {'ordering': 'balance_to_be_received', 'page_size': 2}
        first = self.get(params, summary=False)
        second = self.get({**params, 'page': 2}, summary=False)
        self.assertEqual(first['count'], 5)
        self.assertIsNotNone(first['next'])
        self.assertEqual([row['id'] for row in first['results']], [records[4].id, records[3].id])
        self.assertEqual([row['id'] for row in second['results']], [records[2].id, records[1].id])
        self.assertEqual(self.get(params)['counts']['all'], 5)
        ordered = self.get({'ordering': 'invoice_number'}, summary=False)
        self.assertEqual(ordered['results'][0]['invoice_number'], 'PAGE0')

    def test_filter_facets_are_complete_scope_capped_and_not_collection_assignees(self):
        self.grant()
        CustomerInvoice.objects.bulk_create([
            CustomerInvoice(invoice_number='FACET' + str(index), company='Entity %03d' % index,
                            pm='PM %03d' % index, balance_to_be_received=Decimal('10')) for index in range(201)
        ])
        data = self.get({'company': 'Entity 000'})
        options = data['filter_options']
        self.assertEqual(data['counts']['all'], 1)
        self.assertEqual(options['companies_count'], 201)
        self.assertEqual(options['project_managers_count'], 201)
        self.assertEqual(len(options['companies']), 200)
        self.assertTrue(options['truncated']['companies'])
        self.assertTrue(options['truncated']['project_managers'])

    def test_summary_and_list_do_not_recompute_write_or_replace_detail_fields(self):
        self.grant()
        item = self.invoice('READ-ONLY', '25', days_overdue=999, remarks='Recorded note')
        with CaptureQueriesContext(connection) as queries:
            summary = self.client.get(SUMMARY)
            register = self.client.get(BASE)
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(register.status_code, 200)
        mutations = [row['sql'] for row in queries if row['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE '))]
        self.assertEqual(mutations, [])
        self.assertEqual(summary['Cache-Control'], 'private, no-store')
        item.refresh_from_db()
        self.assertEqual(item.days_overdue, 999)
        self.assertEqual(register.data['results'][0]['remarks'], 'Recorded note')
        self.assertIn('attachments', register.data['results'][0])
        self.assertNotIn('READ-ONLY', str(summary.data))
        self.assertEqual(self.client.get(BASE + str(item.pk) + '/').status_code, 200)

    def test_source_timestamp_tracks_filtered_scope_not_retrieval_or_queue(self):
        self.grant()
        old = self.invoice('FILTER-OLD', '0', payment_status='paid')
        new = self.invoice('FILTER-NEW')
        self.invoice('OTHER')
        CustomerInvoice.objects.filter(pk=old.pk).update(updated_at=datetime(2026, 8, 1, tzinfo=dt_timezone.utc))
        CustomerInvoice.objects.filter(pk=new.pk).update(updated_at=datetime(2026, 9, 1, tzinfo=dt_timezone.utc))
        data = self.get({'search': 'FILTER', 'queue': 'paid'})
        self.assertTrue(data['source_updated_at'].startswith('2026-09-01'))
        self.assertEqual(data['source_timestamp_kind'], 'record_updated_at')
        self.assertEqual(data['filtered_count'], 1)

    def test_existing_create_workflow_preserves_entered_tax_inclusive_total(self):
        self.grant()
        for permission in Permission.objects.filter(module__code='finance_outgoing', action='create', is_active=True):
            RolePermission.objects.get_or_create(role=self.role, permission=permission)
        response = self.client.post(BASE, {
            'invoice_number': 'GROSS-AMOUNT-CHECK', 'category': 'external', 'currency': 'AED',
            'invoice_amount': '105.00', 'grand_total': '105.00', 'actual_payment_received': '0.00',
            'calculated_receivable_balance': '9999.00',
            'payment_status': 'pending', 'invoice_date': '2026-09-14', 'due_date': '2026-10-14',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Decimal(response.data['invoice_amount']), Decimal('105.00'))
        self.assertEqual(Decimal(response.data['grand_total']), Decimal('105.00'))
        self.assertEqual(Decimal(response.data['balance_to_be_received']), Decimal('105.00'))
        self.assertEqual(response.data['calculated_receivable_balance'], '105.00')
        self.assertEqual(Decimal(response.data['amount_excl_vat']), Decimal('100.00'))

    def test_read_only_capabilities_do_not_offer_create_import_or_export(self):
        self.grant()
        self.assertEqual(self.get()['capabilities'], {'create': False, 'import': False, 'export': False})
        self.assertEqual(self.client.post(BASE + 'import-excel/', {}, format='json').status_code, 403)

    def test_create_and_import_capabilities_follow_existing_create_policy_not_update(self):
        self.grant()
        for permission in Permission.objects.filter(module__code='finance_outgoing', action='update', is_active=True):
            RolePermission.objects.get_or_create(role=self.role, permission=permission)
        self.assertFalse(self.get()['capabilities']['import'])
        for permission in Permission.objects.filter(module__code='finance_outgoing', action='create', is_active=True):
            RolePermission.objects.get_or_create(role=self.role, permission=permission)
        self.assertEqual(self.get()['capabilities'], {'create': True, 'import': True, 'export': False})
        # Permission passes to the unchanged import handler; no file is supplied,
        # so it returns validation error without importing or writing records.
        response = self.client.post(BASE + 'import-excel/', {}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['error'], "No 'file' provided")

    def test_formula_drives_queues_drilldown_and_summary_while_preserving_stored_balance(self):
        self.grant()
        known = self.invoice('REDUCED', invoice_amount=Decimal('100'), actual_payment_received=Decimal('80'),
                             balance_to_be_received=Decimal('900'), company='Target')
        self.invoice('STORED-ZERO', invoice_amount=Decimal('50'), actual_payment_received=Decimal('10'),
                     balance_to_be_received=Decimal('0'), company='Target')
        self.invoice('SETTLED', invoice_amount=Decimal('30'), actual_payment_received=Decimal('30'),
                     balance_to_be_received=Decimal('999'), company='Target')
        self.invoice('OVERPAID', invoice_amount=Decimal('20'), actual_payment_received=Decimal('25'),
                     balance_to_be_received=Decimal('999'), company='Target')
        self.invoice('UNKNOWN-L', invoice_amount=None, actual_payment_received=Decimal('1'),
                     balance_to_be_received=Decimal('60'), grand_total=Decimal('100'), currency='USD')
        self.invoice('BLANK-RECEIPT', invoice_amount=Decimal('10'), actual_payment_received=None,
                     balance_to_be_received=Decimal('700'), company='Other')
        params = {'company': 'Target', 'currency': 'AED', 'queue': 'overdue', 'ordering': 'balance_to_be_received'}
        with CaptureQueriesContext(connection) as queries:
            summary = self.get(params)
            register = self.get(params, summary=False)
        self.assertEqual(summary['counts']['open'], 2)
        self.assertEqual(summary['counts']['paid'], 1)
        self.assertEqual(summary['filtered_count'], register['count'])
        self.assertEqual(summary['collection_health']['by_currency'][0]['outstanding'], '60.00')
        self.assertEqual(summary['collection_health']['by_currency'][0]['overdue'], '60.00')
        self.assertEqual([row['invoice_number'] for row in register['results']], ['REDUCED', 'STORED-ZERO'])
        self.assertEqual([row['calculated_receivable_balance'] for row in register['results']], ['20.00', '40.00'])
        self.assertEqual(Decimal(register['results'][0]['balance_to_be_received']), Decimal('900'))
        reverse = self.get({**params, 'ordering': '-calculated_receivable_balance'}, summary=False)
        self.assertEqual([row['invoice_number'] for row in reverse['results']], ['STORED-ZERO', 'REDUCED'])
        all_rows = {row['invoice_number']: row for row in self.get(summary=False)['results']}
        self.assertIsNone(all_rows['UNKNOWN-L']['calculated_receivable_balance'])
        self.assertEqual(all_rows['BLANK-RECEIPT']['calculated_receivable_balance'], '10.00')
        self.assertEqual(all_rows['OVERPAID']['calculated_receivable_balance'], '-5.00')
        self.assertEqual(self.get({'currency': 'USD', 'queue': 'overdue'}, summary=False)['count'], 1)
        known.refresh_from_db()
        self.assertEqual(known.balance_to_be_received, Decimal('900'))
        self.assertEqual([q['sql'] for q in queries if q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))], [])

    def test_capabilities_keep_create_import_and_export_denies_even_for_superuser(self):
        self.grant()
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        self.assertEqual(self.get()['capabilities'], {'create': True, 'import': True, 'export': True})
        export = Permission.objects.filter(module__code='finance_outgoing', action='export', is_active=True).first()
        UserPermissionOverride.objects.create(user_profile=self.profile, permission=export, allowed=False)
        self.assertEqual(self.get()['capabilities'], {'create': True, 'import': True, 'export': False})
        create = Permission.objects.filter(module__code='finance_outgoing', action='create', is_active=True).first()
        UserPermissionOverride.objects.create(user_profile=self.profile, permission=create, allowed=False)
        self.assertEqual(self.get()['capabilities'], {'create': False, 'import': False, 'export': False})
        self.assertEqual(self.client.post(BASE + 'import-excel/', {}, format='json').status_code, 403)
