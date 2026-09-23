"""PO associations retain native PR approval requirements and review state."""

from copy import deepcopy
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.procurement.models import PurchaseOrder, PurchaseRequisition, Vendor
from apps.procurement.views import PODocumentViewSet, PurchaseOrderViewSet, PurchaseRequisitionViewSet
from apps.procurement.services.pr_document_reconciliation import reconcile_pr_po_link
from apps.procurement.services.requisition_workflow import RequisitionWorkflowService
from apps.rbac.models import Module, Permission
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints

from .approval_fixtures import grant_approval, set_position


router = DefaultRouter()
router.register('orders', PurchaseOrderViewSet, basename='link-guard-order')
router.register('requisitions', PurchaseRequisitionViewSet, basename='link-guard-requisition')
router.register('po-documents', PODocumentViewSet, basename='link-guard-po-document')
urlpatterns = [path('api/v1/procurement/', include(router.urls))]
secure_module_endpoints(urlpatterns)
BASE = '/api/v1/procurement/'


@override_settings(
    ROOT_URLCONF=__name__, TEAMS_APPROVAL_WEBHOOK_URL='',
    WEB_PUSH_VAPID_PRIVATE_KEY='', WEB_PUSH_VAPID_PUBLIC_KEY='',
)
class PurchaseRequisitionLinkApprovalGuardsTests(TestCase):
    def setUp(self):
        cache.clear()
        for target in (
            'apps.notifications.services.NotificationService.create_notification',
            'apps.procurement.services.requisition_workflow.RequisitionWorkflowService._notify_level',
            'apps.procurement.serializers.notify_assigned_approvers',
            'apps.procurement.serializers.notify_purchase_order_created',
        ):
            delivery = patch(target)
            delivery.start()
            self.addCleanup(delivery.stop)
        for code in ('procurement_requisitions', 'procurement_orders'):
            module, _ = Module.objects.get_or_create(code=code, defaults={'name': code})
            ensure_module_actions(Module, Permission, module_ids=[module.pk])
        users = get_user_model()
        self.buyer = users.objects.create_superuser('route-buyer', email='route-buyer@example.test', password='test')
        self.employee = users.objects.create_user('route-employee', email='route-employee@example.test')
        self.ceo = users.objects.create_user('route-ceo', first_name='Jarmo', last_name='Suominen', email='jarmo.suominen@example.test')
        for actor, title in ((self.buyer, 'Procurement Manager'), (self.employee, 'Engineer'), (self.ceo, 'CEO')):
            grant_approval(actor)
            set_position(actor, title)
            profile = actor.rbac_profile
            profile.status = 'active'
            profile.signature_image = f'signature-{actor.pk}'
            profile.save(update_fields=['status', 'signature_image'])
        cache.clear()
        self.vendor = Vendor.objects.create(vendor_code='ROUTE-LINK', name='Route supplier', status='active')
        self.client = APIClient()
        self.client.force_authenticate(self.buyer)
        self.sequence = 9100

    def requisition(self, *, include_employee=True, status='submitted', decision=True):
        self.sequence += 1
        rows = [
            {'level': 0, 'role': 'Procurement Manager', 'user_id': str(self.buyer.pk),
             'user_email': self.buyer.email, 'status': 'pending'},
            *([{'level': 1, 'role': 'Level 1 Approver', 'user_id': str(self.employee.pk),
                'user_email': self.employee.email, 'status': 'pending'}] if include_employee else []),
            {'level': 5, 'role': 'CEO', 'user_name': 'Jarmo Suominen', 'user_id': str(self.ceo.pk),
             'user_email': self.ceo.email, 'status': 'pending'},
        ]
        pr = PurchaseRequisition.objects.create(
            pr_number=f'RAD-PRJ-PR-{self.sequence}_2026', title='PR approval route',
            issued_by=self.buyer, status=status, vendor=self.vendor, total_price=100,
            po_applicable=False, approval_workflow_config=rows,
        )
        if decision:
            pr = RequisitionWorkflowService.approve(pr.pk, self.buyer, require_signature=True)
        return pr

    def order(self, pr, *, linked=False):
        return PurchaseOrder.objects.create(
            po_number=pr.pr_number.replace('-PR-', '-PUR-'), vendor=self.vendor,
            title='Linked PO', total_amount=100, pr_reference=pr if linked else None,
        )

    def link(self, pr, po):
        self.client.force_authenticate(self.buyer)
        return self.client.post(
            f'{BASE}requisitions/{pr.pk}/link-purchase-order/',
            {'purchase_order_id': str(po.pk)}, format='json',
        )

    def assert_route_preserved(self, pr, workflow, status):
        pr.refresh_from_db()
        self.assertFalse(pr.po_applicable)
        self.assertEqual(pr.status, status)
        self.assertEqual(pr.approval_workflow_config, workflow)
        self.assertEqual([row['level'] for row in RequisitionWorkflowService._workflow(pr)],
                         [row['level'] for row in workflow])

    def test_manual_link_cannot_remove_ceo_after_an_earlier_approval(self):
        pr = self.requisition()
        workflow, status = deepcopy(pr.approval_workflow_config), pr.status
        direct = self.client.patch(f'{BASE}requisitions/{pr.pk}/', {'po_applicable': True}, format='json')
        self.assertEqual(direct.status_code, 400, direct.data)
        po = self.order(pr)
        response = self.link(pr, po)
        self.assertEqual(response.status_code, 200, response.data)
        self.assert_route_preserved(pr, workflow, status)
        po.refresh_from_db()
        self.assertEqual(po.pr_reference_id, pr.pk)
        self.assertEqual(pr.po_number_reference, po.po_number)
        self.client.force_authenticate(self.employee)
        decision = self.client.post(f'{BASE}requisitions/{pr.pk}/process_dynamic_approval/', {}, format='json')
        self.assertEqual(decision.status_code, 200, decision.data)
        pr.refresh_from_db()
        self.assertEqual(pr.status, 'in_review')
        self.assertEqual(pr.approval_workflow_config[-1]['status'], 'pending')
        self.assertTrue(RequisitionWorkflowService.can_approve(pr, self.ceo))

    def test_manual_link_with_only_ceo_pending_keeps_ceo_actionable(self):
        pr = self.requisition(include_employee=False)
        workflow, status = deepcopy(pr.approval_workflow_config), pr.status
        self.assertTrue(RequisitionWorkflowService.can_approve(pr, self.ceo))
        response = self.link(pr, self.order(pr))
        self.assertEqual(response.status_code, 200, response.data)
        self.assert_route_preserved(pr, workflow, status)
        self.assertTrue(RequisitionWorkflowService.can_approve(pr, self.ceo))
        self.client.force_authenticate(self.ceo)
        decision = self.client.post(f'{BASE}requisitions/{pr.pk}/process_dynamic_approval/', {}, format='json')
        self.assertEqual(decision.status_code, 200, decision.data)
        pr.refresh_from_db()
        self.assertEqual(pr.status, 'converted')
        self.assertEqual(pr.price_remarks_data['po_link_previous_status'], 'approved')
        self.assertEqual(pr.approval_workflow_config[-1]['approved_by_id'], str(self.ceo.pk))
        self.assertTrue(all(row['status'] == 'approved' for row in pr.approval_workflow_config))

    def test_automatic_link_preserves_route_before_and_after_first_decision(self):
        for decided in (False, True):
            with self.subTest(decided=decided):
                pr = self.requisition(decision=decided)
                workflow, status = deepcopy(pr.approval_workflow_config), pr.status
                po = self.order(pr)
                pr.po_number_reference = po.po_number
                pr.save(update_fields=['po_number_reference'])
                result = reconcile_pr_po_link(pr)
                self.assertFalse(result['manual_link_required'], result)
                self.assertEqual(result['status'], 'linked')
                self.assert_route_preserved(pr, workflow, status)
                repeated = reconcile_pr_po_link(pr)
                self.assertEqual(repeated['status'], 'already_linked')
                self.assert_route_preserved(pr, workflow, status)

    def test_manual_link_before_first_decision_keeps_submitted_requirements(self):
        pr = self.requisition(decision=False)
        workflow = deepcopy(pr.approval_workflow_config)
        response = self.link(pr, self.order(pr))
        self.assertEqual(response.status_code, 200, response.data)
        self.assert_route_preserved(pr, workflow, 'submitted')

    def test_automatically_matched_signed_po_import_retains_pending_ceo(self):
        pr = self.requisition(include_employee=False)
        workflow, status = deepcopy(pr.approval_workflow_config), pr.status
        pr.po_number_reference = pr.pr_number.replace('-PR-', '-PUR-')
        pr.save(update_fields=['po_number_reference'])
        fields = {
            'source_po_number': pr.po_number_reference, 'po_number': pr.po_number_reference,
            'source_pr_numbers': [pr.pr_number], 'source_page_count': 1, 'extracted_page_count': 1,
            'extraction_truncated': False, 'po_date': date(2026, 1, 7), 'vendor_name': self.vendor.name,
            'vendor_license_no': '', 'seller_reference': '', 'quote_ref': '',
            'project_number': '', 'summary': 'Signed historical PO', 'payment_terms': 'Net 30',
            'payment_mode': 'Bank Transfer', 'delivery_terms': '', 'expected_delivery': None,
            'total_amount': Decimal('100.00'), 'tax_amount': Decimal('0.00'),
            'gross_amount': Decimal('100.00'), 'currency': 'AED', 'items': [],
        }
        with patch('apps.procurement.services.signed_po_pdf_import.extract_signed_po_fields', return_value=fields):
            response = self.client.post(f'{BASE}po-documents/import_signed_pdf/', {
                'file': SimpleUploadedFile('signed-po.pdf', b'%PDF-1.4 signed source', content_type='application/pdf'),
                'signature_verified': 'true', 'stamp_verified': 'true',
                'approved_by_name': 'Historical PO approver', 'approved_date': '2026-01-07',
            }, format='multipart')
        self.assertEqual(response.status_code, 200, response.data)
        po = PurchaseOrder.objects.get(pk=response.data['purchase_order_id'])
        self.assertEqual(po.pr_reference_id, pr.pk)
        self.assert_route_preserved(pr, workflow, status)
        self.assertTrue(RequisitionWorkflowService.can_approve(pr, self.ceo))

    def test_draft_link_still_sets_po_applicable_without_approving_pr(self):
        pr = self.requisition(status='draft', decision=False)
        response = self.link(pr, self.order(pr))
        self.assertEqual(response.status_code, 200, response.data)
        pr.refresh_from_db()
        self.assertTrue(pr.po_applicable)
        self.assertEqual(pr.status, 'draft')

    def test_signed_external_pr_still_links_and_converts(self):
        pr = self.requisition(status='approved', decision=False)
        pr.approval_workflow_config = [{
            'level': 5, 'role': 'CEO', 'user_name': 'Jarmo Suominen', 'status': 'approved',
            'external': True, 'source': 'signed_purchase_requisition_pdf',
        }]
        pr.price_remarks_data = {'signed_document_verification': {'signed_off': True}}
        pr.save(update_fields=['approval_workflow_config', 'price_remarks_data'])
        workflow = deepcopy(pr.approval_workflow_config)
        response = self.link(pr, self.order(pr))
        self.assertEqual(response.status_code, 200, response.data)
        pr.refresh_from_db()
        self.assertTrue(pr.po_applicable)
        self.assertEqual(pr.status, 'converted')
        self.assertEqual(pr.approval_workflow_config, workflow)

    def test_approved_native_pr_conversion_retains_ceo_approval_evidence(self):
        pr = self.requisition(include_employee=False)
        pr = RequisitionWorkflowService.approve(pr.pk, self.ceo, require_signature=True)
        workflow = deepcopy(pr.approval_workflow_config)
        self.assertEqual(pr.status, 'approved')
        response = self.link(pr, self.order(pr))
        self.assertEqual(response.status_code, 200, response.data)
        self.assert_route_preserved(pr, workflow, 'converted')
        self.assertEqual(pr.price_remarks_data['po_link_previous_status'], 'approved')

    def test_native_po_creation_retains_draft_or_active_pr_status(self):
        for status in ('draft', 'submitted', 'in_review'):
            with self.subTest(status=status):
                pr = self.requisition(status=status, decision=False)
                workflow = deepcopy(pr.approval_workflow_config)
                response = self.client.post(f'{BASE}orders/', {
                    'pr_reference': str(pr.pk), 'vendor': str(self.vendor.pk),
                    'po_number': pr.pr_number.replace('-PR-', '-PUR-'), 'title': 'Native linked PO',
                    'total_amount': '100.00', 'vat_percentage': '0.00', 'category': 'other',
                    'approval_log': [{'stage': 'Final Management Sign-off', 'level': 0,
                                      'user_id': str(self.ceo.pk)}],
                }, format='json')
                self.assertEqual(response.status_code, 201, response.data)
                pr.refresh_from_db()
                self.assertEqual(pr.status, status)
                self.assertEqual(pr.approval_workflow_config, workflow)
                self.assertNotIn('po_link_previous_status', pr.price_remarks_data)
                if status != 'draft':
                    self.assertFalse(pr.po_applicable)
                    self.assertTrue(RequisitionWorkflowService.can_approve(pr, self.buyer))

    def test_relink_native_po_does_not_convert_target_under_review(self):
        pr = self.requisition(include_employee=False)
        po = self.order(pr)
        workflow, status = deepcopy(pr.approval_workflow_config), pr.status
        response = self.client.patch(f'{BASE}orders/{po.pk}/', {'pr_reference': str(pr.pk)}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assert_route_preserved(pr, workflow, status)
        self.assertTrue(RequisitionWorkflowService.can_approve(pr, self.ceo))

    def test_deleting_one_linked_order_preserves_review_with_remaining_order(self):
        pr = self.requisition(include_employee=False)
        removed = self.order(pr, linked=True)
        remaining = PurchaseOrder.objects.create(
            po_number='RAD-PRJ-PUR-9199_2026', pr_reference=pr, vendor=self.vendor,
            title='Remaining association', total_amount=100,
        )
        workflow, status = deepcopy(pr.approval_workflow_config), pr.status
        response = self.client.delete(f'{BASE}orders/{removed.pk}/')
        self.assertEqual(response.status_code, 204, response.data)
        self.assert_route_preserved(pr, workflow, status)
        self.assertEqual(pr.po_number_reference, remaining.po_number)
        self.assertTrue(RequisitionWorkflowService.can_approve(pr, self.ceo))
