"""Transient provider failures retain actionable diagnostics without secrets."""
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
import anthropic
from anthropic import _base_client as anthropic_base_client
from django.test import SimpleTestCase

from ..services import byok_crypto, claude_client, project_ai
from ..services.analysis_result import analysis_result
from ..services.intelligence import analyze_project

# Exercise the HTTP transport actually used by the installed SDK (0.x or 1.x).
httpx = getattr(anthropic_base_client, 'httpx2', None) or anthropic_base_client.httpx


class GeminiFailureRecoveryTests(SimpleTestCase):
    def setUp(self):
        self.key = 'gemini-synthetic-private-credential'
        self.project = SimpleNamespace(pk=7, ai_settings={
            'enabled': True, 'provider': 'gemini', 'api_key_provider': 'gemini',
            'api_key_encrypted': byok_crypto.encrypt_api_key(self.key),
            'model': project_ai.DEFAULT_GEMINI_MODEL,
        })
        self.source = SimpleNamespace(pk=1, extracted_text='Contractor shall prepare Permit Matrix.',
                                      category='sow', original_filename='scope.txt')
        for name, target, options in [
            ('post', project_ai.requests, {'attribute': 'post'}),
            ('sleep', project_ai.time, {'attribute': 'sleep'}),
            ('enabled', project_ai, {'attribute': 'GEMINI_BYOK_ENABLED', 'new': True}),
        ]:
            patcher = patch.object(target, **options)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def response(self, status=200):
        response = Mock(status_code=status)
        response.json.return_value = ({'candidates': [{'finishReason': 'STOP',
            'content': {'parts': [{'text': json.dumps({'facts': [{'type': 'deliverable',
                'value': 'Permit Matrix', 'source_file_id': self.source.pk,
                'quote': self.source.extracted_text}]})}]}}]} if status == 200 else
            {'error': {'message': self.key}})
        return response

    def call(self, **options):
        return project_ai.call_project_ai(self.project, system_prompt='Extract quoted facts.',
            user_prompt=self.source.extracted_text, max_tokens=6000,
            feature='document_intelligence', **options)

    def outcome(self, coverage):
        return analysis_result(self.project, {'intelligence_run_id': 1, 'tasks': [],
            'extraction_summary': {'facts_by_type': {'requirement': 221}},
            'processing_coverage': {'ai_processing': coverage}})

    def test_transient_server_error_retries_same_model_once_and_recovers_validated_fact(self):
        for status in (500, 502, 503, 504):
            with self.subTest(status=status):
                self.post.reset_mock()
                self.sleep.reset_mock()
                failure, success = self.response(status), self.response()
                self.post.side_effect = [failure, success]
                with patch('apps.rbac.ai_telemetry.record_usage') as usage:
                    result = analyze_project([self.source], project=self.project, user=SimpleNamespace(pk=1))
                self.assertEqual(self.post.call_count, 2)
                self.assertEqual(self.post.call_args_list[0], self.post.call_args_list[1])
                failure.close.assert_called_once()
                self.sleep.assert_called_once_with(1)
                self.assertEqual(result['ai_processing_coverage']['status'], 'complete')
                self.assertEqual(result['ai_evidence_facts'][0]['value'], 'Permit Matrix')
                self.assertNotIn('error', result['ai_processing_coverage']['chunks'][0])
                usage.assert_called_once()
                self.assertTrue(usage.call_args.kwargs['success'])

    def test_persistent_503_is_bounded_and_preserved_without_key_or_raw_body(self):
        self.post.return_value = self.response(503)
        with self.assertLogs(project_ai.logger, level='WARNING') as logs:
            result = analyze_project([self.source], project=self.project)
        coverage = result['ai_processing_coverage']
        self.assertEqual(self.post.call_count, 2)
        self.sleep.assert_called_once_with(1)
        self.assertEqual(coverage['chunks_failed'], 1)
        error = coverage['chunks'][0]['error']
        self.assertEqual({key: error[key] for key in ('provider', 'code', 'http_status')}, {
            'provider': 'gemini', 'code': 'provider_unavailable', 'http_status': 503})
        self.assertEqual(error['next_action'], 'retry_analysis')
        self.assertIn('temporarily unavailable', error['message'])
        self.assertEqual(result['ai_checkpoint']['chunks'], {})
        outcome = self.outcome(coverage)
        self.assertEqual(outcome['code'], 'ai_analysis_incomplete')
        self.assertEqual(outcome['ai_error_code'], 'provider_unavailable')
        self.assertEqual(outcome['ai_http_status'], 503)
        self.assertEqual(outcome['next_action'], 'retry_analysis')
        self.assertIn('221 requirements', outcome['message'])
        self.assertIn('temporarily unavailable', outcome['message'])
        self.assertNotIn('Check AI settings', outcome['message'])
        self.assertNotIn(self.key, json.dumps(result) + json.dumps(outcome) + str(logs.output))

    def test_credentials_permissions_model_and_quota_errors_are_not_retried(self):
        for status, code, action in [(401, 'invalid_api_key', 'ai_settings'),
                                     (403, 'permission_denied', 'ai_settings'),
                                     (404, 'model_unavailable', 'ai_settings'),
                                     (429, 'quota_exceeded', 'retry_analysis')]:
            with self.subTest(status=status):
                self.post.reset_mock()
                self.post.return_value = self.response(status)
                result = analyze_project([self.source], project=self.project)
                self.post.assert_called_once()
                self.sleep.assert_not_called()
                outcome = self.outcome(result['ai_processing_coverage'])
                self.assertEqual(outcome['ai_error_code'], code)
                self.assertEqual(outcome['next_action'], action)
                self.assertNotIn(self.key, json.dumps(result) + json.dumps(outcome))

    def test_timeout_is_not_replayed_and_safe_cause_is_collected(self):
        self.post.side_effect = requests.Timeout(self.key)
        errors = {'old_error': self.key}
        self.assertIsNone(self.call(error_details=errors))
        self.post.assert_called_once()
        self.sleep.assert_not_called()
        self.assertEqual(errors, {'provider': 'gemini', 'code': 'timeout', 'http_status': None})

    def test_success_clears_previous_failure_details(self):
        self.post.return_value = self.response()
        errors = {'provider': 'gemini', 'code': 'provider_unavailable', 'http_status': 503}
        self.assertIsNotNone(self.call(error_details=errors))
        self.assertEqual(errors, {})

    def test_rendered_guidance_ignores_untrusted_stored_message_and_http_status(self):
        coverage = {'status': 'partial', 'chunks_failed': 1, 'chunks': [{
            'status': 'failed', 'error': {'provider': 'gemini', 'code': 'provider_unavailable',
                                       'http_status': self.key, 'message': self.key}}]}
        outcome = self.outcome(coverage)
        self.assertIsNone(outcome['ai_http_status'])
        self.assertIn('temporarily unavailable', outcome['message'])
        self.assertNotIn(self.key, json.dumps(outcome))


