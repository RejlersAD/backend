"""Transient provider failures retain actionable diagnostics without secrets."""
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase

from ..services import byok_crypto, project_ai
from ..services.analysis_result import analysis_result
from ..services.intelligence import analyze_project


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
        self.assertEqual(coverage['chunks'][0]['error'], {
            'provider': 'gemini', 'code': 'provider_unavailable', 'http_status': 503})
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

