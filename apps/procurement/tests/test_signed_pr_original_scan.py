"""Opt-in regression using a private PDF and a private expected-values JSON.

Set RADAI_PR_REGRESSION_PDF and RADAI_PR_REGRESSION_EXPECTED to local files.
The expected JSON contains ``fields`` and ``approvals`` objects. Fields include
identity, dates, supplier, notes, currency, amounts and price_lines. Approvals
include approver_names, signatures, role_keys (or approval_rows), approval_date
(or allowed_approval_dates), all_four_signatures, and document_signed_off.
Neither the source PDF nor its expected business data belongs in this repository.
Only extraction runs; no document, approval or business record is saved.
"""
from datetime import date
from decimal import Decimal
import json
import os
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from rest_framework.renderers import JSONRenderer

from apps.procurement.models import PurchaseRequisition
from apps.procurement.services.signed_pr_pdf_import import (
    detect_approval_evidence,
    extract_signed_pr_fields,
    preview_signed_pr_pdf,
)


SOURCE_PATH = os.environ.get("RADAI_PR_REGRESSION_PDF", "")
EXPECTED_PATH = os.environ.get("RADAI_PR_REGRESSION_EXPECTED", "")
PRIVATE_INPUTS_AVAILABLE = bool(
    SOURCE_PATH and EXPECTED_PATH
    and Path(SOURCE_PATH).is_file() and Path(EXPECTED_PATH).is_file()
)
SKIP_REASON = "Set RADAI_PR_REGRESSION_PDF and RADAI_PR_REGRESSION_EXPECTED to private local files"


def expected_values():
    return json.loads(Path(EXPECTED_PATH).read_text(encoding="utf-8"))


def expected_date(value):
    return date.fromisoformat(value) if value else None


@skipUnless(PRIVATE_INPUTS_AVAILABLE, SKIP_REASON)
class OriginalSignedPRScanTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.expected = expected_values()
        cls.source = Path(SOURCE_PATH).read_bytes()
        cls.fields = extract_signed_pr_fields(
            cls.source, Path(SOURCE_PATH).name, _include_source=True,
        )
        layout_source = cls.fields.pop("_layout_source")
        cls.approvals = detect_approval_evidence(cls.source, _source=layout_source)

    def test_original_scan_recovers_financial_fields(self):
        expected = self.expected["fields"]
        self.assertEqual(self.fields["net_total"], Decimal(str(expected["net_total"])))
        self.assertEqual(self.fields["currency"], expected["currency"])
        self.assertEqual(len(self.fields["price_lines"]), len(expected["price_lines"]))
        for actual_line, expected_line in zip(self.fields["price_lines"], expected["price_lines"]):
            self.assertEqual(Decimal(str(actual_line["total"])), Decimal(str(expected_line["total"])))
            for field in ("description", "currency", "remarks"):
                self.assertEqual(actual_line.get(field, ""), expected_line.get(field, ""))
        for field in ("price_remarks", "net_total_aed", "budget_in_aed"):
            self.assertEqual(str(self.fields[field]), str(expected[field]))

    def test_original_scan_recovers_identity_dates_and_notes(self):
        expected = self.expected["fields"]
        for field in (
            "pr_number", "issued_by_name", "project_number", "supplier_name",
            "product_service", "po_reference", "special_notes",
        ):
            self.assertEqual(self.fields[field], expected[field])
        self.assertEqual(self.fields["issued_date"], expected_date(expected["issued_date"]))

    def test_original_source_approval_roles_names_and_signatures(self):
        expected = self.expected["approvals"]
        self.assertEqual(self.approvals["approver_names"], expected["approver_names"])
        roles = expected.get("role_keys")
        if roles is None:
            roles = [row["role_key"] for row in expected["approval_rows"]]
        self.assertEqual([row["role_key"] for row in self.approvals["approval_rows"]], roles)
        self.assertEqual(self.approvals["signatures"], expected["signatures"])
        self.assertEqual(self.approvals["all_four_signatures"], expected["all_four_signatures"])
        allowed_dates = expected.get("allowed_approval_dates", [expected.get("approval_date")])
        self.assertIn(self.approvals.get("approval_date"), [expected_date(value) for value in allowed_dates])


@skipUnless(PRIVATE_INPUTS_AVAILABLE, SKIP_REASON)
class OriginalSignedPRPreviewTests(TestCase):
    def test_original_scan_preview_serializes_expected_fields_without_writes(self):
        expected = expected_values()
        with patch("apps.procurement.services.signed_pr_pdf_import.default_storage") as storage:
            response = preview_signed_pr_pdf(
                Path(SOURCE_PATH).read_bytes(), filename=Path(SOURCE_PATH).name,
            )
        encoded = JSONRenderer().render(response)
        serialized = json.loads(encoded)
        self.assertEqual(
            Decimal(str(serialized["extracted_data"]["net_total"])),
            Decimal(str(expected["fields"]["net_total"])),
        )
        self.assertEqual(serialized["extracted_data"]["po_reference"], expected["fields"]["po_reference"])
        self.assertNotIn(b"_layout_source", encoded)
        self.assertTrue(response["preview_only"])
        self.assertEqual(response["document_signed_off"], expected["approvals"]["document_signed_off"])
        self.assertEqual(response["extracted_data"]["currency"], expected["fields"]["currency"])
        self.assertEqual(response["approval_detection"]["approver_names"], expected["approvals"]["approver_names"])
        self.assertFalse(PurchaseRequisition.objects.exists())
        storage.save.assert_not_called()
