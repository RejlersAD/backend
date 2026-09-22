"""Personal setup credentials stay private and only save after a model test."""
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

from django.core import signing
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.project_models import Project, ProjectTask
from apps.rbac.models import RoleModule, RolePermission
from ..models import PlanningProject, ProjectSetupAISettings
from ..services.byok_crypto import decrypt_api_key, encrypt_api_key
from ..services.project_setup import PREVIEW_SALT, SetupAIUnavailable, generate_ai
from ..services.project_setup_ai import OFFICIAL_OPENAI_URL, generation_credentials, openai_client
from .test_work_assignments import WorkAssignmentFixture


@override_settings(ROOT_URLCONF='apps.planning_intelligence.tests.test_work_assignments',
                   OPENAI_API_KEY='', OPENAI_MODEL='gpt-4o', PROJECT_SETUP_AI_MODEL='',
                   BYOK_ENCRYPTION_KEY='personal-setup-test-encryption')
class ProjectSetupBYOKTests(WorkAssignmentFixture):
    def setUp(self):
        super().setUp()
        cache.clear()
        RoleModule.objects.get_or_create(role=self.role, module=self.module)
        for permission in self.module.permissions.filter(action__in=['read', 'create', 'update'], is_active=True):
            RolePermission.objects.get_or_create(role=self.role, permission=permission)
        self.base = '/api/v1/planning-intelligence/project-setup/'
        self.url = self.base + 'ai-settings/'
        self.secret = 'personal-test-credential-never-expose'
        self.brief = {
            'code': 'BYOK-TEST', 'name': 'Preview only', 'description': 'Develop and test internal application.',
            'project_type': 'software', 'department': 'ICT', 'phase': 'Phase 1',
            'start_date': '2026-09-21', 'end_date': '2026-12-20',
            'project_manager_id': self.reviewer.user_id,
            'team_member_ids': [self.worker.user_id], 'generation_mode': 'template',
        }

    def stored(self, user=None, *, key=None, model='gpt-4o'):
        return ProjectSetupAISettings.objects.create(
            user=user or self.owner, model=model,
            api_key_encrypted=encrypt_api_key(key or self.secret), last_tested_at=timezone.now(),
        )

    @staticmethod
    def completion(content=None):
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=8, completion_tokens=5),
            choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(
                content=json.dumps(content or {'ok': True}), refusal=None))],
        )

    def save_settings(self, data=None, *, response=None, error=None):
        with patch('openai.OpenAI') as client, patch('apps.rbac.ai_telemetry.record_usage') as telemetry:
            completions = client.return_value.__enter__.return_value.chat.completions.create
            completions.return_value = response or self.completion()
            completions.side_effect = error
            result = self.client.post(self.url, data or {'api_key': self.secret, 'model': 'gpt-4o'}, format='json')
        return result, client, telemetry

    def test_save_tests_structured_output_then_encrypts_and_returns_status_only(self):
        before = (Project.objects.count(), PlanningProject.objects.count(), ProjectTask.objects.count())
        response, client, telemetry = self.save_settings()
        self.assertEqual(response.status_code, 200, response.data)
        stored = ProjectSetupAISettings.objects.get(user=self.owner)
        self.assertNotEqual(stored.api_key_encrypted, self.secret)
        self.assertEqual(decrypt_api_key(stored.api_key_encrypted), self.secret)
        self.assertTrue(response.data['ai_available'])
        self.assertTrue(response.data['ai_settings']['key_configured'])
        self.assertTrue(response.data['ai_settings']['last_tested_at'])
        self.assertTrue(response.data['ai_settings']['storage_available'])
        self.assertNotIn(self.secret, str(response.data))
        self.assertNotIn(stored.api_key_encrypted, str(response.data))
        self.assertNotIn(self.secret, str(telemetry.call_args))
        self.assertEqual(before, (Project.objects.count(), PlanningProject.objects.count(), ProjectTask.objects.count()))
        client.assert_called_once_with(api_key=self.secret, timeout=20, max_retries=0,
                                       base_url=OFFICIAL_OPENAI_URL, organization='', project='')
        call = client.return_value.__enter__.return_value.chat.completions.create.call_args.kwargs
        self.assertTrue(call['response_format']['json_schema']['strict'])
        self.assertEqual(call['model'], 'gpt-4o')
        self.assertNotIn(self.secret, json.dumps(call))

    def test_existing_personal_key_can_test_and_save_a_new_model_without_resending_key(self):
        self.stored()
        response, client, _ = self.save_settings({'model': 'gpt-4o-mini'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['ai_settings']['model'], 'gpt-4o-mini')
        self.assertEqual(client.call_args.kwargs['api_key'], self.secret)
        self.assertEqual(decrypt_api_key(ProjectSetupAISettings.objects.get(user=self.owner).api_key_encrypted), self.secret)

    def test_personal_sdk_client_ignores_server_provider_and_account_environment(self):
        with patch.dict(os.environ, {'OPENAI_BASE_URL': 'https://another-provider.invalid/v1',
                                     'OPENAI_ORG_ID': 'server-org', 'OPENAI_PROJECT_ID': 'server-project'}):
            with openai_client(self.secret, personal=True) as client:
                self.assertEqual(str(client.base_url).rstrip('/'), OFFICIAL_OPENAI_URL)
                self.assertEqual(client.organization, '')
                self.assertEqual(client.project, '')

    def test_provider_failure_keeps_previous_key_model_and_test_stamp_and_redacts_error(self):
        previous = self.stored()
        error = type('AuthenticationError', (Exception,), {})(self.secret + ': private response')
        with self.assertLogs('apps.planning_intelligence.services.project_setup_ai', level='WARNING') as logs:
            response, _, telemetry = self.save_settings({'api_key': 'replacement-private-key', 'model': 'gpt-4o-mini'}, error=error)
        self.assertEqual(response.status_code, 503, response.data)
        self.assertIn('OpenAI rejected your API key', str(response.data))
        self.assertNotIn(self.secret, str(response.data) + str(logs.output) + str(telemetry.call_args))
        self.assertNotIn('replacement-private-key', str(response.data) + str(logs.output) + str(telemetry.call_args))
        current = ProjectSetupAISettings.objects.get(user=self.owner)
        self.assertEqual((current.api_key_encrypted, current.model, current.last_tested_at),
                         (previous.api_key_encrypted, previous.model, previous.last_tested_at))

    def test_refusal_or_invalid_structured_response_does_not_save_key(self):
        response = self.completion({'ok': False})
        result, _, _ = self.save_settings(response=response)
        self.assertEqual(result.status_code, 503, result.data)
        self.assertFalse(ProjectSetupAISettings.objects.exists())

    def test_status_options_and_delete_only_access_current_users_configuration(self):
        previous = self.stored()
        other = self.stored(self.outsider, key='other-person-private-key', model='gpt-4o-mini')
        self.client.force_authenticate(self.outsider)
        for path in [self.url, self.base + 'options/']:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['ai_settings']['model'], 'gpt-4o-mini')
            self.assertNotIn(self.secret, str(response.data))
            self.assertNotIn(previous.api_key_encrypted, str(response.data))
        result = self.client.delete(self.url, {'user_id': self.owner.pk}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertFalse(result.data['ai_settings']['key_configured'])
        self.assertFalse(ProjectSetupAISettings.objects.filter(pk=other.pk).exists())
        self.assertTrue(ProjectSetupAISettings.objects.filter(pk=previous.pk).exists())

    def test_save_cannot_target_another_user(self):
        other = self.stored(self.outsider, key='other-person-private-key')
        response, _, _ = self.save_settings({'api_key': self.secret, 'model': 'gpt-4o-mini', 'user_id': self.outsider.pk})
        self.assertEqual(response.status_code, 200, response.data)
        other.refresh_from_db()
        self.assertEqual(decrypt_api_key(other.api_key_encrypted), 'other-person-private-key')
        self.assertEqual(ProjectSetupAISettings.objects.get(user=self.owner).model, 'gpt-4o-mini')

    def test_every_settings_method_requires_planning_edit_access(self):
        self.stored()
        anonymous = APIClient()
        for client in [anonymous, self.worker_client]:
            for method in ['get', 'post', 'delete']:
                response = getattr(client, method)(self.url, {'api_key': self.secret, 'model': 'gpt-4o'}, format='json')
                self.assertIn(response.status_code, [401, 403], response.data)
        self.assertEqual(ProjectSetupAISettings.objects.count(), 1)

    def test_existing_project_planner_can_manage_personal_key_without_creation_rights(self):
        RolePermission.objects.filter(role=self.role, permission__action='create').delete()
        cache.clear()
        result, _, _ = self.save_settings()
        self.assertEqual(result.status_code, 200, result.data)
        status = self.client.get(self.url)
        self.assertEqual(status.status_code, 200, status.data)
        self.assertTrue(status.data['ai_settings']['key_configured'])
        self.assertNotIn(self.secret, str(status.data))
        creation_options = self.client.get(self.base + 'options/')
        self.assertEqual(creation_options.status_code, 403, creation_options.data)

    def test_model_and_key_validation_happens_before_provider_call(self):
        with patch('openai.OpenAI') as client:
            for data in [
                {'api_key': self.secret, 'model': 'model with spaces'},
                {'api_key': self.secret, 'model': 'x' * 129},
                {'api_key': self.secret, 'model': 'https://untrusted.invalid'},
                {'api_key': '', 'model': 'gpt-4o'},
                {'model': 'gpt-4o'},
            ]:
                response = self.client.post(self.url, data, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertNotIn(self.secret, str(response.data))
            client.assert_not_called()
        self.assertFalse(ProjectSetupAISettings.objects.exists())

    @override_settings(BYOK_ENCRYPTION_KEY='', SECRET_KEY='django-insecure-change-this-in-production')
    def test_unconfigured_secure_storage_prevents_network_and_saving(self):
        response = self.client.get(self.url)
        self.assertFalse(response.data['ai_settings']['storage_available'])
        result, client, _ = self.save_settings()
        self.assertEqual(result.status_code, 503, result.data)
        client.assert_not_called()
        self.assertFalse(ProjectSetupAISettings.objects.exists())

    @override_settings(OPENAI_API_KEY='valid-server-fallback')
    def test_delete_returns_server_fallback_status(self):
        self.stored()
        response = self.client.delete(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['ai_available'])
        self.assertFalse(response.data['ai_settings']['key_configured'])
        self.assertIsNone(response.data['ai_settings']['last_tested_at'])
        self.assertEqual(generation_credentials(self.owner), ('valid-server-fallback', 'gpt-4o', False))

    @override_settings(OPENAI_API_KEY='valid-server-fallback')
    def test_corrupt_personal_key_does_not_silently_use_server_key(self):
        stored = self.stored()
        stored.api_key_encrypted = 'corrupt-ciphertext'
        stored.save(update_fields=['api_key_encrypted'])
        with patch('openai.OpenAI') as client:
            result = self.client.get(self.url)
            self.assertFalse(result.data['ai_available'])
            self.assertTrue(result.data['ai_settings']['key_configured'])
            with self.assertRaises(SetupAIUnavailable):
                generate_ai(self.brief, {}, self.owner)
            client.assert_not_called()

    @override_settings(OPENAI_API_KEY='valid-server-fallback')
    def test_personal_generation_uses_own_key_and_model_and_never_falls_back_after_failure(self):
        self.stored(model='gpt-4o-mini')
        error = type('AuthenticationError', (Exception,), {})(self.secret)
        with patch('openai.OpenAI') as client, patch('apps.rbac.ai_telemetry.record_usage') as telemetry:
            client.return_value.chat.completions.create.side_effect = error
            with self.assertRaises(SetupAIUnavailable) as caught:
                generate_ai(self.brief, {}, self.owner)
            client.assert_called_once_with(api_key=self.secret, timeout=50, max_retries=0,
                                           base_url=OFFICIAL_OPENAI_URL, organization='', project='')
            self.assertEqual(client.return_value.chat.completions.create.call_args.kwargs['model'], 'gpt-4o-mini')
            self.assertIn('your API key', str(caught.exception.detail))
            self.assertNotIn(self.secret, str(caught.exception.detail) + str(telemetry.call_args))

    def test_personal_ai_preview_and_signed_token_contain_no_credentials(self):
        from ..project_setup_serializers import ProjectSetupBriefSerializer
        from ..services.project_setup import generate_template
        self.stored()
        serializer = ProjectSetupBriefSerializer(data=self.brief)
        serializer.is_valid(raise_exception=True)
        generated = generate_template(serializer.validated_data)
        with patch('openai.OpenAI') as client, patch('apps.rbac.ai_telemetry.record_usage'):
            client.return_value.chat.completions.create.return_value = self.completion(generated)
            response = self.client.post(self.base + 'preview/', {**self.brief, 'generation_mode': 'ai'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['plan']['source'], 'ai')
        decoded = signing.loads(response.data['preview_token'], salt=PREVIEW_SALT)
        self.assertNotIn(self.secret, str(response.data) + str(decoded))
        self.assertNotIn('api_key', str(decoded))
        self.assertFalse(Project.objects.filter(code='BYOK-TEST').exists())

    def test_template_is_independent_of_personal_key_and_provider(self):
        stored = self.stored()
        stored.api_key_encrypted = 'unavailable-ciphertext'
        stored.save(update_fields=['api_key_encrypted'])
        with patch('openai.OpenAI') as client:
            response = self.client.post(self.base + 'preview/', self.brief, format='json')
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['plan']['source'], 'template')
            client.assert_not_called()

    def test_settings_test_is_throttled_but_status_and_delete_remain_available(self):
        with patch('apps.planning_intelligence.project_setup_views.SetupAISettingsThrottle.rate', '1/hour'):
            first, client, _ = self.save_settings()
            self.assertEqual(first.status_code, 200, first.data)
            second, second_client, _ = self.save_settings()
            self.assertEqual(second.status_code, 429, second.data)
            second_client.assert_not_called()
            self.assertEqual(self.client.get(self.url).status_code, 200)
            self.assertEqual(self.client.delete(self.url).status_code, 200)
