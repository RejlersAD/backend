"""Source PO totals survive cover/table OCR without borrowing adjacent values."""

from decimal import Decimal
from unittest.mock import Mock, patch

import pymupdf
from django.test import SimpleTestCase

from apps.procurement.services.signed_po_pdf_import import (
    _extraction_review_issues,
    extract_signed_po_fields,
    normalize_po_summary,
)
from apps.procurement.services.po_tesseract_extractor import extract_text_from_pdf_tesseract
from apps.procurement.tests import test_purchase_order_exports as export_fixtures


SERVICE = 'apps.procurement.services.signed_po_pdf_import'
FILENAME = 'RAD-PRJ-PUR-0003_JAN2026.pdf'


class SignedPOMoneyExtractionTests(SimpleTestCase):
    def extract(self, text):
        with pymupdf.open() as pdf:
            pdf.new_page()
            source = pdf.tobytes()
        with patch(f'{SERVICE}.extract_text_from_pdf_tesseract', return_value=text) as ocr:
            fields = extract_signed_po_fields(source, FILENAME)
        self.assertEqual(ocr.call_args_list[0].kwargs, {'max_pages': 4})
        return fields

    def assert_amounts(self, fields, net, tax, gross, currency='USD'):
        for key, value in (('total_amount', net), ('tax_amount', tax), ('gross_amount', gross)):
            self.assertEqual(fields[key], Decimal(value), key)
        self.assertEqual(fields['currency'], currency)
        self.assertEqual(_extraction_review_issues(fields), [])

    def assert_unconfirmed(self, fields):
        self.assertEqual(fields['total_amount'], Decimal('0'))
        self.assertTrue(any('purchase amount could not be confirmed' in issue for issue in _extraction_review_issues(fields)))

    def test_scanned_cover_keeps_source_net_vat_and_gross_separate(self):
        fields = self.extract(
            'Purchase Summary: Total Purchase Price: 225,608.00 USD\n'
            'Telecom equipment VAT (5%): 11,280.40 USD\nTotal Sum: 236,888.40 USD'
        )
        self.assert_amounts(fields, '225608', '11280.40', '236888.40')

    def test_native_price_table_accepts_currency_before_amount(self):
        fields = self.extract('Total Price: AED 400,000.00\nVAT (5%): AED 20,000.00\nTotal Sum: AED 420,000.00')
        self.assert_amounts(fields, '400000', '20000', '420000', 'AED')

    def test_generated_scan_columns_map_complete_labels_to_values_in_order(self):
        fields = self.extract(
            'Total Purchase Price:\n\nVAT (5%):\nTotal Sum:\n\n'
            '400,000.00 AED\n20,000.00 AED\n420,000.00 AED\nOrder Confirmation:\n'
            '--- Page 3 ---\nTotal Price: AED 400,000.00\n\n'
            'AED 20,000.00\nAED 420,000.00\n\nVAT (5%):\nTotal Sum:'
        )
        self.assert_amounts(fields, '400000', '20000', '420000', 'AED')

    def test_wrapped_table_values_accept_whole_amounts_and_actual_vat_rate(self):
        fields = self.extract('Total Price: | EUR\n1,000\nVAT (20%):\n200\nEUR\nTotal Sum: EUR 1,200')
        self.assert_amounts(fields, '1000', '200', '1200', 'EUR')

    def test_explicit_zero_vat_is_preserved(self):
        self.assert_amounts(self.extract('Total Price: GBP 500\nVAT (0%): GBP 0\nTotal Sum: GBP 500'), '500', '0', '500', 'GBP')

    def test_estimated_price_and_full_price_without_colon_are_read(self):
        for label in ('Total Estimated Price:', 'Total Estimated Price', 'Total Purchase Price'):
            with self.subTest(label=label):
                self.assert_amounts(self.extract(label + ' 15,450.00 AED\nVAT (5%): 772.50 AED\nTotal Sum: 16,222.50 AED'), '15450', '772.50', '16222.50', 'AED')

    def test_estimated_price_does_not_enter_the_purchase_description(self):
        for text in (
            'Purchase Summary: Onsite designer services\nTotal Estimated Price: 15,450.00 AED',
            'Purchase Summary: Total Estimated Price: 15,450.00 AED\nOnsite designer services VAT (5%): 772.50 AED',
        ):
            with self.subTest(text=text):
                self.assertEqual(normalize_po_summary(text), 'Onsite designer services')

    def test_displaced_cover_columns_retry_only_first_page_in_bounded_row_layout(self):
        original = (
            'Purchase Summary:\nOnsite designer services for one month\n\nApproved by:\n'
            'Total Estimated Price:\nVAT (5%):\nTotal Sum:\nOrder Confirmation:\n'
            'Seller contact column\n15,450.00 AED\n772.50 AED\n16,222.50 AED'
        )
        rows = 'Purchase Summary: Total Estimated Price: 15,450.00 AED\nOnsite designers VAT (5%): 772.50 AED\nOne month Total Sum: 16,222.50 AED'
        with patch(f'{SERVICE}.extract_text_from_pdf_tesseract', side_effect=[original, rows]) as ocr:
            fields = extract_signed_po_fields(b'%PDF-test', FILENAME)
        self.assertEqual(ocr.call_args_list[0].kwargs, {'max_pages': 4})
        self.assertEqual(ocr.call_args_list[1].kwargs, {'max_pages': 1, 'ocr_config': '--psm 6', 'max_image_dimension': 2400})
        fields['extraction_reviewed'] = True
        self.assert_amounts(fields, '15450', '772.50', '16222.50', 'AED')
        self.assertEqual(fields['summary'], 'Onsite designer services for one month')

    def test_alternate_layout_failure_keeps_unknown_amount_warning(self):
        original = 'Total Estimated Price:\nUnrelated column\n15,450.00 AED'
        with patch(f'{SERVICE}.extract_text_from_pdf_tesseract', side_effect=[original, RuntimeError('OCR timed out')]):
            fields = extract_signed_po_fields(b'%PDF-test', FILENAME)
        self.assert_unconfirmed(fields)

    def test_row_layout_pass_caps_render_pixels_and_closes_image(self):
        image = Mock()
        with (
            patch('pdf2image.pdfinfo_from_bytes', return_value={'Pages': 8}),
            patch('pdf2image.convert_from_bytes', return_value=[image]) as render,
            patch('pytesseract.image_to_string', return_value='Total Estimated Price: 15,450.00 AED') as ocr,
        ):
            extract_text_from_pdf_tesseract(b'%PDF-test', max_pages=1, ocr_config='--psm 6', max_image_dimension=2400)
        render.assert_called_once_with(b'%PDF-test', dpi=200, fmt='ppm', first_page=1, last_page=1, timeout=20, size=2400)
        self.assertEqual(ocr.call_args.kwargs['config'], '--psm 6')
        self.assertEqual(ocr.call_args.kwargs['timeout'], 15)
        image.close.assert_called_once()

    def test_missing_net_does_not_take_vat_or_gross(self):
        for text in (
            'Total Price:\nVAT (5%): USD 5.00\nTotal Sum: USD 105.00',
            'VAT (5%): USD 5.00\nTotal Sum: USD 105.00',
            'Total Price: unreadable\nVAT (5%): USD 5.00\nTotal Sum: USD 105.00',
        ):
            with self.subTest(text=text):
                self.assert_unconfirmed(self.extract(text))

    def test_incomplete_or_surplus_column_values_are_not_relabelled(self):
        for values in ('100.00 USD', '100.00 USD\n5.00 USD', '100.00 USD\n5.00 USD\n105.00 USD\n999.00 USD'):
            with self.subTest(values=values):
                fields = self.extract('Total Purchase Price:\nVAT (5%):\nTotal Sum:\n' + values)
                self.assert_unconfirmed(fields)
                self.assertEqual(fields['gross_amount'], Decimal('0'))

    def test_unrelated_section_or_page_stops_label_value_matching(self):
        for separator in ('Order Confirmation:', '--- Page 2 ---', 'Price schedule:'):
            with self.subTest(separator=separator):
                self.assert_unconfirmed(self.extract('Total Purchase Price:\nVAT (5%):\nTotal Sum:\n' + separator + '\n100.00 USD\n5.00 USD\n105.00 USD'))

    def test_item_column_heading_is_not_a_purchase_total(self):
        self.assert_unconfirmed(self.extract('Description Qty Unit Price Total Price\n100.00 USD\nVAT (5%): USD 5.00'))

    def test_cover_totals_are_not_overridden_by_later_supplier_quote(self):
        fields = self.extract('--- Page 1 ---\nTotal Estimated Price: AED 100\nVAT (5%): AED 5\nTotal Sum: AED 105\n'
                              '--- Page 2 ---\nSupplier quotation\nTotal Price: USD 900')
        self.assert_amounts(fields, '100', '5', '105', 'AED')

    def test_unreadable_cover_net_is_not_filled_from_later_quote(self):
        self.assert_unconfirmed(self.extract('--- Page 1 ---\nTotal Estimated Price: illegible\nVAT (5%): AED 5\nTotal Sum: AED 105\n'
                                             '--- Page 2 ---\nSupplier quotation\nTotal Price: AED 100'))

    def test_conflicting_duplicate_totals_and_mixed_currencies_require_review(self):
        for text in (
            'Total Purchase Price: USD 100\nTotal Price: USD 200',
            'Total Price: USD 100\nVAT (5%): AED 5\nTotal Sum: USD 105',
            'Total Price: USD 100\nVAT (5%): USD 5\nVAT (5%): USD 50',
            'Total Price: USD 100\nVAT (5%): illegible\nTotal Sum: USD 105',
            'Total Price: USD 100\nVAT (5%): USD 5\nTotal Sum: USD 150',
        ):
            with self.subTest(text=text):
                with patch(f'{SERVICE}.extract_text_from_pdf_tesseract', return_value=text) as ocr:
                    fields = extract_signed_po_fields(b'%PDF-test', FILENAME)
                self.assert_unconfirmed(fields)
                ocr.assert_called_once_with(b'%PDF-test', max_pages=4)

    def test_malformed_or_negative_amounts_remain_unknown(self):
        for value in ('USD 1,00.00', '1,00.00 USD', 'USD -100.00', '-100.00 USD', 'USD 100.001', '100.001 USD', 'USD 1O0.00'):
            with self.subTest(value=value):
                self.assert_unconfirmed(self.extract('Total Price: ' + value))

    def test_official_native_renderer_round_trips_totals_with_native_fallback(self):
        order = export_fixtures.PurchaseOrderExportTests()._order()
        source, warnings = export_fixtures.build_purchase_order_pdf(order)
        self.assertEqual(warnings, [])
        with patch('pdf2image.pdfinfo_from_bytes', side_effect=RuntimeError('OCR unavailable')):
            fields = extract_signed_po_fields(source, FILENAME)
        self.assert_amounts(fields, '100', '5', '105')

    def test_amount_on_attachment_page_five_is_not_used(self):
        with pymupdf.open() as pdf:
            for number in range(5):
                page = pdf.new_page()
                page.insert_text((40, 60), 'PO source page' if number < 4 else 'Total Purchase Price: USD 999,999.00')
            source = pdf.tobytes()
        with patch('pdf2image.pdfinfo_from_bytes', side_effect=RuntimeError('OCR unavailable')):
            fields = extract_signed_po_fields(source, FILENAME)
        self.assert_unconfirmed(fields)
        self.assertEqual(fields['extracted_page_count'], 4)
