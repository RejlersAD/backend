"""One upload review precedes one atomic PO/vendor/PR save through guarded routes."""

import json
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings

from apps.procurement.models import PODocument, PurchaseOrder, PurchaseRequisition, Vendor
from apps.procurement.services.procurement_lifecycle import ProcurementDeleteConflict

from . import test_signed_po_originating_pr as originating
from . import test_signed_po_single_save as single


@override_settings(ROOT_URLCONF=originating.__name__)
class UnifiedPOPDFReviewTests(TestCase):
    def setUp(self):
        originating.SignedPOOriginatingPRTests.setUp(self)
        self.reviewed = {'summary': 'Reviewed PO source description', 'vendor_name': self.vendor.name,
                         'po_date': '2026-01-07', 'currency': 'USD'}
        self.before_files = self.files()

    upload = originating.SignedPOOriginatingPRTests.upload
    revoke = originating.SignedPOOriginatingPRTests.revoke
    assert_linked = originating.SignedPOOriginatingPRTests.assert_linked
    grant_vendor_create = single.SignedPOSingleSaveTests.grant_vendor_create

    def files(self):
        return {str(path) for path in Path(settings.MEDIA_ROOT).rglob('*') if path.is_file()}

    def save(self, **changes):
        return self.upload(reviewed_fields=json.dumps(self.reviewed), **changes)

    def assert_no_po(self):
        self.assertFalse(PurchaseOrder.objects.exists())
        self.assertFalse(PODocument.objects.exists())
        self.assertEqual(self.files(), self.before_files)

    def test_create_only_user_previews_all_supplier_fields_without_any_staged_record_then_saves(self):
        self.revoke('read')
        self.revoke('update')
        self.fields.update(seller_email='source@example.test', seller_phone='+971 1234',
                           seller_country='United Arab Emirates', seller_address='Source building',
                           seller_contact_person='Source contact', vendor_license_no='CN-1234')
        from django.core.files.uploadedfile import SimpleUploadedFile
        with patch('apps.procurement.services.po_pdf_approval.preview_signed_po_approval',
                   return_value={'approval_evidence': {'signature_detected': True}, 'page_count': 2}):
            response = self.client.post(f'{originating.BASE}po-documents/preview_signed_pdf/', {
                'file': SimpleUploadedFile('source.pdf', self.content), 'pr_id': str(self.pr.pk),
            }, format='multipart')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['bound_pr_number'], self.pr.pr_number)
        for key in ('seller_email', 'seller_phone', 'seller_country', 'seller_address',
                    'seller_contact_person', 'vendor_license_no'):
            self.assertEqual(response.data['extracted_data'][key], self.fields[key])
        self.assert_no_po()
        self.assertEqual(Vendor.objects.count(), 1)
        self.assertEqual(self.assert_linked(self.save()).title, self.reviewed['summary'])

    def test_one_save_registers_reviewed_supplier_details_and_retains_source_snapshot(self):
        self.grant_vendor_create()
        self.reviewed.update(vendor_name='Reviewed source supplier', vendor_license_no='CN-9876',
                             seller_contact_person='Reviewed Contact', seller_email='reviewed@example.test',
                             seller_phone='+971 5555', seller_address='Source Office 4', seller_country='UAE',
                             payment_terms='Net 60', payment_mode='Bank Transfer', delivery_terms='Site delivery')
        response = self.save()
        po = self.assert_linked(response)
        self.assertEqual(po.vendor.name, self.reviewed['vendor_name'])
        self.assertEqual(po.vendor.country, 'UAE')
        self.assertEqual(po.vendor.email, 'reviewed@example.test')
        self.assertEqual(po.vendor.trade_license_number, 'CN-9876')
        self.assertEqual(po.payment_terms, 'Net 60')
        self.assertFalse(po.vendor.is_icv_certified)
        source = PODocument.objects.get().extracted_data
        self.assertEqual(source['source_extracted_data']['vendor_name'], self.vendor.name)
        self.assertEqual(source['seller_country'], 'UAE')
        self.assertEqual(source['reviewed_by'], str(self.user.pk))
        self.assert_linked(self.save())
        self.assertEqual(Vendor.objects.count(), 2)
        self.assertEqual(PODocument.objects.count(), 1)
        self.assertEqual(PurchaseOrder.objects.count(), 1)

    def test_explicit_existing_supplier_reuse_does_not_need_vendor_create_or_edit_master(self):
        before = Vendor.objects.values().get(pk=self.vendor.pk)
        self.reviewed.update(vendor_id=str(self.vendor.pk), vendor_name='Reviewed spelling from PDF',
                             seller_email='different@example.test', seller_country='Different source label')
        po = self.assert_linked(self.save())
        self.assertEqual(po.vendor_id, self.vendor.pk)
        self.assertEqual(Vendor.objects.values().get(pk=self.vendor.pk), before)
        self.assertEqual(Vendor.objects.count(), 1)

    def test_invalid_inactive_or_conflicting_selected_supplier_is_rejected(self):
        self.vendor.trade_license_number = 'CN-1111'
        self.vendor.save(update_fields=['trade_license_number'])
        self.reviewed.update(vendor_id=str(self.vendor.pk), vendor_license_no='CN-2222')
        self.assertEqual(self.save().status_code, 409)
        self.assert_no_po()
        self.vendor.status = 'inactive'
        self.vendor.save(update_fields=['status'])
        self.assertEqual(self.save().status_code, 400)
        self.assert_no_po()

    def test_reviewed_save_never_returns_pending_or_leaves_orphan_files(self):
        self.fields['vendor_name'] = ''
        self.reviewed['vendor_name'] = ''
        response = self.save()
        self.assertEqual(response.status_code, 400, response.data)
        self.assert_no_po()

    def test_late_link_failure_rolls_back_vendor_order_document_and_source(self):
        self.grant_vendor_create()
        self.reviewed['vendor_name'] = 'Supplier rolled back'
        before_pr = PurchaseRequisition.objects.values().get(pk=self.pr.pk)
        with patch(f'{originating.SERVICE}._verified_origin_link', side_effect=ProcurementDeleteConflict('Link conflict.')):
            response = self.save()
        self.assertEqual(response.status_code, 409, response.data)
        self.assert_no_po()
        self.assertEqual(Vendor.objects.count(), 1)
        self.assertEqual(PurchaseRequisition.objects.values().get(pk=self.pr.pk), before_pr)

    def test_restoring_a_missing_retained_file_rolls_back_if_final_link_verification_fails(self):
        self.assert_linked(self.save())
        document = PODocument.objects.get()
        default_storage.delete(document.s3_key)
        before_document = PODocument.objects.values().get(pk=document.pk)
        before_order = PurchaseOrder.objects.values().get(pk=document.confirmed_po_id)
        before_files = self.files()
        from apps.procurement.services.signed_po_pdf_import import _verified_origin_link
        calls = []

        def verify(pr, po_id, user):
            calls.append(po_id)
            if len(calls) == 2:
                raise ProcurementDeleteConflict('Final link verification failed.')
            return _verified_origin_link(pr, po_id, user)

        with patch(f'{originating.SERVICE}._verified_origin_link', side_effect=verify):
            response = self.save()
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(len(calls), 2)
        self.assertEqual(PODocument.objects.values().get(pk=document.pk), before_document)
        self.assertEqual(PurchaseOrder.objects.values().get(pk=document.confirmed_po_id), before_order)
        self.assertEqual(self.files(), before_files)

    def test_review_does_not_override_source_approvals_or_accept_invalid_contact(self):
        for values in ({'signature_verified': True}, {'seller_email': 'invalid email'},
                       {'payment_terms': 'x' * 301}, {'delivery_terms': 'x' * 201}):
            with self.subTest(values=values):
                self.reviewed.update(values)
                self.assertEqual(self.save().status_code, 400)
                self.assert_no_po()
                for key in values:
                    self.reviewed.pop(key)

    def test_reviewed_pr_selection_is_bound_and_conflicting_top_level_context_is_rejected(self):
        other = PurchaseRequisition.objects.create(pr_number='RAD-PRJ-PR-0072_2026')
        self.reviewed['pr_id'] = str(other.pk)
        self.assertEqual(self.save().status_code, 409)
        self.assert_no_po()
        self.reviewed['pr_id'] = str(self.pr.pk)
        self.assert_linked(self.save(pr_id=''))

    def test_standalone_reviewed_source_is_not_replaced_by_auto_matched_pr_words(self):
        self.pr.po_number_reference = self.fields['po_number']
        self.pr.save(update_fields=['po_number_reference'])
        response = self.save(pr_id='')
        self.assertEqual(response.status_code, 200, response.data)
        po = PurchaseOrder.objects.get()
        self.assertEqual(po.pr_reference_id, self.pr.pk)
        self.assertEqual(po.title, self.reviewed['summary'])

    def test_legacy_staged_reconcile_route_loads_shared_link_helper_and_completes(self):
        self.fields.update(extraction_truncated=True, source_page_count=6)
        uploaded = self.upload()
        self.assertIsNone(uploaded.data['purchase_order_id'])
        response = self.client.post(f"{originating.BASE}po-documents/{uploaded.data['document_id']}/reconcile/", {
            'pr_id': str(self.pr.pk), 'vendor_id': str(self.vendor.pk), 'reviewed_fields': self.reviewed,
        }, format='json')
        self.assert_linked(response)
