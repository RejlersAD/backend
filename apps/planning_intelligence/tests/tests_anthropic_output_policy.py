"""Bounded document extraction through the actual SDK and an in-memory transport."""
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import anthropic
from anthropic import _base_client as anthropic_base_client
from django.test import SimpleTestCase

from ..services import claude_client, project_ai
from ..services.analysis_result import analysis_result
from ..services.document_intelligence import ENGINE_VERSION
from ..services.intelligence import analyze_project
from ..services.operational_jobs import canonical_fingerprint, operation_fingerprint


httpx = getattr(anthropic_base_client, 'httpx2', None) or anthropic_base_client.httpx


class AnthropicOutputPolicyTests(SimpleTestCase):
    def setUp(self):
        self.key = 'sk-ant-synthetic-output-policy-credential'
        self.project = SimpleNamespace(pk=9, id=9, ai_settings={'provider': 'anthropic'})
        self.configuration = {'api_key': self.key, 'model': 'claude-opus-5'}
        self.source = SimpleNamespace(pk=2, extracted_text='Contractor shall prepare Permit Matrix.',
                                      category='sow', original_filename='scope.txt')
        configuration = patch.object(claude_client, 'get_claude_config', return_value=self.configuration)
        configuration.start()
        self.addCleanup(configuration.stop)
        self.requests, self.clients = [], []
        self.handler = lambda request: self.stream_response()
        sdk_class = anthropic.Anthropic

        def handle(request):
            self.requests.append(request)
            return self.handler(request)

        def client(**kwargs):
            result = sdk_class(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handle)))
            self.clients.append(result)
            return result

        sdk = patch('anthropic.Anthropic', side_effect=client)
        sdk.start()
        self.addCleanup(sdk.stop)

    def stream_response(self, *, text='{"facts": []}', stop_reason='end_turn',
                        output_tokens=8, thinking=False, terminal=True):
        events = []

        def emit(kind, **data):
            events.append((f'event: {kind}\ndata: ' + json.dumps({'type': kind, **data}) + '\n\n').encode())

        emit('message_start', message={
            'id': 'synthetic-output', 'type': 'message', 'role': 'assistant',
            'model': self.configuration['model'], 'content': [], 'stop_reason': None, 'stop_sequence': None,
            'usage': {'input_tokens': 30000, 'output_tokens': 0},
        })
        if thinking:
            # Private reasoning must never be returned as extraction text or diagnostics.
            emit('content_block_start', index=0,
                 content_block={'type': 'thinking', 'thinking': '', 'signature': ''})
            emit('content_block_delta', index=0, delta={'type': 'thinking_delta', 'thinking': self.key})
            emit('content_block_stop', index=0)
        if text is not None:
            index = int(thinking)
            emit('content_block_start', index=index, content_block={'type': 'text', 'text': ''})
            emit('content_block_delta', index=index, delta={'type': 'text_delta', 'text': text})
            emit('content_block_stop', index=index)
        emit('message_delta', delta={'stop_reason': stop_reason, 'stop_sequence': None},
             usage={'output_tokens': output_tokens})
        if terminal:
            emit('message_stop')
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=b''.join(events))

    def call(self, *, feature='document_intelligence', **options):
        return project_ai.call_project_ai(self.project, system_prompt='Extract quoted facts.',
            user_prompt=self.source.extracted_text, max_tokens=6000, feature=feature,
            json_output=True, **options)

    def test_verified_models_disable_thinking_without_changing_budget_or_retry_policy(self):
        for model in ('claude-opus-5', 'claude-sonnet-5'):
            with self.subTest(model=model):
                self.configuration['model'] = model
                self.assertIsNotNone(self.call())
                payload = json.loads(self.requests[-1].content)
                self.assertEqual(payload['model'], model)
                self.assertEqual(payload['thinking'], {'type': 'disabled'})
                self.assertEqual(payload['output_config'], {'effort': 'high'})
                self.assertEqual(payload['max_tokens'], 6000)
                self.assertTrue(payload['stream'])
                self.assertEqual(self.clients[-1].max_retries, 0)
                self.assertTrue(self.clients[-1].is_closed())
        self.assertEqual(len(self.requests), 2)

    def test_policy_is_not_inferred_for_other_or_future_models(self):
        for model in ('claude-haiku-4-5-20251001', 'future-model'):
            with self.subTest(model=model):
                self.configuration['model'] = model
                self.assertIsNotNone(self.call())
                payload = json.loads(self.requests[-1].content)
                self.assertNotIn('thinking', payload)
                self.assertNotIn('output_config', payload)

    def test_connection_and_narrative_requests_keep_existing_nonstreaming_policy(self):
        self.handler = lambda request: httpx.Response(200, json={
            'id': 'synthetic-output', 'type': 'message', 'role': 'assistant', 'model': self.configuration['model'],
            'content': [{'type': 'text', 'text': 'OK'}], 'stop_reason': 'end_turn', 'stop_sequence': None,
            'usage': {'input_tokens': 20, 'output_tokens': 4},
        })
        self.assertTrue(project_ai.test_project_ai_connection(self.project)['success'])
        self.assertIsNotNone(self.call(feature='narrative'))
        for request in self.requests:
            payload = json.loads(request.content)
            self.assertFalse(payload.get('stream', False))
            self.assertNotIn('thinking', payload)
            self.assertNotIn('output_config', payload)
        self.assertEqual(json.loads(self.requests[0].content)['max_tokens'], 512)

    def test_thinking_only_at_output_cap_reports_limit_and_preserves_safe_usage(self):
        self.handler = lambda request: self.stream_response(text=None, thinking=True,
                                                            stop_reason='max_tokens', output_tokens=6000)
        errors, progress = {}, Mock()
        with patch('apps.rbac.ai_telemetry.record_usage') as usage:
            result = self.call(error_details=errors, progress_callback=progress, user=SimpleNamespace(pk=1))
        self.assertIsNone(result)
        self.assertEqual(errors, {
            'provider': 'anthropic', 'code': 'output_limit', 'http_status': None,
            'stop_reason': 'max_tokens', 'tokens_input': 30000, 'tokens_output': 6000, 'max_tokens': 6000,
        })
        self.assertEqual(project_ai.ai_failure_guidance(errors)['next_action'], 'retry_analysis')
        self.assertFalse(usage.call_args.kwargs['success'])
        self.assertEqual(usage.call_args.kwargs['error_code'], 'output_limit')
        self.assertEqual(usage.call_args.kwargs['tokens_output'], 6000)
        self.assertNotIn(self.key, json.dumps(errors) + str(usage.call_args))
        progress.assert_not_called()
        self.assertEqual(len(self.requests), 1)

    def test_context_limit_is_specific_but_ordinary_empty_response_remains_distinct(self):
        for stop_reason, code in [('model_context_window_exceeded', 'output_limit'), ('end_turn', 'empty_response')]:
            with self.subTest(stop_reason=stop_reason):
                self.handler = lambda request: self.stream_response(text=None, stop_reason=stop_reason)
                errors = {}
                self.assertIsNone(self.call(error_details=errors))
                self.assertEqual(errors['code'], code)
                self.assertNotIn(self.key, json.dumps(errors))

    def test_final_stream_keeps_text_and_usage_without_thinking_content(self):
        self.handler = lambda request: self.stream_response(thinking=True)
        errors = {'old': self.key}
        result = self.call(error_details=errors)
        self.assertEqual(result['text'], '{"facts": []}')
        self.assertEqual(result['tokens_output'], 8)
        self.assertEqual(result['stop_reason'], 'end_turn')
        self.assertEqual(errors, {})
        self.assertNotIn(self.key, json.dumps(result))

    def test_truncated_json_retains_specific_limit_in_chunk_and_outcome(self):
        self.handler = lambda request: self.stream_response(text='{"facts": [', stop_reason='max_tokens', output_tokens=6000)
        result = analyze_project([self.source], project=self.project)
        coverage = result['ai_processing_coverage']
        self.assertEqual(coverage['chunks_failed'], 1)
        self.assertEqual(coverage['chunks_processed'], 0)
        self.assertEqual(coverage['chunks'][0]['error']['code'], 'output_limit')
        self.assertEqual(result['ai_checkpoint']['chunks'], {})
        outcome = analysis_result(self.project, {'intelligence_run_id': 1, 'tasks': [],
            'extraction_summary': {'facts_by_type': {'requirement': 1}},
            'processing_coverage': {'ai_processing': coverage}})
        self.assertEqual(outcome['ai_error_code'], 'output_limit')
        self.assertEqual(outcome['next_action'], 'retry_analysis')
        self.assertIn('response token or context limit', outcome['message'])
        self.assertNotIn('Check AI settings', outcome['message'])

    def test_parseable_limited_json_remains_partial_and_never_becomes_checkpoint(self):
        self.handler = lambda request: self.stream_response(stop_reason='max_tokens', output_tokens=6000)
        result = analyze_project([self.source], project=self.project)
        self.assertEqual(result['ai_processing_coverage']['chunks_partial'], 1)
        self.assertEqual(result['ai_processing_coverage']['chunks_processed'], 0)
        self.assertEqual(result['ai_checkpoint']['chunks'], {})

    def test_missing_terminal_event_is_not_misreported_as_output_limit(self):
        self.handler = lambda request: self.stream_response(text=None, thinking=True,
                                                            stop_reason='max_tokens', terminal=False)
        errors = {}
        self.assertIsNone(self.call(error_details=errors))
        self.assertEqual(errors['code'], 'incomplete_response')
        self.assertNotIn('tokens_output', errors)
        self.assertEqual(len(self.requests), 1)

    def test_untrusted_failure_details_cannot_supply_display_message(self):
        guidance = project_ai.ai_failure_guidance({
            'provider': 'anthropic', 'code': 'output_limit', 'http_status': self.key,
            'message': self.key, 'stop_reason': self.key, 'tokens_output': self.key,
        })
        self.assertEqual(guidance['next_action'], 'retry_analysis')
        self.assertIsNone(guidance['http_status'])
        self.assertNotIn(self.key, json.dumps(guidance))

    def test_corrected_analysis_does_not_share_a_completed_streaming_job_key(self):
        self.project.updated_at = '2026-09-25T12:00:00Z'
        self.project.files = Mock()
        self.project.files.filter.return_value.order_by.return_value.values.return_value = []
        previous = canonical_fingerprint({
            'operation': 'analyze-v5-streamed-ai', 'project_id': self.project.pk,
            'engine_version': ENGINE_VERSION, 'project_updated_at': self.project.updated_at, 'files': [],
        })
        current = operation_fingerprint(self.project, 'analyze', {})
        self.assertNotEqual(current, previous)
        self.assertEqual(current, operation_fingerprint(self.project, 'analyze', {}))
