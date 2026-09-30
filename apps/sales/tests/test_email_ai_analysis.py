"""Source validation, shared response compatibility and isolated AI cache checks."""

import copy
import json
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from apps.sales.email_analysis import analyze_email_conversation
from apps.sales.email_ai_analysis import (
    _sources, enhance_email_analysis, validate_email_proposal,
)


SUBJECT = 'Reminder for Tender - Tender Code Tender_38669 on OQ Tawreed Portal'
PACKAGE = 'Once Off Procurement - PR 70059386 - CHEM;N;PROPYL ALCHI;LAB RGNT,LQD,DRM'
DEADLINE = ('Please note that the deadline for submitting a submission concerning the tender '
            'on OQ Tawreed Portal has been set to:\n\nDate: 2 Oct, 2026\nTime: 01:59 (Gulf Standard Time)')
BODY = ('Dear Supplier,\n\nThis is to remind you that there are only two days left to respond '
        '- Tender Code Tender_38669, published by OQ regarding 6000062192-' + PACKAGE + '.\n\n' + DEADLINE)


def email(key='selected', **changes):
    return {'id': key, 'subject': SUBJECT, 'body_text': BODY,
            'sender_email': 'tawreed@oq.com', 'sender_name': 'OQ Tawreed',
            'sent_at': '2026-09-29T05:15:00Z', 'received_at': '2026-09-29T05:15:01Z',
            '_thread_metadata': {'headers_available': True, 'in_reply_to': [], 'references': []},
            **changes}


def baseline(messages=None):
    messages = messages or [email()]
    return analyze_email_conversation(messages, selected_message_id=messages[-1]['id'],
                                      mailbox_address='sales@consultant.test', coverage={'status': 'complete'})


def fact(name, value, excerpt, source_id='m1-current'):
    return {'name': name, 'value': value, 'source_id': source_id, 'excerpt': excerpt}


def oq_proposal():
    return {'classification': {'code': 'tender_opportunity', 'purpose': 'reminder',
                              'source_id': 'm1-current', 'excerpt': SUBJECT},
            'fields': [
                fact('organization_name', 'OQ', 'published by OQ regarding'),
                fact('tender_reference', 'Tender_38669', 'Tender Code Tender_38669'),
                fact('procurement_reference', '6000062192', 'regarding 6000062192-'),
                fact('pr_reference', '70059386', 'PR 70059386'),
                fact('scope_summary', PACKAGE, PACKAGE),
                fact('source_portal', 'OQ Tawreed Portal', 'OQ Tawreed Portal'),
                fact('due_date', '2026-10-02', DEADLINE),
                fact('deadline_time', '01:59', 'Time: 01:59 (Gulf Standard Time)'),
                fact('deadline_timezone', 'Gulf Standard Time', 'Time: 01:59 (Gulf Standard Time)'),
            ], 'conflicts': []}


