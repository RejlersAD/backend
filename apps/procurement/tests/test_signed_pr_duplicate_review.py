"""Duplicate signed PDFs keep review evidence only for the same source bytes."""

from copy import deepcopy
from datetime import date
import hashlib

from django.test import TestCase

from apps.procurement.models import PurchaseRequisition
from apps.procurement.services.signed_pr_pdf_import import SignedPRImportError, import_signed_pr_pdf
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
        pr = self.first_review()
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
        self.assertEqual(self.storage.save.call_count, 1)

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
