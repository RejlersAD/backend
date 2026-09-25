"""Source signatory annotations retain evidence without granting approval authority."""

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.procurement.models import PODocument, PurchaseOrder, PurchaseRequisition
from apps.procurement.serializers import PurchaseRequisitionSerializer
from apps.procurement.services.pr_source_approval_review import (
    SourceApprovalReviewConflict, SourceApprovalReviewError,
)
from apps.procurement.services.signed_po_pdf_import import SignedPOImportError
from apps.procurement.services.signed_pr_pdf_import import (
    SignedPRImportError, SignedPRStorageError, import_signed_pr_pdf, preview_signed_pr_pdf,
)
from apps.procurement.views import PurchaseRequisitionViewSet

from . import test_paired_signed_import as pair_fixtures
from . import test_signed_pr_pdf_creation as creation_fixtures


EMPTY_REVIEW = {'approval_labels': {}, 'additional_approver': None}
REVIEW = {
    'approval_labels': {'pm': '1', 'moe': '2', 'mop': 'Final source', 'vp': '4'},
    'additional_approver': {
        'name': 'External Source Signer', 'approval_label': '5', 'signature_verified': True,
    },
}


class PRSourceApprovalReviewTests(TestCase):
    # Compose existing isolated fixtures without inheriting their test methods.
    _patch = creation_fixtures.SignedPRPdfCreationTests._patch
    _grant_api_access = creation_fixtures.SignedPRPdfCreationTests._grant_api_access
    _excel_record = creation_fixtures.SignedPRPdfCreationTests._excel_record
    _use_three_signed_source_rows = creation_fixtures.SignedPRPdfCreationTests._use_three_signed_source_rows

    def setUp(self):
        creation_fixtures.SignedPRPdfCreationTests.setUp(self)
        self._grant_api_access()
        self._use_three_signed_source_rows()
        self.source = b'%PDF-source-signatory-review'

    def import_document(self, *, existing=None, source=None, **kwargs):
        options = {'filename': 'signed.pdf', 'uploaded_by': self.reviewer}
        if existing is None:
            options.update(create_new=True, manual_overrides=self.reviewed)
        else:
            options.update(attach_only=True, expected_pr_number=existing.pr_number)
        options.update(kwargs)
        return import_signed_pr_pdf(source or self.source, **options)

    def save_review(self, review=None, expected=None, **kwargs):
        return self.import_document(
            source_approval_review=deepcopy(REVIEW if review is None else review),
            expected_source_approval_review=deepcopy(EMPTY_REVIEW if expected is None else expected),
            **kwargs,
        )

    def upload(self, **changes):
        data = {
            'file': SimpleUploadedFile('signed.pdf', self.source, content_type='application/pdf'),
            'create_new': 'true', 'manual_overrides': json.dumps(self.reviewed),
        }
        data.update(changes)
        request = APIRequestFactory().post(
            '/api/v1/procurement/requisitions/import-signed-pdf/', data, format='multipart',
        )
        force_authenticate(request, self.reviewer)
        return PurchaseRequisitionViewSet.as_view({'post': 'import_signed_pdf'})(request)

    def envelope(self, pr):
        pr.refresh_from_db()
        return deepcopy(pr.price_remarks_data['signed_approval_evidence']['source_approval_review'])

    def test_creation_roundtrip_records_normalized_review_and_server_owned_audit(self):
        requested = deepcopy(REVIEW)
        requested['approval_labels'].update(pm='  1  ', moe='   ')
        requested['additional_approver']['name'] = '  External Source Signer  '
        expected = deepcopy(REVIEW)
        expected['approval_labels'].pop('moe')
        before = timezone.now()
        result = self.save_review(requested)
        pr = PurchaseRequisition.objects.get(pk=result['pr_id'])
        self.assertEqual(result['source_approval_review'], expected)
        envelope = self.envelope(pr)
        self.assertEqual(envelope['review'], expected)
        self.assertEqual(envelope['document_sha256'], hashlib.sha256(self.source).hexdigest())
        self.assertEqual(envelope['reviewed_by_id'], str(self.reviewer.pk))
        self.assertGreaterEqual(parse_datetime(envelope['reviewed_at']), before)
        self.assertLessEqual(parse_datetime(envelope['reviewed_at']), timezone.now())
        self.assertEqual(len(envelope['history']), 1)
        self.assertEqual(envelope['history'][0]['before'], EMPTY_REVIEW)
        self.assertEqual(envelope['history'][0]['after'], expected)
        self.assertEqual(envelope['history'][0]['reviewed_by_id'], str(self.reviewer.pk))
        preview = preview_signed_pr_pdf(self.source, filename='signed.pdf')
        self.assertEqual(preview['source_approval_review'], expected)

    def test_signed_source_retains_original_roles_names_and_workflow_levels(self):
        pr = PurchaseRequisition.objects.get(pk=self.import_document()['pr_id'])
        original_workflow = deepcopy(pr.approval_workflow_config)
        requested = deepcopy(REVIEW)
        requested['additional_approver']['signature_verified'] = False
        result = self.save_review(requested, existing=pr)
        pr.refresh_from_db()
        self.assertTrue(result['document_signed_off'])
        self.assertEqual(pr.status, 'approved')
        self.assertEqual([stage['role'] for stage in pr.approval_workflow_config], ['PD', 'MoP', 'VP, Op'])
        self.assertEqual(pr.approval_workflow_config, original_workflow)
        self.assertEqual([stage['user_name'] for stage in pr.approval_workflow_config],
                         [self.approvers[role].get_full_name() for role in ('pm', 'mop', 'vp')])
        self.assertIsNone(pr.eng_manager_name)
        self.assertEqual(result['approval_detection']['approval_rows'], self.evidence['approval_rows'])
        rows = pr.price_remarks_data['signed_document_verification']['source_approval_rows']
        self.assertEqual([row['role_key'] for row in rows], ['pm', 'mop', 'vp'])
        self.assertNotIn('additional', result['approval_detection']['signatures'])

    def test_verified_additional_signer_cannot_complete_missing_canonical_signature(self):
        self.evidence['signatures']['vp'] = False
        self.evidence['approval_rows'][-1]['signature_detected'] = False
        result = self.save_review()
        pr = PurchaseRequisition.objects.get(pk=result['pr_id'])
        self.assertFalse(result['document_signed_off'])
        self.assertEqual(pr.status, 'draft')
        self.assertIsNone(pr.approved_at)
        self.assertIsNone(pr.approved_by)
        self.assertEqual(pr.approval_workflow_config, [])
        self.assertFalse(result['approval_detection']['signatures']['vp'])
        self.assertEqual(result['source_approval_review'], REVIEW)

    def test_attachment_preserves_existing_commercial_fields_and_source_evidence(self):
        pr = self._excel_record()
        fields = ('title', 'product_service', 'supplier_name', 'vendor_id', 'total_price',
                  'currency', 'items', 'description_reason', 'price_remarks')
        before = {name: deepcopy(getattr(pr, name)) for name in fields}
        metadata = deepcopy(pr.price_remarks_data)
        result = self.save_review(existing=pr)
        pr.refresh_from_db()
        self.assertEqual({name: getattr(pr, name) for name in fields}, before)
        for name, value in metadata.items():
            self.assertEqual(pr.price_remarks_data[name], value)
        self.assertEqual(result['source_approval_review'], REVIEW)
        self.assertEqual(pr.attachments[0]['filename'], 'reference.xlsx')
        self.assertEqual(len(pr.attachments), 2)

    def test_legacy_omission_preserves_same_document_annotation_and_audit(self):
        pr = PurchaseRequisition.objects.get(pk=self.save_review()['pr_id'])
        before = self.envelope(pr)
        result = self.import_document(existing=pr)
        self.assertEqual(result['source_approval_review'], REVIEW)
        self.assertEqual(self.envelope(pr), before)
        self.assertEqual(self.storage.save.call_count, 1)

    def test_identical_retry_with_old_expected_snapshot_does_not_duplicate_audit(self):
        pr = PurchaseRequisition.objects.get(pk=self.save_review()['pr_id'])
        before = self.envelope(pr)
        result = self.save_review(existing=pr)
        self.assertEqual(result['source_approval_review'], REVIEW)
        self.assertEqual(self.envelope(pr), before)
        self.assertEqual(self.storage.save.call_count, 1)
        self.assertEqual(len(pr.attachments), 1)

    def test_current_snapshot_updates_then_explicit_clear_preserves_audit_history(self):
        pr = PurchaseRequisition.objects.get(pk=self.save_review()['pr_id'])
        first = self.envelope(pr)
        changed = deepcopy(REVIEW)
        changed['additional_approver']['name'] = 'Corrected Source Signer'
        changed['additional_approver']['special_note'] = 'Corrected the source signer spelling.'
        self.save_review(changed, expected=REVIEW, existing=pr)
        second = self.envelope(pr)
        self.assertEqual(second['history'][:1], first['history'])
        self.assertEqual(second['history'][-1]['before'], REVIEW)
        self.assertEqual(second['history'][-1]['after'], changed)
        result = self.save_review(EMPTY_REVIEW, expected=changed, existing=pr)
        cleared = self.envelope(pr)
        self.assertEqual(result['source_approval_review'], EMPTY_REVIEW)
        self.assertEqual(len(cleared['history']), 3)
        self.assertEqual(cleared['history'][:2], second['history'])
        self.assertEqual(cleared['history'][-1]['after'], EMPTY_REVIEW)

    def test_stale_snapshot_rejects_before_storage_or_any_record_change(self):
        pr = PurchaseRequisition.objects.get(pk=self.save_review()['pr_id'])
        before = PurchaseRequisition.objects.filter(pk=pr.pk).values().get()
        changed = deepcopy(REVIEW)
        changed['approval_labels']['pm'] = 'Changed'
        self.storage.reset_mock()
        self.detect.reset_mock()
        with self.assertRaises(SourceApprovalReviewConflict):
            self.save_review(changed, existing=pr)
        self.assertEqual(PurchaseRequisition.objects.filter(pk=pr.pk).values().get(), before)
        self.assertEqual(self.storage.mock_calls, [])

    def test_missing_expected_snapshot_and_expected_without_review_are_invalid(self):
        for arguments in ({'source_approval_review': REVIEW},
                          {'expected_source_approval_review': EMPTY_REVIEW}):
            with self.subTest(arguments=arguments), self.assertRaises(SourceApprovalReviewError):
                self.import_document(**arguments)
        self.assertFalse(PurchaseRequisition.objects.exists())
        self.storage.save.assert_not_called()

    def test_replacement_source_does_not_inherit_annotations_but_keeps_prior_audit(self):
        pr = PurchaseRequisition.objects.get(pk=self.save_review()['pr_id'])
        first = self.envelope(pr)
        replacement = b'%PDF-distinct-source-signatory-review'
        preview = preview_signed_pr_pdf(replacement, filename='signed.pdf')
        self.assertEqual(preview['source_approval_review'], EMPTY_REVIEW)
        result = self.import_document(existing=pr, source=replacement)
        self.assertEqual(result['source_approval_review'], EMPTY_REVIEW)
        replaced = self.envelope(pr)
        self.assertEqual(replaced['review'], EMPTY_REVIEW)
        self.assertEqual(replaced['document_sha256'], hashlib.sha256(replacement).hexdigest())
        self.assertEqual(replaced['history'][:1], first['history'])
        self.assertEqual(replaced['history'][-1]['previous_document_sha256'], first['document_sha256'])
        self.assertEqual(replaced['history'][-1]['before'], REVIEW)
        self.assertEqual(replaced['history'][-1]['after'], EMPTY_REVIEW)

    def test_legacy_document_without_review_previews_empty_and_adds_no_false_audit(self):
        result = self.import_document()
        pr = PurchaseRequisition.objects.get(pk=result['pr_id'])
        self.assertEqual(result['source_approval_review'], EMPTY_REVIEW)
        self.assertNotIn('source_approval_review', pr.price_remarks_data['signed_approval_evidence'])
        self.assertEqual(preview_signed_pr_pdf(self.source, filename='signed.pdf')['source_approval_review'],
                         EMPTY_REVIEW)

    def test_review_bound_to_different_digest_is_not_used_for_current_source(self):
        pr = PurchaseRequisition.objects.get(pk=self.save_review()['pr_id'])
        pr.price_remarks_data['signed_approval_evidence']['source_approval_review']['document_sha256'] = 'a' * 64
        pr.save(update_fields=['price_remarks_data'])
        self.assertEqual(preview_signed_pr_pdf(self.source, filename='signed.pdf')['source_approval_review'],
                         EMPTY_REVIEW)

    def test_invalid_review_values_never_create_record_or_store_source(self):
        invalid = [
            None, [], 'review', {'workflow': []}, {'reviewed_by_id': str(self.reviewer.pk)},
            {'document_sha256': 'a' * 64}, {'history': []},
            {'approval_labels': []}, {'approval_labels': {'additional': '5'}},
            {'approval_labels': {'PM': '1'}}, {'approval_labels': {'pm': 1}},
            {'approval_labels': {'pm': None}}, {'approval_labels': {'pm': 'x' * 21}},
            {'additional_approver': []}, {'additional_approver': {'name': 'Person', 'user_id': 7}},
            {'additional_approver': {'name': 'Person', 'status': 'approved'}},
            {'additional_approver': {'name': 'Person', 'signature_verified': 'true'}},
            {'additional_approver': {'name': 'Person', 'signature_verified': 1}},
            {'additional_approver': {'name': 'Person', 'signature_verified': None}},
            {'additional_approver': {'name': 'Person'}},
            {'additional_approver': {'approval_label': '5', 'signature_verified': False}},
            {'additional_approver': {'name': '  ', 'signature_verified': True}},
            {'additional_approver': {'name': 7, 'signature_verified': False}},
            {'additional_approver': {'name': 'x' * 201, 'signature_verified': False}},
            {'additional_approver': {'name': 'Person', 'approval_label': 'x' * 21, 'signature_verified': False}},
        ]
        for review in invalid:
            with self.subTest(review=review), self.assertRaises(SourceApprovalReviewError):
                self.import_document(source_approval_review=review, expected_source_approval_review=EMPTY_REVIEW)
        self.assertFalse(PurchaseRequisition.objects.exists())
        self.storage.save.assert_not_called()

    def test_empty_additional_row_normalizes_to_absent(self):
        pr = None
        for additional in ({}, {'name': ' ', 'approval_label': ' '},
                           {'name': '', 'approval_label': '', 'signature_verified': False}):
            with self.subTest(additional=additional):
                result = self.save_review({'approval_labels': {'pm': ' '}, 'additional_approver': additional},
                                          existing=pr)
                pr = PurchaseRequisition.objects.get(pk=result['pr_id'])
                self.assertEqual(result['source_approval_review'], EMPTY_REVIEW)
                self.assertNotIn('source_approval_review', pr.price_remarks_data['signed_approval_evidence'])

    def test_maximum_length_names_and_labels_are_accepted(self):
        requested = {'approval_labels': {'pm': 'x' * 20}, 'additional_approver': {
            'name': 'n' * 200, 'approval_label': 'y' * 20, 'signature_verified': False,
        }}
        self.assertEqual(self.save_review(requested)['source_approval_review'], requested)

    def test_storage_failure_rolls_back_annotation_and_preserves_previous_history(self):
        pr = PurchaseRequisition.objects.get(pk=self.save_review()['pr_id'])
        before = PurchaseRequisition.objects.filter(pk=pr.pk).values().get()
        changed = deepcopy(REVIEW)
        changed['approval_labels']['pm'] = 'Updated'
        self.storage.exists.side_effect = OSError('private storage failure')
        with self.assertRaises(SignedPRStorageError):
            self.save_review(changed, expected=REVIEW, existing=pr)
        self.assertEqual(PurchaseRequisition.objects.filter(pk=pr.pk).values().get(), before)

    def test_generic_form_edit_cannot_forge_or_clear_source_review_envelope(self):
        self.evidence['signatures']['vp'] = False
        self.evidence['approval_rows'][-1]['signature_detected'] = False
        pr = PurchaseRequisition.objects.get(pk=self.save_review()['pr_id'])
        before = self.envelope(pr)
        for forged in ({}, {'source_approval_review': {'review': EMPTY_REVIEW}}):
            serializer = PurchaseRequisitionSerializer(pr, data={
                'description_reason': 'Permitted draft explanation',
                'price_remarks_data': {'signed_approval_evidence': forged},
            }, partial=True, context={'request': SimpleNamespace(user=self.issuer)})
            self.assertTrue(serializer.is_valid(), serializer.errors)
            serializer.save()
            self.assertEqual(self.envelope(pr), before)

    def test_multipart_roundtrip_and_stale_response_are_explicit(self):
        response = self.upload(source_approval_review=json.dumps(REVIEW),
                               expected_source_approval_review=json.dumps(EMPTY_REVIEW))
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['source_approval_review'], REVIEW)
        preview = self.upload(preview_only='true')
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data['source_approval_review'], REVIEW)
        before = PurchaseRequisition.objects.values().get()
        changed = deepcopy(REVIEW)
        changed['approval_labels']['pm'] = 'Changed'
        response = self.upload(create_new='false', attach_only='true', expected_pr_number=self.fields['pr_number'],
                               source_approval_review=json.dumps(changed),
                               expected_source_approval_review=json.dumps(EMPTY_REVIEW))
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(str(response.data['code']), 'stale_source_approval_review')
        self.assertEqual(PurchaseRequisition.objects.values().get(), before)

    def test_multipart_invalid_json_snapshot_or_missing_snapshot_returns_400(self):
        for fields in (
            {'source_approval_review': '{invalid', 'expected_source_approval_review': '{}'},
            {'source_approval_review': '{}', 'expected_source_approval_review': '{invalid'},
            {'source_approval_review': 'null', 'expected_source_approval_review': '{}'},
            {'source_approval_review': json.dumps(REVIEW)},
            {'expected_source_approval_review': '{}'},
            {'source_approval_review': '{}', 'expected_source_approval_review': '[]'},
            {'source_approval_review': json.dumps({'additional_approver': {'signature_verified': True}}),
             'expected_source_approval_review': '{}'},
        ):
            with self.subTest(fields=fields):
                response = self.upload(**fields)
                self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(PurchaseRequisition.objects.exists())
        self.storage.save.assert_not_called()

    def test_annotation_does_not_bypass_wrong_source_identity(self):
        pr = self._excel_record()
        before = PurchaseRequisition.objects.filter(pk=pr.pk).values().get()
        self.fields['pr_number'] = 'RAD-PRJ-PR-9999_2026'
        with self.assertRaises(SignedPRImportError):
            self.save_review(existing=pr)
        self.assertEqual(PurchaseRequisition.objects.filter(pk=pr.pk).values().get(), before)
        self.storage.save.assert_not_called()