class EmailAIAnalysisTests(SimpleTestCase):
    def setUp(self):
        unmanaged = patch('apps.core.ai_credentials._provider_record', return_value=None)
        unmanaged.start()
        self.addCleanup(unmanaged.stop)
        cache.clear()

    def validate(self, proposal=None, messages=None):
        messages = messages or [email()]
        result = baseline(messages)
        return validate_email_proposal(result, _sources(result, messages),
            proposal if proposal is not None else oq_proposal(), provider='test', model='contract-test')

    def test_oq_reminder_populates_existing_fields_and_preserves_full_deadline(self):
        info = self.validate()['extracted_information']
        self.assertEqual(info['customer_name'], 'OQ')
        self.assertEqual(info['organization_name'], 'OQ')
        self.assertEqual(info['tender_reference'], 'Tender_38669')
        self.assertEqual(info['procurement_reference'], '6000062192')
        self.assertEqual(info['pr_reference'], '70059386')
        self.assertEqual(info['scope_summary'], PACKAGE)
        self.assertEqual(info['due_date'], '2026-10-02')
        self.assertEqual(info['ai_review']['proposal']['deadline_at'], '2026-10-02T01:59:00+04:00')
        self.assertEqual(info['classification']['code'], 'tender_opportunity')
        self.assertEqual(info['classification']['confidence']['method'], 'ai_evidence_v1')
        self.assertEqual(info['intelligence']['opportunity_detection']['status'], 'follow_up')
        self.assertFalse(info['opportunity_detected'])
        self.assertEqual(info['submission_date'], '2026-09-29')
        self.assertEqual(info['estimated_value'], '')
        self.assertEqual(info['expected_award_date'], '')

    def test_fabricated_customer_excerpt_is_rejected_without_replacing_domain_fallback(self):
        proposal = oq_proposal()
        proposal['fields'] = [fact('organization_name', 'Invented Ltd', 'Published by Invented Ltd')]
        info = self.validate(proposal)['extracted_information']
        self.assertNotEqual(info['customer_name'], 'Invented Ltd')
        self.assertEqual(info['organization_name'], '')
        self.assertIn('organization_name', info['ai_review']['rejected_fields'])

    def test_literal_value_must_be_present_in_valid_excerpt(self):
        proposal = oq_proposal()
        proposal['fields'] = [fact('tender_reference', 'Tender_99999', 'Tender Code Tender_38669')]
        info = self.validate(proposal)['extracted_information']
        self.assertEqual(info['tender_reference'], '')

    def test_partial_identifier_cannot_be_promoted(self):
        proposal = oq_proposal()
        proposal['fields'] = [fact('tender_reference', '38669', 'Tender Code Tender_38669')]
        self.assertEqual(self.validate(proposal)['extracted_information']['tender_reference'], '')

    def test_unknown_source_cannot_support_classification(self):
        proposal = oq_proposal()
        proposal['classification']['source_id'] = 'other-mailbox'
        self.assertIsNone(self.validate(proposal))

    def test_quoted_original_cannot_classify_selected_reply(self):
        messages = [email(), email('reply', subject='Re: tender', body_text='Thank you.', sent_at='2026-09-30T05:15:00Z')]
        self.assertIsNone(self.validate(oq_proposal(), messages))

    def test_unknown_classification_and_extra_provider_instructions_rejected(self):
        for change in ('unknown', 'instructions'):
            proposal = oq_proposal()
            if change == 'unknown':
                proposal['classification']['code'] = 'approved_opportunity'
            else:
                proposal['execute'] = 'create opportunity now'
            self.assertIsNone(self.validate(proposal))

    def test_ambiguous_numeric_and_hypothetical_dates_remain_missing(self):
        for passage, proposed in [('Submission deadline: 03/04/2027', '2027-04-03'),
                                  ('If approved, the deadline will be 2 Oct, 2026', '2026-10-02')]:
            messages = [email(body_text=passage)]
            proposal = oq_proposal()
            proposal['fields'] = [fact('due_date', proposed, passage)]
            info = self.validate(proposal, messages)['extracted_information']
            self.assertEqual(info['due_date'], '')

    def test_sent_date_is_not_a_deadline(self):
        messages = [email(body_text='Date: 2 Oct, 2026')]
        proposal = oq_proposal()
        proposal['fields'] = [fact('due_date', '2026-10-02', 'Date: 2 Oct, 2026')]
        self.assertEqual(self.validate(proposal, messages)['extracted_information']['due_date'], '')

    def test_date_near_portal_deadline_reference_is_not_a_deadline(self):
        passage = 'Date: 2 Oct, 2026. Submission deadline is available in the portal.'
        proposal = oq_proposal()
        proposal['fields'] = [fact('due_date', '2026-10-02', passage)]
        info = self.validate(proposal, [email(body_text=passage)])['extracted_information']
        self.assertNotIn('due_date', info['ai_review']['proposal'])

    def test_deadline_document_update_date_is_not_submission_deadline(self):
        passage = 'Deadline documents were updated on 2 October 2026'
        proposal = oq_proposal()
        proposal['fields'] = [fact('due_date', '2026-10-02', passage)]
        info = self.validate(proposal, [email(body_text=passage)])['extracted_information']
        self.assertNotIn('due_date', info['ai_review']['proposal'])

    def test_explicit_updated_deadline_date_remains_extractable(self):
        passage = 'Submission deadline has been revised to 2 October 2026'
        proposal = oq_proposal()
        proposal['fields'] = [fact('due_date', '2026-10-02', passage)]
        info = self.validate(proposal, [email(body_text=passage)])['extracted_information']
        self.assertEqual(info['ai_review']['proposal']['due_date'], '2026-10-02')

    def test_briefing_time_cannot_complete_submission_deadline(self):
        passage = 'Submission deadline: 2 Oct, 2026. Vendor briefing at 10:00 (Gulf Standard Time).'
        proposal = oq_proposal()
        proposal['fields'] = [fact('due_date', '2026-10-02', passage),
            fact('deadline_time', '10:00', passage),
            fact('deadline_timezone', 'Gulf Standard Time', passage)]
        info = self.validate(proposal, [email(body_text=passage)])['extracted_information']
        self.assertEqual(info['ai_review']['proposal']['due_date'], '2026-10-02')
        self.assertNotIn('deadline_time', info['ai_review']['proposal'])
        self.assertNotIn('deadline_timezone', info['ai_review']['proposal'])
        self.assertNotIn('deadline_at', info['ai_review']['proposal'])

    def test_missing_original_sent_date_stays_unresolved_with_partial_history(self):
        messages = [email()]
        result = analyze_email_conversation(messages, selected_message_id='selected',
            mailbox_address='sales@consultant.test', coverage={'status': 'partial'})
        output = validate_email_proposal(result, _sources(result, messages), oq_proposal())
        self.assertEqual(output['extracted_information']['submission_date'], '')
        self.assertEqual(output['extracted_information']['due_date'], '2026-10-02')

    def test_award_date_is_not_inferred_from_submission_deadline(self):
        proposal = oq_proposal()
        proposal['fields'].append(fact('expected_award_date', '2026-10-02', DEADLINE))
        self.assertEqual(self.validate(proposal)['extracted_information']['expected_award_date'], '')

    def test_pr_number_is_not_money(self):
        proposal = oq_proposal()
        proposal['fields'].append(fact('estimated_value', '70059386', 'PR 70059386'))
        self.assertEqual(self.validate(proposal)['extracted_information']['estimated_value'], '')

    def test_pr_number_near_actual_budget_is_not_money(self):
        passage = 'PR 70059386 - Estimated contract value: AED 125,000.50'
        proposal = oq_proposal()
        proposal['fields'] = [fact('estimated_value', '70059386', passage)]
        info = self.validate(proposal, [email(body_text=passage)])['extracted_information']
        self.assertNotIn('estimated_value', info['ai_review']['proposal'])
        proposal['fields'][0]['value'] = '125000.50'
        info = self.validate(proposal, [email(body_text=passage)])['extracted_information']
        self.assertEqual(info['ai_review']['proposal']['estimated_value'], '125000.50')

    def test_budget_range_is_not_promoted_as_exact_amount(self):
        passage = 'Estimated value: AED 100000 to 150000'
        proposal = oq_proposal()
        proposal['fields'] = [fact('estimated_value', '100000', passage)]
        info = self.validate(proposal, [email(body_text=passage)])['extracted_information']
        self.assertNotIn('estimated_value', info['ai_review']['proposal'])

    def test_scaled_or_percentage_budget_is_not_unscaled_absolute_amount(self):
        for passage, value in [('Budget: AED 5 million', '5'), ('Budget: 10% of contract value', '10')]:
            proposal = oq_proposal()
            proposal['fields'] = [fact('estimated_value', value, passage)]
            info = self.validate(proposal, [email(body_text=passage)])['extracted_information']
            self.assertNotIn('estimated_value', info['ai_review']['proposal'])

    def test_explicit_decimal_budget_currency_and_award_date_are_available_for_form(self):
        passage = '\nEstimated contract value: AED 125,000.50\nExpected award date: 10 November 2026'
        messages = [email(body_text=BODY + passage)]
        proposal = oq_proposal()
        proposal['fields'].extend([
            fact('estimated_value', '125000.50', 'Estimated contract value: AED 125,000.50'),
            fact('currency', 'AED', 'Estimated contract value: AED 125,000.50'),
            fact('expected_award_date', '2026-11-10', 'Expected award date: 10 November 2026'),
        ])
        info = self.validate(proposal, messages)['extracted_information']
        self.assertEqual(info['estimated_value'], '125000.50')
        self.assertEqual(info['currency'], 'AED')
        self.assertEqual(info['expected_award_date'], '2026-11-10')

    def test_equal_decimal_representations_do_not_clear_detected_contract_value(self):
        passage = 'Estimated contract value: AED 125,000.00'
        messages = [email(body_text=passage)]
        self.assertEqual(baseline(messages)['extracted_information']['estimated_value'], '125000.00')
        proposal = oq_proposal()
        proposal['fields'] = [fact('estimated_value', '125000', passage)]
        info = self.validate(proposal, messages)['extracted_information']
        self.assertEqual(info['estimated_value'], '125000')
        self.assertNotIn('estimated_value', info['ai_review']['conflicting_fields'])

    def test_labelled_sentence_amounts_accept_terminal_punctuation(self):
        for ending in ('.', ';', '!', '\n'):
            passage = 'The estimated contract value is AED 125,000.50' + ending
            proposal = oq_proposal()
            proposal['fields'] = [fact('estimated_value', '125000.50', passage)]
            with self.subTest(ending=repr(ending)):
                info = self.validate(proposal, [email(body_text=passage)])['extracted_information']
                self.assertEqual(info['estimated_value'], '125000.50')

    def test_amount_punctuation_does_not_allow_malformed_grouping_fractions_or_units(self):
        for token, value in (
            ('125,00.50', '12500.50'), ('125,,000.50', '125000.50'),
            ('125.000.50', '125.00'), ('1 / 2', '1'), ('100 - 200', '100'),
            ('100 to 200', '100'), ('5 million', '5'), ('5 USD million', '5'),
            ('5%', '5'), ('5 AED per hour', '5'), ('100 USD - 200 USD', '100'),
            ('100 USD / 200 USD', '100'), ('100 USD to AED 200', '100'),
            ('100 to USD 200', '100'), ('100 USD%', '100'),
        ):
            passage = 'Estimated contract value: ' + token
            proposal = oq_proposal()
            proposal['fields'] = [fact('estimated_value', value, passage)]
            with self.subTest(token=token):
                info = self.validate(proposal, [email(body_text=passage)])['extracted_information']
                self.assertNotIn('estimated_value', info['ai_review']['proposal'])

    def test_different_decimal_amount_still_requires_review(self):
        first = 'Estimated contract value: AED 125,000.00'
        second = 'The estimated contract value is AED 150,000.00.'
        proposal = oq_proposal()
        proposal['fields'] = [fact('estimated_value', '150000', second)]
        info = self.validate(proposal, [email(body_text=first + '\n' + second)])['extracted_information']
        self.assertEqual(info['estimated_value'], '')
        self.assertIn('estimated_value', info['ai_review']['conflicting_fields'])

    def test_expanded_standard_scope_names_populate_existing_scope_enum(self):
        for value, phrase in (
            ('feed', 'Front-end engineering design'),
            ('pre_feed', 'Pre-front-end engineering design'),
            ('epc', 'Engineering, procurement and construction'),
            ('epcm', 'Engineering, procurement and construction management'),
            ('pmc', 'Project management consultancy'),
            ('owner_engineer', "Owner's engineer"),
            ('detailed_engineering', 'Detailed engineering'),
        ):
            passage = 'The services comprise ' + phrase + ' for the pumping station.'
            proposal = oq_proposal()
            proposal['fields'] = [fact('scope_type', value, passage)]
            with self.subTest(scope=value):
                info = self.validate(proposal, [email(body_text=passage)])['extracted_information']
                self.assertEqual(info['scope_type'], value)
                self.assertEqual(info['ai_review']['proposal']['scope_type'], value)
                self.assertEqual(info['ai_review']['field_evidence']['scope_type']['excerpt'], passage)

    def test_other_scope_requires_explicit_scope_type_label(self):
        for passage, accepted in (('Scope type: Other', True), ('Other suppliers may bid.', False),
                                  ('Once off procurement of laboratory chemicals.', False)):
            proposal = oq_proposal()
            proposal['fields'] = [fact('scope_type', 'other', passage)]
            with self.subTest(passage=passage):
                info = self.validate(proposal, [email(body_text=passage)])['extracted_information']
                self.assertEqual('scope_type' in info['ai_review']['proposal'], accepted)

    def test_scope_aliases_do_not_assert_negated_hypothetical_or_different_services(self):
        for value, passage in (
            ('feed', 'Pre-front-end engineering design services.'),
            ('feed', 'Pre-FEED services only.'),
            ('epc', 'Engineering, procurement and construction management.'),
            ('feed', 'No front-end engineering design is requested.'),
            ('epcm', 'If approved, engineering, procurement and construction management may be requested.'),
            ('feed', 'Please send feedback on the report.'),
        ):
            proposal = oq_proposal()
            proposal['fields'] = [fact('scope_type', value, passage)]
            with self.subTest(passage=passage):
                info = self.validate(proposal, [email(body_text=passage)])['extracted_information']
                self.assertNotIn('scope_type', info['ai_review']['proposal'])

    def test_same_source_literal_scope_continuation_enriches_existing_summary(self):
        scope = 'FEED design\nSupply engineering drawings and calculations.'
        passage = 'Scope of work: ' + scope
        messages = [email(body_text=passage)]
        self.assertEqual(baseline(messages)['extracted_information']['scope_summary'], 'FEED design')
        proposal = oq_proposal()
        proposal['fields'] = [fact('scope_summary', scope, passage)]
        info = self.validate(proposal, messages)['extracted_information']
        self.assertEqual(info['scope_summary'], scope)
        self.assertNotIn('scope_summary', info['ai_review']['conflicting_fields'])
        self.assertEqual(info['field_sources']['scope_summary'], ['m1-current'])

    def test_disjoint_or_superseding_scope_does_not_replace_existing_summary(self):
        for scope in ('Basic engineering services.', 'FEED design is replaced by basic engineering.'):
            passage = 'Scope of work: FEED design\n' + scope
            proposal = oq_proposal()
            proposal['fields'] = [fact('scope_summary', scope, scope)]
            with self.subTest(scope=scope):
                info = self.validate(proposal, [email(body_text=passage)])['extracted_information']
                self.assertEqual(info['scope_summary'], '')
                self.assertIn('scope_summary', info['ai_review']['conflicting_fields'])

    def test_scope_continuation_from_different_source_remains_conflicting(self):
        continuation = 'FEED design with additional control system engineering.'
        messages = [email(body_text='Scope of work: FEED design'), email('reply',
            subject='Re: tender', body_text=continuation, sent_at='2026-09-30T05:15:00Z')]
        proposal = oq_proposal()
        proposal['classification'] = {'code': 'clarification', 'purpose': 'clarification',
            'source_id': 'm2-current', 'excerpt': continuation}
        proposal['fields'] = [fact('scope_summary', continuation, continuation, 'm2-current')]
        info = self.validate(proposal, messages)['extracted_information']
        self.assertEqual(info['scope_summary'], '')
        self.assertIn('scope_summary', info['ai_review']['conflicting_fields'])

    def test_conflicting_ai_dates_do_not_choose_last_value(self):
        second = 'Alternative submission deadline: 3 Oct, 2026'
        messages = [email(body_text=BODY + '\n' + second)]
        proposal = oq_proposal()
        proposal['fields'].append(fact('due_date', '2026-10-03', second))
        info = self.validate(proposal, messages)['extracted_information']
        self.assertEqual(info['due_date'], '')
        self.assertNotIn('deadline_at', info['ai_review']['proposal'])
        self.assertEqual(info['intelligence']['deadline_review']['status'], 'ambiguous')

    def test_existing_rule_deadline_conflict_is_not_overridden(self):
        messages = [email(body_text='Due date: 2 Oct, 2026\nDue date: 3 Oct, 2026')]
        proposal = oq_proposal()
        proposal['fields'] = [fact('due_date', '2026-10-02', 'Due date: 2 Oct, 2026')]
        self.assertEqual(self.validate(proposal, messages)['extracted_information']['due_date'], '')

    def test_conflicting_known_date_removes_stale_summary_field_and_confidence(self):
        first, second = 'Due date: 2 Oct, 2026', 'The requested submission deadline is 3 Oct, 2026'
        messages = [email(body_text=first + '\n' + second)]
        result = baseline(messages)
        result['extracted_information']['due_date'] = '2026-10-02'
        result['analysis']['key_points'] = [{'label': 'Current due date', 'value': '2026-10-02', 'source_ids': ['m1-current']}]
        proposal = oq_proposal()
        proposal['fields'] = [fact('due_date', '2026-10-03', second)]
        output = validate_email_proposal(result, _sources(result, messages), proposal)
        self.assertEqual(output['extracted_information']['confidence']['due_date']['level'], 'unresolved')
        self.assertFalse(any(point['label'] == 'Current due date' for point in output['analysis']['key_points']))
        self.assertTrue(output['extracted_information']['ai_review']['conflict_evidence']['due_date'])

    def test_request_category_conflicts_remain_ambiguous(self):
        result = baseline()
        result['extracted_information']['classification'] = {'version': 1, 'status': 'ambiguous', 'code': '', 'needs_review': True}
        output = validate_email_proposal(result, _sources(result, [email()]), oq_proposal())
        self.assertEqual(output['extracted_information']['classification']['status'], 'ambiguous')
        self.assertFalse(output['extracted_information']['opportunity_detected'])

    def test_organization_conflict_clears_aliases_and_preserves_conflict_evidence(self):
        passage = 'Buyer OQ. Alternate buyer ADNOC.'
        messages = [email(body_text=passage)]
        proposal = oq_proposal()
        proposal['fields'] = [fact('organization_name', 'OQ', 'Buyer OQ'),
                             fact('organization_name', 'ADNOC', 'Alternate buyer ADNOC')]
        info = self.validate(proposal, messages)['extracted_information']
        for name in ('organization_name', 'customer_name', 'company_name'):
            self.assertEqual(info[name], '')
            self.assertEqual(info['confidence'][name]['level'], 'unresolved')
            self.assertIn('Buyer OQ', info['evidence'][name])
            self.assertIn('Alternate buyer ADNOC', info['evidence'][name])
            self.assertEqual(info['field_sources'][name], ['m1-current'])

    def test_ai_cancellation_cannot_establish_new_opportunity(self):
        messages = [email(subject='Tender cancelled', body_text='Tender_38669 is cancelled.')]
        proposal = {'classification': {'code': 'tender_opportunity', 'purpose': 'cancellation',
                    'source_id': 'm1-current', 'excerpt': 'Tender_38669 is cancelled.'}, 'fields': [], 'conflicts': []}
        info = self.validate(proposal, messages)['extracted_information']
        self.assertFalse(info['opportunity_detected'])
        self.assertEqual(info['intelligence']['opportunity_detection']['status'], 'not_established')

    def test_unrelated_maintenance_notice_does_not_establish_opportunity(self):
        messages = [email(subject='Portal maintenance', body_text='The procurement portal will be unavailable on Sunday.')]
        proposal = {'classification': {'code': 'general_communication', 'purpose': 'other',
                    'source_id': 'm1-current', 'excerpt': 'Portal maintenance'}, 'fields': [], 'conflicts': []}
        self.assertFalse(self.validate(proposal, messages)['extracted_information']['opportunity_detected'])

    def test_unhashable_model_source_id_is_rejected_safely(self):
        proposal = oq_proposal()
        proposal['classification']['source_id'] = ['m1-current']
        self.assertIsNone(self.validate(proposal))

    def test_ambiguous_timezone_is_preserved_but_no_offset_invented(self):
        messages = [email(body_text=BODY.replace('Gulf Standard Time', 'GST'))]
        proposal = oq_proposal()
        for row in proposal['fields']:
            row['excerpt'] = row['excerpt'].replace('Gulf Standard Time', 'GST')
            if row['name'] == 'deadline_timezone':
                row['value'] = 'GST'
        info = self.validate(proposal, messages)['extracted_information']
        self.assertEqual(info['deadline_timezone'], 'GST')
        self.assertNotIn('deadline_at', info['ai_review']['proposal'])

    def test_timezone_prefix_cannot_create_incorrect_offset(self):
        for actual, proposed in [('UTC+04:00', 'UTC'), ('UTC+04:30', 'UTC+04')]:
            passage = f'Submission deadline: 2 Oct, 2026 at 10:00 {actual}'
            proposal = oq_proposal()
            proposal['fields'] = [fact('due_date', '2026-10-02', passage),
                fact('deadline_time', '10:00', passage), fact('deadline_timezone', proposed, passage)]
            info = self.validate(proposal, [email(body_text=passage)])['extracted_information']
            self.assertNotIn('deadline_at', info['ai_review']['proposal'])
            proposal['fields'][-1]['value'] = actual
            info = self.validate(proposal, [email(body_text=passage)])['extracted_information']
            self.assertEqual(info['ai_review']['proposal']['deadline_at'], f'2026-10-02T10:00:00{actual[3:]}')

    def test_source_data_and_rule_output_remain_unchanged(self):
        messages = [email()]
        original = copy.deepcopy(messages)
        result = baseline(messages)
        original_result = copy.deepcopy(result)
        validate_email_proposal(result, _sources(result, messages), oq_proposal())
        self.assertEqual(messages, original)
        self.assertEqual(result, original_result)

    def test_draft_and_outgoing_sources_excluded(self):
        for changes in ({'is_draft': True}, {'sender_email': 'sales@consultant.test'}):
            messages = [email(**changes)]
            self.assertEqual(_sources(baseline(messages), messages)['sources'], [])

    def test_source_budget_marks_partial_coverage(self):
        messages = [email(body_text=BODY + '\n' + 'x' * 90_000)]
        payload = _sources(baseline(messages), messages)
        self.assertTrue(payload['partial'])
        self.assertLessEqual(len(payload['sources'][0]['body']), 32_000)

    def run_enhancer(self, *, scope='actor:1:mailbox:1', messages=None, allow_provider=True, ready=True):
        messages = messages or [email()]
        config = {'enabled': True, 'ready': ready, 'provider': 'test', 'model': 'test'}
        with patch('apps.sales.email_ai_analysis.email_ai_configuration', return_value=config), \
             patch('apps.sales.email_ai_analysis.email_ai_cache_identity', return_value='config-v1'):
            return enhance_email_analysis(baseline(messages), messages, scope_key=scope, allow_provider=allow_provider)

    @patch('apps.sales.email_ai_analysis.analyze_email_sources')
    def test_validated_results_cached_by_actor_and_source_and_detached(self, provider):
        provider.return_value = {'status': 'completed', 'proposal': oq_proposal(), 'model': 'test', 'provider': 'test'}
        first = self.run_enhancer()
        first['extracted_information']['customer_name'] = 'changed caller copy'
        second = self.run_enhancer()
        self.assertEqual(second['extracted_information']['customer_name'], 'OQ')
        self.assertEqual(provider.call_count, 1)
        self.run_enhancer(scope='actor:2:mailbox:1')
        self.assertEqual(provider.call_count, 2)
        self.run_enhancer(messages=[email(body_text=BODY + '\nChanged source.')])
        self.assertEqual(provider.call_count, 3)

    @patch('apps.sales.email_ai_analysis.analyze_email_sources')
    def test_validator_revision_invalidates_prior_result_without_changing_public_version(self, provider):
        provider.return_value = {'status': 'completed', 'proposal': oq_proposal(), 'model': 'test', 'provider': 'test'}
        with patch('apps.sales.email_ai_analysis.VALIDATION_REVISION', 100):
            first = self.run_enhancer()
            self.run_enhancer()
        with patch('apps.sales.email_ai_analysis.VALIDATION_REVISION', 101):
            second = self.run_enhancer()
        self.assertEqual(provider.call_count, 2)
        self.assertEqual(first['extracted_information']['ai_review']['version'], 1)
        self.assertEqual(second['extracted_information']['ai_review']['version'], 1)
        self.assertNotEqual(first['extracted_information']['ai_review']['analysis_id'],
                            second['extracted_information']['ai_review']['analysis_id'])

    @patch('apps.sales.email_ai_analysis.analyze_email_sources')
    def test_conversion_cache_only_path_never_calls_provider(self, provider):
        result = self.run_enhancer(allow_provider=False)
        self.assertEqual(result['extracted_information']['ai_review']['error_code'], 'analysis_not_cached')
        provider.assert_not_called()

    @patch('apps.sales.email_ai_analysis.analyze_email_sources')
    def test_missing_config_or_scope_does_not_send_email(self, provider):
        self.run_enhancer(ready=False)
        self.run_enhancer(scope='')
        provider.assert_not_called()

    @patch('apps.sales.email_ai_analysis.analyze_email_sources')
    def test_provider_failure_retains_rules_and_does_not_echo_sensitive_error(self, provider):
        provider.side_effect = RuntimeError('secret-token-and-private-email')
        output = self.run_enhancer()
        self.assertEqual(output['extracted_information']['ai_review']['status'], 'failed')
        self.assertEqual(output['extracted_information']['customer_name'], baseline()['extracted_information']['customer_name'])
        self.assertNotIn('secret-token', str(output))

    @patch('apps.sales.email_ai_analysis.analyze_email_sources')
    def test_schema_invalid_output_falls_back_and_is_not_success(self, provider):
        provider.return_value = {'status': 'completed', 'proposal': {'made_up': 'data'}}
        output = self.run_enhancer()
        self.assertEqual(output['extracted_information']['ai_review']['status'], 'failed')
        self.assertEqual(output['extracted_information']['ai_review']['error_code'], 'invalid_evidence')

    @override_settings(
        SALES_EMAIL_AI_ENABLED=True, SALES_EMAIL_AI_PROVIDER='anthropic',
        SALES_EMAIL_AI_API_KEY='synthetic-claude-key', SALES_EMAIL_AI_MODEL='claude-sonnet-5-5',
        SALES_EMAIL_AI_TIMEOUT_SECONDS=12, SALES_EMAIL_AI_MAX_OUTPUT_TOKENS=3500,
    )
    def test_anthropic_wire_response_reaches_oq_validator_and_safe_failure_fallback(self):
        from anthropic import Anthropic, _base_client

        httpx = getattr(_base_client, 'httpx2', None) or _base_client.httpx

        good_response = {
            'id': 'synthetic-claude-message', 'type': 'message', 'role': 'assistant',
            'model': 'claude-sonnet-5-5', 'stop_reason': 'end_turn', 'stop_sequence': None,
            'content': [
                {'type': 'thinking', 'thinking': 'synthetic-private-reasoning', 'signature': 'synthetic-signature'},
                {'type': 'text', 'text': json.dumps(oq_proposal())},
            ],
            'usage': {'input_tokens': 1000, 'output_tokens': 600},
        }
        for status, response_body in [(200, good_response), (401, {
            'type': 'error', 'error': {'type': 'authentication_error',
                'message': 'synthetic-private-error synthetic-claude-key'},
        })]:
            with self.subTest(status=status):
                cache.clear()
                requests = []

                def transport(request):
                    requests.append(request)
                    return httpx.Response(status, json=response_body)

                client = Anthropic(
                    api_key='synthetic-claude-key', base_url='https://api.anthropic.com', max_retries=0,
                    http_client=httpx.Client(transport=httpx.MockTransport(transport)),
                )
                with patch('apps.sales.email_ai_provider._anthropic_client', return_value=client), \
                     patch('apps.sales.email_ai_provider._openai_client') as openai_client:
                    output = enhance_email_analysis(baseline(), [email()], scope_key='actor:1:mailbox:1')
                    again = enhance_email_analysis(baseline(), [email()], scope_key='actor:1:mailbox:1')
                openai_client.assert_not_called()
                self.assertEqual(len(requests), 1)
                self.assertEqual(str(requests[0].url), 'https://api.anthropic.com/v1/messages')
                self.assertEqual(json.loads(requests[0].content)['output_config']['format']['type'], 'json_schema')
                self.assertEqual(output, again)
                self.assertTrue(client.is_closed())
                self.assertNotIn('synthetic-private', str(output))
                self.assertNotIn('synthetic-claude-key', str(output))
                info = output['extracted_information']
                if status == 200:
                    self.assertEqual(info['customer_name'], 'OQ')
                    self.assertEqual(info['tender_reference'], 'Tender_38669')
                    self.assertEqual(info['classification']['code'], 'tender_opportunity')
                    self.assertEqual(info['ai_review']['provider'], 'anthropic')
                    self.assertEqual(info['ai_review']['purpose'], 'reminder')
                    self.assertEqual(info['ai_review']['proposal']['deadline_at'], '2026-10-02T01:59:00+04:00')
                else:
                    self.assertEqual(info['ai_review']['error_code'], 'provider_authentication')
                    self.assertEqual(info['due_date'], baseline()['extracted_information']['due_date'])
