"""Supplier contact ownership must survive adjacent subcontractor/buyer blocks."""

from django.test import SimpleTestCase

from apps.procurement.services.po_supplier_contacts import parse_vendor_contact_block


class SupplierContactBlockTests(SimpleTestCase):
    vendor = 'Noveltech Surveys Work Measurement & Space L.L.C.'
    contact = '''10. CONTACT PERSONS.
Subcontractor contact person:
M/s. Noveltech:
Mr. Surafel Jimma, Director; Email: surafel@noveltechsurveys.com
Address: Al Souq Tower C20, Abu Dhabi UAE
Mobile: +971554550243
Phone: +97126345562
M/s. PETROSKY GLOBAL ENGINEERING & CONSULTANTS LLC:
Jamal Muhaideen N, Manager - Business Development
Mobile : + 91 7010472310
E-mail: BusinessDevelopment@petroskyglobal.com
RAD contact persons:
Technical Focal point:
Mr. Buyer Person; E-mail: buyer@rejlers.ae
Tel: +971 2639 7449
'''

    def test_actual_named_vendor_block_keeps_only_its_contact_details(self):
        self.assertEqual(parse_vendor_contact_block(self.contact, self.vendor), {
            'seller_contact_person': 'Mr. Surafel Jimma',
            'seller_email': 'surafel@noveltechsurveys.com',
            'seller_address': 'Al Souq Tower C20, Abu Dhabi UAE',
            'seller_phone': '+97126345562',
            'seller_country': 'United Arab Emirates',
        })

    def test_other_subcontractor_can_be_selected_by_its_full_name(self):
        result = parse_vendor_contact_block(self.contact, 'PETROSKY GLOBAL ENGINEERING & CONSULTANTS LLC')
        self.assertEqual(result, {
            'seller_contact_person': 'Jamal Muhaideen N',
            'seller_phone': '+917010472310',
            'seller_email': 'BusinessDevelopment@petroskyglobal.com',
        })

    def test_mobile_is_used_only_when_the_vendor_has_no_readable_phone(self):
        result = parse_vendor_contact_block(self.contact.replace('Phone: +97126345562', 'Phone:'), self.vendor)
        self.assertEqual(result['seller_phone'], '+971554550243')

    def test_missing_vendor_phone_is_not_taken_from_the_next_company(self):
        text = self.contact.replace('Phone: +97126345562', '').replace('Mobile: +971554550243', '')
        self.assertNotIn('seller_phone', parse_vendor_contact_block(text, self.vendor))

    def test_unmatched_and_ambiguous_short_aliases_do_not_take_contacts(self):
        self.assertEqual(parse_vendor_contact_block(self.contact, 'Different Supplier LLC'), {})
        self.assertEqual(parse_vendor_contact_block(self.contact + '\nM/s. Noveltech Logistics LLC:\nPhone: +97199999999', self.vendor), {})
        self.assertEqual(parse_vendor_contact_block(self.contact + '\nM/s. Noveltech:\nPhone: +97199999999', self.vendor), {})

    def test_generic_company_word_is_not_a_short_name_identity(self):
        self.assertEqual(parse_vendor_contact_block('M/s. Global:\nEmail: wrong@example.test', 'Global Engineering LLC'), {})

    def test_full_name_can_be_used_without_messrs_prefix(self):
        text = 'Acme Engineering Ltd:\nContact person: Jane Vendor\nEmail: jane@example.test\nTelephone: +44 1234 567890\nCountry: United Kingdom'
        self.assertEqual(parse_vendor_contact_block(text, 'Acme Engineering Limited'), {
            'seller_contact_person': 'Jane Vendor', 'seller_email': 'jane@example.test',
            'seller_phone': '+441234567890', 'seller_country': 'United Kingdom',
        })

    def test_buyer_heading_and_next_page_stop_the_selected_block(self):
        for boundary in ('Buyer contact persons:', 'RAD contact persons:', '--- Page 7 ---', '11. APPENDICES'):
            with self.subTest(boundary=boundary):
                text = 'M/s. Noveltech:\nMr. Vendor Person\n' + boundary + '\nEmail: buyer@example.test\nPhone: +97199999999'
                self.assertEqual(parse_vendor_contact_block(text, self.vendor), {'seller_contact_person': 'Mr. Vendor Person'})

    def test_empty_label_lines_are_not_company_boundaries(self):
        text = 'M/s. Noveltech:\nContact person:\nJane Vendor\nEmail:\njane@example.test\nAddress:\nOffice 4\nAbu Dhabi, UAE\nPhone:\n+971 2 345 6789'
        result = parse_vendor_contact_block(text, self.vendor)
        self.assertEqual(result['seller_contact_person'], 'Jane Vendor')
        self.assertEqual(result['seller_address'], 'Office 4 Abu Dhabi, UAE')
        self.assertEqual(result['seller_phone'], '+97123456789')

    def test_conflicting_contact_values_are_left_for_review(self):
        text = 'M/s. Noveltech:\nEmail: first@example.test\nEmail: second@example.test\nPhone: +97123456789\nPhone: +97129876543'
        result = parse_vendor_contact_block(text, self.vendor)
        self.assertNotIn('seller_email', result)
        self.assertNotIn('seller_phone', result)

    def test_country_is_not_guessed_from_city_or_telephone_code(self):
        result = parse_vendor_contact_block('M/s. Noveltech:\nAddress: Abu Dhabi\nPhone: +97123456789', self.vendor)
        self.assertNotIn('seller_country', result)

    def test_a_name_in_prose_does_not_establish_a_supplier_contact_block(self):
        self.assertEqual(parse_vendor_contact_block('Noveltech will provide services.\nEmail: buyer@example.test', self.vendor), {})
