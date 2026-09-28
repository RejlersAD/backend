"""Preserve complete printed PO identities through reviewed PDF imports.

Extraction fixtures mock text returned by OCR; they do not certify live OCR.
API fixtures reuse the guarded originating-PR setup without inheriting its tests.
"""

from datetime import date
import json
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings

from apps.procurement.models import PODocument, PurchaseOrder, PurchaseRequisition
from apps.procurement.services.signed_po_pdf_import import SignedPOImportError, extract_signed_po_fields

from . import test_signed_po_originating_pr as originating


NUMBER = 'RAD-PRJ-PUR-0085_JUL2026'
LEGACY_NUMBER = 'RAD-PRJ-PUR-0085_2026'
SERVICE = 'apps.procurement.services.signed_po_pdf_import'


class SignedPONumberExtractionTests(SimpleTestCase):
    def extract(self, printed, filename='original-po.pdf'):
        text = (
            f'PURCHASE ORDER\n{printed}\n7 July 2026\n'
            'Purchase Summary: Engineering equipment\n'
            'Total Purchase Price: 100.00 USD\nVAT (0%): 0.00 USD\n'
            'Total Sum: 100.00 USD\n'
        )
        with patch(f'{SERVICE}.extract_text_from_pdf_tesseract', return_value=text):
            return extract_signed_po_fields(b'%PDF-number-regression', filename)

    def test_printed_month_survives_and_takes_priority_over_different_filename(self):
        fields = self.extract(NUMBER, 'RAD-PRJ-PUR-9999_AUG2026.pdf')
        self.assertEqual(fields['source_po_number'], NUMBER)
        self.assertEqual(fields['po_number'], NUMBER)

    def test_year_only_number_is_read_from_body_instead_of_filename(self):
        fields = self.extract(LEGACY_NUMBER, 'RAD-PRJ-PUR-9999_AUG2026.pdf')
        self.assertEqual(fields['source_po_number'], LEGACY_NUMBER)
        self.assertEqual(fields['po_number'], LEGACY_NUMBER)

    def test_longer_sequence_is_preserved(self):
        number = 'RAD-PRJ-PUR-10085_JUL2026'
        fields = self.extract(number)
        self.assertEqual(fields['source_po_number'], number)
        self.assertEqual(fields['po_number'], number)

    def test_whitespace_after_underscore_does_not_drop_month(self):
        fields = self.extract('RAD-PRJ-PUR-0085_\n JUL2026')
        self.assertEqual(fields['source_po_number'], NUMBER)
        self.assertEqual(fields['po_number'], NUMBER)

    def test_supported_filename_retains_month_when_body_has_no_number(self):
        for filename in (NUMBER + '.pdf', 'Signed ' + NUMBER + ' (1).pdf'):
            with self.subTest(filename=filename):
                fields = self.extract('Order reference unavailable', filename)
                self.assertEqual(fields['po_number'], NUMBER)

    def test_invalid_month_or_trailing_identifier_is_not_partially_accepted(self):
        for printed in (
            'RAD-PRJ-PUR-0085_JULL2026',
            'RAD-PRJ-PUR-0085_XYZ2026',
            'RAD-PRJ-PUR-0085_JUL2026REV',
            'RAD-PRJ-PUR-0085_JUL2026_REV',
            'RAD-PRJ-PUR-0085_JUL2026-REV',
        ):
            with self.subTest(printed=printed), self.assertRaises(SignedPOImportError):
                self.extract(printed)


