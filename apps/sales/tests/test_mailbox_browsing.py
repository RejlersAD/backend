"""Read-only mailbox browsing with real route permissions and mocked Graph."""

from copy import deepcopy
from unittest.mock import Mock, patch
from uuid import uuid4

import requests
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.rbac.models import Permission, RolePermission, UserPermissionOverride, UserProfile
from apps.rbac.route_guard import secure_module_endpoints
from apps.sales import email_content
from apps.sales.email_content import project_email_body
from apps.sales.microsoft_graph import (
    SalesGraphConfigurationError, SalesMailboxReadError, SalesMicrosoftGraphService,
)
from apps.sales.models import SalesEmailIntake, SalesMailboxConnection
from apps.sales.views import SalesMailboxConnectionViewSet

from .access_fixtures import grant_sales_actions


router = DefaultRouter()
router.register('mailbox-connections', SalesMailboxConnectionViewSet, basename='browse-mailboxes')
urlpatterns = [path('api/v1/sales/', include(router.urls))]
secure_module_endpoints(urlpatterns)

MESSAGE = {
    'id': 'AAMk-message-1=', 'subject': 'Synthetic engineering enquiry',
    'from': {'emailAddress': {'name': 'Synthetic Client', 'address': 'client@example.test'}},
    'receivedDateTime': '2026-09-28T08:00:00Z',
    'sentDateTime': '2026-09-28T07:59:00Z',
    'bodyPreview': 'Synthetic preview', 'hasAttachments': True,
    'isRead': False, 'isDraft': False, 'importance': 'normal',
}


def graph_response(payload, status=200):
    response = Mock(status_code=status)
    response.json.return_value = deepcopy(payload)
    return response


