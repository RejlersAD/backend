from datetime import date
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.notifications.teams import (
    build_approval_assignment_payload,
    queue_approval_assignment,
    send_teams_approval_assignment,
)


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
            'description': 'Engineering software renewal',
            'project_name': 'Onboarding Enhancement phase 2',
            'project_id': 'RAD-PRJ-2026-0042',
            'po_number': 'PO-2026-0042',
            'service': 'Engineering software renewal',
            'vendor': 'Engineering Supplier',
            'value': 'AED 12,500.00',
            'currency': 'AED',
            'approval_level': 0,
            'due_date': date(2026, 9, 5),
        })

        self.assertEqual(payload['recipient_email'], 'approver@rejlers.ae')
        self.assertEqual(payload['submitted_by'], 'Requester Name')
        self.assertNotIn('due_date', payload)
        self.assertNotIn('Due Date', json.dumps(payload))
        self.assertEqual(payload['action_url'], 'https://radai.ae/approvals?tab=procurement')
        self.assertIn('New approval request assigned', payload['message'])
        self.assertIn('Service: Engineering software renewal', payload['message'])
        self.assertIn('Description: Engineering software renewal', payload['message'])
        self.assertIn('Project Name: Onboarding Enhancement phase 2', payload['message'])
        self.assertIn('Project Code: RAD-PRJ-2026-0042', payload['message'])
        for key, value in {
            'PO Number': 'PO-2026-0042',
            'Vendor': 'Engineering Supplier',
            'Value': 'AED 12,500.00',
            'Approval Level': 'Level 0',
        }.items():
            self.assertIn(f'{key}: {value}', payload['message'])
        facts = {fact['title']: fact['value'] for fact in payload['attachments'][0]['content']['body'][1]['facts']}
        self.assertEqual(facts['PO Number'], payload['po_number'])
        self.assertEqual(facts['Service'], payload['service'])
        self.assertEqual(facts['Vendor'], payload['vendor'])
        self.assertEqual(facts['Value'], payload['value'])
        self.assertEqual(facts['Approval Level'], 'Level 0')
        self.assertEqual(payload['type'], 'message')
        self.assertEqual(
            payload['attachments'][0]['contentType'],
            'application/vnd.microsoft.card.adaptive',
        )
        self.assertEqual(
            payload['attachments'][0]['content']['actions'][0]['url'],
            payload['action_url'],
        )

    @override_settings(FRONTEND_URL='https://radai.ae')
    def test_payload_supports_purchase_order_created_event(self):
        payload = build_approval_assignment_payload(self._notification(), {
            'event_type': 'purchase_order_created',
            'title': 'New purchase order created',
            'request_name': 'Purchase Order RAD-PRJ-PUR-0117_2026',
        })

        self.assertEqual(payload['event_type'], 'purchase_order_created')
        self.assertEqual(payload['title'], 'New purchase order created')
        self.assertIn('New purchase order created', payload['message'])
        self.assertEqual(
            payload['attachments'][0]['content']['body'][0]['text'],
            'New purchase order created',
        )
        facts = payload['attachments'][0]['content']['body'][1]['facts']
        self.assertNotIn('Approval Level', [fact['title'] for fact in facts])

    def test_missing_po_and_optional_details_have_explicit_fallbacks(self):
        payload = build_approval_assignment_payload(self._notification())
        self.assertEqual(payload['po_number'], 'Not issued')
        self.assertEqual(payload['vendor'], 'Not specified')
        self.assertEqual(payload['value'], 'Not specified')
        self.assertIsNone(payload['approval_level'])

    @override_settings(TEAMS_APPROVAL_WEBHOOK_URL='https://flow.example.test/trigger')
    @patch('apps.notifications.teams.send_teams_approval_assignment.delay')
    def test_enabled_delivery_is_queued(self, delay):
        notification = self._notification()

        self.assertTrue(queue_approval_assignment(notification, {'request_name': 'Request'}))
        delay.assert_called_once_with(notification.pk, {
            'request_name': 'Request',
        })

    @override_settings(TEAMS_APPROVAL_WEBHOOK_URL='https://flow.example.test/trigger')
    @patch('apps.notifications.teams.send_teams_approval_assignment.delay')
    def test_legacy_due_date_is_removed_before_queue_serialization(self, delay):
        context = {'request_name': 'Request', 'due_date': date(2026, 9, 5)}
        self.assertTrue(queue_approval_assignment(self._notification(), context))
        self.assertEqual(delay.call_args.args[1], {'request_name': 'Request'})
        self.assertIn('due_date', context)

    @override_settings(TEAMS_APPROVAL_WEBHOOK_URL='')
    @patch('apps.notifications.teams.send_teams_approval_assignment.delay')
    def test_disabled_delivery_does_not_queue(self, delay):
        self.assertFalse(queue_approval_assignment(self._notification()))
        delay.assert_not_called()

    @override_settings(TEAMS_APPROVAL_WEBHOOK_URL='https://flow.example.test/trigger')
    @patch('apps.notifications.teams.NotificationLog.objects.create')
    @patch('apps.notifications.teams.requests.post')
    @patch('apps.notifications.teams.Notification.objects.select_related')
    def test_delivery_uses_newest_row_when_legacy_ids_are_duplicated(
        self,
        select_related,
        post,
        _create_log,
    ):
        queryset = select_related.return_value.filter.return_value.order_by.return_value
        queryset.first.return_value = self._notification()
        post.return_value.raise_for_status.return_value = None

        result = send_teams_approval_assignment.run('notification-id', {})

        self.assertEqual(result, {'status': 'sent'})
        select_related.return_value.filter.assert_called_once_with(pk='notification-id')
        select_related.return_value.filter.return_value.order_by.assert_called_once_with(
            '-created_at'
        )
