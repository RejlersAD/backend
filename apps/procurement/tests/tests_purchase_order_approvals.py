from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from apps.procurement.services.purchase_order_approvals import (
    FINANCIAL_STAGE,
    TECHNICAL_STAGE,
    normalize_assignments,
)


class EmptyRelations:
    def all(self):
        return []


class PurchaseOrderApprovalAssignmentTests(SimpleTestCase):
    def _profile(self, user_id, name, email, department):
        user = SimpleNamespace(
            id=user_id,
            email=email,
            get_full_name=lambda: name,
        )
        return SimpleNamespace(user_id=user_id, user=user, department=department, roles=EmptyRelations())

    def setUp(self):
        self.technical = self._profile('technical-id', 'Technical Approver', 'technical@example.com', 'Engineering')
        self.finance = self._profile('finance-id', 'Finance Approver', 'finance@example.com', 'Finance')
        self.non_finance = self._profile('other-id', 'Other Approver', 'other@example.com', 'Engineering')

    @patch('apps.procurement.services.purchase_order_approvals._active_profiles')
    def test_assignments_are_resolved_from_active_employees(self, active_profiles):
        active_profiles.return_value = {
            'technical-id': self.technical,
            'finance-id': self.finance,
        }
        result = normalize_assignments([
            {'stage': TECHNICAL_STAGE, 'user_id': 'technical-id'},
            {'stage': FINANCIAL_STAGE, 'user_id': 'finance-id'},
        ])

        self.assertEqual(result[0]['approver'], 'Technical Approver')
        self.assertEqual(result[0]['status'], 'Pending')
        self.assertEqual(result[1]['approver_email'], 'finance@example.com')

    @patch('apps.procurement.services.purchase_order_approvals._active_profiles')
    def test_financial_stage_rejects_non_finance_employee(self, active_profiles):
        active_profiles.return_value = {
            'technical-id': self.technical,
            'other-id': self.non_finance,
        }
        with self.assertRaisesMessage(ValidationError, 'active Finance employee'):
            normalize_assignments([
                {'stage': TECHNICAL_STAGE, 'user_id': 'technical-id'},
                {'stage': FINANCIAL_STAGE, 'user_id': 'other-id'},
            ])

    def test_draft_may_keep_approvals_unassigned(self):
        result = normalize_assignments([
            {'stage': TECHNICAL_STAGE, 'user_id': ''},
            {'stage': FINANCIAL_STAGE, 'user_id': ''},
        ], require_core=False)

        self.assertEqual(result, [])