@override_settings(SALES_MICROSOFT_GRAPH_BASE_URL='https://graph.microsoft.com/v1.0')
class SalesMailboxBrowsingServiceTests(SimpleTestCase):
    def setUp(self):
        self.connection = SalesMailboxConnection(
            id=uuid4(), tenant_id='synthetic-tenant', client_id='synthetic-client',
            mailbox_address='sales@example.test', auth_mode='application', enabled=False,
        )
        self.service = SalesMicrosoftGraphService(self.connection)
        token = patch.object(SalesMicrosoftGraphService, 'token', return_value='synthetic-token')
        self.token = token.start()
        self.addCleanup(token.stop)
        network = patch('apps.sales.microsoft_graph.requests.request')
        self.network = network.start()
        self.addCleanup(network.stop)
        self.network.return_value = graph_response({'value': [MESSAGE]})
        self.url = 'https://graph.microsoft.com/v1.0/users/sales%40example.test/messages'
        self.next_link = self.url + '?$top=50&$select=id%2Csubject&$skip=57'

    def assert_read_error(self, expected_status, callback):
        with self.assertRaises(SalesMailboxReadError) as caught:
            callback()
        self.assertEqual(caught.exception.status_code, expected_status)
        self.assertNotIn('sensitive-provider-text', str(caught.exception))
        return caught.exception

    def cursor(self, **changes):
        payload = {'scope': self.service._mailbox_cursor_scope(17), 'next': self.next_link}
        payload.update(changes)
        return signing.dumps(payload, salt=self.service.MAILBOX_CURSOR_SALT)

    def test_lists_all_mail_with_bounded_projection_and_no_state_write(self):
        message = {**MESSAGE, 'body': {'content': 'not in list'}, 'secret': 'not in projection'}
        self.network.return_value = graph_response({'value': [message]})
        with patch.object(self.connection, 'save') as save:
            result = self.service.list_messages(user_id=17)
        save.assert_not_called()
        self.assertFalse(self.connection.enabled)
        self.assertEqual(result['mailbox_address'], self.connection.mailbox_address)
        self.assertIsNone(result['next_cursor'])
        self.assertEqual(result['results'][0]['sender_email'], 'client@example.test')
        self.assertFalse(result['results'][0]['is_read'])
        self.assertNotIn('body', result['results'][0])
        self.assertNotIn('secret', result['results'][0])
        call = self.network.call_args
        self.assertEqual(call.args, ('GET', self.url))
        self.assertEqual(call.kwargs['params']['$top'], 50)
        self.assertEqual(call.kwargs['params']['$orderby'], 'receivedDateTime desc')
        self.assertNotIn('body', call.kwargs['params']['$select'].split(','))
        self.assertFalse(call.kwargs['allow_redirects'])

    def test_direction_uses_exact_mailbox_recipients_in_the_existing_list_read(self):
        for recipient_field in ('toRecipients', 'ccRecipients'):
            with self.subTest(recipient_field=recipient_field):
                self.network.reset_mock()
                self.network.return_value = graph_response({'value': [{
                    **MESSAGE, 'isRead': True, 'subject': 'FW: Synthetic enquiry',
                    recipient_field: [{'emailAddress': {'address': ' SALES@EXAMPLE.TEST '}}],
                }]})
                result = self.service.list_messages(user_id=17)
                self.assertEqual(result['results'][0]['direction'], 'incoming')
                self.assertEqual(self.network.call_count, 1)
                fields = self.network.call_args.kwargs['params']['$select'].split(',')
                self.assertIn('toRecipients', fields)
                self.assertIn('ccRecipients', fields)
                self.assertNotIn('bccRecipients', fields)
                for key in ('toRecipients', 'ccRecipients', 'bccRecipients', 'to_recipients', 'cc_recipients'):
                    self.assertNotIn(key, result['results'][0])

    def test_direction_recognizes_own_from_sender_delegation_and_self_addressed_mail(self):
        own = {'emailAddress': {'address': ' Sales@Example.Test '}}
        other = {'emailAddress': {'address': 'delegate@example.test'}}
        for origin in (
            {'from': own, 'sender': own},
            {'from': own, 'sender': other},
            {'from': other, 'sender': own},
            {'from': None, 'sender': own},
        ):
            with self.subTest(origin=origin):
                self.network.return_value = graph_response({'value': [{
                    **MESSAGE, **origin, 'toRecipients': [own], 'isRead': False,
                }]})
                self.assertEqual(self.service.list_messages(user_id=17)['results'][0]['direction'], 'outgoing')

    def test_draft_precedes_origin_and_recipient_classification(self):
        for origin in (None, {'emailAddress': {'address': 'sales@example.test'}}, MESSAGE['from']):
            with self.subTest(origin=origin):
                self.network.return_value = graph_response({'value': [{
                    **MESSAGE, 'from': origin, 'isDraft': True,
                    'toRecipients': [{'emailAddress': {'address': 'sales@example.test'}}],
                }]})
                row = self.service.list_messages(user_id=17)['results'][0]
                self.assertEqual(row['direction'], 'draft')
                self.assertTrue(row['is_draft'])

    def test_unproven_direction_stays_unknown_without_guessing_domains_or_hidden_recipients(self):
        own = {'emailAddress': {'address': 'sales@example.test'}}
        variants = [
            {},
            {'toRecipients': [{'emailAddress': {'address': 'sales-alias@example.test'}}]},
            {'bccRecipients': [own]},
            {'toRecipients': {'emailAddress': {'address': 'sales@example.test'}}},
            {'toRecipients': [None, 'sales@example.test', {'emailAddress': 7}]},
            {'from': None, 'sender': None, 'toRecipients': [own]},
            {'from': {'emailAddress': {'address': 7}}, 'toRecipients': [own]},
            {'from': {'emailAddress': {'address': 'not-an-address'}}, 'toRecipients': [own]},
            {'from': {'emailAddress': {'address': ' '}}},
            {'isDraft': 'true'},
        ]
        for index, fields in enumerate(variants):
            with self.subTest(index=index):
                self.network.return_value = graph_response({'value': [{**MESSAGE, **fields}]})
                self.assertEqual(self.service.list_messages(user_id=17)['results'][0]['direction'], 'unknown')

    def test_old_cursor_without_recipient_metadata_remains_usable(self):
        self.network.return_value = graph_response({'value': [MESSAGE]})
        page = self.service.list_messages(user_id=17, cursor=self.cursor())
        self.assertEqual(page['results'][0]['direction'], 'unknown')
        self.assertEqual(self.network.call_count, 1)
        self.assertEqual(self.network.call_args.args, ('GET', self.next_link))
        self.assertIsNone(self.network.call_args.kwargs['params'])

    def test_presentation_direction_does_not_change_source_review_digest(self):
        self.network.return_value = graph_response({
            **MESSAGE, 'body': {'contentType': 'text', 'content': 'Synthetic unchanged source'},
        })
        first = self.service.get_message(MESSAGE['id'])
        with patch.object(SalesMicrosoftGraphService, '_message_direction', return_value='incoming'):
            second = self.service.get_message(MESSAGE['id'])
        self.assertNotEqual(first['direction'], second['direction'])
        self.assertEqual(first['analysis_source_hash'], second['analysis_source_hash'])
        self.assertEqual(
            {key: value for key, value in first['extracted_information'].items() if key != 'analysis'},
            {key: value for key, value in second['extracted_information'].items() if key != 'analysis'},
        )
        self.assertNotEqual(first['extracted_information']['analysis']['sources'][0]['direction'],
                            second['extracted_information']['analysis']['sources'][0]['direction'])

    def test_list_thread_roles_use_draft_and_prefix_without_extra_graph_requests(self):
        messages = [{**MESSAGE, 'id': str(index), 'subject': subject, 'isDraft': draft}
                    for index, (subject, draft) in enumerate([
                        ('Re: RFQ bulletin', False), (' FW: Subject', False), ('New subject', False), ('Re: Draft', True),
                    ])]
        self.network.return_value = graph_response({'value': messages})
        result = self.service.list_messages(user_id=17)
        self.assertEqual([row['thread_role'] for row in result['results']], ['reply', 'forward', 'unknown', 'draft'])
        self.assertEqual(self.network.call_count, 1)
        self.assertNotIn('internetMessageHeaders', self.network.call_args.kwargs['params']['$select'])
        self.assertTrue(all(row['thread_role_reason'] for row in result['results']))

    def test_cursor_keeps_entire_next_link_including_non_page_size_skip(self):
        self.network.side_effect = [
            graph_response({'value': [], '@odata.nextLink': self.next_link}),
            graph_response({'value': [MESSAGE]}),
        ]
        first = self.service.list_messages(user_id=17)
        self.assertEqual(first['results'], [])
        self.assertTrue(first['next_cursor'])
        second = self.service.list_messages(user_id=17, cursor=first['next_cursor'])
        self.assertEqual(second['results'][0]['id'], MESSAGE['id'])
        self.assertEqual(self.network.call_args.args, ('GET', self.next_link))
        self.assertIsNone(self.network.call_args.kwargs['params'])

    def test_graph_literal_at_continuation_is_preserved_without_broad_decoding(self):
        next_link = self.next_link.replace('sales%40example.test', 'sales@example.test')
        self.network.side_effect = [
            graph_response({'value': [MESSAGE], '@odata.nextLink': next_link}),
            graph_response({'value': []}),
        ]
        first = self.service.list_messages(user_id=17)
        final = self.service.list_messages(user_id=17, cursor=first['next_cursor'])
        self.assertEqual(final['results'], [])
        self.assertIsNone(final['next_cursor'])
        self.assertEqual(self.network.call_args.args, ('GET', next_link))

    def test_rejects_tampered_raw_and_misbound_cursors_before_network(self):
        good = self.cursor()
        for cursor in [good + 'tampered', self.next_link, '', 'x' * 24001]:
            with self.subTest(kind=cursor[:12]):
                self.assert_read_error(400, lambda: self.service.list_messages(user_id=17, cursor=cursor))
        self.assert_read_error(400, lambda: self.service.list_messages(user_id=18, cursor=good))
        for field, replacement in [
            ('mailbox_address', 'other@example.test'), ('id', uuid4()),
            ('tenant_id', 'changed-tenant'), ('client_id', 'changed-client'),
        ]:
            previous = getattr(self.connection, field)
            setattr(self.connection, field, replacement)
            self.assert_read_error(400, lambda: self.service.list_messages(user_id=17, cursor=good))
            setattr(self.connection, field, previous)
        self.network.assert_not_called()
        self.token.assert_not_called()

    def test_expired_cursor_returns_refresh_recovery(self):
        with patch('django.core.signing.time.time', return_value=1000):
            cursor = self.cursor()
        with patch('django.core.signing.time.time', return_value=2000):
            self.assert_read_error(410, lambda: self.service.list_messages(user_id=17, cursor=cursor))
        self.network.assert_not_called()

    def test_rejects_unsafe_provider_next_links_without_fetching_them(self):
        links = [
            'https://example.test/messages?$skip=1',
            'http://graph.microsoft.com/v1.0/users/sales%40example.test/messages?$skip=1',
            self.next_link.replace('graph.microsoft.com', 'graph.microsoft.com:443'),
            self.next_link.replace('graph.microsoft.com', 'graph.microsoft.com@evil.test'),
            self.next_link.replace('sales%40example.test', 'other%40example.test'),
            self.next_link.replace('sales%40example.test', 'sales%2540example.test'),
            self.next_link.replace('/messages?', '%2Fmessages?'),
            self.next_link.replace('/messages?', '/messages/attachments?'),
            self.next_link + '#fragment', self.next_link + '\r\n', '', {},
        ]
        for link in links:
            with self.subTest(link_type=type(link).__name__):
                self.network.reset_mock()
                self.network.return_value = graph_response({'value': [MESSAGE], '@odata.nextLink': link})
                self.assert_read_error(502, lambda: self.service.list_messages(user_id=17))
                self.assertEqual(self.network.call_count, 1)

    def test_rejects_signed_unsafe_continuation_and_repeated_page(self):
        cursor = self.cursor(next='https://example.test/messages?$skip=1')
        self.assert_read_error(400, lambda: self.service.list_messages(user_id=17, cursor=cursor))
        self.network.assert_not_called()
        self.network.return_value = graph_response({'value': [MESSAGE], '@odata.nextLink': self.next_link})
        self.assert_read_error(502, lambda: self.service.list_messages(user_id=17, cursor=self.cursor()))

    def test_malformed_pages_and_message_rows_fail_closed(self):
        for payload in [[], {}, {'value': {}}, {'value': [None]}, {'value': [{}]}, {'value': [MESSAGE] * 51}]:
            with self.subTest(shape=type(payload).__name__):
                self.network.return_value = graph_response(payload)
                self.assert_read_error(502, lambda: self.service.list_messages(user_id=17))

    def test_detail_requests_html_and_preserves_plain_provider_fallback(self):
        message_id = 'AAMk/opaque+id=='
        payload = {
            **MESSAGE, 'id': message_id,
            'body': {'contentType': 'text', 'content': '<script>literal text</script>\nHello'},
            'toRecipients': [{'emailAddress': {'name': 'Sales', 'address': 'sales@example.test'}}],
            'ccRecipients': [{'emailAddress': {'name': 'CC', 'address': 'cc@example.test'}}],
            'webLink': 'https://outlook.example.test/', 'bccRecipients': [{'emailAddress': {}}],
        }
        self.network.return_value = graph_response(payload)
        result = self.service.get_message(message_id)
        self.assertEqual(result['body_text'], payload['body']['content'])
        self.assertIsNone(result['body_content'])
        self.assertEqual(result['to_recipients'], [{'name': 'Sales', 'email': 'sales@example.test'}])
        self.assertEqual(result['cc_recipients'][0]['email'], 'cc@example.test')
        self.assertNotIn('webLink', result)
        self.assertNotIn('bccRecipients', result)
        self.assertTrue(self.network.call_args.args[1].endswith('/AAMk%2Fopaque%2Bid%3D%3D'))
        self.assertEqual(self.network.call_args.kwargs['headers']['Prefer'], 'IdType="ImmutableId", outlook.body-content-type="html"')

    def test_html_detail_has_inert_structure_and_does_not_fetch_images(self):
        self.network.return_value = graph_response({
            **MESSAGE, 'body': {'contentType': 'html', 'content': '<p>Hello &amp; goodbye</p><img src="https://evil.test/tracker">'},
        })
        result = self.service.get_message(MESSAGE['id'])
        self.assertEqual(result['body_text'].strip(), 'Hello & goodbye')
        self.assertEqual(result['body_content'], [
            {'type': 'p', 'children': [{'type': 'text', 'text': 'Hello & goodbye'}]},
        ])
        self.assertEqual(self.network.call_count, 1)

    def test_invalid_detail_ids_never_reach_graph(self):
        for message_id in ['', None, '.', '..', '../../users/other/messages/id', '%2f..%2f', 'https://example.test/', 'id\n', 'x' * 2049]:
            self.assert_read_error(400, lambda: self.service.get_message(message_id))
        self.network.assert_not_called()

    def test_upstream_failures_are_safe_and_redirects_are_not_followed(self):
        for provider_status, public_status in [(301, 502), (401, 502), (403, 502), (404, 404), (429, 503), (500, 502), (503, 503), (504, 503)]:
            self.network.return_value = graph_response({'error': {'message': 'sensitive-provider-text'}}, provider_status)
            self.assert_read_error(public_status, lambda: self.service.list_messages(user_id=17))
        self.network.side_effect = requests.Timeout('sensitive-provider-text')
        self.assert_read_error(503, lambda: self.service.list_messages(user_id=17))
        self.network.side_effect = None
        self.network.return_value = graph_response({})
        self.network.return_value.json.side_effect = ValueError('sensitive-provider-text')
        self.assert_read_error(502, lambda: self.service.list_messages(user_id=17))

    def test_delegated_and_non_graph_configuration_rejected_before_token(self):
        self.connection.auth_mode = 'delegated'
        self.assert_read_error(400, lambda: self.service.list_messages(user_id=17))
        self.assert_read_error(400, lambda: self.service.get_message(MESSAGE['id']))
        self.connection.auth_mode = 'application'
        self.service.base_url = 'https://example.test/v1.0'
        self.assert_read_error(503, lambda: self.service.list_messages(user_id=17))
        self.token.assert_not_called()
        self.network.assert_not_called()

    def test_configuration_and_auth_failures_do_not_leak_raw_errors(self):
        for error in [SalesGraphConfigurationError('sensitive-provider-text'), RuntimeError('sensitive-provider-text')]:
            self.token.side_effect = error
            self.assert_read_error(503, lambda: self.service.list_messages(user_id=17))
        self.network.assert_not_called()


