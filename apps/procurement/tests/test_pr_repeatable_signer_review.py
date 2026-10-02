"""Repeatable source annotations and explicit recovery of missed PDF signers."""

from copy import deepcopy
import json
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.procurement.models import PurchaseRequisition
from apps.procurement.services.pr_source_approval_review import (
    SourceApprovalReviewConflict, SourceApprovalReviewError, normalize_source_approval_review,
)
from apps.procurement.services.signed_pr_pdf_import import SignedPRImportError, preview_signed_pr_pdf

from . import test_pr_source_approval_review as fixtures
from . import test_paired_signed_import as pair_fixtures
from apps.procurement.services.signed_po_pdf_import import SignedPOImportError


class RepeatableSignerReviewTests(TestCase):
    _patch = fixtures.PRSourceApprovalReviewTests._patch
    _grant_api_access = fixtures.PRSourceApprovalReviewTests._grant_api_access
    _excel_record = fixtures.PRSourceApprovalReviewTests._excel_record
    _use_three_signed_source_rows = fixtures.PRSourceApprovalReviewTests._use_three_signed_source_rows
    setUp = fixtures.PRSourceApprovalReviewTests.setUp
    import_document = fixtures.PRSourceApprovalReviewTests.import_document
    save_review = fixtures.PRSourceApprovalReviewTests.save_review
    envelope = fixtures.PRSourceApprovalReviewTests.envelope

    def review(self, count=3):
        return {'approval_labels': {}, 'additional_approvers': [
            {'id': f'row-{index}', 'name': f'Additional Person {index}', 'approval_label': str(index + 5),
             'signature_verified': bool(index % 2), 'special_note': f'Source row {index}'}
            for index in range(count)
        ]}

    def test_many_additional_rows_roundtrip_without_changing_canonical_route(self):
        review = self.review(30)
        result = self.save_review(review)
        pr = PurchaseRequisition.objects.get(pk=result['pr_id'])
        self.assertEqual(result['source_approval_review'], review)
        self.assertEqual(preview_signed_pr_pdf(self.source, filename='signed.pdf')['source_approval_review'], review)
        self.assertEqual([row['role_key'] for row in pr.approval_workflow_config], ['pm', 'mop', 'vp'])
        self.assertEqual(self.envelope(pr)['review'], review)

    def test_remove_and_reorder_keep_each_signer_metadata_and_retry_audit(self):
        original = self.review()
        pr = PurchaseRequisition.objects.get(pk=self.save_review(original)['pr_id'])
        changed = {'approval_labels': {}, 'additional_approvers': [original['additional_approvers'][2], original['additional_approvers'][0]]}
        result = self.save_review(changed, expected=original, existing=pr)
        self.assertEqual(result['source_approval_review'], changed)
        saved = self.envelope(pr)
        self.assertEqual(len(saved['history']), 2)
        self.save_review(changed, expected=original, existing=pr)
        self.assertEqual(self.envelope(pr), saved)

    def test_same_id_name_correction_requires_note_after_other_row_removed(self):
        original = self.review()
        pr = PurchaseRequisition.objects.get(pk=self.save_review(original)['pr_id'])
        changed = {'approval_labels': {}, 'additional_approvers': [deepcopy(original['additional_approvers'][2])]}
        changed['additional_approvers'][0].update(name='Corrected Last Person', special_note='')
        with self.assertRaises(SourceApprovalReviewError):
            self.save_review(changed, expected=original, existing=pr)
        changed['additional_approvers'][0]['special_note'] = 'Corrected against the same source row.'
        self.assertEqual(self.save_review(changed, expected=original, existing=pr)['source_approval_review'], changed)

    def test_plural_identity_and_unknown_fields_are_validated(self):
        row = self.review(1)['additional_approvers'][0]
        invalid = [None, {}, [dict(row, id='')], [dict(row, id='../row')], [dict(row, id='x' * 81)],
                   [dict(row, id=1)], [row, row], [{key: value for key, value in row.items() if key != 'id'}],
                   [dict(row, status='approved')]]
        for rows in invalid:
            with self.subTest(rows=rows), self.assertRaises(SourceApprovalReviewError):
                normalize_source_approval_review({'additional_approvers': rows})
        with self.assertRaises(SourceApprovalReviewError):
            normalize_source_approval_review({'additional_approver': None, 'additional_approvers': []})

    def test_legacy_stored_review_and_expected_snapshot_upgrade_without_losing_history(self):
        row = {'name': 'Legacy Person', 'approval_label': '5', 'signature_verified': True}
        legacy = {'approval_labels': {}, 'additional_approver': row}
        pr = PurchaseRequisition.objects.get(pk=self.save_review(legacy)['pr_id'])
        envelope = pr.price_remarks_data['signed_approval_evidence']['source_approval_review']
        envelope['review'] = deepcopy(legacy)
        envelope['history'][0]['after'] = deepcopy(legacy)
        pr.save(update_fields=['price_remarks_data'])
        projected = preview_signed_pr_pdf(self.source, filename='signed.pdf')['source_approval_review']
        self.assertEqual(projected['additional_approvers'], [dict(row, id='legacy-additional')])
        changed = deepcopy(projected)
        changed['additional_approvers'].append(self.review(1)['additional_approvers'][0])
        self.save_review(changed, expected=legacy, existing=pr)
        self.assertEqual(self.envelope(pr)['history'][0]['after'], legacy)
        with self.assertRaises(SourceApprovalReviewConflict):
            self.save_review(legacy, expected=legacy, existing=pr)

    def missed_vp(self):
        self.evidence['approval_rows'] = self.evidence['approval_rows'][:-1]
        self.evidence['approver_names']['vp'] = ''
        self.evidence['signatures']['vp'] = False

    def missing_review(self):
        return {'approval_labels': {}, 'additional_approvers': [],
                'approver_notes': {'vp': 'VP signature and name checked on the original PDF.'}}

    def test_missed_role_name_and_explicit_signature_survive_preview_and_retry(self):
        self.missed_vp()
        result = self.save_review(self.missing_review(), approvals={'vp': 'Recovered VP'},
                                 manual_signature_overrides={'vp': True})
        pr = PurchaseRequisition.objects.get(pk=result['pr_id'])
        self.assertTrue(result['document_signed_off'])
        evidence = pr.price_remarks_data['signed_approval_evidence']
        self.assertEqual(len(evidence['rows']), 2)
        self.assertEqual(evidence['approver_names']['vp'], '')
        recovered = pr.approval_workflow_config[-1]
        self.assertEqual((recovered['user_name'], recovered['signature_source']), ('Recovered VP', 'manual'))
        self.assertTrue(recovered['signature_verified'])
        self.assertEqual(recovered['evidence_source'], 'manual_review')
        preview = preview_signed_pr_pdf(self.source, filename='signed.pdf')
        self.assertTrue(preview['document_signed_off'])
        self.assertEqual(preview['approval_detection']['approver_names']['vp'], 'Recovered VP')
        self.assertEqual(preview['approval_detection']['approval_rows'][-1]['name'], 'Recovered VP')
        self.assertEqual(len(preview['approval_detection']['captured_approval_rows']), 2)
        before_audit = deepcopy(pr.price_remarks_data['source_approval_reviews'])
        self.save_review(self.missing_review(), expected=self.missing_review(), existing=pr,
                         approvals={'vp': 'Recovered VP'}, manual_signature_overrides={'vp': True})
        pr.refresh_from_db()
        self.assertEqual(pr.price_remarks_data['source_approval_reviews'], before_audit)

    def test_missed_role_name_without_signature_stays_draft(self):
        self.missed_vp()
        result = self.save_review(self.missing_review(), approvals={'vp': 'Recovered VP'}, signatures_verified=True)
        self.assertFalse(result['document_signed_off'])
        pr = PurchaseRequisition.objects.get(pk=result['pr_id'])
        self.assertEqual(pr.status, 'draft')
        self.assertFalse(pr.price_remarks_data['signed_document_verification']['source_approval_rows'][-1]['signature_verified'])

    def test_missed_role_requires_source_name_and_note_not_blanket_verification(self):
        self.missed_vp()
        with self.assertRaises(SignedPRImportError):
            self.save_review(approvals={'vp': 'Recovered VP'}, manual_signature_overrides={'vp': True})
        self.assertFalse(PurchaseRequisition.objects.exists())
        self.storage.save.assert_not_called()

    def test_unsigned_known_signer_can_be_corrected_with_note_and_explicit_verification(self):
        self.evidence['approval_rows'][-1]['signature_detected'] = False
        self.evidence['signatures']['vp'] = False
        result = self.save_review(self.missing_review(), approvals={'vp': 'Corrected VP'}, manual_signature_overrides={'vp': True})
        pr = PurchaseRequisition.objects.get(pk=result['pr_id'])
        self.assertTrue(result['document_signed_off'])
        self.assertEqual(pr.approval_workflow_config[-1]['user_name'], 'Corrected VP')
        self.assertNotEqual(pr.price_remarks_data['signed_approval_evidence']['rows'][-1]['name'], 'Corrected VP')

    def test_duplicate_source_names_and_signature_states_remain_individual(self):
        duplicate = {**self.evidence['approval_rows'][0], 'source_role': 'PM,PD', 'name': 'Other Project Director', 'signature_detected': False}
        self.evidence['approval_rows'].insert(1, duplicate)
        result = self.import_document()
        pr = PurchaseRequisition.objects.get(pk=result['pr_id'])
        rows = pr.price_remarks_data['signed_document_verification']['source_approval_rows']
        self.assertEqual(rows[1]['user_name'], 'Other Project Director')
        self.assertFalse(rows[1]['signature_verified'])
        self.assertEqual(rows[1]['signature_source'], 'missing')
        self.assertFalse(result['document_signed_off'])
        with self.assertRaises(SignedPRImportError):
            self.import_document(existing=pr, manual_signature_overrides={'pm': True})

    def test_one_unknown_duplicate_name_can_be_corrected_without_replacing_other_signer(self):
        self.evidence['approval_rows'].insert(1, {**self.evidence['approval_rows'][0], 'source_role': 'PM,PD', 'name': 'Not detected'})
        review = {'approval_labels': {}, 'additional_approvers': [], 'approver_notes': {'pm': 'Read the second signature caption.'}}
        result = self.save_review(review, approvals={'pm': 'Recovered Project Director'})
        pr = PurchaseRequisition.objects.get(pk=result['pr_id'])
        self.assertEqual(pr.approval_workflow_config[0]['user_name'], self.approvers['pm'].get_full_name())
        self.assertEqual(pr.approval_workflow_config[1]['user_name'], 'Recovered Project Director')
        self.assertEqual(pr.price_remarks_data['signed_approval_evidence']['rows'][1]['name'], 'Not detected')

    def test_completed_named_signer_stays_protected_when_another_role_is_unsigned(self):
        self.evidence['approval_rows'][-1]['signature_detected'] = False
        self.evidence['signatures']['vp'] = False
        review = {'approval_labels': {}, 'additional_approvers': [], 'approver_notes': {'pm': 'Attempted replacement.'}}
        with self.assertRaises(SignedPRImportError):
            self.save_review(review, approvals={'pm': 'Replacement PM'})
        self.assertFalse(PurchaseRequisition.objects.exists())

    def duplicate_correction(self, *, name='Second Director', verified=True):
        duplicate = {**self.evidence['approval_rows'][0], 'source_role': 'PM,PD', 'name': '', 'signature_detected': False}
        self.evidence['approval_rows'].insert(1, duplicate)
        preview = preview_signed_pr_pdf(self.source, filename='signed.pdf')
        return {'row_index': 1, 'expected_row': preview['approval_detection']['approval_rows'][1],
                'approver_name': name, 'signature_verified': verified, 'approval_label': 'Reviewed',
                'special_note': 'Individually read and checked the second PM/PD row.'}

    def test_duplicate_row_correction_verifies_only_target_and_restores_preview(self):
        correction = self.duplicate_correction()
        result = self.import_document(source_row_corrections=[correction])
        pr = PurchaseRequisition.objects.get(pk=result['pr_id'])
        self.assertTrue(result['document_signed_off'])
        self.assertEqual(pr.approval_workflow_config[0]['user_name'], self.approvers['pm'].get_full_name())
        self.assertEqual(pr.approval_workflow_config[1]['user_name'], 'Second Director')
        self.assertEqual(pr.approval_workflow_config[1]['approval_label'], 'Reviewed')
        self.assertFalse(pr.price_remarks_data['signed_approval_evidence']['manual_signature_overrides']['pm'])
        self.assertFalse(pr.price_remarks_data['signed_approval_evidence']['rows'][1]['signature_detected'])
        preview = preview_signed_pr_pdf(self.source, filename='signed.pdf')
        self.assertTrue(preview['document_signed_off'])
        row = preview['approval_detection']['approval_rows'][1]
        self.assertEqual(row['name'], 'Second Director')
        self.assertTrue(row['signature_verified'])
        self.assertEqual(row['special_note'], correction['special_note'])
        self.import_document(existing=pr, source_row_corrections=[correction])
        pr.refresh_from_db()
        self.assertEqual(len(pr.price_remarks_data['source_approval_reviews']), 1)

    def test_duplicate_row_name_only_correction_does_not_approve_missing_signature(self):
        correction = self.duplicate_correction(verified=False)
        result = self.import_document(source_row_corrections=[correction])
        pr = PurchaseRequisition.objects.get(pk=result['pr_id'])
        self.assertEqual(pr.status, 'draft')
        self.assertFalse(result['document_signed_off'])
        rows = pr.price_remarks_data['signed_document_verification']['source_approval_rows']
        self.assertFalse(rows[1]['signature_verified'])

    def test_stale_duplicate_row_snapshot_rejects_without_file_or_record_mutation(self):
        correction = self.duplicate_correction()
        correction['expected_row']['name'] = 'Stale OCR'
        with self.assertRaises(SourceApprovalReviewConflict):
            self.import_document(source_row_corrections=[correction])
        self.assertFalse(PurchaseRequisition.objects.exists())
        self.storage.save.assert_not_called()

    def test_row_correction_rejects_completed_names_and_invalid_payloads(self):
        correction = self.duplicate_correction()
        invalid = [None, {}, [dict(correction, signature_verified='true')], [dict(correction, special_note='')],
                   [dict(correction, row_index=True)], [correction, correction], [dict(correction, approved_by='x')]]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(SignedPRImportError):
                self.import_document(source_row_corrections=payload)
        completed = dict(correction, row_index=0, expected_row=deepcopy(self.evidence['approval_rows'][0]))
        with self.assertRaises(SignedPRImportError):
            self.import_document(source_row_corrections=[completed])

    def test_stale_absent_role_caller_cannot_overwrite_new_pending_name_or_verify_without_snapshot(self):
        self.missed_vp()
        result = self.save_review(self.missing_review(), approvals={'vp': 'First Reviewed VP'})
        pr = PurchaseRequisition.objects.get(pk=result['pr_id'])
        before = PurchaseRequisition.objects.filter(pk=pr.pk).values().get()
        for changes in ({'approvals': {'vp': 'Second Reviewer VP'}}, {'manual_signature_overrides': {'vp': True}}):
            with self.subTest(changes=changes), self.assertRaises(SourceApprovalReviewConflict):
                self.save_review(self.missing_review(), existing=pr, **changes)
        self.assertEqual(PurchaseRequisition.objects.filter(pk=pr.pk).values().get(), before)


