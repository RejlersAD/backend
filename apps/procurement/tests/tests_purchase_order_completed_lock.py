from django.test import SimpleTestCase

from apps.procurement.models import PurchaseOrder
from apps.procurement.serializers import PurchaseOrderSerializer


class PurchaseOrderCompletedLockTests(SimpleTestCase):
    def test_completed_order_cannot_be_updated(self):
        order = PurchaseOrder(status='completed')
        serializer = PurchaseOrderSerializer(
            order,
            data={'title': 'Changed title'},
            partial=True,
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn('read-only', str(serializer.errors).lower())

    def test_non_completed_order_remains_editable(self):
        order = PurchaseOrder(status='sent')
        serializer = PurchaseOrderSerializer(
            order,
            data={'title': 'Changed title'},
            partial=True,
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
