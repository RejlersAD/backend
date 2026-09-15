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

    def test_non_positive_quantity_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, 'must be at least'):
            normalize_line_items([{
                'description': 'Pump',
                'quantity': 0,
                'unit_price': 10,
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
