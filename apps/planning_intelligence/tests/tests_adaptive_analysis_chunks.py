"""Adaptive extraction preserves literal source coverage and bounded continuation."""
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase

from ..models import PlanningFile, PlanningProject
from ..services import intelligence
from ..services.document_intelligence import run_document_intelligence
from ..services.operational_jobs import operation_fingerprint


EMPTY = {'text': '{"facts": []}', 'stop_reason': 'end_turn'}
LIMITED = {'text': '{"facts": [', 'stop_reason': 'max_tokens'}
CONFIGURATION = {'provider': 'anthropic', 'model': 'synthetic-model'}


class AdaptiveAnalysisChunkTests(SimpleTestCase):
    def setUp(self):
        self.project = SimpleNamespace(ai_settings={'provider': 'anthropic'})
        self.source = PlanningFile(pk=1, category='sow', original_filename='synthetic.txt', extracted_text='A' * 120)
        self.requests, self.events, self.checkpoints = [], [], []
        self.handler = lambda payload, options: EMPTY
        for target, value in [('CLAUDE_MAX_INPUT_CHARS', 120), ('AI_MIN_CHUNK_CHARS', 15), ('AI_MAX_SPLIT_DEPTH', 3)]:
            patcher = patch.object(intelligence, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        provider = patch.object(intelligence.project_ai, 'call_project_ai', side_effect=self.provider)
        self.provider_mock = provider.start()
        self.addCleanup(provider.stop)
        configuration = patch.object(intelligence.project_ai, 'get_project_ai_config', return_value=deepcopy(CONFIGURATION))
        self.configuration = configuration.start()
        self.addCleanup(configuration.stop)
        budget = patch.dict('os.environ', {'PLANNING_AI_MAX_CHUNKS': '16', 'PLANNING_AI_CONCURRENCY': '1'})
        budget.start()
        self.addCleanup(budget.stop)

    def provider(self, *args, **options):
        payload = json.loads(options['user_prompt'])
        self.requests.append((payload['source_file_id'], payload['character_start'],
                              payload['character_start'] + len(payload['source_text'])))
        return self.handler(payload, options)

    def analyze(self, **options):
        return intelligence.analyze_project([self.source], project=self.project,
            progress_callback=lambda event: self.events.append(deepcopy(event)),
            checkpoint_callback=lambda state: self.checkpoints.append(deepcopy(state)), **options)

    def test_nested_output_limits_split_into_an_exact_leaf_partition(self):
        self.handler = lambda payload, options: LIMITED if len(payload['source_text']) > 30 else EMPTY
        result = self.analyze()
        coverage = result['ai_processing_coverage']
        self.assertEqual(len(self.requests), 7)
        self.assertEqual(len(set(self.requests)), 7)
        self.assertEqual(coverage['status'], 'complete')
        self.assertEqual(coverage['chunks_processed'], 4)
        self.assertEqual(coverage['chunks_failed'], 0)
        self.assertEqual(coverage['split_count'], 3)
        self.assertEqual(coverage['characters_processed'], 120)
        self.assertEqual([(row['character_start'], row['character_end']) for row in coverage['chunks']],
                         [(0, 30), (30, 60), (60, 90), (90, 120)])
        self.assertFalse(coverage['semantic_coverage_verified'])
        finished = [row['characters_finished'] for row in self.events if row['phase'] == 'ai_review']
        self.assertEqual(finished, sorted(finished))
        self.assertEqual(finished[-1], 120)
        self.assertTrue(all(row['characters_total'] == 120 for row in self.events if row['phase'] == 'ai_review'))

    def test_budget_counts_parent_and_children_and_resume_does_not_repeat_them(self):
        self.handler = lambda payload, options: LIMITED if len(payload['source_text']) == 120 else EMPTY
        with patch.dict('os.environ', {'PLANNING_AI_MAX_CHUNKS': '2'}):
            first = self.analyze()
        self.assertEqual(self.requests, [(1, 0, 120), (1, 0, 60)])
        self.assertEqual(first['ai_processing_coverage']['chunks_skipped'], 1)
        self.assertEqual(first['ai_processing_coverage']['calls_this_pass'], 2)
        checkpoint = deepcopy(first['ai_checkpoint'])
        second = self.analyze(resume_state=checkpoint)
        self.assertEqual(self.requests, [(1, 0, 120), (1, 0, 60), (1, 60, 120)])
        self.assertEqual(second['ai_processing_coverage']['calls_this_pass'], 1)
        self.assertEqual(second['ai_processing_coverage']['status'], 'complete')
        self.assertEqual(checkpoint, first['ai_checkpoint'])
        self.assertEqual(set(second['ai_checkpoint']['chunks']), {'0.0', '0.1'})

    def test_large_document_fits_bounded_64_call_pass_and_explicit_16_remains_honored(self):
        self.source.extracted_text = 'A' * 359211
        with patch.object(intelligence, 'CLAUDE_MAX_INPUT_CHARS', 6000):
            with patch.dict('os.environ', {'PLANNING_AI_MAX_CHUNKS': '64'}):
                complete = self.analyze()
            self.assertEqual(complete['ai_processing_coverage']['calls_this_pass'], 60)
            self.assertEqual(complete['ai_processing_coverage']['status'], 'complete')
            self.assertEqual(complete['ai_processing_coverage']['characters_processed'], 359211)
            with patch.dict('os.environ', {'PLANNING_AI_MAX_CHUNKS': '16'}):
                bounded = self.analyze()
            self.assertEqual(bounded['ai_processing_coverage']['calls_this_pass'], 16)
            self.assertEqual(bounded['ai_processing_coverage']['chunks_remaining'], 44)
            self.assertEqual(bounded['ai_processing_coverage']['status'], 'partial')

    def test_split_is_checkpointed_even_when_last_allowed_call_hit_limit(self):
        self.handler = lambda payload, options: LIMITED
        with patch.dict('os.environ', {'PLANNING_AI_MAX_CHUNKS': '1'}):
            first = self.analyze()
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.checkpoints[-1]['split_keys'], ['0'])
        self.assertEqual(first['ai_processing_coverage']['chunks_skipped'], 2)
        self.handler = lambda payload, options: EMPTY
        self.analyze(resume_state=self.checkpoints[-1])
        self.assertEqual(self.requests[1:], [(1, 0, 60), (1, 60, 120)])

    def test_explicit_output_limit_without_any_text_also_splits(self):
        def handler(payload, options):
            if len(payload['source_text']) == 120:
                options['error_details'].update(provider='anthropic', code='output_limit', http_status=None)
                return None
            return EMPTY
        self.handler = handler
        result = self.analyze()
        self.assertEqual(result['ai_processing_coverage']['status'], 'complete')
        self.assertEqual(len(self.requests), 3)

    def test_auth_timeout_and_invalid_json_are_not_automatically_split_or_retried(self):
        for code in ('invalid_api_key', 'timeout', 'invalid_response'):
            with self.subTest(code=code):
                self.requests.clear()
                def handler(payload, options):
                    options['error_details'].update(provider='anthropic', code=code, http_status=None)
                    return None
                self.handler = handler
                result = self.analyze()
                self.assertEqual(len(self.requests), 1)
                self.assertEqual(result['ai_checkpoint']['split_keys'], [])
                self.assertEqual(result['ai_processing_coverage']['chunks_failed'], 1)
        for response in ({'text': 'invalid JSON'}, 'invalid response shape', ['invalid shape']):
            with self.subTest(response=response):
                self.requests.clear()
                self.handler = lambda payload, options: response
                result = self.analyze()
                self.assertEqual(len(self.requests), 1)
                self.assertEqual(result['ai_processing_coverage']['chunks_failed'], 1)

    def test_minimum_leaf_limit_remains_explicit_without_an_automatic_resend(self):
        self.source.extracted_text = 'A' * 29
        self.handler = lambda payload, options: {**EMPTY, 'stop_reason': 'max_tokens'}
        result = self.analyze()
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(result['ai_processing_coverage']['chunks_partial'], 1)
        self.assertTrue(result['ai_processing_coverage']['resume_available'])
        self.assertEqual(result['ai_checkpoint']['chunks'], {})

    def test_partial_terminal_leaf_facts_survive_a_later_failed_manual_retry(self):
        self.source.extracted_text = 'Prepare Permit Matrix.'
        self.handler = lambda payload, options: {'text': json.dumps({'facts': [{
            'type': 'deliverable', 'value': 'Permit Matrix', 'source_file_id': 1,
            'quote': self.source.extracted_text}]}), 'stop_reason': 'max_tokens'}
        first = self.analyze()
        self.assertEqual(first['ai_processing_coverage']['chunks_partial'], 1)
        self.assertIn('0', first['ai_checkpoint']['partial_responses'])
        self.assertEqual(first['ai_checkpoint']['chunks'], {})
        self.handler = lambda payload, options: None
        second = self.analyze(resume_state=first['ai_checkpoint'])
        self.assertEqual(second['ai_processing_coverage']['chunks_failed'], 1)
        self.assertEqual(second['ai_processing_coverage']['chunks_processed'], 0)
        self.assertEqual(second['ai_evidence_facts'], first['ai_evidence_facts'])

    def test_maximum_depth_bounds_adaptation_even_when_every_leaf_fails(self):
        self.handler = lambda payload, options: LIMITED
        with patch.object(intelligence, 'AI_MAX_SPLIT_DEPTH', 1):
            result = self.analyze()
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(result['ai_processing_coverage']['chunks_failed'], 2)
        self.assertEqual(result['ai_processing_coverage']['split_count'], 1)
        self.assertEqual(result['ai_processing_coverage']['characters_processed'], 0)
        self.assertTrue(all(row['error']['code'] == 'output_limit'
                            and row['error']['next_action'] == 'retry_analysis'
                            for row in result['ai_processing_coverage']['chunks']))

    def test_gemini_terminal_truncated_response_keeps_the_limit_recovery_action(self):
        self.configuration.return_value['provider'] = 'gemini'
        self.source.extracted_text = 'A' * 29
        self.handler = lambda payload, options: LIMITED
        result = self.analyze()
        error = result['ai_processing_coverage']['chunks'][0]['error']
        self.assertEqual(error['provider'], 'gemini')
        self.assertEqual(error['code'], 'output_limit')
        self.assertEqual(error['next_action'], 'retry_analysis')
        self.assertIn('Google Gemini reached', error['message'])

    def test_partial_parent_claims_survive_resume_without_becoming_completed_coverage(self):
        title = 'Permit Matrix'
        self.source.extracted_text = 'A' * 30 + title + 'B' * (120 - 30 - len(title))
        fact = {'type': 'deliverable', 'value': title, 'source_file_id': 1, 'quote': title, 'quote_start': 30}
        self.handler = lambda payload, options: {'text': json.dumps({'facts': [fact]}), 'stop_reason': 'max_tokens'}
        with patch.dict('os.environ', {'PLANNING_AI_MAX_CHUNKS': '1'}):
            first = self.analyze()
        self.assertEqual(first['ai_processing_coverage']['chunks_processed'], 0)
        self.assertEqual(first['ai_evidence_facts'][0]['character_start'], 30)
        self.assertNotIn('0', first['ai_checkpoint']['chunks'])
        self.assertIn('0', first['ai_checkpoint']['partial_responses'])
        self.handler = lambda payload, options: ({'text': json.dumps({'facts': [fact]}), 'stop_reason': 'end_turn'}
                                                  if payload['character_start'] == 0 else EMPTY)
        second = self.analyze(resume_state=first['ai_checkpoint'])
        self.assertEqual(second['ai_processing_coverage']['status'], 'complete')
        self.assertEqual(len(second['ai_evidence_facts']), 1)
        self.assertEqual(second['ai_evidence_facts'][0]['quote'], title)

    def test_exact_partial_parent_claim_can_gain_quote_supported_child_discipline(self):
        quote = 'Prepare General Permit Matrix.'
        self.source.extracted_text = quote + 'A' * (120 - len(quote))
        fact = {'type': 'deliverable', 'value': 'Permit Matrix', 'source_file_id': 1, 'quote': quote}
        def handler(payload, options):
            if len(payload['source_text']) == 120:
                return {'text': json.dumps({'facts': [{**fact, 'discipline': None}]}), 'stop_reason': 'max_tokens'}
            return {'text': json.dumps({'facts': [{**fact, 'discipline': 'General'}] * 2
                                                if payload['character_start'] == 0 else []})}
        self.handler = handler
        result = self.analyze()
        self.assertEqual(result['ai_processing_coverage']['status'], 'complete')
        self.assertEqual(len(result['ai_evidence_facts']), 1)
        self.assertEqual(result['ai_evidence_facts'][0]['discipline'], 'General')
        self.assertEqual(result['ai_checkpoint']['partial_responses']['0']['facts'][0]['discipline'], None)

    def test_different_supported_disciplines_and_quote_boundaries_are_not_silently_merged(self):
        quote = 'Prepare General Mechanical Permit Matrix.'
        self.source.extracted_text = quote + ' Next.' + 'A' * (120 - len(quote) - 6)
        fact = {'type': 'deliverable', 'value': 'Permit Matrix', 'source_file_id': 1, 'quote': quote,
                'discipline': 'General'}
        for child in ({**fact, 'discipline': 'Mechanical'}, {**fact, 'quote': quote + ' Next.'}):
            with self.subTest(child_discipline=child['discipline'], quote_length=len(child['quote'])):
                def handler(payload, options):
                    if len(payload['source_text']) == 120:
                        return {'text': json.dumps({'facts': [fact]}), 'stop_reason': 'max_tokens'}
                    return {'text': json.dumps({'facts': [child] if payload['character_start'] == 0 else []})}
                self.handler = handler
                result = self.analyze()
                self.assertEqual(len(result['ai_evidence_facts']), 2)
                self.assertFalse(any(row['executable'] for row in result['ai_evidence_facts']))

    def test_child_quote_offsets_remain_absolute_in_saved_evidence(self):
        title = 'Permit Matrix'
        self.source.extracted_text = 'A' * 70 + title + 'B' * (120 - 70 - len(title))
        def handler(payload, options):
            if len(payload['source_text']) == 120:
                return LIMITED
            if title in payload['source_text']:
                return {'text': json.dumps({'facts': [{'type': 'deliverable', 'value': title,
                    'source_file_id': 1, 'quote': title, 'quote_start': payload['source_text'].index(title)}]})}
            return EMPTY
        self.handler = handler
        claim = self.analyze()['ai_evidence_facts'][0]
        self.assertEqual(claim['character_start'], 70)
        self.assertEqual(claim['character_end'], 70 + len(title))
        self.assertEqual(self.source.extracted_text[claim['character_start']:claim['character_end']], title)

    def test_unique_quote_needs_no_model_offset_but_repeated_quote_still_needs_identity(self):
        title = 'Permit Matrix'
        self.source.extracted_text = title + '\nSafety dossier\n' + 'Inspection report\nInspection report'
        self.handler = lambda payload, options: {'text': json.dumps({'facts': [
            {'type': 'deliverable', 'value': title, 'source_file_id': 1, 'quote': title},
            {'type': 'deliverable', 'value': 'Inspection report', 'source_file_id': 1, 'quote': 'Inspection report'},
        ]})}
        result = self.analyze()
        self.assertEqual([row['value'] for row in result['ai_evidence_facts']], [title])
        self.assertEqual(result['ai_processing_coverage']['rejected_claim_count'], 1)
        self.assertEqual(result['ai_evidence_facts'][0]['character_start'], 0)
        self.assertIn('Omit quote_start when the quote occurs only once', self.provider_mock.call_args.kwargs['system_prompt'])

    def test_newline_partition_never_loses_or_repeats_unicode_source(self):
        self.source.extracted_text = ('Design αβγ\n' * 11) + 'Last requirement'
        self.handler = lambda payload, options: LIMITED if len(payload['source_text']) > 40 else EMPTY
        result = self.analyze()
        rows = result['ai_processing_coverage']['chunks']
        rebuilt = ''.join(self.source.extracted_text[row['character_start']:row['character_end']] for row in rows)
        self.assertEqual(rebuilt, self.source.extracted_text)
        self.assertEqual(sum(row['character_end'] - row['character_start'] for row in rows), len(rebuilt))

    def test_changed_source_or_model_does_not_reuse_split_plan_or_cached_claims(self):
        self.handler = lambda payload, options: LIMITED if len(payload['source_text']) == 120 else EMPTY
        first = self.analyze()
        self.source.extracted_text = 'B' * 120
        self.handler = lambda payload, options: EMPTY
        second = self.analyze(resume_state=first['ai_checkpoint'])
        self.assertEqual(self.requests[-1], (1, 0, 120))
        self.assertNotEqual(first['ai_checkpoint']['source_fingerprint'], second['ai_checkpoint']['source_fingerprint'])
        self.configuration.return_value['model'] = 'other-model'
        self.analyze(resume_state=second['ai_checkpoint'])
        self.assertEqual(self.requests[-1], (1, 0, 120))

    def test_invalid_split_tree_discards_checkpoint_instead_of_rebinding_saved_response(self):
        first = self.analyze()
        damaged = deepcopy(first['ai_checkpoint'])
        damaged['split_keys'] = ['unknown-parent']
        self.analyze(resume_state=damaged)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.requests[-1], (1, 0, 120))

    def test_changed_chunk_configuration_cannot_reuse_an_older_completed_job_key(self):
        project = SimpleNamespace(pk=9, id=9, updated_at='2026-09-25T00:00:00Z', files=Mock())
        project.files.filter.return_value.order_by.return_value.values.return_value = []
        original = operation_fingerprint(project, 'analyze', {})
        for setting, changed in [('CLAUDE_MAX_INPUT_CHARS', 3000), ('CLAUDE_INTELLIGENCE_MAX_TOKENS', 4000),
                                 ('AI_MIN_CHUNK_CHARS', 375), ('AI_MAX_SPLIT_DEPTH', 2), ('AI_MAX_CALLS_PER_PASS', 12)]:
            with self.subTest(setting=setting), patch(f'apps.planning_intelligence.config.{setting}', changed):
                self.assertNotEqual(operation_fingerprint(project, 'analyze', {}), original)


