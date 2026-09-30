"""Central credentials power existing email/planning contracts without BYOK."""
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.core.ai_credential_models import AIProviderConfiguration, AIProviderCredential
from apps.core.ai_credentials import AICredentialUnavailable, encrypt_api_key
from apps.sales.email_ai_provider import _configuration, email_ai_cache_identity
from apps.planning_intelligence.services import project_ai, project_ai_settings
from apps.planning_intelligence.services.claude_client import get_claude_config
from apps.planning_intelligence.services.project_setup_ai import generation_credentials, SetupAIUnavailable


@override_settings(AI_CREDENTIAL_ENCRYPTION_KEY='synthetic-central-tests-encryption',
                   SALES_EMAIL_AI_PROVIDER='anthropic', SALES_EMAIL_AI_MODEL='claude-test',
                   SALES_EMAIL_AI_ENABLED=False, SALES_EMAIL_AI_API_KEY='legacy-email-secret')
class CentralAIConsumerTests(TestCase):
    def configure(self, provider='anthropic', key='synthetic-central-secret', enabled=True):
        credential = AIProviderCredential.objects.create(provider=provider, label='Synthetic test',
                                                        encrypted_key=encrypt_api_key(key))
        configuration = AIProviderConfiguration.objects.create(provider=provider, enabled=enabled,
                                                               selected_credential=credential)
        return configuration, credential

    def test_email_uses_selected_central_key_and_admin_activation(self):
        self.configure()
        result = _configuration()
        self.assertTrue(result.enabled)
        self.assertEqual(result.api_key, 'synthetic-central-secret')
        self.assertEqual(result.model, 'claude-test')
        self.assertNotIn(result.api_key, repr(result))

    def test_email_rotation_changes_cache_identity_without_restart(self):
        _, credential = self.configure()
        before = email_ai_cache_identity()
        credential.encrypted_key = encrypt_api_key('synthetic-replacement')
        credential.save(update_fields=['encrypted_key'])
        self.assertNotEqual(before, email_ai_cache_identity())
        self.assertEqual(_configuration().api_key, 'synthetic-replacement')

    def test_disabled_provider_does_not_revive_environment_key(self):
        self.configure(enabled=False)
        result = _configuration()
        self.assertFalse(result.enabled)
        self.assertEqual(result.api_key, '')

    def test_corrupt_central_key_blocks_environment_fallback(self):
        _, credential = self.configure()
        credential.encrypted_key = 'unreadable-ciphertext'
        credential.save(update_fields=['encrypted_key'])
        result = _configuration()
        self.assertEqual(result.api_key, '')
        self.assertEqual(result.error_code, 'configuration_invalid')

    def test_unconfigured_project_uses_central_anthropic_without_saving_key(self):
        self.configure()
        project = SimpleNamespace(ai_settings={})
        configuration = project_ai.get_project_ai_config(project)
        self.assertEqual(configuration['api_key'], 'synthetic-central-secret')
        self.assertEqual(get_claude_config(project)['api_key'], configuration['api_key'])
        self.assertEqual(project.ai_settings, {})
        public = project_ai_settings.settings_payload(project)
        self.assertTrue(public['enabled'])
        self.assertTrue(public['key_configured'])
        self.assertEqual(public['credential_source'], 'administrator')
        self.assertNotIn('synthetic-central-secret', str(public))

    def test_explicit_project_disable_is_preserved(self):
        self.configure()
        project = SimpleNamespace(ai_settings={'enabled': False})
        self.assertIsNone(project_ai.get_project_ai_config(project))
        self.assertIsNone(get_claude_config(project))

    def test_central_gemini_provider_switch_does_not_require_new_project_key(self):
        self.configure('gemini', 'synthetic-google-central')
        current = {'enabled': True, 'provider': 'anthropic', 'api_key_encrypted': 'legacy-private'}
        updated = project_ai_settings.updated_settings(current, {'provider': 'gemini', 'enabled': True})
        result = project_ai.get_project_ai_config(SimpleNamespace(ai_settings=updated))
        self.assertEqual(result['provider'], 'gemini')
        self.assertEqual(result['api_key'], 'synthetic-google-central')

    def test_central_disabled_does_not_use_project_or_personal_secret(self):
        self.configure(enabled=False)
        project = SimpleNamespace(ai_settings={'enabled': True, 'api_key_encrypted': 'legacy-private'})
        self.assertIsNone(project_ai.get_project_ai_config(project))
        self.configure('openai', enabled=False)
        with self.assertRaises(SetupAIUnavailable):
            generation_credentials(None)

    def test_project_setup_automatically_uses_central_openai(self):
        self.configure('openai', 'synthetic-openai-central')
        key, _, pin_official_api = generation_credentials(None)
        self.assertEqual(key, 'synthetic-openai-central')
        self.assertTrue(pin_official_api)

    def test_model_only_update_preserves_automatic_central_activation(self):
        self.configure()
        updated = project_ai_settings.updated_settings({}, {'model': project_ai.DEFAULT_MODEL_BY_PROVIDER['anthropic']})
        self.assertTrue(updated['enabled'])
        self.assertNotIn('api_key_encrypted', updated)

    def test_central_claude_is_independent_of_legacy_byok_flag(self):
        self.configure()
        with patch('apps.planning_intelligence.services.claude_client.CLAUDE_BYOK_ENABLED', False):
            self.assertEqual(get_claude_config(SimpleNamespace(ai_settings={}))['api_key'], 'synthetic-central-secret')

    def test_central_setup_does_not_claim_personal_connection_was_tested(self):
        from apps.planning_intelligence.services.project_setup_ai import ai_settings_payload
        from django.utils import timezone
        self.configure('openai', 'synthetic-openai-central')
        with patch('apps.planning_intelligence.services.project_setup_ai._personal_settings',
                   return_value=SimpleNamespace(last_tested_at=timezone.now())):
            result = ai_settings_payload(None)
        self.assertEqual(result['ai_settings']['credential_source'], 'administrator')
        self.assertIsNone(result['ai_settings']['last_tested_at'])
        self.assertTrue(result['ai_settings']['storage_available'])

    def test_registry_failure_never_uses_fallback_or_returns_secret(self):
        with patch('apps.core.ai_credentials._provider_record', side_effect=AICredentialUnavailable()):
            self.assertEqual(_configuration().error_code, 'configuration_invalid')
            self.assertIsNone(project_ai.get_project_ai_config(SimpleNamespace(ai_settings={})))

    def test_sequence_disabled_central_provider_never_switches_to_openai(self):
        from apps.planning_intelligence.services.intelligent_sequence import _provider
        from apps.planning_intelligence.services.schedule_approval import ScheduleApprovalError
        self.configure(enabled=False)
        self.configure('openai', 'synthetic-openai-central')
        with patch('apps.planning_intelligence.services.project_setup_ai._personal_settings') as personal, \
                patch('apps.planning_intelligence.services.project_setup_ai.generation_credentials') as fallback:
            with self.assertRaises(ScheduleApprovalError):
                _provider(SimpleNamespace(ai_settings={}), None, {})
        personal.assert_not_called()
        fallback.assert_not_called()

    def test_sequence_central_provider_precedes_legacy_personal_connection(self):
        from apps.planning_intelligence.services.intelligent_sequence import _provider
        self.configure()
        with patch('apps.planning_intelligence.services.project_setup_ai._personal_settings') as personal, \
                patch('apps.planning_intelligence.services.project_ai.call_project_ai',
                      return_value={'text': '{"activities": []}', 'stop_reason': 'end_turn'}) as provider:
            self.assertEqual(_provider(SimpleNamespace(ai_settings={}), None, {}), {'activities': []})
        provider.assert_called_once()
        personal.assert_not_called()

    def test_sequence_registry_failure_blocks_personal_fallback(self):
        from apps.planning_intelligence.services.intelligent_sequence import _provider
        from apps.planning_intelligence.services.schedule_approval import ScheduleApprovalError
        with patch('apps.core.ai_credentials._provider_record', side_effect=AICredentialUnavailable()), \
                patch('apps.planning_intelligence.services.project_setup_ai._personal_settings') as personal, \
                patch('apps.planning_intelligence.services.project_setup_ai.generation_credentials') as fallback:
            with self.assertRaises(ScheduleApprovalError):
                _provider(SimpleNamespace(ai_settings={}), None, {})
        personal.assert_not_called()
        fallback.assert_not_called()
