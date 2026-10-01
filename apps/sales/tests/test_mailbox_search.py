"""Mailbox-wide subject search through the existing guarded read-only route."""

import json
from unittest.mock import patch

import requests
from django.core import signing
from django.test import SimpleTestCase, TestCase, override_settings

from apps.rbac.models import Permission, UserPermissionOverride
from apps.sales.microsoft_graph import SalesMicrosoftGraphService
from apps.sales.models import SalesEmailIntake, SalesMailboxConnection

from . import test_mailbox_browsing as browsing


SUBJECT = 'Q-101371/ FW: Unpriced Technical Bid Invitation'


@override_settings(SALES_MICROSOFT_GRAPH_BASE_URL='https://graph.microsoft.com/v1.0')
class SalesMailboxSearchServiceTests(SimpleTestCase):
    setUp = browsing.SalesMailboxBrowsingServiceTests.setUp
    assert_read_error = browsing.SalesMailboxBrowsingServiceTests.assert_read_error
    cursor = browsing.SalesMailboxBrowsingServiceTests.cursor

    def test_search_finds_older_message_outside_the_first_browse_page(self):
        recent = [{**browsing.MESSAGE, 'id': f'recent-{index}', 'subject': f'Recent {index}'}
                  for index in range(50)]
        older = {**browsing.MESSAGE, 'id': 'older-match', 'subject': SUBJECT,
                 'receivedDateTime': '2025-04-01T08:00:00Z'}

        def provider(method, url, **kwargs):
            self.assertEqual((method, url), ('GET', self.url))
            params = kwargs['params']
            if '$search' in params:
                self.assertEqual(json.loads(params['$search']),
                                 '"Q-101371" AND "FW" AND "Unpriced" AND "Technical" AND "Bid" AND "Invitation"')
                self.assertNotIn('$orderby', params)
                self.assertEqual(params['$top'], 50)
                return browsing.graph_response({'value': [older]})
            return browsing.graph_response({'value': recent, '@odata.nextLink': self.next_link})

        self.network.side_effect = provider
        with patch.object(self.connection, 'save') as save:
            page = self.service.list_messages(user_id=17)
            result = self.service.list_messages(user_id=17, search='  ' + SUBJECT + '\u00a0  ')
        self.assertEqual(len(page['results']), 50)
        self.assertNotIn('older-match', [row['id'] for row in page['results']])
        self.assertEqual([row['id'] for row in result['results']], ['older-match'])
        self.assertEqual(result['search'], SUBJECT)
        self.assertEqual(result['search_result_limit'], 1000)
        self.assertEqual(self.network.call_count, 2)
        save.assert_not_called()

    def test_pasted_spacing_and_punctuation_produce_only_literal_terms(self):
        for search, expected, expression in [
            ('  Q-101371/\u00a0FW:\tUnpriced\n Technical  Bid Invitation  ', SUBJECT,
             '"Q-101371" AND "FW" AND "Unpriced" AND "Technical" AND "Bid" AND "Invitation"'),
            ('a" OR from:someone@example.test OR "b', 'a" OR from:someone@example.test OR "b',
             '"a" AND "OR" AND "from" AND "someone@example.test" AND "OR" AND "b"'),
            ('folder\\name "quoted"', 'folder\\name "quoted"', '"folder" AND "name" AND "quoted"'),
            ('R&D + "bid" / 50% #1', 'R&D + "bid" / 50% #1', '"R" AND "D" AND "bid" AND "50" AND "1"'),
            ('\u0645\u0646\u0627\u0642\u0635\u0629 technique', '\u0645\u0646\u0627\u0642\u0635\u0629 technique',
             '"\u0645\u0646\u0627\u0642\u0635\u0629" AND "technique"'),
        ]:
            with self.subTest(search=search):
                result = self.service.list_messages(user_id=17, search=search)
                self.assertEqual(json.loads(self.network.call_args.kwargs['params']['$search']), expression)
                self.assertEqual(result['search'], expected)

    def test_blank_search_restores_browse_and_validation_happens_before_token(self):
        result = self.service.list_messages(user_id=17, search=' \u00a0\n\t ')
        self.assertEqual(result['search'], '')
        self.assertIsNone(result['search_result_limit'])
        self.assertNotIn('$search', self.network.call_args.kwargs['params'])
        self.assertEqual(self.network.call_args.kwargs['params']['$orderby'], 'receivedDateTime desc')
        self.network.reset_mock()
        self.token.reset_mock()
        for invalid in [42, [], {}, 'x' * 257, ' ' * 2049, 'abc\0def', 'abc\x7fdef', '/ : " + *', '\U0001f680']:
            with self.subTest(invalid_type=type(invalid).__name__):
                self.assert_read_error(400, lambda: self.service.list_messages(user_id=17, search=invalid))
        self.network.assert_not_called()
        self.token.assert_not_called()
        self.service.list_messages(user_id=17, search=' x ' * 128)

    def test_search_continuation_retains_provider_url_and_normalized_query(self):
        next_link = self.next_link + '&$search=%22%5C%22Q-101371%5C%22%22'
        self.network.side_effect = [
            browsing.graph_response({'value': [browsing.MESSAGE], '@odata.nextLink': next_link}),
            browsing.graph_response({'value': []}),
        ]
        first = self.service.list_messages(user_id=17, search=' Q-101371 ')
        second = self.service.list_messages(user_id=17, cursor=first['next_cursor'], search='Q-101371\u00a0')
        self.assertEqual(second['search'], 'Q-101371')
        self.assertEqual(second['results'], [])
        self.assertIsNone(second['next_cursor'])
        self.assertEqual(self.network.call_args.args, ('GET', next_link))
        self.assertIsNone(self.network.call_args.kwargs['params'])
        decoded = signing.loads(first['next_cursor'], salt=self.service.MAILBOX_CURSOR_SALT)
        self.assertEqual(len(decoded['search']), 64)

    def test_cursors_cannot_cross_search_browse_or_changed_query(self):
        self.network.return_value = browsing.graph_response({'value': [], '@odata.nextLink': self.next_link})
        searched = self.service.list_messages(user_id=17, search=SUBJECT)['next_cursor']
        unsearched = self.service.list_messages(user_id=17)['next_cursor']
        legacy = self.cursor()
        self.network.reset_mock()
        self.token.reset_mock()
        for cursor, search in [(searched, None), (searched, 'different'), (unsearched, SUBJECT), (legacy, SUBJECT)]:
            self.assert_read_error(400, lambda: self.service.list_messages(user_id=17, cursor=cursor, search=search))
        self.network.assert_not_called()
        self.token.assert_not_called()
        self.network.return_value = browsing.graph_response({'value': []})
        self.assertEqual(self.service.list_messages(user_id=17, cursor=legacy)['results'], [])

    def test_unsafe_provider_continuation_is_rejected_for_search(self):
        for next_link in ['https://example.test/messages?$skip=50',
                          'https://graph.microsoft.com/v1.0/users/other/messages?$skip=50']:
            self.network.return_value = browsing.graph_response({'value': [], '@odata.nextLink': next_link})
            self.assert_read_error(502, lambda: self.service.list_messages(user_id=17, search=SUBJECT))


