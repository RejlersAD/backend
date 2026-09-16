from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.procurement.services.approval_integrity import (
    purchase_order_signature_issue,
    stage_signature_issue,
)


class ApprovalSignatureIntegrityTests(SimpleTestCase):
    def test_different_signer_cannot_be_attributed_to_assigned_name(self):
        self.assertTrue(stage_signature_issue({'user_id': '1', 'approved_by_id': '2'}))

    def test_signature_owner_must_match_actor_and_assignee(self):
        self.assertTrue(stage_signature_issue({
            'user_id': '1', 'approved_by_id': '1', 'signature_user_id': '2',
        }))

    def test_email_authority_preserves_valid_migrated_identity(self):
        self.assertFalse(stage_signature_issue({
            'user_id': 'old-id', 'user_email': 'assigned@example.test',
            'approved_by_id': 'new-id', 'approved_by_email': 'ASSIGNED@example.test',
            'signature_user_id': 'new-id', 'signature_user_email': 'assigned@example.test',
        }))

    def test_historical_source_evidence_is_not_relabelled(self):
        for source in ({'external': True}, {'evidence_document_id': 'source-document'}):
            self.assertFalse(stage_signature_issue({**source, 'user_id': '1', 'approved_by_id': '2'}))
        self.assertFalse(stage_signature_issue({'user_id': '1', 'signature': 'legacy-without-actor'}))

    def test_final_po_signature_requires_completed_internal_sequence(self):
        order = SimpleNamespace(approval_log=[
            {'user_id': '1', 'status': 'pending'},
            {'user_id': '2', 'status': 'Approved', 'signature': 'final'},
        ], approval_signature='final', approved_by_id='2')
        self.assertIn('incomplete', purchase_order_signature_issue(order))

    def test_final_po_signature_must_belong_to_recorded_final_actor(self):
        order = SimpleNamespace(approval_log=[
            {'user_id': '1', 'approved_by_id': '1', 'status': 'Approved', 'signature': 'first'},
            {'user_id': '2', 'approved_by_id': '2', 'status': 'Approved', 'signature': 'last'},
        ], approval_signature='first', approved_by_id='2')
        self.assertIn('does not match', purchase_order_signature_issue(order))
        order.approval_signature = 'last'
        self.assertFalse(purchase_order_signature_issue(order))

    def test_unordered_po_rows_still_require_final_level_actor(self):
        order = SimpleNamespace(approval_log=[
            {'level': 5, 'user_id': 'ceo', 'approved_by_id': 'ceo', 'status': 'Approved', 'signature': 'ceo-signature'},
            {'level': 0, 'user_id': 'procurement', 'approved_by_id': 'procurement', 'status': 'Approved', 'signature': 'procurement-signature'},
        ], approval_signature='ceo-signature', approved_by_id='ceo')
        self.assertFalse(purchase_order_signature_issue(order))
        order.approved_by_id = 'procurement'
        order.approval_signature = 'procurement-signature'
        self.assertIn('does not match', purchase_order_signature_issue(order))

    def test_final_internal_signature_without_actor_identity_needs_review(self):
        order = SimpleNamespace(approval_log=[
            {'user_id': '1', 'status': 'Approved', 'signature': 'saved'},
        ], approval_signature='saved', approved_by_id=None)
        self.assertIn('no recorded signer', purchase_order_signature_issue(order))
