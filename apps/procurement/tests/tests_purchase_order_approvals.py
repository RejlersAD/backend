from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from apps.procurement.services.purchase_order_approvals import (
    FINANCIAL_STAGE,
    TECHNICAL_STAGE,
    _entry_matches_user,
    normalize_assignments,
    record_decision,
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

    def test_migrated_assignment_uses_email_when_user_id_changed(self):
        actor = SimpleNamespace(id='production-id', email='firaol.akawak@rejlers.ae')
        entry = {
            'user_id': 'preproduction-id',
            'approver_email': 'FIRAOL.AKAWAK@rejlers.ae',
        }

        self.assertTrue(_entry_matches_user(entry, actor))

    @patch('apps.procurement.models.PurchaseOrder.objects.select_for_update')
    def test_approval_records_full_timestamp(self, select_for_update):
        actor = SimpleNamespace(id='jarmo-id', email='jarmo@example.com', get_full_name=lambda: 'Jarmo Suominen')
        locked = SimpleNamespace(
            id='po-id',
            approval_log=[{
                'stage': 'Final Management Sign-off',
                'user_id': 'jarmo-id',
                'status': 'Pending',
            }],
            approved_by=None,
            approved_by_name='',
            approved_date=None,
            approved_at=None,
            save=MagicMock(),
        )
        select_for_update.return_value.select_related.return_value.get.return_value = locked

        updated, entry = record_decision.__wrapped__(SimpleNamespace(pk='po-id'), actor, 'approve')

        self.assertEqual(entry['status'], 'Approved')
        self.assertIn('T', entry['approved_at'])
        self.assertIn('T', entry['decided_at'])
        self.assertIsNotNone(updated.approved_at)
