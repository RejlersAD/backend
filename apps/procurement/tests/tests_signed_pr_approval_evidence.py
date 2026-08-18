from django.test import SimpleTestCase

from apps.procurement.services.signed_pr_pdf_import import (
    SignedPRImportError,
    _apply_manual_signature_overrides,
)


class SignedPRApprovalEvidenceTests(SimpleTestCase):
    def test_manual_verification_supplements_failed_detection(self):
        detected = {
            "signatures": {"pm": True, "moe": False, "mop": True, "vp": False},
            "all_four_signatures": False,
        }

        reviewed = _apply_manual_signature_overrides(detected, {"moe": True, "vp": True})

        self.assertTrue(reviewed["all_four_signatures"])
        self.assertEqual(reviewed["signature_sources"], {
            "pm": "automatic", "moe": "manual", "mop": "automatic", "vp": "manual",
        })
        self.assertEqual(reviewed["automated_signatures"], detected["signatures"])

    def test_manual_verification_rejects_unconfirmed_values(self):
        with self.assertRaisesMessage(SignedPRImportError, "confirmed with true"):
            _apply_manual_signature_overrides({"signatures": {}}, {"pm": False})