@override_settings(ROOT_URLCONF=originating.__name__)
class SignedPONumberPreservationTests(TestCase):
    def setUp(self):
        originating.SignedPOOriginatingPRTests.setUp(self)
        self.fields.update(source_po_number=NUMBER, po_number=NUMBER, po_date=date(2026, 7, 7))

    upload = originating.SignedPOOriginatingPRTests.upload
    revoke = originating.SignedPOOriginatingPRTests.revoke
    order = originating.SignedPOOriginatingPRTests.order
    assert_linked = originating.SignedPOOriginatingPRTests.assert_linked

    def pending_document(self):
        self.fields.update(extraction_truncated=True, source_page_count=6)
        response = self.upload()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['operation'], 'uploaded')
        self.assertFalse(PurchaseOrder.objects.exists())
        return PODocument.objects.get(pk=response.data['document_id'])

    def reconcile(self, document, **changes):
        data = {'vendor_id': str(self.vendor.pk), 'pr_id': str(self.pr.pk)}
        data.update(changes)
        return self.client.post(
            f'{originating.BASE}po-documents/{document.pk}/reconcile/', data, format='json',
        )

    def test_preview_keeps_complete_number_and_writes_no_source_or_order(self):
        with (
            patch('apps.procurement.services.po_pdf_approval.preview_signed_po_approval',
                  return_value={'approval_evidence': {}}),
            patch(f'{SERVICE}.default_storage.save') as store,
        ):
            response = self.client.post(f'{originating.BASE}po-documents/preview_signed_pdf/', {
                'file': SimpleUploadedFile('original.pdf', self.content, content_type='application/pdf'),
                'pr_id': str(self.pr.pk),
            }, format='multipart')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['extracted_data']['po_number'], NUMBER)
        self.assertEqual(response.data['extracted_data']['source_po_number'], NUMBER)
        self.assertTrue(response.data['preview_only'])
        store.assert_not_called()
        self.assertFalse(PurchaseOrder.objects.exists())
        self.assertFalse(PODocument.objects.exists())

    def test_reviewed_save_and_retry_keep_exact_number_and_one_linked_source(self):
        reviewed = json.dumps({'po_number': NUMBER, 'summary': 'Reviewed source title'})
        first = self.upload(reviewed_fields=reviewed)
        order = self.assert_linked(first)
        self.assertEqual(order.po_number, NUMBER)
        self.assertEqual(order.marking, 'RAD-PRJ-PUR-0085')
        self.assertEqual(first.data['po_number'], NUMBER)
        document = PODocument.objects.get()
        self.assertEqual(document.extracted_data['po_number'], NUMBER)
        self.assertEqual(document.extracted_data['source_po_number'], NUMBER)
        self.assertEqual(document.confirmed_po_id, order.pk)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.po_number_reference, NUMBER)
        repeated = self.upload(reviewed_fields=reviewed)
        self.assertEqual(self.assert_linked(repeated).pk, order.pk)
        self.assertEqual(repeated.data['po_number'], NUMBER)
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        self.assertEqual(PODocument.objects.count(), 1)
        order.refresh_from_db()
        sources = [entry for entry in order.attachments if entry.get('type') == 'signed_purchase_order_pdf']
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]['source_po_number'], NUMBER)

    def test_staged_review_reconcile_and_retry_keep_exact_number(self):
        document = self.pending_document()
        reviewed = self.client.patch(f'{originating.BASE}po-documents/{document.pk}/', {
            'po_number': NUMBER, 'summary': 'Reviewed staged title',
        }, format='json')
        self.assertEqual(reviewed.status_code, 200, reviewed.data)
        document.refresh_from_db()
        self.assertEqual(document.extracted_data['po_number'], NUMBER)
        first = self.reconcile(document)
        order = self.assert_linked(first)
        self.assertEqual(order.po_number, NUMBER)
        self.assertEqual(order.title, 'Reviewed staged title')
        repeated = self.reconcile(document)
        self.assertEqual(self.assert_linked(repeated).pk, order.pk)
        self.assertEqual(repeated.data['po_number'], NUMBER)
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        self.assertEqual(PODocument.objects.count(), 1)

    def test_reviewed_reconciliation_requires_update_permission_without_writes(self):
        document = self.pending_document()
        before = PODocument.objects.values().get(pk=document.pk)
        self.revoke('update')
        response = self.reconcile(document, reviewed_fields={'po_number': NUMBER})
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(PODocument.objects.values().get(pk=document.pk), before)
        self.assertFalse(PurchaseOrder.objects.exists())

    def test_invalid_reviewed_month_rejects_without_partial_save(self):
        response = self.upload(reviewed_fields=json.dumps({'po_number': 'RAD-PRJ-PUR-0085_JULL2026'}))
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(PurchaseOrder.objects.exists())
        self.assertFalse(PODocument.objects.exists())
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'approved')

    def test_existing_exact_order_requires_update_permission(self):
        order = self.order()
        self.revoke('update')
        response = self.upload()
        self.assertEqual(response.status_code, 403, response.data)
        order.refresh_from_db()
        self.assertEqual(order.po_number, NUMBER)
        self.assertIsNone(order.pr_reference_id)
        self.assertEqual(order.attachments, [])
        self.assertFalse(PODocument.objects.exists())

    def test_legacy_year_only_alias_without_matching_source_evidence_is_rejected(self):
        order = self.order(po_number=LEGACY_NUMBER)
        before_pr = PurchaseRequisition.objects.values().get(pk=self.pr.pk)
        for pr_id in (str(self.pr.pk), ''):
            with self.subTest(pr_id=pr_id):
                response = self.upload(pr_id=pr_id)
                self.assertEqual(response.status_code, 409, response.data)
        order.refresh_from_db()
        self.assertEqual(order.po_number, LEGACY_NUMBER)
        self.assertEqual(order.attachments, [])
        self.assertIsNone(order.pr_reference_id)
        self.assertEqual(PurchaseRequisition.objects.values().get(pk=self.pr.pk), before_pr)
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        self.assertFalse(PODocument.objects.exists())

    def test_legacy_alias_with_matching_retained_source_reuses_saved_identity(self):
        order = self.order(po_number=LEGACY_NUMBER, attachments=[{
            'type': 'signed_purchase_order_pdf', 'source_po_number': NUMBER,
        }])
        response = self.upload()
        saved = self.assert_linked(response)
        self.assertEqual(saved.pk, order.pk)
        self.assertEqual(saved.po_number, LEGACY_NUMBER)
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        document = PODocument.objects.get()
        self.assertEqual(document.extracted_data['source_po_number'], NUMBER)
        self.assertEqual(document.confirmed_po_id, order.pk)

    def test_legacy_alias_with_different_source_month_is_rejected(self):
        source = {'type': 'signed_purchase_order_pdf', 'source_po_number': 'RAD-PRJ-PUR-0085_JUN2026'}
        order = self.order(po_number=LEGACY_NUMBER, attachments=[source])
        for pr_id in (str(self.pr.pk), ''):
            with self.subTest(pr_id=pr_id):
                response = self.upload(pr_id=pr_id)
                self.assertEqual(response.status_code, 409, response.data)
        order.refresh_from_db()
        self.assertEqual(order.attachments, [source])
        self.assertIsNone(order.pr_reference_id)
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        self.assertFalse(PODocument.objects.exists())

    def test_multiple_saved_number_aliases_reject_without_new_source(self):
        self.order()
        self.order(po_number=LEGACY_NUMBER, attachments=[{
            'type': 'signed_purchase_order_pdf', 'source_po_number': NUMBER,
        }])
        for pr_id in (str(self.pr.pk), ''):
            with self.subTest(pr_id=pr_id):
                response = self.upload(pr_id=pr_id)
                self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(PurchaseOrder.objects.count(), 2)
        self.assertFalse(PODocument.objects.exists())

    def test_legacy_alias_created_after_staging_is_not_silently_reconciled(self):
        document = self.pending_document()
        before = PODocument.objects.values().get(pk=document.pk)
        self.order(po_number=LEGACY_NUMBER)
        response = self.reconcile(document)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(PODocument.objects.values().get(pk=document.pk), before)
        self.assertEqual(PurchaseOrder.objects.count(), 1)
