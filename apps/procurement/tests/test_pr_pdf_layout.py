"""Real OCR probes using wholly synthetic ruled PR forms and business values."""

import shutil
from unittest import TestCase, skipUnless
from unittest.mock import Mock

import pymupdf

from apps.procurement.services.pr_pdf_text import extract_pr_pdf_text


def dense_scanned_pr_pdf():
    """Rasterize a small-font grid so the test cannot use native PDF text."""
    with pymupdf.open() as source:
        page = source.new_page()
        x0, x1 = 40, 555
        rules = [60, 96, 125, 155, 190, 245, 270, 294, 314, 334, 356, 382, 470, 490, 510, 536, 562, 588, 614]
        for y in rules:
            page.draw_line((x0, y), (x1, y), width=0.65)
        for x in (x0, x1):
            page.draw_line((x, rules[0]), (x, rules[-1]), width=0.65)
        for x in (325, 415):
            page.draw_line((x, 270), (x, 356), width=0.65)
        for x in (84, 260, 410):
            page.draw_line((x, 490), (x, 614), width=0.65)

        def text(x, y, value, size=8):
            page.insert_text((x, y), value, fontsize=size)

        text(175, 82, "Purchase Requisition", 14)
        text(45, 114, "PR No. RAD-PRJ-PR-0724_2027")
        text(45, 144, "Issued by: Test Requester    Date: 18.03.2027")
        text(45, 174, "Product/ Service: Synthetic Process Simulation Services for 5907771")
        text(45, 205, "1. Description and Reason for Purchase:")
        text(45, 226, "Synthetic Process Simulation Services for 5907771")
        text(45, 261, "2. Preferred Supplier (if any): Example Simulation Services Ltd")
        text(45, 286, "3. Price")
        text(332, 286, "Total Price")
        text(422, 286, "Remarks")
        text(45, 307, "Synthetic Process Simulation Services for 5907771", 7)
        text(332, 307, "USD 146,320.00", 7)
        text(422, 307, "Sales Budget USD 146,320.00", 7)
        text(45, 349, "Net Total, excl VAT")
        text(332, 349, "USD 146,320.00", 7)
        text(45, 373, "PO Reference: RAD-PRJ-PUR-0724_MAR2027")
        text(45, 399, "4. Special Notes: (If any)")
        text(45, 424, "Synthetic laboratory package, Test report is enclosed, & delivery checklist is attached.")
        text(270, 484, "APPROVALS")
        text(130, 504, "Name")
        text(290, 504, "Signature")
        text(440, 504, "Remarks")
        for y, role, name in ((528, "PD", "Test Director"), (554, "MoP", "Test Manager"), (580, "VP, Op", "Test Operations")):
            text(45, y, role)
            text(90, y, name)
        text(45, 606, "Date")
        text(90, 606, "22.03.2027")
        image = page.get_pixmap(dpi=180).tobytes("png")
    with pymupdf.open() as scanned:
        page = scanned.new_page()
        page.insert_image(page.rect, stream=image)
        return scanned.tobytes()


@skipUnless(shutil.which("tesseract"), "Real scanned-PDF tests need Tesseract")
class PRPDFLayoutTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fallback = Mock(side_effect=AssertionError("Production scans must use layout OCR first"))
        cls.result = extract_pr_pdf_text(dense_scanned_pr_pdf(), fallback=cls.fallback)

    def test_dense_scan_recovers_prices_currency_reference_and_printed_notes(self):
        self.fallback.assert_not_called()
        self.assertEqual(self.result["method"], "ocr")
        self.assertGreaterEqual(self.result["text"].count("USD 146,320.00"), 2)
        self.assertIn("RAD-PRJ-PUR-0724_MAR2027", self.result["text"])
        self.assertIn("Test report is enclosed", self.result["text"])
        self.assertIn("&", self.result["text"])
        self.assertEqual(self.result["warnings"], [])

    def test_price_and_remarks_are_separate_source_cells_with_coordinates(self):
        layout = self.result["page_layout"][0]
        self.assertEqual(layout["coordinate_space"], "pixels")
        self.assertEqual(layout["dpi"], 400)
        self.assertGreater(len(layout["structure"]["horizontal_rules"]), 10)
        price_cells = [region for region in layout["regions"] if region["text"] == "USD 146,320.00"]
        self.assertEqual(len(price_cells), 2)
        self.assertTrue(any("Sales Budget USD 146,320.00" in region["text"] for region in layout["regions"]))
        for cell in price_cells:
            self.assertEqual(cell["source"], "ocr_cell")
            self.assertEqual(len(cell["bbox"]), 4)
            self.assertTrue(cell["tokens"])
            self.assertGreaterEqual(cell["confidence"], 0)
            self.assertLessEqual(cell["confidence"], 100)

    def test_actual_source_roles_remain_separate_from_names_and_signatures(self):
        rows = self.result["page_layout"][0]["table_rows"]
        director_row = next(row for row in rows if any(cell["text"] == "PD" for cell in row["cells"]))
        self.assertEqual(director_row["cells"][1]["text"], "Test Director")
        self.assertEqual(director_row["cells"][2]["text"], "")
        self.assertFalse(director_row["cells"][2]["contains_ink"])
