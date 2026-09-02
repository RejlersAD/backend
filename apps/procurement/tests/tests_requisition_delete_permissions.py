from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.procurement.services.requisition_workflow import RequisitionWorkflowService
from apps.procurement.views import PurchaseRequisitionViewSet


class RequisitionDeletePermissionTests(SimpleTestCase):
    def setUp(self):
        self.user = SimpleNamespace(id=7)
        self.view = PurchaseRequisitionViewSet()
        self.view.request = SimpleNamespace(user=self.user)

    @patch.object(RequisitionWorkflowService, '_is_super_admin', return_value=False)
    def test_issuer_can_delete_requisition_in_any_status(self, _is_super_admin):
        for status in ('draft', 'pending', 'approved', 'rejected', 'converted', 'cancelled'):
            with self.subTest(status=status):
                requisition = SimpleNamespace(issued_by_id=self.user.id, status=status)
                self.view._enforce_owner_mutation(requisition, deletable=True)

    @patch.object(RequisitionWorkflowService, '_is_super_admin', return_value=False)
    def test_non_issuer_cannot_delete_requisition(self, _is_super_admin):
        requisition = SimpleNamespace(issued_by_id=99, status='approved')

        with self.assertRaises(PermissionDenied):
            self.view._enforce_owner_mutation(requisition, deletable=True)

    @patch.object(RequisitionWorkflowService, '_is_super_admin', return_value=False)
    def test_non_draft_requisition_is_still_not_editable(self, _is_super_admin):
        requisition = SimpleNamespace(issued_by_id=self.user.id, status='approved')

        with self.assertRaises(ValidationError):
            self.view._enforce_owner_mutation(requisition)
