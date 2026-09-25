"""Provider rejection diagnostics and bounded, resumable analysis passes."""
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from ..models import PlanningFile, PlanningProject
from ..services import claude_client, intelligence, project_ai


class AnthropicBadRequestDiagnosticTests(SimpleTestCase):
    secret = 'sk-ant-private-test-token'

    def failure(self, message=None, *, nested=True, status=400):
        error = Exception(self.secret)
        error.status_code = status
        body = {'type': 'invalid_request_error', 'message': message}
        error.body = {'type': 'error', 'error': body} if nested else body
        return error

    def test_explicit_credit_diagnostic_is_safe_and_actionable(self):
        for nested in (True, False):
            with self.subTest(nested=nested):
                result = claude_client._safe_failure(self.failure(
                    'Your credit balance is too low to access the Anthropic API. ' + self.secret,
                    nested=nested))
                self.assertEqual(result, {'provider': 'anthropic', 'code': 'credit_balance_exhausted', 'http_status': 400})
                guidance = project_ai.ai_failure_guidance(result)
                self.assertIn('insufficient API credits', guidance['message'])
                self.assertNotIn(self.secret, json.dumps(result) + json.dumps(guidance))

    def test_unknown_400_does_not_guess_authentication_or_billing(self):
        result = claude_client._safe_failure(self.failure('Unexpected rejected request ' + self.secret))
        self.assertEqual(result['code'], 'request_rejected')
        self.assertIn('does not identify a more specific cause', project_ai.ai_failure_guidance(result)['message'])
        self.assertNotIn(self.secret, json.dumps(result))

    def test_unstructured_or_malformed_bodies_do_not_raise(self):
        for body in (None, [], self.secret, {'error': []}, {'error': {'message': []}}):
            with self.subTest(body=type(body).__name__):
                error = self.failure()
                error.body = body
                self.assertEqual(claude_client._safe_failure(error)['code'], 'request_rejected')

    def test_error_excerpt_is_not_misclassified_as_billing(self):
        error = self.failure('Invalid request content: Contractor wrote "credit balance is too low".')
        self.assertEqual(claude_client._safe_failure(error)['code'], 'request_rejected')

    def test_model_parameter_and_input_failures_have_distinct_codes(self):
        for message, code in [
                ('model: synthetic-model does not exist', 'model_unavailable'),
                ('max_tokens: value exceeds allowed maximum', 'request_configuration_error'),
                ('thinking.type: invalid value', 'request_configuration_error'),
                ('output_config: unsupported field', 'request_configuration_error'),
                ('prompt is too long: 10001 tokens', 'input_limit'),
                ('Request is too large', 'input_limit')]:
            with self.subTest(code=code):
                result = claude_client._safe_failure(self.failure(message))
                self.assertEqual(result['code'], code)
                self.assertIsNotNone(project_ai.ai_failure_guidance(result))

    def test_http_413_uses_input_limit_without_exposing_body(self):
        result = claude_client._safe_failure(self.failure(self.secret, status=413))
        self.assertEqual(result['code'], 'input_limit')

    def test_authentication_and_quota_status_take_precedence_over_body(self):
        for status, code in [(401, 'invalid_api_key'), (403, 'permission_denied'),
                             (429, 'quota_exceeded'), (503, 'provider_unavailable')]:
            result = claude_client._safe_failure(self.failure('Your credit balance is too low', status=status))
            self.assertEqual(result['code'], code)