class AnthropicFailureRecoveryTests(SimpleTestCase):
    def setUp(self):
        self.key = 'sk-ant-synthetic-private-credential'
        self.project = SimpleNamespace(pk=9, id=9, ai_settings={'provider': 'anthropic'})
        self.source = SimpleNamespace(pk=2, extracted_text='Contractor shall prepare Permit Matrix.',
                                      category='sow', original_filename='scope.txt')
        configuration = patch.object(claude_client, 'get_claude_config', return_value={
            'api_key': self.key, 'model': 'synthetic-model',
        })
        configuration.start()
        self.addCleanup(configuration.stop)
        sdk = patch('anthropic.Anthropic')
        self.sdk = sdk.start()
        self.addCleanup(sdk.stop)
        self.sdk.return_value.__enter__.return_value = self.sdk.return_value
        self.create = self.sdk.return_value.messages.create
        self.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(type='text', text='{"facts": []}')],
            usage=SimpleNamespace(input_tokens=15, output_tokens=5), stop_reason='end_turn',
        )
        self.stream = self.sdk.return_value.messages.stream
        self.stream.return_value.__enter__.return_value.__iter__.side_effect = lambda: iter([
            SimpleNamespace(type='message_stop', message=self.create.return_value),
        ])

    def failure(self, status=None, name='APIStatusError'):
        error = type(name, (Exception,), {})(self.key + ': private provider response')
        error.status_code = status
        return error

    def call(self, **options):
        return project_ai.call_project_ai(self.project, system_prompt='Extract quoted facts.',
            user_prompt=self.source.extracted_text, max_tokens=1000,
            feature='document_intelligence', **options)

    def test_authentication_failure_is_recorded_as_safe_actionable_chunk_error(self):
        self.stream.side_effect = self.failure(401, 'AuthenticationError')
        with self.assertLogs(claude_client.logger, level='WARNING') as logs, \
                patch('apps.rbac.ai_telemetry.record_usage') as usage:
            result = analyze_project([self.source], project=self.project, user=SimpleNamespace(pk=1))
        coverage = result['ai_processing_coverage']
        self.assertEqual(coverage['status'], 'partial')
        self.assertEqual(coverage['chunks_processed'], 0)
        self.assertEqual(coverage['chunks_failed'], 1)
        self.assertTrue(coverage['resume_available'])
        self.assertEqual(result['ai_checkpoint']['chunks'], {})
        error = coverage['chunks'][0]['error']
        self.assertEqual(error['provider'], 'anthropic')
        self.assertEqual(error['code'], 'invalid_api_key')
        self.assertEqual(error['http_status'], 401)
        self.assertEqual(error['next_action'], 'ai_settings')
        self.assertIn('Anthropic returned HTTP 401', error['message'])
        self.assertIn('save an active Anthropic key', error['message'])
        self.assertFalse(usage.call_args.kwargs['success'])
        self.assertEqual(usage.call_args.kwargs['error_code'], 'invalid_api_key')
        self.assertNotIn(self.key, json.dumps(result) + str(logs.output) + str(usage.call_args))
        self.stream.assert_called_once()

    def test_http_failures_have_provider_specific_guidance_without_raw_bodies(self):
        for status, code, action in [(403, 'permission_denied', 'ai_settings'),
                                     (404, 'model_unavailable', 'ai_settings'),
                                     (429, 'quota_exceeded', 'retry_analysis'),
                                     (503, 'provider_unavailable', 'retry_analysis'),
                                     (400, 'request_rejected', 'ai_settings')]:
            with self.subTest(status=status):
                self.stream.reset_mock()
                self.stream.side_effect = self.failure(status)
                details = {}
                self.assertIsNone(self.call(error_details=details))
                guidance = project_ai.ai_failure_guidance(details)
                self.assertEqual(details, {'provider': 'anthropic', 'code': code, 'http_status': status})
                self.assertEqual(guidance['next_action'], action)
                self.assertNotIn(self.key, json.dumps(details) + json.dumps(guidance))
                self.stream.assert_called_once()

    def test_timeout_connection_and_unknown_exceptions_do_not_leak_or_raise(self):
        for name, code in [('APITimeoutError', 'timeout'), ('APIConnectionError', 'connection_error'),
                           ('UnexpectedProviderError', 'invalid_response')]:
            with self.subTest(name=name):
                self.stream.side_effect = self.failure(name=name)
                details = {'old': self.key}
                self.assertIsNone(self.call(error_details=details))
                self.assertEqual(details, {'provider': 'anthropic', 'code': code, 'http_status': None})

    def test_success_clears_error_details_and_preserves_response_contract(self):
        details = {'provider': 'anthropic', 'code': 'invalid_api_key', 'http_status': 401}
        result = self.call(error_details=details)
        self.assertEqual(details, {})
        self.assertEqual(result['text'], '{"facts": []}')
        self.assertEqual(result['tokens_input'], 15)
        self.assertEqual(result['tokens_output'], 5)
        self.assertEqual(result['stop_reason'], 'end_turn')

    def test_empty_response_is_visible_and_successful_retry_covers_failed_chunk(self):
        self.create.return_value.content = []
        failed = analyze_project([self.source], project=self.project)
        self.assertEqual(failed['ai_processing_coverage']['chunks'][0]['error']['code'], 'empty_response')
        self.create.return_value.content = [SimpleNamespace(type='text', text='{"facts": []}')]
        recovered = analyze_project([self.source], project=self.project, resume_state=failed['ai_checkpoint'])
        self.assertEqual(recovered['ai_processing_coverage']['status'], 'complete')
        self.assertEqual(recovered['ai_processing_coverage']['chunks_failed'], 0)
        self.assertNotIn('error', recovered['ai_processing_coverage']['chunks'][0])
        self.assertEqual(self.stream.call_count, 2)

    def test_connection_test_returns_specific_sanitized_authentication_error(self):
        self.create.side_effect = self.failure(401, 'AuthenticationError')
        response = project_ai.test_project_ai_connection(self.project)
        self.assertFalse(response['success'])
        self.assertIn('HTTP 401', response['error'])
        self.assertIn('active Anthropic key', response['error'])
        self.assertNotIn(self.key, json.dumps(response))

    def test_untrusted_saved_error_values_cannot_supply_guidance_text(self):
        details = {'provider': 'anthropic', 'code': 'invalid_api_key',
                   'http_status': self.key, 'message': self.key, 'next_action': self.key}
        guidance = project_ai.ai_failure_guidance(details)
        self.assertIsNone(guidance['http_status'])
        self.assertEqual(guidance['next_action'], 'ai_settings')
        self.assertNotIn(self.key, json.dumps(guidance))
        self.assertIsNone(project_ai.ai_failure_guidance({'provider': 'anthropic', 'code': self.key}))


