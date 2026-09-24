"""Receipt read API: authority, queue parity and honest evidence coverage."""
from datetime import date, datetime, timezone as dt_timezone
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import include, path
from rest_framework.test import APIClient

from apps.core.project_models import Project as CoreProject
from apps.procurement.models import Project, PurchaseOrder, Receipt, Vendor
from apps.rbac.models import (Module, Organization, Permission, Role, RoleModule, RolePermission,
                              UserPermissionOverride, UserProfile, UserRole)
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints
from .approval_fixtures import set_position


urlpatterns = [path('api/v1/procurement/', include('apps.procurement.urls'))]
secure_module_endpoints(urlpatterns)
BASE = '/api/v1/procurement/receipts/'
SUMMARY = BASE + 'inspection-summary/'


@override_settings(ROOT_URLCONF=__name__, TIME_ZONE='Asia/Dubai', RADAI_BUSINESS_APPROVAL_ROUTES={
    f'procurement_receipts.Receipt.{operation}': {'positions': ['engineer'], 'pending_states': ['pending']}
    for operation in ('accept', 'reject_delivery')
})
class ReceiptInspectionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user('receipt-reader', email='receipt-reader@example.test')
        org, _ = Organization.objects.get_or_create(code='receipt-tests', defaults={'name': 'Receipt tests'})
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'organization': org})
        self.profile.roles.clear()
        set_position(self.user)
        self.role = Role.objects.create(code='receipt-reader', name='Receipt reader', level=3)
        UserRole.objects.create(user_profile=self.profile, role=self.role)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.clock = patch('django.utils.timezone.now', return_value=datetime(2026, 9, 14, 9, tzinfo=dt_timezone.utc))
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.vendor = Vendor.objects.create(vendor_code='TEST-VENDOR', name='Supplier Example')

    def grant(self, action='read', code='procurement_receipts'):
        module, _ = Module.objects.get_or_create(code=code, defaults={'name': code})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        RoleModule.objects.get_or_create(role=self.role, module=module)
        for permission in module.permissions.filter(action=action, is_active=True):
            RolePermission.objects.get_or_create(role=self.role, permission=permission)

    def receipt(self, number, *, po=None, po_values=None, **values):
        if po is None:
            data = {'vendor': self.vendor, 'title': 'Test order', 'category': 'piping_materials',
                    'total_amount': '100', 'po_number': 'PO-' + number}
            data.update(po_values or {})
            po = PurchaseOrder.objects.create(**data)
        item = Receipt.objects.create(receipt_number=number, purchase_order=po, **values)
        # Keep register fixtures on a fixed business date for period/filter tests.
        Receipt.objects.filter(pk=item.pk).update(receipt_date=date(2026, 9, 14))
        item.receipt_date = date(2026, 9, 14)
        return item

    def get(self, params=None, *, summary=True):
        response = self.client.get(SUMMARY if summary else BASE, params or {})
        self.assertEqual(response.status_code, 200, getattr(response, 'data', None))
        return response.data

    def test_auth_module_and_explicit_read_deny_are_enforced(self):
        self.assertEqual(self.client.get(SUMMARY).status_code, 403)
        self.grant(code='procurement_orders')
        self.assertEqual(self.client.get(SUMMARY).status_code, 403)
        self.grant()
        self.assertEqual(self.client.get(SUMMARY).status_code, 200)
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        permission = Permission.objects.filter(module__code='procurement_receipts', action='read').first()
        UserPermissionOverride.objects.create(user_profile=self.profile, permission=permission, allowed=False)
        self.assertEqual(self.client.get(SUMMARY).status_code, 403)
        self.assertEqual(self.client.get(BASE).status_code, 403)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(SUMMARY).status_code, [401, 403])

    def test_capabilities_match_actual_actions_and_do_not_require_po_read(self):
        self.grant()
        item = self.receipt('PENDING')
        self.assertFalse(any(self.get()['capabilities'].values()))
        self.assertEqual(self.client.post(BASE + str(item.pk) + '/accept/').status_code, 403)
        for action in ('create', 'approve', 'update', 'export'):
            self.grant(action)
        caps = self.get()['capabilities']
        self.assertTrue(all(caps[key] for key in ('create', 'approve', 'update', 'export')))
        self.assertFalse(caps['read_purchase_orders'])
        self.assertEqual(self.get(summary=False)['results'][0]['capabilities'],
                         {'accept': True, 'reject': True, 'update': True, 'export': True, 'delete': False})
        self.grant(code='procurement_orders')
        self.assertTrue(self.get()['capabilities']['read_purchase_orders'])
        permission = Permission.objects.filter(module__code='procurement_receipts', action='approve').first()
        UserPermissionOverride.objects.create(user_profile=self.profile, permission=permission, allowed=False)
        self.assertFalse(self.get()['capabilities']['approve'])
        self.assertFalse(self.get(summary=False)['results'][0]['capabilities']['accept'])

    def test_pending_default_flags_do_not_imply_accepted_and_denominator_is_explicit(self):
        self.grant()
        self.receipt('PENDING')
        data = self.get()
        self.assertEqual(data['counts']['pending'], 1)
        self.assertEqual(data['counts']['exceptions'], 0)
        self.assertIsNone(data['kpis']['acceptance_rate']['value'])
        self.assertIsNone(data['kpis']['missing_certificates']['value'])
        self.receipt('ACCEPTED', status='accepted')
        self.receipt('PARTIAL', status='partial')
        self.receipt('REJECTED', status='rejected')
        rate = self.get()['kpis']['acceptance_rate']
        self.assertEqual((rate['value'], rate['numerator'], rate['denominator']), (33.3, 1, 3))

    def test_all_queue_counts_match_paginated_list_and_ignore_selected_queue(self):
        self.grant()
        self.receipt('PENDING', po_values={'required_certifications': ['MTC'], 'heat_numbers_required': True,
                                        'ndt_requirements': '100% UT'})
        self.receipt('ACCEPTED', status='accepted')
        self.receipt('REJECTED', status='rejected')
        self.receipt('FALSE-DIMENSION', dimensional_check_passed=False)
        baseline = self.get()
        self.assertEqual(baseline['counts']['exceptions'], 2)
        for queue, count in baseline['counts'].items():
            with self.subTest(queue=queue):
                summary = self.get({'queue': queue})
                register = self.get({'queue': queue, 'page_size': 1}, summary=False)
                self.assertEqual(summary['counts'], baseline['counts'])
                self.assertEqual(summary['filtered_count'], count)
                self.assertEqual(register['count'], count)

    def test_quality_filters_distinguish_pending_defaults_recorded_pass_and_any_failure(self):
        self.grant()
        self.receipt('PENDING-DEFAULT')
        self.receipt('PENDING-ISSUE', dimensional_check_passed=False)
        self.receipt('ACCEPTED-PASS', status='accepted', quality_check_passed=True)
        self.receipt('ACCEPTED-ISSUE', status='accepted', visual_inspection_passed=False)
        self.receipt('REJECTED-DEFAULT', status='rejected')
        self.receipt('PARTIAL', status='partial')
        for quality, expected in [('pending', {'PENDING-DEFAULT'}), ('passed', {'ACCEPTED-PASS'}),
                                  ('failed', {'PENDING-ISSUE', 'ACCEPTED-ISSUE', 'REJECTED-DEFAULT'})]:
            params = {'quality_check': quality}
            register = self.get(params, summary=False)
            self.assertEqual({row['receipt_number'] for row in register['results']}, expected)
            self.assertEqual(self.get(params)['counts']['all'], len(expected))
        intersection = {'quality_check': 'failed', 'status': 'pending'}
        self.assertEqual(self.get(intersection)['counts']['all'], 1)
        self.assertEqual(self.get(intersection, summary=False)['results'][0]['receipt_number'], 'PENDING-ISSUE')

    def test_certificate_names_compare_exactly_and_missing_or_malformed_requirements_stay_unknown(self):
        self.grant()
        self.receipt('MATCH', po_values={'required_certifications': [' mtc ', 'COC', 'MTC']},
                     certificates_received=['mtc', 'extra'])
        self.receipt('COMPLETE', po_values={'required_certifications': ['MTC']}, certificates_received=['mtc'])
        self.receipt('NO-REQUIREMENTS', certificates_received=['MTC'])
        self.receipt('BAD-REQUIREMENTS', po_values={'required_certifications': {'MTC': True}})
        self.receipt('BAD-RECEIVED', po_values={'required_certifications': ['MTC']}, certificates_received=[{'name': 'MTC'}])
        summary = self.get()
        metric = summary['kpis']['missing_certificates']
        self.assertEqual((metric['status'], metric['value'], metric['assessed_count'], metric['unassessed_count']),
                         ('partial', 1, 2, 3))
        evidence = self.get({'search': 'MATCH'}, summary=False)['results'][0]['evidence']['certificates']
        self.assertEqual(evidence['missing'], ['COC'])
        self.assertEqual((evidence['required_count'], evidence['received_count'], evidence['matched_count']), (2, 2, 1))

    def test_true_zero_missing_declarations_and_empty_source_differ(self):
        self.grant()
        empty = self.get()
        self.assertEqual(empty['kpis']['open_inspections']['value'], 0)
        self.assertIsNone(empty['kpis']['missing_certificates']['value'])
        self.assertIsNone(empty['kpis']['acceptance_rate']['value'])
        self.receipt('COMPLETE', po_values={'required_certifications': ['MTC']}, certificates_received=['MTC'])
        metric = self.get()['kpis']['missing_certificates']
        self.assertEqual((metric['status'], metric['value']), ('available', 0))

    def test_ndt_only_explicit_requirements_and_performance_not_pass(self):
        self.grant()
        for index, text in enumerate(['UT required', 'required', '100% UT', 'not required', 'as applicable', '',
                                      'UT or RT if required by client']):
            self.receipt('NDT-' + str(index), po_values={'ndt_requirements': text})
        self.receipt('NDT-RECORDED', po_values={'ndt_requirements': 'UT required'}, ndt_performed=True,
                     ndt_results='Review findings before acceptance')
        self.assertEqual(self.get()['counts']['ndt_pending'], 3)
        evidence = self.get({'search': 'NDT-RECORDED'}, summary=False)['results'][0]['evidence']['ndt']
        self.assertEqual(evidence['status'], 'recorded')
        self.assertTrue(evidence['results_recorded'])

    def test_traceability_compares_required_records_without_claiming_verified_items(self):
        self.grant()
        self.receipt('HEAT-MISSING', po_values={'heat_numbers_required': True})
        self.receipt('HEAT-RECORDED', po_values={'heat_numbers_required': True}, heat_numbers=[' ABC '])
        self.receipt('HEAT-INVALID', po_values={'heat_numbers_required': True}, heat_numbers='ABC')
        self.receipt('HEAT-NOT-REQUIRED')
        data = self.get()
        self.assertEqual(data['counts']['traceability_gaps'], 1)
        metric = data['kpis']['traceability_coverage']
        self.assertEqual((metric['status'], metric['value'], metric['numerator'], metric['denominator'],
                          metric['unassessed_count']), ('partial', 33.3, 1, 3, 1))

    def test_filters_intersect_and_date_order_validation_prevents_silent_wrong_scope(self):
        self.grant()
        project = CoreProject.objects.create(code='SITE-1', name='Plant')
        self.receipt('MATCH', inspector_name='Recorded Inspector', po_values={'enterprise_project': project})
        self.receipt('OTHER', inspector_name='Other Inspector')
        params = {'vendor': str(self.vendor.pk), 'project': f'core:{project.pk}', 'inspector': 'Recorded Inspector',
                  'received_from': '2026-09-14', 'received_to': '2026-09-14', 'search': 'Plant'}
        self.assertEqual(self.get(params)['counts']['all'], 1)
        self.assertEqual(self.get(params, summary=False)['count'], 1)
        for invalid in ({'received_from': '2026-02-30'}, {'received_from': '2026-09-20', 'received_to': '2026-09-10'},
                        {'queue': 'unknown'}, {'vendor': 'invalid'}, {'project': 'invalid'}, {'ordering': 'bank_details'}):
            self.assertEqual(self.client.get(SUMMARY, invalid).status_code, 400)
            self.assertEqual(self.client.get(BASE, invalid).status_code, 400)

    def test_canonical_project_wins_over_legacy_and_metadata_is_narrow(self):
        self.grant()
        core = CoreProject.objects.create(code='CANONICAL', name='Canonical plant')
        legacy = Project.objects.create(project_number='LEGACY', project_name='Legacy plant')
        self.receipt('PROJECT', po_values={'enterprise_project': core, 'project': legacy, 'seller_reference': 'PRIVATE'})
        row = self.get(summary=False)['results'][0]
        self.assertEqual((row['project_id'], row['project_number']), (f'core:{core.pk}', 'CANONICAL'))
        self.assertEqual(row['vendor_name'], 'Supplier Example')
        self.assertEqual(self.get({'project': f'procurement:{legacy.pk}'})['counts']['all'], 0)
        for key in ('total_amount', 'seller_reference', 'bank_details', 'ncr_number', 'inspection_due_date'):
            self.assertNotIn(key, row)

    def test_small_server_pages_and_stable_tie_breaker(self):
        self.grant()
        first = self.receipt('PAGE-0')
        for index in range(1, 13):
            self.receipt(f'PAGE-{index}', po=first.purchase_order)
        one = self.get({'page_size': 6, 'ordering': 'receipt_date'}, summary=False)
        two = self.get({'page_size': 6, 'ordering': 'receipt_date', 'page': 2}, summary=False)
        self.assertEqual((one['count'], len(one['results']), len(two['results'])), (13, 6, 6))
        self.assertFalse({row['id'] for row in one['results']} & {row['id'] for row in two['results']})

    def test_explicit_page_size_and_facet_caps_disclose_remaining_rows(self):
        self.grant()
        first = self.receipt('CAP-0', inspector_name='Inspector 000')
        Receipt.objects.bulk_create([
            Receipt(receipt_number=f'CAP-{index}', purchase_order=first.purchase_order,
                    inspector_name=f'Inspector {index:03d}') for index in range(1, 205)
        ])
        page = self.get({'page_size': 1000}, summary=False)
        self.assertEqual((page['count'], len(page['results'])), (205, 200))
        self.assertIsNotNone(page['next'])
        facets = self.get()['filter_options']
        self.assertTrue(facets['truncated']['inspectors'])
        self.assertEqual((len(facets['inspectors']), facets['total_options']['inspectors']), (200, 205))

    def test_month_kpi_uses_recorded_receipt_dates_and_current_local_day(self):
        self.grant()
        for number, day in [('OLD', date(2026, 8, 31)), ('FIRST', date(2026, 9, 1)),
                            ('TODAY', date(2026, 9, 14)), ('FUTURE', date(2026, 9, 15))]:
            item = self.receipt(number)
            Receipt.objects.filter(pk=item.pk).update(receipt_date=day)
        data = self.get()
        self.assertEqual(data['kpis']['receipts_this_month']['value'], 2)
        with patch('django.utils.timezone.now', return_value=datetime(2026, 9, 14, 20, 30, tzinfo=dt_timezone.utc)):
            data = self.get()
        self.assertEqual(data['as_of_date'], '2026-09-15')
        self.assertEqual(data['kpis']['receipts_this_month']['value'], 3)

    def test_shared_visibility_gets_write_nothing_and_return_record_update_timestamp(self):
        self.grant()
        other = get_user_model().objects.create_user('other-receiver')
        self.receipt('OTHER-RECEIVER', received_by=other)
        with CaptureQueriesContext(connection) as captured:
            summary = self.get()
            register = self.get(summary=False)
        self.assertEqual((summary['counts']['all'], register['count']), (1, 1))
        self.assertIsNotNone(summary['source_updated_at'])
        self.assertEqual(self.client.get(SUMMARY)['Cache-Control'], 'private, no-store')
        writes = [query['sql'] for query in captured if query['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE '))]
        self.assertEqual(writes, [])

    def test_existing_accept_reject_contract_and_pending_only_decision_capability(self):
        self.grant()
        self.grant('approve')
        accepted = self.receipt('ACCEPT', po_values={'status': 'sent', 'items': [{'quantity': '1', 'unit': 'EA'}], 'approval_log': [{
            'stage': 'Recorded approval', 'approver': 'Historical approver', 'status': 'Approved',
        }]}, items_received=[{'line_number': 1, 'received_qty': '1'}])
        response = self.client.post(BASE + str(accepted.pk) + '/accept/', {'expected_updated_at': accepted.updated_at.isoformat()}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'accepted')
        self.assertFalse(response.data['capabilities']['accept'])
        accepted.purchase_order.refresh_from_db()
        self.assertEqual(accepted.purchase_order.status, 'sent')
        self.assertIsNone(accepted.purchase_order.actual_delivery)
        rejected = self.receipt('REJECT')
        response = self.client.post(BASE + str(rejected.pk) + '/reject_delivery/', {'reason': 'Recorded damage', 'expected_updated_at': rejected.updated_at.isoformat()}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['inspection_notes'], 'Recorded damage')
        self.assertFalse(response.data['capabilities']['reject'])

    def test_missing_route_or_wrong_official_position_blocks_even_superuser(self):
        self.grant()
        self.grant('approve')
        item = self.receipt('POSITION-GATES')
        url = BASE + str(item.pk) + '/accept/'
        with override_settings(RADAI_BUSINESS_APPROVAL_ROUTES={}):
            self.assertEqual(self.client.post(url).status_code, 403)
            self.assertFalse(self.get(summary=False)['results'][0]['capabilities']['accept'])
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        set_position(self.user, 'CEO')
        self.assertEqual(self.client.post(url).status_code, 403)
        item.refresh_from_db()
        self.assertEqual(item.status, 'pending')

    def test_create_only_can_record_pending_but_cannot_set_inspection_dispositions(self):
        self.grant()
        self.grant('create')
        self.grant(code='procurement_orders')
        order = self.receipt('ORDER-SOURCE', po_values={'status': 'sent', 'items': [{'quantity': '10', 'unit': 'EA'}],
                             'approval_log': [{'stage': 'Recorded approval', 'approver': 'Historical approver', 'status': 'Approved'}]},
                             items_received=[{'line_number': 1, 'received_qty': '1'}]).purchase_order
        for disposition in ['accepted', 'rejected', 'partial']:
            with self.subTest(status=disposition):
                response = self.client.post(BASE, {'purchase_order': str(order.pk), 'status': disposition}, format='json')
                self.assertEqual(response.status_code, 403)
        response = self.client.post(BASE, {'purchase_order': str(order.pk), 'operation_key': str(uuid4()),
                                         'expected_po_updated_at': order.updated_at.isoformat(),
                                         'items_received': [{'line_id': 'line:1', 'received_qty': '2'}]}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['status'], 'pending')
        self.assertEqual(Receipt.objects.count(), 2)

    def test_update_only_cannot_change_status_but_can_edit_notes_and_unchanged_status(self):
        self.grant()
        self.grant('update')
        item = self.receipt('PENDING-EDIT')
        url = BASE + str(item.pk) + '/'
        for disposition in ['accepted', 'rejected', 'partial']:
            self.assertEqual(self.client.patch(url, {'status': disposition}, format='json').status_code, 403)
        response = self.client.patch(url, {'status': 'pending', 'notes': 'Recorded delivery note', 'expected_updated_at': item.updated_at.isoformat()}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['notes'], 'Recorded delivery note')
        accepted = self.receipt('ACCEPTED-EDIT', status='accepted')
        accepted_url = BASE + str(accepted.pk) + '/'
        self.assertEqual(self.client.patch(accepted_url, {'status': 'pending'}, format='json').status_code, 403)
        response = self.client.patch(accepted_url, {'status': 'accepted', 'notes': 'Clarification'}, format='json')
        self.assertEqual(response.status_code, 400)
        rejected = self.receipt('REJECTED-EDIT', status='rejected')
        self.assertEqual(self.client.patch(BASE + str(rejected.pk) + '/', {'notes': 'Clarification'}, format='json').status_code, 400)

    def test_approve_grant_cannot_bypass_named_disposition_actions(self):
        self.grant()
        for action in ['create', 'update', 'approve']:
            self.grant(action)
        order = self.receipt('ORDER-SOURCE').purchase_order
        for disposition in ['accepted', 'rejected', 'partial']:
            response = self.client.post(BASE, {'purchase_order': str(order.pk), 'status': disposition}, format='json')
            self.assertEqual(response.status_code, 403, response.data)
        pending = self.receipt('CHANGE-SOURCE', po=order)
        response = self.client.patch(BASE + str(pending.pk) + '/', {'status': 'accepted'}, format='json')
        self.assertEqual(response.status_code, 403)
        order.refresh_from_db()
        self.assertEqual(order.status, 'draft')
        self.assertIsNone(order.actual_delivery)

    def test_explicit_approve_deny_still_blocks_superuser_generic_disposition_writes(self):
        self.grant()
        self.grant('approve')
        item = self.receipt('DENIED-APPROVAL')
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        permission = Permission.objects.filter(module__code='procurement_receipts', action='approve').first()
        UserPermissionOverride.objects.create(user_profile=self.profile, permission=permission, allowed=False)
        for disposition in ['accepted', 'rejected', 'partial']:
            self.assertEqual(self.client.post(BASE, {'purchase_order': str(item.purchase_order_id), 'status': disposition}, format='json').status_code, 403)
            self.assertEqual(self.client.patch(BASE + str(item.pk) + '/', {'status': disposition}, format='json').status_code, 403)