@override_settings(ROOT_URLCONF=pair_fixtures.originating.__name__)
class PairedSourceRowCorrectionTests(TestCase):
    setUp = pair_fixtures.PairedSignedImportTests.setUp
    grant = pair_fixtures.PairedSignedImportTests.grant
    revoke = pair_fixtures.PairedSignedImportTests.revoke
    source_files = pair_fixtures.PairedSignedImportTests.source_files
    upload = pair_fixtures.PairedSignedImportTests.upload
    assert_no_pair = pair_fixtures.PairedSignedImportTests.assert_no_pair

    def correction_payload(self):
        self.pr_evidence['approval_rows'] = [
            {'role_key': role, 'source_role': role.upper(), 'name': name, 'signature_detected': True}
            for role, name in self.pr_evidence['approver_names'].items()
        ]
        row = self.pr_evidence['approval_rows'][0]
        self.pr_evidence['approval_rows'].insert(1, {**row, 'source_role': 'PM,PD', 'name': '', 'signature_detected': False})
        return json.dumps([{'row_index': 1, 'expected_row': self.pr_evidence['approval_rows'][1],
                           'approver_name': 'Second Source Director', 'signature_verified': True,
                           'special_note': 'Verified second director independently.'}])

    def test_pair_per_row_correction_and_cached_retry_acknowledge_same_row_once(self):
        payload = self.correction_payload()
        response = self.upload(source_row_corrections=payload)
        self.assertEqual(response.status_code, 201, response.data)
        before = PurchaseRequisition.objects.values().get()
        replay = self.upload(source_row_corrections=payload)
        self.assertEqual(replay.status_code, 200, replay.data)
        row = replay.data['approval_detection']['approval_rows'][1]
        self.assertEqual(row['name'], 'Second Source Director')
        self.assertTrue(row['signature_verified'])
        self.assertEqual(PurchaseRequisition.objects.values().get(), before)
        self.assertEqual(len(before['price_remarks_data']['source_approval_reviews']), 1)

    def test_paired_failure_rolls_back_row_review_and_source_files(self):
        payload = self.correction_payload()
        with patch('apps.procurement.services.signed_po_pdf_import.extract_signed_po_fields',
                   side_effect=SignedPOImportError('Cannot read PO.')):
            response = self.upload(source_row_corrections=payload)
        self.assertEqual(response.status_code, 400, response.data)
        self.assert_no_pair()

    def test_row_correction_on_attachment_requires_update_grant(self):
        payload = self.correction_payload()
        response = self.upload(source_row_corrections=payload)
        self.assertEqual(response.status_code, 201, response.data)
        before = PurchaseRequisition.objects.values().get()
        self.revoke('procurement_requisitions', 'update')
        response = self.upload(paired=False, create_new='false', expected_pr_number=self.pr_number,
                               source_row_corrections=payload)
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(PurchaseRequisition.objects.values().get(), before)
