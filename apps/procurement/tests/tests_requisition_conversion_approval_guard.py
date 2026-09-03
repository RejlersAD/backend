from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from apps.procurement.services.requisition_conversion import RequisitionConversionService


class _NoRelatedOrders:
    def order_by(self, *_args):
        return self

    def first(self):
        return None


class RequisitionConversionApprovalGuardTests(SimpleTestCase):
    @patch('apps.procurement.services.requisition_conversion.PurchaseOrder.objects')
    def test_approved_status_cannot_bypass_pending_workflow_stages(self, purchase_orders):
        requisition = SimpleNamespace(
            status='approved',
            purchase_orders=_NoRelatedOrders(),
            approval_workflow_config=[{
                'role': 'Procurement Department',
                'status': 'pending',
            }],
            po_number_reference='',
        )

        with self.assertRaisesMessage(ValidationError, 'All configured approval stages must be approved'):
            RequisitionConversionService._convert_locked(requisition, SimpleNamespace(id='buyer'))

        purchase_orders.create.assert_not_called()
