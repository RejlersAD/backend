"""Signed PO uploads launched from a PR retain that association through review."""

from copy import deepcopy
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.test import APIClient

from apps.procurement.models import PODocument, PurchaseOrder, PurchaseRequisition, Vendor
from apps.procurement.services.signed_po_pdf_import import SignedPOImportError
from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, RolePermission, UserProfile, UserRole
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints


urlpatterns = [path('api/v1/procurement/', include('apps.procurement.urls'))]
secure_module_endpoints(urlpatterns)
BASE = '/api/v1/procurement/'
SERVICE = 'apps.procurement.services.signed_po_pdf_import'


@override_settings(ROOT_URLCONF=__name__)
class SignedPOOriginatingPRTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user('origin-po', email='origin-po@example.test')
        organization = Organization.objects.create(code='origin-po', name='Origin PO tests')
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'organization': organization})
        profile.roles.clear()
        self.role = Role.objects.create(code='origin-po', name='Origin PO buyer', level=3)
        UserRole.objects.create(user_profile=profile, role=self.role)
        module, _ = Module.objects.get_or_create(code='procurement_orders', defaults={'name': 'Orders'})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        RoleModule.objects.create(role=self.role, module=module)
        for permission in module.permissions.filter(action__in=['read', 'create', 'update'], is_active=True):
            RolePermission.objects.create(role=self.role, permission=permission)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.vendor = Vendor.objects.create(vendor_code='ORIGIN-PO', name='Original PDF supplier', status='active')
        self.pr = PurchaseRequisition.objects.create(
            pr_number='RAD-PRJ-PR-0071_2026', status='approved', product_service='PR wording',
            total_price='900.00', currency='AED', price_remarks_data={'payment_terms': 'Keep PR terms'},
        )
        self.content = b'%PDF-1.4 originating PR original'
        self.fields = {
            'source_po_number': 'RAD-PRJ-PUR-0088_JAN2026', 'po_number': 'RAD-PRJ-PUR-0088_2026',
            'source_pr_numbers': [self.pr.pr_number], 'source_page_count': 2, 'extracted_page_count': 2,
            'extraction_truncated': False, 'po_date': date(2026, 1, 7), 'vendor_name': self.vendor.name,
            'vendor_license_no': '', 'seller_reference': '', 'quote_ref': 'Original quote',
            'project_number': '', 'summary': 'Original PDF description', 'payment_terms': 'Net 45',
            'payment_mode': 'Bank Transfer', 'delivery_terms': '', 'expected_delivery': None,
            'total_amount': Decimal('100.00'), 'tax_amount': Decimal('0.00'),
            'gross_amount': Decimal('100.00'), 'currency': 'USD', 'items': [],
        }
        extractor = patch(f'{SERVICE}.extract_signed_po_fields', side_effect=lambda *args: deepcopy(self.fields))
        self.extractor = extractor.start()
        self.addCleanup(extractor.stop)

    def upload(self, **changes):
        data = {'file': SimpleUploadedFile('original-po.pdf', self.content, content_type='application/pdf'),
                'pr_id': str(self.pr.pk), 'signature_verified': 'true', 'stamp_verified': 'true',
                'approved_by_name': 'Source Approver', 'approved_date': '2026-01-07'}
        data.update(changes)
        return self.client.post(f'{BASE}po-documents/import_signed_pdf/', data, format='multipart')

    def revoke(self, action):
        RolePermission.objects.filter(role=self.role, permission__action=action).delete()
        cache.clear()

    def order(self, **changes):
        values = {'po_number': self.fields['po_number'], 'vendor': self.vendor,
                  'title': 'Existing title', 'total_amount': '100.00', 'currency': 'USD'}
        values.update(changes)
        return PurchaseOrder.objects.create(**values)

    def assert_linked(self, response):
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['pr_id'], str(self.pr.pk))
        self.assertEqual(response.data['pr_number'], self.pr.pr_number)
        self.assertFalse(response.data['po_link']['manual_link_required'])
        order = PurchaseOrder.objects.get(pk=response.data['purchase_order_id'])
        self.assertEqual(order.pr_reference_id, self.pr.pk)
        return order

    def test_selected_pr_creates_linked_po_with_original_commercial_values_and_create_permission(self):
        self.revoke('update')
        other_vendor = Vendor.objects.create(vendor_code='PR-VENDOR', name='Different PR supplier')
        self.pr.vendor = other_vendor
        self.pr.save(update_fields=['vendor'])
        response = self.upload()
        order = self.assert_linked(response)
        self.assertEqual(order.title, self.fields['summary'])
        self.assertEqual(order.vendor_id, self.vendor.pk)
        self.assertEqual(order.total_amount, Decimal('100.00'))
        self.assertEqual(order.currency, 'USD')
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.total_price, Decimal('900.00'))
        self.assertEqual(self.pr.currency, 'AED')
        self.assertEqual(self.pr.status, 'converted')
        self.assertEqual(self.pr.po_number_reference, order.po_number)
        self.assertEqual(self.pr.price_remarks_data['payment_terms'], 'Keep PR terms')
        self.assertEqual(self.pr.price_remarks_data['po_link']['po_id'], str(order.pk))
        document = PODocument.objects.get(pk=response.data['document_id'])
        self.assertEqual(document.extracted_data['originating_pr_id'], str(self.pr.pk))
        self.assertEqual(document.confirmed_po_id, order.pk)
        with default_storage.open(document.s3_key, 'rb') as source:
            self.assertEqual(source.read(), self.content)

    def test_selected_draft_is_linked_without_being_approved_or_converted(self):
        self.pr.status = 'draft'
        self.pr.save(update_fields=['status'])
        self.assert_linked(self.upload())
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'draft')

    def test_existing_completed_order_can_link_and_retain_commercial_fields(self):
        existing = self.order(status='completed', total_amount='95.00', currency='AED')
        response = self.upload()
        order = self.assert_linked(response)
        self.assertEqual(order.pk, existing.pk)
        self.assertEqual(order.status, 'completed')
        self.assertEqual(order.title, 'Existing title')
        self.assertEqual(order.total_amount, Decimal('95.00'))
        self.assertEqual(order.currency, 'AED')
        self.assertTrue(response.data['reconciliation_required'])

    def test_existing_po_requires_update_permission_before_link_or_source_changes(self):
        order = self.order()
        self.revoke('update')
        response = self.upload()
        self.assertEqual(response.status_code, 403, response.data)
        order.refresh_from_db()
        self.assertIsNone(order.pr_reference_id)
        self.assertEqual(order.attachments, [])
        self.assertFalse(PODocument.objects.exists())

    def test_missing_create_permission_rejects_before_extraction(self):
        self.revoke('create')
        self.assertEqual(self.upload().status_code, 403)
        self.extractor.assert_not_called()

    def test_invalid_and_missing_originating_pr_reject_without_saving(self):
        for identity in ('invalid-id', str(uuid4())):
            with self.subTest(identity=identity):
                self.assertEqual(self.upload(pr_id=identity).status_code, 400)
        self.assertFalse(PurchaseOrder.objects.exists())
        self.assertFalse(PODocument.objects.exists())

    def test_explicit_other_pr_source_and_existing_links_reject_before_storage(self):
        other = PurchaseRequisition.objects.create(pr_number='RAD-PRJ-PR-0072_2026')
        with patch(f'{SERVICE}.default_storage.save') as save:
            self.fields['source_pr_numbers'] = [other.pr_number]
            self.assertEqual(self.upload().status_code, 409)
            self.fields['source_pr_numbers'] = [self.pr.pr_number]
            order = self.order(pr_reference=other)
            self.assertEqual(self.upload().status_code, 409)
            order.delete()
            self.order(po_number='RAD-PRJ-PUR-0099_2026', pr_reference=self.pr)
            self.assertEqual(self.upload().status_code, 409)
            save.assert_not_called()
        self.assertFalse(PODocument.objects.exists())

    def test_retained_origin_cannot_be_replaced_by_another_upload_context(self):
        self.fields.update(extraction_truncated=True, source_page_count=6)
        first = self.upload()
        self.assertEqual(first.data['operation'], 'uploaded')
        other = PurchaseRequisition.objects.create(pr_number='RAD-PRJ-PR-0072_2026')
        self.fields['source_pr_numbers'] = []
        self.assertEqual(self.upload(pr_id=str(other.pk)).status_code, 409)
        document = PODocument.objects.get()
        self.assertEqual(document.extracted_data['originating_pr_id'], str(self.pr.pk))
        self.assertFalse(PurchaseOrder.objects.exists())

    def test_pending_review_and_reconcile_preserve_origin_and_return_verified_link(self):
        self.fields.update(extraction_truncated=True, source_page_count=6)
        response = self.upload()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['operation'], 'uploaded')
        self.assertIsNone(response.data['purchase_order_id'])
        self.assertEqual(response.data['pr_id'], str(self.pr.pk))
        url = f"{BASE}po-documents/{response.data['document_id']}/"
        other = PurchaseRequisition.objects.create(pr_number='RAD-PRJ-PR-0072_2026')
        for value in (None, str(other.pk)):
            self.assertEqual(self.client.patch(url, {'pr_id': value}, format='json').status_code, 409)
        self.assertEqual(self.client.post(url + 'reconcile/', {
            'vendor_id': str(self.vendor.pk), 'pr_id': str(other.pk)}, format='json').status_code, 409)
        saved = self.client.patch(url, {'summary': 'Reviewed source title', 'pr_id': str(self.pr.pk)}, format='json')
        self.assertEqual(saved.status_code, 200, saved.data)
        reconciled = self.client.post(url + 'reconcile/', {'vendor_id': str(self.vendor.pk)}, format='json')
        order = self.assert_linked(reconciled)
        self.assertEqual(order.title, 'Reviewed source title')
        repeated = self.client.post(url + 'reconcile/', {'vendor_id': str(self.vendor.pk)}, format='json')
        self.assert_linked(repeated)
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        self.assertEqual(PODocument.objects.count(), 1)

    def test_deferred_reconcile_rejects_competing_link_created_since_upload(self):
        self.fields.update(extraction_truncated=True, source_page_count=6)
        uploaded = self.upload()
        self.order(po_number='RAD-PRJ-PUR-0099_2026', pr_reference=self.pr)
        result = self.client.post(f"{BASE}po-documents/{uploaded.data['document_id']}/reconcile/",
                                 {'vendor_id': str(self.vendor.pk)}, format='json')
        self.assertEqual(result.status_code, 409, result.data)
        self.assertIsNone(PODocument.objects.get().confirmed_po_id)
        self.assertEqual(PurchaseOrder.objects.count(), 1)

    def complete_deferred_draft(self, *, existing=False):
        self.pr.status = 'draft'
        self.pr.save(update_fields=['status'])
        self.fields.update(extraction_truncated=True, source_page_count=6)
        if existing:
            self.order(status='completed')
        uploaded = self.upload()
        self.assertEqual(uploaded.data['operation'], 'uploaded')
        result = self.client.post(f"{BASE}po-documents/{uploaded.data['document_id']}/reconcile/",
                                 {'vendor_id': str(self.vendor.pk)}, format='json')
        self.assert_linked(result)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'draft')
        self.assertNotIn('po_link_previous_status', self.pr.price_remarks_data)

    def test_deferred_new_order_does_not_convert_or_approve_draft_pr(self):
        self.complete_deferred_draft()

    def test_deferred_existing_order_does_not_convert_or_approve_draft_pr(self):
        self.complete_deferred_draft(existing=True)

    def test_reupload_keeps_pending_reviewed_fields_vat_and_signed_evidence(self):
        self.fields.update(extraction_truncated=True, source_page_count=6)
        uploaded = self.upload()
        url = f"{BASE}po-documents/{uploaded.data['document_id']}/"
        reviewed = self.client.patch(url, {
            'summary': 'Corrected source title', 'vat_basis': 'none', 'entered_amount': '123.00',
        }, format='json')
        self.assertEqual(reviewed.status_code, 200, reviewed.data)
        before = PODocument.objects.get().extracted_data
        self.fields['summary'] = 'New OCR must not replace the review'
        with patch(f'{SERVICE}.default_storage.save') as save:
            repeated = self.upload(signature_verified='false', stamp_verified='false')
            save.assert_not_called()
        self.assertEqual(repeated.status_code, 200, repeated.data)
        self.assertEqual(repeated.data['operation'], 'uploaded')
        self.assertEqual(repeated.data['document_id'], uploaded.data['document_id'])
        self.assertEqual(PODocument.objects.get().extracted_data, before)
        self.assertTrue(repeated.data['signature_verified'])
        completed = self.client.post(url + 'reconcile/', {'vendor_id': str(self.vendor.pk)}, format='json')
        order = self.assert_linked(completed)
        self.assertEqual(order.title, 'Corrected source title')
        self.assertEqual(order.net_amount, Decimal('123.00'))

    def test_completion_between_document_reads_returns_verified_link(self):
        from django.shortcuts import get_object_or_404

        self.fields.update(extraction_truncated=True, source_page_count=6)
        uploaded = self.upload()
        order = self.order(pr_reference=self.pr)

        def observe_completion(queryset, *args, **kwargs):
            if getattr(queryset, 'model', None) is PODocument:
                PODocument.objects.filter(pk=uploaded.data['document_id']).update(confirmed_po=order)
            return get_object_or_404(queryset, *args, **kwargs)

        with patch('apps.procurement.services.po_document_reconciliation.get_object_or_404',
                   side_effect=observe_completion):
            response = self.client.post(f"{BASE}po-documents/{uploaded.data['document_id']}/reconcile/",
                                        {'vendor_id': str(self.vendor.pk)}, format='json')
        self.assert_linked(response)
        self.assertEqual(response.data['operation'], 'already_reconciled')

    def test_attach_failure_rolls_back_selected_link_and_pr_metadata(self):
        order = self.order(status='completed')
        before = PurchaseRequisition.objects.values().get(pk=self.pr.pk)
        with patch(f'{SERVICE}._attach_existing_order', side_effect=SignedPOImportError('Source could not be retained.')):
            response = self.upload()
        self.assertEqual(response.status_code, 400)
        order.refresh_from_db()
        self.assertIsNone(order.pr_reference_id)
        self.assertEqual(PurchaseRequisition.objects.values().get(pk=self.pr.pk), before)
        self.assertFalse(PODocument.objects.exists())
