"""One reviewed Save PO registers a source supplier and commits the linked order."""

from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.procurement.models import PODocument, PurchaseOrder, PurchaseRequisition, Vendor
from apps.rbac.models import Module, Permission, RoleModule, RolePermission
from apps.rbac.module_actions import ensure_module_actions

from . import test_signed_po_originating_pr as originating


@override_settings(ROOT_URLCONF=originating.__name__)
class SignedPOSingleSaveTests(TestCase):
    def setUp(self):
        originating.SignedPOOriginatingPRTests.setUp(self)

    upload = originating.SignedPOOriginatingPRTests.upload
    revoke = originating.SignedPOOriginatingPRTests.revoke
    order = originating.SignedPOOriginatingPRTests.order
    assert_linked = originating.SignedPOOriginatingPRTests.assert_linked

    def grant_vendor_create(self):
        module, _ = Module.objects.get_or_create(code='procurement_vendors', defaults={'name': 'Vendors'})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        RoleModule.objects.get_or_create(role=self.role, module=module)
        for permission in module.permissions.filter(action='create', is_active=True):
            RolePermission.objects.get_or_create(role=self.role, permission=permission)
        cache.clear()

    def pending(self, *, supplier='New source supplier'):
        self.fields.update(vendor_name=supplier, extraction_truncated=True, source_page_count=6)
        response = self.upload()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['operation'], 'uploaded')
        self.assertIsNone(response.data['purchase_order_id'])
        return PODocument.objects.get(pk=response.data['document_id'])

    def save_po(self, document, *, reviewed_fields=None, **mapping):
        data = {'pr_id': str(self.pr.pk), 'vendor_id': None,
                'reviewed_fields': reviewed_fields if reviewed_fields is not None else {
                    'summary': 'Reviewed PO description', 'vendor_name': 'New source supplier',
                    'vat_basis': 'none', 'entered_amount': '125.00',
                }}
        data.update(mapping)
        return self.client.post(f'{originating.BASE}po-documents/{document.pk}/reconcile/', data, format='json')

    def test_initial_import_registers_source_supplier_and_retry_reuses_it(self):
        self.grant_vendor_create()
        self.fields.update(vendor_name='New source supplier', vendor_license_no='CN-12345',
                           seller_contact_person='Supplier Contact', seller_email='seller@example.test',
                           seller_phone='+971 555 1234', seller_address='Supplier building, Abu Dhabi')
        response = self.upload()
        order = self.assert_linked(response)
        self.assertTrue(response.data['vendor_registered'])
        vendor = order.vendor
        self.assertEqual(vendor.name, self.fields['vendor_name'])
        self.assertEqual(vendor.trade_license_number, 'CN-12345')
        self.assertEqual(vendor.email, 'seller@example.test')
        self.assertEqual(vendor.address, self.fields['seller_address'])
        self.assertEqual(vendor.created_by_id, self.user.pk)
        self.assertFalse(vendor.adnoc_approved)
        self.assertFalse(vendor.is_icv_certified)
        self.assertEqual(vendor.icv_issuing_authority, '')
        self.assertEqual(vendor.certifications, [])
        self.assertIsNone(vendor.rating)
        self.assert_linked(self.upload())
        self.assertEqual(Vendor.objects.count(), 2)
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        self.assertEqual(PODocument.objects.count(), 1)

    def test_missing_vendor_create_permission_rejects_without_partial_registration(self):
        self.fields['vendor_name'] = 'New source supplier'
        before = PurchaseRequisition.objects.values().get(pk=self.pr.pk)
        response = self.upload()
        self.assertEqual(response.status_code, 403, response.data)
        self.assertIn('Vendor create permission', str(response.data))
        self.assertEqual(Vendor.objects.count(), 1)
        self.assertFalse(PurchaseOrder.objects.exists())
        self.assertFalse(PODocument.objects.exists())
        self.assertEqual(PurchaseRequisition.objects.values().get(pk=self.pr.pk), before)

    def test_existing_supplier_name_reuse_needs_no_vendor_create_and_preserves_master(self):
        before = Vendor.objects.values().get(pk=self.vendor.pk)
        self.fields['vendor_name'] = '  ORIGINAL   PDF, SUPPLIER  '
        order = self.assert_linked(self.upload())
        self.assertEqual(order.vendor_id, self.vendor.pk)
        self.assertEqual(Vendor.objects.values().get(pk=self.vendor.pk), before)
        self.assertEqual(Vendor.objects.count(), 1)

    def test_matching_license_reuses_master_without_copying_source_over_it(self):
        self.vendor.trade_license_number = 'CN-12345'
        self.vendor.save(update_fields=['trade_license_number'])
        before = Vendor.objects.values().get(pk=self.vendor.pk)
        self.fields.update(vendor_name='Alternate source name', vendor_license_no='cn 12345')
        self.assertEqual(self.assert_linked(self.upload()).vendor_id, self.vendor.pk)
        self.assertEqual(Vendor.objects.values().get(pk=self.vendor.pk), before)

    def test_inactive_ambiguous_and_conflicting_supplier_matches_do_not_create_clones(self):
        self.grant_vendor_create()
        for status in ('inactive', 'blacklisted'):
            self.vendor.status = status
            self.vendor.save(update_fields=['status'])
            self.assertEqual(self.upload().status_code, 409)
        self.vendor.status = 'active'
        self.vendor.trade_license_number = 'CN-11111'
        self.vendor.save(update_fields=['status', 'trade_license_number'])
        self.fields['vendor_license_no'] = 'CN-22222'
        self.assertEqual(self.upload().status_code, 409)
        self.fields['vendor_license_no'] = ''
        duplicate = Vendor.objects.create(vendor_code='AMBIGUOUS', name=self.vendor.name)
        self.assertEqual(self.upload().status_code, 409)
        self.assertEqual(Vendor.objects.count(), 2)
        duplicate.delete()
        self.assertFalse(PurchaseOrder.objects.exists())
        self.assertFalse(PODocument.objects.exists())

    def test_one_save_registers_vendor_applies_review_and_links_pr_without_patch(self):
        self.grant_vendor_create()
        document = self.pending()
        self.assertEqual(Vendor.objects.count(), 1)
        response = self.save_po(document)
        order = self.assert_linked(response)
        self.assertTrue(response.data['vendor_registered'])
        self.assertEqual(order.title, 'Reviewed PO description')
        self.assertEqual(order.net_amount, Decimal('125.00'))
        self.assertEqual(order.vendor.name, 'New source supplier')
        document.refresh_from_db()
        self.assertEqual(document.confirmed_po_id, order.pk)
        self.assertEqual(document.extracted_data['summary'], order.title)
        self.assertEqual(document.extracted_data['source_extracted_data']['summary'], self.fields['summary'])
        self.assertEqual(document.extracted_data['originating_pr_id'], str(self.pr.pk))
        self.assertTrue(document.extracted_data['signature_verified'])
        self.assertTrue(document.extracted_data['vendor_match']['matched'])
        self.assert_linked(self.save_po(document))
        self.assertEqual(Vendor.objects.count(), 2)
        self.assertEqual(PurchaseOrder.objects.count(), 1)

    def test_failed_single_save_rolls_back_review_and_new_vendor(self):
        self.grant_vendor_create()
        document = self.pending()
        before = PODocument.objects.values().get(pk=document.pk)
        response = self.save_po(document, reviewed_fields={
            'vendor_name': 'New source supplier', 'summary': '',
        })
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(PODocument.objects.values().get(pk=document.pk), before)
        self.assertEqual(Vendor.objects.count(), 1)
        self.assertFalse(PurchaseOrder.objects.exists())
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'approved')

    def test_final_attachment_failure_rolls_back_vendor_order_review_and_pr_link(self):
        self.grant_vendor_create()
        document = self.pending()
        before_document = PODocument.objects.values().get(pk=document.pk)
        before_pr = PurchaseRequisition.objects.values().get(pk=self.pr.pk)
        from rest_framework.exceptions import ValidationError
        with patch('apps.procurement.services.po_document_reconciliation._attach_existing_order',
                   side_effect=ValidationError({'error': 'Source evidence could not be attached.'})):
            response = self.save_po(document)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(PODocument.objects.values().get(pk=document.pk), before_document)
        self.assertEqual(PurchaseRequisition.objects.values().get(pk=self.pr.pk), before_pr)
        self.assertEqual(Vendor.objects.count(), 1)
        self.assertFalse(PurchaseOrder.objects.exists())

    def test_single_save_enforces_vendor_create_and_order_update_permissions(self):
        document = self.pending()
        before = PODocument.objects.values().get(pk=document.pk)
        self.assertEqual(self.save_po(document).status_code, 403)
        self.grant_vendor_create()
        self.revoke('update')
        self.assertEqual(self.save_po(document).status_code, 403)
        self.assertEqual(PODocument.objects.values().get(pk=document.pk), before)
        self.assertEqual(Vendor.objects.count(), 1)
        self.assertFalse(PurchaseOrder.objects.exists())

    def test_single_save_reuses_supplier_without_vendor_create_permission(self):
        document = self.pending(supplier=self.vendor.name)
        response = self.save_po(document, reviewed_fields={'summary': 'One save', 'vendor_name': self.vendor.name})
        self.assertEqual(self.assert_linked(response).vendor_id, self.vendor.pk)
        self.assertFalse(response.data['vendor_registered'])

    def test_single_save_preserves_original_pr_and_rejects_approval_tampering(self):
        document = self.pending(supplier=self.vendor.name)
        other = PurchaseRequisition.objects.create(pr_number='RAD-PRJ-PR-0072_2026')
        response = self.save_po(document, reviewed_fields={'pr_id': str(other.pk)}, vendor_id=str(self.vendor.pk))
        self.assertEqual(response.status_code, 409, response.data)
        response = self.save_po(document, reviewed_fields={'signature_verified': True}, vendor_id=str(self.vendor.pk))
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(PurchaseOrder.objects.exists())

    def test_missing_supplier_name_requires_real_name_without_fabricating_vendor(self):
        self.grant_vendor_create()
        document = self.pending(supplier='')
        response = self.save_po(document, reviewed_fields={'summary': 'Reviewed description', 'vendor_name': ''})
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('vendor_name', response.data)
        self.assertEqual(Vendor.objects.count(), 1)
        self.assertFalse(PurchaseOrder.objects.exists())