class SalesEmailContentProjectionTests(SimpleTestCase):
    @staticmethod
    def flatten(nodes):
        result = []
        for node in nodes or []:
            result.append(node)
            result.extend(SalesEmailContentProjectionTests.flatten(node.get('children', [])))
        return result

    def test_preserves_paragraphs_lists_formatting_and_wrapper_text_in_order(self):
        result = project_email_body(
            '<html><body><section>Opening<p>Hello <b>bold</b> and <i>italic</i><br>Next line</p>'
            '<ul><li>First</li><li>Second</li></ul><blockquote>Quoted</blockquote>'
            '<pre>  fixed\n  spacing</pre><div>End <code>code</code></div>Tail</section></body></html>',
            'html',
        )
        nodes = self.flatten(result['body_content'])
        types = [node['type'] for node in nodes]
        for expected in ['p', 'strong', 'em', 'br', 'ul', 'li', 'blockquote', 'pre', 'div', 'code']:
            self.assertIn(expected, types)
        self.assertNotIn('section', types)
        text = ''.join(node['text'] for node in nodes if node['type'] == 'text')
        self.assertTrue(text.startswith('OpeningHello bold and italic'))
        self.assertTrue(text.endswith('End codeTail'))
        self.assertIn('Next line\n', result['body_text'])
        self.assertIn('First\n', result['body_text'])
        self.assertIn('  fixed\n  spacing', result['body_text'])

    def test_table_structure_and_spans_survive_without_untrusted_attributes(self):
        result = project_email_body(
            '<table style="width:9000px" background="https://example.test/tracker">'
            '<thead><tr><th colspan="2">Heading</th></tr></thead>'
            '<tr><td rowspan="2" id="unsafe">Item A</td><td onclick="attack()">50</td></tr>'
            '<tr><td colspan="99999" rowspan="-1">75</td></tr></table>', 'html',
        )
        table = result['body_content'][0]
        self.assertEqual(table['type'], 'table')
        self.assertEqual([node['type'] for node in table['children']], ['thead', 'tbody'])
        self.assertEqual(table['children'][0]['children'][0]['children'][0]['col_span'], 2)
        rows = table['children'][1]['children']
        self.assertEqual(rows[0]['children'][0]['row_span'], 2)
        self.assertNotIn('col_span', rows[1]['children'][0])
        self.assertNotIn('row_span', rows[1]['children'][0])
        self.assertIn('Item A\t50', result['body_text'])
        for node in self.flatten(result['body_content']):
            self.assertLessEqual(set(node), {'type', 'children', 'text', 'col_span', 'row_span'})

    def test_active_subtrees_attributes_and_remote_assets_are_removed(self):
        result = project_email_body(
            '<html><head><style>hidden-css</style><script>hidden-head-script</script></head><body>'
            '<p class="external" style="background:url(https://example.test/)" onclick="attack()">Visible'
            '<script>hidden-script</script><style>hidden-style</style>'
            '<svg><text>hidden-svg</text></svg><math><mtext>hidden-math</mtext></math>'
            '<iframe src="https://example.test/">hidden-frame</iframe>'
            '<object data="https://example.test/">hidden-object</object>'
            '<form><button>hidden-button</button><input value="hidden-input"></form>'
            '<img src="https://example.test/tracker" onerror="attack()">'
            '<embed src="https://example.test/"> Tail</p></body></html>', 'html',
        )
        self.assertTrue(result['body_content'])
        nodes = self.flatten(result['body_content'])
        self.assertTrue(all(node['type'] in email_content.ALLOWED_TYPES | {'text'} for node in nodes))
        self.assertNotIn('hidden-', str(result))
        self.assertNotIn('https://example.test/', str(result))
        self.assertIn('Visible', result['body_text'])
        self.assertIn('Tail', result['body_text'])
        for node in nodes:
            self.assertLessEqual(set(node), {'type', 'children', 'text'})

    def test_table_caption_stays_attached_to_its_rows(self):
        result = project_email_body(
            '<table><caption>Quoted services</caption><tr><th>Service</th><th>Hours</th></tr>'
            '<tr><td>Review</td><td>12</td></tr></table>', 'html',
        )
        table = result['body_content'][0]
        self.assertEqual([node['type'] for node in table['children']], ['caption', 'tbody'])
        self.assertEqual(table['children'][0]['children'], [{'type': 'text', 'text': 'Quoted services'}])
        self.assertEqual(len(table['children'][1]['children']), 2)
        self.assertIn('Quoted services\n', result['body_text'])
        self.assertIsNone(project_email_body('<caption>Orphan caption</caption>', 'html')['body_content'])

    def test_only_explicit_safe_links_survive_and_unsafe_labels_remain(self):
        unsafe = [
            'javascript:alert(1)', 'jav&#x09;ascript:alert(1)', 'data:text/html,attack',
            '//example.test/path', '/relative', 'https://user:pass@example.test/',
            'https://example.test\\@evil.test/', 'https://[broken', 'mailto:missing-address',
            'mailto:help@example.test?subject=Hi%0d%0aBcc:other@example.test',
            'mailto:help%0A@example.test',
        ]
        source = '<p>' + ''.join(f'<a href="{href}" onclick="attack()">Unsafe label</a>' for href in unsafe)
        source += '<a href="https://example.test/report?a=1&amp;b=2">Report</a>'
        source += '<a href="http://example.test/">Reference</a><a href="mailto:sales@example.test">Contact</a></p>'
        result = project_email_body(source, 'html')
        links = [node for node in self.flatten(result['body_content']) if node['type'] == 'a']
        self.assertEqual([link['href'] for link in links], [
            'https://example.test/report?a=1&b=2', 'http://example.test/', 'mailto:sales@example.test',
        ])
        self.assertEqual(result['body_text'].count('Unsafe label'), len(unsafe))
        self.assertTrue(all(set(link) == {'type', 'href', 'children'} for link in links))

    def test_malformed_table_falls_back_without_fabricating_cells_or_losing_text(self):
        for source in [
            '<table><div>Outside row</div><tr><td>Inside cell</td></tr></table>',
            '<tr><td>Orphan cell</td></tr>',
        ]:
            result = project_email_body(source, 'html')
            self.assertIsNone(result['body_content'])
            self.assertIn('cell', result['body_text'])
        corrected = project_email_body('<p>Broken <b>emphasis<p>Next paragraph', 'html')
        self.assertTrue(corrected['body_content'])
        self.assertIn('Broken emphasis', corrected['body_text'])
        self.assertIn('Next paragraph', corrected['body_text'])

    def test_plain_text_is_retained_verbatim_and_empty_html_is_valid(self):
        plain = 'First line\n\n  Indented <script>literal text</script>\nFinal'
        self.assertEqual(project_email_body(plain, 'text'), {'body_text': plain, 'body_content': None})
        self.assertEqual(project_email_body('', 'html'), {'body_text': '', 'body_content': []})

    def test_large_deep_and_many_node_messages_keep_full_text_fallback(self):
        long_text = 'x' * (email_content.MAX_HTML_CHARACTERS + 1)
        large = project_email_body(f'<p>{long_text}</p>', 'html')
        self.assertIsNone(large['body_content'])
        self.assertEqual(large['body_text'], long_text)
        deep = project_email_body('<div>' * 45 + 'Deep content' + '</div>' * 45, 'html')
        self.assertIsNone(deep['body_content'])
        self.assertEqual(deep['body_text'], 'Deep content')
        many = project_email_body('<span>Value</span>' * 3000, 'html')
        self.assertIsNone(many['body_content'])
        self.assertEqual(many['body_text'], 'Value' * 3000)

    def test_network_disabled_parser_and_failure_preserve_safe_text(self):
        original_parser = email_content.html.HTMLParser
        with patch('apps.sales.email_content.html.HTMLParser', wraps=original_parser) as parser:
            result = project_email_body(
                '<!DOCTYPE html SYSTEM "https://example.test/private.dtd"><p>Safe text</p>', 'html',
            )
        self.assertTrue(result['body_content'])
        self.assertTrue(parser.call_args.kwargs['no_network'])
        self.assertFalse(parser.call_args.kwargs['huge_tree'])
        with patch('apps.sales.email_content.html.document_fromstring', side_effect=ValueError('synthetic parse failure')):
            fallback = project_email_body('<p>First</p><p>Second</p>', 'html')
        self.assertIsNone(fallback['body_content'])
        self.assertEqual(fallback['body_text'], 'First\n\nSecond')


