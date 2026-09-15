"""Extraction regressions using unrelated synthetic business documents."""

from decimal import Decimal
from unittest.mock import patch

import pymupdf
from django.test import SimpleTestCase

from apps.procurement.services.signed_pr_pdf_import import (
    SignedPRImportError,
    _apply_manual_overrides,
    extract_signed_pr_fields,
    extract_signed_pr_fields_from_text,
)


class SignedPRPriceExtractionTests(SimpleTestCase):
    def _extract(self, price_rows: str, net_total: str):
        ocr_text = f"""
        Purchase Requisition
        PR No. RAD-PRJ-PR-0721_2027 Date: 12.03.2027
        2. Preferred Supplier (if any): Example Supplier
        {price_rows}
        Net Total, excl VAT {net_total}
        4. Purchase Recommendation
        APPROVALS
        """
        with patch(
            "apps.procurement.services.signed_pr_pdf_import.extract_text_from_pdf_tesseract",
            return_value=ocr_text,
        ):
            return extract_signed_pr_fields(b"%PDF-test", "RAD-PRJ-PR-0721_2027.pdf")

    def test_extracts_currency_before_amount_and_generic_description(self):
        fields = self._extract(
            "Example Simulation License (2No) Lease USD 4,820.60 | Unit Price: $2,410.30",
            "USD 4,820.60",
        )

        self.assertEqual(fields["currency"], "USD")
        self.assertEqual(fields["net_total"], Decimal("4820.60"))
        self.assertEqual(fields["net_total_aed"], "")
        self.assertEqual(fields["field_confidence"]["net_total_aed"], "missing")
        self.assertEqual(fields["price_lines"], [{
            "description": "Example Simulation License (2No) Lease",
            "total": "4820.60",
            "currency": "USD",
            "remarks": "Unit Price: $2,410.30",
        }])

    def test_extracts_amount_before_currency(self):
        fields = self._extract("Engineering service 2,000.00 AED", "2,000.00 AED")

        self.assertEqual(fields["currency"], "AED")
        self.assertEqual(fields["net_total"], Decimal("2000.00"))
        self.assertEqual(fields["net_total_aed"], "2000.00")
        self.assertEqual(fields["price_lines"][0]["total"], "2000.00")

    def test_sums_labeled_price_rows_when_net_total_is_missing_from_ocr(self):
        ocr_text = """
        PR No. RAD-PRJ-PR-0722_2027 Date: 14.03.2027
        Issued by: Example Requester
        Product/ Service: Laboratory Services Supplier: Example Supplier
        Project/Department: 5907773 Test Project
        1. Description and Reason for Purchase: Laboratory Services
        2. Preferred Supplier (if any): Example Supplier
        For M/s ExampleLab USD 1,800.00 | Budget -> AED 44,000.00
        Agency fees USD 60.00
        PO Reference: RAD-PRJ-PUR-0722_MAR2027
        4. Special Notes: (If any) Synthetic training requirement
        APPROVALS
        """
        with patch(
            "apps.procurement.services.signed_pr_pdf_import.extract_text_from_pdf_tesseract",
            return_value=ocr_text,
        ):
            fields = extract_signed_pr_fields(b"%PDF-test", "RAD-PRJ-PR-0722_2027.pdf")

        self.assertEqual(fields["net_total"], Decimal("1860.00"))
        self.assertEqual([line["total"] for line in fields["price_lines"]], ["1800.00", "60.00"])
        self.assertEqual(fields["budget_in_aed"], "44000.00")

    def test_uses_repeated_detached_amount_as_row_and_net_total(self):
        ocr_text = """
        PR No. RAD-PRJ-PR-0723_2027 Date: 16.03.2027
        Issued by: Example Requester
        Product/ Service: License Supplier: Example Supplier
        Project/Department: 5907774 Test Project Supplier Business ID No.: CN-
        0007771
        1. Description and Reason for Purchase: Example Drawing License
        2. Preferred Supplier (if any): Example Supplier
        Total Price Remarks
        Supply of license:
        Net Total, excl VAT
        PO Reference: RAD-PRJ-PUR-0723_MAR2027
        4. Purchase Recommendation: 8% training discount applied.
        USD 684.52
        USD 684.52
        APPROVALS
        """
        with patch(
            "apps.procurement.services.signed_pr_pdf_import.extract_text_from_pdf_tesseract",
            return_value=ocr_text,
        ):
            fields = extract_signed_pr_fields(b"%PDF-test", "RAD-PRJ-PR-0723_2027.pdf")

        self.assertEqual(fields["net_total"], Decimal("684.52"))
        self.assertEqual(fields["supplier_business_id"], "CN-0007771")
        self.assertEqual(fields["special_notes"], "8% training discount applied.")


