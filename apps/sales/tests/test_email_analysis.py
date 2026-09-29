"""Varied synthetic conversation evidence; no mailbox or external AI calls."""

from django.test import SimpleTestCase
from unittest.mock import patch

from apps.sales.email_analysis import analyze_email_conversation
from apps.sales.email_extraction import extract_email_information


def message(identifier, subject, body, sender='buyer@customer.test', **extra):
    return {
        'id': identifier, 'subject': subject, 'body_text': body, 'sender_email': sender,
        'sender_name': 'Synthetic contact', 'sent_at': '2026-09-28T08:00:00Z', **extra,
    }


class EmailConversationAnalysisTests(SimpleTestCase):
    def analyze(self, messages, **kwargs):
        return analyze_email_conversation(
            messages, selected_message_id=messages[-1]['id'] if messages else None,
            mailbox_address='sales@consultant.test', **kwargs,
        )

    def test_thread_role_is_independent_of_selected_business_purpose(self):
        result = self.analyze([
            message('initial', 'RFQ for survey', 'Customer: Orchard Works\nPlease submit a quotation.', sent_at='2026-09-20T08:00:00Z'),
            message('reply', 'Re: RFQ bulletin', 'Please review bulletin 2.', sent_at='2026-09-21T08:00:00Z'),
        ])
        analysis = result['analysis']
        selected = next(source for source in analysis['sources'] if source['is_selected'])
        self.assertEqual(selected['thread_role'], 'reply')
        self.assertEqual(analysis['message_kind'], 'tender_bulletin')
        self.assertEqual(analysis['selected_source_id'], selected['id'])
        self.assertEqual(analysis['first_incoming_source_id'], 'm1-current')
        self.assertEqual(analysis['original_request_source_id'], 'm1-current')

    def test_first_available_incoming_can_be_reply_without_original(self):
        analysis = self.analyze([message('reply', 'Re: RFQ for pumps', 'Thank you for the update.')])['analysis']
        source = analysis['sources'][0]
        self.assertEqual(source['thread_role'], 'reply')
        self.assertTrue(source['is_first_incoming'])
        self.assertFalse(source['is_original_request'])
        self.assertIsNone(analysis['original_request_source_id'])

    def test_changed_subject_reply_header_cannot_be_original_invitation(self):
        analysis = self.analyze([message(
            'reply', 'RFQ: Revised submission', 'Please submit the updated proposal.',
            _thread_metadata={'headers_available': True, 'in_reply_to': ['<prior@example.test>'], 'references': []},
        )])['analysis']
        self.assertEqual(analysis['sources'][0]['thread_role'], 'reply')
        self.assertEqual(analysis['sources'][0]['thread_role_basis'], 'reply_headers')
        self.assertIsNone(analysis['original_request_source_id'])

    def test_known_headerless_new_message_is_not_claimed_as_thread_first_from_list(self):
        analysis = self.analyze([message(
            'first', 'Hello', 'Hello there.',
            _thread_metadata={'headers_available': True, 'in_reply_to': [], 'references': []},
        )])['analysis']
        self.assertEqual(analysis['sources'][0]['thread_role'], 'new_message')
        self.assertIsNone(analysis['original_request_source_id'])

    def test_unknown_sender_and_drafts_cannot_establish_first_or_original(self):
        for changes in ({'sender_email': ''}, {'sender_email': 'not-an-address'}, {'is_draft': True}):
            with self.subTest(changes=changes):
                analysis = self.analyze([message('item', 'RFQ for works', 'Please submit a quotation.', **changes)])['analysis']
                self.assertIsNone(analysis['first_incoming_source_id'])
                self.assertIsNone(analysis['original_request_source_id'])
                self.assertIn(analysis['sources'][0]['direction'], {'unknown', 'draft'})

    def test_missing_or_tied_actual_timestamps_do_not_invent_first(self):
        for stamp in ('', '2026-09-28T08:00:00Z'):
            with self.subTest(stamp=stamp):
                analysis = self.analyze([
                    message('a', 'RFQ', 'Please submit a quotation.'),
                    message('b', 'Re: RFQ', 'Thank you.', sent_at=stamp),
                ])['analysis']
                self.assertIsNone(analysis['first_incoming_source_id'])
                self.assertFalse(any(source['is_first_incoming'] for source in analysis['sources']))

    def test_indented_folded_outlook_headers_preserve_original_and_unknown_timezone(self):
        result = self.analyze([message(
            'reply', 'Re: RFQ for works',
            'We are reviewing.\n\n    From: Buyer <buyer@customer.test>\n'
            '    Sent: Monday, September 21, 2026 8:00 AM\n    To: Sales\n'
            '        <sales@consultant.test>\n    Subject: RFQ for works\n\n'
            'Customer: Willow Infrastructure\nPlease submit your quotation.',
        )])
        analysis = result['analysis']
        quoted = next(source for source in analysis['sources'] if source['origin'] == 'quoted')
        self.assertEqual(quoted['sender_email'], 'buyer@customer.test')
        self.assertEqual(quoted['sent_at'], '')
        self.assertEqual(quoted['chronology_basis'], 'quoted_order')
        self.assertEqual(analysis['original_request_source_id'], quoted['id'])
        self.assertFalse(quoted['is_first_incoming'])
        self.assertEqual(result['extracted_information']['organization_name'], 'Willow Infrastructure')

    def test_multiline_gmail_reply_marker_splits_current_and_original(self):
        result = self.analyze([message(
            'reply', 'RFQ for works',
            'We will review.\nOn Monday, September 21, 2026\nat 8:00 AM Buyer\n'
            '<buyer@customer.test> wrote:\n> Customer: Cedar Engineering\n> Please submit your quotation.',
            sender='sales@consultant.test',
        )])
        sources = result['analysis']['sources']
        self.assertEqual(next(source for source in sources if source['is_selected'])['thread_role'], 'reply')
        self.assertEqual(result['extracted_information']['organization_name'], 'Cedar Engineering')
        self.assertTrue(result['analysis']['coverage']['original_identified'])

    def test_bare_quotes_do_not_borrow_parent_sender_or_prove_original(self):
        analysis = self.analyze([message(
            'reply', 'Re: RFQ', 'Please review.\n> Please submit your quotation.\n> Customer: Unknown quoted party',
        )])['analysis']
        quote = next(source for source in analysis['sources'] if source['origin'] == 'quoted')
        self.assertEqual(quote['sender_email'], '')
        self.assertEqual(quote['direction'], 'unknown')
        self.assertIsNone(analysis['original_request_source_id'])

    def test_same_text_from_different_senders_is_not_deduplicated(self):
        body = 'Please submit your quotation.'
        result = self.analyze([
            message('original', 'RFQ', body),
            message('reply', 'Re: RFQ', 'Thanks.\n-----Original Message-----\n'
                    'From: Different <different@customer.test>\nSent: Mon, 28 Sep 2026 08:00:00 +0000\n'
                    'To: sales@consultant.test\nSubject: RFQ\n\n' + body),
        ])
        self.assertEqual(result['analysis']['coverage']['segments_reviewed'], 3)

    def test_quote_without_subject_cannot_inherit_request_identity_from_parent(self):
        result = self.analyze([message(
            'reply', 'Re: RFQ for works', 'Thanks.\n-----Original Message-----\n'
            'From: Buyer <buyer@customer.test>\nSent: Monday, September 21, 2026 8:00 AM\n'
            'To: sales@consultant.test\n\nThank you for your update.',
        )])
        self.assertIsNone(result['analysis']['original_request_source_id'])
        self.assertFalse(result['analysis']['coverage']['original_identified'])

    def test_repeated_naive_dated_quotes_do_not_exhaust_limit_or_drop_selected_actual(self):
        quote = ('\n-----Original Message-----\nFrom: Buyer <buyer@customer.test>\n'
                 'Sent: Monday, September 21, 2026 8:00 AM\nTo: sales@consultant.test\n'
                 'Subject: RFQ for works\n\nCustomer: Rowan Works\nPlease submit your quotation.')
        messages = [message(str(index), 'Re: RFQ for works', 'Current reply.' + quote * 20,
                            sent_at=f'2026-09-{22 + index:02d}T08:00:00Z') for index in range(6)]
        with patch('apps.sales.email_analysis.MAX_SEGMENTS', 8):
            result = self.analyze(messages)
        analysis = result['analysis']
        self.assertEqual(analysis['coverage']['status'], 'complete')
        self.assertEqual(analysis['coverage']['messages_reviewed'], 6)
        self.assertEqual(analysis['coverage']['segments_reviewed'], 7)
        self.assertEqual(analysis['selected_source_id'], 'm6-current')
        self.assertEqual(sum(source['is_selected'] for source in analysis['sources']), 1)
        quote_source = next(source for source in analysis['sources'] if source['origin'] == 'quoted')
        self.assertEqual(quote_source['sent_at'], '')
        self.assertEqual(quote_source['chronology_basis'], 'quoted_order')
        self.assertNotIn('_raw_sent_at', str(analysis))

    def test_naive_quotes_with_different_date_or_sender_are_not_merged(self):
        def quote(sender, day):
            return ('\n-----Original Message-----\nFrom: Buyer <' + sender + '>\n'
                    f'Sent: September {day}, 2026 8:00 AM\nTo: sales@consultant.test\n'
                    'Subject: RFQ for works\n\nPlease submit your quotation.')
        body = 'Current reply.' + quote('one@customer.test', 21) + quote('one@customer.test', 22) + quote('two@customer.test', 21)
        analysis = self.analyze([message('reply', 'Re: RFQ for works', body)])['analysis']
        self.assertEqual(len([source for source in analysis['sources'] if source['origin'] == 'quoted']), 3)

    def test_undated_and_oversized_date_quotes_are_not_guessed_as_duplicates(self):
        for date_text in ('', 'Unknown date', 'September 21, 2026 ' + 'x' * 512):
            with self.subTest(date_kind='oversized' if len(date_text) > 512 else date_text):
                quote = ('\n-----Original Message-----\nFrom: Buyer <buyer@customer.test>\n'
                         f'Sent: {date_text}\nTo: sales@consultant.test\nSubject: RFQ\n\nSame quoted words.')
                analysis = self.analyze([message('reply', 'Re: RFQ', 'Thanks.' + quote * 2)])['analysis']
                self.assertEqual(len([source for source in analysis['sources'] if source['origin'] == 'quoted']), 2)

    def test_naive_quote_is_not_merged_with_actual_aware_message(self):
        body = 'Customer: Delta Works\nPlease submit your quotation.'
        result = self.analyze([
            message('original', 'RFQ', body, sent_at='2026-09-21T08:00:00Z'),
            message('reply', 'Re: RFQ', 'Thanks.\n-----Original Message-----\n'
                    'From: Buyer <buyer@customer.test>\nSent: September 21, 2026 8:00 AM\n'
                    'To: sales@consultant.test\nSubject: RFQ\n\n' + body),
        ])
        self.assertEqual(result['analysis']['coverage']['segments_reviewed'], 3)

    def test_actual_message_preferred_over_matching_quote_even_if_supplied_later(self):
        body = 'Customer: Delta Works Ltd\nPlease submit your proposal.'
        result = self.analyze([
            message('reply', 'Re: RFP', 'Thanks.\n-----Original Message-----\n'
                    'From: Buyer <buyer@customer.test>\nSent: Mon, 28 Sep 2026 08:00:00 +0000\n'
                    'To: sales@consultant.test\nSubject: RFP\n\n' + body),
            message('original', 'RFP', body),
        ])
        self.assertEqual(result['analysis']['coverage']['segments_reviewed'], 2)
        self.assertTrue(all(source['origin'] == 'message' for source in result['analysis']['sources']))

    def test_original_incoming_request_survives_internal_reply(self):
        result = self.analyze([
            message('request', 'RFP-2026-101 | Water network design',
                    'Customer: Rivermark Water Ltd\nPlease submit your proposal by 21 October 2026.\n'
                    'Scope summary: Detailed engineering of the distribution network.'),
            message('reply', 'Re: RFP-2026-101 | Water network design',
                    'Thank you. We are reviewing this internally.', sender='sales@consultant.test'),
        ])
        fields, analysis = result['extracted_information'], result['analysis']
        self.assertEqual(fields['organization_name'], 'Rivermark Water Ltd')
        self.assertEqual(fields['title'], 'RFP-2026-101 | Water network design')
        self.assertEqual(fields['request_type_code'], 'RFP')
        self.assertEqual(fields['due_date'], '2026-10-21')
        self.assertEqual(analysis['message_kind'], 'reply')
        self.assertTrue(analysis['coverage']['original_identified'])
        self.assertIn('Rivermark Water Ltd', analysis['summary'])
        self.assertIn('distribution network', analysis['summary'])
        self.assertEqual(analysis['coverage']['messages_reviewed'], 2)

    def test_forwarded_outlook_chain_finds_original_customer_and_date(self):
        body = (
            'Please review the enquiry below.\n\n-----Original Message-----\n'
            'From: Tender Desk <tenders@harbor.test>\nSent: Mon, 28 Sep 2026 08:00:00 +0000\n'
            'To: Sales <sales@consultant.test>\nSubject: ITT-203 | Port extension\n\n'
            'Harbor Infrastructure PLC invites you to participate.\n'
            'Tenders close on Friday, 23 October 2026.\nPlease confirm your intention to bid.'
        )
        data = extract_email_information(
            subject='FW: ITT-203 | Port extension', body_text=body,
            sender_email='engineer@consultant.test', mailbox_address='sales@consultant.test',
            coverage={'status': 'saved_content'},
        )
        self.assertEqual(data['organization_name'], 'Harbor Infrastructure PLC')
        self.assertEqual(data['due_date'], '2026-10-23')
        self.assertEqual(data['request_type_code'], 'ITT')
        self.assertEqual(data['title'], 'ITT-203 | Port extension')
        self.assertTrue(data['analysis']['coverage']['original_identified'])
        self.assertEqual(data['analysis']['coverage']['status'], 'saved_content')
        self.assertEqual(data['analysis']['message_kind'], 'forward')
        self.assertTrue(any('confirm your intention' in item['text'] for item in data['analysis']['requested_actions']))
        self.assertTrue(any(source['origin'] == 'quoted' for source in data['analysis']['sources']))

    def test_gmail_quoted_reply_uses_original_body_without_fabricated_dates(self):
        data = extract_email_information(
            subject='Re: EOI | Network services', sender_email='sales@consultant.test',
            mailbox_address='sales@consultant.test',
            body_text='Received with thanks.\nOn Mon, 28 Sep 2026, Buyer <buyer@city.test> wrote:\n'
                      '> Customer: City Services Corporation\n> Expression of interest for network services.\n'
                      '> Please provide your credentials.\n',
        )
        self.assertEqual(data['organization_name'], 'City Services Corporation')
        self.assertEqual(data['request_type_code'], 'EOI')
        self.assertEqual(data['due_date'], '')
        self.assertEqual(data['submission_date'], '2026-09-28')
        self.assertEqual(data['stated_submission_date'], '')
        self.assertTrue(data['analysis']['coverage']['original_identified'])

    def test_portal_notification_organization_and_attachment_only_bulletin(self):
        result = self.analyze([
            message('invitation', 'RFQ-702 | Asset inspection',
                    'You are receiving this because your customer, Meridian Utilities LLC, has identified your company for this event.\n'
                    'Meridian Utilities LLC sourcing site, Event RFQ-702.\nPlease submit a quotation.'),
            message('bulletin', 'RFQ-702 | Bulletin 2',
                    'Please review the updated documents available on the sourcing portal.\n'
                    'Please acknowledge receipt of this bulletin.', has_attachments=False),
        ])
        fields, analysis = result['extracted_information'], result['analysis']
        self.assertEqual(fields['organization_name'], 'Meridian Utilities LLC')
        self.assertEqual(fields['request_type_code'], 'RFQ')
        self.assertEqual(analysis['message_kind'], 'tender_bulletin')
        self.assertEqual(fields['due_date'], '')
        self.assertEqual(fields['submission_date'], '2026-09-28')
        self.assertEqual(fields['stated_submission_date'], '')
        self.assertIn('bulletin', analysis['summary'])
        self.assertTrue(any('were not read' in item for item in analysis['limitations']))
        self.assertTrue(any('acknowledge' in item['text'] for item in analysis['requested_actions']))
        self.assertTrue(any('deadline' in item['text'] for item in analysis['suggested_actions']))

    def test_latest_explicit_deadline_revision_supersedes_old_date(self):
        result = self.analyze([
            message('initial', 'RFT-301 | Cooling system', 'Client: Polar Engineering Ltd\nDue date: 12 October 2026'),
            message('update', 'Re: RFT-301 | Deadline update', 'The submission deadline has been extended from 12 October 2026 to 26 October 2026.'),
            message('ack', 'Re: RFT-301 | Cooling system', 'Thank you for confirming.', sender='sales@consultant.test'),
        ])
        fields = result['extracted_information']
        self.assertEqual(fields['due_date'], '2026-10-26')
        self.assertEqual(fields['deadline_date'], '2026-10-26')
        self.assertEqual(fields['field_sources']['due_date'], ['m2-current'])
        self.assertIn('explicitly revised', result['analysis']['summary'])

    def test_requested_extension_is_not_an_approved_deadline_change(self):
        result = self.analyze([
            message('initial', 'RFT for station design', 'Due date: 12 October 2026'),
            message('request', 'Re: RFT for station design', 'We request the deadline extended to 26 October 2026.', sender='sales@consultant.test'),
        ])
        self.assertEqual(result['extracted_information']['due_date'], '2026-10-12')

    def test_conflicting_dates_without_explicit_revision_are_unresolved(self):
        result = self.analyze([
            message('initial', 'RFQ for turbines', 'Due date: 2026-10-12'),
            message('different', 'Re: RFQ for turbines', 'Due date: 2026-10-19'),
        ])
        self.assertEqual(result['extracted_information']['due_date'], '')
        self.assertTrue(result['extracted_information']['warnings'])
        self.assertEqual(len(result['extracted_information']['field_sources']['due_date']), 2)

    def test_ambiguous_revision_does_not_restore_superseded_original(self):
        result = self.analyze([
            message('initial', 'RFQ for pumps', 'Due date: 2026-10-12'),
            message('update', 'Re: RFQ for pumps', 'Revised deadline: 03/04/2026'),
        ])
        self.assertEqual(result['extracted_information']['due_date'], '')
        self.assertEqual(result['extracted_information']['due_date_text'], '03/04/2026')
        self.assertTrue(result['extracted_information']['warnings'])

    def test_quoted_original_is_deduplicated_against_actual_message(self):
        original_body = 'Customer: Delta Works Ltd\nDue date: 2026-10-23\nPlease submit your proposal.'
        reply = (
            'We will review.\n-----Original Message-----\nFrom: Buyer <buyer@customer.test>\n'
            'Sent: Mon, 28 Sep 2026 08:00:00 +0000\nTo: sales@consultant.test\n'
            'Subject: RFP for works\n\n' + original_body
        )
        result = self.analyze([
            message('first', 'RFP for works', original_body),
            message('reply', 'Re: RFP for works', reply, sender='sales@consultant.test'),
        ])
        self.assertEqual(result['analysis']['coverage']['segments_reviewed'], 2)
        self.assertEqual(result['extracted_information']['field_sources']['due_date'], ['m1-current'])
        self.assertEqual(len([item for item in result['analysis']['requested_actions'] if 'submit your proposal' in item['text']]), 1)

    def test_customer_is_not_internal_forwarder_or_sender_domain(self):
        result = self.analyze([
            message('original', 'RFT | Rail survey', 'Customer: North Rail Corporation'),
            message('forward', 'FW: RFT | Rail survey', 'Customer: Internal Consulting LLC', sender='sales@consultant.test'),
        ])
        self.assertEqual(result['extracted_information']['organization_name'], 'North Rail Corporation')
        missing = self.analyze([message('unknown', 'Hello', 'Thank you.', sender='person@famous-business.test')])
        self.assertEqual(missing['extracted_information']['organization_name'], '')

    def test_original_sent_date_is_submission_date_and_stated_date_is_separate(self):
        result = self.analyze([message(
            'original', 'RFP for controls', 'Submission date: 2026-10-01\nProposal deadline: 2026-10-20',
            received_at='2026-09-29T09:00:00Z',
        )])
        fields = result['extracted_information']
        self.assertEqual(fields['submission_date'], '2026-09-28')
        self.assertEqual(fields['stated_submission_date'], '2026-10-01')
        self.assertEqual(fields['due_date'], '2026-10-20')
        self.assertEqual(fields['expected_award_date'], '')
        self.assertEqual(fields['field_sources']['submission_date'], ['m1-current'])
        self.assertEqual(fields['customer_domain'], 'customer.test')
        self.assertEqual(fields['customer_name'], 'Customer')
        self.assertEqual(fields['intelligence']['customer_name']['basis'], 'domain_label')
        self.assertEqual(fields['organization_name'], '')

    def test_missing_sent_date_does_not_use_received_or_stated_submission_date(self):
        fields = self.analyze([message(
            'original', 'Request for quotation', 'Submission date: 2026-10-01\nPlease submit a quotation.',
            sent_at='', received_at='2026-09-29T09:00:00Z',
        )])['extracted_information']
        self.assertEqual(fields['submission_date'], '')
        self.assertEqual(fields['stated_submission_date'], '2026-10-01')

    def test_missing_original_and_partial_coverage_are_explicit(self):
        result = self.analyze([
            message('reply', 'Re: RFQ-901', 'Thank you for your update.', sender='sales@consultant.test'),
        ], coverage={'status': 'partial', 'reason': 'Earlier conversation messages were unavailable.'})
        analysis = result['analysis']
        self.assertFalse(analysis['coverage']['original_identified'])
        self.assertEqual(analysis['coverage']['status'], 'partial')
        self.assertIn('Earlier conversation messages were unavailable.', analysis['limitations'])
        self.assertTrue(any('original incoming request' in limitation for limitation in analysis['limitations']))

    def test_source_references_are_real_and_suggestions_are_separate(self):
        result = self.analyze([message(
            'original', 'EOI for civil works', 'Orchard Development Ltd invites you to participate.\nPlease provide your credentials.\nSee attached scope.',
        )])
        analysis = result['analysis']
        source_ids = {source['id'] for source in analysis['sources']}
        for collection in ('key_points', 'requested_actions', 'suggested_actions'):
            for item in analysis[collection]:
                self.assertTrue(set(item['source_ids']) <= source_ids)
        self.assertTrue(any('provide your credentials' in action['text'] for action in analysis['requested_actions']))
        self.assertTrue(any('Review the referenced' in action['text'] for action in analysis['suggested_actions']))
        self.assertTrue(all(source['origin'] in {'message', 'quoted'} for source in analysis['sources']))

    def test_old_original_below_large_reply_is_parsed_before_field_limit(self):
        data = extract_email_information(
            subject='FW: RFQ for lighting', sender_email='sales@consultant.test',
            mailbox_address='sales@consultant.test',
            body_text='x' * 250_001 + '\n-----Original Message-----\n'
                      'From: Buyer <buyer@light.test>\nSent: Mon, 28 Sep 2026 08:00:00 +0000\n'
                      'To: sales@consultant.test\nSubject: RFQ for lighting\n\nCustomer: Lumen Services LLC\nDue date: 2026-11-20',
        )
        self.assertEqual(data['organization_name'], 'Lumen Services LLC')
        self.assertEqual(data['due_date'], '2026-11-20')
        self.assertEqual(data['analysis']['coverage']['status'], 'partial')

    def test_bounded_many_messages_and_empty_input_remain_honest(self):
        result = self.analyze([message(str(index), f'Re: enquiry {index}', 'Thanks') for index in range(102)])
        self.assertEqual(result['analysis']['coverage']['status'], 'partial')
        self.assertEqual(result['analysis']['coverage']['messages_reviewed'], 100)
        empty = analyze_email_conversation([])
        self.assertEqual(empty['analysis']['coverage']['messages_reviewed'], 0)
        self.assertFalse(empty['analysis']['coverage']['original_identified'])
        self.assertEqual(empty['extracted_information']['organization_name'], '')

    def test_same_customer_pattern_handles_different_organizations(self):
        for company in ('Westhaven Logistics BV', 'Summit Process Industries Inc', 'Kestrel Renewables SA'):
            with self.subTest(company=company):
                result = self.analyze([message('request', 'RFQ for specialist services',
                    f'You are receiving this because your customer, {company}, has identified you as a participant.')])
                self.assertEqual(result['extracted_information']['organization_name'], company)

    def test_old_dated_quote_cannot_supersede_later_actual_deadline_update(self):
        result = self.analyze([
            message('initial', 'RFT for bridges', 'Due date: 15 January 2026', sent_at='2026-01-01T08:00:00Z'),
            message('new-update', 'Re: RFT for bridges', 'The deadline has been extended to 25 January 2026.', sent_at='2026-01-05T08:00:00Z'),
            message('reply', 'Re: RFT for bridges',
                    'Thanks.\n-----Original Message-----\nFrom: Buyer <buyer@customer.test>\n'
                    'Sent: Sat, 3 Jan 2026 08:00:00 +0000\nTo: sales@consultant.test\n'
                    'Subject: Re: RFT for bridges\n\nThe deadline has been extended to 20 January 2026.',
                    sender='sales@consultant.test', sent_at='2026-01-06T08:00:00Z'),
        ])
        self.assertEqual(result['extracted_information']['due_date'], '2026-01-25')
        self.assertEqual(result['extracted_information']['field_sources']['due_date'], ['m2-current'])

    def test_undated_conflicting_quote_does_not_claim_latest_revision(self):
        result = self.analyze([
            message('initial', 'RFT for bridges', 'Due date: 15 January 2026', sent_at='2026-01-01T08:00:00Z'),
            message('new-update', 'Re: RFT for bridges', 'The deadline has been extended to 25 January 2026.', sent_at='2026-01-05T08:00:00Z'),
            message('reply', 'Re: RFT for bridges',
                    'Thanks.\nOn a previous day, Buyer <buyer@customer.test> wrote:\n'
                    '> The deadline has been extended to 20 January 2026.',
                    sender='sales@consultant.test', sent_at='2026-01-06T08:00:00Z'),
        ])
        self.assertEqual(result['extracted_information']['due_date'], '')
        self.assertTrue(result['extracted_information']['warnings'])
        self.assertIn('m2-current', result['extracted_information']['field_sources']['due_date'])
        self.assertIn('m3-quoted-1', result['extracted_information']['field_sources']['due_date'])
        self.assertIn('25 January 2026', result['extracted_information']['evidence']['due_date'])
        self.assertIn('20 January 2026', result['extracted_information']['evidence']['due_date'])

    def test_undated_quoted_revision_cannot_override_plain_dated_deadline(self):
        result = self.analyze([
            message('initial', 'RFT for bridges', 'Due date: 15 January 2026', sent_at='2026-01-01T08:00:00Z'),
            message('dated-deadline', 'Re: RFT for bridges', 'Due date: 25 January 2026', sent_at='2026-01-05T08:00:00Z'),
            message('reply', 'Re: RFT for bridges',
                    'Thanks.\n-----Original Message-----\nFrom: Buyer <buyer@customer.test>\n'
                    'Sent: Unknown date\nTo: sales@consultant.test\nSubject: Re: RFT for bridges\n\n'
                    'The deadline has been extended to 20 January 2026.',
                    sender='sales@consultant.test', sent_at='2026-01-06T08:00:00Z'),
        ])
        fields, analysis = result['extracted_information'], result['analysis']
        self.assertEqual(fields['due_date'], '')
        self.assertTrue(fields['warnings'])
        self.assertIn('m2-current', fields['field_sources']['due_date'])
        self.assertIn('m3-quoted-1', fields['field_sources']['due_date'])
        self.assertIn('25 January 2026', fields['evidence']['due_date'])
        self.assertIn('20 January 2026', fields['evidence']['due_date'])
        self.assertFalse(any(point['label'] == 'Current due date' for point in analysis['key_points']))

    def test_sole_reply_or_forward_instruction_does_not_identify_original(self):
        for prefix in ('Re:', 'FW:'):
            with self.subTest(prefix=prefix):
                result = self.analyze([message(
                    'follow-up', f'{prefix} RFQ for pumping services', 'Please submit the revised proposal.',
                )])
                self.assertFalse(result['analysis']['coverage']['original_identified'])
                self.assertEqual(result['extracted_information']['request_type_code'], 'RFQ')
                self.assertTrue(any('original incoming request' in text for text in result['analysis']['limitations']))
                self.assertTrue(result['analysis']['requested_actions'])

    def test_reply_instruction_preserves_distinct_quoted_original_invitation(self):
        result = self.analyze([message(
            'follow-up', 'Re: RFQ for pumping services',
            'Please submit the revised proposal.\n-----Original Message-----\n'
            'From: Buyer <buyer@customer.test>\nSent: Thu, 1 Jan 2026 08:00:00 +0000\n'
            'To: sales@consultant.test\nSubject: RFQ for pumping services\n\n'
            'Customer: Meadow Utilities Ltd\nYou are invited to submit your quotation.',
        )])
        self.assertTrue(result['analysis']['coverage']['original_identified'])
        self.assertEqual(result['extracted_information']['field_sources']['title'], ['m1-quoted-1'])
        self.assertEqual(result['extracted_information']['organization_name'], 'Meadow Utilities Ltd')

    def test_five_display_fields_come_from_original_request_not_later_reply(self):
        result = self.analyze([message(
            'follow-up', 'Re: Q-10597 RFQ for engineering support',
            'Customer: Internal Coordination LLC\n'
            'Please use the RFP response template for our review.\n\n'
            '-----Original Message-----\n'
            'From: Procurement Contact <procurement@northstar-eng.com>\n'
            'Sent: Wednesday, September 23, 2026 10:04 AM +0400\n'
            'To: Sales <sales@consultant.test>\n'
            'Subject: Q-10597 RFQ for engineering support\n\n'
            'We invite you to submit your competitive proposal against the attached RFQ.\n'
            'Submission Deadline: 28 September 2026\n'
            'Please confirm your interest within two days.',
            sender='coordinator@consultant.test',
        )])
        fields, analysis = result['extracted_information'], result['analysis']
        self.assertEqual(analysis['original_request_source_id'], 'm1-quoted-1')
        self.assertEqual(fields['title'], 'Q-10597 RFQ for engineering support')
        self.assertEqual(fields['customer_name'], 'Northstar Engineering')
        self.assertEqual(fields['customer_domain'], 'northstar-eng.com')
        self.assertEqual(fields['organization_name'], '')
        self.assertEqual(fields['submission_date'], '2026-09-23')
        self.assertEqual(fields['due_date'], '2026-09-28')
        self.assertEqual(fields['request_type_code'], 'RFQ')
        self.assertEqual(fields['field_sources']['request_type_code'], ['m1-quoted-1'])
        self.assertEqual(fields['intelligence']['customer_name']['basis'], 'domain_label')

    def test_bulletin_imperative_does_not_claim_original_request_is_present(self):
        result = self.analyze([message(
            'bulletin', 'RFQ-100 bulletin 2', 'Please submit the revised proposal by 28 January 2026.',
        )])
        self.assertEqual(result['analysis']['message_kind'], 'tender_bulletin')
        self.assertFalse(result['analysis']['coverage']['original_identified'])
        self.assertTrue(any('original incoming request' in text for text in result['analysis']['limitations']))

    def test_salutation_is_not_part_of_customer_name(self):
        result = self.analyze([message(
            'request', 'Request for quotation',
            'Dear supplier, Alder Energy invites you to submit your quotation by 15 January 2026.',
        )])
        self.assertEqual(result['extracted_information']['organization_name'], 'Alder Energy')

    def test_invitation_sentence_is_not_misidentified_as_customer_name(self):
        result = self.analyze([message(
            'request', 'RFQ for engineering support',
            'We are pleased to inform you that the project was awarded to us, and we would like to '
            'invite you to submit a competitive proposal against the attached RFQ.',
            sender='procurement@northstar-eng.com',
        )])
        fields = result['extracted_information']
        self.assertEqual(fields['organization_name'], '')
        self.assertEqual(fields['customer_name'], 'Northstar Engineering')
        self.assertEqual(fields['customer_domain'], 'northstar-eng.com')
        self.assertEqual(fields['intelligence']['customer_name']['basis'], 'domain_label')

    def test_short_named_organization_before_invitation_is_retained(self):
        fields = self.analyze([message(
            'request', 'RFQ for inspection', 'ADNOC invites you to submit your quotation.',
            sender='procurement@adnoc.ae',
        )])['extracted_information']
        self.assertEqual(fields['organization_name'], 'ADNOC')
        self.assertEqual(fields['customer_name'], 'ADNOC')
        self.assertEqual(fields['intelligence']['customer_name']['basis'], 'explicit_organization')

    def test_unsent_draft_cannot_change_customer_deadline_or_requested_actions(self):
        result = analyze_email_conversation([
            message('initial', 'RFT for structures', 'Customer: Eastbank Civil Ltd\nDue date: 15 January 2026', sent_at='2026-01-01T08:00:00Z'),
            message('draft', 'Re: RFT for structures',
                    'Customer: Draft-only Company\nThe deadline has been extended to 30 January 2026.\nPlease send the contract.',
                    sender='sales@consultant.test', sent_at='2026-01-04T08:00:00Z', is_draft=True),
        ], selected_message_id='initial', mailbox_address='sales@consultant.test')
        self.assertEqual(result['extracted_information']['due_date'], '2026-01-15')
        self.assertEqual(result['extracted_information']['organization_name'], 'Eastbank Civil Ltd')
        self.assertFalse(any('send the contract' in item['text'] for item in result['analysis']['requested_actions']))
        self.assertTrue(any('Unsent draft' in text for text in result['analysis']['limitations']))
        self.assertTrue(any(source['label'].startswith('Draft') for source in result['analysis']['sources']))

    def test_explicit_customer_from_same_domain_different_sender_is_not_discarded(self):
        result = analyze_email_conversation([message(
            'selected', 'Synthetic engineering enquiry',
            'Customer name: Reviewed Company\nDue date: 21 November 2026', sender='client@example.test',
        )], selected_message_id='selected', mailbox_address='sales@example.test')
        self.assertEqual(result['extracted_information']['organization_name'], 'Reviewed Company')
        self.assertEqual(result['extracted_information']['due_date'], '2026-11-21')

    def test_superseded_dated_submission_action_is_not_presented_as_current(self):
        result = self.analyze([
            message('initial', 'RFT for restoration',
                    'Customer: Redfern Estates Ltd\nPlease submit your proposal by 15 October 2026.',
                    sent_at='2026-10-01T08:00:00Z'),
            message('update', 'Re: RFT for restoration', 'The deadline has been extended to 20 October 2026.',
                    sent_at='2026-10-05T08:00:00Z'),
        ])
        analysis = result['analysis']
        self.assertEqual(result['extracted_information']['due_date'], '2026-10-20')
        self.assertFalse(any('15 October' in item['text'] for item in analysis['requested_actions']))
        self.assertNotIn('15 October', analysis['summary'])
        self.assertTrue(any('15 October' in item['excerpt'] for item in analysis['sources']))

    def test_bulletin_obligation_and_portal_notice_are_explained_from_source(self):
        notice = 'Please be advised that any impact arising from this bulletin shall be considered in your future bid submissions.'
        result = self.analyze([
            message('original', 'RFQ-845 | Controls replacement',
                    'You are receiving this because your customer, Cedar Process SA, has identified your company.\n'
                    'Cedar Process SA sourcing site, Event RFQ-845\n'
                    'The scope includes replacement of the control cabinets.'),
            message('bulletin', 'RFQ-845 | Bulletin 3', notice + '\nThe revised documents are available on the sourcing portal.'),
        ])
        fields, analysis = result['extracted_information'], result['analysis']
        self.assertEqual(fields['organization_name'], 'Cedar Process SA')
        self.assertEqual(fields['tender_reference'], 'RFQ-845')
        self.assertEqual(fields['scope_summary'], 'replacement of the control cabinets.')
        self.assertEqual(fields['due_date'], '')
        self.assertIn('impact arising from this bulletin', analysis['summary'])
        self.assertTrue(any(item['label'] == 'Notice' and item['value'] == notice for item in analysis['key_points']))
        self.assertTrue(any(item['text'] == notice for item in analysis['requested_actions']))
        self.assertTrue(any('document contents were not read' in text for text in analysis['limitations']))

    def test_general_must_and_required_obligations_keep_exact_evidence(self):
        result = self.analyze([message(
            'original', 'RFP for coastal assessment',
            'Issued by: Bluewater Research Institute\n'
            'Bidders must include the technical schedule in their response.\n'
            'Applicants are required to provide evidence of comparable work.',
        )])
        actions = [item['text'] for item in result['analysis']['requested_actions']]
        self.assertIn('Bidders must include the technical schedule in their response.', actions)
        self.assertIn('Applicants are required to provide evidence of comparable work.', actions)
        self.assertEqual(result['extracted_information']['organization_name'], 'Bluewater Research Institute')

    def test_original_request_type_is_not_reclassified_by_a_reply_subject(self):
        result = self.analyze([
            message('initial', 'EOI for regional study', 'Please provide your credentials.'),
            message('later', 'Re: RFT for regional study', 'Please submit your proposal.'),
        ])
        self.assertEqual(result['extracted_information']['request_type_code'], 'EOI')
        self.assertEqual(result['extracted_information']['request_type'], 'Expression of interest')
        self.assertEqual(result['extracted_information']['field_sources']['request_type_code'], ['m1-current'])
        self.assertEqual(result['analysis']['original_request_source_id'], 'm1-current')
        self.assertTrue(any('RFT' in source['excerpt'] for source in result['analysis']['sources']))

    def test_repeated_actual_message_keeps_selected_reply_purpose(self):
        body = 'Customer: Morrow Energy Ltd\nDue date: 20 November 2026'
        result = self.analyze([
            message('original', 'RFQ for valves', body, sent_at='2026-10-01T08:00:00Z'),
            message('repeated', 'Re: RFQ for valves', body, sent_at='2026-10-02T08:00:00Z'),
        ])
        self.assertEqual(result['analysis']['message_kind'], 'reply')
        self.assertEqual(result['analysis']['coverage']['segments_reviewed'], 2)
        self.assertEqual(result['extracted_information']['organization_name'], 'Morrow Energy Ltd')

    def test_deadline_revision_does_not_discard_separate_bid_intention_response(self):
        result = self.analyze([
            message('original', 'RFT for renewal',
                    'Please submit your proposal by 15 October 2026.\n'
                    'Please confirm your intention to bid by 10 October 2026.', sent_at='2026-10-01T08:00:00Z'),
            message('update', 'Re: RFT for renewal', 'The deadline has been extended to 20 October 2026.', sent_at='2026-10-05T08:00:00Z'),
        ])
        actions = [item['text'] for item in result['analysis']['requested_actions']]
        self.assertTrue(any('confirm your intention' in text for text in actions))
        self.assertFalse(any('15 October' in text for text in actions))

    def test_old_dated_instruction_in_same_amendment_is_not_current_action(self):
        result = self.analyze([message(
            'update', 'RFT | Amendment',
            'Please submit your proposal by 15 October 2026.\nThe deadline has been extended to 20 October 2026.',
        )])
        self.assertEqual(result['extracted_information']['due_date'], '2026-10-20')
        self.assertFalse(any('15 October' in item['text'] for item in result['analysis']['requested_actions']))
