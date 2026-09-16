"""PO approval notifications follow the live level and assignment identity."""

from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.exceptions import PermissionDenied

from apps.notifications.models import Notification
from apps.procurement.models import PurchaseOrder, Vendor
from apps.rbac.models import Organization, UserProfile
from .approval_fixtures import grant_approval, set_position
from apps.procurement.services.purchase_order_approvals import (
    normalize_assignments,
    notify_assigned_approvers,
    notify_purchase_order_created,
    pending_entries_for,
    record_decision,
)


@override_settings(TEAMS_APPROVAL_WEBHOOK_URL='', WEB_PUSH_VAPID_PRIVATE_KEY='')
class PurchaseOrderNotificationSequenceTests(TestCase):
    def setUp(self):
        organization = Organization.objects.create(name='PO approval sequence', code='PO-SEQUENCE')
        self.users = {
            key: get_user_model().objects.create_user(
                username=f'po-sequence-{key}', email=f'{key}@example.test',
                first_name=key,
            )
            for key in ('zero_a', 'zero_b', 'one', 'two', 'ceo', 'buyer')
        }
        for user in self.users.values():
            UserProfile.objects.get_or_create(user=user, defaults={'organization': organization})
            grant_approval(user)
            set_position(user, 'CEO' if user == self.users['ceo'] else 'Engineer')
        vendor = Vendor.objects.create(vendor_code='PO-SEQUENCE-VENDOR', name='Sequence supplier')
        self.order = PurchaseOrder.objects.create(
            po_number='PO-SEQUENCE', vendor=vendor, title='Engineering services',
            total_amount='1500.00', currency='AED', created_by=self.users['buyer'],
            approval_log=[
                self.entry('ceo', 3), self.entry('zero_a', 0),
                self.entry('one', 1), self.entry('zero_b', 0), self.entry('two', 2),
            ],
        )
        self.deliveries = []
        patcher = patch(
            'apps.notifications.services.NotificationService.create_notification',
            side_effect=self.capture_notification,
        )
        self.create_notification = patcher.start()
        self.addCleanup(patcher.stop)

    def entry(self, key, level, stage=None):
        user = self.users[key]
        return {
            'user_id': str(user.pk), 'approver_email': user.email,
            'stage': stage or f'Level {level} - {key}', 'level': level,
            'status': 'Pending',
            'business_position': 'ceo' if key == 'ceo' else 'engineer',
        }

    def capture_notification(self, **kwargs):
        self.deliveries.append(kwargs)
        return Notification.objects.create(**{
            key: kwargs[key] for key in ('recipient', 'sender', 'title', 'message', 'metadata')
        })

    def approve(self, key):
        with self.captureOnCommitCallbacks(execute=True):
            self.order, _ = record_decision(self.order, self.users[key], 'approve')

    def test_parallel_level_zero_then_one_two_and_ceo_follow_numeric_order(self):
        notify_assigned_approvers(self.order)
        self.assertEqual(
            {item['recipient'].pk for item in self.deliveries},
            {self.users['zero_a'].pk, self.users['zero_b'].pk},
        )
        self.assertTrue(all(item['teams_context']['approval_level'] == 0 for item in self.deliveries))
        notify_assigned_approvers(self.order)
        self.assertEqual(len(self.deliveries), 2)

        with self.assertRaises(PermissionDenied):
            self.approve('ceo')
        self.approve('zero_a')
        self.assertEqual(len(self.deliveries), 2)
        self.assertEqual(pending_entries_for(self.users['one'], [self.order]), [])
        self.approve('zero_b')
        self.assertEqual(self.deliveries[-1]['recipient'], self.users['one'])
        self.approve('one')
        self.assertEqual(self.deliveries[-1]['recipient'], self.users['two'])
        self.approve('two')
        self.assertEqual(self.deliveries[-1]['recipient'], self.users['ceo'])
        self.approve('ceo')

        self.assertEqual([item['metadata']['approval_level'] for item in self.deliveries], [0, 0, 1, 2, 3])
        self.assertTrue(all(item['metadata']['event_type'] == 'approval_assignment' for item in self.deliveries))
        self.assertTrue(all(item['send_teams'] for item in self.deliveries))
        self.assertEqual(self.order.approved_by, self.users['ceo'])
        self.assertIsNotNone(self.order.approved_at)

    def test_rejection_stops_peer_and_later_actions_and_notifications(self):
        notify_assigned_approvers(self.order)
        with self.captureOnCommitCallbacks(execute=True):
            self.order, _ = record_decision(self.order, self.users['zero_a'], 'reject')
        for key in ('zero_b', 'one', 'two', 'ceo'):
            self.assertEqual(pending_entries_for(self.users[key], [self.order]), [])
            with self.assertRaises(PermissionDenied):
                self.approve(key)
        notify_assigned_approvers(self.order)
        self.assertEqual(len(self.deliveries), 2)

    def test_created_fyi_excludes_all_approvers_but_keeps_other_buyers(self):
        self.order.contact_persons = {
            'buyer_references': [
                {'email': self.users[key].email}
                for key in ('ceo', 'two', 'one', 'zero_a', 'buyer', 'buyer')
            ],
        }
        self.order.buyer_reference_email = self.users['buyer'].email
        with patch('apps.procurement.services.purchase_order_approvals._jarmo_user') as ceo_lookup:
            notify_purchase_order_created(self.order)
            notify_purchase_order_created(self.order)
        ceo_lookup.assert_not_called()
        self.assertEqual(len(self.deliveries), 1)
        self.assertEqual(self.deliveries[0]['recipient'], self.users['buyer'])
        self.assertEqual(self.deliveries[0]['metadata']['event_type'], 'po_created')

    def test_legacy_actionable_notification_still_prevents_ordinary_resend(self):
        self.order.approval_log = [self.entry('zero_a', 0)]
        self.order.save(update_fields=['approval_log'])
        Notification.objects.create(
            recipient=self.users['zero_a'], title='Existing approval', message='Existing approval',
            metadata={
                'po_id': str(self.order.pk), 'approval_stage': 'Level 0 - zero_a',
                'approval_level': 0, 'requires_action': True,
            },
        )
        notify_assigned_approvers(self.order)
        self.assertEqual(self.deliveries, [])

    def test_same_person_and_stage_at_different_levels_receives_each_turn(self):
        self.order.approval_log = [
            self.entry('zero_a', 0, 'Review'), self.entry('zero_a', 1, 'Review'),
        ]
        self.order.save(update_fields=['approval_log'])
        notify_assigned_approvers(self.order)
        self.approve('zero_a')
        self.assertEqual([item['metadata']['approval_level'] for item in self.deliveries], [0, 1])
        notify_assigned_approvers(self.order)
        self.assertEqual(len(self.deliveries), 2)

    def test_reassignment_back_to_original_employee_gets_a_fresh_alert(self):
        profiles = {
            str(user.pk): SimpleNamespace(user=user, department='Engineering')
            for user in self.users.values()
        }
        workflow = []
        assignment_ids = []
        for key in ('zero_a', 'zero_a', 'zero_b', 'zero_a'):
            selected = str(self.users[key].pk)
            with patch(
                'apps.procurement.services.purchase_order_approvals._active_profiles',
                return_value={selected: profiles[selected]},
            ):
                workflow = normalize_assignments(
                    [self.entry(key, 0, 'Review')], existing_log=workflow, require_core=False,
                )
            assignment_ids.append(workflow[0]['assignment_id'])
            self.order.approval_log = workflow
            self.order.save(update_fields=['approval_log'])
            notify_assigned_approvers(self.order)

        self.assertEqual(assignment_ids[0], assignment_ids[1])
        self.assertEqual(len(set(assignment_ids)), 3)
        self.assertEqual([item['recipient'] for item in self.deliveries], [
            self.users['zero_a'], self.users['zero_b'], self.users['zero_a'],
        ])

    def test_moving_assignment_to_another_level_resets_decision_and_identity(self):
        user = self.users['zero_a']
        previous = self.entry('zero_a', 0, 'Review')
        previous.update(status='Approved', assignment_id='previous-assignment')
        with patch(
            'apps.procurement.services.purchase_order_approvals._active_profiles',
            return_value={str(user.pk): SimpleNamespace(user=user, department='Engineering')},
        ):
            normalized = normalize_assignments(
                [self.entry('zero_a', 1, 'Review')], existing_log=[previous], require_core=False,
            )
        self.assertEqual(normalized[0]['status'], 'Pending')
        self.assertNotEqual(normalized[0]['assignment_id'], 'previous-assignment')
