from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.finance.models import (
    Invoice,
    InvoiceLineItem,
    InvoiceMatchStatus,
    InvoicePaymentStatus,
    InvoicePurchaseOrderAllocation,
    ProcurementInvoiceStatus,
)
from apps.finance.serializers import InvoiceDetailSerializer
from apps.finance.services.payables import record_payment_operation
from apps.procurement.models import PurchaseOrder, Receipt, Vendor


class ProcurementVendorInvoiceFoundationTests(TestCase):
    def setUp(self):
        self.vendor = Vendor.objects.create(
            vendor_code='TEST-VENDOR-001',
            name='Test Vendor LLC',
            status='active',
        )
        self.purchase_order = PurchaseOrder.objects.create(
            po_number='RAD-GEN-PUR-9999_2026',
            vendor=self.vendor,
            title='Test procurement order',
            category='other',
            total_amount=Decimal('1050.00'),
            currency='AED',
        )
        self.invoice = Invoice.objects.create(
            invoice_number='SUP-INV-TEST-001',
            vendor_name=self.vendor.name,
            vendor=self.vendor,
            amount=Decimal('1000.00'),
            tax_amount=Decimal('50.00'),
            total_amount=Decimal('1050.00'),
            currency='AED',
            original_filename='supplier-invoice.pdf',
            file_path='invoices/supplier-invoice.pdf',
        )

    def test_existing_invoice_defaults_are_procurement_safe(self):
        self.assertEqual(self.invoice.procurement_status, ProcurementInvoiceStatus.OCR_REVIEW)
        self.assertEqual(self.invoice.match_status, InvoiceMatchStatus.UNMATCHED)
        self.assertEqual(self.invoice.payment_status, InvoicePaymentStatus.NOT_SCHEDULED)
        self.assertTrue(self.invoice.manual_review_required)
        self.assertEqual(self.invoice.paid_amount, Decimal('0'))

    def test_structured_lines_are_ordered_and_unique_per_invoice(self):
        InvoiceLineItem.objects.create(
            invoice=self.invoice,
            line_number=2,
            description='Second line',
            total_amount=Decimal('525.00'),
            currency='AED',
        )
        InvoiceLineItem.objects.create(
            invoice=self.invoice,
            line_number=1,
            description='First line',
            total_amount=Decimal('525.00'),
            currency='AED',
        )
        self.assertEqual(
            list(self.invoice.structured_line_items.values_list('line_number', flat=True)),
            [1, 2],
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            InvoiceLineItem.objects.create(invoice=self.invoice, line_number=1)

    def test_po_allocation_supports_receipt_evidence_and_is_unique(self):
        receipt = Receipt.objects.create(
            receipt_number='GRN-TEST-001',
            purchase_order=self.purchase_order,
            status='accepted',
        )
        allocation = InvoicePurchaseOrderAllocation.objects.create(
            invoice=self.invoice,
            purchase_order=self.purchase_order,
            allocated_amount=Decimal('1050.00'),
            currency='AED',
            vendor_matched=True,
            currency_matched=True,
        )
        allocation.receipts.add(receipt)
        self.assertEqual(list(allocation.receipts.all()), [receipt])
        with self.assertRaises(IntegrityError), transaction.atomic():
            InvoicePurchaseOrderAllocation.objects.create(
                invoice=self.invoice,
                purchase_order=self.purchase_order,
                allocated_amount=Decimal('1.00'),
            )

    def test_detail_serializer_exposes_structured_procurement_data(self):
        InvoiceLineItem.objects.create(
            invoice=self.invoice,
            line_number=1,
            description='Structured item',
            total_amount=Decimal('1050.00'),
            currency='AED',
        )
        InvoicePurchaseOrderAllocation.objects.create(
            invoice=self.invoice,
            purchase_order=self.purchase_order,
            allocated_amount=Decimal('1050.00'),
            currency='AED',
        )
        data = InvoiceDetailSerializer(self.invoice).data
        self.assertEqual(data['vendor_master_name'], self.vendor.name)
        self.assertEqual(len(data['structured_line_items']), 1)
        self.assertEqual(len(data['po_allocations']), 1)
        self.assertEqual(
            data['po_allocations'][0]['purchase_order_number'],
            self.purchase_order.po_number,
        )

    def test_invoice_number_is_unique_per_vendor_not_globally(self):
        other_vendor = Vendor.objects.create(
            vendor_code='TEST-VENDOR-002', name='Second Vendor LLC', status='active',
        )
        Invoice.objects.create(
            invoice_number=self.invoice.invoice_number,
            vendor=other_vendor,
            vendor_name=other_vendor.name,
            total_amount=Decimal('10.00'), currency='AED',
            original_filename='other.pdf', file_path='invoices/other.pdf',
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Invoice.objects.create(
                invoice_number=self.invoice.invoice_number.lower(), vendor=self.vendor,
                vendor_name=self.vendor.name, total_amount=Decimal('10.00'), currency='AED',
                original_filename='duplicate.pdf', file_path='invoices/duplicate.pdf',
            )

    def test_payment_ledger_drives_invoice_payment_status(self):
        self.invoice.procurement_status = ProcurementInvoiceStatus.APPROVED_FOR_PAYMENT
        self.invoice.save(update_fields=['procurement_status'])
        record_payment_operation(self.invoice, {
            'operation': 'payment', 'amount': '500.00',
            'effective_date': '2026-08-18', 'reference': 'BANK-001',
        }, None)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, Decimal('500.00'))
        self.assertEqual(self.invoice.payment_status, InvoicePaymentStatus.PARTIAL)
        self.assertEqual(self.invoice.payment_operations.count(), 1)
