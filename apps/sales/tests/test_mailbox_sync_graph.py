"""Synthetic Graph delta transport; no credentials, mailbox or database access."""

from copy import deepcopy
from datetime import datetime, timezone
import json
from unittest.mock import Mock, patch
from uuid import uuid4

import requests
from django.test import SimpleTestCase, override_settings

from apps.sales.mailbox_sync_graph import SalesMailboxSyncError, SalesMailboxSyncGraphService
from apps.sales.microsoft_graph import SalesGraphConfigurationError, SalesMailboxReadError
from apps.sales.models import SalesMailboxConnection


def graph_response(payload, status=200, headers=None):
    response = Mock(status_code=status, headers=headers or {}, content=b'{}')
    response.json.return_value = deepcopy(payload)
    response.iter_content.side_effect = lambda chunk_size: iter([json.dumps(payload).encode('utf-8')])
    return response


@override_settings(SALES_MICROSOFT_GRAPH_BASE_URL='https://graph.microsoft.com/v1.0')
class SalesMailboxSyncGraphTests(SimpleTestCase):
    def setUp(self):
        self.connection = SalesMailboxConnection(
            id=uuid4(), tenant_id='synthetic-tenant', client_id='synthetic-client',
            mailbox_address='sales@example.test', auth_mode='application', enabled=False,
        )
        self.service = SalesMailboxSyncGraphService(self.connection)
        self.folder_id = 'AAMk-folder_1/+=-'
        self.folder_url = 'https://graph.microsoft.com/v1.0/users/sales%40example.test/mailFolders/delta'
        self.message_url = (
            'https://graph.microsoft.com/v1.0/users/sales%40example.test/'
            'mailFolders/AAMk-folder_1%2F%2B%3D-/messages/delta'
        )
        token = patch.object(SalesMailboxSyncGraphService, 'token', return_value='synthetic-token')
        self.token = token.start()
        self.addCleanup(token.stop)
        network = patch('apps.sales.mailbox_sync_graph.requests.request')
        self.network = network.start()
        self.addCleanup(network.stop)

    def page(self, *, records=None, link=None, next_page=False):
        return {
            'value': records if records is not None else [],
            '@odata.nextLink' if next_page else '@odata.deltaLink': link or self.folder_url + '?$deltatoken=synthetic',
        }

    def assert_sync_error(self, code, callback, status=None):
        with self.assertRaises(SalesMailboxSyncError) as caught:
            callback()
        error = caught.exception
        self.assertEqual(error.code, code)
        if status is not None:
            self.assertEqual(error.status_code, status)
        for marker in ('private-provider', 'synthetic-token', '$deltatoken', 'sales@example.test'):
            self.assertNotIn(marker, str(error))
            self.assertNotIn(marker, repr(error))
        return error

    def test_folder_discovery_returns_only_ids_and_removals_including_nested_entries(self):
        self.network.return_value = graph_response(self.page(records=[
            {'id': 'parent-folder', 'displayName': 'Private folder name'},
            {'id': 'nested-folder', 'parentFolderId': 'parent-folder', 'displayName': 'Private nested name'},
            {'id': 'removed-folder', '@removed': {'reason': 'deleted'}},
        ]))
        with patch.object(self.connection, 'save') as save:
            result = self.service.read_folder_changes()
        self.assertEqual(result['records'], [
            {'id': 'parent-folder', 'removed': False},
            {'id': 'nested-folder', 'removed': False},
            {'id': 'removed-folder', 'removed': True},
        ])
        self.assertEqual(self.network.call_count, 1)
        self.assertEqual(self.network.call_args.args, ('GET', self.folder_url))
        self.assertEqual(self.network.call_args.kwargs['params'], {'$select': 'id'})
        self.assertFalse(self.network.call_args.kwargs['allow_redirects'])
        self.assertTrue(self.network.call_args.kwargs['stream'])
        self.assertIn('IdType="ImmutableId"', self.network.call_args.kwargs['headers']['Prefer'])
        self.assertIn('odata.maxpagesize=50', self.network.call_args.kwargs['headers']['Prefer'])
        self.assertIsNone(result['next_link'])
        save.assert_not_called()
        self.network.return_value.close.assert_called_once_with()
        self.assertFalse(self.connection.enabled)

    def test_message_delta_is_folder_scoped_and_preserves_case_and_replayed_changes(self):
        link = self.message_url + '?$deltatoken=opaque%2b%2f%3d+token'
        self.network.return_value = graph_response(self.page(records=[
            {'id': 'Message-A', 'body': 'not retained'},
            {'id': 'message-a'}, {'id': 'Message-A', '@removed': {'reason': 'deleted'}},
        ], link=link))
        result = self.service.read_message_changes(self.folder_id)
        self.assertEqual(result['records'], [
            {'id': 'Message-A', 'removed': False}, {'id': 'message-a', 'removed': False},
            {'id': 'Message-A', 'removed': True},
        ])
        self.assertEqual(result['delta_link'], link)
        self.assertEqual(self.network.call_args.args, ('GET', self.message_url))
        self.assertEqual(self.network.call_args.kwargs['params'], {'$select': 'id'})

    def test_resume_uses_exact_opaque_url_and_can_finish_with_unchanged_delta(self):
        cursor = self.message_url + '?$skiptoken=opaque%2b%2f%3d+value&custom=%27x%27'
        delta = self.message_url + '?$deltatoken=final%2f+value'
        self.network.return_value = graph_response(self.page(link=delta))
        result = self.service.read_message_changes(self.folder_id, cursor)
        self.assertEqual(self.network.call_args.args, ('GET', cursor))
        self.assertIsNone(self.network.call_args.kwargs['params'])
        self.assertEqual(result['delta_link'], delta)
        self.network.return_value = graph_response(self.page(link=delta))
        self.assertEqual(self.service.read_message_changes(self.folder_id, delta)['records'], [])

    def test_next_page_and_equivalent_odata_key_syntax_are_accepted_without_rewriting(self):
        paths = [
            "https://graph.microsoft.com/v1.0/users('sales%40example.test')/mailfolders('AAMk-folder_1%2F%2B%3D-')/messages/delta",
            "https://graph.microsoft.com/v1.0/users/sales@example.test/mailFolders('AAMk-folder_1%2F%2B%3D-')/messages/delta",
            self.message_url,
        ]
        for path in paths:
            with self.subTest(path=path):
                cursor = path + '?$skiptoken=opaque'
                following = path + '?$skiptoken=next'
                self.network.return_value = graph_response(self.page(link=following, next_page=True))
                result = self.service.read_message_changes(self.folder_id, cursor)
                self.assertEqual(self.network.call_args.args, ('GET', cursor))
                self.assertEqual(result['next_link'], following)
                self.assertIsNone(result['delta_link'])
        cursor = "https://graph.microsoft.com/v1.0/users('sales@example.test')/mailfolders/delta?$deltatoken=folder"
        self.network.return_value = graph_response(self.page(link=cursor))
        self.assertEqual(self.service.read_folder_changes(cursor)['delta_link'], cursor)

    def test_invalid_checkpoint_locations_fail_before_authentication_or_network(self):
        valid = self.message_url + '?$skiptoken=opaque'
        locations = [
            valid.replace('https:', 'http:'), valid.replace('graph.microsoft.com', 'other.example.test'),
            valid.replace('graph.microsoft.com', 'graph.microsoft.com:443'),
            valid.replace('graph.microsoft.com', 'user:password@graph.microsoft.com'),
            valid.replace('/v1.0/', '/beta/'), valid.replace('sales%40', 'other%40'),
            valid.replace('AAMk-folder_1', 'AAMk-folder_2'), valid.replace('/messages/delta', '/messages'),
            valid.replace('/messages/delta', '/childFolders/delta'), valid + '#fragment', valid + '#',
            valid + '?second=query', valid + '\r\nX-Injected: true', valid.replace('/users/', '/users/../users/'),
            valid + '&unsafe=%QQ', valid.replace('/mailFolders/', '/mailFolders%255c/'),
            self.folder_url + '?$skiptoken=opaque', 'https://graph.microsoft.com/v1.0/me/mailFolders/delta?x=1',
            '', None, 123, 'x' * 16001,
        ]
        for location in locations:
            if location == '':
                continue  # Empty string deliberately requests an initial page.
            with self.subTest(location=location):
                self.network.reset_mock()
                self.token.reset_mock()
                self.assert_sync_error('invalid_response', lambda: self.service.read_message_changes(self.folder_id, location))
                self.network.assert_not_called()
                self.token.assert_not_called()

    def test_foreign_response_link_or_missing_terminal_state_rejects_entire_page(self):
        valid = self.folder_url + '?$deltatoken=opaque'
        pages = [
            {'value': [{'id': 'valid'}]},
            {'value': [{'id': 'valid'}], '@odata.deltaLink': valid, '@odata.nextLink': valid},
            self.page(records=[{'id': 'valid'}], link=''),
            self.page(records=[{'id': 'valid'}], link=valid.replace('sales%40', 'other%40')),
            self.page(records=[{'id': 'valid'}], link=12),
        ]
        pages[2]['@odata.deltaLink'] = ''
        for page in pages:
            with self.subTest(page=page):
                self.network.return_value = graph_response(page)
                self.assert_sync_error('invalid_response', self.service.read_folder_changes)

    def test_self_repeating_next_page_fails_but_empty_page_with_next_is_valid(self):
        cursor = self.folder_url + '?$skiptoken=first'
        self.network.return_value = graph_response(self.page(link=cursor, next_page=True))
        self.assert_sync_error('invalid_response', lambda: self.service.read_folder_changes(cursor))
        following = self.folder_url + '?$skiptoken=second'
        self.network.return_value = graph_response(self.page(link=following, next_page=True))
        self.assertEqual(self.service.read_folder_changes(cursor)['next_link'], following)

    def test_malformed_or_oversized_records_never_return_partial_success(self):
        invalid_records = [
            None, {}, [None], [{'id': 123}], [{'id': ''}], [{'id': 'x' * 513}],
            [{'id': 'valid'}, {'id': 'bad?query'}], [{'id': 'bad\n'}],
            [{'id': 'valid', '@removed': None}], [{'id': 'valid', '@removed': 'deleted'}],
            [{'id': f'message-{index}'} for index in range(51)],
        ]
        for records in invalid_records:
            with self.subTest(records=records):
                payload = self.page()
                payload['value'] = records
                self.network.return_value = graph_response(payload)
                self.assert_sync_error('invalid_response', self.service.read_folder_changes)
        self.network.return_value = graph_response(self.page(records=[{'id': f'message-{i}'} for i in range(50)]))
        self.assertEqual(len(self.service.read_folder_changes()['records']), 50)

    def test_invalid_folder_ids_fail_before_network(self):
        for value in (None, '', 12, 'x' * 513, '../folder', 'folder?query', 'folder\n'):
            with self.subTest(value=value):
                self.network.reset_mock()
                self.assert_sync_error('invalid_response', lambda: self.service.read_message_changes(value))
                self.network.assert_not_called()

    def test_expired_checkpoint_codes_are_safe_and_do_not_follow_location(self):
        cases = [(410, 'Gone'), (400, 'syncStateNotFound'), (404, 'ErrorSyncStateNotFound'),
                 (400, 'ErrorInvalidSyncStateData'), (409, 'resyncRequired')]
        for status, code in cases:
            with self.subTest(status=status, code=code):
                self.network.reset_mock()
                self.network.return_value = graph_response(
                    {'error': {'code': code, 'message': 'private-provider'}}, status,
                    {'Location': 'https://other.example.test/unsafe'},
                )
                self.assert_sync_error('checkpoint_expired', self.service.read_folder_changes, status)
                self.assertEqual(self.network.call_count, 1)

    def test_authorization_missing_source_and_provider_errors_keep_status_without_content(self):
        cases = [(401, 'authorization_required'), (403, 'authorization_required'),
                 (404, 'source_unavailable'), (400, 'invalid_response'),
                 (500, 'provider_unavailable'), (503, 'provider_unavailable'),
                 (504, 'provider_unavailable'), (302, 'provider_unavailable')]
        for status, code in cases:
            with self.subTest(status=status):
                self.network.return_value = graph_response({'error': {'message': 'private-provider'}}, status)
                self.assert_sync_error(code, self.service.read_folder_changes, status)
                self.network.return_value.close.assert_called_once_with()
        self.network.return_value = graph_response({'error': {'code': 'syncStateNotFound'}}, 403)
        self.assert_sync_error('authorization_required', self.service.read_folder_changes, 403)

    def test_throttling_honors_integer_and_http_date_without_sleep_or_early_clamp(self):
        now = datetime(2026, 9, 28, 12, 0, 0, 1000, tzinfo=timezone.utc)
        cases = [('120', 120, False), ('0', 0, False), ('86400', 86400, False),
                 ('Mon, 28 Sep 2026 12:02:00 GMT', 120, False),
                 ('Mon, 28 Sep 2026 11:59:00 GMT', 0, False),
                 ('86401', None, True), ('999999999999999999', None, True),
                 ('Wed, 30 Sep 2026 12:00:00 GMT', None, True),
                 ('invalid', None, False), ('-1', None, False)]
        with patch('apps.sales.mailbox_sync_graph.timezone.now', return_value=now):
            for value, expected, excessive in cases:
                with self.subTest(value=value):
                    self.network.return_value = graph_response(
                        {'error': {'message': 'private-provider'}}, 429, {'Retry-After': value},
                    )
                    error = self.assert_sync_error('throttled', self.service.read_folder_changes, 429)
                    self.assertEqual(error.retry_after, expected)
                    self.assertEqual(error.retry_after_exceeds_limit, excessive)
        self.network.return_value = graph_response({}, 503, {'Retry-After': '45'})
        self.assertEqual(self.assert_sync_error('provider_unavailable', self.service.read_folder_changes, 503).retry_after, 45)

    def test_network_token_and_json_failures_are_sanitized(self):
        self.network.side_effect = requests.Timeout('private-provider request with synthetic-token')
        self.assert_sync_error('provider_unavailable', self.service.read_folder_changes, 503)
        self.network.side_effect = None
        for error, code in ((SalesGraphConfigurationError('private-provider'), 'authorization_required'),
                            (RuntimeError('private-provider'), 'provider_unavailable')):
            self.token.side_effect = error
            self.assert_sync_error(code, self.service.read_folder_changes, 503)
        self.token.side_effect = None
        self.network.return_value = graph_response({})
        self.network.return_value.iter_content.side_effect = lambda chunk_size: iter([b'{private-provider'])
        self.assert_sync_error('invalid_response', self.service.read_folder_changes, 502)
        self.network.return_value.close.assert_called_once_with()
        self.network.return_value = graph_response([])
        self.assert_sync_error('invalid_response', self.service.read_folder_changes, 502)

    def test_oversized_stream_stops_reading_before_body_allocation_and_closes_response(self):
        consumed = []

        def chunks(chunk_size):
            for index in range(20):
                consumed.append(index)
                yield b'x' * 16

        self.network.return_value = graph_response({})
        self.network.return_value.iter_content.side_effect = chunks
        with patch.object(self.service, 'MAX_RESPONSE_BYTES', 32):
            with patch('apps.sales.mailbox_sync_graph.json.loads') as parse:
                self.assert_sync_error('invalid_response', self.service.read_folder_changes, 502)
            parse.assert_not_called()
        self.assertEqual(consumed, [0, 1, 2])
        self.network.return_value.close.assert_called_once_with()

    def test_interrupted_stream_returns_safe_retry_error_without_partial_page_and_closes(self):
        def chunks(chunk_size):
            yield b'{"value":[{"id":"valid-message"}]'
            raise requests.ConnectionError('private-provider interrupted source with synthetic-token')

        self.network.return_value = graph_response({})
        self.network.return_value.iter_content.side_effect = chunks
        self.assert_sync_error('provider_unavailable', self.service.read_folder_changes, 503)
        self.network.return_value.close.assert_called_once_with()

    def test_error_body_is_bounded_and_unneeded_provider_bodies_are_not_downloaded(self):
        self.network.return_value = graph_response({}, 404)
        self.network.return_value.iter_content.side_effect = lambda chunk_size: iter([b'x' * 64001])
        self.assert_sync_error('source_unavailable', self.service.read_folder_changes, 404)
        self.network.return_value.close.assert_called_once_with()
        self.network.return_value = graph_response({'private': 'private-provider'}, 429, {'Retry-After': '12'})
        self.assert_sync_error('throttled', self.service.read_folder_changes, 429)
        self.network.return_value.iter_content.assert_not_called()
        self.network.return_value.close.assert_called_once_with()

    def test_inherited_full_source_capture_uses_same_get_retry_contract_and_safe_failure(self):
        message = {
            'id': 'message-1', 'subject': 'Synthetic request', 'isDraft': False,
            'from': {'emailAddress': {'address': 'client@example.test'}},
            'toRecipients': [{'emailAddress': {'address': 'sales@example.test'}}],
            'hasAttachments': False, 'conversationId': 'conversation-1',
            'internetMessageId': '<synthetic@example.test>',
            'receivedDateTime': '2026-09-28T08:00:00Z',
            'body': {'contentType': 'text', 'content': 'Synthetic full source.'},
        }
        self.network.return_value = graph_response(message)
        result = self.service.get_message_for_capture('message-1')
        self.assertEqual(result['body_text'], 'Synthetic full source.')
        self.assertEqual(self.network.call_args.args, (
            'GET', 'https://graph.microsoft.com/v1.0/users/sales%40example.test/messages/message-1',
        ))
        self.assertFalse(self.network.call_args.kwargs['allow_redirects'])
        self.assertIn('body', self.network.call_args.kwargs['params']['$select'].split(','))
        self.network.return_value = graph_response({}, 429, {'Retry-After': '75'})
        error = self.assert_sync_error('throttled', lambda: self.service.get_message_for_capture('message-1'), 429)
        self.assertEqual(error.retry_after, 75)
        message['body'] = None
        self.network.return_value = graph_response(message)
        with self.assertRaises(SalesMailboxReadError) as caught:
            self.service.get_message_for_capture('message-1')
        self.assertEqual(caught.exception.status_code, 502)

    def test_only_application_public_graph_connections_and_bounded_timeouts(self):
        self.connection.auth_mode = 'delegated'
        with self.assertRaises(SalesMailboxReadError):
            self.service.read_folder_changes()
        self.network.assert_not_called()
        self.connection.auth_mode = 'application'
        self.service.base_url = 'https://other.example.test/v1.0'
        with self.assertRaises(SalesMailboxReadError):
            self.service.read_folder_changes()
        self.network.assert_not_called()
        self.service.base_url = 'https://graph.microsoft.com/v1.0'
        self.service.timeout = 900
        self.network.return_value = graph_response(self.page())
        self.service.read_folder_changes()
        self.assertEqual(self.network.call_args.kwargs['timeout'], 30)
