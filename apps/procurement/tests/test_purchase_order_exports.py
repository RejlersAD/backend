from io import BytesIO
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from docx import Document
from PyPDF2 import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from apps.procurement.services.purchase_order_exports import (
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
            description='<p>Test scope</p>',
            seller_reference='',
            quote_ref='',
            project_number='590001',
            rad_project_no='',
            payment_terms='30 days',
            delivery_terms='DAP',
            approved_by_name='',
            approved_by_title='',
            approved_date=None,
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
        # Two PO pages + three attachment covers + three one-page source PDFs.
        self.assertEqual(len(exported.pages), 8)
        first_cover_text = exported.pages[2].extract_text()
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

        self.assertIn('Summary of Prices', rendered_text)
        self.assertNotIn('Should not be exported to Word', rendered_text)