@override_settings(ROOT_URLCONF=browsing.__name__, SALES_MICROSOFT_GRAPH_BASE_URL='https://graph.microsoft.com/v1.0')
class SalesMailboxSearchAPITests(TestCase):
    setUp = browsing.SalesMailboxBrowsingAPITests.setUp
    assert_private = browsing.SalesMailboxBrowsingAPITests.assert_private

    def assert_unmodified(self, before):
        self.assertEqual(before, SalesMailboxConnection.objects.values().get(pk=self.connection.pk))
        self.assertEqual(SalesEmailIntake.objects.count(), 0)

    def test_guarded_search_and_retry_have_no_persistent_effects(self):
        before = SalesMailboxConnection.objects.values().get(pk=self.connection.pk)
        self.network.return_value = browsing.graph_response({'value': [{**browsing.MESSAGE, 'subject': SUBJECT}]})
        for unused in range(2):
            response = self.client.get(self.list_url, {'search': SUBJECT + '\u00a0 '})
            self.assertEqual(response.status_code, 200)
            self.assert_private(response)
            self.assertEqual(response.data['search'], SUBJECT)
            self.assertEqual(response.data['results'][0]['subject'], SUBJECT)
            self.assert_unmodified(before)
        self.assertTrue(all(call.args[0] == 'GET' for call in self.network.call_args_list))

    def test_invalid_and_duplicate_query_parameters_block_provider(self):
        for params in [
            {'search': ['one', 'two']}, {'search': SUBJECT, 'cursor': ['one', 'two']},
            {'search': SUBJECT, 'nextLink': self.graph_url},
            {'search': SUBJECT, 'mailbox': 'other@example.test'},
            {'search': 'x' * 257}, {'search': ' ' * 2049}, {'search': 'bad\0query'},
        ]:
            response = self.client.get(self.list_url, params)
            self.assertEqual(response.status_code, 400)
            self.assert_private(response)
        self.network.assert_not_called()
        self.token.assert_not_called()

    def test_search_rechecks_owner_scope_authentication_and_explicit_denial(self):
        for user, expected in [(self.other, {404}), (self.no_access, {403}), (None, {401, 403})]:
            self.client.force_authenticate(user)
            response = self.client.get(self.list_url, {'search': SUBJECT})
            self.assertIn(response.status_code, expected)
            self.assert_private(response)
        permission = Permission.objects.get(module__code='sales_email_intake', action='read')
        UserPermissionOverride.objects.create(user_profile=self.admin.rbac_profile, permission=permission, allowed=False)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(self.list_url, {'search': SUBJECT}).status_code, 403)
        self.network.assert_not_called()
        self.token.assert_not_called()

    def test_search_cursor_mismatch_tamper_actor_and_expiry_fail_before_network(self):
        self.network.return_value = browsing.graph_response({'value': [], '@odata.nextLink': self.graph_url + '?$skip=55'})
        first = self.client.get(self.list_url, {'search': SUBJECT})
        cursor = first.data['next_cursor']
        self.network.reset_mock()
        self.token.reset_mock()
        for params in [{'cursor': cursor}, {'cursor': cursor, 'search': 'different'},
                       {'cursor': cursor + 'tamper', 'search': SUBJECT}]:
            response = self.client.get(self.list_url, params)
            self.assertEqual(response.status_code, 400)
            self.assert_private(response)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(self.list_url, {'cursor': cursor, 'search': SUBJECT}).status_code, 400)
        self.client.force_authenticate(self.owner)
        with patch('django.core.signing.time.time', return_value=1000):
            expired = signing.dumps(signing.loads(cursor, salt=SalesMicrosoftGraphService.MAILBOX_CURSOR_SALT),
                                    salt=SalesMicrosoftGraphService.MAILBOX_CURSOR_SALT)
        with patch('django.core.signing.time.time', return_value=2000):
            response = self.client.get(self.list_url, {'cursor': expired, 'search': SUBJECT})
        self.assertEqual(response.status_code, 410)
        self.assert_private(response)
        self.network.assert_not_called()
        self.token.assert_not_called()

    def test_provider_failures_are_private_safe_and_retryable_without_writes(self):
        before = SalesMailboxConnection.objects.values().get(pk=self.connection.pk)
        for status in [400, 401, 403, 429, 500, 503]:
            self.network.return_value = browsing.graph_response({'error': 'sensitive-provider-text'}, status=status)
            response = self.client.get(self.list_url, {'search': SUBJECT})
            self.assertEqual(response.status_code, 503 if status in {429, 503} else 502)
            self.assert_private(response)
            self.assertNotIn('sensitive-provider-text', str(response.data))
            self.assert_unmodified(before)
        self.network.side_effect = requests.Timeout('sensitive-provider-text')
        response = self.client.get(self.list_url, {'search': SUBJECT})
        self.assertEqual(response.status_code, 503)
        self.assert_private(response)
        self.network.side_effect = None
        self.network.return_value = browsing.graph_response({'value': []})
        self.assertEqual(self.client.get(self.list_url, {'search': SUBJECT}).status_code, 200)
        self.assert_unmodified(before)
