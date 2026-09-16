from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.notifications.teams import build_approval_assignment_payload
from apps.procurement.services.notification_context import (
    purchase_order_teams_context,
    requisition_teams_context,
)


class ProcurementNotificationContextTests(SimpleTestCase):
    def test_po_uses_canonical_project_vendor_and_saved_total(self):
        order = SimpleNamespace(
            po_number='PO-42',
            enterprise_project=SimpleNamespace(name='Canonical Project', code='PROJECT-42'),
            project=SimpleNamespace(project_name='Old project name', project_number='OLD'),
            title='Engineering services',
            scope_of_services='Design review and verification',
            vendor=SimpleNamespace(name='Supplier LLC'),
            total_amount=Decimal('10500.00'),
            tax_amount=Decimal('500.00'),
            currency='AED',
        )
        context = purchase_order_teams_context(order, approval_level=0)
        self.assertEqual(context['po_number'], 'PO-42')
        self.assertEqual(context['project_name'], 'Canonical Project')
        self.assertEqual(context['project_id'], 'PROJECT-42')
        self.assertEqual(context['service'], 'Engineering services')
        self.assertEqual(context['description'], 'Design review and verification')
        self.assertEqual(context['vendor'], 'Supplier LLC')
        self.assertEqual(context['value'], 'AED 10,500.00')
        self.assertEqual(context['approval_level'], 0)
        self.assertNotIn('due_date', context)

        notification = SimpleNamespace(
            pk=1, title='PO approval', recipient=SimpleNamespace(email='approver@example.test',
                get_full_name=lambda: 'Approver'), sender=None,
            action_url='/procurement/orders/42', action_label='Open Request',
        )
        payload = build_approval_assignment_payload(notification, context)
        self.assertIn('PO Number: PO-42', payload['message'])
        self.assertIn('Value: AED 10,500.00', payload['message'])
        self.assertNotIn('Due Date', payload['message'])

    def test_po_falls_back_to_legacy_project_and_preserves_zero_value(self):
        order = SimpleNamespace(po_number='PO-0', currency='USD', total_amount=Decimal('0'),
                                project=SimpleNamespace(project_name='Legacy', project_number='LEGACY-1'))
        context = purchase_order_teams_context(order)
        self.assertEqual(context['project_name'], 'Legacy')
        self.assertEqual(context['project_id'], 'LEGACY-1')
        self.assertEqual(context['value'], 'USD 0.00')

    def test_pr_uses_latest_linked_po_and_vendor_master(self):
        orders = [
            SimpleNamespace(po_number='OLD-PO', created_at=datetime(2025, 1, 1, tzinfo=timezone.utc)),
            SimpleNamespace(po_number='NEW-PO', created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        ]
        pr = SimpleNamespace(pr_number='PR-1', purchase_orders=SimpleNamespace(all=lambda: orders),
            po_number_reference='STALE-REFERENCE', vendor=SimpleNamespace(name='Master Vendor'),
            supplier_name='Old supplier', product_service='Consultancy',
            total_price=Decimal('42.50'), currency='EUR')
        context = requisition_teams_context(pr)
        self.assertEqual(context['po_number'], 'NEW-PO')
        self.assertEqual(context['vendor'], 'Master Vendor')
        self.assertEqual(context['service'], 'Consultancy')
        self.assertEqual(context['value'], 'EUR 42.50')

    def test_pr_preserves_multiple_project_details_and_manual_po_reference(self):
        pr = SimpleNamespace(pr_number='PR-MULTI', po_number_reference='MANUAL-PO',
            supplier_name='Legacy Supplier', project_details=[
                {'project_name': 'Alpha', 'project_number': 'A-1'},
                {'project_name': 'Beta', 'project_code': 'B-2'},
            ])
        context = requisition_teams_context(pr)
        self.assertEqual(context['project_name'], 'Alpha, Beta')
        self.assertEqual(context['project_id'], 'A-1, B-2')
        self.assertEqual(context['po_number'], 'MANUAL-PO')
        self.assertEqual(context['vendor'], 'Legacy Supplier')
        self.assertEqual(context['value'], 'Not specified')

    def test_unissued_po_is_explicit_and_missing_values_are_not_invented(self):
        context = requisition_teams_context(SimpleNamespace(pr_number='PR-DRAFT'))
        self.assertEqual(context['po_number'], 'Not issued')
        self.assertEqual(context['value'], 'Not specified')
        self.assertEqual(context['project_id'], 'Not specified')
