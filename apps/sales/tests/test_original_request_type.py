"""Original solicitation type is distinct from later response terminology."""

from django.test import SimpleTestCase

from apps.sales.email_analysis import analyze_email_conversation


def source(key, subject, body, *, day=1, sender='buyer@cedar.example', **extra):
    return {
        'id': key, 'subject': subject, 'body_text': body,
        'sender_email': sender, 'sender_name': 'Procurement contact',
        'sent_at': f'2026-11-{day:02d}T08:00:00Z',
        **extra,
    }


class OriginalRequestTypeTests(SimpleTestCase):
    def analyze(self, *messages):
        output = analyze_email_conversation(
            list(messages), selected_message_id=messages[-1]['id'],
            mailbox_address='sales@consultant.example', coverage={'status': 'complete'},
        )
        return output['extracted_information'], output['analysis']

    def test_formal_original_rfq_can_request_a_competitive_proposal(self):
        fields, analysis = self.analyze(source(
            'original', 'RFQ-527 | Harbor survey',
            'We invite you to submit a competitive proposal against the attached RFQ.\n'
            'Kindly acknowledge receipt and confirm that you will submit a quotation.',
        ))
        self.assertEqual(fields['request_type_code'], 'RFQ')
        self.assertEqual(analysis['original_request_source_id'], 'm1-current')
        self.assertEqual(fields['field_sources']['request_type_code'], ['m1-current'])

    def test_reply_subject_code_and_incidental_form_reference_do_not_reclassify_original(self):
        fields, analysis = self.analyze(
            source('original', 'RFQ-527 | Harbor survey', 'Please submit your quotation.'),
            source('reply', 'Re: RFP response format',
                   'Please use the RFP template for the technical proposal.', day=2),
        )
        self.assertEqual(fields['request_type_code'], 'RFQ')
        self.assertEqual(fields['field_sources']['request_type_code'], ['m1-current'])
        self.assertEqual(analysis['original_request_source_id'], 'm1-current')
        self.assertTrue(any('RFP' in item['excerpt'] for item in analysis['sources']))

    def test_original_types_are_stable_across_other_explicit_response_codes(self):
        for code, subject, invitation in (
            ('EOI', 'Expression of interest: coastal asset study', 'Please provide your credentials.'),
            ('RFP', 'Request for proposal: drainage design', 'Please submit your proposal.'),
            ('RFT', 'Request for tender: bridge inspection', 'Please submit your tender.'),
            ('ITT', 'Invitation to tender: water treatment', 'Please submit your response.'),
        ):
            with self.subTest(code=code):
                fields, analysis = self.analyze(
                    source('original', subject, invitation),
                    source('reply', 'Re: RFQ document format',
                           'The RFQ pricing table can be used in your proposal.', day=2),
                )
                self.assertEqual(fields['request_type_code'], code)
                self.assertEqual(fields['field_sources']['request_type_code'], ['m1-current'])
                self.assertEqual(analysis['original_request_source_id'], 'm1-current')

    def test_current_clarification_keeps_original_underlying_request_type(self):
        fields, analysis = self.analyze(
            source('original', 'RFQ: controls replacement', 'Please submit a quotation.'),
            source('reply', 'Re: RFQ clarification',
                   'Please clarify the assumptions in your RFP response.', day=2),
        )
        self.assertEqual(fields['request_type_code'], 'RFQ')
        self.assertEqual(analysis['message_kind'], 'clarification')
        self.assertEqual(fields['classification']['code'], 'clarification')

    def test_explicit_original_is_ranked_before_earlier_weak_request_body(self):
        fields, analysis = self.analyze(
            source('preliminary', 'Engineering requirements', 'Please submit a proposal.'),
            source('formal', 'RFQ-810: definitive request', 'The quotation documents are attached.', day=2),
            source('reply', 'Re: RFQ-810', 'We are reviewing the proposal requirements.', day=3),
        )
        self.assertEqual(analysis['original_request_source_id'], 'm2-current')
        self.assertEqual(fields['title'], 'RFQ-810: definitive request')
        self.assertEqual(fields['request_type_code'], 'RFQ')
        self.assertEqual(fields['field_sources']['request_type_code'], ['m2-current'])

    def test_ambiguous_explicit_original_is_not_skipped_for_later_resolved_reply(self):
        fields, analysis = self.analyze(
            source('original', 'RFQ / RFP invitation: capacity study', 'Documents are attached.'),
            source('reply', 'Re: RFQ capacity study', 'Thank you for your enquiry.', day=2),
        )
        self.assertEqual(analysis['original_request_source_id'], 'm1-current')
        self.assertEqual(fields['title'], 'RFQ / RFP invitation: capacity study')
        self.assertEqual(fields['request_type_code'], '')
        self.assertEqual(fields['request_type'], '')
        self.assertEqual(fields['field_sources']['request_type_code'], ['m1-current'])
        self.assertEqual(fields['confidence']['request_type_code']['level'], 'unresolved')

    def test_conflicting_formal_original_still_outranks_weak_preliminary_body(self):
        fields, analysis = self.analyze(
            source('preliminary', 'Initial discussion', 'Please submit a proposal.'),
            source('formal', 'RFT / ITT instruction', 'The documents are attached.', day=2),
        )
        self.assertEqual(analysis['original_request_source_id'], 'm2-current')
        self.assertEqual(fields['request_type_code'], '')
        self.assertEqual(fields['field_sources']['request_type_code'], ['m2-current'])

    def test_strong_conflict_between_original_subject_and_body_remains_unresolved(self):
        fields, analysis = self.analyze(source(
            'original', 'RFQ-927: electrical study', 'This is a request for proposal for the study.',
        ))
        self.assertEqual(fields['request_type_code'], '')
        self.assertEqual(fields['request_type'], '')
        self.assertEqual(analysis['original_request_source_id'], 'm1-current')
        self.assertIn('RFQ', fields['evidence']['request_type_code'])
        self.assertIn('request for proposal', fields['evidence']['request_type_code'])

    def test_forward_preserves_quoted_original_type_despite_current_subject_code(self):
        body = ('Please review the RFP response format.\n\n-----Original Message-----\n'
                'From: Buyer <buyer@cedar.example>\nSent: Sun, 1 Nov 2026 08:00:00 +0000\n'
                'To: Sales <sales@consultant.example>\nSubject: Request for quotation: marine survey\n\n'
                'Please submit your competitive proposal against this RFQ.')
        for sender in ('sales@consultant.example', 'coordinator@agency.example'):
            with self.subTest(sender=sender):
                fields, analysis = self.analyze(source('forward', 'FW: RFP response', body, day=3, sender=sender))
                self.assertEqual(fields['request_type_code'], 'RFQ')
                self.assertEqual(analysis['original_request_source_id'], 'm1-quoted-1')
                self.assertEqual(fields['field_sources']['request_type_code'], ['m1-quoted-1'])

    def test_positive_type_reclassification_requires_review_with_both_sources(self):
        fields, analysis = self.analyze(
            source('original', 'RFQ-812: reservoir survey', 'Please submit your quotation.'),
            source('amendment', 'Re: RFQ-812 amended procedure',
                   'This RFQ has been replaced by an RFP.', day=2),
        )
        self.assertEqual(analysis['original_request_source_id'], 'm1-current')
        self.assertEqual(fields['request_type_code'], '')
        self.assertEqual(fields['request_type'], '')
        self.assertEqual(set(fields['field_sources']['request_type_code']), {'m1-current', 'm2-current'})
        self.assertIn('replaced', fields['evidence']['request_type_code'])
        self.assertEqual(fields['confidence']['request_type_code']['level'], 'unresolved')
        self.assertTrue(fields['warnings'])

    def test_hypothetical_requested_or_negated_reclassification_does_not_amend_original(self):
        for body in (
            'If approved, this RFQ will be replaced by an RFP.',
            'Can this RFQ be replaced by an RFP?',
            'We request that this RFQ be replaced by an RFP.',
            'We request that this RFQ is replaced by an RFP.',
            'This RFQ has not been replaced by an RFP.',
        ):
            with self.subTest(body=body):
                fields, _ = self.analyze(
                    source('original', 'RFQ-812: reservoir survey', 'Please submit your quotation.'),
                    source('reply', 'Re: RFQ-812', body, day=2),
                )
                self.assertEqual(fields['request_type_code'], 'RFQ')
                self.assertEqual(fields['field_sources']['request_type_code'], ['m1-current'])

    def test_bare_quoted_reclassification_is_not_a_confirmed_current_amendment(self):
        fields, _ = self.analyze(
            source('original', 'RFQ-812: reservoir survey', 'Please submit your quotation.'),
            source('reply', 'Re: RFQ-812',
                   'Please check this example wording.\n> This RFQ has been replaced by an RFP.', day=2),
        )
        self.assertEqual(fields['request_type_code'], 'RFQ')
        self.assertEqual(fields['field_sources']['request_type_code'], ['m1-current'])

    def test_reclassification_quoted_as_example_is_not_current_amendment(self):
        fields, _ = self.analyze(
            source('original', 'RFQ-812: reservoir survey', 'Please submit your quotation.'),
            source('reply', 'Re: RFQ-812',
                   'The example says: "This RFQ has been replaced by an RFP."', day=2),
        )
        self.assertEqual(fields['request_type_code'], 'RFQ')
        self.assertEqual(fields['field_sources']['request_type_code'], ['m1-current'])

    def test_outgoing_and_draft_reclassification_cannot_override_incoming_request(self):
        for flags in ({'sender': 'sales@consultant.example'}, {'is_draft': True}):
            with self.subTest(flags=flags):
                fields, _ = self.analyze(
                    source('original', 'RFT: access road', 'Please submit your tender.'),
                    source('reply', 'Re: RFT', 'This RFT has been replaced by an RFQ.', day=2, **flags),
                )
                self.assertEqual(fields['request_type_code'], 'RFT')
                self.assertEqual(fields['field_sources']['request_type_code'], ['m1-current'])

    def test_missing_original_does_not_fabricate_an_authoritative_anchor(self):
        fields, analysis = self.analyze(source('reply', 'Re: RFQ-812', 'We are reviewing the proposal.'))
        self.assertIsNone(analysis['original_request_source_id'])
        self.assertEqual(fields['request_type_code'], 'RFQ')
        self.assertFalse(analysis['coverage']['original_identified'])
