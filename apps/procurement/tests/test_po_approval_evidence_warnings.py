"""Incomplete external approval details warn without blocking document registration."""

from copy import deepcopy

from django.test import TestCase, override_settings

from apps.procurement.models import PODocument, PurchaseOrder
from . import test_paired_signed_import as paired
from . import test_signed_po_originating_pr as originating
from . import test_unified_po_pdf_review as unified
from . import test_po_document_reconciliation as reconciliation


def assert_review_needed(test, result, po):
    test.assertTrue(result['signature_visible'])
    test.assertFalse(result['signature_verified'])
    test.assertFalse(result['approval_evidence_complete'])
    test.assertTrue(any('requires review' in issue for issue in result['approval_evidence_issues']))
    test.assertEqual(po.approved_by_name, '')
    test.assertIsNone(po.approved_date)
    test.assertIsNone(po.approved_at)
    test.assertEqual(po.approval_signature, '')
    test.assertEqual(po.approval_log[-1]['status'], 'Evidence review required')


@override_settings(ROOT_URLCONF=originating.__name__)
class PairedPOApprovalWarningTests(TestCase):
    setUp = paired.PairedSignedImportTests.setUp
    grant = paired.PairedSignedImportTests.grant
    upload = paired.PairedSignedImportTests.upload
    source_files = paired.PairedSignedImportTests.source_files

    def test_missing_po_approver_saves_both_originals_with_independent_review_warning(self):
        result = self.upload(po_approved_by_name='')
        self.assertEqual(result.status_code, 201, result.data)
        po = PurchaseOrder.objects.get()
        assert_review_needed(self, result.data['purchase_order'], po)
        self.assertTrue(result.data['document_signed_off'])
        self.assertFalse(result.data['po_link']['manual_link_required'])
        self.assertTrue(po.pr_reference.attachments)
        self.assertTrue(po.attachments)
        document = PODocument.objects.get()
        self.assertEqual(document.extracted_data['approved_by_name'], '')
        self.assertEqual(document.extracted_data['approved_date'], '2026-01-08')
        self.assertEqual(document.extracted_data['approved_by_title'], 'PO Director')

    def test_missing_po_date_saves_warning_and_exact_retry_is_idempotent(self):
        first = self.upload(po_approved_date='')
        self.assertEqual(first.status_code, 201, first.data)
        po = PurchaseOrder.objects.get()
        assert_review_needed(self, first.data['purchase_order'], po)
        before = PurchaseOrder.objects.values().get(pk=po.pk)
        source = deepcopy(PODocument.objects.get().extracted_data)
        again = self.upload(po_approved_date='')
        self.assertEqual(again.status_code, 200, again.data)
        self.assertEqual(again.data['operation'], 'already_imported')
        self.assertEqual(again.data['purchase_order']['approval_evidence_issues'], first.data['purchase_order']['approval_evidence_issues'])
        self.assertEqual(PurchaseOrder.objects.values().get(pk=po.pk), before)
        self.assertEqual(PODocument.objects.get().extracted_data, source)
        self.assertEqual(PurchaseOrder.objects.count(), 1)


