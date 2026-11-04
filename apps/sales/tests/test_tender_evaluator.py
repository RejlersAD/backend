"""Unit tests for the tender evaluation projection (rules-only, no DB required)."""

from django.test import SimpleTestCase, TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from unittest.mock import patch
from uuid import uuid4

from apps.sales.evidence.tender_evaluator import build_tender_evaluation, CLARIFY
from apps.sales.evidence.tender_facts import source_coverage
from apps.sales.evidence.rejlers_review import build_rejlers_review
from apps.sales.tests.test_private_attachments import PrivateFixtures
from apps.sales.tests.test_opportunity_workspace import CONFIG


class FakeClient:
    company_name = 'ADNOC Logistics & Services'


class FakeDeal:
    deal_name = 'ADNOC L&S ERP Transformation'
    client = FakeClient()
    submission_due_date = '2026-12-15'


SOW = """
Project Name: ADNOC L&S ERP Transformation
Client: ADNOC Logistics & Services
Location: Abu Dhabi, UAE

Scope of Work:
Design, implementation and rollout of a unified ERP platform across ADNOC L&S and
subsidiaries together with a Global Business Services operating model.

Technologies:
SAP S/4HANA, Microsoft Azure, Oracle database, integration middleware.

Required Disciplines:
SAP functional, SAP technical, data migration, integration, cybersecurity,
change management, training, testing, project management.

Deliverables:
ERP architecture design, data migration plan, cutover plan, training material,
test scripts, go-live plan.

Timeline:
Kickoff Q1 2027, go-live Q4 2027.

Key Risks:
Data migration quality risk, resource availability risk.

Submission deadline: 15 December 2026.
"""


def compiled_with(text):
    return {
        'unified_evidence': {'selected': {}},
        'evidence_debug': [{
            'source_name': 'ADNOC L&S ERP SOW.pdf',
            'source_id': 1,
            'chunks': [('Page 1', text)],
            'extracted': [
                {'kind': 'submission', 'text': 'Submit the technical proposal.', 'excerpt': text[:60],
                 'status': 'likely', 'confidence': 86, 'source_name': 'ADNOC L&S ERP SOW.pdf',
                 'source_location': 'Page 1'},
                {'kind': 'deadline', 'text': 'Submission deadline 15 December 2026.', 'excerpt': text[:60],
                 'date': '2026-12-15', 'status': 'likely', 'confidence': 86, 'source_name': 'ADNOC L&S ERP SOW.pdf',
                 'source_location': 'Page 1'},
                {'kind': 'risk', 'text': 'Data migration quality risk.', 'excerpt': text[:60],
                 'status': 'likely', 'confidence': 86, 'source_name': 'ADNOC L&S ERP SOW.pdf',
                 'source_location': 'Page 1'},
                {'kind': 'milestone', 'text': 'Go-live Q4 2027.', 'excerpt': text[:60],
                 'status': 'likely', 'confidence': 86, 'source_name': 'ADNOC L&S ERP SOW.pdf',
                 'source_location': 'Page 1'},
            ],
        }],
    }


def first_value(result, key):
    rows = result.get('opportunity_summary', {}).get('project_facts', {}).get(key) or []
    return rows[0].get('value') or rows[0].get('finding') if rows else ''


