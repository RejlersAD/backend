import os
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.sales.microsoft_graph import SalesMicrosoftGraphService
from apps.sales.models import SalesMailboxConnection


class SalesMicrosoftGraphServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='graph-admin',
            email='graph-admin@example.com',
            password='test',
        )
        self.connection = SalesMailboxConnection.objects.create(
            tenant_id='tenant-id',
            client_id='client-id',
            mailbox_address='sales.intake@example.com',
            auth_mode='application',
            enabled=True,
            created_by=self.user,
            updated_by=self.user,
        )

    @patch.dict(os.environ, {'RADAI_SALES_GRAPH_CLIENT_SECRET': 'test-secret'})
    @patch('apps.sales.microsoft_graph.requests.request')
    @patch('apps.sales.microsoft_graph.requests.post')
    def test_health_check_authenticates_and_reads_inbox(self, token_post, graph_request):
        token_response = Mock(ok=True)
        token_response.json.return_value = {'access_token': 'access-token'}
        token_post.return_value = token_response

        folder_response = Mock(ok=True, content=b'{}')
        folder_response.json.return_value = {
            'id': 'inbox-id',
            'displayName': 'Inbox',
            'totalItemCount': 42,
            'unreadItemCount': 7,
        }
        graph_request.return_value = folder_response

        result = SalesMicrosoftGraphService(self.connection).health_check()

        self.assertTrue(result['connected'])
        self.assertEqual(result['total_item_count'], 42)
        self.assertEqual(result['unread_item_count'], 7)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.last_status, 'connected')
        self.assertEqual(self.connection.last_error, '')
        requested_url = graph_request.call_args.args[1]
        self.assertIn('sales.intake%40example.com/mailFolders/inbox', requested_url)
        self.assertNotIn('test-secret', str(graph_request.call_args))

    @patch.dict(os.environ, {}, clear=True)
    def test_health_check_reports_missing_environment_secret(self):
        result = SalesMicrosoftGraphService(self.connection).health_check()

        self.assertFalse(result['connected'])
        self.assertIn('RADAI_SALES_GRAPH_CLIENT_SECRET', result['error'])
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.last_status, 'error')
        self.assertIsNotNone(self.connection.last_health_check_at)

    @override_settings(SALES_GRAPH_TOKEN_ENCRYPTION_KEY='test-token-encryption-key')
    @patch.dict(os.environ, {'RADAI_SALES_GRAPH_CLIENT_SECRET': 'test-secret'})
    @patch('apps.sales.microsoft_graph.requests.request')
    @patch('apps.sales.microsoft_graph.requests.post')
    def test_delegated_authorization_encrypts_refresh_token_and_reads_my_inbox(
        self,
        token_post,
        graph_request,
    ):
        self.connection.auth_mode = 'delegated'
        self.connection.save(update_fields=['auth_mode'])
        token_response = Mock(ok=True)
        token_response.json.return_value = {
            'access_token': 'delegated-access-token',
            'refresh_token': 'delegated-refresh-token',
            'scope': 'User.Read Mail.Read',
        }
        token_post.return_value = token_response
        profile_response = Mock(ok=True, content=b'{}')
        profile_response.json.return_value = {
            'id': 'account-id',
            'displayName': 'Sales User',
            'mail': 'sales.user@example.com',
        }
        inbox_response = Mock(ok=True, content=b'{}')
        inbox_response.json.return_value = {
            'displayName': 'Inbox',
            'totalItemCount': 12,
            'unreadItemCount': 3,
        }
        graph_request.side_effect = [profile_response, inbox_response]

        result = SalesMicrosoftGraphService(
            self.connection
        ).complete_delegated_authorization('authorization-code')

        self.assertTrue(result['connected'])
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.mailbox_address, 'sales.user@example.com')
        self.assertNotEqual(
            self.connection.encrypted_refresh_token,
            'delegated-refresh-token',
        )
        self.assertNotIn(
            'delegated-refresh-token',
            self.connection.encrypted_refresh_token,
        )
        requested_urls = [call.args[1] for call in graph_request.call_args_list]
        self.assertTrue(any(url.endswith('/me') for url in requested_urls))
        self.assertTrue(any('/me/mailFolders/inbox' in url for url in requested_urls))
