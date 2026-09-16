from io import BytesIO
import zipfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from apps.procurement.services.requisition_validation import (
    line_items_total,
    normalize_line_items,
    sanitize_attachment_name,
    validate_attachments,
)


class RequisitionLineItemValidationTests(SimpleTestCase):
    def test_items_are_normalized_and_totalled(self):
        items = normalize_line_items([{
            'item': 'Engineering review',
            'qty': '2',
            'uom': 'hour',
            'price': '125.50',
            'total': '251.00',
        }])

        self.assertEqual(items, [{
            'description': 'Engineering review',
            'quantity': '2',
            'unit': 'hour',
            'unit_price': '125.50',
            'total': '251.00',
        }])
        self.assertEqual(str(line_items_total(items)), '251.00')

    def test_incorrect_supplied_total_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, 'must equal quantity'):
            normalize_line_items([{
                'description': 'Pump',
                'quantity': 2,
                'unit_price': 10,
                'total': 30,
            }])

    def test_zero_quantity_and_optional_description_are_accepted(self):
        items = normalize_line_items([{'description': '', 'quantity': 0, 'unit_price': 10}])
        self.assertEqual(items[0]['description'], '')
        self.assertEqual(items[0]['quantity'], '0')
        self.assertEqual(items[0]['total'], '0.00')

    def test_partial_line_keeps_missing_operands_and_recorded_quote_total(self):
        for quantity, price in [('', '10.00'), ('2', ''), (None, None)]:
            with self.subTest(quantity=quantity, price=price):
                items = normalize_line_items([{
                    'description': '', 'quantity': quantity, 'unit_price': price, 'total': '250.00',
                }])
                self.assertEqual(items[0]['quantity'], quantity or '')
                self.assertEqual(items[0]['unit_price'], price or '')
                self.assertEqual(items[0]['total'], '250.00')
                self.assertIsNone(line_items_total(items))
                self.assertEqual(normalize_line_items(items), items)

    def test_description_only_line_does_not_invent_pricing(self):
        items = normalize_line_items([{'description': 'Pricing to follow'}])
        self.assertEqual(items[0]['quantity'], '')
        self.assertEqual(items[0]['unit_price'], '')
        self.assertEqual(items[0]['total'], '')
        self.assertIsNone(line_items_total(items))

    def test_partial_rows_keep_entered_units_codes_discounts_and_metadata(self):
        for details in ({'unit': 'HR'}, {'code': 'ITEM-1'}, {'discount': '12.00'}):
            with self.subTest(details=details):
                rows = normalize_line_items([{'description': '', 'quantity': '', 'unit_price': '', **details}])
                self.assertEqual(len(rows), 1)
                for key, value in details.items():
                    self.assertEqual(rows[0][key], value)
        for metadata in ({'vendor_id': 'supplier-1'}, {'budget': '0'}, {'vat_rate': '0'}):
            with self.subTest(metadata=metadata):
                rows = normalize_line_items([{'description': '', 'quantity': '', 'unit_price': ''}], [metadata])
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['quantity'], '')
                self.assertEqual(rows[0]['unit_price'], '')
                self.assertEqual(normalize_line_items(rows, [metadata]), rows)

    def test_negative_or_malformed_entered_quantity_is_rejected(self):
        for quantity in (-1, 'invalid', 'NaN'):
            with self.subTest(quantity=quantity), self.assertRaises(ValidationError):
                normalize_line_items([{'description': '', 'quantity': quantity, 'unit_price': 10}])

    def test_entered_invalid_price_is_rejected_even_on_partial_rows(self):
        with self.assertRaises(ValidationError):
            normalize_line_items([{
                'description': 'Pump',
                'quantity': '',
                'unit_price': 'not a price',
            }])


class RequisitionAttachmentValidationTests(SimpleTestCase):
    def test_valid_pdf_signature_is_accepted(self):
        upload = SimpleUploadedFile(
            'quote.pdf',
            b'%PDF-1.7\nvalid-test-document',
            content_type='application/pdf',
        )
        self.assertEqual(validate_attachments([upload]), [upload])
        self.assertEqual(upload.safe_name, 'quote.pdf')

    def test_spoofed_pdf_is_rejected(self):
        upload = SimpleUploadedFile(
            'quote.pdf',
            b'MZ executable content',
            content_type='application/pdf',
        )
        with self.assertRaisesMessage(ValidationError, 'contents do not match'):
            validate_attachments([upload])

    def test_valid_ooxml_structure_is_accepted(self):
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('[Content_Types].xml', '<Types />')
            archive.writestr('word/document.xml', '<document />')
        upload = SimpleUploadedFile(
            'scope.docx',
            buffer.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        )
        self.assertEqual(validate_attachments([upload]), [upload])

    def test_path_components_are_removed_from_filename(self):
        self.assertEqual(sanitize_attachment_name('../../unsafe quote.pdf'), 'unsafe_quote.pdf')
        self.assertEqual(sanitize_attachment_name('..\\unsafe quote.pdf'), 'unsafe_quote.pdf')
