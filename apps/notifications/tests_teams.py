from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.notifications.teams import build_approval_assignment_payload, queue_approval_assignment


class TeamsApprovalNotificationTests(SimpleTestCase):
    def _notification(self):
        return SimpleNamespace(
            pk='notification-id',
            title='PR requires approval',
            recipient=SimpleNamespace(
                email='approver@rejlers.ae',
                username='approver',
                get_full_name=lambda: 'Approver Name',
            ),
            sender=SimpleNamespace(
                email='requester@rejlers.ae',
                username='requester',
                get_full_name=lambda: 'Requester Name',
            ),
            action_url='/approvals?tab=procurement',
            action_label='Open Request',
        )

    @override_settings(FRONTEND_URL='https://radai.ae')
    def test_payload_contains_private_recipient_and_open_request_link(self):
        payload = build_approval_assignment_payload(self._notification(), {
            'request_name': 'Purchase Requisition RAD-PRJ-PR-0001_2026',
            'due_date': date(2026, 9, 5),
        })

        self.assertEqual(payload['recipient_email'], 'approver@rejlers.ae')
        self.assertEqual(payload['submitted_by'], 'Requester Name')
        self.assertEqual(payload['due_date'], '05-Sep-2026')
        self.assertEqual(payload['action_url'], 'https://radai.ae/approvals?tab=procurement')
        self.assertIn('New approval request assigned', payload['message'])

    @override_settings(TEAMS_APPROVAL_WEBHOOK_URL='https://flow.example.test/trigger')
    @patch('apps.notifications.teams.send_teams_approval_assignment.delay')
    def test_enabled_delivery_is_queued(self, delay):
        notification = self._notification()

        self.assertTrue(queue_approval_assignment(notification, {'request_name': 'Request'}))
        delay.assert_called_once_with(notification.pk, {
            'request_name': 'Request',
            'due_date': 'Not specified',
        })

    @override_settings(TEAMS_APPROVAL_WEBHOOK_URL='')
    @patch('apps.notifications.teams.send_teams_approval_assignment.delay')
    def test_disabled_delivery_does_not_queue(self, delay):
        self.assertFalse(queue_approval_assignment(self._notification()))
        delay.assert_not_called()
