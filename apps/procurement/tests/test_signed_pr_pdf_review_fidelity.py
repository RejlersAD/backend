"""Reviewed financial rows retain source evidence and cannot silently lose detail."""
from copy import deepcopy
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from apps.procurement.services.signed_pr_pdf_import import SignedPRImportError, _apply_manual_overrides


class SignedPRReviewFidelityTests(SimpleTestCase):
    def setUp(self):
        self.fields = {
            "pr_number": "RAD-PRJ-PR-0002_2026", "issued_by_name": "Document Issuer",
            "issued_date": date(2025, 12, 31), "product_service": "Engineering package",
            "supplier_name": "Example Supplier", "description_reason": "Engineering package",
            "net_total": Decimal("225608.00"), "currency": "USD",
            "price_remarks": "Sales Budget USD225,608.00", "net_total_aed": "",
            "price_lines": [
                {"description": "Package A", "total": "200000.00", "currency": "USD", "remarks": "Sales Budget USD225,608.00"},
                {"description": "Package B", "total": "25608.00", "currency": "USD", "remarks": "Subcontract"},
            ],
            "field_provenance": {"price": {"source": "labeled_net_total", "evidence": "Net Total USD225,608.00"}},
        }

    def test_unchanged_review_preserves_both_rows_remarks_and_source(self):
        before = deepcopy(self.fields)
        result = _apply_manual_overrides(self.fields, {"net_total": "225608.00", "currency": "USD"})
        self.assertEqual(result["price_lines"], before["price_lines"])
        self.assertEqual(result["source_price_lines"], before["price_lines"])
        self.assertEqual(result["field_provenance"]["net_total"]["evidence"], "Net Total USD225,608.00")
        self.assertFalse(result["field_provenance"]["net_total"]["changed"])
        self.assertEqual(result["net_total_aed"], "")
        self.assertEqual(self.fields, before)

    def test_rejects_total_correction_that_drops_multi_row_prices(self):
        with self.assertRaisesMessage(SignedPRImportError, "do not add up"):
            _apply_manual_overrides(self.fields, {"net_total": "225000.00"})

    def test_conflicting_single_row_cannot_be_silently_replaced_by_net_total(self):
        self.fields["price_lines"] = [{"description": "Engineering", "total": "230000.00", "currency": "USD"}]
        with self.assertRaisesMessage(SignedPRImportError, "do not add up"):
            _apply_manual_overrides(self.fields, {"net_total": "225608.00", "price_lines": self.fields["price_lines"]})

    def test_accepts_corrected_rows_and_retains_original_rows(self):
        rows = deepcopy(self.fields["price_lines"])
        rows[1]["total"] = "25000.00"
        result = _apply_manual_overrides(self.fields, {"net_total": "225000.00", "price_lines": rows})
        self.assertEqual(result["price_lines"][1]["total"], "25000.00")
        self.assertEqual(result["source_price_lines"][1]["total"], "25608.00")

    def test_invalid_row_amount_and_mixed_currency_are_rejected(self):
        for value in ("NaN", "Infinity", "-1", "0.001", "1e100", "invalid"):
            rows = deepcopy(self.fields["price_lines"])
            rows[0]["total"] = value
            with self.subTest(value=value), self.assertRaises(SignedPRImportError):
                _apply_manual_overrides(self.fields, {"price_lines": rows})
        rows = deepcopy(self.fields["price_lines"])
        rows[1]["currency"] = "AED"
        with self.assertRaisesMessage(SignedPRImportError, "currency does not match"):
            _apply_manual_overrides(self.fields, {"price_lines": rows})

    def test_single_row_total_correction_keeps_description_and_remarks(self):
        self.fields["price_lines"] = [{"description": "Source description", "total": "225608.00", "currency": "USD", "remarks": "Source remarks"}]
        result = _apply_manual_overrides(self.fields, {"net_total": "225600.00", "price_remarks": "Reviewed remarks"})
        self.assertEqual(result["price_lines"], [{"description": "Source description", "total": "225600.00", "currency": "USD", "remarks": "Reviewed remarks"}])

    def test_row_remark_edit_survives_unchanged_summary(self):
        self.fields["price_lines"] = [{"description": "Engineering", "total": "225608.00", "currency": "USD", "remarks": self.fields["price_remarks"]}]
        rows = deepcopy(self.fields["price_lines"])
        rows[0]["remarks"] = "Corrected from PDF"
        result = _apply_manual_overrides(self.fields, {"price_remarks": self.fields["price_remarks"], "price_lines": rows})
        self.assertEqual(result["price_remarks"], "Corrected from PDF")