class TenderEvaluatorFactExtractionTests(SimpleTestCase):
    def test_rejlers_dashboard_uses_requested_weights_without_invented_fit(self):
        review = build_rejlers_review([{'source_id': 'sow', 'source_name': 'SOW.pdf',
                                        'chunks': [('Page 3', 'Digital transformation for ADNOC in the UAE.\n'
                                                    'The bidder shall provide a T&M rate card.')]}])
        self.assertEqual(sum(review['weights'].values()), 100)
        self.assertEqual(review['weights']['engineering_capability'], 30)
        self.assertIsNone(review['weighted_score'])
        self.assertEqual(review['recommendation'], 'insufficient_evidence')
        self.assertTrue(all(row['score'] is None and row['assessment'] == 'unknown'
                            for row in review['dimensions']))
        strategic = next(row for row in review['dimensions'] if row['key'] == 'strategic_fit')
        self.assertGreater(strategic['evidence_found'], 0)
        capability = next(row for row in review['dimensions'] if row['key'] == 'engineering_capability')
        self.assertEqual(capability['evidence_found'], 1)

    def test_rejlers_review_does_not_confuse_data_architecture_or_invitation_with_qualification(self):
        def found(text, dimension, item):
            review = build_rejlers_review([{'source_id': 'sow', 'source_name': 'SOW.pdf',
                                           'chunks': [('Page 5', text)]}])
            row = next(row for row in review['dimensions'] if row['key'] == dimension)
            return next(row for row in row['items'] if row['key'] == item)['evidence']
        self.assertFalse(found('Enable a single source of truth for financial data.', 'win_probability', 'competition'))
        self.assertFalse(found('ADNOC Logistics & Services is seeking qualified organizations.', 'qualification_compliance', 'adnoc'))
        self.assertTrue(found('Single-source procurement is the stated route.', 'win_probability', 'competition'))
        self.assertTrue(found('ADNOC vendor registration is mandatory.', 'qualification_compliance', 'adnoc'))

    def test_required_facts_are_extracted_from_source_evidence(self):
        result = build_tender_evaluation(FakeDeal(), compiled_with(SOW))
        summary = result['opportunity_summary']
        self.assertEqual(summary['project_name'], 'ADNOC L&S ERP Transformation')
        self.assertEqual(summary['client_name'], 'ADNOC Logistics & Services')
        self.assertEqual(summary['submission_date'], '2026-12-15')
        self.assertEqual(result['decision']['recommendation'], 'INSUFFICIENT_EVIDENCE')
        self.assertIn('Abu Dhabi', summary['location'])

        facts = summary['project_facts']
        self.assertEqual(first_value(result, 'project_name'), 'ADNOC L&S ERP Transformation')
        self.assertEqual(first_value(result, 'client'), 'ADNOC Logistics & Services')
        self.assertTrue(any('unified ERP platform' in f.get('finding', '') for f in facts['scope']))
        self.assertTrue(any('SAP functional' in f.get('finding', '') for f in facts['disciplines']))
        self.assertTrue(any('SAP S/4HANA' in f.get('finding', '') for f in facts['technologies']))
        self.assertTrue(any('data migration plan' in f.get('finding', '') for f in facts['deliverables']))
        self.assertTrue(any('Q4 2027' in f.get('finding', '') for f in facts['timeline']))
        self.assertTrue(any('Abu Dhabi' in f.get('finding', '') for f in facts['locations']))
        self.assertTrue(any('Data migration quality risk' in f.get('finding', '') for f in facts['key_risks']))

    def test_never_shows_clarification_when_source_evidence_exists(self):
        result = build_tender_evaluation(FakeDeal(), compiled_with(SOW))
        diagnostics = result['opportunity_summary']['field_diagnostics']
        for entry in diagnostics:
            if entry['key'] in {'contract_type', 'commercial_model', 'payment_terms', 'clarification_deadline'}:
                continue
            self.assertEqual(entry['status'], 'Extracted')
            self.assertTrue(entry['evidence_found'])
            self.assertNotEqual(entry['value'], CLARIFY)
            self.assertNotEqual(entry['value'], 'Unknown')
            self.assertNotEqual(entry['value'], '')

    def test_clarification_required_only_when_no_source_evidence(self):
        empty = {'unified_evidence': {'selected': {}},
                 'evidence_debug': [{'source_name': 'Empty.pdf', 'source_id': 1, 'chunks': [], 'extracted': []}]}
        result = build_tender_evaluation(FakeDeal(), empty)
        summary = result['opportunity_summary']
        self.assertEqual(summary['project_name'], CLARIFY)
        self.assertEqual(summary['client_name'], CLARIFY)
        for entry in summary['field_diagnostics']:
            self.assertEqual(entry['status'], 'Clarification Required')
            self.assertFalse(entry['evidence_found'])
            self.assertIn('No supported source evidence found', entry['reason'])

    def test_clarify_constant_is_defined(self):
        self.assertEqual(CLARIFY, 'Further client clarification required')

    def test_dictionary_chunks_retain_full_multiline_section_evidence(self):
        compiled = compiled_with(SOW)
        compiled['evidence_debug'][0]['chunks'] = [{'location': 'Page 1', 'text': SOW}]
        result = build_tender_evaluation(FakeDeal(), compiled)
        scope = result['opportunity_summary']['project_facts']['scope'][0]
        self.assertIn('subsidiaries together with a Global Business Services', scope['value'])
        self.assertIn(scope['source_text'], SOW)
        self.assertEqual(scope['source_file'], 'ADNOC L&S ERP SOW.pdf')
        self.assertEqual(scope['source_page'], 'Page 1')
        self.assertEqual(scope['verification_status'], 'Source Confirmed')
        self.assertTrue(scope['confidence'])

    def test_split_material_table_cells_and_named_systems_keep_page_evidence(self):
        text = ('Materials Master\n500,000\n(60,000 active)\n'
                'The bidder shall use Tricentis Tosca for testing.\n'
                'Connectivity models between vessels and shore include satellite links.\n'
                'Historical data will remain in legacy systems in read-only mode.')
        facts = build_tender_evaluation(FakeDeal(), compiled_with(text))['opportunity_summary']['project_facts']
        self.assertTrue(any('Materials Master\n500,000\n(60,000 active)' in row['value']
                            for row in facts['scope']))
        self.assertTrue(any('Tricentis Tosca' in row['value'] for row in facts['technologies']))
        self.assertTrue(any('satellite' in row['value'] for row in facts['technologies']))
        self.assertTrue(any('read-only' in row['value'] for row in facts['scope']))

    def test_known_facts_do_not_generate_generic_client_questions(self):
        result = build_tender_evaluation(FakeDeal(), compiled_with(SOW))
        missing = {row['field'] for row in result['critical_clarifications']}
        self.assertFalse(missing & {'scope', 'technologies', 'disciplines', 'deliverables', 'locations', 'timeline'})
        self.assertIn('payment_terms', missing)
        self.assertIn('contract_type', missing)
        self.assertNotIn('submission_details', missing)

    def test_present_commercial_terms_do_not_require_an_estimated_budget(self):
        text = SOW + '\nContract type: Services\nCommercial model: Fixed price\nPayment terms: Net 30 days\nClarification deadline: 1 December 2026\n'
        result = build_tender_evaluation(FakeDeal(), compiled_with(text))
        self.assertEqual(result['critical_clarifications'], [])

    def test_conflicting_names_keep_both_values_visible_and_require_review(self):
        result = build_tender_evaluation(FakeDeal(), compiled_with(SOW + '\nProject Name: Different Project\n'))
        values = result['opportunity_summary']['project_facts']['project_name']
        self.assertEqual(len(values), 2)
        diag = next(row for row in result['opportunity_summary']['field_diagnostics'] if row['key'] == 'project_name')
        self.assertEqual(diag['status'], 'Clarification Required')
        self.assertTrue(diag['evidence_found'])
        self.assertTrue(diag['contradictory'])

    def test_client_acronym_variant_is_not_a_conflict(self):
        text = ('Client: ADNOC Logistics & Services (ADNOC L&S)\n'
                'Client: ADNOC Logistics & Services\n')
        result = build_tender_evaluation(FakeDeal(), compiled_with(text))
        self.assertEqual(result['opportunity_summary']['client_name'], 'ADNOC Logistics & Services')
        diag = next(row for row in result['opportunity_summary']['field_diagnostics'] if row['key'] == 'client')
        self.assertEqual(diag['status'], 'Extracted')
        self.assertFalse(diag['contradictory'])

    def test_coverage_uses_sow_pages_when_email_mentions_the_same_topic(self):
        rows = source_coverage([
            {'source_id': 'email', 'source_name': 'Invitation.eml',
             'chunks': [('Email body', 'IMOS may be reviewed.')]},
            {'source_id': 'sow', 'source_name': '02. SOW.PDF',
             'chunks': [('Page 18', 'SAP integration\nIMOS sends AP/AR invoices to SAP.')]},
        ])
        imos = next(row for row in rows if row['title'] == 'IMOS voyage accounting')
        self.assertEqual(imos['mentions'], 1)
        self.assertEqual(imos['evidence'][0]['source_page'], 'Page 18')
        self.assertEqual(imos['evidence'][0]['source_id'], 'sow')

    def test_coverage_prefers_integration_flow_over_generic_system_mention(self):
        rows = source_coverage([{'source_id': 'sow', 'source_name': 'SOW.pdf', 'chunks': [
            ('Page 5', 'SAP integration with AMOS is in scope.'),
            ('Page 18', 'AMOS->SAP: PO, inventory and goods movements.'),
        ]}])
        amos = next(row for row in rows if row['title'] == 'AMOS vessel management')
        self.assertEqual(amos['mentions'], 2)
        self.assertEqual(amos['evidence'][0]['source_page'], 'Page 18')

    def test_conflicting_written_and_numeric_duration_becomes_cited_clarification(self):
        text = 'Hypercare lasts for the first ten (10) weeks of the eight (12) weeks period.'
        result = build_tender_evaluation(FakeDeal(), compiled_with(text))
        question = next(row for row in result['critical_clarifications']
                        if row['field'] == 'source_quantity_conflict')
        self.assertIn('eight (12)', question['question'])
        self.assertEqual(question['evidence'][0]['source_page'], 'Page 1')
        self.assertIn(text, question['evidence'][0]['source_text'])

    def test_incomplete_source_is_a_processing_gap_not_a_client_question(self):
        compiled = {'evidence_debug': [{'source_name': 'Unreadable.pdf', 'parse_status': 'failed', 'chunks': []}]}
        result = build_tender_evaluation(FakeDeal(), compiled)
        self.assertEqual(result['critical_clarifications'], [])
        self.assertTrue(all(row['incomplete'] for row in result['opportunity_summary']['field_diagnostics']))

    def test_section_continuation_on_next_page_keeps_evidence_and_location(self):
        compiled = {'evidence_debug': [{'source_id': 1, 'source_name': 'Scope.pdf', 'chunks': [
            {'location': 'Page 1', 'text': 'Deliverables:\nMigration plan.'},
            {'location': 'Page 2', 'text': 'Cutover runbook.\nTraining handbook.\nTimeline:\nQ4 2027.'},
        ]}]}
        facts = build_tender_evaluation(FakeDeal(), compiled)['opportunity_summary']['project_facts']
        self.assertTrue(any('Cutover runbook' in row['value'] and row['source_page'] == 'Page 2'
                            for row in facts['deliverables']))

    def test_inline_scope_retains_wrapped_sentence_without_requiring_more_keywords(self):
        text = 'Scope: Design a unified ERP platform across the group and\nsubsidiaries together with a Global Business Services operating model.\nTechnologies:\nSAP.'
        facts = build_tender_evaluation(FakeDeal(), compiled_with(text))['opportunity_summary']['project_facts']
        self.assertIn('Global Business Services', facts['scope'][0]['value'])

    def test_raci_project_group_is_not_a_competing_project_title(self):
        text = 'ADNOC L&S ERP Transformation\nProject\nManagement\nDefine overall program plan\nA\nI\nR'
        result = build_tender_evaluation(FakeDeal(), compiled_with(text))
        self.assertEqual(result['opportunity_summary']['project_name'], 'ADNOC L&S ERP Transformation')
        diag = next(row for row in result['opportunity_summary']['field_diagnostics'] if row['key'] == 'project_name')
        self.assertEqual(diag['status'], 'Extracted')

    def test_explicit_organization_introduction_retains_client_evidence(self):
        text = 'Harbour Logistics & Services [HLS] is an integrated logistics company.'
        result = build_tender_evaluation(FakeDeal(), compiled_with(text))
        self.assertEqual(result['opportunity_summary']['client_name'], 'Harbour Logistics & Services')
        self.assertEqual(result['opportunity_summary']['project_facts']['client'][0]['source_text'], text)


