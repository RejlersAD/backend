import base64
from io import BytesIO
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import fitz
from docx import Document
from PIL import Image as PILImage
from PyPDF2 import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from apps.procurement.services.purchase_order_exports import (
    _html_blocks,
    build_purchase_order_docx,
    build_purchase_order_pdf,
)


class PurchaseOrderExportTests(TestCase):
    def _order(self, attachments=None):
        return SimpleNamespace(
            vendor=SimpleNamespace(name='Test Vendor'),
            po_number='RAD-PRJ-PUR-0001_2026',
            po_date='2026-09-03',
            items=[{'description': 'Test item', 'quantity': 2, 'unit_price': 50, 'uom': 'EA'}],
            total_amount=105,
            tax_amount=5,
            discount_amount=0,
            vat_percentage=5,
            currency='USD',
            title='Test Purchase Order',
            description='<p>First scope&nbsp;paragraph</p><p>Second scope paragraph</p><ul><li>Required document</li></ul>',
            seller_reference='Vendor Contact',
            quote_ref='',
            seller_license_no='',
            seller_address='Vendor City',
            seller_contact_person='',
            seller_phone='+971 1 234 5678',
            seller_fax='+971 1 234 5679',
            seller_email='vendor@example.com',
            invoicing_attn='Accounts Payable',
            invoicing_emails=['aneef.thadikkarantavida@rejlers.ae'],
            company_fax='+971 2 639 7448',
            buyer_reference_pm='Test Buyer',
            buyer_reference_email='richahannah.thomas@rejlers.ae',
            contact_persons={},
            project_number='590001',
            rad_project_no='',
            payment_terms='30 days',
            payment_mode='Bank Transfer',
            delivery_terms='DAP',
            expected_delivery='2026-09-30',
            marking='RAD-PRJ-PUR-0001_2026',
            form_note='(PO no. to be used in all documents)',
            approved_by_name='',
            approved_by_title='',
            approved_date=None,
            confirmation_date=None,
            attachments=attachments or [],
        )

    def _one_page_pdf(self):
        output = BytesIO()
        pdf = canvas.Canvas(output, pagesize=A4)
        pdf.drawString(50, 780, 'Supporting document')
        pdf.save()
        return output.getvalue()

    def test_pdf_adds_one_cover_and_source_per_attachment_in_order(self):
        attachments = [
            {
                'title': f'Attachment {index}',
                'description': f'Description {index}',
                'filename': f'attachment-{index}.pdf',
                's3_key': str(index),
                'content_type': 'application/pdf',
            }
            for index in range(1, 4)
        ]
        with patch(
            'apps.procurement.services.purchase_order_exports._download_attachment',
            return_value=self._one_page_pdf(),
        ):
            content, warnings = build_purchase_order_pdf(self._order(attachments))

        exported = PdfReader(BytesIO(content))
        self.assertEqual(warnings, [])
        # Cover/details, narrative, and price summary, then one cover and one
        # source page for each of the three attachments.
        self.assertEqual(len(exported.pages), 9)
        narrative_text = exported.pages[1].extract_text()
        self.assertIn('PURCHASE ORDER', narrative_text)
        # Header and footer use the official Rejlers image asset rather than a
        # substitute text/vector wordmark.
        xobjects = exported.pages[1]['/Resources'].get('/XObject', {})
        self.assertGreaterEqual(len(xobjects), 2)
        self.assertIn('Rejlers International Engineering Solutions', narrative_text)
        self.assertIn('Page 2', narrative_text)
        self.assertIn('First scope paragraph', narrative_text)
        self.assertIn('Second scope paragraph', narrative_text)
        self.assertNotIn('&nbsp;', narrative_text)
        first_page_text = exported.pages[0].extract_text()
        self.assertIn('Seller Address:', first_page_text)
        self.assertIn('Seller Name:', first_page_text)
        self.assertIn('Seller Ref. no:', first_page_text)
        self.assertIn('Contact Person:', first_page_text)
        self.assertRegex(
            first_page_text,
            r'Buyer\s+Reference:\s+Test Buyer\s+Procurement Manager\s+richahannah\.thomas@rejlers\.ae',
        )
        self.assertIn('aneef.thadikkarantavida@rejlers.ae', first_page_text)
        self.assertIn('Phone Number:', first_page_text)
        self.assertIn('Fax:', first_page_text)
        self.assertIn('Email:', first_page_text)
        self.assertNotIn('Phone / Email:', first_page_text)
        self.assertRegex(first_page_text, r'Seller Ref\. no:\s+—')
        self.assertRegex(first_page_text, r'Contact Person:\s+Vendor Contact')
        self.assertIn('SUMMARY OF PRICES', exported.pages[2].extract_text())
        first_cover_text = exported.pages[3].extract_text()
        self.assertIn('PURCHASE ORDER', first_cover_text)
        self.assertIn('Page 4', first_cover_text)
        self.assertIn('Description 1', first_cover_text)
        self.assertNotIn('attachment-1.pdf', first_cover_text)

    def test_word_stops_at_price_summary_and_excludes_attachments(self):
        content = build_purchase_order_docx(self._order([{
            'title': 'Attachment 1',
            'description': 'Should not be exported to Word',
            'filename': 'support.pdf',
        }]))
        document = Document(BytesIO(content))
        rendered_text = '\n'.join(paragraph.text for paragraph in document.paragraphs)
        header_text = '\n'.join(
            cell.text
            for table in document.sections[0].header.tables
            for row in table.rows
            for cell in row.cells
        )
        footer_text = '\n'.join(
            cell.text
            for table in document.sections[0].footer.tables
            for row in table.rows
            for cell in row.cells
        )

        self.assertIn('Summary of Prices', rendered_text)
        self.assertIn('First scope paragraph', rendered_text)
        self.assertIn('Second scope paragraph', rendered_text)
        self.assertNotIn('&nbsp;', rendered_text)
        self.assertNotIn('Should not be exported to Word', rendered_text)
        self.assertIn('PURCHASE ORDER', header_text)
        self.assertIn('RAD-PRJ-PUR-0001_2026', header_text)
        self.assertIn('HOME OF THE', header_text)
        self.assertIn('Rejlers International Engineering Solutions', footer_text)
        self.assertIn('Page ', footer_text)

    def test_long_purchase_summary_stays_on_first_page_without_displacing_approval(self):
        order = self._order()
        order.title = (
            'Supply of Smart Interop Publisher License — May 2026 (PO Form 29) '
            '(1 No) for RFIN XLPE Project = USD 1,755.40 1 No: USD 1,755.40 '
            'equally shared in RAB Projects 5900863 (H2 Extraction) and 5901055 (ADOC)'
        )

        content, warnings = build_purchase_order_pdf(order)
        exported = PdfReader(BytesIO(content))
        first_page_text = exported.pages[0].extract_text()

        self.assertEqual(warnings, [])
        self.assertEqual(len(exported.pages), 3)
        self.assertIn('5901055', first_page_text)
        self.assertIn('(ADOC)', first_page_text)
        self.assertIn('Approved by:', first_page_text)
        self.assertIn('Order Confirmation:', first_page_text)

    def test_rich_text_normalization_decodes_entities_and_keeps_blocks(self):
        self.assertEqual(
            _html_blocks('&lt;p&gt;Alpha&amp;nbsp;Beta&lt;/p&gt;<div>Gamma<br>Delta</div>'),
            ['Alpha Beta', 'Gamma', 'Delta'],
        )

    def _assert_body_clear_of_footer(self, pdf):
        # Branded page furniture is blue/white. Business text is slate/black;
        # inspect actual glyph rectangles rather than page counts alone.
        furniture_colors = {0x3275B6, 0x0870AA, 0xFFFFFF}
        for page in pdf:
            for block in page.get_text('dict')['blocks']:
                for line in block.get('lines', []):
                    for span in line['spans']:
                        if span['color'] in furniture_colors or span['bbox'][1] < 34 * mm:
                            continue
                        self.assertLessEqual(
                            span['bbox'][3], page.rect.height - 40 * mm,
                            f'Page {page.number + 1} enters the footer: {span["text"]}',
                        )

    def _long_order(self):
        order = self._order()
        order.vendor.name = 'Synthetic International Instrumentation and Technical Services Limited'
        order.seller_address = '\n'.join(f'Seller address level {index:02d}' for index in range(8))
        order.invoicing_attn = 'Invoice contact: Synthetic Accounts and Commercial Administration'
        order.invoicing_emails = [
            f'accounting.department.region{index}.contract.invoices@example.invalid'
            for index in range(4)
        ]
        order.company_fax = 'INVOICEFAX9988'
        order.contact_persons = {'buyer_references': [
            {'name': f'Synthetic Buyer {index}', 'designation': 'Senior Commercial Administrator',
             'email': f'buyer{index}.regional.contracts@example.invalid'}
            for index in range(4)
        ]}
        order.payment_terms = 'Payment follows receipt of a fully checked invoice and accepted deliverables.'
        order.seller_contact_person = 'Synthetic Supplier Contact for Regional Commercial Administration'
        order.seller_email = 'supplier.commercial.department.contract.administration@example.invalid'
        order.approved_by_name = 'Synthetic Authorised Approver'
        order.approved_by_title = 'Director of Commercial Operations\nRegional Engineering Division'
        order.approved_date = '2026-09-12'
        order.confirmation_date = '2026-09-13'
        return order

    def test_long_addresses_and_contacts_flow_before_following_fields_and_footer(self):
        order = self._long_order()
        content, warnings = build_purchase_order_pdf(order)
        self.assertEqual(warnings, [])
        with fitz.open(stream=content, filetype='pdf') as pdf:
            self._assert_body_clear_of_footer(pdf)
            text = ''.join(page.get_text() for page in pdf)
            compact_text = ''.join(text.split())
            for value in [
                order.vendor.name, order.seller_address, *order.invoicing_emails,
                order.seller_contact_person, order.seller_email,
                order.approved_by_name, order.approved_by_title,
                order.payment_terms, order.approved_date,
            ]:
                self.assertIn(''.join(value.split()), compact_text)
            for reference in order.contact_persons['buyer_references']:
                self.assertIn(''.join(reference['email'].split()), compact_text)
            fax = [(page.number, rect.y1) for page in pdf for rect in page.search_for('INVOICEFAX9988')]
            payment = [(page.number, rect.y0) for page in pdf for rect in page.search_for('Payment Terms:')]
            self.assertEqual(len(fax), 1)
            self.assertEqual(len({page_number for page_number, _ in payment}), 1)
            self.assertLess(fax[0], min(payment))
            self.assertGreater(len(pdf), 3)

    def test_single_address_taller_than_page_preserves_every_line(self):
        order = self._order()
        address_lines = [f'ADDRESSLINE{index:03d} Synthetic site location' for index in range(125)]
        order.seller_address = '\n'.join(address_lines)
        content, warnings = build_purchase_order_pdf(order)
        self.assertEqual(warnings, [])
        with fitz.open(stream=content, filetype='pdf') as pdf:
            self._assert_body_clear_of_footer(pdf)
            text = '\n'.join(page.get_text() for page in pdf)
            for index in range(125):
                self.assertEqual(text.count(f'ADDRESSLINE{index:03d}'), 1)
            for label in ('Payment Terms:', 'Order Confirmation:', 'Phone Number:', 'SUMMARY OF PRICES'):
                self.assertIn(''.join(label.split()), ''.join(text.split()))
            self.assertIn('USD 105.00', text)
            self.assertGreater(len(pdf), 4)

    def test_signature_approver_and_confirmation_stay_together_after_long_details(self):
        order = self._long_order()
        signature = BytesIO()
        PILImage.new('RGB', (180, 50), 'navy').save(signature, format='PNG')
        order.approval_signature = 'data:image/png;base64,' + base64.b64encode(signature.getvalue()).decode()
        content, warnings = build_purchase_order_pdf(order)
        self.assertEqual(warnings, [])
        with fitz.open(stream=content, filetype='pdf') as pdf:
            self._assert_body_clear_of_footer(pdf)
            approval_pages = [page for page in pdf if page.search_for('Approved by:')]
            self.assertEqual(len(approval_pages), 1)
            page = approval_pages[0]
            heading = page.search_for('Approved by:')[0]
            approver = page.search_for(order.approved_by_name)[0]
            self.assertTrue(page.search_for('Order Confirmation:'))
            signature_images = [image for image in page.get_images() if image[2:4] == (180, 50)]
            self.assertEqual(len(signature_images), 1)
            signature_rect = page.get_image_rects(signature_images[0][0])[0]
            self.assertLess(heading.y1, signature_rect.y0)
            self.assertLess(signature_rect.y1, approver.y0)
            self.assertAlmostEqual(signature_rect.x0, approver.x0, delta=1)