@override_settings(ROOT_URLCONF=originating.__name__)
class UnifiedPOApprovalWarningTests(TestCase):
    setUp = unified.UnifiedPOPDFReviewTests.setUp
    upload = originating.SignedPOOriginatingPRTests.upload
    assert_linked = originating.SignedPOOriginatingPRTests.assert_linked
    files = unified.UnifiedPOPDFReviewTests.files
    save = unified.UnifiedPOPDFReviewTests.save

    def test_missing_both_po_details_saves_without_filling_them_from_pr(self):
        response = self.save(approved_by_name='', approved_date='')
        po = self.assert_linked(response)
        assert_review_needed(self, response.data, po)
        fields = PODocument.objects.get().extracted_data
        self.assertEqual(fields['approved_by_name'], '')
        self.assertEqual(fields['approved_date'], '')
        self.assertIn(response.data['approval_evidence_issues'][0], response.data['workflow_issues'])

    def test_partial_same_source_can_be_corrected_then_cannot_be_downgraded(self):
        first = self.save(approved_by_name='Unclear name', approved_date='')
        po = self.assert_linked(first)
        assert_review_needed(self, first.data, po)
        corrected = self.save(approved_by_name='Confirmed PO Signer', approved_date='2026-01-09')
        self.assertEqual(corrected.status_code, 200, corrected.data)
        po.refresh_from_db()
        self.assertTrue(corrected.data['signature_verified'])
        self.assertTrue(corrected.data['approval_evidence_complete'])
        self.assertEqual(corrected.data['approval_evidence_issues'], [])
        self.assertEqual(po.approved_by_name, 'Confirmed PO Signer')
        self.assertEqual(po.approved_date.isoformat(), '2026-01-09')
        self.assertEqual(len(po.approval_log), 1)
        self.assertEqual(po.approval_log[0]['status'], 'Approved')
        recorded = {key: getattr(po, key) for key in ('approved_by_name', 'approved_by_title', 'approved_date', 'approved_at', 'approval_signature', 'approval_log')}
        incomplete_retry = self.save(signature_verified='false', approved_by_name='', approved_date='')
        self.assertTrue(incomplete_retry.data['signature_verified'])
        self.assertEqual(incomplete_retry.data['approval_evidence_issues'], [])
        po.refresh_from_db()
        self.assertEqual({key: getattr(po, key) for key in recorded}, recorded)
        self.assertEqual(PODocument.objects.count(), 1)

    def test_incomplete_new_source_does_not_erase_existing_completed_order_approval(self):
        first = self.save()
        po = self.assert_linked(first)
        preserved = {key: getattr(po, key) for key in ('approved_by_name', 'approved_date', 'approved_at', 'approval_signature', 'total_amount', 'tax_amount')}
        self.content += b'\nNew retained source'
        response = self.save(approved_by_name='', approved_date='')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data['signature_verified'])
        po.refresh_from_db()
        self.assertEqual({key: getattr(po, key) for key in preserved}, preserved)
        self.assertEqual([row['status'] for row in po.approval_log], ['Approved', 'Evidence review required'])

    def test_malformed_nonempty_date_still_rejects_without_orphan_records(self):
        response = self.save(approved_date='not a date')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(PurchaseOrder.objects.exists())
        self.assertFalse(PODocument.objects.exists())
        self.assertEqual(self.files(), self.before_files)

    def test_pending_same_source_keeps_complete_evidence_during_reviewed_materialization(self):
        self.fields.update(extraction_truncated=True, source_page_count=6)
        first = self.upload()
        self.assertEqual(first.status_code, 200, first.data)
        self.assertIsNone(first.data['purchase_order_id'])
        original = deepcopy(PODocument.objects.get().extracted_data)
        response = self.save(signature_verified='false', approved_by_name='', approved_by_title='', approved_date='')
        po = self.assert_linked(response)
        self.assertTrue(response.data['signature_verified'])
        self.assertTrue(response.data['signature_visible'])
        self.assertEqual(po.approved_by_name, original['approved_by_name'])
        self.assertEqual(po.approved_date.isoformat(), original['approved_date'])
        self.assertEqual(PODocument.objects.count(), 1)

    def test_adding_stamp_confirmation_updates_log_without_rewriting_approval_time(self):
        response = self.save(stamp_verified='false')
        po = self.assert_linked(response)
        date = po.approval_log[0]['date']
        recorded_at = po.approval_log[0]['approved_at']
        response = self.save(stamp_verified='true')
        self.assertEqual(response.status_code, 200, response.data)
        po.refresh_from_db()
        self.assertTrue(po.approval_log[0]['stamp_verified'])
        self.assertEqual(po.approval_log[0]['date'], date)
        self.assertEqual(po.approval_log[0]['approved_at'], recorded_at)


@override_settings(ROOT_URLCONF=reconciliation.__name__)
class SavedPOApprovalWarningTests(TestCase):
    setUp = reconciliation.PODocumentReconciliationTests.setUp
    reconcile = reconciliation.PODocumentReconciliationTests.reconcile

    def test_saved_incomplete_signature_evidence_reconciles_and_retry_keeps_warning(self):
        self.document.extracted_data['approved_by_name'] = ''
        self.document.extracted_data['approved_date'] = ''
        self.document.save(update_fields=['extracted_data'])
        response = self.reconcile()
        self.assertEqual(response.status_code, 200, response.data)
        po = PurchaseOrder.objects.get()
        assert_review_needed(self, response.data, po)
        repeated = self.reconcile()
        self.assertEqual(repeated.status_code, 200, repeated.data)
        self.assertFalse(repeated.data['approval_evidence_complete'])
        self.assertEqual(repeated.data['approval_evidence_issues'], response.data['approval_evidence_issues'])