@override_settings(**CONFIG)
class TenderFactsPipelineTests(PrivateFixtures, TestCase):
    def test_uploaded_pdf_reaches_ordinary_sales_assessment_before_internal_review(self):
        import pymupdf
        from apps.sales.private_attachments import upload_private_file
        from apps.sales.analysis_models import OpportunityAnalysis
        from apps.sales.opportunity_analysis import request_analysis, run_analysis_step, analysis_projection
        document = pymupdf.open()
        page = document.new_page()
        page.insert_textbox((36, 36, 559, 806), SOW, fontsize=10)
        content = document.tobytes()
        document.close()
        upload_private_file(self.opportunity, self.actor, 'tender',
                            SimpleUploadedFile('Synthetic ERP SOW.pdf', content, content_type='application/pdf'),
                            uuid4())
        with patch('apps.sales.email_ai_provider.analyze_email_sources',
                   side_effect=AssertionError('No paid AI for evaluation')):
            request_analysis(self.opportunity, self.actor)
            report = OpportunityAnalysis.objects.get(opportunity=self.opportunity)
            for _ in range(12):
                run_analysis_step(report.pk)
                report.refresh_from_db()
                if report.status in {'ready', 'partial', 'failed', 'blocked'}:
                    break
            self.assertIn(report.status, {'ready', 'partial'}, report.error_code)
            projection = analysis_projection(self.opportunity, self.actor)
        facts = projection['result']['manager_review']['tender_evaluation']['opportunity_summary']['project_facts']
        for key in ('project_name', 'client', 'scope', 'disciplines', 'technologies', 'deliverables',
                    'locations', 'timeline', 'key_risks'):
            self.assertTrue(facts[key], key)
            self.assertEqual(facts[key][0]['source_file'], 'Synthetic ERP SOW.pdf')
            self.assertTrue(facts[key][0]['source_page'].startswith('Page '))
        self.assertEqual(facts['project_name'][0]['value'], 'ADNOC L&S ERP Transformation')
        self.assertNotIn('evidence_debug', projection['result'])
        evaluation = projection['result']['manager_review']['tender_evaluation']
        self.assertTrue(evaluation['opportunity_summary']['field_diagnostics'])
        source_id = str(facts['scope'][0]['source_id'])
        self.assertEqual(evaluation['source_documents'][source_id]['name'], 'Synthetic ERP SOW.pdf')
        self.assertEqual(evaluation['source_documents'][source_id]['folder'], 'tender')
        self.assertEqual(evaluation['source_documents'][source_id]['provider'], 'radai')
        self.assertTrue(evaluation['source_documents'][source_id]['file_id'].startswith('radai-'))
        self.assertEqual(evaluation['capability_assessment']['assessment'], 'Unknown')
