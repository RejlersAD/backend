from .access_fixtures import grant_sales_actions
import os
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

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


class SalesMailboxConnectionOAuthTests(TestCase):
    endpoint = '/api/v1/sales/mailbox-connections/connect-my-outlook/'

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='sales-employee',
            email='sales.employee@example.com',
            password='test',
        )
        grant_sales_actions(self.user, 'sales_email_intake')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    @override_settings(
        SALES_MICROSOFT_TENANT_ID='central-tenant-id',
        SALES_MICROSOFT_CLIENT_ID='central-client-id',
        SALES_GRAPH_TOKEN_ENCRYPTION_KEY='test-token-encryption-key',
        SALES_MICROSOFT_OAUTH_REDIRECT_URI='https://api.example.com/oauth/callback/',
    )
    @patch.dict(os.environ, {'RADAI_SALES_GRAPH_CLIENT_SECRET': 'test-secret'})
    def test_one_click_connect_creates_user_connection_and_returns_microsoft_url(self):
        response = self.client.post(self.endpoint, {}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            'login.microsoftonline.com/central-tenant-id',
            response.data['authorization_url'],
        )
        self.assertIn('client_id=central-client-id', response.data['authorization_url'])
        connection = SalesMailboxConnection.objects.get(created_by=self.user)
        self.assertEqual(connection.mailbox_address, self.user.email)
        self.assertEqual(connection.auth_mode, 'delegated')
        self.assertFalse(connection.enabled)

    @override_settings(
        SALES_MICROSOFT_TENANT_ID='',
        SALES_MICROSOFT_CLIENT_ID='',
        SALES_GRAPH_TOKEN_ENCRYPTION_KEY='test-token-encryption-key',
    )
    @patch.dict(os.environ, {'RADAI_SALES_GRAPH_CLIENT_SECRET': 'test-secret'})
    def test_one_click_connect_reports_missing_central_configuration(self):
        response = self.client.post(self.endpoint, {}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data['detail'],
            'Outlook connection is not available yet. Contact your RADAI administrator.',
        )
        self.assertEqual(SalesMailboxConnection.objects.count(), 0)
