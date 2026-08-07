from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.exceptions import ValidationError

from apps.procurement.models import PurchaseOrder, PurchaseOrderLine, Vendor
from apps.procurement.services.goods_receipt import GoodsReceiptService


User = get_user_model()


class GoodsReceiptWorkflowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='receiver', password='test-password')
        self.vendor = Vendor.objects.create(vendor_code='V-GR-001', name='GR Test Vendor', status='active')
        self.po = PurchaseOrder.objects.create(
            po_number='RAD-GEN-PUR-9991_2026',
            vendor=self.vendor,
            title='Test materials',
            status='acknowledged',
            category='consumables',
            total_amount=Decimal('1000.00'),
            created_by=self.user,
        )
        self.line = PurchaseOrderLine.objects.create(
            purchase_order=self.po,
            line_number=1,
            description='Test material',
            ordered_quantity=Decimal('10'),
            unit_of_measure='EA',
            unit_price=Decimal('100'),
        )

    def _create_receipt(self, delivered, accepted=None, rejected=0, suffix='1'):
        accepted = delivered if accepted is None else accepted
        return GoodsReceiptService.create({
            'purchase_order': self.po,
            'delivery_note_number': f'DN-{suffix}',
            'lines': [{
                'purchase_order_line': self.line.id,
                'delivered_quantity': Decimal(str(delivered)),
                'accepted_quantity': Decimal(str(accepted)),
                'rejected_quantity': Decimal(str(rejected)),
                'rejection_reason': 'Damaged during delivery' if rejected else '',
            }],
        }, self.user)

    def test_full_receipt_completes_purchase_order(self):
        receipt = self._create_receipt(10)
        self.assertEqual(receipt.status, 'draft')
        self.assertRegex(receipt.receipt_number, r'^RAD-GR-\d{4}_\d{4}$')

        GoodsReceiptService.submit(receipt.id, self.user)
        receipt = GoodsReceiptService.accept(receipt.id, self.user)
        self.po.refresh_from_db()

        self.assertEqual(receipt.status, 'accepted')
        self.assertEqual(self.po.status, 'completed')
        self.assertIsNotNone(self.po.actual_delivery)

    def test_partial_receipts_recalculate_remaining_quantity(self):
        first = self._create_receipt(4, suffix='1')
        GoodsReceiptService.submit(first.id, self.user)
        GoodsReceiptService.accept(first.id, self.user)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, 'partially_received')

        summary = GoodsReceiptService.receiving_summary(self.po)
        self.assertEqual(summary['accepted_quantity'], Decimal('4'))
        self.assertEqual(summary['remaining_quantity'], Decimal('6'))

        second = self._create_receipt(6, suffix='2')
        GoodsReceiptService.submit(second.id, self.user)
        GoodsReceiptService.accept(second.id, self.user)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, 'completed')

    def test_pending_receipt_reservation_blocks_concurrent_over_receipt(self):
        first = self._create_receipt(6, suffix='1')
        second = self._create_receipt(6, suffix='2')
        GoodsReceiptService.submit(first.id, self.user)

        with self.assertRaises(ValidationError):
            GoodsReceiptService.submit(second.id, self.user)

    def test_rejected_receipt_does_not_increase_accepted_quantity(self):
        receipt = self._create_receipt(5, suffix='1')
        GoodsReceiptService.submit(receipt.id, self.user)
        GoodsReceiptService.reject(receipt.id, self.user, 'Entire delivery failed inspection')
        self.po.refresh_from_db()

        summary = GoodsReceiptService.receiving_summary(self.po)
        self.assertEqual(summary['accepted_quantity'], Decimal('0'))
        self.assertEqual(summary['remaining_quantity'], Decimal('10'))
        self.assertNotEqual(self.po.status, 'completed')
