"""Agreement administration remains separate from tender creation facts."""

from django.test import SimpleTestCase

from apps.sales.email_analysis import analyze_email_conversation
from apps.sales.email_agreement_actions import agreement_return_deadlines
from apps.sales.email_ai_analysis import _sources, validate_email_proposal
from apps.sales.email_opportunity_evidence import reviewed_analysis_snapshot


SCOPE = 'Replacement of Transformers, HVLV Switchgear of Substation-7 and Outstation-10 in Das Island'
SUBJECT = 'Action Required: WO Agreement 4700030672 - ' + SCOPE
CHECKLIST = '''To enable us to proceed, kindly:
1. Initial all pages of the agreement by signing and stamping them.
2. Submit valid copies of the supporting documents Power of Attorney (POA) of the authorized signatory, and Trade License.
3. Ensure the signature page is duly signed at the designated signature block by an authorized signatory holding a valid POA, and complete the name, title, and date fields.'''
SHARE = 'Kindly share the above no later than Friday 25 September 2026, at 11:00 am (UAE time).'
BODY = ('Dear All,\nThis has been responded.\n\n-----Original Message-----\n'
        'From: Buyer <buyer@customer.example.test>\nTo: Sales <sales@consultant.example.test>\n'
        'Subject: ' + SUBJECT + '\n\n' + CHECKLIST + '\n\n' + SHARE + '\n\n'
        'Your customer, Abu Dhabi National Oil Company, has identified you as the appropriate contact.')


def message(body=BODY, subject='RE: Q-101752 Fw: ' + SUBJECT, **kwargs):
    return {'id': 'agreement-fixture', 'subject': subject, 'body_text': body,
            'sender_email': 'reviewer@consultant.example.test', 'direction': 'incoming',
            'sent_at': '2026-09-29T08:00:00Z', **kwargs}


def analyze(source):
    return analyze_email_conversation([source], selected_message_id=source['id'],
                                      mailbox_address='sales@consultant.example.test', coverage={'status': 'complete'})


