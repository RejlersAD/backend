"""Public v2 semantics across varied synthetic conversation evidence."""

from django.test import SimpleTestCase

from apps.sales.email_analysis import analyze_email_conversation
from apps.sales.email_intelligence import domain


def message(key='original', **changes):
    return {
        'id': key, 'subject': 'RFQ - River Pumping Project',
        'body_text': 'Customer: River Utilities Ltd\nPlease submit your quotation.\nSubmission deadline: 22 October 2026',
        'sender_email': 'procurement@river.test', 'sender_name': 'Procurement Team',
        'sent_at': '2026-09-20T23:50:00Z', 'received_at': '2026-09-21T00:01:00Z',
        '_thread_metadata': {'headers_available': True, 'in_reply_to': [], 'references': []},
        **changes,
    }


class EmailIntelligenceTests(SimpleTestCase):
    def analyze(self, *messages, **kwargs):
        output = analyze_email_conversation(
            list(messages), selected_message_id=messages[-1]['id'] if messages else None,
            mailbox_address='sales@consultant.test', **kwargs,
        )
        return {**output['extracted_information'], 'analysis': output['analysis']}

    def test_original_sent_day_is_separate_from_reply_received_and_due_dates(self):
        fields = self.analyze(message(), message('reply', subject='Re: RFQ',
            body_text='Thank you. We will review the proposal.', sent_at='2026-09-27T08:00:00Z'))
        self.assertEqual(fields['submission_date'], '2026-09-20')
        self.assertEqual(fields['due_date'], '2026-10-22')
        self.assertEqual(fields['customer_name'], 'River Utilities Ltd')
        self.assertEqual(fields['customer_domain'], 'river.test')
        self.assertEqual(fields['organization_name'], 'River Utilities Ltd')
        self.assertEqual(fields['company_name'], 'River Utilities Ltd')
        self.assertEqual(fields['field_sources']['submission_date'], ['m1-current'])
        self.assertEqual(fields['intelligence']['opportunity_detection']['status'], 'follow_up')
        self.assertFalse(fields['opportunity_detected'])

    def test_missing_original_sent_date_cannot_fall_back_to_received_or_body_date(self):
        fields = self.analyze(message(sent_at=''))
        self.assertEqual(fields['submission_date'], '')
        self.assertEqual(fields['intelligence']['submission_date']['status'], 'requires_verification')
        self.assertEqual(fields['confidence']['submission_date']['level'], 'unresolved')

    def test_original_request_is_not_invented_from_a_reply_even_with_sent_date(self):
        fields = self.analyze(message(subject='Re: RFQ', body_text='Thanks for the reminder.'))
        self.assertEqual(fields['submission_date'], '')
        self.assertEqual(fields['customer_name'], '')
        self.assertFalse(fields['intelligence']['source']['confirmed'])

    def test_quoted_original_uses_its_unambiguous_calendar_day_without_timezone_guess(self):
        body = ('Please review this request.\n\nFrom: Buyer <buyer@cedar.test>\n'
                'Sent: 17 September 2026 10:30\nTo: sales@consultant.test\n'
                'Subject: Request for proposal\n\nCustomer: Cedar Works Ltd\nPlease submit a proposal.')
        fields = self.analyze(message(subject='Fw: Request', body_text=body, sender_email='sales@consultant.test'))
        self.assertEqual(fields['submission_date'], '2026-09-17')
        self.assertEqual(fields['customer_name'], 'Cedar Works Ltd')
        self.assertEqual(fields['customer_domain'], 'cedar.test')
        self.assertEqual(fields['intelligence']['submission_date']['basis'], 'sent_header_calendar_date')
        self.assertEqual(fields['confidence']['submission_date']['level'], 'medium')
        self.assertNotEqual(fields['analysis']['original_incoming_source_id'], fields['analysis']['selected_source_id'])

    def test_ambiguous_quoted_numeric_sent_date_requires_verification(self):
        body = ('From: Buyer <buyer@cedar.test>\nSent: 09/10/2026 10:30\n'
                'To: sales@consultant.test\nSubject: RFQ\n\nPlease submit a quotation.')
        fields = self.analyze(message(subject='Fw: RFQ', body_text=body))
        self.assertEqual(fields['submission_date'], '')

    def test_portal_customer_domain_is_not_delivery_domain(self):
        fields = self.analyze(message(sender_email='notice@smtp.mn1.ariba.com',
            subject='Tender Bulletin 04', body_text='Customer: Seabrook Energy Ltd\nSAP Ariba sourcing portal. Review the linked bulletin.'))
        self.assertEqual(fields['customer_name'], '')
        self.assertEqual(fields['organization_name'], 'Seabrook Energy Ltd')
        self.assertEqual(fields['intelligence']['customer_domain']['status'], 'requires_verification')
        self.assertEqual(fields['intelligence']['deadline_review']['status'], 'requires_verification')
        self.assertEqual(fields['submission_date'], '')

    def test_absent_reply_headers_alone_do_not_prove_original_notice(self):
        notice = message(subject='Tender Bulletin 04', body_text='Review the attached bulletin.')
        for coverage in ('selected_only', 'saved_content', 'partial'):
            with self.subTest(coverage=coverage):
                fields = self.analyze(notice, coverage={'status': coverage})
                self.assertFalse(fields['intelligence']['source']['confirmed'])
                self.assertEqual(fields['submission_date'], '')
        complete = self.analyze(notice, coverage={'status': 'complete'})
        self.assertEqual(complete['submission_date'], '2026-09-20')
        tied = self.analyze(notice, message('another', subject='General notice', body_text='For information.'), coverage={'status': 'complete'})
        self.assertFalse(tied['intelligence']['source']['confirmed'])

    def test_explicit_customer_domain_in_portal_source_is_cited_separately(self):
        fields = self.analyze(message(sender_email='notify@ariba.com', body_text=
            'Customer: Seabrook Energy Ltd\nCustomer domain: seabrook.test\nPlease submit a quotation.\nDeadline: 22 October 2026'))
        self.assertEqual(fields['customer_name'], 'Seabrook Energy Ltd')
        self.assertEqual(fields['customer_domain'], 'seabrook.test')
        self.assertIn('Seabrook Energy Ltd', fields['evidence']['customer_name'])
        self.assertIn('seabrook.test', fields['evidence']['customer_domain'])
        self.assertEqual(fields['due_date'], '2026-10-22')
        self.assertEqual(fields['confidence']['due_date']['level'], 'low')
        self.assertEqual(fields['intelligence']['deadline_review']['status'], 'requires_verification')

    def test_public_and_internal_sender_domains_do_not_become_customer_identity(self):
        for sender in ('buyer@gmail.com', 'colleague@consultant.test', 'buyer@sub.gmail.com'):
            with self.subTest(sender=sender):
                fields = self.analyze(message(sender_email=sender))
                self.assertEqual(fields['customer_domain'], '')
                self.assertEqual(fields['customer_name'], 'River Utilities Ltd')
                self.assertEqual(fields['intelligence']['customer_name']['basis'], 'explicit_organization')
                self.assertEqual(fields['intelligence']['customer_domain']['status'], 'requires_verification')

    def test_conflicting_domain_statements_do_not_choose_first_or_sender(self):
        for body in ('Customer domain: other.test', 'Customer domain: river.test\nClient domain: other.test'):
            with self.subTest(body=body):
                fields = self.analyze(message(body_text=body + '\nPlease submit a quotation.'))
                self.assertEqual(fields['customer_name'], '')
                self.assertEqual(fields['intelligence']['customer_domain']['status'], 'conflicting')

    def test_dns_validation_does_not_guess_domains_from_urls_credentials_or_ips(self):
        for value in ('', None, 'https://user:password@client.test/', '127.0.0.1', 'https://[::1]', 'not a host', 'bad..test'):
            with self.subTest(value=value):
                self.assertEqual(domain(value), '')
        self.assertEqual(domain('Buyer@Dept.Customer.Test'), 'dept.customer.test')
        self.assertEqual(domain('https://customer.test/tenders'), 'customer.test')
        self.assertEqual(domain('ariba.com.customer.test'), 'ariba.com.customer.test')

    def test_multiline_budgetary_request_is_candidate_with_evidence_not_probability(self):
        fields = self.analyze(message(subject='Request for Budgetary Quotation - River Pumping Project', body_text=
            'Client: River Utilities Ltd\nWe invite you to submit a budgetary quotation.\n'
            'The deadline for submission of the budgetary quotation\nis 22 October 2026.'))
        self.assertEqual(fields['request_type_code'], 'RFQ')
        self.assertEqual(fields['due_date'], '2026-10-22')
        self.assertEqual(fields['project_name'], 'River Pumping Project')
        self.assertTrue(fields['opportunity_detected'])
        self.assertTrue(fields['intelligence']['opportunity_detection']['needs_review'])
        self.assertNotIn('opportunity_score', fields)
        entities = {item['entity_type']: item for item in fields['intelligence']['entities']}
        self.assertEqual(entities['contact']['value'], 'Procurement Team')
        self.assertEqual(entities['organization']['value'], 'River Utilities Ltd')
        self.assertTrue(all(item['evidence'] and item['source_ids'] for item in entities.values()))

    def test_invoice_fees_and_unrelated_keywords_do_not_create_opportunity(self):
        fields = self.analyze(message(subject='Invoice reminder', body_text='Please pay invoice AED 5,000.00.'))
        self.assertFalse(fields['opportunity_detected'])
        self.assertEqual(fields['estimated_value'], '')
        self.assertEqual(fields['intelligence']['opportunity_detection']['status'], 'not_established')

    def test_conflicting_requests_remain_ambiguous(self):
        fields = self.analyze(message(subject='RFQ / RFP invitation', body_text='Please submit your response.'))
        self.assertEqual(fields['request_type_code'], '')
        self.assertEqual(fields['intelligence']['opportunity_detection']['status'], 'ambiguous')
        self.assertEqual(fields['confidence']['request_type_code']['level'], 'unresolved')

    def test_partial_history_lowers_confidence_and_keeps_cited_sources(self):
        fields = self.analyze(message(), coverage={'status': 'partial'})
        self.assertEqual(fields['confidence']['submission_date']['level'], 'medium')
        ids = {item['id'] for item in fields['analysis']['sources']}
        for field in fields['confidence'].values():
            self.assertIn('partial', field['reason'])
            self.assertTrue(set(field['source_ids']) <= ids)

    def test_unsent_draft_cannot_establish_domain_or_submission(self):
        fields = self.analyze(message(is_draft=True))
        self.assertEqual(fields['customer_name'], '')
        self.assertEqual(fields['submission_date'], '')
        self.assertFalse(fields['opportunity_detected'])

    def test_empty_input_has_structured_unknowns(self):
        fields = self.analyze()
        self.assertEqual(fields['detection_version'], 2)
        self.assertEqual(fields['customer_name'], '')
        self.assertFalse(fields['intelligence']['source']['confirmed'])

    def test_parenthesized_header_organization_is_separate_from_domain(self):
        fields = self.analyze(message(sender_name='Jordan (Cedar Engineering Ltd)',
            sender_email='jordan@cedar.test', body_text='Please submit a quotation.'))
        self.assertEqual(fields['organization_name'], 'Cedar Engineering Ltd')
        self.assertEqual(fields['customer_name'], 'Cedar Engineering Ltd')
        self.assertEqual(fields['customer_domain'], 'cedar.test')
        self.assertIn('Jordan (Cedar Engineering Ltd)', fields['evidence']['organization_name'])

    def test_wrapped_actual_revision_supersedes_original_but_conditional_does_not(self):
        reply = message('revision', subject='Re: RFQ', sent_at='2026-09-25T08:00:00Z',
                        body_text='The submission deadline has been\nextended to 25 October 2026.')
        fields = self.analyze(message(), reply)
        self.assertEqual(fields['due_date'], '2026-10-25')
        self.assertEqual(fields['submission_date'], '2026-09-20')
        self.assertEqual(fields['field_sources']['due_date'], ['m2-current'])
        reply['body_text'] = 'If approved, the deadline has been\nextended to 25 October 2026.'
        conditional = self.analyze(message(), reply)
        self.assertEqual(conditional['due_date'], '2026-10-22')

    def test_weak_conflicting_reply_words_cannot_obscure_original_explicit_request(self):
        fields = self.analyze(message(), message('reply', subject='Re: RFQ',
            body_text='We are reviewing the quotation and proposal.', sent_at='2026-09-25T08:00:00Z'))
        self.assertEqual(fields['request_type_code'], 'RFQ')
        self.assertEqual(fields['field_sources']['request_type_code'], ['m1-current'])
        # Remove the explicit code from the reply subject: only the original
        # should establish the underlying request despite two weak body nouns.
        fields = self.analyze(message(), message('reply', subject='Re: Your enquiry',
            body_text='We are reviewing the quotation and proposal.', sent_at='2026-09-25T08:00:00Z'))
        self.assertEqual(fields['request_type_code'], 'RFQ')
        self.assertEqual(fields['field_sources']['request_type_code'], ['m1-current'])

    def test_keyword_confidence_is_low_even_without_an_original_request(self):
        fields = self.analyze(message(subject='Status update', body_text='The quotation is ready.'))
        self.assertEqual(fields['request_type_code'], 'RFQ')
        self.assertEqual(fields['confidence']['request_type_code']['level'], 'low')
        self.assertFalse(fields['opportunity_detected'])

    def test_may_month_is_not_treated_as_a_hypothetical_revision(self):
        fields = self.analyze(message(body_text='Please submit a quotation.\nDue date: 20 April 2027'),
            message('revision', subject='Re: RFQ', sent_at='2026-09-25T08:00:00Z',
                    body_text='The deadline has been extended to May 2, 2027.'))
        self.assertEqual(fields['due_date'], '2027-05-02')

    def test_ambiguous_corroborating_sent_header_is_not_silently_discarded(self):
        original = message(subject='RFQ', body_text='Please submit a quotation.', sent_at='')
        def quoted(stamp):
            return (f'From: Buyer <procurement@river.test>\nSent: {stamp}\n'
                    'To: sales@consultant.test\nSubject: RFQ\n\nPlease submit a quotation.')
        reply = message('reply', subject='Re: RFQ', sent_at='2026-09-25T08:00:00Z', body_text=
            quoted('1 September 2026 8:00 AM') + '\n\n' + quoted('09/10/2026 8:00 AM'))
        fields = self.analyze(original, reply)
        self.assertEqual(fields['submission_date'], '')
        status = fields['intelligence']['submission_date']
        self.assertEqual(status['status'], 'requires_verification')
        self.assertIn('ambiguous', status['reason'])
        self.assertEqual(len(status['source_ids']), 2)

    def test_clarification_contact_instructions_do_not_hide_original_invitation(self):
        fields = self.analyze(message(body_text='Customer: River Utilities Ltd\n'
            'Please submit your quotation by 21 October 2026.\nFor clarification, contact the procurement team.'))
        self.assertEqual(fields['analysis']['message_kind'], 'request')
        self.assertTrue(fields['analysis']['coverage']['original_identified'])
        self.assertEqual(fields['submission_date'], '2026-09-20')
        self.assertEqual(fields['customer_name'], 'River Utilities Ltd')
        self.assertEqual(fields['customer_domain'], 'river.test')
        self.assertEqual(fields['classification']['code'], 'rfq')

    def test_explicit_clarification_action_is_still_a_followup(self):
        fields = self.analyze(message(body_text='Please provide technical clarifications.'))
        self.assertEqual(fields['analysis']['message_kind'], 'clarification')
        self.assertFalse(fields['opportunity_detected'])
        self.assertEqual(fields['intelligence']['opportunity_detection']['status'], 'follow_up')
