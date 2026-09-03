from django.test import SimpleTestCase
from rest_framework.permissions import IsAuthenticated

from apps.procurement.views import PurchaseRequisitionViewSet


class PurchaseRequisitionApprovalAccessTests(SimpleTestCase):
    def test_personal_approval_queue_requires_login_but_not_procurement_module(self):
        view = PurchaseRequisitionViewSet()
        view.action = 'pending_for_me'

        permissions = view.get_permissions()

        self.assertEqual(len(permissions), 1)
        self.assertIsInstance(permissions[0], IsAuthenticated)

    def test_dynamic_approval_action_requires_login_but_not_procurement_module(self):
        view = PurchaseRequisitionViewSet()
        view.action = 'process_dynamic_approval'

        permissions = view.get_permissions()

        self.assertEqual(len(permissions), 1)
        self.assertIsInstance(permissions[0], IsAuthenticated)
