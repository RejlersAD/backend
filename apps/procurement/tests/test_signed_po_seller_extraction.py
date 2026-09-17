"""Seller extraction must respect neighboring fields in two-column PO scans."""

from unittest.mock import patch

import pymupdf
from django.test import SimpleTestCase

from apps.procurement.services.signed_po_pdf_import import extract_signed_po_fields


class SignedPOSellerExtractionTests(SimpleTestCase):
    def extract(self, seller_text, pdf_bytes=b"%PDF-test"):
        text = "PURCHASE ORDER\nRAD-PRJ-PUR-0002_JAN2026\n" + seller_text + "\nTotal Purchase Price: 500.00 USD"
        with patch("apps.procurement.services.signed_po_pdf_import.extract_text_from_pdf_tesseract", return_value=text):
            return extract_signed_po_fields(pdf_bytes, "RAD-PRJ-PUR-0002_JAN2026.pdf")

    def test_split_seller_address_and_invoicing_fields_do_not_enter_company_name(self):
        source = (
            "Seller: Hexagon PPM Middle East Software Services. L.L.C\n"
            "Seller Unit 01, Lake View Tower,\nAddress: Dubai, UAE\n"
            "Invoicing Attn. Finance Team\nAddress: Rejlers office\n"
            "Seller Reference: Mr. Buyer"
        )
        fields = self.extract(source)
        self.assertEqual(fields["vendor_name"], "Hexagon PPM Middle East Software Services. L.L.C")
        self.assertIn("Lake View Tower", fields["ocr_vendor_name_raw"])
        self.assertEqual(fields["vendor_name_source"], "ocr")

    def test_normal_company_name_stops_at_seller_reference(self):
        fields = self.extract("Seller: Example Engineering LLC\nSeller Reference: Mr. Buyer")
        self.assertEqual(fields["vendor_name"], "Example Engineering LLC")

    def test_wrapped_company_name_is_preserved_before_address_label(self):
        fields = self.extract("Seller: Example Engineering\nInternational Pte Ltd\nSeller Address: Suite 21\nSeller Reference: Mr. Buyer")
        self.assertEqual(fields["vendor_name"], "Example Engineering International Pte Ltd")

    def test_invoicing_boundary_works_without_a_seller_reference(self):
        fields = self.extract("Seller: Example Supplies Ltd\nInvoicing Address: Accounts Payable\nBuyer Reference: A. Buyer")
        self.assertEqual(fields["vendor_name"], "Example Supplies Ltd")

    def test_embedded_seller_text_is_preferred_over_corrupted_ocr(self):
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((40, 60), "Seller: Example Native Engineering LLC")
            page.insert_text((40, 90), "Seller Address: Unit 01, Business Tower")
            fields = self.extract("Seller: Examp1e Natlve Eng1neering LLC\nSeller Reference: Mr. Buyer", document.tobytes())
        self.assertEqual(fields["vendor_name"], "Example Native Engineering LLC")
        self.assertEqual(fields["vendor_name_source"], "native")

    def test_supplier_registration_uses_only_labelled_seller_contact_and_license(self):
        fields = self.extract(
            'Seller: Source Supplier LLC\nLicense No.: CN-12345\n'
            'Seller Contact Person: Supplier Contact\nSeller Email: seller@example.test\n'
            'Seller Phone: +971 555 1234\nSeller Address: Supplier Building\n'
            'Seller Country: United Arab Emirates Buyer Address: Rejlers Office\n'
            'Invoicing Address: Rejlers Office\nLicense No.: CN-99999\n'
            'Email: buyer@example.test\nPhone: +971 999 9999\nBuyer Reference: Buyer Person'
        )
        self.assertEqual(fields['vendor_license_no'], 'CN-12345')
        self.assertEqual(fields['seller_contact_person'], 'Supplier Contact')
        self.assertEqual(fields['seller_email'], 'seller@example.test')
        self.assertEqual(fields['seller_phone'], '+971 555 1234')
        self.assertEqual(fields['seller_address'], 'Supplier Building')
        self.assertEqual(fields['seller_country'], 'United Arab Emirates')

    def test_buyer_contact_and_license_are_not_registered_as_supplier_details(self):
        fields = self.extract(
            'Seller: Source Supplier LLC\nInvoicing Address: Rejlers Office\n'
            'License No.: CN-99999\nEmail: buyer@example.test\n'
            'Phone: +971 999 9999\nBuyer Reference: Buyer Person'
        )
        for name in ('vendor_license_no', 'seller_contact_person', 'seller_email', 'seller_phone', 'seller_address'):
            self.assertEqual(fields[name], '', name)
        generic_buyer = self.extract('Seller: Source Supplier LLC\nBuyer: Rejlers\nLicense No.: CN-99999')
        self.assertEqual(generic_buyer['vendor_name'], 'Source Supplier LLC')
        self.assertEqual(generic_buyer['vendor_license_no'], '')

    def test_same_line_neighbouring_fields_and_generic_buyer_stop_supplier_details(self):
        fields = self.extract(
            'Seller: Source Supplier LLC\n'
            'Seller Contact: Jane Supplier Seller Email: seller@example.test\n'
            'Seller Address: Office 4 Buyer Reference: Alice Buyer\n'
            'Buyer: Rejlers\nLicense No.: CN-99999'
        )
        self.assertEqual(fields['seller_contact_person'], 'Jane Supplier')
        self.assertEqual(fields['seller_address'], 'Office 4')
        self.assertEqual(fields['vendor_license_no'], '')
