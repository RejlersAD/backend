"""Real guarded routes cannot progress orders around their approval evidence."""

from copy import deepcopy
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.exceptions import ValidationError
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.procurement.models import PODocument, PurchaseOrder, PurchaseRequisition, Receipt, Vendor
from apps.procurement.views import PurchaseOrderViewSet, ReceiptViewSet
from apps.procurement.services.purchase_order_lifecycle import (
    PROGRESSED_STATUSES,
    lock_purchase_order,
    purchase_order_transition_issue,
    validate_purchase_order_transition,
)
from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, RolePermission, UserProfile, UserRole
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints

from .approval_fixtures import set_position


router = DefaultRouter()
router.register('orders', PurchaseOrderViewSet, basename='lifecycle-order')
router.register('receipts', ReceiptViewSet, basename='lifecycle-receipt')
urlpatterns = [path('api/v1/procurement/', include(router.urls))]
secure_module_endpoints(urlpatterns)
BASE = '/api/v1/procurement/'


@override_settings(ROOT_URLCONF=__name__, RADAI_BUSINESS_APPROVAL_ROUTES={
    'procurement_receipts.Receipt.accept': {'positions': ['engineer'], 'pending_states': ['pending']},
})
class PurchaseOrderLifecycleApprovalGuardsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user('lifecycle-editor', email='editor@example.test')
        org, _ = Organization.objects.get_or_create(code='lifecycle-tests', defaults={'name': 'Lifecycle tests'})
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'organization': org})
        profile.roles.clear()
        set_position(self.user)
        self.role = Role.objects.create(code='lifecycle-editor', name='Lifecycle editor', level=3)
        UserRole.objects.create(user_profile=profile, role=self.role)
        for code, actions in (
            ('procurement_orders', ('read', 'create', 'update')),
            ('procurement_receipts', ('read', 'approve')),
        ):
            module, _ = Module.objects.get_or_create(code=code, defaults={'name': code})
            ensure_module_actions(Module, Permission, module_ids=[module.pk])
            RoleModule.objects.get_or_create(role=self.role, module=module)
            for permission in module.permissions.filter(action__in=actions, is_active=True):
                RolePermission.objects.get_or_create(role=self.role, permission=permission)
        self.vendor = Vendor.objects.create(vendor_code='LIFECYCLE', name='Lifecycle supplier', status='active')
        self.pr = PurchaseRequisition.objects.create(
            pr_number='RAD-PRJ-PR-0450_2026', issued_by=self.user, requested_by=self.user,
            vendor=self.vendor, status='draft',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.notification = patch('apps.procurement.serializers.notify_assigned_approvers')
        self.notification.start()
        self.addCleanup(self.notification.stop)

    def order(self, **values):
        fields = {
            'po_number': 'RAD-PRJ-PUR-0450_2026', 'pr_reference': self.pr,
            'vendor': self.vendor, 'created_by': self.user, 'title': 'Lifecycle approval test',
            'category': 'other', 'total_amount': '100.00',
            'approval_log': [{
                'stage': 'Technical Approval', 'level': 0,
                'user_id': str(self.user.pk), 'approver_email': self.user.email,
                'status': 'Pending',
            }],
        }
        fields.update(values)
        return PurchaseOrder.objects.create(**fields)

    def approved_rows(self):
        return [{
            'stage': 'Technical Approval', 'level': 0,
            'user_id': str(self.user.pk), 'approver_email': self.user.email,
            'approved_by_id': str(self.user.pk), 'approved_by_email': self.user.email,
            'status': 'Approved',
        }]

    def source_row(self, order, **overrides):
        document = PODocument.objects.create(
            original_filename='approved.pdf', document_type='purchase_order',
            confirmed_po=order, uploaded_by=self.user,
            extracted_data={'signature_verified': True, 'approval_evidence_complete': True},
        )
        row = {
            'stage': 'Signed PO document approval', 'status': 'Approved',
            'approver': 'Recorded source approver', 'signature_verified': True,
            'approval_evidence_complete': True, 'evidence_document_id': str(document.pk),
        }
        row.update(overrides)
        return row, document

    def test_pending_approval_blocks_every_progressed_generic_patch(self):
        order = self.order()
        original = deepcopy(order.approval_log)
        for target in PROGRESSED_STATUSES:
            with self.subTest(status=target):
                response = self.client.patch(f'{BASE}orders/{order.pk}/', {'status': target}, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                order.refresh_from_db()
                self.assertEqual(order.status, 'draft')
                self.assertEqual(order.approval_log, original)

    def test_empty_route_cannot_be_created_as_progressed(self):
        for target in PROGRESSED_STATUSES:
            with self.subTest(status=target):
                response = self.client.post(f'{BASE}orders/', {
                    'pr_reference': str(self.pr.pk), 'vendor': str(self.vendor.pk),
                    'title': 'Cannot skip approval', 'category': 'other',
                    'total_amount': '100.00', 'status': target,
                }, format='json')
                self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(PurchaseOrder.objects.exists())
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'draft')

    def test_send_action_blocks_pending_without_changing_evidence(self):
        order = self.order()
        response = self.client.post(f'{BASE}orders/{order.pk}/send_to_vendor/', {}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        order.refresh_from_db()
        self.assertEqual(order.status, 'draft')
        self.assertEqual(order.approval_log[0]['status'], 'Pending')

    def test_approved_route_can_be_sent_acknowledged_and_completed(self):
        order = self.order(approval_log=self.approved_rows())
        for operation in ('send_to_vendor', 'acknowledge'):
            response = self.client.post(f'{BASE}orders/{order.pk}/{operation}/', {}, format='json')
            self.assertEqual(response.status_code, 200, response.data)
        response = self.client.patch(f'{BASE}orders/{order.pk}/', {'status': 'completed'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        order.refresh_from_db()
        self.assertEqual(order.status, 'completed')

    def test_acknowledgement_checks_existing_historical_pending_route(self):
        order = self.order(status='sent')
        response = self.client.post(f'{BASE}orders/{order.pk}/acknowledge/', {}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        order.refresh_from_db()
        self.assertEqual(order.status, 'sent')

    def test_pending_rejected_malformed_and_empty_routes_cannot_progress(self):
        order = self.order()
        for rows in ([], {}, ['bad row'], [{'status': 'Approved'}],
                     [{'status': 'Rejected', 'approver': 'Recorded reviewer'}],
                     [{'status': 'Pending', 'approver': 'Recorded reviewer'}]):
            with self.subTest(rows=rows):
                order.approval_log = rows
                with self.assertRaises(ValidationError):
                    validate_purchase_order_transition(order, 'completed')

    def test_approved_status_with_conflicting_signer_cannot_progress(self):
        rows = self.approved_rows()
        rows[0]['approved_by_email'] = 'someone-else@example.test'
        order = self.order(approval_log=rows)
        response = self.client.post(f'{BASE}orders/{order.pk}/send_to_vendor/', {}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('signer', str(response.data).lower())

    def test_final_signature_must_match_recorded_final_decision(self):
        rows = self.approved_rows()
        rows[0]['signature'] = 'recorded-signature'
        order = self.order(approval_log=rows, approved_by=self.user, approval_signature='different-signature')
        response = self.client.patch(f'{BASE}orders/{order.pk}/', {'status': 'completed'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('signature', str(response.data).lower())

    def test_verified_source_route_preserves_historical_progression(self):
        order = self.order(approval_log=[])
        row, _document = self.source_row(order)
        order.approval_log = [row]
        order.save(update_fields=['approval_log'])
        response = self.client.post(f'{BASE}orders/{order.pk}/send_to_vendor/', {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)

    def test_external_evidence_requires_actual_verified_document_for_this_order(self):
        order = self.order(approval_log=[])
        row, document = self.source_row(order)
        for changed in ({'signature_verified': False}, {'approval_evidence_complete': False},
                        {'evidence_document_id': 'missing-document'}):
            with self.subTest(changed=changed):
                order.approval_log = [{**row, **changed}]
                with self.assertRaises(ValidationError):
                    validate_purchase_order_transition(order, 'sent')
        order.approval_log = [row]
        document.confirmed_po = None
        document.save(update_fields=['confirmed_po'])
        with self.assertRaises(ValidationError):
            validate_purchase_order_transition(order, 'sent')
        document.confirmed_po = order
        document.extracted_data = {'signature_verified': False}
        document.save(update_fields=['confirmed_po', 'extracted_data'])
        with self.assertRaises(ValidationError):
            validate_purchase_order_transition(order, 'sent')

    def test_verified_source_does_not_override_pending_internal_assignment(self):
        order = self.order()
        row, _document = self.source_row(order)
        order.approval_log.append(row)
        with self.assertRaises(ValidationError):
            validate_purchase_order_transition(order, 'sent')

    def test_verified_signature_cannot_approve_source_with_unresolved_commercial_mismatch(self):
        order = self.order(approval_log=[])
        row, document = self.source_row(order)
        order.approval_log = [row]
        order.save(update_fields=['approval_log'])
        for mismatch in (
            {'reconciliation_required': True},
            {'reconciliation_issues': ['The PDF amount differs from the saved order.']},
        ):
            with self.subTest(mismatch=mismatch):
                document.extracted_data = {'signature_verified': True, 'approval_evidence_complete': True, **mismatch}
                document.save(update_fields=['extracted_data'])
                response = self.client.post(f'{BASE}orders/{order.pk}/send_to_vendor/', {}, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                order.refresh_from_db()
                self.assertEqual(order.status, 'draft')

    def test_named_historical_approved_rows_remain_valid_evidence(self):
        order = self.order(approval_log=[{
            'stage': 'Original PR approval', 'approver': 'Historical Approver', 'status': 'Approved',
            'comments': 'Approved on source purchase requisition.',
        }])
        self.assertEqual(purchase_order_transition_issue(order, 'sent'), '')
        response = self.client.post(f'{BASE}orders/{order.pk}/send_to_vendor/', {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)

    def test_receipt_accept_rolls_back_when_order_approval_pending(self):
        order = self.order()
        receipt = Receipt.objects.create(receipt_number='GUARDED-PENDING', purchase_order=order)
        response = self.client.post(f'{BASE}receipts/{receipt.pk}/accept/', {}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        receipt.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(receipt.status, 'pending')
        self.assertEqual(order.status, 'draft')
        self.assertIsNone(order.actual_delivery)

    def test_receipt_accept_completes_only_approved_order(self):
        order = self.order(approval_log=self.approved_rows())
        receipt = Receipt.objects.create(receipt_number='GUARDED-APPROVED', purchase_order=order)
        response = self.client.post(f'{BASE}receipts/{receipt.pk}/accept/', {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        receipt.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(receipt.status, 'accepted')
        self.assertEqual(order.status, 'completed')
        self.assertIsNotNone(order.actual_delivery)

    def test_receipt_business_position_gate_remains_enforced(self):
        order = self.order(approval_log=self.approved_rows())
        receipt = Receipt.objects.create(receipt_number='GUARDED-PERMISSION', purchase_order=order)
        with override_settings(RADAI_BUSINESS_APPROVAL_ROUTES={}):
            response = self.client.post(f'{BASE}receipts/{receipt.pk}/accept/', {}, format='json')
        self.assertEqual(response.status_code, 403, response.data)
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, 'pending')

    def test_read_only_user_cannot_send_or_complete_approved_order(self):
        order = self.order(approval_log=self.approved_rows())
        RolePermission.objects.filter(role=self.role).exclude(permission__action='read').delete()
        cache.clear()
        self.assertEqual(self.client.post(f'{BASE}orders/{order.pk}/send_to_vendor/').status_code, 403)
        self.assertEqual(self.client.patch(f'{BASE}orders/{order.pk}/', {'status': 'completed'}, format='json').status_code, 403)

    def test_regressive_or_cancelled_transitions_remain_blocked(self):
        order = self.order(status='in_progress', approval_log=self.approved_rows())
        for target in ('draft', 'sent', 'acknowledged'):
            with self.subTest(target=target), self.assertRaises(ValidationError):
                validate_purchase_order_transition(order, target)
        validate_purchase_order_transition(order, 'cancelled')
        order.status = 'cancelled'
        with self.assertRaises(ValidationError):
            validate_purchase_order_transition(order, 'completed')

    def test_stale_po_relationship_is_rejected_after_lock_refresh(self):
        order = self.order()
        PurchaseOrder.objects.filter(pk=order.pk).update(pr_reference=None)
        with transaction.atomic(), self.assertRaises(ValidationError):
            lock_purchase_order(order)

    def test_prospective_approval_route_is_checked_with_status_change(self):
        order = self.order(approval_log=self.approved_rows())
        with self.assertRaises(ValidationError):
            validate_purchase_order_transition(order, 'completed', approval_log=[])