class SignedPRTableAndEvidenceTests(SimpleTestCase):
    # All identities, amounts, dates and notes below are synthetic test data.
    REFERENCE_TEXT = """
    Purchase Requisition
    Issued by: Example Requester | PR No. RAD-PRJ-PR-0724_2027 | Date: 18.03.2027
    Product/ Service: Synthetic Process Simulation Services for 5907771 | Supplier: Example Simulation Services Ltd
    Project/Department: SYNTHETIC DESIGN FOR THE TRAINING CONTROL SYSTEM AT THE EXAMPLE
    LABORATORY SITE (27041 & 27042), 5907771
    1. Description and Reason for Purchase:
    Synthetic Process Simulation Services for 5907771
    2. Preferred Supplier (if any): Example Simulation Services Ltd
    3. Price | Total Price | Remarks
    Synthetic Process Simulation Services for 5907771 | USD 146,320.00 | Sales Budget USD 146,320.00
    Net Total, excl VAT | USD 146,320.00
    PO Reference: RAD-PRJ-PUR-0724_MAR2027
    4. Special Notes: (If any)
    This is a synthetic training package. Test report is enclosed, & delivery checklist as attached,
    APPROVALS
    PD | Example Director
    MoP | Example Manager
    VP, Op | Example Operations
    Date | 22-03-2027
    """

    def parse(self, text=None):
        return extract_signed_pr_fields_from_text(text or self.REFERENCE_TEXT, "upload.pdf")

    def test_reference_preserves_financial_cells_notes_po_and_project_identity(self):
        fields = self.parse()
        self.assertEqual(fields["pr_number"], "RAD-PRJ-PR-0724_2027")
        self.assertEqual(fields["issued_by_name"], "Example Requester")
        self.assertEqual(str(fields["issued_date"]), "2027-03-18")
        self.assertEqual(fields["supplier_name"], "Example Simulation Services Ltd")
        self.assertEqual(fields["project_number"], "5907771")
        self.assertEqual(fields["project_numbers"], ["5907771"])
        self.assertEqual(fields["project_reference_numbers"], ["27041", "27042", "5907771"])
        self.assertEqual(fields["net_total"], Decimal("146320.00"))
        self.assertEqual(fields["currency"], "USD")
        self.assertEqual(fields["price_remarks"], "Sales Budget USD 146,320.00")
        self.assertEqual(fields["price_lines"], [{
            "description": "Synthetic Process Simulation Services for 5907771",
            "total": "146320.00", "currency": "USD",
            "remarks": "Sales Budget USD 146,320.00",
        }])
        self.assertEqual(fields["po_reference"], "RAD-PRJ-PUR-0724_MAR2027")
        self.assertEqual(fields["special_notes"], "This is a synthetic training package. Test report is enclosed, & delivery checklist as attached,")
        self.assertEqual(fields["budget_in_aed"], "")
        self.assertEqual(fields["net_total_aed"], "")
        self.assertEqual(fields["field_provenance"]["price"]["source"], "labeled_net_total")

    def test_row_remarks_without_vertical_borders_are_not_dropped_or_counted_twice(self):
        fields = self.parse(self.REFERENCE_TEXT.replace(" | ", "  "))
        self.assertEqual(fields["net_total"], Decimal("146320.00"))
        self.assertEqual(len(fields["price_lines"]), 1)
        self.assertEqual(fields["price_remarks"], "Sales Budget USD 146,320.00")
        self.assertEqual(fields["field_confidence"]["price"], "high")

    def test_label_punctuation_reference_spacing_and_money_spacing(self):
        text = self.REFERENCE_TEXT.replace("Net Total, excl VAT | USD 146,320.00", "Net Total (excl. VAT):\nUSD 146 320 .00")
        text = text.replace("PO Reference: RAD-PRJ-PUR-0724_MAR2027", "PO Reference :\nRAD - PRJ - PUR - 0724 _ MAR2027")
        text = text.replace("Date: 18.03.2027", "Date : 18/03/2027")
        text = text.replace("4. Special Notes: (If any)", "4) Special Notes (if any):")
        fields = self.parse(text)
        self.assertEqual(fields["net_total"], Decimal("146320.00"))
        self.assertEqual(fields["po_reference"], "RAD-PRJ-PUR-0724_MAR2027")
        self.assertEqual(str(fields["issued_date"]), "2027-03-18")
        self.assertTrue(fields["special_notes"].startswith("This is a synthetic"))

    def test_description_and_price_in_separate_rows_keep_remarks(self):
        row = "Synthetic Process Simulation Services for 5907771 | USD 146,320.00 | Sales Budget USD 146,320.00"
        replacement = "Synthetic Process Simulation Services for 5907771\nUSD 146,320.00\nSales Budget USD 146,320.00"
        fields = self.parse(self.REFERENCE_TEXT.replace(row, replacement))
        self.assertEqual(fields["price_lines"][0]["total"], "146320.00")
        self.assertEqual(fields["price_lines"][0]["remarks"], "Sales Budget USD 146,320.00")

    def test_mismatched_net_total_is_flagged_instead_of_silently_trusted(self):
        fields = self.parse(self.REFERENCE_TEXT.replace("Net Total, excl VAT | USD 146,320.00", "Net Total, excl VAT | USD 120,000.00"))
        self.assertEqual(fields["net_total"], Decimal("120000.00"))
        self.assertEqual(fields["field_confidence"]["price"], "conflict")
        self.assertTrue(any("differs from price-row total" in issue for issue in fields["extraction_issues"]))

    def test_mixed_currency_rows_do_not_produce_an_invented_total(self):
        text = self.REFERENCE_TEXT.replace("Net Total, excl VAT | USD 146,320.00", "Agency fees AED 100.00\nNet Total, excl VAT")
        fields = self.parse(text)
        self.assertIsNone(fields["net_total"])
        self.assertEqual(fields["currency"], "")
        self.assertEqual(fields["field_confidence"]["currency"], "conflict")

    def test_budget_only_is_not_a_purchase_price(self):
        text = "PR No. RAD-PRJ-PR-0724_2027\n2. Preferred Supplier: Supplier\n3. Price\nSales Budget USD 146,320.00\nNet Total, excl VAT\n4. Special Notes (if any): Budget USD 146,320.00\nAPPROVALS"
        fields = self.parse(text)
        self.assertIsNone(fields["net_total"])
        self.assertEqual(fields["price_lines"], [])

    def test_multiple_project_codes_require_selection(self):
        fields = self.parse(self.REFERENCE_TEXT.replace("(27041 & 27042), 5907771", "5907771 & 5907772"))
        self.assertEqual(fields["project_number"], "")
        self.assertEqual(fields["field_confidence"]["project_number"], "conflict")
        self.assertCountEqual(fields["project_numbers"], ["5907771", "5907772"])

    def test_notes_can_end_at_document_end_without_approval_heading(self):
        fields = self.parse(self.REFERENCE_TEXT.split("APPROVALS")[0])
        self.assertIn("delivery checklist as attached", fields["special_notes"])
        self.assertNotIn("Express delivery", fields["special_notes"])

    def test_ocr_values_are_reviewable_not_automatically_high_confidence(self):
        with patch("apps.procurement.services.signed_pr_pdf_import.extract_text_from_pdf_tesseract", return_value=self.REFERENCE_TEXT):
            fields = extract_signed_pr_fields(b"%PDF-test", "upload.pdf")
        self.assertEqual(fields["extraction_method"], "fallback")
        self.assertEqual(fields["field_confidence"]["price"], "medium")
        self.assertEqual(fields["field_provenance"]["special_notes"]["text_method"], "fallback")

    def test_unreadable_decimal_is_not_silently_truncated_to_an_integer(self):
        fields = self.parse(self.REFERENCE_TEXT.replace("146,320.00", "146,320.0O"))
        self.assertIsNone(fields["net_total"])
        self.assertEqual(fields["field_confidence"]["price"], "missing")

    def test_explicit_aed_budget_and_equivalent_are_preserved_without_exchange_math(self):
        text = self.REFERENCE_TEXT.replace("PO Reference:", "Net Total in AED: 537,412.75\nBudget: AED 610,000.00\nPO Reference:")
        fields = self.parse(text)
        self.assertEqual(fields["net_total_aed"], "537412.75")
        self.assertEqual(fields["budget_in_aed"], "610000.00")
        self.assertEqual(fields["field_provenance"]["net_total_aed"]["source"], "labeled_aed_total")

    def test_conflicting_repeated_net_total_labels_require_review(self):
        text = self.REFERENCE_TEXT.replace("PO Reference:", "Net Total, excl VAT USD 1,000.00\nPO Reference:")
        fields = self.parse(text)
        self.assertEqual(fields["field_confidence"]["price"], "conflict")
        self.assertTrue(any("Different labeled net totals" in issue for issue in fields["extraction_issues"]))

    @staticmethod
    def _native_table_pdf(net_total="USD 146,320.00"):
        """Build separate physical cells, deliberately out of reading order."""
        with pymupdf.open() as document:
            page = document.new_page(width=1200, height=900)
            cells = [
                (430, 65, "Purchase Requisition"),
                (50, 100, "Issued by: Example Requester"),
                (440, 100, "PR No. RAD-PRJ-PR-0724_2027"),
                (1000, 100, "Date: 18.03.2027"),
                (50, 130, "Product/Service: Synthetic Process Simulation Services for 5907771"),
                (780, 130, "Supplier: Example Simulation Services Ltd"),
                (50, 160, "Project/Department: SYNTHETIC DESIGN FOR THE TRAINING CONTROL SYSTEM AT THE EXAMPLE"),
                (50, 178, "LABORATORY SITE (27041 & 27042), 5907771"),
                (50, 210, "1. Description and Reason for Purchase:"),
                (50, 240, "Synthetic Process Simulation Services for 5907771"),
                (50, 290, "2. Preferred Supplier (if any): Example Simulation Services Ltd"),
                (50, 330, "3. Price"),
                (600, 330, "Total Price"),
                (820, 330, "Remarks"),
                (50, 370, "Synthetic Process Simulation Services for 5907771"),
                (600, 370, "USD 146,320.00"),
                (820, 370, "Sales Budget USD 146,320.00"),
                (50, 410, "Net Total, excl VAT"),
                (600, 410, net_total),
                (50, 450, "PO Reference: RAD-PRJ-PUR-0724_MAR2027"),
                (50, 485, "4. Special Notes: (If any)"),
                (50, 520, "This is a synthetic training package. Test report is enclosed, & delivery checklist as attached,"),
                (500, 680, "APPROVALS"),
                (50, 720, "PD"),
                (220, 720, "Example Director"),
                (50, 745, "MoP"),
                (220, 745, "Example Manager"),
            ]
            # A document commonly draws a complete column before its neighbor.
            # The extractor must recover physical rows instead of drawing order.
            for x, y, value in sorted(cells, key=lambda cell: (-cell[0], -cell[1])):
                page.insert_text((x, y), value, fontsize=10)
            for y in (80, 112, 142, 188, 315, 350, 390, 430, 470, 660, 700, 760):
                page.draw_line((40, y), (1160, y), color=(0, 0, 0), width=0.5)
            for x in (40, 580, 800, 1160):
                page.draw_line((x, 315), (x, 430), color=(0, 0, 0), width=0.5)
            return document.tobytes()

    def test_real_native_pdf_preserves_multicolumn_cells_without_ocr(self):
        with patch("apps.procurement.services.signed_pr_pdf_import.extract_text_from_pdf_tesseract") as ocr:
            fields = extract_signed_pr_fields(self._native_table_pdf(), "synthetic-table.pdf")
        ocr.assert_not_called()
        self.assertEqual(fields["extraction_method"], "native")
        self.assertEqual(fields["pr_number"], "RAD-PRJ-PR-0724_2027")
        self.assertEqual(fields["issued_by_name"], "Example Requester")
        self.assertEqual(str(fields["issued_date"]), "2027-03-18")
        self.assertEqual(fields["project_number"], "5907771")
        self.assertEqual(fields["supplier_name"], "Example Simulation Services Ltd")
        self.assertEqual(fields["net_total"], Decimal("146320.00"))
        self.assertEqual(fields["currency"], "USD")
        self.assertEqual(fields["price_remarks"], "Sales Budget USD 146,320.00")
        self.assertEqual(fields["price_lines"][0]["description"], "Synthetic Process Simulation Services for 5907771")
        self.assertEqual(len(fields["price_lines"]), 1)
        self.assertEqual(fields["po_reference"], "RAD-PRJ-PUR-0724_MAR2027")
        self.assertEqual(fields["special_notes"], "This is a synthetic training package. Test report is enclosed, & delivery checklist as attached,")
        self.assertEqual(fields["field_confidence"]["price"], "high")

    def test_real_native_pdf_retains_single_row_net_total_conflict(self):
        with patch("apps.procurement.services.signed_pr_pdf_import.extract_text_from_pdf_tesseract") as ocr:
            fields = extract_signed_pr_fields(self._native_table_pdf("USD 120,000.00"), "conflicting-table.pdf")
        ocr.assert_not_called()
        self.assertEqual(fields["net_total"], Decimal("120000.00"))
        self.assertEqual(fields["price_lines"][0]["total"], "146320.00")
        self.assertEqual(fields["field_confidence"]["price"], "conflict")
        self.assertTrue(any("differs from price-row total" in issue for issue in fields["extraction_issues"]))


