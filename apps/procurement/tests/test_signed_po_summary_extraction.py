from unittest.mock import patch

from django.test import SimpleTestCase

from apps.procurement.services.signed_po_pdf_import import (
    extract_signed_po_fields,
    normalize_po_summary,
)


class SignedPOSummaryExtractionTests(SimpleTestCase):
    def test_explicit_scope_title_preferred_over_interleaved_cover_page(self):
        text = """--- Page 1 ---
PURCHASE ORDER
RAD-PRJ-PUR-0083_JUN2026
Purchase Summary: Total Purchase Price: 413.27 USD
Supply of engineering licenses VAT (5%): 20.66 USD
Total Sum: 433.93 USD
Approved by: A manager
Order Confirmation: We acknowledge this order.
--- Page 2 ---
PURCHASE ORDER: Supply of engineering licenses (July 2026) to Example
Engineering office.

We, Example Engineering (Buyer) are pleased to send this purchase order.
SCOPE: Long scope details.
PRICES: The total purchase price is 413.27 USD.
"""
        with patch(
            "apps.procurement.services.signed_po_pdf_import.extract_text_from_pdf_tesseract",
            return_value=text,
        ):
            fields = extract_signed_po_fields(b"%PDF-test", "RAD-PRJ-PUR-0083_JUN2026.pdf")
        self.assertEqual(
            fields["summary"],
            "Supply of engineering licenses (July 2026) to Example Engineering office.",
        )

    def test_flattened_legacy_spillover_uses_later_title(self):
        text = (
            "Total Purchase Price: 120.00 AED Original cover title VAT (5%): 6.00 AED "
            "Total Sum: 126.00 AED Approved by: Manager Order Confirmation: Accepted "
            "--- Page 2 --- PURCHASE ORDER: Annual maintenance of pumps "
            "to Example site. We, Example Buyer are pleased to send this order. "
            "SCOPE: Maintenance and inspection PRICES: 120.00 AED"
        )
        self.assertEqual(normalize_po_summary(text), "Annual maintenance of pumps to Example site.")

    def test_standard_cover_summary_stops_at_price(self):
        self.assertEqual(
            normalize_po_summary("Purchase Summary: Equipment inspection\nTotal Purchase Price: 900.00 EUR"),
            "Equipment inspection",
        )

    def test_interleaved_cover_without_scope_title(self):
        self.assertEqual(
            normalize_po_summary(
                "Purchase Summary: Total Purchase Price: 900.00 EUR\n"
                "Supply of control valves VAT (5%): 45.00 EUR\nApproved by: Manager"
            ),
            "Supply of control valves",
        )

    def test_title_stops_before_each_following_field(self):
        for field in (
            "Approved by: Manager", "Order Confirmation: Accepted", "SCOPE: Work scope",
            "Total Purchase Price: 900.00 EUR", "VAT (5%): 45.00 EUR",
            "--- Page 3 --- unrelated text", "SUMMARY OF PRICES: Table",
        ):
            with self.subTest(field=field):
                self.assertEqual(normalize_po_summary("PURCHASE ORDER: Valve testing " + field), "Valve testing")

    def test_already_clean_title_preserved(self):
        self.assertEqual(normalize_po_summary("Inspection of fire protection systems"), "Inspection of fire protection systems")

    def test_missing_title_does_not_return_prices_or_document_header(self):
        for text in (
            "Purchase Summary: Total Purchase Price: 900.00 EUR\nVAT (5%): 45.00 EUR",
            "PURCHASE ORDER\nRAD-PRJ-PUR-0002_JAN2026\nSeller: Example Supplier",
            "Total Purchase Price: 900.00 EUR\nApproved by: Manager",
            "",
        ):
            with self.subTest(text=text):
                self.assertEqual(normalize_po_summary(text), "")

    def test_unbounded_document_text_is_not_truncated_into_a_title(self):
        self.assertEqual(normalize_po_summary("Purchase Summary: " + "Unseparated document body " * 30), "")