class ResumableProviderPauseTests(SimpleTestCase):
    empty = {'text': '{"facts": []}', 'stop_reason': 'end_turn'}

    def setUp(self):
        self.project = SimpleNamespace(ai_settings={'provider': 'anthropic'})
        self.source = PlanningFile(pk=1, category='sow', original_filename='synthetic.txt', extracted_text='A' * 500)
        self.configuration = {'provider': 'anthropic', 'model': 'synthetic-model'}
        self.requests, self.checkpoints, self.events = [], [], []
        self.handler = lambda payload, options: self.empty
        for patcher in [
                patch.object(intelligence, 'CLAUDE_MAX_INPUT_CHARS', 100),
                patch.object(intelligence, 'AI_MIN_CHUNK_CHARS', 25),
                patch.object(intelligence.project_ai, 'get_project_ai_config', return_value=self.configuration),
                patch.object(intelligence.project_ai, 'call_project_ai', side_effect=self.provider),
                patch.dict('os.environ', {'PLANNING_AI_MAX_CHUNKS': '64'})]:
            patcher.start()
            self.addCleanup(patcher.stop)

    def provider(self, project, **options):
        payload = json.loads(options['user_prompt'])
        self.requests.append(payload['character_start'])
        return self.handler(payload, options)

    def analyze(self, **options):
        return intelligence.analyze_project([self.source], project=self.project,
            checkpoint_callback=lambda value: self.checkpoints.append(deepcopy(value)),
            progress_callback=lambda value: self.events.append(deepcopy(value)), **options)

    def failure(self, options, code, status=400):
        options['error_details'].update(provider='anthropic', code=code, http_status=status)
        return None

    def test_credit_failure_stops_pass_and_resume_only_calls_unfinished_sections(self):
        self.handler = lambda payload, options: (self.empty if payload['character_start'] == 0
            else self.failure(options, 'credit_balance_exhausted'))
        failed = self.analyze()
        coverage = failed['ai_processing_coverage']
        self.assertEqual(self.requests, [0, 100])
        self.assertEqual((coverage['chunks_processed'], coverage['chunks_failed'], coverage['chunks_skipped']), (1, 1, 3))
        self.assertEqual(coverage['pause_error']['code'], 'credit_balance_exhausted')
        self.assertTrue(coverage['resume_available'])
        self.assertTrue(all(row['reason'] == 'provider_failure_pause' for row in coverage['chunks'][2:]))
        self.assertEqual(self.checkpoints[-1], failed['ai_checkpoint'])
        self.assertEqual(set(failed['ai_checkpoint']['chunks']), {'0'})
        self.handler = lambda payload, options: self.empty
        resumed = self.analyze(resume_state=failed['ai_checkpoint'])
        self.assertEqual(self.requests, [0, 100, 100, 200, 300, 400])
        self.assertEqual(resumed['ai_processing_coverage']['status'], 'complete')
        self.assertNotIn('pause_error', resumed['ai_processing_coverage'])

    def test_known_systemic_failures_stop_after_one_attempt(self):
        for code, status in [('invalid_api_key', 401), ('permission_denied', 403),
                             ('quota_exceeded', 429), ('model_unavailable', 404),
                             ('request_configuration_error', 400)]:
            with self.subTest(code=code):
                self.requests.clear()
                self.handler = lambda payload, options: self.failure(options, code, status)
                result = self.analyze()
                self.assertEqual(self.requests, [0])
                self.assertEqual(result['ai_processing_coverage']['chunks_skipped'], 4)
                self.assertTrue(result['ai_processing_coverage']['resume_available'])

    def test_unknown_rejections_stop_after_two_consecutive_attempts(self):
        self.handler = lambda payload, options: self.failure(options, 'request_rejected')
        result = self.analyze()
        self.assertEqual(self.requests, [0, 100])
        coverage = result['ai_processing_coverage']
        self.assertEqual((coverage['chunks_failed'], coverage['chunks_skipped']), (2, 3))
        self.assertEqual(coverage['calls_this_pass'], 2)
        self.assertEqual(coverage['chunks_remaining'], 5)
        self.assertEqual(coverage['pause_error']['code'], 'request_rejected')

    def test_success_between_unknown_rejections_does_not_stop_other_sections(self):
        self.handler = lambda payload, options: (self.failure(options, 'request_rejected')
            if payload['character_start'] in (0, 200) else self.empty)
        result = self.analyze()
        self.assertEqual(len(self.requests), 5)
        self.assertEqual(result['ai_processing_coverage']['chunks_processed'], 3)
        self.assertNotIn('pause_error', result['ai_processing_coverage'])

    def test_transient_timeout_is_not_misclassified_as_systemic_rejection(self):
        self.handler = lambda payload, options: self.failure(options, 'timeout', None)
        result = self.analyze()
        self.assertEqual(len(self.requests), 5)
        self.assertEqual(result['ai_processing_coverage']['chunks_failed'], 5)
        self.assertNotIn('pause_error', result['ai_processing_coverage'])

    def test_processed_chunks_after_failed_early_section_survive_later_pause(self):
        self.handler = lambda payload, options: (self.failure(options, 'timeout', None)
            if payload['character_start'] == 0 else self.empty)
        first = self.analyze()
        self.requests.clear()
        self.handler = lambda payload, options: self.failure(options, 'credit_balance_exhausted')
        resumed = self.analyze(resume_state=first['ai_checkpoint'])
        self.assertEqual(self.requests, [0])
        coverage = resumed['ai_processing_coverage']
        self.assertEqual((coverage['chunks_processed'], coverage['chunks_failed'], coverage['chunks_skipped']), (4, 1, 0))
        self.assertEqual(set(resumed['ai_checkpoint']['chunks']), {'1', '2', '3', '4'})

    def test_input_limit_splits_source_instead_of_pausing_all_sections(self):
        self.source.extracted_text = 'A' * 100
        self.handler = lambda payload, options: (self.failure(options, 'input_limit')
            if len(payload['source_text']) > 50 else self.empty)
        result = self.analyze()
        self.assertEqual(self.requests, [0, 0, 50])
        self.assertEqual(result['ai_processing_coverage']['status'], 'complete')
        self.assertEqual(result['ai_processing_coverage']['split_count'], 1)
        self.assertNotIn('pause_error', result['ai_processing_coverage'])

    def test_old_checkpoint_remains_compatible_with_circuit_breaker(self):
        with patch.dict('os.environ', {'PLANNING_AI_MAX_CHUNKS': '2'}):
            first = self.analyze()
        checkpoint = deepcopy(first['ai_checkpoint'])
        self.requests.clear()
        resumed = self.analyze(resume_state=checkpoint)
        self.assertEqual(self.requests, [200, 300, 400])
        self.assertEqual(resumed['ai_processing_coverage']['status'], 'complete')
        self.assertEqual(checkpoint, first['ai_checkpoint'])

    def test_cached_only_refresh_retains_literal_facts_and_previous_failures_without_requests(self):
        quote = 'Contractor shall prepare Permit Matrix.'
        self.source.extracted_text = quote + 'A' * (500 - len(quote))
        literal = {'text': json.dumps({'facts': [{'type': 'deliverable', 'value': 'Permit Matrix',
            'source_file_id': 1, 'quote': quote, 'discipline': None}]}), 'stop_reason': 'end_turn'}
        self.handler = lambda payload, options: (literal if payload['character_start'] == 0
            else self.failure(options, 'timeout', None))
        first = self.analyze()
        old_checkpoint = deepcopy(first['ai_checkpoint'])
        old_coverage = deepcopy(first['ai_processing_coverage'])
        self.requests.clear()
        self.handler = lambda *args: self.fail('Cached-only recovery called the provider')
        refreshed = self.analyze(allow_ai=False, resume_state=old_checkpoint, resume_coverage=old_coverage)
        self.assertEqual(self.requests, [])
        self.assertEqual(refreshed['ai_evidence_facts'], first['ai_evidence_facts'])
        self.assertEqual(len(refreshed['ai_evidence_facts']), 1)
        coverage = refreshed['ai_processing_coverage']
        self.assertEqual((coverage['chunks_processed'], coverage['chunks_failed'], coverage['chunks_remaining']), (1, 4, 4))
        self.assertEqual(coverage['calls_this_pass'], 0)
        self.assertTrue(coverage['cached_only'])
        self.assertTrue(coverage['resume_available'])
        self.assertTrue(all(unit['failure_from_previous_pass'] for unit in coverage['chunks'][1:]))
        self.assertEqual(old_checkpoint, first['ai_checkpoint'])
        self.assertEqual(old_coverage, first['ai_processing_coverage'])

    def test_cached_only_refresh_without_historical_coverage_defers_unfinished_sections(self):
        with patch.dict('os.environ', {'PLANNING_AI_MAX_CHUNKS': '2'}):
            first = self.analyze()
        self.requests.clear()
        refreshed = self.analyze(allow_ai=False, resume_state=first['ai_checkpoint'])
        self.assertEqual(self.requests, [])
        coverage = refreshed['ai_processing_coverage']
        self.assertEqual((coverage['chunks_processed'], coverage['chunks_skipped'], coverage['chunks_remaining']), (2, 3, 3))
        self.assertEqual(coverage['status'], 'partial')
        self.assertEqual(set(refreshed['ai_checkpoint']['chunks']), {'0', '1'})

    def test_cached_only_refresh_does_not_reuse_changed_source_or_model_checkpoint(self):
        first = self.analyze()
        for change in ('source', 'model'):
            with self.subTest(change=change):
                self.source.extracted_text = 'B' * 500 if change == 'source' else 'A' * 500
                self.configuration['model'] = 'other-model' if change == 'model' else 'synthetic-model'
                self.requests.clear()
                refreshed = self.analyze(allow_ai=False, resume_state=first['ai_checkpoint'],
                                         resume_coverage=first['ai_processing_coverage'])
                self.assertEqual(self.requests, [])
                self.assertEqual(refreshed['ai_evidence_facts'], [])
                self.assertEqual(refreshed['ai_processing_coverage']['chunks_processed'], 0)
                self.assertEqual(refreshed['ai_processing_coverage']['chunks_skipped'], 5)
                self.assertEqual(refreshed['ai_checkpoint']['chunks'], {})
                self.assertIn('incompatible', refreshed['ai_processing_coverage']['reason'])

    def test_cached_only_refresh_does_not_accept_failure_from_another_source_range(self):
        self.handler = lambda payload, options: self.failure(options, 'timeout', None)
        first = self.analyze()
        coverage = deepcopy(first['ai_processing_coverage'])
        for row in coverage['chunks']:
            row['character_start'] += 1
        self.requests.clear()
        refreshed = self.analyze(allow_ai=False, resume_state=first['ai_checkpoint'], resume_coverage=coverage)
        self.assertEqual(self.requests, [])
        self.assertEqual(refreshed['ai_processing_coverage']['chunks_failed'], 0)
        self.assertEqual(refreshed['ai_processing_coverage']['chunks_skipped'], 5)

    def test_cached_only_refresh_sanitizes_historical_error_metadata(self):
        self.handler = lambda payload, options: self.failure(options, 'request_rejected')
        first = self.analyze()
        coverage = deepcopy(first['ai_processing_coverage'])
        private = 'sk-ant-private-test-token'
        coverage['chunks'][0]['error'].update(message=private, next_action=private,
                                             http_status=private, response=private)
        self.requests.clear()
        refreshed = self.analyze(allow_ai=False, resume_state=first['ai_checkpoint'], resume_coverage=coverage)
        self.assertEqual(self.requests, [])
        self.assertNotIn(private, json.dumps(refreshed))
        self.assertEqual(refreshed['ai_processing_coverage']['chunks'][0]['error']['code'], 'request_rejected')

    def test_cached_only_refresh_can_later_resume_only_remaining_sections(self):
        with patch.dict('os.environ', {'PLANNING_AI_MAX_CHUNKS': '2'}):
            first = self.analyze()
        refreshed = self.analyze(allow_ai=False, resume_state=first['ai_checkpoint'])
        self.requests.clear()
        resumed = self.analyze(resume_state=refreshed['ai_checkpoint'])
        self.assertEqual(self.requests, [200, 300, 400])
        self.assertEqual(resumed['ai_processing_coverage']['status'], 'complete')

    def test_disabled_ai_without_checkpoint_remains_deterministic_only(self):
        result = self.analyze(allow_ai=False)
        self.assertEqual(self.requests, [])
        self.assertEqual(result['ai_evidence_facts'], [])
        self.assertEqual(result['ai_processing_coverage']['status'], 'not_run')
        self.assertNotIn('ai_checkpoint', result)