class SignedPRManualReviewTests(SimpleTestCase):
    def test_preview_allows_missing_pr_number_for_manual_review(self):
        with patch(
            "apps.procurement.services.signed_pr_pdf_import.extract_text_from_pdf_tesseract",
            return_value="Purchase Requisition\nIssued by: Test User",
        ):
            fields = extract_signed_pr_fields(
                b"%PDF-test", "unreadable-scan.pdf", allow_missing_pr_number=True,
            )

        self.assertEqual(fields["pr_number"], "")
        self.assertTrue(fields["extraction_issues"])

    def test_manual_review_applies_validated_corrections(self):
        fields = {
            "pr_number": "",
            "issued_by_name": "",
            "issued_date": None,
            "product_service": "",
            "supplier_name": "",
            "description_reason": "",
            "net_total": None,
            "currency": "",
            "field_confidence": {"price": "missing"},
            "extraction_issues": ["OCR could not confidently extract the labeled field: price."],
        }
        corrected = _apply_manual_overrides(fields, {
            "pr_number": "RAD-PRJ-PR-0123 2026",
            "issued_by_name": "Test User",
            "issued_date": "2026-08-18",
            "product_service": "Engineering service",
            "supplier_name": "Approved Supplier",
            "description_reason": "Engineering service for project",
            "net_total": "1,250.50",
            "currency": "aed",
        })

        self.assertEqual(corrected["pr_number"], "RAD-PRJ-PR-0123_2026")
        self.assertEqual(corrected["net_total"], Decimal("1250.50"))
        self.assertEqual(corrected["currency"], "AED")
        self.assertEqual(corrected["field_confidence"]["price"], "manual")
        self.assertTrue(corrected["manual_review_applied"])

    def test_manual_review_rejects_invalid_pr_number(self):
        with self.assertRaisesMessage(SignedPRImportError, "valid PR number"):
            _apply_manual_overrides({}, {"pr_number": "PR-123"})
