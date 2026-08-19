import shutil
import tempfile
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.exceptions import ValidationError

from apps.finance.models import Invoice, InvoiceMatchStatus
from apps.finance.services.vendor_invoice_import import VendorInvoiceImportService
from apps.procurement.models import PurchaseOrder, Receipt, Vendor


SAMPLE_OCR = """
ALPHA ENGINEERING SERVICES LLC
Tax Invoice Number: AE-INV-2026-044
Invoice Date: 17/08/2026
Supplier: ALPHA ENGINEERING SERVICES LLC
Purchase Order: RAD-PRJ-PUR-0037_2026
Payment Terms: 30 days from invoice date
Subtotal: AED 1,000.00
VAT Amount: AED 50.00
Grand Total: AED 1,050.00
TRN: 100123456700003
"""


class VendorInvoiceOCRParsingTests(SimpleTestCase):
    def test_dynamic_labels_extract_core_invoice_fields(self):
        extracted, confidence, warnings = VendorInvoiceImportService()._extract_fields(SAMPLE_OCR)

        self.assertEqual(extracted['invoice_number'], 'AE-INV-2026-044')
        self.assertEqual(extracted['vendor_name'], 'ALPHA ENGINEERING SERVICES LLC')
        self.assertEqual(extracted['invoice_date'], '2026-08-17')
        self.assertEqual(extracted['po_reference_text'], 'RAD-PRJ-PUR-0037_2026')
        self.assertEqual(extracted['amount'], '1000.00')
        self.assertEqual(extracted['tax_amount'], '50.00')
        self.assertEqual(extracted['total_amount'], '1050.00')
        self.assertEqual(extracted['currency'], 'AED')
        self.assertGreaterEqual(confidence['total_amount'], 90)
        self.assertEqual(warnings, [])

    def test_missing_total_is_calculated_and_flagged_for_review(self):
        extracted, _, warnings = VendorInvoiceImportService()._extract_fields(
            'Invoice: X-1\nInvoice Date: 2026-08-17\nSubtotal: USD 100.00\nTax Amount: USD 5.00'
        )
        self.assertEqual(extracted['total_amount'], '105.00')
        self.assertTrue(any('calculated' in warning.lower() for warning in warnings))


class VendorInvoiceReviewedImportTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix='radai-invoice-test-')
        self.override = override_settings(MEDIA_ROOT=self.media_root)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(shutil.rmtree, self.media_root, True)
        self.vendor = Vendor.objects.create(
            vendor_code='OCR-VENDOR-001', name='Alpha Engineering Services LLC', status='active',
        )
        self.po = PurchaseOrder.objects.create(
            po_number='RAD-PRJ-PUR-9998_2026', vendor=self.vendor,
            title='OCR import test PO', category='other', total_amount=Decimal('1050.00'), currency='AED',
        )
        self.payload = {
            'invoice_number': 'AE-INV-TEST-001',
            'vendor_id': str(self.vendor.id),
            'vendor_name': self.vendor.name,
            'invoice_date': '2026-08-17',
            'amount': '1000.00',
            'tax_amount': '50.00',
            'total_amount': '1050.00',
            'currency': 'AED',
            'ocr_confidence': '91.50',
            'line_items': [{
                'line_number': 1, 'description': 'Engineering services',
                'quantity': '1', 'unit_price': '1000.00', 'net_amount': '1000.00',
                'tax_amount': '50.00', 'total_amount': '1050.00', 'currency': 'AED',
            }],
        }

    @patch('apps.finance.services.vendor_invoice_import.extract_text_from_pdf_tesseract', return_value=SAMPLE_OCR)
    def test_preview_suggests_but_does_not_write(self, _extract):
        before = Invoice.objects.count()
        result = VendorInvoiceImportService().preview(b'%PDF-1.4 preview-only', 'invoice.pdf')
        self.assertEqual(Invoice.objects.count(), before)
        self.assertEqual(result['extracted']['invoice_number'], 'AE-INV-2026-044')
        self.assertTrue(result['vendor_suggestions'])
        self.assertTrue(all(item['requires_user_confirmation'] for item in result['purchase_order_suggestions']))

    def test_reviewed_import_records_normalized_lines_without_po(self):
        invoice = VendorInvoiceImportService().save_reviewed(
            pdf_bytes=b'%PDF-1.4 reviewed-no-po', filename='invoice.pdf',
            reviewed_data=self.payload, user=None,
        )
        self.assertEqual(invoice.vendor, self.vendor)
        self.assertEqual(invoice.structured_line_items.count(), 1)
        self.assertEqual(invoice.po_allocations.count(), 0)
        self.assertEqual(invoice.line_items[0]['total_amount'], '1050.00')

    def test_po_link_requires_explicit_confirmation(self):
        payload = {**self.payload, 'confirmed_po_id': str(self.po.id)}
        with self.assertRaises(ValidationError):
            VendorInvoiceImportService().save_reviewed(
                pdf_bytes=b'%PDF-1.4 no-confirmation', filename='invoice.pdf',
                reviewed_data=payload, user=None,
            )
        self.assertFalse(Invoice.objects.filter(invoice_number='AE-INV-TEST-001').exists())

    def test_confirmed_po_creates_audited_three_way_match(self):
        self.po.items = [{'item_no': '1', 'description': 'Engineering services', 'quantity': '1'}]
        self.po.save(update_fields=['items'])
        Receipt.objects.create(
            receipt_number='OCR-GRN-001', purchase_order=self.po, status='accepted',
            items_received=[{'item_no': '1', 'accepted_qty': '1'}],
        )
        payload = {
            **self.payload,
            'confirmed_po_id': str(self.po.id),
            'confirm_po_match': True,
        }
        invoice = VendorInvoiceImportService().save_reviewed(
            pdf_bytes=b'%PDF-1.4 confirmed-match', filename='invoice.pdf',
            reviewed_data=payload, user=None,
        )
        allocation = invoice.po_allocations.get()
        self.assertEqual(allocation.match_status, InvoiceMatchStatus.VERIFIED)
        self.assertEqual(allocation.receipts.count(), 1)
        self.assertEqual(allocation.exception_codes, [])
        self.assertTrue(allocation.line_items_matched)
        self.assertTrue(allocation.receipt_quantities_matched)
        self.assertTrue(self.po.related_invoices.filter(pk=invoice.pk).exists())
        self.assertTrue(invoice.audit_logs.filter(action='procurement_invoice_imported').exists())

    def test_missing_receipt_is_recorded_as_match_exception(self):
        payload = {
            **self.payload,
            'confirmed_po_id': str(self.po.id),
            'confirm_po_match': True,
        }
        invoice = VendorInvoiceImportService().save_reviewed(
            pdf_bytes=b'%PDF-1.4 missing-receipt', filename='invoice.pdf',
            reviewed_data=payload, user=None,
        )
        allocation = invoice.po_allocations.get()
        self.assertEqual(allocation.match_status, InvoiceMatchStatus.EXCEPTION)
        self.assertIn('missing_accepted_receipt', allocation.exception_codes)