class CachedOnlyRunRecoveryTests(TestCase):
    def test_new_run_rebuilds_facts_from_saved_responses_and_preserves_original_run(self):
        from ..services.document_intelligence import run_document_intelligence

        project = PlanningProject.objects.create(name='Synthetic cached recovery')
        quote = 'Contractor shall prepare Permit Matrix.'
        source = PlanningFile.objects.create(project=project, category='sow', original_filename='scope.txt',
            file='scope.txt', parse_status='done', extracted_text=quote + 'A' * (500 - len(quote)))
        configuration = {'provider': 'anthropic', 'model': 'synthetic-model'}
        literal = {'text': json.dumps({'facts': [{'type': 'deliverable', 'value': 'Permit Matrix',
            'source_file_id': source.pk, 'quote': quote, 'discipline': None}]}), 'stop_reason': 'end_turn'}

        def first_pass(project, **options):
            if json.loads(options['user_prompt'])['character_start'] == 0:
                return literal
            options['error_details'].update(provider='anthropic', code='timeout', http_status=None)
            return None

        with patch.object(intelligence, 'CLAUDE_MAX_INPUT_CHARS', 100), \
                patch.object(intelligence.project_ai, 'get_project_ai_config', return_value=configuration), \
                patch.object(intelligence.project_ai, 'call_project_ai', side_effect=first_pass) as provider, \
                patch.dict('os.environ', {'PLANNING_AI_MAX_CHUNKS': '64'}):
            original, _ = run_document_intelligence(project)
            original_summary = deepcopy(original.summary)
            original_fact_count = original.facts.count()
            provider.reset_mock()
            refreshed, result = run_document_intelligence(project, allow_ai=False, resume_run=original)
            provider.assert_not_called()
        self.assertNotEqual(original.pk, refreshed.pk)
        original.refresh_from_db()
        self.assertEqual(original.summary, original_summary)
        self.assertEqual(original.facts.count(), original_fact_count)
        self.assertEqual(refreshed.facts.filter(extraction_method='ai', fact_type='deliverable').count(), 1)
        ai = result['processing_coverage']['ai_processing']
        self.assertEqual((ai['chunks_processed'], ai['chunks_remaining'], ai['calls_this_pass']), (1, 4, 0))
        self.assertEqual(ai['chunks_failed'], 4)
        self.assertEqual(ai['chunks'][1]['error']['code'], 'timeout')
        self.assertTrue(ai['chunks'][1]['failure_from_previous_pass'])
        self.assertTrue(ai['cached_only'])
        self.assertTrue(ai['resume_available'])
        self.assertEqual(refreshed.summary['resumed_from_run_id'], original.pk)

    def test_cached_only_recovery_rejects_foreign_project_run_with_identical_text(self):
        from ..models import DocumentIntelligenceRun
        from ..services.document_intelligence import ResumeSourceChanged, run_document_intelligence

        projects = [PlanningProject.objects.create(name=name) for name in ('First unrelated project', 'Second unrelated project')]
        for project in projects:
            PlanningFile.objects.create(project=project, category='sow', original_filename='same-scope.txt',
                file='same-scope.txt', parse_status='done', extracted_text='Identical source text. ' * 8)
        configuration = {'provider': 'anthropic', 'model': 'synthetic-model'}
        with patch.object(intelligence, 'CLAUDE_MAX_INPUT_CHARS', 80), \
                patch.object(intelligence.project_ai, 'get_project_ai_config', return_value=configuration), \
                patch.object(intelligence.project_ai, 'call_project_ai', return_value={'text': '{"facts": []}'}) as provider, \
                patch.dict('os.environ', {'PLANNING_AI_MAX_CHUNKS': '1'}):
            original, _ = run_document_intelligence(projects[0])
            self.assertTrue(original.summary['ai_checkpoint']['chunks'])
            original_summary = deepcopy(original.summary)
            provider.reset_mock()
            with self.assertRaises(ResumeSourceChanged):
                run_document_intelligence(projects[1], allow_ai=False, resume_run=original)
            provider.assert_not_called()
        self.assertEqual(DocumentIntelligenceRun.objects.count(), 1)
        original.refresh_from_db()
        self.assertEqual(original.summary, original_summary)
        self.assertFalse(projects[1].intelligence_runs.exists())


