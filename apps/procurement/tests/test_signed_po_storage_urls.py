"""Signed storage URLs must survive PO imports without truncating source evidence."""

import hashlib
import json
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.procurement.models import PODocument, PurchaseOrder

from . import test_signed_po_originating_pr as originating


def signed_source_url(length):
    """Return a synthetic URL; no credentials or external storage are involved."""
    prefix = 'https://storage.example.test/signed-po.pdf?X-Amz-Security-Token='
    return prefix + 'x' * (length - len(prefix))


@override_settings(ROOT_URLCONF=originating.__name__)
class SignedPOStorageURLTests(TestCase):
    def setUp(self):
        originating.SignedPOOriginatingPRTests.setUp(self)
        storage = patch(f'{originating.SERVICE}.default_storage')
        self.storage = storage.start()
        self.addCleanup(storage.stop)
        self.storage.save.return_value = 'procurement/signed_documents/2026/synthetic-po.pdf'
        self.storage.open.return_value.__enter__.return_value.read.return_value = self.content
        self.reviewed = {
            'summary': self.fields['summary'], 'vendor_name': self.vendor.name,
            'po_date': '2026-01-07', 'currency': self.fields['currency'],
        }

    order = originating.SignedPOOriginatingPRTests.order
    assert_linked = originating.SignedPOOriginatingPRTests.assert_linked

    def upload(self, **changes):
        return originating.SignedPOOriginatingPRTests.upload(
            self, reviewed_fields=json.dumps(self.reviewed), **changes,
        )

    def assert_source_saved(self, response, url):
        order = self.assert_linked(response)
        document = PODocument.objects.get(pk=response.data['document_id'])
        self.assertEqual(document.confirmed_po_id, order.pk)
        self.assertEqual(document.s3_url, url)
        self.assertEqual(response.data['source_document_url'], url)
        self.assertEqual(order.approval_stamp, url + '#page=1')
        self.assertEqual(order.attachments[-1]['url'], url)
        self.assertEqual(order.attachments[-1]['document_id'], str(document.pk))
        digest = hashlib.sha256(self.content).hexdigest()
        self.assertEqual(document.extracted_data['source_sha256'], digest)
        self.assertEqual(order.attachments[-1]['sha256'], digest)
        self.assertEqual(document.file_size_bytes, len(self.content))
        saved_source = self.storage.save.call_args.args[1]
        saved_source.seek(0)
        self.assertEqual(saved_source.read(), self.content)
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        self.assertEqual(PODocument.objects.count(), 1)
        # Exercise the model validation contract too: SQLite does not enforce
        # varchar limits on writes, while the deployed PostgreSQL database does.
        PODocument._meta.get_field('s3_url').clean(document.s3_url, document)
        PurchaseOrder._meta.get_field('approval_stamp').clean(order.approval_stamp, order)
        return order, document

    def check_new_order(self, length):
        url = signed_source_url(length)
        self.storage.url.return_value = url
        response = self.upload()
        order, document = self.assert_source_saved(response, url)
        self.assertEqual(response.data['operation'], 'created')
        self.assertEqual(order.approval_signature, url + '#page=1')
        repeated = self.upload()
        repeated_order, repeated_document = self.assert_source_saved(repeated, url)
        self.assertEqual(repeated_order.pk, order.pk)
        self.assertEqual(repeated_document.pk, document.pk)
        self.assertEqual(self.storage.save.call_count, 1)

    def check_existing_order(self, length):
        existing = self.order(category='other', status='completed')
        existing.refresh_from_db()
        url = signed_source_url(length)
        self.storage.url.return_value = url
        response = self.upload()
        order, _ = self.assert_source_saved(response, url)
        self.assertEqual(response.data['operation'], 'attached')
        self.assertEqual(order.pk, existing.pk)
        self.assertEqual(order.status, existing.status)
        self.assertEqual(order.title, existing.title)
        self.assertEqual(order.total_amount, existing.total_amount)
        self.assertEqual(order.currency, existing.currency)

    def test_new_order_retains_source_url_longer_than_500_characters(self):
        self.check_new_order(600)

    def test_new_order_retains_source_url_longer_than_1000_characters(self):
        self.check_new_order(1600)

    def test_existing_order_accepts_source_url_longer_than_500_characters(self):
        self.check_existing_order(600)

    def test_existing_order_accepts_source_url_longer_than_1000_characters(self):
        self.check_existing_order(1600)

    def test_missing_source_restores_with_a_long_url_and_keeps_document_identity(self):
        self.storage.url.return_value = signed_source_url(120)
        first = self.upload(stamp_verified='false')
        original = self.assert_linked(first)
        document = PODocument.objects.get(pk=first.data['document_id'])
        previous_key = document.s3_key
        self.assertEqual(original.approval_stamp, '')

        restored_url = signed_source_url(1600)
        self.storage.open.side_effect = FileNotFoundError('Synthetic missing source')
        self.storage.save.return_value = 'procurement/signed_documents/2026/restored-po.pdf'
        self.storage.url.return_value = restored_url
        restored_order, restored_document = self.assert_source_saved(self.upload(), restored_url)

        self.assertEqual(restored_order.pk, original.pk)
        self.assertEqual(restored_document.pk, document.pk)
        self.assertEqual(restored_document.s3_key, self.storage.save.return_value)
        self.assertEqual(restored_document.extracted_data['source_storage_history'][-1]['previous_storage_key'], previous_key)
        self.assertEqual(self.storage.save.call_count, 2)
