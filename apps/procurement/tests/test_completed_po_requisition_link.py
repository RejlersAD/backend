"""Completed orders retain their evidence while procurement repairs PR links."""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import include, path
from django.utils import timezone
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.procurement.models import PODocument, PurchaseOrder, PurchaseRequisition, Vendor
from apps.procurement.views import PurchaseOrderViewSet, PurchaseRequisitionViewSet
from apps.rbac.models import (
    Module, Organization, Permission, Role, RoleModule, RolePermission, UserProfile, UserRole,
)
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints


router = DefaultRouter()
router.register('orders', PurchaseOrderViewSet, basename='completed-link-order')
router.register('requisitions', PurchaseRequisitionViewSet, basename='completed-link-requisition')
urlpatterns = [path('api/v1/procurement/', include(router.urls))]
secure_module_endpoints(urlpatterns)
BASE = '/api/v1/procurement/'


@override_settings(ROOT_URLCONF=__name__)
class CompletedPurchaseOrderRequisitionLinkTests(TestCase):
    def setUp(self):
        cache.clear()
        self.modules = {}
        for code in ('procurement_orders', 'procurement_requisitions'):
            module, _ = Module.objects.get_or_create(code=code, defaults={'name': code})
            ensure_module_actions(Module, Permission, module_ids=[module.pk])
            self.modules[code] = module
        self.organization = Organization.objects.create(code='completed-link', name='Link tests')
        self.user = self.user_with_actions('buyer', 'procurement_orders', ['read', 'update'])
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.vendor = Vendor.objects.create(vendor_code='LINK-COMPLETED', name='Historical supplier')
        self.pr = PurchaseRequisition.objects.create(
            pr_number='RAD-PRJ-PR-0091_2026', status='approved',
            total_price=Decimal('100.00'), currency='AED',
            price_remarks_data={'signed_document_verification': {'signed_off': True},
                                'budget_in_aed': '200.00', 'payment_terms': 'Net 30'},
            attachments=[{'filename': 'signed-pr.pdf', 's3_key': 'tests/signed-pr.pdf'}],
        )
        self.order = PurchaseOrder.objects.create(
            po_number='RAD-PRJ-PUR-0091_2026', vendor=self.vendor, title='Completed historical order',
            status='completed', category='other', total_amount=Decimal('105.00'),
            net_amount=Decimal('100.00'), tax_amount=Decimal('5.00'), currency='AED',
            approval_log=[{'stage': 'Management Approval', 'status': 'Approved', 'comments': 'Signed source'}],
            approved_by_name='Historical approver', approval_signature='source-signature',
            attachments=[{'type': 'signed_purchase_order_pdf', 'filename': 'signed-po.pdf',
                          's3_key': 'tests/signed-po.pdf'}],
        )
        self.source = PODocument.objects.create(
            original_filename='signed-po.pdf', document_type='purchase_order',
            confirmed_po=self.order, s3_key='tests/signed-po.pdf',
            extracted_data={'reconciliation_required': True, 'total_amount': '105.00'},
        )

    def user_with_actions(self, name, module_code, actions):
        user = get_user_model().objects.create_user(f'completed-link-{name}', email=f'{name}@example.test')
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': self.organization})
        profile.roles.clear()
        role = Role.objects.create(code=f'completed-link-{name}', name=f'Link {name}', level=3)
        UserRole.objects.create(user_profile=profile, role=role)
        module = self.modules[module_code]
        RoleModule.objects.create(role=role, module=module)
        for permission in module.permissions.filter(action__in=actions, is_active=True):
            RolePermission.objects.create(role=role, permission=permission)
        return user

    def link(self, *, requisition=None, order=None):
        return self.client.post(
            f'{BASE}requisitions/{(requisition or self.pr).pk}/link-purchase-order/',
            {'purchase_order_id': str((order or self.order).pk)}, format='json',
        )

    def test_order_only_editor_can_link_completed_order_without_editing_source_or_financials(self):
        before_order = PurchaseOrder.objects.values().get(pk=self.order.pk)
        before_source = PODocument.objects.values().get(pk=self.source.pk)
        before_pr = PurchaseRequisition.objects.values().get(pk=self.pr.pk)

        # A PO operator may access the purpose-built selector without PR register access.
        self.assertEqual(self.client.get(f'{BASE}requisitions/').status_code, 403)
        response = self.link()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['po_link']['status'], 'linked')
        self.assertEqual(response.data['po_link']['linked_by_id'], str(self.user.pk))

        after_order = PurchaseOrder.objects.values().get(pk=self.order.pk)
        self.assertEqual(after_order['pr_reference_id'], self.pr.pk)
        for field in set(before_order) - {'pr_reference_id', 'updated_at'}:
            self.assertEqual(after_order[field], before_order[field], field)
        self.assertEqual(PODocument.objects.values().get(pk=self.source.pk), before_source)

        after_pr = PurchaseRequisition.objects.values().get(pk=self.pr.pk)
        self.assertEqual(after_pr['status'], 'converted')
        self.assertEqual(after_pr['po_number_reference'], self.order.po_number)
        self.assertTrue(after_pr['po_applicable'])
        for field in set(before_pr) - {'status', 'po_number_reference', 'po_applicable', 'price_remarks_data', 'updated_at'}:
            self.assertEqual(after_pr[field], before_pr[field], field)
        for key, value in before_pr['price_remarks_data'].items():
            self.assertEqual(after_pr['price_remarks_data'][key], value, key)
        self.assertEqual(after_pr['price_remarks_data']['po_link_previous_status'], 'approved')

    def test_completed_order_ordinary_edit_is_still_rejected(self):
        before = PurchaseOrder.objects.values().get(pk=self.order.pk)
        response = self.client.patch(f'{BASE}orders/{self.order.pk}/', {
            'pr_reference': str(self.pr.pk), 'title': 'Changed title',
        }, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('Completed purchase orders are read-only', str(response.data))
        self.assertEqual(PurchaseOrder.objects.values().get(pk=self.order.pk), before)

    def test_repeated_link_succeeds_without_duplicate_orders_or_sources(self):
        self.assertEqual(self.link().status_code, 200)
        repeated = self.link()
        self.assertEqual(repeated.status_code, 200, repeated.data)
        self.assertEqual(repeated.data['po_link']['status'], 'already_linked')
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        self.assertEqual(PODocument.objects.count(), 1)

    def test_linking_a_draft_requisition_does_not_approve_it(self):
        self.pr.status = 'draft'
        self.pr.save(update_fields=['status'])
        self.assertEqual(self.link().status_code, 200)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'draft')

    def test_completed_order_cannot_be_taken_from_another_requisition(self):
        other = PurchaseRequisition.objects.create(pr_number='RAD-PRJ-PR-0092_2026')
        self.order.pr_reference = other
        self.order.save(update_fields=['pr_reference'])
        response = self.link()
        self.assertEqual(response.status_code, 409, response.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.pr_reference_id, other.pk)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'approved')
        self.assertNotIn('po_link', self.pr.price_remarks_data)

    def test_existing_requisition_link_and_conflicting_source_reference_are_preserved(self):
        previous = PurchaseOrder.objects.create(
            po_number='RAD-PRJ-PUR-0091_SEP2026', pr_reference=self.pr,
            vendor=self.vendor, title='Prior order', total_amount=100,
        )
        response = self.link()
        self.assertEqual(response.status_code, 409, response.data)
        previous.refresh_from_db()
        self.assertEqual(previous.pr_reference_id, self.pr.pk)
        previous.delete()

        self.order.contact_persons = {'requisition_number': 'RAD-PRJ-PR-9999_2026'}
        self.order.save(update_fields=['contact_persons'])
        response = self.link()
        self.assertEqual(response.status_code, 409, response.data)
        self.assertIn('source names a different PR', response.data['error'])
        self.order.refresh_from_db()
        self.assertIsNone(self.order.pr_reference_id)

    def test_read_only_and_requisition_only_users_cannot_mutate_order_links(self):
        for name, module, actions in (
            ('reader', 'procurement_orders', ['read']),
            ('pr-editor', 'procurement_requisitions', ['read', 'update']),
        ):
            with self.subTest(name=name):
                self.client.force_authenticate(self.user_with_actions(name, module, actions))
                self.assertEqual(self.link().status_code, 403)
        self.client.force_authenticate(user=None)
        self.assertIn(self.link().status_code, (401, 403))
        self.order.refresh_from_db()
        self.assertIsNone(self.order.pr_reference_id)

    def test_invalid_or_missing_order_does_not_modify_requisition(self):
        for order_id, expected in (('bad-id', 400), (str(uuid4()), 404)):
            response = self.client.post(f'{BASE}requisitions/{self.pr.pk}/link-purchase-order/',
                {'purchase_order_id': order_id}, format='json')
            self.assertEqual(response.status_code, expected, response.data)
        self.pr.refresh_from_db()
        self.assertNotIn('po_link', self.pr.price_remarks_data)

    def test_order_only_dropdown_search_finds_older_requisitions_beyond_initial_limit(self):
        old = timezone.now() - timedelta(days=365)
        PurchaseRequisition.objects.filter(pk=self.pr.pk).update(created_at=old)
        PurchaseRequisition.objects.bulk_create([
            PurchaseRequisition(pr_number=f'RAD-PRJ-PR-{index:04}_2026', title='Newer recommendation')
            for index in range(200, 320)
        ])
        url = f'{BASE}orders/available-requisitions/'
        initial = self.client.get(url, {'limit': 50})
        self.assertEqual(initial.status_code, 200, initial.data)
        self.assertEqual(len(initial.data), 50)
        self.assertNotIn(str(self.pr.pk), [str(row['id']) for row in initial.data])
        searched = self.client.get(url, {'search': self.pr.pr_number, 'limit': 50})
        self.assertEqual(searched.status_code, 200, searched.data)
        self.assertEqual([str(row['id']) for row in searched.data], [str(self.pr.pk)])
        self.assertEqual(searched.data[0]['pr_number'], self.pr.pr_number)
        self.assertEqual(searched.data[0]['status'], 'approved')
