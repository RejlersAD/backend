"""PR approval history cannot lock, authorize, or block the PO's own decision."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from apps.procurement.services.purchase_order_approvals import normalize_assignments
from apps.procurement.services.purchase_order_content import (
    commercial_edit_locked,
    is_requisition_approval_history,
    protect_purchase_order_content,
    purchase_order_content_fingerprint,
    purchase_order_content_issue,
)
from apps.procurement.services.purchase_order_lifecycle import validate_purchase_order_transition


class PurchaseOrderRequisitionHistoryGuardsTests(SimpleTestCase):
    SOURCES = ('purchase_requisition', 'signed_purchase_requisition_pdf')

    def history(self, source='purchase_requisition', **overrides):
        return {
            'stage': 'Recommendation approval', 'external': True,
            'source': source, 'source_pr_id': 'recorded-pr',
            'approver': 'Recommendation signer', 'status': 'Approved',
            **overrides,
        }

    def assignment(self, **overrides):
        return {
            'stage': 'Final Management Sign-off', 'level': 0,
            'user_id': 'po-signer', 'approver_email': 'signer@example.test',
            'status': 'Pending', **overrides,
        }

    def order(self, *rows):
        return SimpleNamespace(
            pk='order', status='draft', total_amount='100.00',
            approved_at=None, approved_date=None, approval_signature='',
            approval_log=list(rows),
        )

    def test_pr_history_keeps_unapproved_po_terms_editable(self):
        for source in self.SOURCES:
            with self.subTest(source=source):
                history = self.history(source, user_id='historical-pr-signer',
                                       content_fingerprint='not-a-po-fingerprint')
                order = self.order(history, self.assignment())
                self.assertFalse(commercial_edit_locked(order))
                self.assertEqual(purchase_order_content_issue(order), '')
                protect_purchase_order_content(order, {'total_amount': '125.00'})
                with self.assertRaises(ValidationError):
                    validate_purchase_order_transition(order, 'sent')

    def test_pr_history_alone_cannot_authorize_a_po_lifecycle_change(self):
        for source in self.SOURCES:
            for rows in ([], [self.history(source)]):
                with self.subTest(source=source, rows=rows):
                    order = self.order(*rows)
                    for target in ('sent', 'completed'):
                        with self.assertRaisesMessage(ValidationError, 'Complete a purchase order approval route'):
                            validate_purchase_order_transition(order, target)

    def test_completed_po_approval_is_not_stuck_on_pr_source_evidence(self):
        for source in self.SOURCES:
            with self.subTest(source=source):
                order = self.order(self.history(source), self.assignment(status='Approved'))
                order.approval_log[-1]['content_fingerprint'] = purchase_order_content_fingerprint(order)
                with patch('apps.procurement.services.purchase_order_lifecycle.PODocument.objects.filter') as documents:
                    validate_purchase_order_transition(order, 'sent')
                    # Completion also needs accepted receiving evidence. This
                    # test isolates PR/PO approval history from that separate guard.
                    with patch('apps.procurement.services.receiving.receiving_summary',
                               return_value={'status': 'complete'}) as receiving:
                        validate_purchase_order_transition(order, 'completed')
                    receiving.assert_called_once_with(order)
                documents.assert_not_called()
                self.assertTrue(commercial_edit_locked(order))
                with self.assertRaises(ValidationError):
                    protect_purchase_order_content(order, {'total_amount': '125.00'})
                order.total_amount = '125.00'
                self.assertTrue(purchase_order_content_issue(order))
                with self.assertRaises(ValidationError):
                    validate_purchase_order_transition(order, 'sent')

    def test_pr_history_does_not_override_pending_or_rejected_po_decisions(self):
        for status in ('Pending', 'Rejected'):
            with self.subTest(status=status):
                order = self.order(self.history(), self.assignment(status=status))
                with self.assertRaisesMessage(ValidationError, 'All purchase order approval stages'):
                    validate_purchase_order_transition(order, 'sent')

    def test_only_explicit_pr_history_is_excluded_from_po_controls(self):
        for overrides in ({'external': False}, {'external': 'true'},
                          {'source': 'purchase_order'}, {'source': 'unknown'}, {'source': {}}):
            with self.subTest(overrides=overrides):
                row = self.history(**overrides)
                self.assertFalse(is_requisition_approval_history(row))
                self.assertTrue(commercial_edit_locked(self.order(row)))
        assigned = self.assignment(source='purchase_requisition')
        self.assertFalse(is_requisition_approval_history(assigned))
        with self.assertRaises(ValidationError):
            validate_purchase_order_transition(self.order(assigned), 'sent')

    def test_pr_history_cannot_substitute_for_unverified_signed_po_source(self):
        source = {
            'stage': 'Signed PO document approval', 'external': True,
            'status': 'Approved', 'approver': 'PO source signer',
            'source': 'signed_purchase_order_pdf', 'signature_verified': False,
        }
        order = self.order(self.history(), source)
        self.assertTrue(commercial_edit_locked(order))
        with self.assertRaisesMessage(ValidationError, 'Verify the signed source document'):
            validate_purchase_order_transition(order, 'sent')

    def test_client_pr_markers_cannot_hide_pending_or_recorded_po_assignments(self):
        profile = SimpleNamespace(user=SimpleNamespace(email='signer@example.test'))
        for source in self.SOURCES:
            for saved_status in ('Pending', 'Approved'):
                with self.subTest(source=source, saved_status=saved_status):
                    original = self.assignment(status=saved_status)
                    order = self.order(original)
                    if saved_status == 'Approved':
                        original['content_fingerprint'] = purchase_order_content_fingerprint(order)
                    payload = {**deepcopy(original), 'external': True, 'source': source,
                               'source_pr_id': 'fake-pr', 'status': 'Approved',
                               'content_fingerprint': 'client-replacement'}
                    with patch('apps.procurement.services.purchase_order_approvals._active_profiles',
                               return_value={'po-signer': profile}), \
                            patch('apps.procurement.services.purchase_order_approvals.eligible_stage_assignee', return_value=True), \
                            patch('apps.procurement.services.purchase_order_approvals.employee_display_name', return_value='PO signer'):
                        normalized = normalize_assignments([payload], existing_log=[original], require_core=False)
                    row = normalized[0]
                    self.assertEqual(row['status'], saved_status)
                    for field in ('external', 'source', 'source_pr_id'):
                        self.assertNotIn(field, row)
                    self.assertFalse(is_requisition_approval_history(row))
                    order.approval_log = normalized
                    if saved_status == 'Pending':
                        self.assertNotIn('content_fingerprint', row)
                        with self.assertRaises(ValidationError):
                            validate_purchase_order_transition(order, 'sent')
                    else:
                        self.assertEqual(row['content_fingerprint'], original['content_fingerprint'])
                        self.assertTrue(commercial_edit_locked(order))

    def test_unassigned_client_history_is_discarded_by_assignment_normalization(self):
        with patch('apps.procurement.services.purchase_order_approvals._active_profiles', return_value={}):
            rows = normalize_assignments([self.history()], require_core=False)
        self.assertEqual(rows, [])
        with self.assertRaises(ValidationError):
            validate_purchase_order_transition(self.order(*rows), 'sent')
