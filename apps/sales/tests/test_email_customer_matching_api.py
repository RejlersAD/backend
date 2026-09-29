"""Current source/client scope and read-only enrichment through guarded routes."""

from copy import deepcopy
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.rbac.models import Permission, UserPermissionOverride
from apps.sales.microsoft_graph import SalesMicrosoftGraphService
from apps.sales.models import Client, Deal, OpportunityAuditEvent, SalesEmailIntake, SalesMailboxConnection
from apps.sales.serializers import SalesEmailIntakeSerializer

from .test_email_customer_matching import CustomerMatchingFixtures, client_queries
from .test_mailbox_browsing import graph_response
from .test_mailbox_capture import CAPTURE_MESSAGE


@override_settings(ROOT_URLCONF='apps.sales.tests.test_mailbox_capture',
                   SALES_MICROSOFT_GRAPH_BASE_URL='https://graph.microsoft.com/v1.0')
class EmailCustomerMatchingAPITests(CustomerMatchingFixtures, TestCase):
    def setUp(self):
        super().setUp()
        self.mailbox = SalesMailboxConnection.objects.create(
            name='Synthetic matching mailbox', created_by=self.owner,
            mailbox_address='sales@example.test', tenant_id='synthetic-tenant',
            client_id='synthetic-client', auth_mode='application', enabled=False,
        )
        self.intake = SalesEmailIntake.objects.create(
            mailbox_connection=self.mailbox, captured_by=self.owner,
            source_mailbox_address=self.mailbox.mailbox_address, source_tenant_id=self.mailbox.tenant_id,
            source_message_id='synthetic-matching-message', conversation_id='synthetic-matching-chain',
            subject='RFQ-880: Engineering study', sender_name='Tender Desk', sender_email='buyer@example.test',
            received_at=timezone.now(), body_preview='Customer: Northbridge Utilities Ltd\nPlease submit your quotation.',
            status='under_review', reviewed_by=self.owner, reviewed_at=timezone.now(),
            resolution_note='Synthetic existing review must remain unchanged.',
        )
        self.url = f'/api/v1/sales/email-intakes/{self.intake.pk}/'
        self.live_url = f'/api/v1/sales/mailbox-connections/{self.mailbox.pk}/message/'
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def test_saved_get_preserves_source_review_and_business_state_without_network(self):
        account = self.account()
        before = SalesEmailIntake.objects.values().get(pk=self.intake.pk)
        client_before = Client.objects.values().get(pk=account.pk)
        with patch('requests.request') as network, patch('requests.post') as post:
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        proposal = response.data['extracted_information']['customer_match']
        self.assertEqual(proposal['status'], 'matched')
        self.assertTrue(proposal['needs_review'])
        self.assertEqual(proposal['candidates'][0]['id'], str(account.pk))
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('private', response['Cache-Control'])
        self.assertEqual(SalesEmailIntake.objects.values().get(pk=self.intake.pk), before)
        self.assertEqual(Client.objects.values().get(pk=account.pk), client_before)
        self.assertEqual(Client.objects.count(), 1)
        self.assertFalse(Deal.objects.exists())
        self.assertFalse(OpportunityAuditEvent.objects.exists())
        network.assert_not_called()
        post.assert_not_called()

    def test_saved_list_scans_authorized_directory_once_for_all_source_names(self):
        self.account()
        self.account('Second Utilities')
        SalesEmailIntake.objects.create(
            source_message_id='legacy-second', subject='RFQ-881', sender_email='buyer@example.test',
            received_at=timezone.now(), body_preview='Customer: Second Utilities',
        )
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get('/api/v1/sales/email-intakes/')
        self.assertEqual(response.status_code, 200)
        rows = response.data['results'] if isinstance(response.data, dict) else response.data
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row['extracted_information']['customer_match']['status'] == 'matched' for row in rows))
        self.assertEqual(len(client_queries(queries)), 1)

    def test_other_mailbox_owner_is_denied_before_customer_lookup(self):
        self.account()
        self.client.force_authenticate(self.other)
        with patch('apps.sales.email_customer_matching.visible_email_clients') as directory:
            saved = self.client.get(self.url)
            live = self.client.get(self.live_url, {'message_id': self.intake.source_message_id})
        self.assertEqual(saved.status_code, 404)
        self.assertEqual(live.status_code, 404)
        directory.assert_not_called()

    def test_intake_read_denial_prevents_saved_and_live_lookup(self):
        permission = Permission.objects.get(module__code='sales_email_intake', action='read', is_active=True)
        UserPermissionOverride.objects.create(
            user_profile=self.owner.rbac_profile, permission=permission, allowed=False,
        )
        with patch('apps.sales.email_customer_matching.visible_email_clients') as directory:
            saved = self.client.get(self.url)
            live = self.client.get(self.live_url, {'message_id': self.intake.source_message_id})
        self.assertEqual(saved.status_code, 403)
        self.assertEqual(live.status_code, 403)
        directory.assert_not_called()

    def test_hidden_canonical_duplicate_does_not_change_saved_match(self):
        visible = self.account()
        hidden = self.account(account_manager=self.outsider)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        proposal = response.data['extracted_information']['customer_match']
        self.assertEqual(proposal['status'], 'matched')
        self.assertEqual([item['id'] for item in proposal['candidates']], [str(visible.pk)])
        self.assertNotIn(str(hidden.pk), str(proposal))

    def test_client_read_revocation_after_prior_match_returns_denied_without_existence_query(self):
        account = self.account()
        self.assertEqual(self.client.get(self.url).data['extracted_information']['customer_match']['status'], 'matched')
        self.deny()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['extracted_information']['customer_match']['status'], 'denied')
        self.assertNotIn(str(account.pk), str(response.data['extracted_information']['customer_match']))
        self.assertEqual(client_queries(queries), [])
        self.assertFalse(response.data['can_create_client'])

    def test_matching_and_no_match_do_not_auto_link_or_create(self):
        matched = self.account()
        self.client.get(self.url)
        matched.delete()
        response = self.client.get(self.url)
        self.assertEqual(response.data['extracted_information']['customer_match']['status'], 'no_match')
        self.assertEqual(Client.objects.count(), 0)
        self.assertFalse(Deal.objects.exists())
        self.intake.refresh_from_db()
        self.assertIsNone(self.intake.opportunity_id)

    def test_live_and_saved_enrichments_match_and_graph_call_count_is_unchanged(self):
        self.account()
        message = {**deepcopy(CAPTURE_MESSAGE), 'id': self.intake.source_message_id,
                   'subject': self.intake.subject, 'conversationId': self.intake.conversation_id,
                   'body': {'contentType': 'text', 'content': self.intake.body_preview}}
        with patch.object(SalesMicrosoftGraphService, 'token', return_value='synthetic-matching-token'), \
                patch('apps.sales.microsoft_graph.requests.request', side_effect=[graph_response(message), graph_response({'value': [message]})]) as network:
            live = self.client.get(self.live_url, {'message_id': message['id']})
        saved = self.client.get(self.url)
        self.assertEqual(live.status_code, 200)
        self.assertEqual(live.data['extracted_information']['customer_match'], saved.data['extracted_information']['customer_match'])
        self.assertEqual(network.call_count, 2)
        self.assertTrue(all(call.args[0] == 'GET' for call in network.call_args_list))
        self.assertTrue(live.data['source_token'])
        self.assertIn('no-store', live['Cache-Control'])

    def test_manual_client_creation_capability_requires_all_existing_permissions(self):
        self.assertTrue(self.client.get(self.url).data['can_create_client'])
        self.deny('create')
        response = self.client.get(self.url)
        self.assertFalse(response.data['can_create_client'])
        self.assertTrue(response.data['can_create_opportunity'])

    def test_client_creation_capability_is_false_without_request_or_after_resolution(self):
        output = SalesEmailIntakeSerializer(self.intake).data
        self.assertFalse(output['can_create_client'])
        self.assertEqual(output['extracted_information']['customer_match']['status'], 'unavailable')
        self.intake.status = 'rejected'
        self.intake.save(update_fields=['status'])
        self.assertFalse(self.client.get(self.url).data['can_create_client'])