class MultiDocumentProviderRecoveryTests(SimpleTestCase):
    """Shared recovery does not depend on one provider, project or source file."""

    def setUp(self):
        self.project = SimpleNamespace(pk=987, ai_settings={})
        self.configuration = {'model': 'synthetic-same-model'}
        for patcher in [
                patch.object(intelligence, 'CLAUDE_MAX_INPUT_CHARS', 40),
                patch.object(project_ai, 'get_project_ai_config', return_value=self.configuration),
                patch.dict('os.environ', {'PLANNING_AI_MAX_CHUNKS': '64'})]:
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(project_ai, 'call_project_ai', side_effect=self.literal_response)
        self.provider = patcher.start()
        self.addCleanup(patcher.stop)

    def prepare(self, provider):
        self.project.ai_settings = {'provider': provider}
        self.configuration['provider'] = provider
        self.sources = [SimpleNamespace(pk=1001 + index, category='other', original_filename=f'unrelated-{index}.txt',
            extracted_text=quote + 'A' * (80 - len(quote))) for index, quote in enumerate((
                'Prepare Permit Matrix.', 'Prepare Interface Register.'))]
        self.provider.side_effect = self.literal_response
        self.provider.reset_mock()

    def literal_response(self, project, **options):
        payload = json.loads(options['user_prompt'])
        facts = []
        if payload['character_start'] == 0:
            quote = payload['source_text'].split('.')[0] + '.'
            facts.append({'type': 'deliverable', 'value': quote.removeprefix('Prepare ').removesuffix('.'),
                          'source_file_id': payload['source_file_id'], 'quote': quote, 'discipline': None})
        return {'text': json.dumps({'facts': facts}), 'stop_reason': 'end_turn'}

    def initial(self, provider):
        self.prepare(provider)
        with patch.dict('os.environ', {'PLANNING_AI_MAX_CHUNKS': '3'}):
            return intelligence.analyze_project(self.sources, project=self.project)

    def test_both_providers_reuse_facts_across_two_documents_without_requests(self):
        for provider in ('anthropic', 'gemini'):
            with self.subTest(provider=provider):
                initial = self.initial(provider)
                self.assertEqual(self.provider.call_count, 3)
                self.assertEqual({fact['source_file_id'] for fact in initial['ai_evidence_facts']}, {1001, 1002})
                self.provider.reset_mock()
                recovered = intelligence.analyze_project(self.sources, project=self.project, allow_ai=False,
                    resume_state=initial['ai_checkpoint'], resume_coverage=initial['ai_processing_coverage'])
                self.provider.assert_not_called()
                self.assertEqual(recovered['ai_evidence_facts'], initial['ai_evidence_facts'])
                ai = recovered['ai_processing_coverage']
                self.assertEqual((ai['chunks_processed'], ai['chunks_remaining'], ai['calls_this_pass']), (3, 1, 0))
                self.assertEqual(ai['chunks_skipped'], 1)
                self.assertTrue(ai['resume_available'])
                self.assertEqual(recovered['ai_provider_used'], provider)

    def test_both_providers_reject_checkpoint_when_one_document_identity_changes(self):
        for provider in ('anthropic', 'gemini'):
            with self.subTest(provider=provider):
                initial = self.initial(provider)
                self.sources[0].pk = 9001
                self.provider.reset_mock()
                recovered = intelligence.analyze_project(self.sources, project=self.project, allow_ai=False,
                    resume_state=initial['ai_checkpoint'], resume_coverage=initial['ai_processing_coverage'])
                self.provider.assert_not_called()
                self.assertEqual(recovered['ai_evidence_facts'], [])
                self.assertEqual(recovered['ai_processing_coverage']['chunks_processed'], 0)
                self.assertEqual(recovered['ai_processing_coverage']['chunks_remaining'], 4)
                self.assertEqual(recovered['ai_checkpoint']['chunks'], {})

    def test_provider_switch_rejects_cache_even_when_model_and_documents_are_unchanged(self):
        for provider, replacement in (('anthropic', 'gemini'), ('gemini', 'anthropic')):
            with self.subTest(provider=provider, replacement=replacement):
                initial = self.initial(provider)
                self.project.ai_settings['provider'] = replacement
                self.configuration['provider'] = replacement
                self.provider.reset_mock()
                recovered = intelligence.analyze_project(self.sources, project=self.project, allow_ai=False,
                    resume_state=initial['ai_checkpoint'])
                self.provider.assert_not_called()
                self.assertEqual(recovered['ai_evidence_facts'], [])
                self.assertEqual(recovered['ai_processing_coverage']['chunks_processed'], 0)
                self.assertEqual(recovered['ai_processing_coverage']['chunks_remaining'], 4)

    def test_both_providers_pause_all_remaining_document_sections_after_quota_failure(self):
        for provider in ('anthropic', 'gemini'):
            with self.subTest(provider=provider):
                self.prepare(provider)

                def quota(project, **options):
                    options['error_details'].update(provider=provider, code='quota_exceeded', http_status=429)
                    return None

                self.provider.side_effect = quota
                result = intelligence.analyze_project(self.sources, project=self.project)
                self.provider.assert_called_once()
                ai = result['ai_processing_coverage']
                self.assertEqual((ai['chunks_failed'], ai['chunks_skipped'], ai['chunks_remaining']), (1, 3, 4))
                self.assertEqual(ai['calls_this_pass'], 1)
                self.assertEqual(ai['pause_error']['provider'], provider)
                self.assertTrue(all(chunk['reason'] == 'provider_failure_pause' for chunk in ai['chunks'][1:]))
