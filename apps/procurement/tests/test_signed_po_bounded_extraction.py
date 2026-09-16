"""Long signed PO attachments must not become an unbounded OCR workload."""

from unittest.mock import Mock, patch

import pymupdf
from django.test import SimpleTestCase

from apps.procurement.services.po_tesseract_extractor import extract_text_from_pdf_tesseract
from apps.procurement.services.signed_po_pdf_import import (
    SignedPOImportError,
    extract_signed_po_fields,
)


SIGNED_SERVICE = 'apps.procurement.services.signed_po_pdf_import'


def document_bytes(pages=27, *, blank=False):
    with pymupdf.open() as document:
        for number in range(1, pages + 1):
            page = document.new_page()
            if not blank:
                page.insert_text((50, 50), f'Contract page {number}')
        return document.tobytes()


class SignedPOBoundedExtractionTests(SimpleTestCase):
    def test_long_scan_renders_only_first_four_pages_and_releases_each_before_next(self):
        source = document_bytes()
        images = []

        def render(pdf_bytes, **options):
            self.assertEqual(pdf_bytes, source)
            self.assertEqual(options['first_page'], options['last_page'])
            self.assertEqual(options['fmt'], 'ppm')
            self.assertEqual(options['dpi'], 200)
            self.assertLessEqual(options['last_page'], 4)
            self.assertGreater(options['timeout'], 0)
            if images:
                images[-1].close.assert_called_once()
            image = Mock()
            images.append(image)
            return [image]

        with (
            patch('pdf2image.pdfinfo_from_bytes', return_value={'Pages': 27}),
            patch('pdf2image.convert_from_bytes', side_effect=render) as render_pdf,
            patch('pytesseract.image_to_string', side_effect=[
                'RAD-PRJ-PUR-0002_JAN2026', 'Scope text',
                'Payment term: Net 30 days\nPayment mode: Bank Transfer', 'Price summary',
            ]) as ocr,
        ):
            fields = extract_signed_po_fields(source, 'signed.pdf')

        self.assertEqual(render_pdf.call_count, 4)
        self.assertEqual(fields['payment_terms'], 'Net 30 days')
        self.assertEqual(fields['payment_mode'], 'Bank Transfer')
        self.assertEqual(fields['source_page_count'], 27)
        self.assertEqual(fields['extracted_page_count'], 4)
        self.assertTrue(fields['extraction_truncated'])
        self.assertEqual([call.kwargs['first_page'] for call in render_pdf.call_args_list], [1, 2, 3, 4])
        self.assertTrue(all(call.kwargs['timeout'] > 0 for call in ocr.call_args_list))
        for image in images:
            image.close.assert_called_once()
        with pymupdf.open(stream=source, filetype='pdf') as original:
            self.assertEqual(len(original), 27)

    def test_ocr_failure_releases_image_and_native_fallback_obeys_page_limit(self):
        image = Mock()
        with (
            patch('pdf2image.pdfinfo_from_bytes', return_value={'Pages': 27}),
            patch('pdf2image.convert_from_bytes', return_value=[image]),
            patch('pytesseract.image_to_string', side_effect=RuntimeError('OCR timed out')),
        ):
            text = extract_text_from_pdf_tesseract(document_bytes(), max_pages=4)
        image.close.assert_called_once()
        self.assertIn('Contract page 4', text)
        self.assertNotIn('Contract page 5', text)
        self.assertNotIn('Contract page 27', text)

    def test_last_resort_reader_also_obeys_page_limit(self):
        with (
            patch('pdf2image.pdfinfo_from_bytes', side_effect=RuntimeError('renderer unavailable')),
            patch('fitz.open', side_effect=RuntimeError('native reader unavailable')),
        ):
            text = extract_text_from_pdf_tesseract(document_bytes(), max_pages=4)
        self.assertIn('Contract page 4', text)
        self.assertNotIn('Contract page 5', text)

    def test_other_callers_keep_full_document_extraction(self):
        with patch('pdf2image.convert_from_bytes', side_effect=RuntimeError('renderer unavailable')):
            text = extract_text_from_pdf_tesseract(document_bytes(6))
        self.assertIn('Contract page 6', text)

    def test_unreadable_pdf_cannot_import_using_only_the_filename(self):
        with patch('pdf2image.pdfinfo_from_bytes', side_effect=RuntimeError('OCR unavailable')):
            with self.assertRaisesMessage(SignedPOImportError, 'The PDF text could not be read'):
                extract_signed_po_fields(document_bytes(6, blank=True), 'RAD-PRJ-PUR-0002_JAN2026.pdf')

    def test_ocr_infrastructure_failure_keeps_its_cause_for_the_server_error_response(self):
        with pymupdf.open() as document:
            document.new_page().draw_rect((20, 20, 100, 100))
            source = document.tobytes()
        failure = RuntimeError('OCR worker timed out')
        with patch('pdf2image.pdfinfo_from_bytes', side_effect=failure):
            with self.assertRaises(RuntimeError) as raised:
                extract_signed_po_fields(source, 'RAD-PRJ-PUR-0002_JAN2026.pdf')
        self.assertIs(raised.exception.__cause__, failure)

    def test_corrupt_pdf_gets_an_actionable_document_error(self):
        with patch('pdf2image.pdfinfo_from_bytes', side_effect=ValueError('Invalid PDF')):
            with self.assertRaisesMessage(SignedPOImportError, 'The PDF text could not be read'):
                extract_signed_po_fields(b'%PDF-broken', 'RAD-PRJ-PUR-0002_JAN2026.pdf')

    def test_seller_is_not_taken_from_a_contract_attachment_outside_the_po_pages(self):
        with pymupdf.open(stream=document_bytes(5), filetype='pdf') as document:
            document[4].insert_text((50, 100), 'Seller: Unrelated supplier\nSeller Address: Other city')
            source = document.tobytes()
        with patch(f'{SIGNED_SERVICE}.extract_text_from_pdf_tesseract', return_value='RAD-PRJ-PUR-0002_JAN2026'):
            fields = extract_signed_po_fields(source, 'signed.pdf')
        self.assertEqual(fields['vendor_name'], '')
