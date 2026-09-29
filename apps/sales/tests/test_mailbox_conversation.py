"""Scoped whole-conversation reads and review revisions; synthetic Graph only."""

from copy import deepcopy
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import uuid4

from django.test import SimpleTestCase, override_settings

from apps.sales.microsoft_graph import SalesMailboxReadError, SalesMicrosoftGraphService
from apps.sales.models import SalesMailboxConnection

from .test_mailbox_browsing import MESSAGE, graph_response


@override_settings(SALES_MICROSOFT_GRAPH_BASE_URL='https://graph.microsoft.com/v1.0')
class MailboxConversationTests(SimpleTestCase):
    def setUp(self):
        self.connection = SalesMailboxConnection(
            id=uuid4(), tenant_id='fixture-tenant', client_id='fixture-app',
            mailbox_address='sales@example.test', auth_mode='application', enabled=False,
        )
        self.service = SalesMicrosoftGraphService(self.connection)
        self.selected = {
            **deepcopy(MESSAGE), 'conversationId': 'fixture-conversation',
            'subject': 'Re: RFT 410005 - Pump station FEED',
            'body': {'contentType': 'text', 'content': 'Please review the original enquiry below.'},
            'toRecipients': [{'emailAddress': {'address': 'sales@example.test'}}],
        }
        self.original = {
            **deepcopy(self.selected), 'id': 'original-message=',
            'subject': 'RFT 410005 - Pump station FEED',
            'sentDateTime': '2026-09-20T08:00:00Z',
            'receivedDateTime': '2026-09-20T08:00:02Z',
            'from': {'emailAddress': {'name': 'Procurement', 'address': 'buyer@customer.test'}},
            'body': {'contentType': 'text', 'content': 'Customer: Coastal Utilities Ltd\nSubmission date: 10 October 2026\nDue date: 15 October 2026'},
        }
        token = patch.object(SalesMicrosoftGraphService, 'token', return_value='synthetic-token')
        token.start()
        self.addCleanup(token.stop)
        network = patch('apps.sales.microsoft_graph.requests.request')
        self.network = network.start()
        self.addCleanup(network.stop)
        self.url = 'https://graph.microsoft.com/v1.0/users/sales%40example.test/messages'
        self.filter = "conversationId eq 'fixture-conversation'"

    def read(self, pages=None, selected=None):
        selected = self.selected if selected is None else selected
        pages = [{'value': [self.selected, self.original]}] if pages is None else pages
        self.network.side_effect = [graph_response(selected), *[graph_response(page) for page in pages]]
        return self.service.get_message(selected['id'])

    def continuation(self, **changes):
        params = {'$filter': self.filter, '$top': '25', '$skip': '41'}
        params.update(changes)
        return self.url + '?' + urlencode(params)

    def assert_partial_selected(self, result):
        analysis = result['extracted_information']['analysis']
        self.assertEqual(analysis['coverage']['status'], 'partial')
        self.assertEqual(analysis['coverage']['messages_reviewed'], 1)
        self.assertTrue(analysis['limitations'])
        self.assertEqual(result['body_text'], self.selected['body']['content'])
        self.assertNotIn('sensitive-provider-text', str(result))

    def test_original_request_is_detected_without_replacing_selected_preview(self):
        result = self.read()
        info = result['extracted_information']
        self.assertEqual(result['subject'], self.selected['subject'])
        self.assertEqual(result['body_text'], self.selected['body']['content'])
        self.assertEqual(info['title'], self.original['subject'])
        self.assertEqual(info['organization_name'], 'Coastal Utilities Ltd')
        self.assertEqual(info['submission_date'], '2026-09-20')
        self.assertEqual(info['stated_submission_date'], '2026-10-10')
        self.assertEqual(info['customer_domain'], 'customer.test')
        self.assertEqual(info['customer_name'], 'Coastal Utilities Ltd')
        self.assertEqual(info['due_date'], '2026-10-15')
        self.assertEqual(info['analysis']['coverage']['status'], 'complete')
        self.assertEqual(info['analysis']['coverage']['messages_reviewed'], 2)
        call = self.network.call_args
        self.assertEqual(call.args, ('GET', self.url))
        self.assertEqual(call.kwargs['params']['$filter'], self.filter)
        self.assertNotIn('$orderby', call.kwargs['params'])
        self.assertFalse(call.kwargs['allow_redirects'])
        self.assertIn('IdType="ImmutableId"', call.kwargs['headers']['Prefer'])
        self.assertFalse(self.connection.enabled)

    def test_changed_subject_reply_uses_selected_headers_not_business_subject(self):
        self.selected['subject'] = 'RFQ updated commercial response'
        self.selected['internetMessageHeaders'] = [
            {'name': 'In-Reply-To', 'value': '<original@customer.test>'},
            {'name': 'X-Private-Route', 'value': 'sensitive-private-header'},
        ]
        result = self.read()
        analysis = result['extracted_information']['analysis']
        selected = next(source for source in analysis['sources'] if source['is_selected'])
        self.assertEqual(result['thread_role'], 'reply')
        self.assertEqual(selected['thread_role_basis'], 'reply_headers')
        self.assertFalse(selected['is_original_request'])
        self.assertEqual(analysis['original_request_source_id'], 'm1-current')
        self.assertNotIn('sensitive-private-header', str(result))
        self.assertNotIn('_thread_metadata', result)
        self.assertEqual(self.network.call_count, 2)
        self.assertTrue(all('internetMessageHeaders' in call.kwargs['params']['$select'] for call in self.network.call_args_list))

    def test_reference_header_alone_supports_reply_and_header_change_invalidates_review(self):
        self.selected['subject'] = 'Commercial response'
        self.selected['internetMessageHeaders'] = [{'name': 'References', 'value': '<first@example.test> <second@example.test>'}]
        first = self.read()
        self.assertEqual(first['thread_role'], 'reply')
        self.selected['internetMessageHeaders'][0]['value'] = '<first@example.test> <third@example.test>'
        second = self.read()
        self.assertNotEqual(first['analysis_source_hash'], second['analysis_source_hash'])
        self.assertNotIn('<third@example.test>', str(second))

    def test_header_order_and_unrelated_headers_do_not_change_review_hash(self):
        self.selected['internetMessageHeaders'] = [
            {'name': 'References', 'value': '<first@example.test>'},
            {'name': 'In-Reply-To', 'value': '<first@example.test>'},
            {'name': 'X-Unrelated', 'value': 'one'},
        ]
        first = self.read()['analysis_source_hash']
        self.selected['internetMessageHeaders'] = list(reversed(self.selected['internetMessageHeaders']))
        self.selected['internetMessageHeaders'][0]['value'] = 'two'
        self.assertEqual(first, self.read()['analysis_source_hash'])

    def test_missing_or_invalid_reply_metadata_cannot_claim_new_message(self):
        self.selected['subject'] = 'Commercial response'
        variants = [None, [], 'invalid', [{'name': 'References', 'value': 'not-a-message-id'}],
                    [{'name': 'In-Reply-To', 'value': '<' + 'x' * 8192 + '>'}],
                    [{'name': 'X-Header', 'value': 'value'}] * 201]
        for headers in variants:
            with self.subTest(metadata_type=type(headers).__name__):
                self.selected['internetMessageHeaders'] = headers
                result = self.read()
                self.assertEqual(result['thread_role'], 'unknown')
                self.assertEqual(result['extracted_information']['analysis']['coverage']['status'], 'complete')
                self.assertNotIn('_thread_metadata', result)

    def test_forward_remains_forward_when_it_also_has_reply_references(self):
        self.selected['subject'] = 'FW: RFQ'
        self.selected['internetMessageHeaders'] = [{'name': 'In-Reply-To', 'value': '<first@example.test>'}]
        self.assertEqual(self.read()['thread_role'], 'forward')

    def test_invalid_parent_references_cannot_confirm_original_but_keep_explicit_fields(self):
        self.selected['subject'] = 'RFQ for survey'
        self.selected['body']['content'] = 'Customer: Willow Works\nPlease submit your quotation.'
        for value in ('malformed-parent-reference', '<' + 'x' * 8192 + '>'):
            with self.subTest(oversized=len(value) > 8192):
                self.selected['internetMessageHeaders'] = [{'name': 'In-Reply-To', 'value': value}]
                result = self.read([{'value': [self.selected]}])
                fields = result['extracted_information']
                self.assertEqual(result['thread_role'], 'unknown')
                self.assertEqual(fields['organization_name'], 'Willow Works')
                self.assertEqual(fields['request_type_code'], 'RFQ')
                self.assertIsNone(fields['analysis']['original_request_source_id'])
                self.assertFalse(fields['analysis']['coverage']['original_identified'])
                self.assertTrue(fields['analysis']['requested_actions'])

    def test_first_incoming_is_selected_when_only_oldest_available_reply_exists(self):
        result = self.read([{'value': [self.selected]}])
        analysis = result['extracted_information']['analysis']
        self.assertEqual(analysis['first_incoming_source_id'], analysis['selected_source_id'])
        self.assertIsNone(analysis['original_request_source_id'])
        self.assertEqual(result['thread_role'], 'reply')

    def test_separate_delegated_sender_affects_authority_and_source_review(self):
        self.selected['body']['content'] = 'Customer: Own sent candidate\nPlease send the final contract.'
        first = self.read([{'value': [self.selected]}])
        self.selected['sender'] = {'emailAddress': {'address': ' SALES@EXAMPLE.TEST '}}
        second = self.read([{'value': [self.selected]}])
        self.assertNotEqual(first['analysis_source_hash'], second['analysis_source_hash'])
        self.assertEqual(second['direction'], 'outgoing')
        self.assertEqual(second['extracted_information']['organization_name'], '')
        self.assertEqual(second['extracted_information']['analysis']['requested_actions'], [])
        self.assertIsNone(second['extracted_information']['analysis']['first_incoming_source_id'])
        self.assertNotIn('_sender_address', second)
        self.selected['sender']['emailAddress']['address'] = 'sales@example.test'
        normalized = self.read([{'value': [self.selected]}])
        self.assertEqual(second['analysis_source_hash'], normalized['analysis_source_hash'])

    def test_full_filtered_continuation_is_preserved_and_original_on_later_page_is_read(self):
        next_link = self.continuation().replace('sales%40example.test', 'sales@example.test')
        result = self.read([
            {'value': [self.selected], '@odata.nextLink': next_link},
            {'value': [self.original]},
        ])
        self.assertEqual(result['extracted_information']['organization_name'], 'Coastal Utilities Ltd')
        self.assertEqual(self.network.call_args.args, ('GET', next_link))
        self.assertIsNone(self.network.call_args.kwargs['params'])
        self.assertEqual(self.network.call_count, 3)

    def test_no_conversation_identifier_keeps_selected_and_quoted_only_coverage(self):
        selected = deepcopy(self.selected)
        selected.pop('conversationId')
        result = self.read([], selected=selected)
        self.assertEqual(self.network.call_count, 1)
        self.assertEqual(result['extracted_information']['analysis']['coverage']['status'], 'selected_only')
        self.assertEqual(result['extracted_information']['submission_date'], '')
        self.assertEqual(result['extracted_information']['due_date'], '')

    def test_bad_conversation_record_cannot_supply_evidence_or_break_selected_preview(self):
        bad_variants = [
            {'conversationId': 'different-conversation'}, {'id': []}, {'id': {}},
            {'id': ''}, {'body': {'content': []}}, {'body': {}}, {'toRecipients': {}},
        ]
        for changed in bad_variants:
            with self.subTest(changed=changed):
                self.network.reset_mock()
                bad = {**deepcopy(self.original), **changed}
                result = self.read([{'value': [self.selected, bad]}])
                self.assert_partial_selected(result)
                self.assertEqual(result['extracted_information']['organization_name'], '')

    def test_continuations_cannot_escape_mailbox_conversation_or_loop(self):
        bad_links = [
            self.continuation().replace('graph.microsoft.com', 'attacker.test'),
            self.continuation().replace('sales%40example.test', 'other%40example.test'),
            self.url + '?$skip=25', self.continuation(**{'$filter': "conversationId eq 'another'"}),
        ]
        for next_link in bad_links:
            with self.subTest(next_link=next_link):
                self.network.reset_mock()
                result = self.read([{'value': [self.selected], '@odata.nextLink': next_link}])
                self.assert_partial_selected(result)
                self.assertEqual(self.network.call_count, 2)
        next_link = self.continuation()
        result = self.read([
            {'value': [self.selected], '@odata.nextLink': next_link},
            {'value': [self.original], '@odata.nextLink': next_link},
        ])
        self.assert_partial_selected(result)

    def test_provider_failure_does_not_leak_raw_error_or_claim_complete_history(self):
        self.network.side_effect = [
            graph_response(self.selected),
            graph_response({'error': {'message': 'sensitive-provider-text'}}, status=503),
        ]
        self.assert_partial_selected(self.service.get_message(self.selected['id']))

    def test_bounds_include_duplicate_record_body_processing_and_page_limits(self):
        with patch.object(self.service, 'CONVERSATION_MAX_CHARACTERS', 100):
            duplicate = deepcopy(self.selected)
            duplicate['body']['content'] = 'x' * 101
            self.assert_partial_selected(self.read([{'value': [duplicate]}]))
        with patch.object(self.service, 'CONVERSATION_MAX_PAGES', 1):
            self.network.reset_mock()
            self.assert_partial_selected(self.read([{'value': [self.selected], '@odata.nextLink': self.continuation()}]))
            self.assertEqual(self.network.call_count, 2)
        with patch.object(self.service, 'CONVERSATION_MAX_MESSAGES', 1):
            self.assert_partial_selected(self.read())

    def test_elapsed_request_budget_cannot_report_complete_coverage(self):
        with patch('apps.sales.microsoft_graph.time.monotonic', side_effect=[0, 0, 21]):
            self.assert_partial_selected(self.read())
        with patch('apps.sales.microsoft_graph.time.monotonic', side_effect=[0, 21]):
            self.network.reset_mock()
            self.assert_partial_selected(self.read([]))
            self.assertEqual(self.network.call_count, 1)

    def test_revision_ignores_order_and_read_flags_but_tracks_other_message_changes(self):
        first = self.read()['analysis_source_hash']
        original_read = {**deepcopy(self.original), 'isRead': True}
        second = self.read([{'value': [original_read, self.selected]}])['analysis_source_hash']
        self.assertEqual(first, second)
        amended = deepcopy(self.original)
        amended['body']['content'] += '\nThe closing date has been extended to 20 October 2026.'
        third = self.read([{'value': [amended, self.selected]}])['analysis_source_hash']
        self.assertNotEqual(first, third)

    def test_provider_must_return_requested_message_identity(self):
        self.network.return_value = graph_response(self.original)
        with self.assertRaises(SalesMailboxReadError):
            self.service.get_message(self.selected['id'])