class AdaptiveRequirementRetentionTests(TestCase):
    @patch('apps.planning_intelligence.services.project_ai.get_project_ai_config', return_value=CONFIGURATION)
    @patch('apps.planning_intelligence.services.project_ai.call_project_ai')
    def test_prompt_dedup_retains_all_deterministic_lines_and_additional_multiline_requirement(self, provider, _configuration):
        lines = ['The contractor shall inspect the existing facilities.',
                 'The supplier must preserve source evidence.',
                 'The consultant is required to deliver the source register.']
        extra = 'Delivery includes commissioning records\nfor both existing units.'
        project = PlanningProject.objects.create(name='Requirement retention')
        source = PlanningFile.objects.create(project=project, category='sow', original_filename='requirements.txt',
            file='requirements.txt', parse_status='done', extracted_text='\n'.join(lines + [extra]))
        provider.return_value = {'text': json.dumps({'facts': [{'type': 'requirement', 'value': extra,
            'source_file_id': source.pk, 'quote': extra}]}), 'stop_reason': 'end_turn'}
        run, _ = run_document_intelligence(project)
        self.assertEqual(set(run.facts.filter(fact_type='requirement', extraction_method='deterministic')
                             .values_list('value', flat=True)), set(lines))
        self.assertEqual(run.facts.filter(fact_type='requirement', extraction_method='ai').get().value, extra)
        self.assertIn('Still extract their explicit deliverables', provider.call_args.kwargs['system_prompt'])