class AgreementActionTests(SimpleTestCase):
    def test_supplied_numbered_checklist_share_above_is_a_quoted_action_deadline(self):
        result = analyze(message())
        info, analysis = result['extracted_information'], result['analysis']
        self.assertEqual(info['agreement_reference'], '4700030672')
        self.assertEqual(info['correspondence_reference'], 'Q-101752')
        self.assertEqual(info['scope_summary'], SCOPE)
        self.assertEqual(info['organization_name'], 'Abu Dhabi National Oil Company')
        self.assertEqual((info['due_date'], info['deadline_date'], info['tender_reference']), ('', '', ''))
        self.assertEqual(info['intelligence']['opportunity_detection']['status'], 'follow_up')
        self.assertFalse(info['opportunity_detected'])
        deadline, = info['action_deadlines']
        self.assertEqual({key: deadline[key] for key in ('kind', 'date', 'time', 'timezone', 'status')}, {
            'kind': 'agreement_return', 'date': '2026-09-25', 'time': '11:00',
            'timezone': 'UAE time', 'status': 'requires_verification',
        })
        self.assertEqual(deadline['source_ids'], ['m1-quoted-1'])
        self.assertIn(deadline['evidence'], BODY)
        self.assertTrue(any('Initial all pages' in item['text'] for item in analysis['requested_actions']))
        self.assertTrue(any('Ensure the signature page' in item['text'] for item in analysis['requested_actions']))
        self.assertTrue(any('share the above' in item['text'] for item in analysis['requested_actions']))
        source_ids = {source['id'] for source in analysis['sources']}
        for key in ('agreement_reference', 'correspondence_reference', 'scope_summary'):
            self.assertTrue(set(info['field_sources'][key]) <= source_ids)
            self.assertTrue(info['evidence'][key])

    def test_adjacent_checklist_is_required_for_share_above(self):
        for body in (SHARE, CHECKLIST + '\n\nPlease attend a vendor briefing.\n\n' + SHARE):
            with self.subTest(body=body):
                self.assertEqual(analyze(message(body, SUBJECT))['extracted_information']['action_deadlines'], [])

    def test_html_list_item_paragraphs_keep_the_contiguous_signing_checklist(self):
        body = BODY.replace('\n2.', '\n\n2.').replace('\n3.', '\n\n3.')
        deadline, = analyze(message(body))['extracted_information']['action_deadlines']
        self.assertEqual((deadline['date'], deadline['time'], deadline['timezone']), ('2026-09-25', '11:00', 'UAE time'))

    def test_explicit_return_clock_and_zone_are_local_to_its_date(self):
        body = 'Please return the signed agreement by 25 September 2026, at 1:30 pm (UAE time).'
        deadline, = agreement_return_deadlines(body)
        self.assertEqual((deadline['date'], deadline['time'], deadline['timezone']), ('2026-09-25', '13:30', 'UAE time'))
        date_only, = agreement_return_deadlines('Please return the signed agreement by 25 September 2026.')
        self.assertEqual((date_only['time'], date_only['timezone']), ('', ''))

    def test_no_briefing_or_license_expiry_date_is_borrowed(self):
        bodies = (
            'Please return the signed agreement; the briefing is scheduled by 25 September 2026 at 11:00 UAE time.',
            'Please return the signed agreement and confirm the trade license expires by 25 September 2026.',
            'Please return the signed agreement. Trade license expiry date: 25 September 2026.',
            'Please return the signed agreement. Sent: 25 September 2026.',
        )
        for body in bodies:
            with self.subTest(body=body):
                self.assertEqual(agreement_return_deadlines(body), [])

    def test_clock_in_a_separate_paragraph_is_not_attached_to_return_date(self):
        body = 'Please return the signed agreement by 25 September 2026.\n\n11:00 UAE time briefing starts.'
        deadline, = agreement_return_deadlines(body)
        self.assertEqual((deadline['time'], deadline['timezone']), ('', ''))

    def test_uncertain_historical_cancelled_or_alternative_dates_never_become_proposal_dates(self):
        for body in (
            'Agreement return deadline: 25 September 2026 or 26 September 2026.',
            'Agreement return deadline: 25 September 2026 (proposed only, subject to confirmation).',
            'The old agreement return deadline was 25 September 2026. Please wait for further instructions.',
            'Do not return the agreement by 25 September 2026; that date is cancelled.',
            'Please return the signed agreement by 25 September 2026. That deadline has been cancelled.',
        ):
            with self.subTest(body=body):
                info = analyze(message(body, SUBJECT))['extracted_information']
                self.assertEqual(info['action_deadlines'], [])
                self.assertEqual(info['due_date'], '')

    def test_mixed_genuine_proposal_and_agreement_dates_remain_distinct(self):
        for joiner in ('\n', '; ', ' and '):
            body = ('Proposal submission deadline: 2 October 2026' + joiner
                    + 'please return the signed agreement by 25 September 2026, 11:00 UAE time.')
            with self.subTest(joiner=joiner):
                result = analyze(message(body, SUBJECT))
                info = result['extracted_information']
                self.assertEqual(info['due_date'], '2026-10-02')
                self.assertEqual(info['action_deadlines'][0]['date'], '2026-09-25')
                self.assertIn('due date is 2026-10-02', result['analysis']['summary'])

    def test_labelled_agreement_date_does_not_create_a_false_proposal_conflict(self):
        body = 'Proposal submission deadline: 2 October 2026.\nAgreement return deadline: 25 September 2026.'
        result = analyze(message(body, SUBJECT))
        self.assertEqual(result['extracted_information']['due_date'], '2026-10-02')
        self.assertEqual(result['extracted_information']['action_deadlines'][0]['date'], '2026-09-25')

    def test_same_literal_due_date_in_separate_semantic_clauses_preserves_proposal(self):
        body = 'Due date: 25 September 2026.\nAgreement return due date: 25 September 2026.'
        result = analyze(message(body, SUBJECT))
        self.assertEqual(result['extracted_information']['due_date'], '2026-09-25')
        self.assertEqual(result['extracted_information']['action_deadlines'][0]['date'], '2026-09-25')

    def test_multiple_agreements_or_correspondence_references_remain_unresolved(self):
        result = analyze(message('Please review WO Agreement 4700030672 and WO Agreement 4700030673.',
                                 'RE: Q-101752 Q-101753 - Agreement review'))
        info = result['extracted_information']
        self.assertEqual((info['agreement_reference'], info['correspondence_reference']), ('', ''))
        self.assertIn('4700030672', info['evidence']['agreement_reference'])
        self.assertTrue(any('agreement reference' in warning for warning in info['warnings']))

    def test_outgoing_and_draft_sources_do_not_establish_agreement_requirements(self):
        for flags in ({'direction': 'outgoing'}, {'is_draft': True}):
            info = analyze(message('Please return agreement by 25 September 2026.', SUBJECT, **flags))['extracted_information']
            self.assertEqual(info['agreement_reference'], '')
            self.assertEqual(info['action_deadlines'], [])

    def test_review_snapshot_retains_typed_facts_and_source_evidence(self):
        info = analyze(message())['extracted_information']
        snapshot = reviewed_analysis_snapshot(info)
        for key in ('agreement_reference', 'correspondence_reference', 'action_deadlines', 'scope_summary'):
            self.assertEqual(snapshot[key], info[key])
        self.assertEqual(snapshot['due_date'], '')

    def checked(self, source, fields):
        baseline = analyze(source)
        payload = _sources(baseline, [source])
        selected = payload['selected_source_id']
        proposal = {'classification': {'code': '', 'purpose': 'other', 'source_id': selected, 'excerpt': ''},
                    'fields': [{**item, 'source_id': item.get('source_id', selected)} for item in fields], 'conflicts': []}
        output = validate_email_proposal(baseline, payload, proposal, provider='synthetic', model='synthetic')
        self.assertIsNotNone(output)
        return output['extracted_information']

    def test_ai_cannot_relabel_agreement_or_correspondence_as_tender_reference(self):
        for reference, excerpt in (('4700030672', 'WO Agreement 4700030672'), ('4700030672', '4700030672'),
                                   ('Q-101752', 'Q-101752')):
            with self.subTest(reference=reference, excerpt=excerpt):
                info = self.checked(message(), [{'name': 'tender_reference', 'value': reference, 'excerpt': excerpt}])
                self.assertEqual(info['tender_reference'], '')
                self.assertIn('tender_reference', info['ai_review']['rejected_fields'])

    def test_ai_cannot_clip_agreement_context_or_uncertainty_to_create_proposal_deadline(self):
        for body in (
            'Agreement return deadline: 25 September 2026.',
            'Agreement return deadline: 25 September 2026 or 26 September 2026.',
            'Agreement return deadline: 25 September 2026 (proposed only, subject to confirmation).',
            'The old agreement return deadline was 25 September 2026. Please wait for further instructions.',
        ):
            with self.subTest(body=body):
                excerpt = 'deadline was 25 September 2026' if 'deadline was' in body else 'deadline: 25 September 2026'
                info = self.checked(message(body, SUBJECT), [{'name': 'due_date', 'value': '2026-09-25', 'excerpt': excerpt}])
                self.assertEqual(info['due_date'], '')
                self.assertIn('due_date', info['ai_review']['rejected_fields'])

    def test_ai_accepts_explicit_tender_reference_even_if_reusing_a_q_style_identifier(self):
        source = message('Tender reference: Q-101752\nPlease submit your proposal.', 'New tender request')
        info = self.checked(source, [{'name': 'tender_reference', 'value': 'Q-101752', 'excerpt': 'Tender reference: Q-101752'}])
        self.assertEqual(info['tender_reference'], 'Q-101752')

    def test_ai_preserves_selected_reply_classification_and_quoted_typed_facts(self):
        source = message()
        baseline = analyze(source)
        payload = _sources(baseline, [source])
        proposal = {'classification': {'code': 'general_communication', 'purpose': 'other',
                    'source_id': payload['selected_source_id'], 'excerpt': 'This has been responded.'},
                    'fields': [], 'conflicts': []}
        result = validate_email_proposal(baseline, payload, proposal, provider='synthetic', model='synthetic')
        info = result['extracted_information']
        self.assertEqual(info['classification']['code'], 'general_communication')
        self.assertEqual(info['intelligence']['opportunity_detection']['status'], 'follow_up')
        self.assertEqual(info['action_deadlines'], baseline['extracted_information']['action_deadlines'])
        self.assertEqual(info['due_date'], '')
