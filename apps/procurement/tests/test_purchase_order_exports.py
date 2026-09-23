import base64
from io import BytesIO
from datetime import datetime, timezone
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
    _approval_display,
    _html_blocks,
    build_purchase_order_docx,
    build_purchase_order_pdf,
)


class PurchaseOrderExportTests(TestCase):
    def test_pending_po_exports_status_without_inventing_a_completed_approver(self):
        order = self._order()
        order.status, order.approved_at = 'draft', None
        order.approved_by_name = 'Preselected Approver'
        order.approval_log = [{'user_id': 'po-reviewer', 'stage': 'Final Management Sign-off', 'status': 'Pending'}]
        content, _ = build_purchase_order_pdf(order)
        with fitz.open(stream=content, filetype='pdf') as pdf:
            text = '\n'.join(page.get_text() for page in pdf)
        self.assertIn('PO status: Draft', text)
        self.assertIn('Approval pending:', text)
        self.assertIn('Not yet approved', text)
        self.assertNotIn('Preselected Approver', text)
        self.assertNotIn('Approved by:', text)
        document = Document(BytesIO(build_purchase_order_docx(order)))
        text = '\n'.join(cell.text for table in document.tables for row in table.rows for cell in row.cells)
        self.assertIn('Approval pending:', text)
        self.assertNotIn('Preselected Approver', text)

    def test_unassigned_draft_pdf_and_word_do_not_claim_approval_was_requested(self):
        order = self._order()
        order.status, order.approved_at = 'draft', None
        order.approved_by_name = 'Preselected Approver'
        order.approval_log = []
        content, warnings = build_purchase_order_pdf(order)
        self.assertEqual(warnings, [])
        with fitz.open(stream=content, filetype='pdf') as pdf:
            self.assertEqual(len(pdf), 3)
            text = '\n'.join(page.get_text() for page in pdf)
            self.assertTrue(pdf[0].search_for('Order Confirmation:'))
            self.assertIn('Approval not requested:', text)
            self.assertIn('No approver assigned', text)
            self.assertNotIn('Approval pending:', text)
            self.assertNotIn('Preselected Approver', text)
            self.assertNotIn('Approved by:', text)
        document = Document(BytesIO(build_purchase_order_docx(order)))
        cell = next(cell for table in document.tables for row in table.rows for cell in row.cells
                    if 'Approval not requested:' in cell.text)
        self.assertEqual(cell.vertical_alignment, 0)
        self.assertIn('No approver assigned', cell.text)
        self.assertNotIn('Preselected Approver', cell.text)

    def test_linked_requisition_approval_and_source_rows_do_not_supply_po_approval(self):
        order = self._order()
        order.status, order.approved_at, order.approved_by_name = 'draft', None, ''
        order.pr_reference = SimpleNamespace(
            pr_number='PR-APPROVED', status='approved', approved_by_name='PR Signer',
            approved_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
        )
        order.approval_log = [{
            'source': 'signed_purchase_requisition_pdf', 'external': True,
            'user_id': 'pr-reviewer', 'approver': 'PR Signer', 'status': 'Approved',
            'evidence_document_id': 'retained-pr-source',
        }]
        content, _ = build_purchase_order_pdf(order)
        with fitz.open(stream=content, filetype='pdf') as pdf:
            text = '\n'.join(page.get_text() for page in pdf)
        document = Document(BytesIO(build_purchase_order_docx(order)))
        word_text = '\n'.join(cell.text for table in document.tables for row in table.rows for cell in row.cells)
        for rendered in (text, word_text):
            self.assertIn('Approval not requested:', rendered)
            self.assertIn('No approver assigned', rendered)
            self.assertNotIn('PR Signer', rendered)
            self.assertNotIn('Approved by:', rendered)
            self.assertNotIn('Approval pending:', rendered)

    def test_pending_status_needs_an_assigned_internal_stage_and_stopped_routes_are_not_pending(self):
        order = self._order()
        order.approved_at, order.approved_by_name = None, ''
        for status in ('draft', 'sent', 'in_progress'):
            with self.subTest(status=status):
                order.status = status
                order.approval_log = [{
                    'approver_email': 'signer@example.test', 'stage': 'Final Management Sign-off',
                    'status': 'in_review',
                }]
                display = _approval_display(order)
                self.assertEqual(display['heading'], 'Approval pending:')
                self.assertEqual(display['name'], 'Not yet approved')
        order.status = 'draft'
        for row in (
            {'status': 'pending', 'stage': 'Final Management Sign-off'},
            {'status': 'pending', 'user_id': 'pr-reviewer', 'source': 'signed_purchase_requisition_pdf'},
            {'status': 'pending', 'user_id': 'source-reviewer', 'evidence_document_id': 'source'},
        ):
            with self.subTest(row=row):
                order.approval_log = [row]
                self.assertEqual(_approval_display(order)['heading'], 'Approval not requested:')
        order.approval_log = [
            {'status': 'Rejected', 'user_id': 'first-reviewer'},
            {'status': 'Pending', 'user_id': 'next-reviewer'},
        ]
        self.assertEqual(_approval_display(order)['heading'], 'Approval record:')

    def test_pdf_and_docx_flag_mismatched_approval_instead_of_printing_signature(self):
        order = self._order()
        order.approved_by_name = 'Assigned Approver'
        order.approved_by_id = 'assigned'
        signature = BytesIO()
        PILImage.new('RGB', (181, 51), 'navy').save(signature, format='PNG')
        order.approval_signature = 'data:image/png;base64,' + base64.b64encode(signature.getvalue()).decode()
        order.approval_log = [{
            'user_id': 'assigned', 'approved_by_id': 'different-person',
            'status': 'Approved', 'signature': order.approval_signature,
        }]
        content, warnings = build_purchase_order_pdf(order)
        self.assertEqual(warnings, [])
        with fitz.open(stream=content, filetype='pdf') as pdf:
            self.assertIn('Approval review is required', ' '.join(page.get_text() for page in pdf))
            self.assertFalse(any(image[2:4] == (181, 51) for page in pdf for image in page.get_images()))
        document = Document(BytesIO(build_purchase_order_docx(order)))
        self.assertIn('Approval review is required', ' '.join(
            paragraph.text for table in document.tables for row in table.rows
            for cell in row.cells for paragraph in cell.paragraphs
        ))
        self.assertNotIn(signature.getvalue(), [part.blob for part in document.part.package.parts])

    def test_later_lifecycle_with_missing_approval_details_is_unknown_not_pending(self):
        for status in ('sent', 'completed', 'approved'):
            with self.subTest(status=status):
                order = self._order()
                order.status, order.approved_at, order.approved_by_name = status, None, ''
                content, _ = build_purchase_order_pdf(order)
                with fitz.open(stream=content, filetype='pdf') as pdf:
                    text = '\n'.join(page.get_text() for page in pdf)
                self.assertIn('Approval record:', text)
                self.assertIn('Not recorded', text)
                self.assertNotIn('Not yet approved', text)
                self.assertNotIn('Approval pending:', text)
                document = Document(BytesIO(build_purchase_order_docx(order)))
                text = '\n'.join(cell.text for table in document.tables for row in table.rows for cell in row.cells)
                self.assertIn('Approval record:', text)
                self.assertIn('Not recorded', text)

    def test_recorded_timestamp_supplies_date_when_separate_approval_date_is_missing(self):
        order = self._order()
        self.assertIsNone(order.approved_date)
        content, _ = build_purchase_order_pdf(order)
        with fitz.open(stream=content, filetype='pdf') as pdf:
            text = pdf[0].get_text()
        self.assertIn('Approved by:', text)
        self.assertIn('2026-09-03', text)
        document = Document(BytesIO(build_purchase_order_docx(order)))
        text = '\n'.join(cell.text for table in document.tables for row in table.rows for cell in row.cells)
        self.assertIn('Date: 2026-09-03', text)

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
            status='sent',
            approved_by_name='Jarmo Suominen',
            approved_by_title='Sr. Vice President, Middle East\nCEO, Rejlers Abu Dhabi',
            approved_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
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
        price_summary_text = exported.pages[2].extract_text()
        self.assertIn('SUMMARY OF PRICES', price_summary_text)
        self.assertRegex(price_summary_text, r'Total Price:\s+USD 100\.00')
        self.assertRegex(price_summary_text, r'VAT \(5%\):\s+USD 5\.00')
        self.assertRegex(price_summary_text, r'Total Sum:\s+USD 105\.00')
        self.assertNotIn('Grand Total', price_summary_text)
        self.assertNotIn('AED', price_summary_text)
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
        self.assertIn('Total Price: USD 100.00', rendered_text)
        self.assertIn('VAT (5%): USD 5.00', rendered_text)
        self.assertIn('Total Sum: USD 105.00', rendered_text)
        self.assertNotIn('Grand Total', rendered_text)
        self.assertNotIn('AED', rendered_text)
        self.assertIn('First scope\u00a0paragraph', rendered_text)
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

    def _realistic_long_contact_order(self):
        # Match the reported cover's density: two buyers, three invoice email
        # addresses and the same long supplier contact in both cover columns.
        order = self._order()
        order.vendor.name = 'SYNTHETIC SURVEYS WORK MEASUREMENT & SPACE L.L.C.'
        order.title = 'Provision of Piping Design engineer for 2 Months'
        order.seller_address = 'Al Example Tower, Office M04, Abu Dhabi, United Arab Emirates'
        order.seller_reference = (
            'Mr. Synthetic Contact\ninfo@syntheticsurveys.com\n'
            'supplier@syntheticsurveys.com'
        )
        order.quote_ref = 'CESPR092026016 R0 & Email Dated 14.09.2026'
        order.seller_license_no = 'CN-4349991'
        order.invoicing_attn = 'Attn. Mr. Synthetic Accounts Contact'
        order.invoicing_emails = [
            'accounts.payable.contact@example.ae',
            'cc. uae.finance@example.ae',
            'uae.procurement@example.ae',
        ]
        order.company_fax = 'INVOICEFAX9988'
        order.contact_persons = {'buyer_references': [
            {'name': 'Synthetic Primary Buyer', 'designation': 'Procurement Manager',
             'email': 'primarybuyer.thomas@example.ae'},
            {'name': 'Synthetic Second Buyer', 'designation': 'Procurement Engineer',
             'email': 'secondarybuyer.ravichandran@example.ae'},
        ]}
        order.payment_terms = '45 days net for agreed payment milestones'
        order.delivery_terms = 'Services completed and accepted'
        order.marking = 'RAD-PRJ-PUR-0126_SEP2026'
        return order

    def test_realistic_cover_keeps_every_field_legible_on_first_page(self):
        order = self._realistic_long_contact_order()
        content, warnings = build_purchase_order_pdf(order)
        self.assertEqual(warnings, [])
        with fitz.open(stream=content, filetype='pdf') as pdf:
            self.assertEqual(len(pdf), 3)
            self._assert_body_clear_of_footer(pdf)
            page = pdf[0]
            text = ''.join(page.get_text().split())
            for label in (
                'Seller:', 'Seller Address:', 'Invoicing Address:', 'Seller Reference:',
                'Buyer Reference:', 'Payment Terms:', 'Payment Mode:', 'Project:',
                'Delivery terms:', 'Delivery date:', 'Marking:', 'Purchase Summary:',
                'Total Purchase Price:', 'Total Sum:', 'Approved by:', 'Order Confirmation:',
                'Seller Signature:', 'Seller Name:', 'Seller Ref. no:', 'Contact Person:',
                'Phone Number:', 'Fax:', 'Email:',
            ):
                self.assertIn(''.join(label.split()), text)
            for value in (
                order.vendor.name, order.seller_address, order.seller_reference,
                order.quote_ref, order.seller_license_no, order.invoicing_attn,
                *order.invoicing_emails, order.payment_terms, order.delivery_terms,
                order.marking, order.title, order.seller_phone, order.seller_fax,
                order.seller_email,
            ):
                self.assertIn(''.join(value.split()), text)
            for reference in order.contact_persons['buyer_references']:
                for value in reference.values():
                    self.assertIn(''.join(value.split()), text)
            # Keep the identity/date roughly 40mm above the previous bottom
            # anchor, grouped with the heading and clear of the footer.
            heading = page.search_for('Approved by:')[0]
            approver = page.search_for('Jarmo Suominen')[0]
            date_line = page.search_for('2026-09-03')[0]
            self.assertAlmostEqual(approver.x0, heading.x0, delta=1)
            self.assertGreater(approver.y0, page.rect.height - 120 * mm)
            self.assertLess(approver.y0, page.rect.height - 95 * mm)
            self.assertGreater(approver.y0, heading.y1)
            self.assertLess(date_line.x1, page.rect.width / 2)
            self.assertGreater(date_line.y0, approver.y1)
            self.assertGreater(date_line.y1, page.rect.height - 88 * mm)
            self.assertLess(date_line.y1, page.rect.height - 82 * mm)
            self.assertLess(
                page.search_for('INVOICEFAX9988')[0].y1,
                min(rect.y0 for rect in page.search_for('Payment Terms:')),
            )
            # Evaluate the actual exported glyph sizes after any fit scaling.
            for block in page.get_text('dict')['blocks']:
                for line in block.get('lines', []):
                    for span in line['spans']:
                        if (span['bbox'][1] >= 34 * mm
                                and span['bbox'][3] <= page.rect.height - 40 * mm):
                            self.assertGreaterEqual(span['size'], 8,
                                f'Cover text became too small: {span["text"]}')
            self.assertIn('PO DESCRIPTION & SCOPE', pdf[1].get_text())
            self.assertNotIn('Order Confirmation:', pdf[1].get_text())
            self.assertIn('SUMMARY OF PRICES', pdf[2].get_text())

    def test_long_addresses_and_contacts_fit_on_first_page_without_footer_overlap(self):
        order = self._long_order()
        content, warnings = build_purchase_order_pdf(order)
        self.assertEqual(warnings, [])
        with fitz.open(stream=content, filetype='pdf') as pdf:
            self._assert_body_clear_of_footer(pdf)
            text = pdf[0].get_text()
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
            self.assertEqual(fax[0][0], 0)
            self.assertEqual(min(payment)[0], 0)
            self.assertIn('Approved by:', text)
            self.assertIn('Order Confirmation:', text)
            self.assertEqual(len(pdf), 3)

    def test_single_address_taller_than_page_preserves_every_line_on_first_page(self):
        order = self._order()
        address_lines = [f'ADDRESSLINE{index:03d} Synthetic site location' for index in range(125)]
        order.seller_address = '\n'.join(address_lines)
        content, warnings = build_purchase_order_pdf(order)
        self.assertEqual(warnings, [])
        with fitz.open(stream=content, filetype='pdf') as pdf:
            self._assert_body_clear_of_footer(pdf)
            text = pdf[0].get_text()
            for index in range(125):
                self.assertEqual(text.count(f'ADDRESSLINE{index:03d}'), 1)
            for label in ('Payment Terms:', 'Order Confirmation:', 'Phone Number:'):
                self.assertIn(''.join(label.split()), ''.join(text.split()))
            self.assertIn('105.00USD', ''.join(text.split()))
            self.assertEqual(len(pdf), 3)
            self.assertIn('PO DESCRIPTION & SCOPE', pdf[1].get_text())
            self.assertIn('SUMMARY OF PRICES', pdf[2].get_text())

    def test_signature_approver_and_confirmation_stay_together_on_first_page(self):
        order = self._realistic_long_contact_order()
        order.approved_by_name = 'Synthetic Authorised Approver'
        order.approved_date = '2026-09-12'
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
            self.assertEqual(page.number, 0)
            self.assertEqual(len(pdf), 3)
            heading = page.search_for('Approved by:')[0]
            approver = page.search_for(order.approved_by_name)[0]
            self.assertTrue(page.search_for('Order Confirmation:'))
            signature_images = [image for image in page.get_images() if image[2:4] == (180, 50)]
            self.assertEqual(len(signature_images), 1)
            signature_rect = page.get_image_rects(signature_images[0][0])[0]
            self.assertLess(heading.y1, signature_rect.y0)
            self.assertLess(signature_rect.y1, approver.y0)
            self.assertLess(approver.y0 - signature_rect.y1, 5 * mm)
            self.assertAlmostEqual(signature_rect.x0, approver.x0, delta=1)
            approval_date = page.search_for(order.approved_date)[0]
            self.assertGreater(approver.y0, page.rect.height - 120 * mm)
            self.assertLess(approver.y0, page.rect.height - 95 * mm)
            self.assertGreater(approval_date.y0, approver.y1)
            self.assertLess(approval_date.x1, page.rect.width / 2)
            self.assertGreater(approval_date.y1, page.rect.height - 88 * mm)
            # The seal now sits beside the signature/name. Remaining identity
            # lines flow beneath it, still well clear of the printed footer.
            self.assertLess(approval_date.y1, page.rect.height - 75 * mm)
            self.assertFalse(page.search_for('__________________________'))

    def test_pending_approval_identity_is_higher_and_word_cell_stays_top_aligned(self):
        order = self._realistic_long_contact_order()
        order.status, order.approved_at, order.approved_date = 'draft', None, None
        order.approval_log = [{'user_id': 'po-reviewer', 'stage': 'Final Management Sign-off', 'status': 'Pending'}]
        content, warnings = build_purchase_order_pdf(order)
        self.assertFalse(warnings)
        with fitz.open(stream=content, filetype='pdf') as pdf:
            self.assertEqual(len(pdf), 3)
            self._assert_body_clear_of_footer(pdf)
            page = pdf[0]
            heading = page.search_for('Approval pending:')[0]
            identity = page.search_for('Not yet approved')[0]
            self.assertGreater(identity.y0, heading.y1)
            self.assertAlmostEqual(identity.x0, heading.x0, delta=1)
            self.assertGreater(identity.y0, page.rect.height - 115 * mm)
            self.assertLess(identity.y0, page.rect.height - 95 * mm)
            self.assertNotIn(order.approved_by_name, page.get_text())
            self.assertTrue(page.search_for('Order Confirmation:'))
        word = Document(BytesIO(build_purchase_order_docx(order)))
        cell = next(cell for table in word.tables for row in table.rows for cell in row.cells if 'Approval pending:' in cell.text)
        self.assertEqual(cell.vertical_alignment, 0)  # Word TOP alignment.
        self.assertIn('Not yet approved', cell.text)
