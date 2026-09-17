"""Supplier cover fields stay separate from adjacent buyer details."""

from django.test import SimpleTestCase

from apps.procurement.services.po_supplier_extraction import (
    complete_wrapped_seller_name, extract_seller_cover_details, needs_seller_layout,
)


class SupplierCoverExtractionTests(SimpleTestCase):
    cover = (
        'Seller: Example Surveys Work Measurement & Seller Reference: info@example.test\n'
        'Space L.L.C. Mr. Alice Supplier\n'
        'alice@example.test\n'
        'Seller Al Souq Tower C20, Abu Dhabi UAE Quote Ref.: Quote & discussion\n'
        'Address:\n'
        'License No. CN-12345\n'
        'Invoicing Attn. Mr. Buyer Person Buyer\n'
        'Address: buyer@buyer.test Reference: Buyer Person\n'
        'Phone: +971 2000 0000\nLicense No. CN-99999\n'
    )

    def test_interleaved_reference_and_address_are_captured_as_supplier_fields(self):
        fields = extract_seller_cover_details(self.cover)
        self.assertEqual(fields, {
            'vendor_license_no': 'CN-12345', 'seller_contact_person': 'Mr. Alice Supplier',
            'seller_email': 'info@example.test', 'seller_phone': '',
            'seller_address': 'Al Souq Tower C20, Abu Dhabi UAE', 'seller_country': 'United Arab Emirates',
        })
        self.assertFalse(needs_seller_layout(self.cover, fields))

    def test_missing_values_under_detected_labels_request_layout_recovery(self):
        text = 'Seller: Example LLC\nSeller Reference:\nLicense No.\nInvoicing Address: Buyer'
        fields = extract_seller_cover_details(text)
        self.assertEqual(fields['vendor_license_no'], '')
        self.assertEqual(fields['seller_email'], '')
        self.assertTrue(needs_seller_layout(text, fields))

    def test_buyer_country_and_phone_are_not_used_for_supplier(self):
        fields = extract_seller_cover_details(
            'Seller: Example LLC\nSeller Address: Main Tower\nBuyer Reference: Buyer Person\n'
            'Country: United Arab Emirates\nPhone: +971 2000 0000\nEmail: buyer@buyer.test'
        )
        self.assertEqual(fields['seller_address'], 'Main Tower')
        for key in ('seller_phone', 'seller_email', 'seller_country'):
            self.assertEqual(fields[key], '', key)

    def test_wrapped_legal_name_continues_before_reference_contact(self):
        self.assertEqual(complete_wrapped_seller_name(self.cover, 'Example Surveys Work Measurement &'),
                         'Example Surveys Work Measurement & Space L.L.C.')
        self.assertEqual(complete_wrapped_seller_name(self.cover, 'Example LLC'), 'Example LLC')

    def test_explicit_seller_labels_preserve_international_address_and_phone(self):
        fields = extract_seller_cover_details(
            'Seller: Example Ltd\nSeller Contact: Jane Seller\nSeller Email: jane@example.test\n'
            'Seller Telephone: +44 (0) 1234 567890\nSeller Address: Suite 2\nBusiness Tower\n'
            'Seller Country: United Kingdom\nInvoicing Address: Buyer Office'
        )
        self.assertEqual(fields['seller_contact_person'], 'Jane Seller')
        self.assertEqual(fields['seller_phone'], '+44 (0) 1234 567890')
        self.assertEqual(fields['seller_address'], 'Suite 2 Business Tower')
        self.assertEqual(fields['seller_country'], 'United Kingdom')

    def test_attachment_seller_fields_do_not_replace_cover_supplier(self):
        fields = extract_seller_cover_details(
            '--- Page 1 ---\nSeller: Example LLC\nSeller Address: Main Tower\n'
            '--- Page 2 ---\nSeller: Different Company\nSeller Phone: +971500000001'
        )
        self.assertEqual(fields['seller_address'], 'Main Tower')
        self.assertEqual(fields['seller_phone'], '')
