"""Duplicate signed PDFs keep review evidence only for the same source bytes."""

from copy import deepcopy
from datetime import date
import hashlib

from botocore.exceptions import ClientError
from django.test import TestCase
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from apps.procurement.models import PurchaseRequisition
from apps.procurement.services.signed_pr_pdf_import import SignedPRImportError, SignedPRStorageError, import_signed_pr_pdf
from apps.procurement.tests import test_signed_pr_pdf_creation as creation_fixtures


class DuplicateSignedPRReviewTests(TestCase):
    # Reuse fixture construction without inheriting (and rerunning) its tests.
    _patch = creation_fixtures.SignedPRPdfCreationTests._patch
    _use_three_signed_source_rows = creation_fixtures.SignedPRPdfCreationTests._use_three_signed_source_rows

    def setUp(self):
        creation_fixtures.SignedPRPdfCreationTests.setUp(self)
        self._use_three_signed_source_rows()
        self.evidence["signatures"]["vp"] = False
        self.evidence["approval_date"] = None
        for row in self.evidence["approval_rows"]:
            if row["role_key"] == "vp":
                row["signature_detected"] = False
        self.source_bytes = b"%PDF-reviewed-version-one"

    def import_document(self, pdf_bytes=None, *, existing=None, **kwargs):
        arguments = {"filename": "signed.pdf", "uploaded_by": self.reviewer}
        if existing is None:
            arguments.update(create_new=True, manual_overrides=self.reviewed)
        else:
            arguments.update(create_new=False, attach_only=True, expected_pr_number=existing.pr_number)
        arguments.update(kwargs)
        return import_signed_pr_pdf(pdf_bytes or self.source_bytes, **arguments)

    def first_review(self):
        result = self.import_document(manual_signature_overrides={"vp": True}, approval_date="2026-01-07")
        pr = PurchaseRequisition.objects.get(pk=result["pr_id"])
        self.assertTrue(result["document_signed_off"])
        self.assertEqual(pr.status, "approved")
        self.assertEqual(pr.approved_at.date(), date(2026, 1, 7))
        return pr

    def test_same_pdf_preserves_manual_signature_and_approval_date_without_new_overrides(self):
        before_upload = timezone.now()
        pr = self.first_review()
        uploaded_at = pr.attachments[0]['uploaded_at']
        self.assertGreaterEqual(parse_datetime(uploaded_at), before_upload)
        self.assertLessEqual(parse_datetime(uploaded_at), timezone.now())
        result = self.import_document(existing=pr)
        pr.refresh_from_db()
        self.assertTrue(result["document_signed_off"])
        self.assertEqual(pr.status, "approved")
        self.assertEqual(pr.approved_at.date(), date(2026, 1, 7))
        verification = pr.price_remarks_data["signed_document_verification"]
        self.assertEqual(verification["document_sha256"], hashlib.sha256(self.source_bytes).hexdigest())
        self.assertEqual(verification["approval_date"], "2026-01-07")
        self.assertEqual(verification["approval_date_source"], "manual")
        self.assertTrue(pr.price_remarks_data["signed_approval_evidence"]["manual_signature_overrides"]["vp"])
        self.assertEqual(len(pr.attachments), 1)
        self.assertEqual(pr.attachments[0]['uploaded_at'], uploaded_at)
        self.assertEqual(self.storage.save.call_count, 1)

    def test_same_pdf_repairs_missing_original_in_place_without_changing_commercial_values(self):
        pr = self.first_review()
        old_key = pr.attachments[0]['storage_key']
        original_total, original_items = pr.total_price, deepcopy(pr.items)
        self.storage.reset_mock()
        self.storage.exists.return_value = False

        result = self.import_document(existing=pr)

        self.storage.exists.assert_called_once_with(old_key)
        self.storage.save.assert_called_once()
        key, content = self.storage.save.call_args.args
        self.assertTrue(key.startswith(f'procurement/signed_requisitions/{pr.pk}/'))
        self.assertEqual(content.read(), self.source_bytes)
        self.storage.delete.assert_not_called()
        pr.refresh_from_db()
        self.assertTrue(result['document_signed_off'])
        self.assertEqual(len(pr.attachments), 1)
        self.assertEqual(pr.attachments[0]['storage_key'], key)
        self.assertEqual(pr.total_price, original_total)
        self.assertEqual(pr.items, original_items)
        self.assertEqual(pr.status, 'approved')
        self.assertEqual(pr.approved_at.date(), date(2026, 1, 7))

    def test_same_pdf_repairs_unresolvable_source_without_accessing_unrelated_key(self):
        pr = self.first_review()
        pr.attachments[0].update(storage_key='private/unrelated.pdf', s3_key='private/unrelated.pdf')
        pr.save(update_fields=['attachments'])
        self.storage.reset_mock()

        self.import_document(existing=pr)

        self.storage.exists.assert_not_called()
        self.storage.delete.assert_not_called()
        self.storage.save.assert_called_once()
        pr.refresh_from_db()
        self.assertEqual(len(pr.attachments), 1)
        self.assertTrue(pr.attachments[0]['storage_key'].startswith(f'procurement/signed_requisitions/{pr.pk}/'))
        self.assertNotIn('s3_key', pr.attachments[0])
        self.assertEqual(pr.attachments[0]['sha256'], hashlib.sha256(self.source_bytes).hexdigest())

    def test_same_hash_quotation_does_not_replace_designated_original(self):
        pr = self.first_review()
        pr.attachments[0].update(type='quotation', document_type='quotation')
        quotation = deepcopy(pr.attachments[0])
        pr.save(update_fields=['attachments'])
        self.storage.reset_mock()

        self.import_document(existing=pr)

        self.storage.save.assert_called_once()
        pr.refresh_from_db()
        self.assertEqual(len(pr.attachments), 2)
        self.assertEqual(pr.attachments[0], quotation)
        self.assertEqual(pr.attachments[1]['type'], 'signed_purchase_requisition_pdf')

    def test_storage_failure_during_reattach_does_not_save_false_success_or_change_evidence(self):
        pr = self.first_review()
        original = deepcopy(pr.attachments)
        metadata = deepcopy(pr.price_remarks_data)
        failures = [
            ('exists', OSError('private storage details')),
            ('exists', ClientError({'Error': {'Code': 'AccessDenied', 'Message': 'private bucket'}}, 'HeadObject')),
            ('save', OSError('private storage details')),
            ('url', OSError('private signing details')),
        ]
        for operation, failure in failures:
            with self.subTest(operation=operation, failure=type(failure).__name__):
                self.storage.reset_mock()
                self.storage.exists.side_effect = failure if operation == 'exists' else None
                self.storage.exists.return_value = False
                self.storage.save.side_effect = failure if operation == 'save' else lambda key, content: key
                self.storage.url.side_effect = failure if operation == 'url' else lambda key: '/media/' + key
                with self.assertRaisesRegex(SignedPRStorageError, 'could not be stored or verified') as error:
                    self.import_document(existing=pr)
                self.assertNotIn('private', str(error.exception))
                if operation == 'exists':
                    self.storage.save.assert_not_called()
                self.storage.delete.assert_not_called()
                pr.refresh_from_db()
                self.assertEqual(pr.attachments, original)
                self.assertEqual(pr.price_remarks_data, metadata)
                self.assertEqual(pr.status, 'approved')

    def test_remaining_same_pdf_signature_supplements_prior_partial_manual_review(self):
        self.evidence['signatures'] = {role: False for role in self.approvers}
        self.evidence['approval_rows'] = [
            {'source_role': role.upper(), 'role_key': role, 'name': user.get_full_name(),
             'signature_detected': False, 'signature_candidate': True, 'page': 1}
            for role, user in self.approvers.items()
        ]
        self.evidence['approver_names'] = {
            role: user.get_full_name() for role, user in self.approvers.items()
        }
        self.evidence['signature_candidates'] = {role: True for role in self.approvers}
        self.evidence['signature_density'] = {role: 0.005 for role in self.approvers}
        self.evidence['approval_evidence_issues'] = ['Some signature cells contain uncertain ink.']
        first = self.import_document(
            manual_signature_overrides={'pm': True, 'moe': True, 'mop': True},
            approval_date='2026-01-29',
        )
        pr = PurchaseRequisition.objects.get(pk=first['pr_id'])
        self.assertEqual(pr.status, 'draft')
        self.assertEqual(pr.approval_workflow_config, [])
        metadata = pr.price_remarks_data
        history = metadata['signed_document_verification']['source_approval_rows']
        self.assertEqual([row['signature_source'] for row in history], ['manual'] * 3 + ['missing'])
        self.assertTrue(history[-1]['signature_candidate'])
        self.assertFalse(history[-1]['signature_verified'])
        self.assertEqual(history[-1]['status'], 'not_recorded')
        self.assertEqual(metadata['signed_approval_evidence']['signature_density'], self.evidence['signature_density'])
        self.assertEqual(metadata['signed_approval_evidence']['approval_evidence_issues'], self.evidence['approval_evidence_issues'])

        result = self.import_document(existing=pr, manual_signature_overrides={'vp': True})
        pr.refresh_from_db()
        self.assertTrue(result['document_signed_off'])
        self.assertEqual(pr.status, 'approved')
        self.assertEqual(pr.approved_at.date(), date(2026, 1, 29))
        self.assertEqual([row['status'] for row in pr.approval_workflow_config], ['approved'] * 4)
        self.assertTrue(all(pr.price_remarks_data['signed_approval_evidence']['manual_signature_overrides'].values()))
        self.assertEqual(self.storage.save.call_count, 1)

    def test_new_source_bytes_do_not_inherit_partial_signature_confirmations(self):
        for row in self.evidence['approval_rows']:
            row['signature_detected'] = False
        self.evidence['signatures'] = {role: False for role in self.approvers}
        first = self.import_document(manual_signature_overrides={'pm': True, 'mop': True})
        pr = PurchaseRequisition.objects.get(pk=first['pr_id'])
        result = self.import_document(b'%PDF-other-source', existing=pr, manual_signature_overrides={'vp': True})
        pr.refresh_from_db()
        self.assertFalse(result['document_signed_off'])
        self.assertEqual(pr.status, 'draft')
        self.assertEqual(pr.approval_workflow_config, [])
        overrides = pr.price_remarks_data['signed_approval_evidence']['manual_signature_overrides']
        self.assertEqual(overrides, {'pm': False, 'moe': False, 'mop': False, 'vp': True})

    def test_manual_review_can_complete_same_pdf_when_ocr_misses_entire_approval_table(self):
        self.evidence.update(
            table_detected=False, approval_rows=[],
            signatures={role: False for role in self.approvers},
            approver_names={role: '' for role in self.approvers},
        )
        names = {role: user.get_full_name() for role, user in self.approvers.items()}
        first = self.import_document(
            approvals=names, manual_signature_overrides={'pm': True, 'moe': True, 'mop': True},
            approval_date='2026-01-29',
            manual_overrides={**self.reviewed, 'net_total': '1500.75'},
        )
        pr = PurchaseRequisition.objects.get(pk=first['pr_id'])
        self.assertEqual(pr.status, 'draft')
        old_verification = deepcopy(pr.price_remarks_data['signed_document_verification'])
        old_corrected_fields = pr.price_remarks_data['manual_ocr_review']['corrected_fields']
        old_items = deepcopy(pr.items)
        old_source_price_lines = deepcopy(pr.price_remarks_data['price_lines'])
        result = self.import_document(existing=pr, manual_signature_overrides={'vp': True})
        pr.refresh_from_db()
        self.assertTrue(result['document_signed_off'])
        self.assertEqual(pr.status, 'approved')
        self.assertEqual(pr.approved_at.date(), date(2026, 1, 29))
        self.assertEqual([stage['user_name'] for stage in pr.approval_workflow_config], list(names.values()))
        evidence = pr.price_remarks_data['signed_approval_evidence']
        self.assertFalse(evidence['table_detected'])
        self.assertTrue(evidence['manual_table_reviewed'])
        self.assertFalse(any(evidence['automated_signatures'].values()))
        self.assertEqual(evidence['reviewed_approver_names'], names)
        verification = pr.price_remarks_data['signed_document_verification']
        self.assertEqual(verification['source_fields'], old_verification['source_fields'])
        self.assertEqual(verification['approved_fields'], old_verification['approved_fields'])
        self.assertEqual(verification['approved_fields']['net_total'], '1500.75')
        self.assertEqual(str(pr.total_price), '1500.75')
        self.assertEqual(pr.items, old_items)
        self.assertEqual(pr.price_remarks_data['price_lines'], old_source_price_lines)
        self.assertEqual(pr.price_remarks_data['manual_ocr_review']['corrected_fields'], old_corrected_fields)

    def test_no_detected_table_cannot_approve_from_names_or_unidentified_manual_signatures(self):
        self.evidence.update(
            table_detected=False, approval_rows=[],
            signatures={role: False for role in self.approvers},
            approver_names={role: '' for role in self.approvers},
        )
        names = {role: user.get_full_name() for role, user in self.approvers.items()}
        for arguments in (
            {'approvals': names, 'signatures_verified': True},
            {'manual_signature_overrides': {role: True for role in self.approvers}},
            {'approvals': {**names, 'vp': ''},
             'manual_signature_overrides': {role: True for role in self.approvers}},
        ):
            with self.subTest(arguments=arguments):
                result = self.import_document(**arguments)
                pr = PurchaseRequisition.objects.get(pk=result['pr_id'])
                self.assertFalse(result['document_signed_off'])
                self.assertEqual(pr.status, 'draft')
                self.assertEqual(pr.approval_workflow_config, [])
                pr.delete()

    def assert_unsigned_replacement_keeps_approval(self, pr, pdf_bytes=None, **kwargs):
        original_metadata = deepcopy(pr.price_remarks_data)
        original_attachments = deepcopy(pr.attachments)
        with self.assertRaisesRegex(
            SignedPRImportError,
            "This PDF is not fully signed; the existing signed approval was kept",
        ):
            self.import_document(pdf_bytes, existing=pr, **kwargs)
        pr.refresh_from_db()
        self.assertEqual(pr.status, "approved")
        self.assertEqual(pr.approved_at.date(), date(2026, 1, 7))
        self.assertEqual(pr.price_remarks_data, original_metadata)
        self.assertEqual(pr.attachments, original_attachments)
        self.assertEqual(self.storage.save.call_count, 1)

    def test_different_unsigned_pdf_cannot_inherit_review_or_replace_signed_approval(self):
        self.assert_unsigned_replacement_keeps_approval(
            self.first_review(), b"%PDF-unreviewed-version-two",
        )

    def test_explicit_false_rejects_unsigned_replacement_and_keeps_prior_approval(self):
        self.assert_unsigned_replacement_keeps_approval(
            self.first_review(), signatures_verified=False,
        )

    def test_explicit_empty_overrides_rejects_replacement_without_reusing_previous_review(self):
        self.assert_unsigned_replacement_keeps_approval(
            self.first_review(), manual_signature_overrides={},
        )

    def test_invalid_nonempty_approval_date_is_rejected_before_storage(self):
        for invalid_date in ("not-a-date", "2026-02-30"):
            with self.subTest(approval_date=invalid_date), self.assertRaises(SignedPRImportError):
                self.import_document(manual_signature_overrides={"vp": True}, approval_date=invalid_date)
        self.storage.save.assert_not_called()
        self.assertFalse(PurchaseRequisition.objects.exists())

    def test_reimport_cannot_replace_reopened_round_or_its_archived_source(self):
        pr = self.first_review()
        previous_metadata = deepcopy(pr.price_remarks_data)
        previous_workflow = deepcopy(pr.approval_workflow_config)
        pr.price_remarks_data = {'approval_revision_history': [{
            'round': 1, 'rejection_reason': 'Correct the commercial description.',
            'approval_workflow_config': previous_workflow,
            'snapshot': {'price_remarks_data': previous_metadata},
        }]}
        pr.approval_workflow_config = [{
            'level': 1, 'role': 'Level 1 Approver', 'user_id': str(self.approvers['pm'].pk),
            'status': 'pending', 'assignment_id': 'current-review-assignment',
        }]
        pr.description_reason = 'Corrected commercial description awaiting fresh review.'
        pr.approved_at = None
        pr.approved_by = None
        for current_status in ('draft', 'submitted', 'in_review', 'rejected', 'approved'):
            pr.status = current_status
            pr.save()
            before = PurchaseRequisition.objects.filter(pk=pr.pk).values().get()
            for attach_only in (False, True):
                with self.subTest(status=current_status, attach_only=attach_only):
                    self.storage.reset_mock()
                    self.detect.reset_mock()
                    with self.assertRaisesRegex(SignedPRImportError, 'cannot replace its current approval round'):
                        self.import_document(
                            existing=pr, attach_only=attach_only,
                            manual_signature_overrides={'vp': True}, approval_date='2026-01-07',
                        )
                    self.assertEqual(PurchaseRequisition.objects.filter(pk=pr.pk).values().get(), before)
                    self.detect.assert_not_called()
                    self.assertEqual(self.storage.mock_calls, [])
