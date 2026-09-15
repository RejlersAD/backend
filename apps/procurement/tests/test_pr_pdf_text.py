"""Exercise real PDF layout and per-page OCR routing without database writes."""

from unittest import TestCase, skipUnless
from unittest.mock import Mock, patch
import shutil

import pymupdf

from apps.procurement.services.pr_pdf_text import extract_pr_pdf_text


def _digital_pdf():
    with pymupdf.open() as document:
        page = document.new_page()
        # Deliberately write each column separately, as many PDF producers do.
        page.insert_text((40, 50), "Purchase Requisition", fontsize=15)
        page.insert_text((40, 85), "PR No. RAD-PRJ-PR-0002_2026", fontsize=10)
        page.insert_text((40, 130), "3. Price", fontsize=10)
        page.insert_text((40, 155), "Telecom consulting services", fontsize=10)
        page.insert_text((40, 180), "Net Total, excl VAT", fontsize=10)
        page.insert_text((300, 130), "Total Price", fontsize=10)
        page.insert_text((300, 155), "USD 225,608.00", fontsize=10)
        page.insert_text((300, 180), "USD 225,608.00", fontsize=10)
        page.insert_text((430, 130), "Remarks", fontsize=10)
        page.insert_text((430, 155), "Sales Budget", fontsize=10)
        return document.tobytes()


def _scan_pdf(*, with_header=False):
    # This is a genuine image-only page, not a mock of the PDF word reader.
    with pymupdf.open() as source:
        page = source.new_page(width=595, height=842)
        page.insert_text((50, 110), "Purchase Requisition", fontsize=20)
        page.insert_text((50, 155), "PR No. RAD-PRJ-PR-0002_2026", fontsize=16)
        page.insert_text((50, 210), "Total Price USD 225,608.00", fontsize=16)
        page.insert_text((50, 260), "Special Notes: approval letter is awaited.", fontsize=14)
        image = page.get_pixmap(dpi=150).tobytes("png")
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_image(page.rect, stream=image)
        if with_header:
            page.insert_text((50, 45), "REJLERS", fontsize=14)
        return document.tobytes()


