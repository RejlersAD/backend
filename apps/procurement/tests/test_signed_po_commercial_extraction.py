"""PO commercial facts stay with their labelled source field and page."""

from unittest.mock import patch

import pymupdf
from django.test import SimpleTestCase

from apps.procurement.services.po_commercial_extraction import extract_po_commercial_fields
from apps.procurement.services.signed_po_pdf_import import extract_signed_po_fields
from apps.procurement.tests import test_purchase_order_exports as export_fixtures


SERVICE = 'apps.procurement.services.signed_po_pdf_import'
COMMERCIAL = 'apps.procurement.services.po_commercial_extraction'
FILENAME = 'RAD-PRJ-PUR-0042_SEP2026.pdf'
PAYMENT = '30 days net for timesheet approval'
DELIVERY = 'Services completed and accepted'


def two_column_source():
    """A valid native cover with interleaved baselines and a split field label."""
    with pymupdf.open() as document:
        page = document.new_page(width=800, height=900)
        for x, y, value in (
            (40, 40, 'PURCHASE ORDER'),
            (40, 60, FILENAME[:-4]),
            (40, 90, 'Seller:'), (155, 90, 'Synthetic Supplier LLC'),
            (425, 90, 'Seller Reference:'), (545, 90, "Amira O'Neil-Santos,"),
            (545, 104, 'Director'), (545, 118, 'supplier@example.test'),
            (40, 130, 'Seller Address:'), (155, 130, 'Synthetic Supplier Office'),
            (425, 150, 'Quote Ref.:'), (545, 150, 'SYNTHETIC-QUOTE-42'),
            (40, 180, 'Invoicing Address:'), (155, 180, 'Buyer Office'),
            (425, 190, 'Buyer Reference:'), (545, 190, 'Unrelated Buyer'),
            (545, 204, 'buyer@example.test'),
            (40, 300, 'Payment'), (40, 314, 'Terms:'),
            (155, 300, '30 days net for timesheet'), (155, 314, 'approval'),
            (425, 300, 'Delivery terms:'), (545, 300, 'Services completed'),
            (545, 314, 'and accepted'),
            (40, 355, 'Payment Mode:'), (155, 355, 'Bank Transfer'),
            (425, 355, 'Start date:'), (545, 355, '01.10.2026'),
            (40, 385, 'Project:'), (155, 385, '123456'),
            (425, 385, 'Marking:'), (545, 385, 'SOURCE-ONLY-42'),
            (40, 430, 'Purchase Summary:'), (155, 430, 'Synthetic design services'),
            (40, 465, 'Total Purchase Price: 100.00 USD'),
            (40, 500, 'Approved by:'), (155, 500, 'Synthetic Buyer Approver'),
        ):
            page.insert_text((x, y), value, fontsize=10)
        annex = document.new_page(width=800, height=900)
        annex.insert_text((40, 60), (
            'CONTRACT ATTACHMENT\nSeller Reference: Unrelated Contract Contact\n'
            'Payment Terms: Advance payment in full\n'
            'Delivery terms: Unrelated contract delivery\nPayment Mode: Cash'
        ))
        return document.tobytes()


def native_reference_source(reference):
    with pymupdf.open() as document:
        page = document.new_page(width=800, height=900)
        page.insert_text((425, 90), 'Seller Reference:', fontsize=10)
        if reference:
            page.insert_text((545, 90), reference, fontsize=10)
        page.insert_text((425, 150), 'Quote Ref.:', fontsize=10)
        page.insert_text((545, 150), 'NATIVE-QUOTE', fontsize=10)
        return document.tobytes()