class AnthropicStreamingTransportTests(SimpleTestCase):
    """Exercise the installed SDK through an in-memory transport, never the network."""

    def setUp(self):
        self.key = 'sk-ant-synthetic-private-credential'
        self.project = SimpleNamespace(pk=9, id=9, ai_settings={'provider': 'anthropic'})
        configuration = patch.object(claude_client, 'get_claude_config', return_value={
            'api_key': self.key, 'model': 'synthetic-model',
        })
        configuration.start()
        self.addCleanup(configuration.stop)
        self.requests, self.clients, self.bodies = [], [], []
        self.clock = 0
        self.handler = lambda request: self.sse_response()
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

    def sse_response(self, *, terminal=True, stop_reason='end_turn', fail_after_text=False, timed=False):
        owner = self

        class Body(httpx.SyncByteStream):
            closed = False

            def __iter__(self):
                def event(kind, **data):
                    return (f'event: {kind}\ndata: ' + json.dumps({'type': kind, **data}) + '\n\n').encode()

                yield event('message_start', message={
                    'id': 'synthetic-message', 'type': 'message', 'role': 'assistant',
                    'model': 'synthetic-model', 'content': [], 'stop_reason': None, 'stop_sequence': None,
                    'usage': {'input_tokens': 15, 'output_tokens': 0},
                })
                yield event('content_block_start', index=0, content_block={'type': 'text', 'text': ''})
                for index, fragment in enumerate(['{"facts"', ': ', '[]', '}']):
                    if timed:
                        owner.clock = [0, 2, 6, 7][index]
                    yield event('content_block_delta', index=0, delta={'type': 'text_delta', 'text': fragment})
                if fail_after_text:
                    raise httpx.ReadTimeout(owner.key)
                yield event('content_block_stop', index=0)
                yield event('message_delta', delta={'stop_reason': stop_reason, 'stop_sequence': None},
                            usage={'output_tokens': 5})
                if terminal:
                    yield event('message_stop')

            def close(self):
                self.closed = True

        body = Body()
        self.bodies.append(body)
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=body)

    def call(self, **options):
        return project_ai.call_project_ai(self.project, system_prompt='Extract quoted facts.',
            user_prompt='Synthetic source.', max_tokens=6000, feature='document_intelligence', **options)

    def test_completed_stream_preserves_result_usage_timeout_and_selected_configuration(self):
        with patch('apps.rbac.ai_telemetry.record_usage') as usage:
            result = self.call(user=SimpleNamespace(pk=1))
        self.assertEqual(result['text'], '{"facts": []}')
        self.assertEqual(result['stop_reason'], 'end_turn')
        self.assertEqual((result['tokens_input'], result['tokens_output']), (15, 5))
        self.assertEqual(len(self.requests), 1)
        payload = json.loads(self.requests[0].content)
        self.assertTrue(payload['stream'])
        self.assertEqual(payload['model'], 'synthetic-model')
        self.assertEqual(payload['max_tokens'], 6000)
        self.assertEqual(self.requests[0].extensions['timeout']['read'], claude_client.CLAUDE_REQUEST_TIMEOUT_SECONDS)
        self.assertEqual(self.clients[0].max_retries, 0)
        self.assertTrue(self.clients[0].is_closed())
        self.assertTrue(self.bodies[0].closed)
        usage.assert_called_once()
        self.assertTrue(usage.call_args.kwargs['success'])

    def test_valid_json_without_terminal_event_is_discarded(self):
        self.handler = lambda request: self.sse_response(terminal=False)
        errors = {}
        self.assertIsNone(self.call(error_details=errors))
        self.assertEqual(errors, {'provider': 'anthropic', 'code': 'incomplete_response', 'http_status': None})
        self.assertEqual(project_ai.ai_failure_guidance(errors)['next_action'], 'retry_analysis')
        self.assertEqual(len(self.requests), 1)
        self.assertTrue(self.bodies[0].closed)
        self.assertTrue(self.clients[0].is_closed())

    def test_initial_timeout_has_one_attempt_and_no_automatic_replay(self):
        def fail(request):
            raise httpx.ReadTimeout(self.key, request=request)

        self.handler = fail
        errors = {}
        with patch('anthropic._base_client.time.sleep') as sleep:
            self.assertIsNone(self.call(error_details=errors))
        self.assertEqual(len(self.requests), 1)
        sleep.assert_not_called()
        self.assertEqual(errors['code'], 'timeout')
        self.assertTrue(self.clients[0].is_closed())

    def test_midstream_timeout_discards_even_parseable_partial_output_without_secret(self):
        self.handler = lambda request: self.sse_response(fail_after_text=True)
        errors = {}
        with self.assertLogs(claude_client.logger, level='WARNING') as logs:
            self.assertIsNone(self.call(error_details=errors))
        self.assertEqual(errors['code'], 'timeout')
        self.assertEqual(len(self.requests), 1)
        self.assertTrue(self.bodies[0].closed)
        self.assertTrue(self.clients[0].is_closed())
        self.assertNotIn(self.key, json.dumps(errors) + str(logs.output))

    def test_http_failures_are_not_silently_retried_or_exposed(self):
        for status, code in [(401, 'invalid_api_key'), (429, 'quota_exceeded'), (503, 'provider_unavailable')]:
            with self.subTest(status=status):
                self.requests.clear()
                self.handler = lambda request: httpx.Response(status, json={
                    'type': 'error', 'error': {'type': 'api_error', 'message': self.key},
                })
                errors = {}
                with patch('anthropic._base_client.time.sleep') as sleep:
                    self.assertIsNone(self.call(error_details=errors))
                self.assertEqual(len(self.requests), 1)
                sleep.assert_not_called()
                self.assertEqual(errors['code'], code)
                self.assertNotIn(self.key, json.dumps(errors))

    def test_output_limit_is_retained_for_existing_partial_coverage_checks(self):
        self.handler = lambda request: self.sse_response(stop_reason='max_tokens')
        self.assertEqual(self.call()['stop_reason'], 'max_tokens')

    def test_safe_progress_reports_first_text_then_throttles_updates(self):
        self.handler = lambda request: self.sse_response(timed=True)
        progress = Mock()
        with patch.object(claude_client.time, 'monotonic', side_effect=lambda: self.clock):
            self.assertIsNotNone(self.call(progress_callback=progress))
        self.assertEqual([call.args[0] for call in progress.call_args_list], [
            {'response_characters_received': 8}, {'response_characters_received': 12},
        ])

    def test_progress_delivery_failure_does_not_discard_completed_result(self):
        with self.assertLogs(claude_client.logger, level='WARNING') as logs:
            self.assertIsNotNone(self.call(progress_callback=Mock(side_effect=RuntimeError(self.key))))
        self.assertNotIn(self.key, str(logs.output))

    def test_connection_test_remains_nonstreaming_and_closes_client(self):
        self.handler = lambda request: httpx.Response(200, json={
            'id': 'synthetic-message', 'type': 'message', 'role': 'assistant', 'model': 'synthetic-model',
            'content': [{'type': 'text', 'text': 'OK'}], 'stop_reason': 'end_turn', 'stop_sequence': None,
            'usage': {'input_tokens': 15, 'output_tokens': 1},
        })
        self.assertTrue(project_ai.test_project_ai_connection(self.project)['success'])
        self.assertFalse(json.loads(self.requests[0].content).get('stream', False))
        self.assertEqual(len(self.requests), 1)
        self.assertTrue(self.clients[0].is_closed())
