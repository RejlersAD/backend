"""Provider routing and Gemini failures use synthetic keys and mocked HTTP only."""
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase

from ..services import byok_crypto, claude_client, project_ai
from ..services.evidence_bulk_ai import ai_availability
from ..services.intelligence import analyze_project


class ProjectAIProviderTests(SimpleTestCase):
    def setUp(self):
        self.key = 'gemini-test-credential-never-real'
        self.project = SimpleNamespace(pk=7, ai_settings={
            'enabled': True, 'provider': 'gemini', 'api_key_provider': 'gemini',
            'api_key_encrypted': byok_crypto.encrypt_api_key(self.key),
            'model': project_ai.DEFAULT_GEMINI_MODEL,
        })
        enabled = patch.object(project_ai, 'GEMINI_BYOK_ENABLED', True)
        enabled.start()
        self.addCleanup(enabled.stop)
        posting = patch.object(project_ai.requests, 'post')
        self.post = posting.start()
        self.addCleanup(posting.stop)
        self.respond()

    def respond(self, text='{"facts": []}', finish='STOP', status=200, payload=None):
        self.post.return_value = Mock(status_code=status)
        self.post.return_value.json.return_value = payload if payload is not None else {
            'candidates': [{'content': {'parts': [{'text': text}]}, 'finishReason': finish}],
            'usageMetadata': {'promptTokenCount': 123, 'candidatesTokenCount': 29, 'thoughtsTokenCount': 8},
        }

    def call(self, **kwargs):
        return project_ai.call_project_ai(self.project, system_prompt='Extract quoted facts.',
            user_prompt='Contractor shall prepare the report.', max_tokens=6000,
            feature='document_intelligence', **kwargs)

    def source(self):
        return SimpleNamespace(pk=1, extracted_text='Contractor shall prepare Permit Matrix.',
                               category='sow', original_filename='scope.txt')

    def test_gemini_auth_header_fixed_url_json_request_and_usage(self):
        with patch('apps.rbac.ai_telemetry.record_usage') as usage:
            result = self.call(json_output=True, user=SimpleNamespace(pk=1))
        args, options = self.post.call_args
        self.assertEqual(args, (f'https://generativelanguage.googleapis.com/v1beta/models/{project_ai.DEFAULT_GEMINI_MODEL}:generateContent',))
        self.assertNotIn(self.key, args[0])
        self.assertEqual(options['headers']['x-goog-api-key'], self.key)
        self.assertFalse(options['allow_redirects'])
        self.assertEqual(options['json']['generationConfig']['responseMimeType'], 'application/json')
        self.assertEqual(options['json']['generationConfig']['maxOutputTokens'], 6000)
        self.assertEqual(options['json']['systemInstruction']['parts'][0]['text'], 'Extract quoted facts.')
        self.assertEqual(json.loads(result['text']), {'facts': []})
        self.assertEqual(result['stop_reason'], 'end_turn')
        self.assertEqual(result['tokens_input'], 123)
        self.assertEqual(result['tokens_output'], 37)
        self.assertEqual(usage.call_args.kwargs['provider'], 'gemini')
        self.assertEqual(usage.call_args.kwargs['tokens_output'], 37)
        self.assertNotIn(self.key, str(usage.call_args))

    def test_narrative_uses_plain_text_and_omits_thought_parts(self):
        self.respond(payload={'candidates': [{'finishReason': 'STOP', 'content': {'parts': [
            {'text': 'private reasoning', 'thought': True}, {'text': 'Executive summary.'},
        ]}}]})
        result = self.call()
        self.assertEqual(result['text'], 'Executive summary.')
        self.assertNotIn('responseMimeType', self.post.call_args.kwargs['json']['generationConfig'])

    def test_legacy_settings_use_anthropic_only(self):
        self.project.ai_settings.pop('provider')
        self.project.ai_settings.pop('api_key_provider')
        with patch.object(claude_client, 'get_claude_config', return_value={'api_key': 'legacy', 'model': 'legacy-model'}), \
                patch.object(claude_client, 'call_claude', return_value={'text': 'legacy result'}) as call:
            self.assertEqual(self.call(), {'text': 'legacy result'})
        call.assert_called_once()
        self.post.assert_not_called()

    def test_provider_switch_cannot_reuse_key_bound_to_another_provider(self):
        for provider, binding in [('gemini', 'anthropic'), ('anthropic', 'gemini')]:
            with self.subTest(provider=provider):
                self.project.ai_settings.update(provider=provider, api_key_provider=binding)
                self.assertIsNone(project_ai.get_project_ai_config(self.project))
                self.assertIsNone(self.call())
        self.post.assert_not_called()

    def test_direct_claude_cannot_decrypt_or_send_gemini_key(self):
        with patch.object(byok_crypto, 'decrypt_api_key') as decrypt:
            self.assertIsNone(claude_client.get_claude_config(self.project))
            self.assertIsNone(claude_client.call_claude(self.project, system_prompt='test',
                user_prompt='test', max_tokens=10, feature='test'))
        decrypt.assert_not_called()

    def test_recognizable_anthropic_key_cannot_be_sent_to_gemini(self):
        self.project.ai_settings['api_key_encrypted'] = byok_crypto.encrypt_api_key('sk-ant-test-private-key')
        self.assertIsNone(self.call())
        self.post.assert_not_called()

    def test_disabled_unknown_provider_and_corrupt_key_never_call_remote(self):
        for changes in ({'enabled': False}, {'provider': 'unknown'}, {'api_key_encrypted': 'corrupt'}):
            with self.subTest(changes=changes), patch.object(self.project, 'ai_settings', {**self.project.ai_settings, **changes}):
                self.assertIsNone(self.call())
        with patch.object(project_ai, 'GEMINI_BYOK_ENABLED', False):
            self.assertIsNone(self.call())
        self.post.assert_not_called()

    def test_invalid_model_is_never_interpolated_into_endpoint(self):
        self.project.ai_settings['model'] = '../../different-host?key=credential'
        self.call()
        self.assertIn(f'/{project_ai.DEFAULT_GEMINI_MODEL}:generateContent', self.post.call_args.args[0])

    def test_auth_errors_are_actionable_and_do_not_expose_raw_provider_text(self):
        self.respond(status=400, payload={'error': {'message': self.key, 'status': 'INVALID_ARGUMENT',
            'details': [{'reason': 'API_KEY_INVALID'}]}})
        with self.assertLogs(project_ai.logger, level='WARNING') as logs:
            result = project_ai.test_project_ai_connection(self.project)
        self.assertFalse(result['success'])
        self.assertIn('rejected the API key', result['error'])
        self.assertNotIn(self.key, json.dumps(result) + str(logs.output))

    def test_connection_failure_is_sanitized_and_logged_as_failure(self):
        self.post.side_effect = requests.ConnectionError(f'connection failed: {self.key}')
        with patch('apps.rbac.ai_telemetry.record_usage') as usage:
            result = project_ai.test_project_ai_connection(self.project, user=SimpleNamespace(pk=1))
        self.assertFalse(result['success'])
        self.assertNotIn(self.key, json.dumps(result) + str(usage.call_args))
        self.assertEqual(usage.call_args.kwargs['error_code'], 'connection_error')
        self.assertFalse(usage.call_args.kwargs['success'])

    def test_rate_limit_blocked_empty_and_malformed_outputs_do_not_fallback(self):
        scenarios = [(429, {'error': {'message': self.key}}), (302, {}),
            (200, {'promptFeedback': {'blockReason': 'SAFETY'}}),
            (200, {'candidates': [{'finishReason': 'SAFETY', 'content': {'parts': [{'text': 'unsafe'}]}}]}),
            (200, {'candidates': [{'finishReason': 'STOP', 'content': {'parts': []}}]}),
            (200, [])]
        with patch.object(claude_client, 'call_claude') as claude:
            for status, payload in scenarios:
                with self.subTest(status=status, payload=payload):
                    self.respond(status=status, payload=payload)
                    self.assertIsNone(self.call())
        claude.assert_not_called()

    def test_gemini_extraction_keeps_validated_facts_and_provider_identity(self):
        source = self.source()
        self.respond(text=json.dumps({'facts': [{'type': 'deliverable', 'value': 'Permit Matrix',
            'source_file_id': 1, 'quote': source.extracted_text}], 'review_summary': 'Quoted source reviewed.'}))
        result = analyze_project([source], project=self.project)
        self.assertEqual(result['ai_provider_used'], 'gemini')
        self.assertEqual(result['ai_processing_coverage']['status'], 'complete')
        self.assertEqual(result['ai_evidence_facts'][0]['value'], 'Permit Matrix')
        self.assertEqual(result['ai_evidence_facts'][0]['quote'], source.extracted_text)
        self.assertFalse(result['ai_evidence_facts'][0]['executable'])

    def test_truncated_json_is_never_treated_as_complete_coverage(self):
        for text in ('{"facts": []}', '{"facts": ['):
            with self.subTest(text=text):
                self.respond(text=text, finish='MAX_TOKENS')
                result = analyze_project([self.source()], project=self.project)
                self.assertEqual(result['ai_processing_coverage']['chunks_processed'], 0)
                self.assertEqual(result['ai_processing_coverage']['status'], 'partial')
                self.assertTrue(result['ai_processing_coverage']['resume_available'])

    def test_changed_provider_or_model_does_not_reuse_extraction_checkpoint(self):
        first = analyze_project([self.source()], project=self.project)
        self.project.ai_settings['model'] = 'gemini-3.5-flash-lite'
        second = analyze_project([self.source()], project=self.project, resume_state=first['ai_checkpoint'])
        self.assertEqual(self.post.call_count, 2)
        self.assertNotEqual(first['ai_checkpoint']['source_fingerprint'], second['ai_checkpoint']['source_fingerprint'])
        self.project.ai_settings.update(provider='anthropic', api_key_provider='anthropic')
        with patch.object(claude_client, 'get_claude_config', return_value={'api_key': 'legacy', 'model': 'legacy-model'}), \
                patch.object(claude_client, 'call_claude', return_value={'text': '{"facts": []}'}) as claude:
            third = analyze_project([self.source()], project=self.project, resume_state=second['ai_checkpoint'])
        claude.assert_called_once()
        self.assertEqual(third['ai_provider_used'], 'anthropic')
        self.assertNotEqual(second['ai_checkpoint']['source_fingerprint'], third['ai_checkpoint']['source_fingerprint'])

    def test_evidence_review_availability_uses_gemini_without_exposing_keys(self):
        availability = ai_availability(self.project)
        self.assertTrue(availability['available'])
        self.assertEqual(availability['provider'], 'gemini')
        self.assertNotIn(self.key, json.dumps(availability))
        self.post.assert_not_called()
