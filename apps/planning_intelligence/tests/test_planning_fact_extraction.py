"""Heterogeneous quoted facts, exact provenance and resumable chunk coverage."""
import hashlib
import json
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from ..models import DocumentIntelligenceRun, PlanningFile, PlanningProject
from ..services.document_intelligence import _extract_file_facts, _persist_ai_facts, run_document_intelligence
from ..services.extraction_coverage import summarize_assertions
from ..services.evidence_schema import input_schema, validate_value
from ..services.intelligence import analyze_project
from ..services.planning_fact_extraction import validated_claim


class PlanningAssertionTests(SimpleTestCase):
    def claim(self, kind, value, quote, **extra):
        return validated_claim({'type': kind, 'value': value, 'quote': quote, 'source_file_id': 1, **extra},
                               {'source_file_id': 1, 'character_start': 17, 'text': quote})

    def test_heterogeneous_explicit_assertions_keep_exact_values(self):
        examples = [
            ('milestone', {'name': 'Factory acceptance', 'date': '20/Dec/2026'}, 'Factory acceptance milestone: 20/Dec/2026'),
            ('constraint', {'text': 'No lifting during night shifts'}, 'Constraint: No lifting during night shifts'),
            ('review_cycle', {'name': 'Client review', 'duration': 7, 'unit': 'working days'}, 'Client review requires 7 working days.'),
            ('package', {'name': 'Civil enabling works', 'source_id': 'WP-8'}, 'Work package WP-8: Civil enabling works'),
            ('discipline', {'name': 'Operations'}, 'Discipline: Operations'),
            ('responsibility', {'role': 'Purchasing manager', 'work': 'vendor shortlist'}, 'Purchasing manager shall approve the vendor shortlist.'),
            ('resource_requirement', {'resource': 'cranes', 'quantity': 2, 'unit': 'units'}, 'Required resource: cranes, 2 units.'),
            ('dependency', {'predecessor': 'A-10', 'successor': 'B-20'}, 'B-20 can start only after A-10 is complete.'),
            ('risk', {'text': 'Port closure may delay transformer delivery'}, 'Risk: Port closure may delay transformer delivery'),
        ]
        for kind, value, quote in examples:
            with self.subTest(kind=kind):
                claim = self.claim(kind, value, quote)
                self.assertEqual(claim['value'], value)
                self.assertEqual(claim['character_start'], 17)
                self.assertEqual(claim['character_end'], 17 + len(quote))
                self.assertFalse(claim['executable'])
                self.assertEqual(claim['status'], 'requires_review')

    def test_unquoted_defaults_and_guessed_relationship_fields_are_rejected(self):
        quote = 'B-20 can start only after A-10 is complete.'
        for field, value in [('relationship_type', 'FS'), ('lag', 0), ('lag_unit', 'working days')]:
            with self.subTest(field=field):
                self.assertIsNone(self.claim('dependency', {'predecessor': 'A-10', 'successor': 'B-20', field: value}, quote))
        self.assertIsNone(self.claim('review_cycle', {'name': 'Client review', 'duration': 7, 'unit': 'working days'},
                                     'Client review requires 7 days.'))
        self.assertIsNone(self.claim('milestone', {'name': 'Handover', 'date': '2026-12-20'}, 'Handover on 20 Dec 2026.'))

    def test_numeric_evidence_cannot_borrow_part_of_decimal_signed_or_grouped_number(self):
        for literal in ('1.5', '-1', '+1', '1,000'):
            with self.subTest(literal=literal):
                self.assertIsNone(self.claim('resource_requirement', {'resource': 'cranes', 'quantity': 1},
                                             f'Required resource: cranes, {literal} units.'))

    def test_proposals_and_topic_values_cannot_masquerade_as_extraction(self):
        self.assertIsNone(self.claim('deliverable', 'Cost report', 'Cost report', classification='proposal'))
        self.assertIsNone(self.claim('resource_requirement', {'resource': 'cranes', 'quantity': True}, 'cranes True'))
        self.assertIsNone(self.claim('milestone', {'name': 3}, '3'))
        self.assertIsNone(self.claim('risk', {'text': 'Risk', 'confidence': .9}, 'Risk .9'))

    def test_graph_assertion_schema_does_not_turn_statement_into_executable_link(self):
        statement = {'predecessor': 'A-10', 'successor': 'B-20'}
        self.assertIsNone(validate_value('dependency', statement))
        self.assertIsNotNone(validate_value('dependencies', statement))
        self.assertIsNone(validate_value('review_cycle', {'name': 'Client review', 'working_days': 7}))
        self.assertIsNone(validate_value('discipline', {'name': 'Mechanical Engineering', 'code': 'mechanical'}))
        self.assertEqual(input_schema('responsibility')['required'], ['role', 'work'])
        self.assertIsNotNone(validate_value('resource_requirement', {'resource': 'cranes', 'quantity': float('nan')}))

    def test_repeated_quote_requires_exact_occurrence_offset(self):
        text = 'Risk: Snow\nRisk: Snow'
        raw = {'type': 'risk', 'value': {'text': 'Snow'}, 'source_file_id': 1, 'quote': 'Risk: Snow'}
        chunk = {'source_file_id': 1, 'character_start': 100, 'text': text}
        self.assertIsNone(validated_claim(raw, chunk))
        self.assertEqual(validated_claim({**raw, 'quote_start': 11}, chunk)['character_start'], 111)
        self.assertIsNone(validated_claim({**raw, 'quote_start': 5}, chunk))

    def test_labeled_fallback_retains_unknown_disciplines_and_no_catalogue_scope(self):
        text = ('Work package: Warehouse rollout\nDiscipline: Business Assurance\n'
                'Risk: Supplier liquidation\nResource requirement: two locally certified operators\n'
                'Constraint: No occupancy before inspection\nExamples: Steelwork\nPackage: example only\n')
        source = PlanningFile(id=1, category='other', original_filename='arbitrary.txt', extracted_text=text)
        rows = {'run': DocumentIntelligenceRun(id=1), 'facts': [], '_seen': set()}
        _extract_file_facts(rows, source)
        self.assertEqual({row.fact_type for row in rows['facts']}, {'package', 'discipline', 'risk', 'resource_requirement', 'constraint'})
        for row in rows['facts']:
            loc = row.source_locator
            self.assertEqual(loc['quote'], text[loc['character_start']:loc['character_end']])
            self.assertEqual(loc['extracted_text_sha256'], hashlib.sha256(text.encode()).hexdigest())
            self.assertFalse(loc['executable'])
        discipline = next(row for row in rows['facts'] if row.fact_type == 'discipline')
        self.assertEqual(discipline.value['name'], 'Business Assurance')

    def test_persistence_revalidates_every_structured_leaf_and_preserves_physical_locator(self):
        quote = 'Owner: QA Lead shall check turnover records.'
        text = '--- Sheet: Construction ---\n\n' + quote
        source = PlanningFile(id=1, category='sow', extracted_text=text)
        start = text.index(quote)
        claims = [
            {'type': 'responsibility', 'value': {'role': 'QA Lead', 'work': 'turnover records'}, 'quote': quote,
             'source_file_id': 1, 'character_start': start},
            {'type': 'responsibility', 'value': {'role': 'QA Lead', 'work': 'turnover records', 'person': 'Invented'},
             'quote': quote, 'source_file_id': 1, 'character_start': start},
        ]
        rows = {'run': DocumentIntelligenceRun(id=1), 'facts': [], '_seen': set()}
        result = {'ai_evidence_facts': claims}
        _persist_ai_facts(rows, result, [source])
        self.assertEqual(len(rows['facts']), 1)
        self.assertEqual(len(result['unverified_ai_claims']), 1)
        loc = rows['facts'][0].source_locator
        self.assertEqual((loc['sheet'], loc['line'], loc['character_end']), ('Construction', 3, len(text)))
        self.assertEqual(loc['quote'], quote)

    def test_extraction_summary_never_certifies_semantic_completeness(self):
        result = summarize_assertions([], {'status': 'complete'}, {'status': 'partial', 'chunks_remaining': 5, 'resume_available': True})
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['chunks_remaining'], 5)
        self.assertFalse(result['semantic_coverage_verified'])