class SignedPOCommercialExtractionTests(SimpleTestCase):
    def extract(self, text):
        source = '--- Page 1 ---\nPURCHASE ORDER\n' + FILENAME[:-4] + '\n' + text
        with patch(f'{SERVICE}.extract_text_from_pdf_tesseract', return_value=source):
            return extract_signed_po_fields(b'%PDF-test', FILENAME)

    def test_seller_reference_preserves_literal_name_title_and_separates_email(self):
        for reference in ('Surafel Jimma, Director', "Amira O'Neil-Santos, Sales Director", 'Ms. Synthetic Contact'):
            with self.subTest(reference=reference):
                fields = self.extract(
                    'Seller: Synthetic Supplier LLC\nSeller Reference: ' + reference + '\n'
                    'supplier@example.test\nQuote Ref.: SOURCE-QUOTE\n'
                    'Buyer Reference: Unrelated Buyer\nbuyer@example.test'
                )
                self.assertEqual(fields['seller_reference'], reference)
                self.assertEqual(fields['seller_email'], 'supplier@example.test')

    def test_wrapped_payment_label_and_value_are_read_without_a_later_payment_label(self):
        fields = self.extract('Payment\nTerms:\n30 days net for timesheet\napproval\nMarking: SOURCE-42')
        self.assertEqual(fields['payment_terms'], PAYMENT)

    def test_delivery_value_stops_before_neighbour_fields_or_page_boundary(self):
        for boundary in (
            'Payment Mode: Bank Transfer', 'Start date: 01.10.2026',
            'Marking: SOURCE-42', 'Quote Ref.: SOURCE-QUOTE', 'Project: 123456',
            'Approved by: Synthetic Approver', 'Delivery date: 02.10.2026',
            '--- Page 2 ---\nUnrelated contract text\nPayment Terms: Advance payment',
            '',
        ):
            with self.subTest(boundary=boundary):
                fields = self.extract('Delivery terms: Services completed\nand accepted\n' + boundary)
                self.assertEqual(fields['delivery_terms'], DELIVERY)

    def test_payment_value_stops_before_neighbour_fields_or_page_boundary(self):
        for boundary in (
            'Delivery terms: Services accepted', 'Payment Mode: Bank Transfer',
            'Start date: 01.10.2026', 'Marking: SOURCE-42', 'Project: 123456',
            '--- Page 2 ---\nContract text\nPayment Terms: Advance payment', '',
        ):
            with self.subTest(boundary=boundary):
                self.assertEqual(self.extract('Payment Terms: ' + PAYMENT + '\n' + boundary)['payment_terms'], PAYMENT)

    def test_cover_values_are_not_replaced_or_extended_by_later_repeated_labels(self):
        fields = self.extract(
            'Seller Reference: Synthetic Contact, Director\nQuote Ref.: COVER-QUOTE\n'
            'Payment Terms: ' + PAYMENT + '\nDelivery terms: ' + DELIVERY + '\n'
            '--- Page 2 ---\nContract attachment\nSeller Reference: Other Contract Contact\n'
            'Payment Terms: Advance payment\nDelivery terms: Other delivery\nPayment Mode: Cash'
        )
        self.assertEqual(fields['seller_reference'], 'Synthetic Contact, Director')
        self.assertEqual(fields['payment_terms'], PAYMENT)
        self.assertEqual(fields['delivery_terms'], DELIVERY)

    def test_blank_cover_fields_do_not_borrow_values_from_neighbours_or_later_pages(self):
        fields = self.extract(
            'Seller Reference:\nBuyer Reference: Unrelated Buyer\n'
            'Payment Terms:\nDelivery terms:\nMarking: SOURCE-42\n'
            '--- Page 2 ---\nSeller Reference: Contract Contact\nQuote Ref.: OTHER-QUOTE\n'
            'Payment Terms: Advance payment\nDelivery terms: Other delivery\nPayment Mode: Cash'
        )
        for field in ('seller_reference', 'payment_terms', 'delivery_terms'):
            self.assertEqual(fields[field], '', field)

    def test_terms_first_printed_on_later_inspected_po_page_remain_supported(self):
        fields = self.extract(
            'Purchase Summary: Synthetic design services\nTotal Purchase Price: 100.00 USD\n'
            '--- Page 2 ---\nPO scope\n'
            '--- Page 3 ---\nPayment Terms: ' + PAYMENT + '\nDelivery terms: ' + DELIVERY + '\n'
            'Payment Mode: Bank Transfer\n--- Page 4 ---\nPayment Terms: Unrelated repeated terms'
        )
        self.assertEqual(fields['payment_terms'], PAYMENT)
        self.assertEqual(fields['delivery_terms'], DELIVERY)

    def test_missing_commercial_labels_do_not_promote_unlabelled_contract_text(self):
        fields = self.extract('Buyer Reference: Synthetic Buyer\n' + PAYMENT + '\n' + DELIVERY)
        for field in ('seller_reference', 'payment_terms', 'delivery_terms'):
            self.assertEqual(fields[field], '', field)

    def test_legitimate_bounded_source_values_are_not_silently_truncated(self):
        payment = 'Payment after reviewed milestone ' * 12
        delivery = 'Acceptance after verified service ' * 8
        fields = self.extract('Payment Terms: ' + payment + '\nDelivery terms: ' + delivery + '\nMarking: SOURCE-42')
        self.assertEqual(fields['payment_terms'], payment.strip())
        self.assertEqual(fields['delivery_terms'], delivery.strip())

    def test_explicit_field_boundaries_preserve_paragraphs_and_buyer_name_in_terms(self):
        payment = (
            '30 days after Rejlers International Engineering accepts the timesheet.\n\n'
            'Submit the supporting service record with the invoice.'
        )
        fields = self.extract('Payment Terms: ' + payment + '\nDelivery terms: ' + DELIVERY + '\nMarking: SOURCE-42')
        self.assertEqual(fields['payment_terms'], ' '.join(payment.split()))
        self.assertEqual(fields['delivery_terms'], DELIVERY)

    def test_without_a_next_field_blank_paragraph_stops_unrelated_document_body(self):
        fields = self.extract('Payment Terms: ' + PAYMENT + '\n\nUnrelated appendix narrative without a field label.')
        self.assertEqual(fields['payment_terms'], PAYMENT)

    def test_native_columns_keep_split_payment_label_and_wrapped_values_separate(self):
        source = two_column_source()
        with (
            patch('pdf2image.pdfinfo_from_bytes', side_effect=RuntimeError('OCR disabled for native fixture')),
            patch(f'{COMMERCIAL}._ocr_words', return_value=[]) as commercial_ocr,
        ):
            fields = extract_signed_po_fields(source, FILENAME)
        commercial_ocr.assert_not_called()
        self.assertEqual(fields['seller_reference'], "Amira O'Neil-Santos, Director")
        self.assertEqual(fields['payment_terms'], PAYMENT)
        self.assertEqual(fields['delivery_terms'], DELIVERY)
        self.assertEqual(fields['source_page_count'], 2)

    def test_scanned_column_recovery_uses_only_one_cover_word_layout_pass(self):
        with pymupdf.open(stream=two_column_source(), filetype='pdf') as reference:
            words = [
                {'text': word[4], 'bbox': list(word[:4]), 'method': 'ocr', 'confidence': 95}
                for word in reference[0].get_text('words')
            ]
        with pymupdf.open() as document:
            document.new_page(width=800, height=900).draw_rect((40, 40, 760, 850))
            document.new_page(width=800, height=900)
            source = document.tobytes()
        text = (
            '--- Page 1 ---\nPURCHASE ORDER\n' + FILENAME[:-4] + '\n'
            'Seller Reference: unreadable\nQuote Ref.: SOURCE-QUOTE\n'
            'Payment 30 days net for timesheet Delivery terms: Services completed\n'
            'Terms: approval and accepted\nPayment Mode: Bank Transfer\n'
            '--- Page 2 ---\nPayment Terms: Unrelated contract terms\n'
            'Delivery terms: Unrelated contract delivery\nPayment Mode: Cash'
        )
        inspected_pages = []

        def cover_words(page, *, clip):
            inspected_pages.append(page.number)
            self.assertEqual(clip, page.rect)
            return words

        with (
            patch(f'{SERVICE}.extract_text_from_pdf_tesseract', return_value=text),
            patch(f'{COMMERCIAL}._ocr_words', side_effect=cover_words) as commercial_ocr,
        ):
            fields = extract_signed_po_fields(source, FILENAME)
        self.assertEqual(inspected_pages, [0])
        commercial_ocr.assert_called_once()
        self.assertEqual(fields['seller_reference'], "Amira O'Neil-Santos, Director")
        self.assertEqual(fields['payment_terms'], PAYMENT)
        self.assertEqual(fields['delivery_terms'], DELIVERY)

    def test_close_stacked_payment_labels_do_not_pair_terms_with_the_next_mode_label(self):
        # Label boxes reproduce the reported scan's overlapping OCR baselines;
        # the value words are synthetic and no source image is retained.
        words = [
            {'text': text, 'bbox': box, 'method': 'ocr', 'confidence': 95}
            for text, box in (
                ('Payment', [77.04, 305.28, 114.12, 317.16]),
                ('Terms:', [76.68, 317.16, 105.48, 324.00]),
                ('Payment', [77.04, 326.16, 114.12, 338.04]),
                ('Mode:', [77.04, 331.20, 102.24, 345.96]),
                ('Project:', [77.04, 360, 113, 370]),
            )
        ]
        for text, y in ((PAYMENT, 307), ('Bank Transfer', 328)):
            x = 136
            for value in text.split():
                width = pymupdf.get_text_length(value, fontsize=8)
                words.append({'text': value, 'bbox': [x, y, x + width, y + 9], 'method': 'ocr', 'confidence': 95})
                x += width + 2
        with pymupdf.open() as document:
            document.new_page().draw_rect((40, 40, 550, 800))
            source = document.tobytes()
        with patch(f'{COMMERCIAL}._ocr_words', return_value=words) as commercial_ocr:
            fields = extract_po_commercial_fields(source, 'Payment Terms: unreadable\nPayment Mode: unreadable')
        commercial_ocr.assert_called_once()
        self.assertEqual(fields['payment_terms'], PAYMENT)
        self.assertEqual(fields['payment_mode'], 'Bank Transfer')

    def test_hybrid_cover_recovers_scanned_terms_without_overriding_native_reference_or_blank(self):
        with pymupdf.open(stream=two_column_source(), filetype='pdf') as reference:
            words = [
                {'text': word[4], 'bbox': list(word[:4]), 'method': 'ocr', 'confidence': 95}
                for word in reference[0].get_text('words')
            ]
        text = (
            'Seller Reference: Wrong OCR Contact\nQuote Ref.: SOURCE-QUOTE\n'
            'Payment 30 days net for timesheet Delivery terms: Services completed\n'
            'Terms: approval and accepted\nPayment Mode: Bank Transfer'
        )
        for reference in ('Native Contact, Director', ''):
            with self.subTest(reference=reference):
                source = native_reference_source(reference)
                with patch(f'{COMMERCIAL}._ocr_words', return_value=words) as commercial_ocr:
                    fields = extract_po_commercial_fields(source, text)
                commercial_ocr.assert_called_once()
                self.assertEqual(fields['seller_reference'], reference)
                self.assertEqual(fields['payment_terms'], PAYMENT)
                self.assertEqual(fields['delivery_terms'], DELIVERY)
                self.assertEqual(fields['payment_mode'], 'Bank Transfer')

    def test_hybrid_cover_ocr_failure_keeps_native_reference_and_bounded_text_terms(self):
        source = native_reference_source('Native Contact, Director')
        text = (
            'Seller Reference: Wrong OCR Contact\nQuote Ref.: SOURCE-QUOTE\n'
            'Payment Terms: ' + PAYMENT + '\nDelivery terms: ' + DELIVERY + '\n'
            'Payment Mode: Bank Transfer\nMarking: SOURCE-42'
        )
        with patch(f'{COMMERCIAL}._ocr_words', side_effect=RuntimeError('OCR timed out')) as commercial_ocr:
            fields = extract_po_commercial_fields(source, text)
        commercial_ocr.assert_called_once()
        self.assertEqual(fields['seller_reference'], 'Native Contact, Director')
        self.assertEqual(fields['payment_terms'], PAYMENT)
        self.assertEqual(fields['delivery_terms'], DELIVERY)
        self.assertEqual(fields['payment_mode'], 'Bank Transfer')

    def test_official_native_cover_round_trips_commercial_fields(self):
        order = export_fixtures.PurchaseOrderExportTests()._realistic_long_contact_order()
        order.seller_reference = "Amira O'Neil-Santos, Director\nsupplier@example.test"
        order.payment_terms = PAYMENT
        order.delivery_terms = DELIVERY
        source, warnings = export_fixtures.build_purchase_order_pdf(order)
        self.assertEqual(warnings, [])
        with (
            patch('pdf2image.pdfinfo_from_bytes', side_effect=RuntimeError('OCR disabled for native fixture')),
            patch(f'{COMMERCIAL}._ocr_words', return_value=[]) as commercial_ocr,
        ):
            fields = extract_signed_po_fields(source, FILENAME)
        commercial_ocr.assert_not_called()
        self.assertEqual(fields['seller_reference'], "Amira O'Neil-Santos, Director")
        self.assertEqual(fields['payment_terms'], PAYMENT)
        self.assertEqual(fields['delivery_terms'], DELIVERY)