@override_settings(ROOT_URLCONF=pair_fixtures.originating.__name__)
class PairedPRSourceApprovalReviewTests(TestCase):
    grant = pair_fixtures.PairedSignedImportTests.grant
    revoke = pair_fixtures.PairedSignedImportTests.revoke
    source_files = pair_fixtures.PairedSignedImportTests.source_files
    upload = pair_fixtures.PairedSignedImportTests.upload
    assert_no_pair = pair_fixtures.PairedSignedImportTests.assert_no_pair

    def setUp(self):
        pair_fixtures.PairedSignedImportTests.setUp(self)

    def save_pair(self, **changes):
        options = {'source_approval_review': json.dumps(REVIEW),
                   'expected_source_approval_review': json.dumps(EMPTY_REVIEW)}
        options.update(changes)
        return self.upload(**options)

    def test_pair_roundtrip_and_cached_retry_return_review_without_rewriting_records(self):
        first = self.save_pair()
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(first.data['source_approval_review'], REVIEW)
        pr_before = PurchaseRequisition.objects.values().get()
        po_before = PurchaseOrder.objects.values().get()
        document_before = PODocument.objects.values().get()
        files_before = self.source_files()
        second = self.save_pair()
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(second.data['operation'], 'already_imported')
        self.assertEqual(second.data['source_approval_review'], REVIEW)
        self.assertEqual(PurchaseRequisition.objects.values().get(), pr_before)
        self.assertEqual(PurchaseOrder.objects.values().get(), po_before)
        self.assertEqual(PODocument.objects.values().get(), document_before)
        self.assertEqual(self.source_files(), files_before)
        preview = preview_signed_pr_pdf(self.pr_content, filename='signed-pr.pdf')
        self.assertEqual(preview['source_approval_review'], REVIEW)

    def test_cached_pair_rejects_review_changed_by_later_standalone_import(self):
        first = self.save_pair()
        self.assertEqual(first.status_code, 201, first.data)
        pr = PurchaseRequisition.objects.get()
        changed = deepcopy(REVIEW)
        changed['approval_labels']['pm'] = 'Later review'
        import_signed_pr_pdf(self.pr_content, filename='signed-pr.pdf', uploaded_by=self.user,
                             expected_pr_number=pr.pr_number, attach_only=True,
                             source_approval_review=changed, expected_source_approval_review=REVIEW)
        before = PurchaseRequisition.objects.values().get()
        files_before = self.source_files()
        response = self.save_pair()
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(str(response.data['code']), 'stale_source_approval_review')
        self.assertEqual(PurchaseRequisition.objects.values().get(), before)
        self.assertEqual(self.source_files(), files_before)
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        self.assertEqual(PODocument.objects.count(), 1)

    def test_po_failure_rolls_back_review_record_and_both_source_files(self):
        with patch('apps.procurement.services.signed_po_pdf_import.extract_signed_po_fields',
                   side_effect=SignedPOImportError('The PO cannot be read.')):
            response = self.save_pair()
        self.assertEqual(response.status_code, 400, response.data)
        self.assert_no_pair()

    def clear_pair_review(self):
        first = self.save_pair()
        self.assertEqual(first.status_code, 201, first.data)
        pr = PurchaseRequisition.objects.get()
        import_signed_pr_pdf(self.pr_content, filename='signed-pr.pdf', uploaded_by=self.user,
                             expected_pr_number=pr.pr_number, attach_only=True,
                             source_approval_review=EMPTY_REVIEW, expected_source_approval_review=REVIEW)
        return PurchaseRequisition.objects.get(pk=pr.pk)

    def test_cached_pair_applies_review_when_prior_expected_state_was_restored(self):
        pr = self.clear_pair_review()
        po_before = PurchaseOrder.objects.values().get()
        document_before = PODocument.objects.values().get()
        files_before = self.source_files()
        workflow_before = deepcopy(pr.approval_workflow_config)
        response = self.save_pair()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['source_approval_review'], REVIEW)
        pr.refresh_from_db()
        envelope = pr.price_remarks_data['signed_approval_evidence']['source_approval_review']
        self.assertEqual(envelope['review'], REVIEW)
        self.assertEqual(len(envelope['history']), 3)
        self.assertEqual(envelope['history'][-1]['before'], EMPTY_REVIEW)
        self.assertEqual(envelope['history'][-1]['after'], REVIEW)
        self.assertEqual(pr.approval_workflow_config, workflow_before)
        self.assertEqual(PurchaseOrder.objects.values().get(), po_before)
        self.assertEqual(PODocument.objects.values().get(), document_before)
        self.assertEqual(self.source_files(), files_before)
        saved = PurchaseRequisition.objects.values().get()
        replay = self.save_pair()
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(PurchaseRequisition.objects.values().get(), saved)

    def test_cached_pair_annotation_mutation_requires_current_pr_update_permission(self):
        self.clear_pair_review()
        before = PurchaseRequisition.objects.values().get()
        files_before = self.source_files()
        self.revoke('procurement_requisitions', 'update')
        response = self.save_pair()
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(PurchaseRequisition.objects.values().get(), before)
        self.assertEqual(self.source_files(), files_before)

    def test_annotations_do_not_grant_missing_pr_update_permission(self):
        first = self.save_pair()
        self.assertEqual(first.status_code, 201, first.data)
        before = PurchaseRequisition.objects.values().get()
        files_before = self.source_files()
        self.revoke('procurement_requisitions', 'update')
        response = self.save_pair(create_new='false', attach_only='true', expected_pr_number=self.pr_number)
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(PurchaseRequisition.objects.values().get(), before)
        self.assertEqual(self.source_files(), files_before)

    def test_standalone_annotation_upload_requires_current_pr_update_permission(self):
        first = self.save_pair()
        self.assertEqual(first.status_code, 201, first.data)
        before = PurchaseRequisition.objects.values().get()
        po_before = PurchaseOrder.objects.values().get()
        files_before = self.source_files()
        self.revoke('procurement_requisitions', 'update')
        response = self.save_pair(paired=False, create_new='false', attach_only='true',
                                  expected_pr_number=self.pr_number)
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(PurchaseRequisition.objects.values().get(), before)
        self.assertEqual(PurchaseOrder.objects.values().get(), po_before)
        self.assertEqual(self.source_files(), files_before)