@patch('apps.planning_intelligence.services.claude_client.get_claude_config', return_value={'configured': True})
@patch.dict('os.environ', {'PLANNING_AI_MAX_CHUNKS': '1'})
@patch('apps.planning_intelligence.services.intelligence.CLAUDE_MAX_INPUT_CHARS', 32)
class ResumableChunkTests(SimpleTestCase):
    def source(self, text='A' * 75):
        return PlanningFile(id=1, category='sow', original_filename='source.txt', extracted_text=text)

    @patch('apps.planning_intelligence.services.claude_client.call_claude', return_value={'text': '{"facts": []}'})
    def test_resume_skips_completed_chunks_and_preserves_category_context(self, call, _config):
        source = self.source()
        checkpoints = []
        first = analyze_project([source], checkpoint_callback=checkpoints.append)
        second = analyze_project([source], resume_state=checkpoints[-1])
        third = analyze_project([source], resume_state=second['ai_checkpoint'])
        self.assertEqual(call.call_count, 3)
        self.assertEqual(first['ai_processing_coverage']['chunks_remaining'], 2)
        self.assertEqual(second['ai_processing_coverage']['chunks_remaining'], 1)
        self.assertEqual(third['ai_processing_coverage']['status'], 'complete')
        self.assertFalse(third['ai_processing_coverage']['semantic_coverage_verified'])
        prompts = [json.loads(row.kwargs['user_prompt']) for row in call.call_args_list]
        self.assertEqual([row['character_start'] for row in prompts], [0, 32, 64])
        self.assertTrue(all(row['declared_category'] == 'sow' for row in prompts))
        self.assertIn('dependency', prompts[0]['assertion_schemas'])

    @patch('apps.planning_intelligence.services.claude_client.call_claude', return_value={'text': '{"facts": []}'})
    def test_changed_source_invalidates_all_checkpoint_chunks(self, call, _config):
        first = analyze_project([self.source()])
        analyze_project([self.source('B' * 75)], resume_state=first['ai_checkpoint'])
        self.assertEqual(json.loads(call.call_args.kwargs['user_prompt'])['character_start'], 0)

    @patch('apps.planning_intelligence.services.claude_client.call_claude')
    def test_output_limited_chunk_is_not_cached_as_complete(self, call, _config):
        call.side_effect = [
            {'text': '{"facts": []}', 'stop_reason': 'max_tokens'},
            {'text': '{"facts": []}'},
        ]
        first = analyze_project([self.source()])
        second = analyze_project([self.source()], resume_state=first['ai_checkpoint'])
        self.assertEqual(first['ai_processing_coverage']['chunks_partial'], 1)
        self.assertEqual(second['ai_processing_coverage']['chunks_processed'], 1)
        self.assertEqual(json.loads(call.call_args.kwargs['user_prompt'])['character_start'], 0)


