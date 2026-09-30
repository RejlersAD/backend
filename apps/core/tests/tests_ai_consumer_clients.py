"""Credential rotation and transport lifetime tests; no provider/network calls."""
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch
import io
import logging

from django.test import SimpleTestCase, override_settings

from apps.core import ai_consumer_clients as clients
from apps.core.ai_credentials import AICredentialUnavailable


@override_settings(OPENAI_API_KEY='', ANTHROPIC_API_KEY='', GEMINI_API_KEY='')
class AIConsumerClientTests(SimpleTestCase):
    def setUp(self):
        self.resolve = patch.object(clients.ai_credentials, 'resolve_provider_credential').start()
        self.get_key = patch.object(clients.ai_credentials, 'get_provider_api_key').start()
        self.addCleanup(patch.stopall)
        self.resolve.return_value = ('central-test-key', {'managed': True})
        self.get_key.return_value = 'central-test-key'
        self.instances = []
        self.received = []

    def factory(self, **options):
        self.received.append(options)
        client = SimpleNamespace(close=Mock())
        client.chat = SimpleNamespace(completions=SimpleNamespace(create=Mock(return_value={'ok': True})))
        client.models = SimpleNamespace(generate_content=Mock(return_value={'ok': True}))
        self.instances.append(client)
        return client

    def test_construction_does_not_resolve_or_connect(self):
        factory = Mock()
        proxy = clients.lazy_provider_client('openai', factory, api_key='legacy-test-key')
        endpoint = proxy.chat.completions.create
        self.resolve.assert_not_called()
        self.get_key.assert_not_called()
        factory.assert_not_called()
        self.assertTrue(callable(endpoint))

    @override_settings(BASE_DIR=Path(__file__).resolve().parents[3])
    def test_comment_cleaner_singleton_observes_later_configuration_and_disable(self):
        from apps.crs_documents.helpers import comment_cleaner
        config = SimpleNamespace(config={'openai': {'enabled': True}})
        self.get_key.return_value = ''
        with patch.object(comment_cleaner, 'CommentCleanerConfig', return_value=config), \
                patch.object(comment_cleaner, 'OPENAI_AVAILABLE', True):
            cleaner = comment_cleaner.CommentCleaner()
        self.get_key.assert_not_called()
        self.assertFalse(cleaner.openai_client)
        self.get_key.return_value = 'newly-configured-key'
        self.assertTrue(cleaner.openai_client)
        self.get_key.return_value = ''
        self.assertFalse(cleaner.openai_client)

    def test_observed_factory_uses_registry_and_preserves_endpoint_result(self):
        from apps.rbac.ai_telemetry import observed_openai
        with patch('openai.OpenAI', self.factory):
            client = observed_openai(api_key='legacy-key')
            self.resolve.assert_not_called()
            self.assertEqual(client.chat.completions.create(model='kept-model'), {'ok': True})
        self.assertEqual(self.received[0]['api_key'], 'central-test-key')
        self.instances[0].close.assert_called_once()

    def test_saved_nested_endpoint_uses_current_key_on_each_operation(self):
        endpoint = clients.lazy_provider_client('openai', self.factory).chat.completions.create
        self.resolve.side_effect = [('first-key', {'managed': True}), ('rotated-key', {'managed': True})]
        endpoint(model='kept-model', messages=[])
        endpoint(model='kept-model', messages=[])
        self.assertEqual([item['api_key'] for item in self.received], ['first-key', 'rotated-key'])
        for instance in self.instances:
            instance.chat.completions.create.assert_called_once_with(model='kept-model', messages=[])
            instance.close.assert_called_once()

    def test_disabled_provider_cannot_revive_legacy_or_cached_key(self):
        endpoint = clients.lazy_provider_client('openai', self.factory, api_key='legacy-key').chat.completions.create
        endpoint()
        self.resolve.return_value = ('', {'managed': True})
        with self.assertRaises(AICredentialUnavailable):
            endpoint()
        self.assertEqual(len(self.instances), 1)

    def test_registry_failure_does_not_invoke_legacy_client(self):
        self.resolve.side_effect = AICredentialUnavailable('registry_unavailable')
        with self.assertRaises(AICredentialUnavailable):
            clients.lazy_provider_client('openai', self.factory, api_key='legacy-key').chat.completions.create()
        self.assertEqual(self.instances, [])

    def test_managed_openai_uses_official_host_and_no_ambient_identity(self):
        proxy = clients.lazy_provider_client('openai', self.factory, api_key='legacy-key',
                                             base_url='https://untrusted.example.test',
                                             organization='legacy-org', project='legacy-project',
                                             default_headers={'Authorization': 'legacy-token'},
                                             http_client=object(), timeout=27)
        proxy.chat.completions.create()
        options = self.received[0]
        self.assertEqual(options['base_url'], 'https://api.openai.com/v1')
        self.assertEqual(options['organization'], '')
        self.assertEqual(options['project'], '')
        self.assertEqual(options['timeout'], 27)
        self.assertNotIn('default_headers', options)
        self.assertNotIn('http_client', options)

    def test_managed_gemini_disables_vertex_and_custom_host(self):
        proxy = clients.lazy_provider_client('gemini', self.factory, vertexai=True,
                                             http_options={'base_url': 'https://untrusted.example.test'})
        proxy.models.generate_content(model='kept-model', contents=['synthetic'])
        self.assertIs(self.received[0]['vertexai'], False)
        self.assertEqual(self.received[0]['http_options']['base_url'], 'https://generativelanguage.googleapis.com')

    def test_unmanaged_provider_preserves_legacy_options(self):
        self.resolve.return_value = ('legacy-key', {'managed': False})
        clients.lazy_provider_client('openai', self.factory, base_url='https://legacy.example.test').chat.completions.create()
        self.assertEqual(self.received[0]['base_url'], 'https://legacy.example.test')

    def test_with_options_still_rechecks_registry_and_constrains_host(self):
        proxy = clients.lazy_provider_client('openai', self.factory).with_options(
            timeout=12, base_url='https://untrusted.example.test')
        proxy.chat.completions.create()
        self.assertEqual(self.received[0]['timeout'], 12)
        self.assertEqual(self.received[0]['base_url'], 'https://api.openai.com/v1')

    def test_provider_alias_resolves_anthropic_without_another_provider_key(self):
        self.assertEqual(clients.provider_api_key('claude', 'legacy-claude-key'), 'central-test-key')
        self.assertEqual(self.get_key.call_args.args, ('anthropic',))
        self.assertEqual(self.get_key.call_args.kwargs['fallback'](), 'legacy-claude-key')

    def test_managed_key_does_not_evaluate_broken_legacy_fallback(self):
        fallback = Mock(side_effect=RuntimeError('legacy decryption failed'))
        proxy = clients.lazy_provider_client('openai', self.factory, api_key=fallback)
        proxy.chat.completions.create()
        self.assertEqual(self.received[0]['api_key'], 'central-test-key')
        fallback.assert_not_called()

    def test_real_openai_mock_transport_debug_output_never_contains_key_or_source(self):
        import httpx
        import openai

        output = io.StringIO()
        handler = logging.StreamHandler(output)
        names = ['openai._base_client', 'openai._response', 'httpx', 'httpcore.http11',
                 'anthropic.lib.credentials._providers', 'google.genai._api_client']
        previous = []
        for name in names:
            logger = logging.getLogger(name)
            previous.append((logger, logger.level))
            logger.addHandler(handler)
            logger.setLevel(logging.DEBUG)
        def restore_logs():
            for logger, level in previous:
                logger.removeHandler(handler)
                logger.setLevel(level)
        self.addCleanup(restore_logs)

        def transport(request):
            self.assertEqual(request.url.host, 'api.openai.com')
            self.assertEqual(request.headers['authorization'], 'Bearer central-test-key')
            self.assertEqual(request.headers.get('OpenAI-Organization', ''), '')
            self.assertEqual(request.headers.get('OpenAI-Project', ''), '')
            for name in names:
                logging.getLogger(name).debug('central-test-key private-source-fixture')
            return httpx.Response(401, json={'error': {'message': 'central-test-key private-source-fixture', 'type': 'invalid_api_key'}})
        def factory(**options):
            return openai.OpenAI(http_client=httpx.Client(transport=httpx.MockTransport(transport)),
                                 max_retries=0, **options)
        proxy = clients.lazy_provider_client('openai', factory)
        with self.assertRaises(clients.AIProviderOperationError) as caught:
            proxy.chat.completions.create(model='synthetic-model', messages=[{'role': 'user', 'content': 'private-source-fixture'}])
        self.assertEqual(caught.exception.status_code, 401)
        self.assertNotIn('central-test-key', output.getvalue())
        self.assertNotIn('private-source-fixture', output.getvalue())
        logging.getLogger('openai._response').warning('unrelated-request-visible')
        self.assertIn('unrelated-request-visible', output.getvalue())

    def test_missing_key_is_unavailable_without_constructing_sdk(self):
        self.resolve.return_value = ('', {'managed': False})
        self.get_key.return_value = ''
        proxy = clients.lazy_provider_client('openai', self.factory)
        self.assertFalse(proxy)
        with self.assertRaises(AICredentialUnavailable):
            proxy.chat.completions.create()
        self.assertEqual(self.instances, [])

    def test_stream_iterator_keeps_client_open_until_consumed(self):
        client = SimpleNamespace(close=Mock())
        observed = []
        def events():
            observed.append(client.close.call_count)
            yield 'event-one'
            observed.append(client.close.call_count)
            yield 'event-two'
        client.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: events()))
        stream = clients.lazy_provider_client('openai', lambda **kwargs: client).chat.completions.create(stream=True)
        self.assertEqual(client.close.call_count, 0)
        self.assertEqual(list(stream), ['event-one', 'event-two'])
        self.assertEqual(observed, [0, 0])
        client.close.assert_called_once()

    def test_stream_context_keeps_client_alive_for_final_message(self):
        client = SimpleNamespace(close=Mock())
        entered = SimpleNamespace(text_stream=iter(['text']), get_final_message=lambda: {'done': True})
        manager = Mock()
        manager.__enter__ = Mock(return_value=entered)
        manager.__exit__ = Mock(return_value=False)
        client.messages = SimpleNamespace(stream=lambda **kwargs: manager)
        with clients.lazy_provider_client('openai', lambda **kwargs: client).messages.stream() as stream:
            self.assertEqual(list(stream.text_stream), ['text'])
            self.assertEqual(stream.get_final_message(), {'done': True})
            client.close.assert_not_called()
        client.close.assert_called_once()
        manager.__exit__.assert_called_once()

    def test_provider_error_is_sanitized_and_client_closed(self):
        sensitive_error = RuntimeError('private provider response with test-key and source text')
        sensitive_error.status_code = 401
        client = SimpleNamespace(close=Mock(), chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(side_effect=sensitive_error))))
        with self.assertRaises(clients.AIProviderOperationError) as caught:
            clients.lazy_provider_client('openai', lambda **kwargs: client).chat.completions.create()
        self.assertEqual(caught.exception.code, 'authentication_failed')
        self.assertEqual(caught.exception.status_code, 401)
        self.assertNotIn('test-key', str(caught.exception))
        self.assertNotIn('source text', str(caught.exception))
        client.close.assert_called_once()

    def test_stream_failure_is_sanitized_and_closes_client(self):
        def events():
            yield 'one'
            raise RuntimeError('private response and secret-key')
        client = SimpleNamespace(close=Mock(), chat=SimpleNamespace(completions=SimpleNamespace(create=lambda: events())))
        stream = clients.lazy_provider_client('openai', lambda **kwargs: client).chat.completions.create()
        with self.assertRaises(clients.AIProviderOperationError) as caught:
            list(stream)
        self.assertNotIn('secret-key', str(caught.exception))
        client.close.assert_called_once()

    def test_close_prevents_another_operation(self):
        proxy = clients.lazy_provider_client('openai', self.factory)
        proxy.close()
        self.assertFalse(proxy)
        with self.assertRaisesRegex(RuntimeError, 'closed'):
            proxy.chat.completions.create()
        self.assertEqual(self.instances, [])

    def test_process_datasheet_factory_works_without_environment_credentials(self):
        from apps.process_datasheet import ai_provider
        with patch('openai.OpenAI', side_effect=self.factory):
            proxy = ai_provider.build_openai_client()
            self.assertIsNotNone(proxy)
            proxy.chat.completions.create(model='retained-extraction-model', messages=[])
        self.assertEqual(self.received[0]['api_key'], 'central-test-key')

    def test_raw_pid_engine_uses_central_key_without_user_input(self):
        from apps.pid_verification_v2.ai_extraction import PIDExtractionEngine
        response = Mock()
        response.json.return_value = {'choices': []}
        engine = PIDExtractionEngine(mode='enhanced_openai')
        with patch('apps.pid_verification_v2.ai_extraction.requests.post', return_value=response) as post:
            self.assertEqual(engine._call_openai_vision('eA==', 'synthetic prompt'), {'choices': []})
        self.assertEqual(post.call_args.args[0], 'https://api.openai.com/v1/chat/completions')
        self.assertEqual(post.call_args.kwargs['headers']['Authorization'], 'Bearer central-test-key')
        self.assertIs(post.call_args.kwargs['allow_redirects'], False)

    def test_disabled_raw_pid_engine_does_not_use_legacy_user_key(self):
        from apps.pid_verification_v2.ai_extraction import PIDExtractionEngine
        engine = PIDExtractionEngine(openai_key='legacy-user-key', mode='enhanced_openai')
        self.get_key.return_value = ''
        with patch('apps.pid_verification_v2.ai_extraction.requests.post') as post:
            with self.assertRaises(ValueError):
                engine._call_openai_vision('eA==', 'synthetic prompt')
        post.assert_not_called()

    def test_electrical_handwriting_central_key_overrides_legacy_user_key_and_disable(self):
        from apps.electrical_checklist.handwriting_extractor import HandwritingExtractor
        extractor = HandwritingExtractor(user_openai_api_key='sk-' + 'legacy-fixture' * 4)
        self.assertTrue(extractor._vision_available())
        self.assertEqual(extractor._resolve_api_key(), 'central-test-key')
        with patch('apps.electrical_checklist.handwriting_extractor.provider_managed', return_value=True):
            self.assertEqual(extractor.key_source, 'platform')
        self.get_key.return_value = ''
        self.assertFalse(extractor._vision_available())
        self.assertEqual(extractor._resolve_api_key(), '')
        with patch('apps.electrical_checklist.handwriting_extractor.provider_managed', return_value=True):
            self.assertEqual(extractor.key_source, 'none')

    def test_pid_checker_extraction_accepts_missing_request_key(self):
        from apps.pid_checker_v2.services import vision_extractor
        with patch.object(vision_extractor, '_render_pages', return_value=[object()]), \
                patch.object(vision_extractor, '_prepare_image_b64', return_value='synthetic-image'), \
                patch.object(vision_extractor, '_tile_image', return_value=[]), \
                patch.object(vision_extractor, 'VISION_INCLUDE_OVERVIEW', True), \
                patch.object(vision_extractor, '_call_vision', return_value=('[]', 3, 2)) as call:
            vision_extractor.extract_line_tags_via_vision(b'synthetic-pdf', 'openai', None)
        self.assertEqual(call.call_args.args[:2], ('openai', 'central-test-key'))

    def test_pid_checker_disabled_provider_rejects_even_with_legacy_key(self):
        from apps.pid_checker_v2.services import vision_extractor
        self.get_key.return_value = ''
        with patch.object(vision_extractor, '_render_pages') as render:
            with self.assertRaises(ValueError):
                vision_extractor.extract_line_tags_via_vision(b'synthetic-pdf', 'openai', 'legacy-user-key')
        render.assert_not_called()

    def test_pid_verification_vision_runs_without_user_key(self):
        from apps.pid_verification_v2.services import extraction
        with patch.object(extraction, '_tesseract_available', return_value=False), \
                patch.object(extraction, '_run_vision_ocr', return_value=('FT-201', {})) as vision, \
                patch.object(extraction, '_extract_tag_positions', return_value={}), \
                patch.object(extraction, '_extract_pipeline_tags_multi_angle', return_value=[]), \
                patch.object(extraction, '_extract_red_annotations', return_value=[]):
            result = extraction.extract_drawing('synthetic.pdf', provider='claude')
        self.assertEqual(vision.call_args.args[2:4], ('central-test-key', 'claude'))
        self.assertIs(result['extraction_info']['vision_used'], True)

    def test_real_anthropic_mock_transport_uses_official_host_without_bearer_auth(self):
        import httpx
        import anthropic
        from anthropic import _base_client
        # Anthropic 1.x uses httpx2; older supported releases use httpx.
        # Exercise the installed SDK's actual transport, without opening a socket.
        transport_api = getattr(_base_client, 'httpx2', httpx)
        def transport(request):
            self.assertEqual(request.url.host, 'api.anthropic.com')
            self.assertEqual(request.headers['x-api-key'], 'central-test-key')
            self.assertNotIn('authorization', request.headers)
            return transport_api.Response(200, json={
                'id': 'msg_fixture', 'type': 'message', 'role': 'assistant', 'model': 'retained-model',
                'content': [{'type': 'text', 'text': 'synthetic answer'}], 'stop_reason': 'end_turn',
                'stop_sequence': None, 'usage': {'input_tokens': 3, 'output_tokens': 2},
            })
        def factory(**options):
            return anthropic.Anthropic(http_client=transport_api.Client(transport=transport_api.MockTransport(transport)),
                                       max_retries=0, **options)
        response = clients.lazy_provider_client('anthropic', factory).messages.create(
            model='retained-model', max_tokens=10, messages=[{'role': 'user', 'content': 'synthetic prompt'}])
        self.assertEqual(response.content[0].text, 'synthetic answer')