class PRPDFTextTests(TestCase):
    def test_digital_pdf_preserves_price_rows_and_never_calls_ocr(self):
        fallback = Mock(side_effect=AssertionError("Digital text should not be rasterized"))
        result = extract_pr_pdf_text(_digital_pdf(), fallback=fallback)
        fallback.assert_not_called()
        self.assertEqual(result["method"], "native")
        self.assertEqual(result["warnings"], [])
        self.assertIn("Telecom consulting services | USD 225,608.00 | Sales Budget", result["text"])
        self.assertIn("Net Total, excl VAT | USD 225,608.00", result["text"])

    def test_mixed_document_layout_ocr_receives_only_the_scanned_page(self):
        with pymupdf.open(stream=_digital_pdf(), filetype="pdf") as document:
            with pymupdf.open(stream=_scan_pdf(), filetype="pdf") as scan:
                document.insert_pdf(scan)
            pdf_bytes = document.tobytes()

        def read_scan(page, *, page_number):
            self.assertEqual(page_number, 2)
            self.assertEqual(page.get_text(), "")
            self.assertTrue(page.get_images())
            return {"text": "Special Notes: approval letter awaited.", "layout": {"page": 2}, "warnings": []}

        fallback = Mock(side_effect=AssertionError("Legacy OCR must not bypass layout OCR"))
        with patch("apps.procurement.services.pr_pdf_text.extract_scanned_page_layout", side_effect=read_scan) as layout_ocr:
            result = extract_pr_pdf_text(pdf_bytes, fallback=fallback)
        self.assertEqual(result["method"], "mixed")
        self.assertEqual([page["method"] for page in result["pages"]], ["native", "ocr"])
        layout_ocr.assert_called_once()
        fallback.assert_not_called()
        self.assertIn("--- Page 2 ---\nSpecial Notes: approval letter awaited.", result["text"])
        self.assertEqual(result["text"].count("--- Page 1 ---"), 1)

    def test_native_logo_does_not_hide_a_scanned_body(self):
        fallback = Mock(side_effect=AssertionError("Legacy OCR must not bypass layout OCR"))
        with patch("apps.procurement.services.pr_pdf_text.extract_scanned_page_layout", return_value={
            "text": "Purchase Requisition\nTotal Price USD 225,608.00", "layout": {"page": 1}, "warnings": [],
        }) as layout_ocr:
            result = extract_pr_pdf_text(_scan_pdf(with_header=True), fallback=fallback)
        layout_ocr.assert_called_once()
        fallback.assert_not_called()
        self.assertEqual(result["method"], "ocr")
        self.assertIn("225,608.00", result["text"])

    def test_currency_in_separate_cell_remains_paired_with_its_amount(self):
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((40, 50), "Net Total, excl VAT", fontsize=10)
            page.insert_text((300, 50), "USD", fontsize=10)
            page.insert_text((360, 50), "225,608.00", fontsize=10)
            result = extract_pr_pdf_text(document.tobytes())
        self.assertIn("Net Total, excl VAT | USD 225,608.00", result["text"])

    def test_native_approval_table_exposes_real_source_cells_without_ocr(self):
        with pymupdf.open() as document:
            page = document.new_page()
            for y in (50, 75, 100, 125, 150, 175):
                page.draw_line((40, y), (540, y))
            for x in (40, 90, 270, 400, 540):
                page.draw_line((x, 50), (x, 175))
            page.insert_text((200, 67), "APPROVALS")
            page.insert_text((100, 93), "Name")
            page.insert_text((280, 93), "Signature")
            page.insert_text((410, 93), "Remarks")
            for y, role, name in ((118, "PD", "Test Director"), (143, "MoP", "Test Manager"), (168, "VP, Op", "Test Operations")):
                page.insert_text((45, y), role, fontsize=9)
                page.insert_text((100, y), name, fontsize=9)
            with patch("apps.procurement.services.pr_pdf_text.extract_scanned_page_layout") as ocr:
                result = extract_pr_pdf_text(document.tobytes())
        ocr.assert_not_called()
        layout = result["page_layout"][0]
        self.assertEqual(layout["coordinate_space"], "points")
        row = next(row for row in layout["table_rows"] if row["cells"][0]["text"] == "PD")
        self.assertEqual(row["cells"][1]["text"], "Test Director")
        self.assertEqual(row["cells"][2]["text"], "")
        self.assertIsNone(row["cells"][2]["contains_ink"])

    def test_scanned_page_failure_is_explicit_and_preserves_native_header(self):
        with patch("apps.procurement.services.pr_pdf_text.extract_scanned_page_layout", side_effect=RuntimeError("OCR unavailable")):
            result = extract_pr_pdf_text(
                _scan_pdf(with_header=True), fallback=Mock(side_effect=RuntimeError("OCR unavailable")),
            )
        self.assertEqual(result["method"], "native_partial")
        self.assertIn("REJLERS", result["text"])
        self.assertIn("Page 1: the scanned content could not be read", result["warnings"][0])

    def test_empty_ocr_is_not_reported_as_successful(self):
        with patch("apps.procurement.services.pr_pdf_text.extract_scanned_page_layout", return_value={
            "text": "", "layout": {"page": 1}, "warnings": [],
        }):
            result = extract_pr_pdf_text(_scan_pdf(), fallback=Mock(return_value="--- Page 1 ---\n"))
        self.assertEqual(result["method"], "unreadable")
        self.assertEqual(result["pages"][0]["characters"], 0)
        self.assertTrue(result["warnings"])

    def test_unreadable_page_in_mixed_document_is_not_silently_dropped(self):
        with pymupdf.open(stream=_digital_pdf(), filetype="pdf") as document:
            with pymupdf.open(stream=_scan_pdf(), filetype="pdf") as scan:
                document.insert_pdf(scan)
            with patch("apps.procurement.services.pr_pdf_text.extract_scanned_page_layout", side_effect=RuntimeError("OCR unavailable")):
                result = extract_pr_pdf_text(document.tobytes(), fallback=Mock(return_value=""))
        self.assertEqual(len(result["pages"]), 2)
        self.assertEqual(result["pages"][1]["method"], "unreadable")
        self.assertIn("Page 2:", result["warnings"][0])
        self.assertIn("--- Page 2 ---", result["text"])

    def test_blank_page_does_not_trigger_ocr(self):
        with pymupdf.open() as document:
            document.new_page()
            fallback = Mock(side_effect=AssertionError("Blank page"))
            result = extract_pr_pdf_text(document.tobytes(), fallback=fallback)
        self.assertEqual(result["method"], "blank")
        self.assertEqual(result["warnings"], [])
        fallback.assert_not_called()

    def test_nonparseable_pdf_uses_injected_legacy_boundary(self):
        fallback = Mock(return_value="PR No. RAD-PRJ-PR-0002_2026")
        result = extract_pr_pdf_text(b"%PDF-test", fallback=fallback)
        fallback.assert_called_once_with(b"%PDF-test")
        self.assertEqual(result["method"], "fallback")
        self.assertIn("RAD-PRJ-PR-0002_2026", result["text"])

    @skipUnless(shutil.which("tesseract"), "Tesseract must be installed for the genuine scanned-PDF probe")
    def test_real_scan_ocr_retains_amount_currency_and_notes(self):
        result = extract_pr_pdf_text(_scan_pdf())
        self.assertEqual(result["method"], "ocr")
        self.assertIn("USD 225,608.00", result["text"])
        self.assertIn("approval letter is awaited", result["text"])
        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["page_layout"][0]["dpi"], 400)
        self.assertTrue(result["page_layout"][0]["tokens"])

    def test_legacy_fallback_is_used_only_after_layout_failure_and_reports_warning(self):
        fallback = Mock(return_value="Purchase Requisition Total Price USD 225,608.00")
        with patch("apps.procurement.services.pr_pdf_text.extract_scanned_page_layout", side_effect=RuntimeError("OCR failed")):
            result = extract_pr_pdf_text(_scan_pdf(), fallback=fallback)
        fallback.assert_called_once()
        self.assertEqual(result["method"], "ocr_fallback")
        self.assertIn("table-aware OCR failed", result["warnings"][0])