class PersistedResumeTests(TestCase):
    @patch.dict('os.environ', {'PLANNING_AI_MAX_CHUNKS': '1'})
    @patch('apps.planning_intelligence.services.intelligence.CLAUDE_MAX_INPUT_CHARS', 32)
    @patch('apps.planning_intelligence.services.claude_client.get_claude_config', return_value={'configured': True})
    @patch('apps.planning_intelligence.services.claude_client.call_claude', return_value={'text': '{"facts": []}'})
    def test_resume_creates_new_run_without_rewriting_previous_review_evidence(self, call, _config):
        project = PlanningProject.objects.create(name='Resume project')
        source = PlanningFile.objects.create(project=project, category='sow', file='resume.txt',
                                            original_filename='resume.txt', parse_status='done', extracted_text='A' * 75)
        first, _ = run_document_intelligence(project)
        old_summary = first.summary.copy()
        old_ids = list(first.facts.values_list('pk', flat=True))
        second, intelligence = run_document_intelligence(project, resume_run=first)
        first.refresh_from_db()
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(first.summary, old_summary)
        self.assertEqual(list(first.facts.values_list('pk', flat=True)), old_ids)
        self.assertEqual(intelligence['extraction_summary']['chunks_remaining'], 1)
        self.assertEqual(second.summary['resumed_from_run_id'], first.pk)
        source.extracted_text = 'Changed source'
        source.save(update_fields=['extracted_text'])
        with self.assertRaisesMessage(ValueError, 'source set changed'):
            run_document_intelligence(project, resume_run=second)
        self.assertEqual(call.call_count, 2)
