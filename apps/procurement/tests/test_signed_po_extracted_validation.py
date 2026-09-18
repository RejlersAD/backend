"""OCR text must fit the order schema before any source or vendor is saved."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.procurement.models import PODocument, PurchaseOrder, Vendor

from . import test_signed_po_originating_pr as originating
from . import test_signed_po_single_save as single


@override_settings(ROOT_URLCONF=originating.__name__)
class SignedPOExtractedValidationTests(TestCase):
    def setUp(self):
        originating.SignedPOOriginatingPRTests.setUp(self)
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        storage_settings = override_settings(MEDIA_ROOT=self.media.name)
        storage_settings.enable()
        self.addCleanup(storage_settings.disable)

    upload = originating.SignedPOOriginatingPRTests.upload
    assert_linked = originating.SignedPOOriginatingPRTests.assert_linked
    order = originating.SignedPOOriginatingPRTests.order
    grant_vendor_create = single.SignedPOSingleSaveTests.grant_vendor_create

    def assert_nothing_saved(self):
        self.assertFalse(PurchaseOrder.objects.exists())
        self.assertFalse(PODocument.objects.exists())
        self.assertEqual(Vendor.objects.count(), 1)
        self.assertFalse(any(path.is_file() for path in Path(self.media.name).rglob('*')))

    def test_unreviewed_and_omitted_review_fields_obey_model_limits_before_storage(self):
        source_to_model = {
            'payment_terms': 'payment_terms', 'payment_mode': 'payment_mode',
            'delivery_terms': 'delivery_terms', 'project_number': 'project_number',
            'seller_reference': 'seller_reference', 'quote_ref': 'quote_ref',
            'vendor_license_no': 'seller_license_no',
        }
        for reviewed in (False, True):
            for source_name, model_name in source_to_model.items():
                with self.subTest(reviewed=reviewed, field=source_name):
                    original = self.fields[source_name]
                    limit = PurchaseOrder._meta.get_field(model_name).max_length
                    self.fields[source_name] = 'x' * (limit + 1)
                    changes = {'reviewed_fields': json.dumps({'summary': 'Reviewed summary'})} if reviewed else {}
                    with patch(f'{originating.SERVICE}.default_storage.save') as save:
                        response = self.upload(**changes)
                    self.assertEqual(response.status_code, 400, response.data)
                    self.assertIn(source_name, response.data)
                    self.assertIn(str(limit), str(response.data[source_name]))
                    save.assert_not_called()
                    self.assert_nothing_saved()
                    self.fields[source_name] = original

    def test_invalid_source_is_rejected_before_automatic_vendor_registration(self):
        self.grant_vendor_create()
        self.fields['vendor_name'] = 'New supplier from rejected source'
        self.fields['payment_terms'] = 'x' * 301
        with patch('apps.procurement.services.document_vendor.resolve_document_vendor') as resolve:
            response = self.upload(reviewed_fields=json.dumps({'summary': 'Reviewed summary'}))
        self.assertEqual(response.status_code, 400, response.data)
        resolve.assert_not_called()
        self.assert_nothing_saved()

    def test_reviewed_correction_saves_without_truncating_original_source_text(self):
        original_terms = 'x' * 301
        self.fields['payment_terms'] = original_terms
        self.fields['project_number'] = 'x' * 101
        response = self.upload(reviewed_fields=json.dumps({'payment_terms': 'Net 45 days', 'project_number': 'PRJ-123'}))
        order = self.assert_linked(response)
        self.assertEqual(order.payment_terms, 'Net 45 days')
        self.assertEqual(order.project_number, 'PRJ-123')
        self.assertEqual(order.rad_project_no, 'PRJ-123')
        document = PODocument.objects.get(pk=response.data['document_id'])
        self.assertEqual(document.extracted_data['payment_terms'], 'Net 45 days')
        self.assertEqual(document.extracted_data['source_extracted_data']['payment_terms'], original_terms)
        self.assertEqual(document.extracted_data['source_extracted_data']['project_number'], 'x' * 101)

    def test_reviewed_project_number_is_validated_before_storage(self):
        response = self.upload(reviewed_fields=json.dumps({'project_number': 'x' * 101}))
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('project_number', response.data)
        self.assert_nothing_saved()

    def test_text_at_model_limits_is_saved_intact(self):
        for field in ('payment_terms', 'payment_mode', 'delivery_terms', 'seller_reference', 'quote_ref'):
            self.fields[field] = 'x' * PurchaseOrder._meta.get_field(field).max_length
        order = self.assert_linked(self.upload())
        for field in ('payment_terms', 'payment_mode', 'delivery_terms', 'seller_reference', 'quote_ref'):
            self.assertEqual(getattr(order, field), self.fields[field])

    def test_existing_order_retains_long_source_text_without_overwriting_commercial_terms(self):
        existing = self.order(payment_terms='Keep agreed terms', delivery_terms='Keep agreed delivery')
        self.fields['payment_terms'] = 'x' * 301
        self.fields['delivery_terms'] = 'x' * 201
        response = self.upload(reviewed_fields=json.dumps({'summary': 'Reviewed evidence'}))
        order = self.assert_linked(response)
        self.assertEqual(order.pk, existing.pk)
        self.assertEqual(order.payment_terms, 'Keep agreed terms')
        self.assertEqual(order.delivery_terms, 'Keep agreed delivery')
        document = PODocument.objects.get(pk=response.data['document_id'])
        self.assertEqual(document.extracted_data['payment_terms'], self.fields['payment_terms'])
        self.assertEqual(document.extracted_data['delivery_terms'], self.fields['delivery_terms'])

    def test_incomplete_extraction_can_still_be_retained_for_review(self):
        self.fields.update(currency='', extraction_truncated=True, source_page_count=6)
        response = self.upload()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['operation'], 'uploaded')
        self.assertIsNone(response.data['purchase_order_id'])
        self.assertFalse(PurchaseOrder.objects.exists())
        self.assertEqual(PODocument.objects.get().extracted_data['currency'], '')
