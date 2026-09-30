"""Central Planning credentials stay on their provider and release worker DBs."""
import logging
import os
import threading
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from apps.core.ai_credential_models import AIProviderConfiguration, AIProviderCredential
from apps.core.ai_credentials import encrypt_api_key
from apps.planning_intelligence.services import claude_client, project_ai


@override_settings(AI_CREDENTIAL_ENCRYPTION_KEY='synthetic-transport-test-encryption')
class CentralPlanningTransportTests(TestCase):
    def test_managed_claude_direct_and_project_calls_pin_host_auth_and_hide_sdk_logs(self):
        secret = 'synthetic-managed-anthropic-test-key'
        credential = AIProviderCredential.objects.create(provider='anthropic', label='Synthetic',
                                                         encrypted_key=encrypt_api_key(secret))
        AIProviderConfiguration.objects.create(provider='anthropic', selected_credential=credential,
                                               model='synthetic-model')
        project = SimpleNamespace(ai_settings={}, pk='synthetic-project', id='synthetic-project')
        response = SimpleNamespace(content=[SimpleNamespace(type='text', text='Synthetic response')],
                                   usage=SimpleNamespace(input_tokens=1, output_tokens=2), stop_reason='end_turn')
        kwargs = {'system_prompt': 'Synthetic test', 'user_prompt': 'Synthetic test',
                  'max_tokens': 32, 'feature': 'synthetic_transport_test'}
        logs = []

        class Capture(logging.Handler):
            def emit(self, record):
                logs.append(record.getMessage())

        logger = logging.getLogger('anthropic._base_client')
        handler, previous_level = Capture(), logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        self.addCleanup(logger.removeHandler, handler)
        self.addCleanup(logger.setLevel, previous_level)

        def provider_response(**_):
            logger.debug('Synthetic private SDK payload %s', secret)
            return response

        with patch.dict(os.environ, {'ANTHROPIC_BASE_URL': 'https://unrelated.invalid',
                                     'ANTHROPIC_AUTH_TOKEN': 'unrelated-auth-token'}), \
                patch('anthropic.Anthropic') as constructor:
            client = constructor.return_value.__enter__.return_value
            client.messages.create.side_effect = provider_response
            for call in (claude_client.call_claude, project_ai.call_project_ai):
                with self.subTest(entry_point=call.__name__):
                    self.assertEqual(call(project, **kwargs)['text'], 'Synthetic response')
                    options = constructor.call_args.kwargs
                    self.assertEqual(options['base_url'], 'https://api.anthropic.com')
                    self.assertEqual(options['api_key'], secret)
                    self.assertEqual(options['auth_token'], '')
                    self.assertEqual(options['default_headers']['X-Api-Key'], secret)
                    self.assertEqual(type(options['default_headers']['Authorization']).__name__, 'Omit')
                    self.assertEqual(options['max_retries'], 0)
        self.assertEqual(logs, [])


class CentralPlanningWorkerCleanupTests(SimpleTestCase):
    def test_executor_releases_its_database_connections_on_success_and_failure(self):
        parent_thread = threading.get_ident()
        project = SimpleNamespace(ai_settings={}, pk='synthetic-project')
        for outcome in ({'text': 'Synthetic result'}, RuntimeError('synthetic provider failure')):
            events = []

            def record(name):
                events.append((name, threading.get_ident()))

            def provider(*_, **__):
                record('provider')
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

            with self.subTest(failure=isinstance(outcome, Exception)), \
                    patch.object(project_ai, 'close_old_connections', side_effect=lambda: record('start')), \
                    patch.object(project_ai.connections, 'close_all', side_effect=lambda: record('close')), \
                    patch.object(project_ai, 'call_project_ai', side_effect=provider):
                with project_ai.AnalysisRequests(project, None, 1) as requests:
                    requests.submit('synthetic-section')
                    future = next(iter(requests.pending))
                    result, errors, usage = future.result(timeout=5)
                self.assertEqual([name for name, _ in events], ['start', 'provider', 'close'])
                self.assertEqual(len({thread for _, thread in events}), 1)
                self.assertNotEqual(events[0][1], parent_thread)
                self.assertEqual(result, None if isinstance(outcome, Exception) else outcome)
                self.assertEqual(usage, [])
                self.assertEqual(bool(errors), isinstance(outcome, Exception))
