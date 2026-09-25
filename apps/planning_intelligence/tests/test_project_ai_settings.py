"""Project credentials stay encrypted and bound to the selected AI provider."""
from copy import deepcopy
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.core.project_models import Project, ProjectMember
from apps.rbac.models import Module, Permission
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints
from apps.users.models import User
from ..models import PlanningProject
from ..services.operational_jobs import get_or_create_job
from ..services.preview_confirmation import source_fingerprint
from ..services.byok_crypto import decrypt_api_key, encrypt_api_key
from ..services import project_ai
from ..views import PlanningProjectViewSet
from .test_business_approval_gates import grant_test_approval

router = DefaultRouter()
router.register('api/v1/planning-intelligence/projects', PlanningProjectViewSet, basename='planning-project')
urlpatterns = router.urls
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__, BYOK_ENCRYPTION_KEY='isolated-gemini-settings-tests')
class ProjectAISettingsTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='gemini-settings-owner', email='gemini-owner@example.test')
        module, _ = Module.objects.get_or_create(code='planning_package', defaults={'name': 'Planning'})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        grant_test_approval((self.owner,))
        self.enterprise = Project.objects.create(code='GEMINI-SETTINGS', name='Gemini scope', owner=self.owner)
        self.project = PlanningProject.objects.create(name='Gemini scope', enterprise_project=self.enterprise, created_by=self.owner)
        self.url = f'/api/v1/planning-intelligence/projects/{self.project.pk}/ai-settings/'
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.key = 'AIza' + 'isolated-Google-test-key-1234567890'

    def save_gemini(self):
        response = self.client.post(self.url, {
            'provider': 'gemini', 'enabled': True,
            'model': project_ai.DEFAULT_GEMINI_MODEL, 'api_key': self.key,
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.project.refresh_from_db()
        return response.data

    def test_catalog_and_encrypted_gemini_save_never_return_credentials(self):
        catalog = self.client.get(self.url)
        self.assertEqual(catalog.status_code, 200)
        self.assertEqual({choice['value'] for choice in catalog.data['provider_choices']}, {'anthropic', 'gemini'})
        result = self.save_gemini()
        self.assertEqual(result['provider'], 'gemini')
        self.assertTrue(result['key_configured'])
        self.assertEqual(self.project.ai_settings['api_key_provider'], 'gemini')
        encrypted = self.project.ai_settings['api_key_encrypted']
        self.assertNotEqual(encrypted, self.key)
        self.assertEqual(decrypt_api_key(encrypted), self.key)
        for response in (result, self.client.get(self.url).data):
            self.assertNotIn(self.key, str(response))
            self.assertNotIn(encrypted, str(response))
            self.assertNotIn('api_key_encrypted', response)

    def test_switch_provider_requires_new_key_and_preserves_old_settings_on_rejection(self):
        self.project.ai_settings = {'enabled': True, 'model': project_ai.DEFAULT_MODEL_BY_PROVIDER['anthropic'],
                                    'api_key_encrypted': encrypt_api_key('sk-ant-legacy-private-test-credential')}
        self.project.save(update_fields=['ai_settings'])
        before = deepcopy(self.project.ai_settings)
        rejected = self.client.post(self.url, {'provider': 'gemini', 'enabled': True}, format='json')
        self.assertEqual(rejected.status_code, 400)
        self.project.refresh_from_db()
        self.assertEqual(self.project.ai_settings, before)
        self.save_gemini()
        self.assertEqual(decrypt_api_key(self.project.ai_settings['api_key_encrypted']), self.key)

    def test_provider_model_validation_and_disabling_keep_the_saved_gemini_key(self):
        self.save_gemini()
        before = deepcopy(self.project.ai_settings)
        for payload in ({'model': project_ai.DEFAULT_MODEL_BY_PROVIDER['anthropic']},
                        {'provider': 'unknown'}, {'api_key': 'sk-ant-this-is-a-different-provider-key'}):
            rejected = self.client.post(self.url, payload, format='json')
            self.assertEqual(rejected.status_code, 400, rejected.data)
            self.project.refresh_from_db()
            self.assertEqual(self.project.ai_settings, before)
        response = self.client.post(self.url, {'enabled': False}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data['enabled'])
        self.project.refresh_from_db()
        self.assertEqual(self.project.ai_settings['api_key_encrypted'], before['api_key_encrypted'])
        self.assertEqual(self.project.ai_settings['provider'], 'gemini')

    def test_connection_uses_saved_provider_and_returns_actionable_failure(self):
        self.save_gemini()
        failure = {'success': False, 'provider': 'gemini', 'model': project_ai.DEFAULT_GEMINI_MODEL,
                   'error': 'Google rejected the Gemini API key. Check the key in Google AI Studio.'}
        with patch.object(project_ai, 'test_project_ai_connection', return_value=failure) as probe:
            response = self.client.post(self.url + 'test/', {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data['success'])
        self.assertEqual(response.data['message'], failure['error'])
        self.assertEqual(probe.call_args.args[0].ai_settings['provider'], 'gemini')
        self.assertNotIn(self.key, str(response.data))

    def test_optional_ai_gate_accepts_saved_gemini_and_reports_missing_provider(self):
        gate = PlanningProjectViewSet()._require_byok
        self.save_gemini()
        self.assertIsNone(gate(self.project))
        self.project.ai_settings = {}
        rejected = gate(self.project)
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(rejected.data['code'], 'byok_required')
        self.assertIn('choose a provider', rejected.data['error'])

    def test_delete_and_disabled_connection_do_not_call_provider(self):
        self.save_gemini()
        response = self.client.delete(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data['key_configured'])
        self.assertEqual(len(response.data['provider_choices']), 2)
        self.project.refresh_from_db()
        self.assertEqual(self.project.ai_settings, {})
        with patch.object(project_ai, 'test_project_ai_connection') as probe:
            response = self.client.post(self.url + 'test/', {}, format='json')
        self.assertEqual(response.status_code, 400)
        probe.assert_not_called()

    def test_read_only_member_cannot_change_or_test_project_credentials(self):
        self.save_gemini()
        viewer = User.objects.create_user(username='gemini-settings-viewer', email='gemini-viewer@example.test')
        grant_test_approval((viewer,))
        ProjectMember.objects.create(project=self.enterprise, user=viewer, role='reviewer')
        self.client.force_authenticate(viewer)
        with patch.object(project_ai, 'test_project_ai_connection') as probe:
            for url, data in ((self.url, {'enabled': False}), (self.url + 'test/', {})):
                response = self.client.post(url, data, format='json')
                self.assertEqual(response.status_code, 403, response.data)
        probe.assert_not_called()

    def test_changed_credentials_invalidate_completed_analysis_job_without_overwriting_it(self):
        self.save_gemini()
        original_fingerprint = source_fingerprint(self.project)
        previous, _ = get_or_create_job(self.project, 'analyze', {}, self.owner)
        previous.status = 'succeeded'
        previous.result_data = {'extraction_summary': {'status': 'partial', 'chunks_remaining': 2}}
        previous.save(update_fields=['status', 'result_data'])
        response = self.client.post(self.url, {'api_key': self.key + '-replacement'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.project.refresh_from_db()
        self.assertNotEqual(source_fingerprint(self.project), original_fingerprint)
        current, created = get_or_create_job(self.project, 'analyze', {}, self.owner)
        self.assertTrue(created)
        self.assertNotEqual(current.pk, previous.pk)
        previous.refresh_from_db()
        self.assertEqual(previous.status, 'succeeded')
        self.assertEqual(previous.result_data, {'extraction_summary': {'status': 'partial', 'chunks_remaining': 2}})

    def test_unchanged_settings_preserve_review_fingerprint_and_reusable_job(self):
        self.save_gemini()
        original_updated_at = self.project.updated_at
        original_fingerprint = source_fingerprint(self.project)
        previous, _ = get_or_create_job(self.project, 'analyze', {}, self.owner)
        response = self.client.post(self.url, {
            'provider': 'gemini', 'enabled': True, 'model': project_ai.DEFAULT_GEMINI_MODEL,
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.project.refresh_from_db()
        self.assertEqual(self.project.updated_at, original_updated_at)
        self.assertEqual(source_fingerprint(self.project), original_fingerprint)
        current, created = get_or_create_job(self.project, 'analyze', {}, self.owner)
        self.assertFalse(created)
        self.assertEqual(current.pk, previous.pk)
