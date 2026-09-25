"""Guarded receiving handoff: real evidence, decimals, authority and retries."""
import base64
from copy import deepcopy
from datetime import date, datetime, timezone as datetime_timezone
from io import BytesIO
from uuid import uuid4
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import include, path
from django.utils import timezone
from rest_framework.test import APIClient
from PIL import Image

from apps.procurement.models import PurchaseOrder, PurchaseRequisition, Receipt, Vendor
from apps.procurement.services.receiving import receiving_summary
from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, RolePermission, UserProfile, UserRole, UserPermissionOverride
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints
from .approval_fixtures import set_position


urlpatterns = [path('api/v1/procurement/', include('apps.procurement.urls'))]
secure_module_endpoints(urlpatterns)
BASE = '/api/v1/procurement/'
ROUTES = {f'procurement_receipts.Receipt.{action}': {'positions': ['engineer'], 'pending_states': ['pending']}
          for action in ('accept', 'reject_delivery')}


@override_settings(ROOT_URLCONF=__name__, RADAI_BUSINESS_APPROVAL_ROUTES=ROUTES)
class ReceivingHandoffTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user('receiving-operator', email='receiver@example.test')
        org, _ = Organization.objects.get_or_create(code='receiving-tests', defaults={'name': 'Receiving tests'})
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'organization': org})
        self.profile.roles.clear()
        set_position(self.user)
        self.role = Role.objects.create(code='receiving-operator', name='Receiving operator', level=3)
        UserRole.objects.create(user_profile=self.profile, role=self.role)
        for module, actions in [('procurement_orders', ('read', 'update')), ('procurement_receipts', ('read', 'create', 'update', 'approve'))]:
            for action in actions:
                self.grant(module, action)
        self.vendor = Vendor.objects.create(vendor_code='RECEIVE-VENDOR', name='Receiving vendor')
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.patch_notify = patch('apps.procurement.serializers.notify_assigned_approvers')
        self.patch_notify.start()
        self.addCleanup(self.patch_notify.stop)

    def grant(self, code, action):
        module, _ = Module.objects.get_or_create(code=code, defaults={'name': code})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        RoleModule.objects.get_or_create(role=self.role, module=module)
        for permission in module.permissions.filter(action=action, is_active=True):
            RolePermission.objects.get_or_create(role=self.role, permission=permission)

    def deny(self, code, action):
        permission = Permission.objects.filter(module__code=code, action=action, is_active=True).first()
        UserPermissionOverride.objects.create(user_profile=self.profile, permission=permission, allowed=False)
        cache.clear()

    def order(self, **extra):
        data = {'po_number': 'PO-' + str(uuid4()), 'title': 'Pressure gauges', 'status': 'sent',
                'vendor': self.vendor, 'category': 'instrumentation', 'total_amount': '100.00',
                'approval_log': [{'stage': 'Final Management Sign-off', 'approver': 'Recorded signer', 'status': 'Approved'}],
                'items': [{'description': 'Gauge', 'quantity': '10.50', 'unit': 'EA'}]}
        data.update(extra)
        return PurchaseOrder.objects.create(**data)

    def payload(self, po, received='2.50', rejected='0', **extra):
        po.refresh_from_db()
        data = {'purchase_order': str(po.pk), 'operation_key': str(uuid4()), 'expected_po_updated_at': po.updated_at.isoformat(),
                'items_received': [{'line_id': 'line:1', 'received_qty': received, 'rejected_qty': rejected}]}
        data.update(extra)
        return data

    def record(self, po, received='2.50', **extra):
        response = self.client.post(BASE + 'receipts/', self.payload(po, received, **extra), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def decide(self, data, action='accept', **extra):
        return self.client.post(BASE + f"receipts/{data['id']}/{action}/", {'expected_updated_at': data['updated_at'], **extra}, format='json')

    def test_queue_is_paginated_searchable_and_keeps_blocked_orders_visible(self):
        for state in ('sent', 'acknowledged', 'in_progress', 'partially_received'):
            self.order(status=state)
        blocked = self.order(items=[])
        self.order(status='draft')
        self.order(status='cancelled')
        closed = self.order(status='completed')
        response = self.client.get(BASE + 'receipts/available-orders/', {'page_size': 2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 5)
        self.assertEqual(len(response.data['results']), 2)
        self.assertIsNotNone(response.data['next'])
        row = self.client.get(BASE + 'receipts/available-orders/', {'search': blocked.po_number}).data['results'][0]
        self.assertFalse(row['receiving']['can_record'])
        self.assertTrue(row['receiving']['blocked_reason'])
        results = self.client.get(BASE + 'receipts/available-orders/', {'queue': 'reconciliation'}).data
        self.assertEqual(results['count'], 1)
        self.assertEqual(results['results'][0]['id'], str(closed.pk))
        self.assertTrue(results['results'][0]['receiving']['can_reconcile'])

    def test_queue_and_summary_deny_po_read_without_leaking_orders(self):
        po = self.order()
        self.deny('procurement_orders', 'read')
        self.assertEqual(self.client.get(BASE + 'receipts/available-orders/').status_code, 403)
        self.assertEqual(self.client.get(BASE + f'orders/{po.pk}/receiving-summary/').status_code, 403)
        self.assertEqual(self.client.post(BASE + 'receipts/', self.payload(po), format='json').status_code, 403)
        self.assertFalse(Receipt.objects.exists())

    def test_pending_decimal_balance_unknown_inspection_and_exact_retry(self):
        po = self.order()
        payload = self.payload(po, '2.50', rejected='0.25')
        first = self.client.post(BASE + 'receipts/', payload, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(first.data['status'], 'pending')
        self.assertIsNone(first.data['quality_check_passed'])
        second = self.client.post(BASE + 'receipts/', payload, format='json')
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(first.data['id'], second.data['id'])
        self.assertEqual(Receipt.objects.count(), 1)
        summary = receiving_summary(po)
        self.assertEqual(summary['lines'][0]['accepted'], '0')
        self.assertEqual(summary['lines'][0]['pending'], '2.25')
        self.assertEqual(summary['lines'][0]['available'], '8.25')
        self.assertEqual(len(first.data['workflow_history']), 1)

    def test_operation_collision_actor_or_payload_cannot_reuse_request(self):
        po = self.order()
        payload = self.payload(po)
        self.assertEqual(self.client.post(BASE + 'receipts/', payload, format='json').status_code, 201)
        payload['notes'] = 'Different request'
        self.assertEqual(self.client.post(BASE + 'receipts/', payload, format='json').status_code, 409)
        payload.pop('notes')
        original_user = self.user
        other = get_user_model().objects.create_user('another-receiver', email='another@example.test')
        profile, _ = UserProfile.objects.get_or_create(user=other, defaults={'organization': self.profile.organization})
        set_position(other)
        UserRole.objects.create(user_profile=profile, role=self.role)
        self.client.force_authenticate(other)
        self.assertEqual(self.client.post(BASE + 'receipts/', payload, format='json').status_code, 409)
        self.assertEqual(Receipt.objects.get().received_by, original_user)

    def test_recorded_date_and_remarks_persist_without_claiming_inspection(self):
        data = self.record(self.order(), receipt_date='2026-08-31', delivery_note_number='DN-TEST-31', notes='Retained delivery note')
        receipt = Receipt.objects.get(pk=data['id'])
        self.assertEqual(data['receipt_date'], '2026-08-31')
        self.assertEqual(receipt.receipt_date, date(2026, 8, 31))
        self.assertEqual(receipt.delivery_note_number, 'DN-TEST-31')
        self.assertEqual(receipt.notes, 'Retained delivery note')
        self.assertEqual(receipt.workflow_history[0]['receipt_date'], '2026-08-31')
        self.assertEqual(receipt.status, 'pending')
        for field in ('quality_check_passed', 'dimensional_check_passed', 'visual_inspection_passed', 'material_verification_passed'):
            self.assertIsNone(getattr(receipt, field))

    def test_date_participates_in_retry_identity_and_reconciliation_keeps_recorded_date(self):
        po = self.order(status='completed')
        approval = deepcopy(po.approval_log)
        payload = self.payload(po, receipt_date='2026-08-31', reason='Retained delivery evidence')
        first = self.client.post(BASE + 'receipts/reconcile/', payload, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        retry = self.client.post(BASE + 'receipts/reconcile/', payload, format='json')
        self.assertEqual(retry.status_code, 201, retry.data)
        self.assertEqual(first.data['id'], retry.data['id'])
        self.assertEqual(retry.data['receipt_date'], '2026-08-31')
        payload['receipt_date'] = '2026-09-01'
        self.assertEqual(self.client.post(BASE + 'receipts/reconcile/', payload, format='json').status_code, 409)
        self.assertEqual(Receipt.objects.count(), 1)
        self.assertEqual(Receipt.objects.get().receipt_date, date(2026, 8, 31))
        po.refresh_from_db()
        self.assertEqual(po.status, 'completed')
        self.assertEqual(po.approval_log, approval)

    def test_omitted_date_defaults_to_local_date_and_retry_does_not_change_it(self):
        po = self.order()
        payload = self.payload(po)
        with timezone.override('Asia/Dubai'), patch('django.utils.timezone.now', return_value=datetime(2026, 9, 23, 21, tzinfo=datetime_timezone.utc)):
            first = self.client.post(BASE + 'receipts/', payload, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(first.data['receipt_date'], '2026-09-24')
        with timezone.override('Asia/Dubai'), patch('django.utils.timezone.now', return_value=datetime(2026, 9, 24, 21, tzinfo=datetime_timezone.utc)):
            retry = self.client.post(BASE + 'receipts/', payload, format='json')
        self.assertEqual(retry.status_code, 201, retry.data)
        self.assertEqual(retry.data['id'], first.data['id'])
        self.assertEqual(retry.data['receipt_date'], '2026-09-24')

    def test_invalid_date_rejects_without_receipt_or_po_mutation(self):
        po = self.order()
        original_updated_at = po.updated_at
        for value in ('', None, '2026-02-30', '2026-09-24T00:00:00Z', 'not-a-date'):
            with self.subTest(value=value):
                response = self.client.post(BASE + 'receipts/', self.payload(po, receipt_date=value), format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn('receipt_date', response.data)
        self.assertFalse(Receipt.objects.exists())
        po.refresh_from_db()
        self.assertEqual(po.updated_at, original_updated_at)

    def test_recorded_date_cannot_be_changed_on_pending_or_decided_evidence(self):
        data = self.record(self.order(), receipt_date='2026-08-31', notes='Original evidence')
        url = BASE + f"receipts/{data['id']}/"
        response = self.client.patch(url, {'expected_updated_at': data['updated_at'], 'receipt_date': '2026-09-01', 'notes': 'Changed'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('receipt_date', response.data)
        receipt = Receipt.objects.get(pk=data['id'])
        self.assertEqual(receipt.receipt_date, date(2026, 8, 31))
        self.assertEqual(receipt.notes, 'Original evidence')
        self.assertEqual(len(receipt.workflow_history), 1)
        accepted = self.decide(data)
        self.assertEqual(accepted.status_code, 200, accepted.data)
        response = self.client.patch(url, {'expected_updated_at': accepted.data['updated_at'], 'receipt_date': '2026-09-01'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        receipt.refresh_from_db()
        self.assertEqual(receipt.receipt_date, date(2026, 8, 31))
        self.assertEqual(receipt.status, 'accepted')
        self.assertEqual(len(receipt.workflow_history), 2)

    def test_stale_po_and_overreceipt_have_no_effects(self):
        po = self.order()
        stale = self.payload(po)
        self.record(po, '8')
        self.assertEqual(self.client.post(BASE + 'receipts/', stale, format='json').status_code, 409)
        response = self.client.post(BASE + 'receipts/', self.payload(po, '3'), format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(Receipt.objects.count(), 1)

    def test_invalid_lines_and_nonpending_creates_are_rejected(self):
        po = self.order()
        for items in ([], [{'line_id': 'unknown', 'received_qty': '1'}], [{'line_id': 'line:1', 'received_qty': 'NaN'}],
                      [{'line_id': 'line:1', 'received_qty': '1', 'rejected_qty': '2'}],
                      [{'line_id': 'line:1', 'received_qty': '1'}] * 2):
            response = self.client.post(BASE + 'receipts/', self.payload(po, items_received=items), format='json')
            self.assertEqual(response.status_code, 400, response.data)
        for status in ('accepted', 'rejected', 'partial'):
            self.assertEqual(self.client.post(BASE + 'receipts/', self.payload(po, status=status), format='json').status_code, 403)
        self.assertFalse(Receipt.objects.exists())

    def test_pending_approval_draft_and_cancelled_cannot_receive(self):
        for kwargs in ({'status': 'draft'}, {'status': 'cancelled'}, {'approval_log': []}):
            po = self.order(**kwargs)
            response = self.client.post(BASE + 'receipts/', self.payload(po), format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Receipt.objects.exists())

    def test_partial_then_full_acceptance_does_not_close_po_until_explicit_completion(self):
        po = self.order()
        first = self.record(po, '2.50')
        response = self.decide(first)
        self.assertEqual(response.status_code, 200, response.data)
        po.refresh_from_db()
        self.assertEqual(po.status, 'sent')
        self.assertEqual(receiving_summary(po)['status'], 'partial')
        self.assertEqual(self.client.patch(BASE + f'orders/{po.pk}/', {'status': 'completed'}, format='json').status_code, 400)
        last = self.record(po, '8.00')
        self.assertEqual(self.decide(last).status_code, 200)
        summary = self.client.get(BASE + f'orders/{po.pk}/receiving-summary/')
        self.assertEqual(summary.status_code, 200, summary.data)
        self.assertEqual(summary.data['status'], 'complete')
        self.assertEqual(self.client.get(BASE + 'receipts/available-orders/').data['count'], 0)
        completed = self.client.patch(BASE + f'orders/{po.pk}/', {'status': 'completed'}, format='json')
        self.assertEqual(completed.status_code, 200, completed.data)

    def test_rejection_releases_reservation_and_requires_reason_and_authority(self):
        po = self.order()
        data = self.record(po, '10.50')
        self.assertEqual(self.decide(data, 'reject_delivery').status_code, 400)
        response = self.decide(data, 'reject_delivery', reason='Damaged goods')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(receiving_summary(po)['lines'][0]['available'], '10.50')
        denied = self.record(po, '1')
        self.deny('procurement_receipts', 'approve')
        self.assertEqual(self.decide(denied).status_code, 403)
        self.assertEqual(Receipt.objects.get(pk=denied['id']).status, 'pending')

    def test_approval_retry_is_single_audit_and_revocation_still_applies(self):
        data = self.record(self.order())
        self.assertEqual(self.decide(data).status_code, 200)
        self.assertEqual(self.decide(data).status_code, 200)
        self.assertEqual(len(Receipt.objects.get(pk=data['id']).workflow_history), 2)
        set_position(self.user, 'Unassigned position')
        self.assertEqual(self.decide(data).status_code, 403)

    def test_stale_receipt_update_and_decision_preserve_evidence(self):
        data = self.record(self.order())
        url = BASE + f"receipts/{data['id']}/"
        updated = self.client.patch(url, {'expected_updated_at': data['updated_at'], 'notes': 'Inspection prepared'}, format='json')
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(self.decide(data).status_code, 409)
        self.assertEqual(self.decide(updated.data).status_code, 200)
        denied = self.client.patch(url, {'expected_updated_at': updated.data['updated_at'], 'notes': 'Erase evidence'}, format='json')
        self.assertEqual(denied.status_code, 400)
        self.assertEqual(Receipt.objects.get(pk=data['id']).notes, 'Inspection prepared')

    def test_pending_lines_and_server_history_cannot_be_replaced(self):
        data = self.record(self.order())
        url = BASE + f"receipts/{data['id']}/"
        for value in ({'items_received': []}, {'purchase_order': str(self.order().pk)}, {'workflow_history': []}):
            response = self.client.patch(url, {'expected_updated_at': data['updated_at'], **value}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(len(Receipt.objects.get(pk=data['id']).items_received), 1)

    def test_reconcile_requires_reason_creates_pending_and_preserves_completed_approval(self):
        po = self.order(status='completed')
        approval = deepcopy(po.approval_log)
        payload = self.payload(po)
        self.assertEqual(self.client.post(BASE + 'receipts/', payload, format='json').status_code, 400)
        self.assertEqual(self.client.post(BASE + 'receipts/reconcile/', payload, format='json').status_code, 400)
        payload['reason'] = 'Recording retained delivery evidence'
        response = self.client.post(BASE + 'receipts/reconcile/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['status'], 'pending')
        self.assertEqual(response.data['workflow_history'][0]['reason'], payload['reason'])
        self.assertEqual(self.decide(response.data).status_code, 200)
        po.refresh_from_db()
        self.assertEqual(po.status, 'completed')
        self.assertEqual(po.approval_log, approval)

    def test_reconciliation_maps_create_not_update_or_approve(self):
        po = self.order(status='completed')
        self.deny('procurement_receipts', 'update')
        self.deny('procurement_receipts', 'approve')
        response = self.client.post(BASE + 'receipts/reconcile/', self.payload(po, reason='Retained delivery note'), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.deny('procurement_receipts', 'create')
        denied = self.client.post(BASE + 'receipts/reconcile/', self.payload(po, reason='Another delivery'), format='json')
        self.assertEqual(denied.status_code, 403)

    def test_service_acceptance_records_actual_net_value_without_invented_quantity(self):
        po = self.order(items=[], category='engineering_services', scope_of_services='Review project specifications',
                        vat_basis='exclusive', net_amount='100.00', total_amount='105.00', currency='AED')
        payload = self.payload(po, items_received=[{'line_id': 'service:total', 'received_amount': '40.25', 'rejected_amount': '0.25'}])
        response = self.client.post(BASE + 'receipts/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotIn('received_qty', response.data['items_received'][0])
        self.assertEqual(self.decide(response.data).status_code, 200)
        summary = receiving_summary(po)
        self.assertEqual(summary['basis'], 'service_value')
        self.assertEqual(summary['lines'][0]['ordered'], '100.00')
        self.assertEqual(summary['lines'][0]['accepted'], '40.00')
        self.assertEqual(summary['lines'][0]['remaining'], '60.00')

    def test_blank_rich_service_scope_blocks_queue_and_receipt_creation(self):
        for blank in ('<p><br></p>', '<p>&nbsp;</p>', '<div>\u200b\u200c\u200d\ufeff</div>',
                      '<div style="page-break-before:always"><br></div>',
                      '<script>Not service evidence</script>'):
            with self.subTest(scope=blank):
                po = self.order(items=[], category='engineering_services', scope_of_services=blank,
                                description='<p>&nbsp;<br></p>', vat_basis='exclusive',
                                net_amount='100.00', total_amount='105.00', currency='AED')
                row = self.client.get(BASE + 'receipts/available-orders/', {'search': po.po_number}).data['results'][0]
                self.assertEqual(row['receiving']['status'], 'blocked')
                self.assertFalse(row['receiving']['can_record'])
                payload = self.payload(po, items_received=[{'line_id': 'service:total', 'received_amount': '10'}])
                response = self.client.post(BASE + 'receipts/', payload, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertFalse(Receipt.objects.filter(purchase_order=po).exists())

    def test_service_basis_uses_first_meaningful_recorded_scope(self):
        fallback = '<p>Inspect the retained project specifications.</p>'
        po = self.order(items=[], category='engineering_services', scope_of_services='<p>&nbsp;<br></p>',
                        description=fallback, vat_basis='exclusive', net_amount='100.00', currency='AED')
        po.refresh_from_db()
        self.assertEqual(receiving_summary(po)['lines'][0]['description'], fallback)
        payload = self.payload(po, items_received=[{'line_id': 'service:total', 'received_amount': '10'}])
        response = self.client.post(BASE + 'receipts/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['items_received'][0]['item'], fallback)
        po.scope_of_services = '<p>Primary recorded service scope.</p>'
        self.assertEqual(receiving_summary(po)['lines'][0]['description'], po.scope_of_services)

    def test_safe_rich_text_image_and_table_service_scopes_remain_receivable(self):
        content = BytesIO()
        Image.new('RGB', (2, 2), 'white').save(content, format='PNG')
        encoded = base64.b64encode(content.getvalue()).decode('ascii')
        for scope in ('<p><strong>Review project specifications.</strong></p>',
                      f'<p><img src="data:image/png;base64,{encoded}"></p>',
                      '<table><tr><td>Recorded service schedule.</td></tr></table>'):
            with self.subTest(scope=scope):
                po = self.order(items=[], category='engineering_services', scope_of_services=scope,
                                description='', vat_basis='exclusive', net_amount='100.00', currency='AED')
                payload = self.payload(po, items_received=[{'line_id': 'service:total', 'received_amount': '10'}])
                response = self.client.post(BASE + 'receipts/', payload, format='json')
                self.assertEqual(response.status_code, 201, response.data)
                self.assertEqual(receiving_summary(po)['basis'], 'service_value')
                self.assertEqual(response.data['items_received'][0]['item'], scope)

    def test_ambiguous_service_basis_and_legacy_receipt_evidence_remain_blocked(self):
        for kwargs in ({}, {'net_amount': '100'}, {'net_amount': '100', 'vat_basis': 'none', 'scope_of_services': ''}):
            po = self.order(items=[], category='engineering_services', **kwargs)
            self.assertEqual(receiving_summary(po)['status'], 'blocked')
        po = self.order()
        Receipt.objects.create(receipt_number='LEGACY-NO-LINES', purchase_order=po, status='accepted')
        summary = receiving_summary(po)
        self.assertEqual(summary['status'], 'blocked')
        self.assertFalse(summary['can_record'])

    def test_new_request_requires_tokens_and_unknown_flags_remain_unknown(self):
        po = self.order()
        for missing in ('operation_key', 'expected_po_updated_at'):
            data = self.payload(po)
            data.pop(missing)
            response = self.client.post(BASE + 'receipts/', data, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        data = self.record(po)
        self.assertEqual(self.decide(data).status_code, 200)
        receipt = Receipt.objects.get(pk=data['id'])
        self.assertIsNone(receipt.quality_check_passed)

    def test_missing_approval_route_denies_without_changes(self):
        data = self.record(self.order())
        with override_settings(RADAI_BUSINESS_APPROVAL_ROUTES={}):
            self.assertEqual(self.decide(data).status_code, 403)
        self.assertEqual(Receipt.objects.get(pk=data['id']).status, 'pending')

    def test_canonical_line_reference_and_partial_inspection_are_preserved(self):
        po = self.order(items=[{'line_number': 10, 'item_no': 'VALVE-10', 'description': 'Valve', 'quantity': '5', 'unit': 'EA'}])
        payload = self.payload(po, items_received=[{'line_id': 'line:10', 'received_qty': '3', 'rejected_qty': '1'}])
        response = self.client.post(BASE + 'receipts/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        item = response.data['items_received'][0]
        self.assertEqual(item['line_number'], 10)
        self.assertEqual(item['po_item_reference'], 'VALVE-10')
        decided = self.decide(response.data)
        self.assertEqual(decided.status_code, 200, decided.data)
        self.assertEqual(decided.data['status'], 'partial')
        self.assertEqual(self.decide(response.data).status_code, 200)
        summary = receiving_summary(po)
        self.assertEqual(summary['lines'][0]['accepted'], '2')
        self.assertEqual(summary['lines'][0]['remaining'], '3')

    def test_inspection_evidence_is_explicit_and_conflicting_decision_retry_is_rejected(self):
        data = self.record(self.order())
        response = self.decide(data, quality_check_passed=True, visual_inspection_passed=True,
                               inspection_notes='Inspected against delivery note')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['quality_check_passed'])
        self.assertIsNone(response.data['material_verification_passed'])
        self.assertEqual(self.decide(data, quality_check_passed=True, visual_inspection_passed=True,
                                    inspection_notes='Inspected against delivery note').status_code, 200)
        self.assertEqual(self.decide(data, quality_check_passed=False).status_code, 409)
        self.assertTrue(Receipt.objects.get(pk=data['id']).quality_check_passed)

    def test_approved_category_cannot_be_reclassified_to_change_receiving_basis(self):
        from apps.procurement.services.purchase_order_content import purchase_order_content_fingerprint
        po = self.order()
        fingerprint = purchase_order_content_fingerprint(po)
        response = self.client.patch(BASE + f'orders/{po.pk}/', {'category': 'engineering_services'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        po.refresh_from_db()
        self.assertEqual(po.category, 'instrumentation')
        self.assertEqual(purchase_order_content_fingerprint(po), fingerprint)

    def test_legacy_inconsistent_pending_quantities_cannot_be_silently_certified(self):
        po = self.order()
        receipt = Receipt.objects.create(receipt_number='LEGACY-INCONSISTENT', purchase_order=po,
                                         items_received=[{'line_number': 1, 'received_qty': '3', 'accepted_qty': '1', 'rejected_qty': '0'}])
        response = self.decide({'id': receipt.pk, 'updated_at': receipt.updated_at.isoformat()})
        self.assertEqual(response.status_code, 400, response.data)
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, 'pending')
        self.assertEqual(receipt.items_received[0]['accepted_qty'], '1')

    def test_partial_pending_reservations_can_be_accepted_in_either_order(self):
        for partial_first in (True, False):
            with self.subTest(partial_first=partial_first):
                po = self.order(items=[{'description': 'Valve', 'quantity': '10', 'unit': 'EA'}])
                partial = self.record(po, '10', rejected='5')
                replacement = self.record(po, '5')
                ordered = (partial, replacement) if partial_first else (replacement, partial)
                for data in ordered:
                    response = self.decide(data)
                    self.assertEqual(response.status_code, 200, response.data)
                receipt = Receipt.objects.get(pk=partial['id'])
                self.assertEqual(receipt.items_received[0]['received_qty'], '10')
                self.assertEqual(receipt.items_received[0]['accepted_qty'], '5')
                self.assertEqual(receipt.status, 'partial')
                self.assertEqual(receiving_summary(po)['status'], 'complete')

    def test_http_approval_guard_uses_parent_before_receipt_lock_order(self):
        from django.db.models.query import QuerySet
        original = QuerySet.select_for_update
        for action in ('accept', 'reject_delivery'):
            with self.subTest(action=action):
                pr = PurchaseRequisition.objects.create(pr_number=f'PR-LOCK-{action}', vendor=self.vendor,
                                                        requested_by=self.user, issued_by=self.user, status='approved')
                data = self.record(self.order(pr_reference=pr))
                locks = []

                def capture(queryset, *args, **kwargs):
                    if queryset.model in (PurchaseRequisition, PurchaseOrder, Receipt):
                        locks.append(queryset.model.__name__)
                    return original(queryset, *args, **kwargs)

                with patch.object(QuerySet, 'select_for_update', capture):
                    response = self.decide(data, action, **({'reason': 'Recorded inspection issue'} if action == 'reject_delivery' else {}))
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(locks[:3], ['PurchaseRequisition', 'PurchaseOrder', 'Receipt'])
                self.assertEqual(locks[3:6], ['PurchaseRequisition', 'PurchaseOrder', 'Receipt'])