@override_settings(ROOT_URLCONF=__name__, SALES_MICROSOFT_GRAPH_BASE_URL='https://graph.microsoft.com/v1.0')
class SalesMailboxBrowsingAPITests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        users = get_user_model()
        self.owner = users.objects.create_user('mail-owner', email='owner@example.test')
        self.other = users.objects.create_user('mail-other', email='other@example.test')
        self.admin = users.objects.create_superuser('mail-admin', email='admin@example.test', password='test')
        for user in [self.owner, self.other, self.admin]:
            grant_sales_actions(user, 'sales_email_intake')
        RolePermission.objects.filter(role__code='sales-test-operator').exclude(permission__action='read').delete()
        self.no_access = users.objects.create_user('mail-no-access', email='denied@example.test')
        UserProfile.objects.get_or_create(
            user=self.no_access,
            defaults={'organization': self.owner.rbac_profile.organization},
        )
        self.connection = SalesMailboxConnection.objects.create(
            name='Synthetic shared sales mailbox', tenant_id='synthetic-tenant', client_id='synthetic-client',
            mailbox_address='sales@example.test', auth_mode='application', enabled=False,
            created_by=self.owner, last_status='not_tested',
        )
        self.list_url = f'/api/v1/sales/mailbox-connections/{self.connection.pk}/messages/'
        self.detail_url = f'/api/v1/sales/mailbox-connections/{self.connection.pk}/message/'
        self.graph_url = 'https://graph.microsoft.com/v1.0/users/sales%40example.test/messages'
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        token = patch.object(SalesMicrosoftGraphService, 'token', return_value='synthetic-token')
        self.token = token.start()
        self.addCleanup(token.stop)
        network = patch('apps.sales.microsoft_graph.requests.request')
        self.network = network.start()
        self.addCleanup(network.stop)
        self.network.return_value = graph_response({'value': [MESSAGE]})

    def assert_private(self, response):
        self.assertIn('private', response['Cache-Control'])
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['Pragma'], 'no-cache')

    def test_guarded_owner_reads_disabled_mailbox_and_detail_without_writes(self):
        before = SalesMailboxConnection.objects.values().get(pk=self.connection.pk)
        listed = self.client.get(self.list_url)
        self.assertEqual(listed.status_code, 200)
        self.assert_private(listed)
        self.network.return_value = graph_response({**MESSAGE, 'body': {'contentType': 'text', 'content': 'Synthetic body'}})
        detail = self.client.get(self.detail_url, {'message_id': MESSAGE['id']})
        self.assertEqual(detail.status_code, 200)
        self.assert_private(detail)
        self.assertEqual(detail.data['body_text'], 'Synthetic body')
        self.assertEqual(before, SalesMailboxConnection.objects.values().get(pk=self.connection.pk))
        self.assertEqual(SalesEmailIntake.objects.count(), 0)
        self.assertTrue(all(call.args[0] == 'GET' for call in self.network.call_args_list))

    def test_guarded_list_and_detail_directions_agree_without_writes_or_extra_reads(self):
        before = SalesMailboxConnection.objects.values().get(pk=self.connection.pk)
        own = {'emailAddress': {'name': 'Synthetic Sales', 'address': 'sales@example.test'}}
        for expected, metadata in (
            ('incoming', {'toRecipients': [own]}),
            ('outgoing', {'from': own, 'toRecipients': [own]}),
            ('draft', {'from': own, 'isDraft': True}),
            ('unknown', {}),
        ):
            with self.subTest(expected=expected):
                payload = {**MESSAGE, **metadata, 'body': {'contentType': 'text', 'content': 'Synthetic body'}}
                self.network.reset_mock()
                self.network.side_effect = [graph_response({'value': [payload]}), graph_response(payload)]
                listed = self.client.get(self.list_url)
                detail = self.client.get(self.detail_url, {'message_id': MESSAGE['id']})
                self.assertEqual(listed.status_code, 200)
                self.assertEqual(detail.status_code, 200)
                self.assertEqual(listed.data['results'][0]['direction'], expected)
                self.assertEqual(detail.data['direction'], expected)
                self.assert_private(listed)
                self.assert_private(detail)
                self.assertEqual(self.network.call_count, 2)
                self.assertTrue(all(call.args[0] == 'GET' for call in self.network.call_args_list))
                self.assertNotIn('to_recipients', listed.data['results'][0])
                self.assertEqual(detail.data['to_recipients'],
                                 [{'name': 'Synthetic Sales', 'email': 'sales@example.test'}]
                                 if metadata.get('toRecipients') else [])
        self.assertEqual(before, SalesMailboxConnection.objects.values().get(pk=self.connection.pk))
        self.assertEqual(SalesEmailIntake.objects.count(), 0)

    def test_module_grant_does_not_allow_another_owners_mail(self):
        self.client.force_authenticate(self.other)
        for endpoint, params in [(self.list_url, {}), (self.detail_url, {'message_id': MESSAGE['id']})]:
            response = self.client.get(endpoint, params)
            self.assertEqual(response.status_code, 404)
            self.assert_private(response)
        self.network.assert_not_called()

    def test_missing_module_and_explicit_denial_block_graph(self):
        self.client.force_authenticate(self.no_access)
        denied = self.client.get(self.list_url)
        self.assertEqual(denied.status_code, 403)
        self.assert_private(denied)
        permission = Permission.objects.filter(module__code='sales_email_intake', action='read').first()
        UserPermissionOverride.objects.create(user_profile=self.admin.rbac_profile, permission=permission, allowed=False)
        self.client.force_authenticate(self.admin)
        denied_admin = self.client.get(self.list_url)
        self.assertEqual(denied_admin.status_code, 403)
        self.assert_private(denied_admin)
        self.network.assert_not_called()

    def test_unauthenticated_reads_are_denied_and_private(self):
        self.client.force_authenticate(None)
        response = self.client.get(self.list_url)
        self.assertIn(response.status_code, {401, 403})
        self.assert_private(response)
        self.network.assert_not_called()

    def test_existing_admin_scope_can_read_system_owned_connection(self):
        self.connection.created_by = None
        self.connection.save(update_fields=['created_by'])
        self.client.force_authenticate(self.admin)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        self.assert_private(response)

    def test_guarded_cursor_is_bound_to_actor_and_expiration(self):
        self.network.return_value = graph_response({'value': [MESSAGE], '@odata.nextLink': self.graph_url + '?$skip=55'})
        first = self.client.get(self.list_url)
        self.assertEqual(first.status_code, 200)
        cursor = first.data['next_cursor']
        self.network.reset_mock()
        self.client.force_authenticate(self.admin)
        cross_actor = self.client.get(self.list_url, {'cursor': cursor})
        self.assertEqual(cross_actor.status_code, 400)
        self.assert_private(cross_actor)
        self.client.force_authenticate(self.owner)
        tampered = self.client.get(self.list_url, {'cursor': cursor + 'tampered'})
        self.assertEqual(tampered.status_code, 400)
        self.assert_private(tampered)
        with patch('django.core.signing.time.time', return_value=1000):
            expired = signing.dumps(
                {'scope': SalesMicrosoftGraphService(self.connection)._mailbox_cursor_scope(self.owner.pk), 'next': self.graph_url + '?$skip=55'},
                salt=SalesMicrosoftGraphService.MAILBOX_CURSOR_SALT,
            )
        with patch('django.core.signing.time.time', return_value=2000):
            response = self.client.get(self.list_url, {'cursor': expired})
        self.assertEqual(response.status_code, 410)
        self.assert_private(response)
        self.network.assert_not_called()

    def test_invalid_queries_and_delegated_connection_do_not_call_graph(self):
        for endpoint, params in [
            (self.list_url, {'nextLink': 'https://example.test/'}),
            (self.list_url, {'mailbox': 'other@example.test'}),
            (self.list_url, {'cursor': ['one', 'two']}),
            (self.detail_url, {}),
            (self.detail_url, {'message_id': '../../users/other/messages/id'}),
        ]:
            response = self.client.get(endpoint, params)
            self.assertEqual(response.status_code, 400)
            self.assert_private(response)
        self.connection.auth_mode = 'delegated'
        self.connection.save(update_fields=['auth_mode'])
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 400)
        self.network.assert_not_called()

    def test_provider_error_and_unexpected_failure_do_not_leak_content(self):
        self.network.return_value = graph_response({'error': {'message': 'sensitive-provider-text'}}, 403)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 502)
        self.assert_private(response)
        self.assertNotIn('sensitive-provider-text', str(response.data))
        with patch.object(SalesMicrosoftGraphService, 'list_messages', side_effect=Exception('sensitive-provider-text')):
            response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 502)
        self.assert_private(response)
        self.assertNotIn('sensitive-provider-text', str(response.data))
