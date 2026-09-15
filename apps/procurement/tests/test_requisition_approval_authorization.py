from types import SimpleNamespace

from django.test import SimpleTestCase
from rest_framework.exceptions import PermissionDenied

from apps.procurement.services.requisition_workflow import RequisitionWorkflowService


class PurchaseRequisitionApprovalAuthorizationTests(SimpleTestCase):
    def setUp(self):
        self.assigned_user = SimpleNamespace(id='assigned-user', is_superuser=False)
        self.other_user = SimpleNamespace(id='other-user', is_superuser=False)

    def test_selected_user_can_act(self):
        stage = {'role': 'Project Manager', 'user_id': self.assigned_user.id}

        RequisitionWorkflowService._enforce_assigned_approver(stage, self.assigned_user)

    def test_unselected_user_cannot_act(self):
        stage = {'role': 'Engineering Manager', 'user_id': self.assigned_user.id}

        with self.assertRaisesMessage(PermissionDenied, 'Only the assigned approver'):
            RequisitionWorkflowService._enforce_assigned_approver(stage, self.other_user)

    def test_legacy_approver_id_is_supported(self):
        stage = {'role': 'Procurement Manager', 'approver_id': self.assigned_user.id}

        RequisitionWorkflowService._enforce_assigned_approver(stage, self.assigned_user)

    def test_stage_without_assignee_is_rejected(self):
        stage = {'role': 'Project Manager'}

        with self.assertRaisesMessage(PermissionDenied, 'No approver is assigned'):
            RequisitionWorkflowService._enforce_assigned_approver(stage, self.other_user)

    def test_superuser_can_override_selected_approver(self):
        superuser = SimpleNamespace(id='admin-user', is_superuser=True)
        stage = {'role': 'Project Manager', 'user_id': self.assigned_user.id}

        RequisitionWorkflowService._enforce_assigned_approver(stage, superuser)
