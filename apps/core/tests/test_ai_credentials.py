"""Synthetic administrator credential lifecycle; no real provider or application DB."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import include, path
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.ai_credential_models import AIProviderConfiguration, AIProviderCredential
from apps.core.ai_credential_probes import probe_credential
from apps.core.ai_credential_views import ProviderStatusView
from apps.core.ai_credentials import (
    AICredentialUnavailable, TestCredential, decrypt_api_key, encryption_ready,
    get_provider_api_key, get_provider_configuration, resolve_provider_credential,
)
from apps.rbac.models import AuditLog, Permission, Role, UserPermissionOverride, UserRole
from apps.rbac.route_guard import secure_module_endpoints
from apps.sales.tests.access_fixtures import grant_sales_actions


urlpatterns = [
    path('api/v1/rbac/admin/ai-api-keys/', include('apps.core.ai_credential_urls')),
    path('api/v1/rbac/ai-provider-status/', ProviderStatusView.as_view()),
]
secure_module_endpoints(urlpatterns)
ROOT = '/api/v1/rbac/admin/ai-api-keys/'
KEY = 'synthetic-provider-secret-for-tests-only'


@override_settings(ROOT_URLCONF=__name__, AI_CREDENTIAL_ENCRYPTION_KEY='synthetic-registry-encryption',
                   BYOK_ENCRYPTION_KEY=None)
class AICredentialRegistryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('ai-admin', email='ai-admin@example.test', is_superuser=True)
        grant_sales_actions(self.user, 'admin_dashboard')
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.network = patch('apps.core.ai_credential_views.probe_credential', return_value={'success': True, 'reason': ''})
        self.probe = self.network.start()
        self.addCleanup(self.network.stop)

    def create(self, **overrides):
        response = self.client.post(ROOT, {
            'provider': 'anthropic', 'label': 'Primary account', 'api_key': KEY,
            'model': 'synthetic-model', 'expected_provider_revision': 0, **overrides,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data['credentials'][-1]

    def revisions(self, key):
        configuration = AIProviderConfiguration.objects.get(provider=key['provider'])
        row = AIProviderCredential.objects.get(pk=key['id'])
        return {'expected_revision': row.revision, 'expected_provider_revision': configuration.revision}

    def test_create_encrypts_selects_and_never_returns_or_audits_secret(self):
        key = self.create()
        row = AIProviderCredential.objects.get(pk=key['id'])
        self.assertNotEqual(row.encrypted_key, KEY)
        self.assertEqual(decrypt_api_key(row.encrypted_key), KEY)
        self.assertTrue(key['is_selected'])
        response = self.client.get(ROOT)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(KEY, str(response.data))
        self.assertNotIn(row.encrypted_key, str(response.data))
        self.assertNotIn(KEY, str(list(AuditLog.objects.values())))
        self.assertNotIn(row.encrypted_key, str(list(AuditLog.objects.values())))
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(get_provider_api_key('anthropic', fallback='legacy-key'), KEY)
        self.probe.assert_not_called()

    def test_multiple_keys_select_only_requested_provider_and_preserve_model(self):
        first = self.create()
        second = self.create(label='Second account', api_key='another-synthetic-provider-key', expected_provider_revision=1)
        self.assertFalse(second['is_selected'])
        self.assertEqual(get_provider_api_key('anthropic'), KEY)
        response = self.client.post(ROOT + second['id'] + '/select/', self.revisions(second), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(get_provider_api_key('anthropic'), 'another-synthetic-provider-key')
        self.assertEqual(get_provider_api_key('openai', fallback='legacy-openai'), 'legacy-openai')
        self.assertEqual(get_provider_configuration('anthropic')['model'], 'synthetic-model')
        self.assertNotEqual(first['id'], second['id'])

    def test_rotation_without_process_cache_and_blank_key_cannot_erase(self):
        key = self.create()
        for replacement in ('replacement-key-one-for-tests', 'replacement-key-two-for-tests'):
            response = self.client.patch(ROOT + key['id'] + '/', {
                **self.revisions(key), 'api_key': replacement,
            }, format='json')
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(get_provider_api_key('anthropic'), replacement)
        response = self.client.patch(ROOT + key['id'] + '/', {**self.revisions(key), 'label': 'Renamed'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(get_provider_api_key('anthropic'), replacement)
        response = self.client.patch(ROOT + key['id'] + '/', {**self.revisions(key), 'api_key': ''}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(get_provider_api_key('anthropic'), replacement)

    def test_disabled_provider_and_deleted_selected_key_never_fall_back(self):
        key = self.create()
        response = self.client.patch(ROOT + 'providers/anthropic/', {'expected_revision': 1, 'enabled': False}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(get_provider_api_key('anthropic', 'legacy'), '')
        response = self.client.post(ROOT + key['id'] + '/select/', self.revisions(key), format='json')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(get_provider_configuration('anthropic')['enabled'])
        response = self.client.delete(ROOT + key['id'] + '/', self.revisions(key), format='json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(get_provider_configuration('anthropic')['managed'])
        self.assertIsNone(get_provider_configuration('anthropic')['selected_credential_id'])
        self.assertEqual(get_provider_api_key('anthropic', 'legacy'), '')

    def test_disabled_selected_credential_does_not_select_an_alternative(self):
        key = self.create()
        self.create(label='Other', api_key='second-valid-length-synthetic-key', expected_provider_revision=1)
        response = self.client.patch(ROOT + key['id'] + '/', {**self.revisions(key), 'enabled': False}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(get_provider_api_key('anthropic', 'legacy'), '')
        response = self.client.post(ROOT + key['id'] + '/select/', self.revisions(key), format='json')
        self.assertEqual(response.status_code, 400)

    def test_provider_can_be_disabled_before_any_key_without_changing_legacy_providers(self):
        response = self.client.patch(ROOT + 'providers/anthropic/', {'expected_revision': 0, 'enabled': False}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(get_provider_api_key('anthropic', 'legacy-anthropic'), '')
        self.assertEqual(get_provider_api_key('openai', 'legacy-openai'), 'legacy-openai')

    def test_missing_encryption_and_changed_encryption_fail_closed(self):
        with override_settings(AI_CREDENTIAL_ENCRYPTION_KEY=None, BYOK_ENCRYPTION_KEY=None, SECRET_KEY='not-an-encryption-fallback'):
            self.assertFalse(encryption_ready())
            response = self.client.post(ROOT, {'provider': 'anthropic', 'label': 'New', 'api_key': KEY,
                                               'expected_provider_revision': 0}, format='json')
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.data['reason'], 'encryption_unavailable')
            self.assertFalse(AIProviderCredential.objects.exists())
        self.create()
        with override_settings(AI_CREDENTIAL_ENCRYPTION_KEY='different-material'):
            with self.assertRaises(AICredentialUnavailable) as caught:
                get_provider_api_key('anthropic', 'legacy')
            self.assertEqual(caught.exception.reason, 'credential_unreadable')

    def test_explicit_byok_material_is_supported_without_default_secret(self):
        with override_settings(AI_CREDENTIAL_ENCRYPTION_KEY=None, BYOK_ENCRYPTION_KEY=b'explicit-dedicated-key'):
            self.create()
            self.assertTrue(encryption_ready())
            self.assertEqual(get_provider_api_key('anthropic'), KEY)

    def test_database_failure_never_uses_environment_fallback(self):
        with patch('apps.core.ai_credentials._provider_record', side_effect=AICredentialUnavailable('registry_unavailable')):
            with self.assertRaises(AICredentialUnavailable):
                get_provider_api_key('anthropic', 'legacy')
        with patch.object(AIProviderConfiguration.objects, 'select_related', side_effect=DatabaseError('private database details')):
            response = self.client.get(ROOT)
            self.assertEqual(response.status_code, 503)
            self.assertNotIn('private', str(response.data))
            with self.assertRaises(AICredentialUnavailable) as caught:
                get_provider_api_key('anthropic', 'legacy')
            self.assertEqual(caught.exception.reason, 'registry_unavailable')

    def test_resolver_reads_key_and_ownership_metadata_from_one_snapshot(self):
        self.create()
        with self.assertNumQueries(1):
            key, metadata = resolve_provider_credential('anthropic', 'legacy')
        self.assertEqual(key, KEY)
        self.assertTrue(metadata['managed'])
        self.assertTrue(metadata['ready'])

    def test_lazy_fallback_is_evaluated_only_for_unmanaged_provider(self):
        self.create()
        with patch('builtins.input', side_effect=AssertionError('Legacy key must not be accessed')) as legacy:
            self.assertEqual(get_provider_api_key('anthropic', legacy), KEY)
            configuration = AIProviderConfiguration.objects.get(provider='anthropic')
            configuration.enabled = False
            configuration.save()
            self.assertEqual(get_provider_api_key('anthropic', legacy), '')
            legacy.assert_not_called()
        calls = []
        self.assertEqual(get_provider_api_key('openai', lambda: calls.append('legacy') or 'legacy-key'), 'legacy-key')
        self.assertEqual(calls, ['legacy'])

    def test_stale_key_and_provider_revisions_preserve_newer_values(self):
        key = self.create()
        old = self.revisions(key)
        response = self.client.patch(ROOT + key['id'] + '/', {**old, 'label': 'Latest'}, format='json')
        self.assertEqual(response.status_code, 200)
        for method, url, payload in (
            ('patch', ROOT + key['id'] + '/', {**old, 'api_key': 'stale-replacement-must-not-save'}),
            ('post', ROOT + key['id'] + '/select/', old),
            ('delete', ROOT + key['id'] + '/', old),
            ('patch', ROOT + 'providers/anthropic/', {'expected_revision': 1, 'enabled': False}),
            ('post', ROOT, {'provider': 'anthropic', 'label': 'stale', 'api_key': KEY, 'expected_provider_revision': 0}),
        ):
            with self.subTest(method=method, url=url):
                response = getattr(self.client, method)(url, payload, format='json')
                self.assertEqual(response.status_code, 409, response.data)
                self.assertEqual(response.data['code'], 'ai_credentials_stale')
        self.assertEqual(AIProviderCredential.objects.get(pk=key['id']).label, 'Latest')
        self.assertEqual(get_provider_api_key('anthropic'), KEY)

    def test_unknown_fields_provider_mutation_and_nonstrict_revisions_are_denied(self):
        key = self.create()
        for extra in ({'provider': 'openai'}, {'encrypted_key': KEY}, {'expected_revision': True}, {'api_key': 123}):
            response = self.client.patch(ROOT + key['id'] + '/', {**self.revisions(key), **extra}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(get_provider_api_key('anthropic'), KEY)

    def test_audit_failure_rolls_back_creation_and_secret_rotation(self):
        with patch('apps.core.ai_credential_views.create_audit_log', side_effect=RuntimeError('private audit error')):
            response = self.client.post(ROOT, {'provider': 'anthropic', 'label': 'New', 'api_key': KEY,
                                               'expected_provider_revision': 0}, format='json')
        self.assertEqual(response.status_code, 503)
        self.assertFalse(AIProviderCredential.objects.exists())
        self.assertFalse(AIProviderConfiguration.objects.exists())
        key = self.create()
        with patch('apps.core.ai_credential_views.create_audit_log', side_effect=RuntimeError('private audit error')):
            response = self.client.patch(ROOT + key['id'] + '/', {**self.revisions(key), 'api_key': 'unsaved-rotation-key-for-tests'}, format='json')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(get_provider_api_key('anthropic'), KEY)

    def test_admin_roles_work_but_staff_ordinary_inactive_locked_and_explicit_deny_do_not(self):
        role_user = get_user_model().objects.create_user('role-admin', email='role-admin@example.test')
        grant_sales_actions(role_user, 'admin_dashboard')
        self.client.force_authenticate(role_user)
        for code in ('admin', 'ict_admin', 'super_admin'):
            role, _ = Role.objects.get_or_create(code=code, defaults={'name': code, 'level': 1})
            assignment = UserRole.objects.create(user_profile=role_user.rbac_profile, role=role)
            self.assertEqual(self.client.get(ROOT).status_code, 200)
            assignment.delete()
        role_user.is_staff = True
        role_user.save()
        self.assertEqual(self.client.get(ROOT).status_code, 403)
        self.client.force_authenticate(self.user)
        profile = self.user.rbac_profile
        for changes in ({'status': 'inactive'}, {'is_deleted': True}, {'locked_until': timezone.now() + timedelta(hours=1)}):
            original = {name: getattr(profile, name) for name in changes}
            for name, value in changes.items():
                setattr(profile, name, value)
            profile.save()
            self.assertEqual(self.client.get(ROOT).status_code, 403)
            for name, value in original.items():
                setattr(profile, name, value)
            profile.save()
        permission = Permission.objects.filter(module__code='admin_dashboard', action='read').first()
        UserPermissionOverride.objects.create(user_profile=profile, permission=permission, allowed=False)
        self.assertEqual(self.client.get(ROOT).status_code, 403)
        self.probe.assert_not_called()

    def test_ordinary_authenticated_status_is_minimal_and_anonymous_denied(self):
        self.create()
        ordinary = get_user_model().objects.create_user('ordinary-status-reader', email='ordinary@example.test')
        self.client.force_authenticate(ordinary)
        response = self.client.get('/api/v1/rbac/ai-provider-status/')
        self.assertEqual(response.status_code, 200)
        for provider in response.data['providers']:
            self.assertEqual(set(provider), {'provider', 'managed', 'enabled', 'ready', 'model'})
        self.assertNotIn(KEY, str(response.data))
        self.assertEqual(self.client.get(ROOT).status_code, 403)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get('/api/v1/rbac/ai-provider-status/').status_code, (401, 403))

    def test_explicit_write_denials_prevent_mutation_and_provider_spending(self):
        key = self.create()
        revision = self.revisions(key)
        cases = (
            ('create', 'post', ROOT, {'provider': 'openai', 'label': 'Denied', 'api_key': KEY,
                                     'expected_provider_revision': 0}),
            ('update', 'patch', ROOT + key['id'] + '/', {**revision, 'api_key': 'denied-secret-replacement'}),
            ('update', 'post', ROOT + key['id'] + '/select/', revision),
            ('update', 'patch', ROOT + 'providers/anthropic/', {'expected_revision': 1, 'enabled': False}),
            ('update', 'post', ROOT + key['id'] + '/test/', {'expected_revision': 1, 'model': 'synthetic-model'}),
            ('delete', 'delete', ROOT + key['id'] + '/', revision),
        )
        for action, method, url, payload in cases:
            permission = Permission.objects.get(module__code='admin_dashboard', action=action)
            denial = UserPermissionOverride.objects.create(user_profile=self.user.rbac_profile,
                                                           permission=permission, allowed=False)
            with self.subTest(action=action, method=method, url=url):
                response = getattr(self.client, method)(url, payload, format='json')
                self.assertEqual(response.status_code, 403, response.data)
                self.assertEqual(self.client.get(ROOT).status_code, 200)
                self.assertEqual(get_provider_api_key('anthropic'), KEY)
                self.assertEqual(AIProviderCredential.objects.count(), 1)
            denial.delete()
        self.probe.assert_not_called()

    @override_settings(MIDDLEWARE=[
        'django.contrib.sessions.middleware.SessionMiddleware',
        'django.contrib.auth.middleware.AuthenticationMiddleware',
        'apps.rbac.middleware.RBACMiddleware',
    ])
    def test_request_audit_middleware_never_copies_json_secret_on_success_or_denial(self):
        key = self.create()
        response = self.client.patch(ROOT + key['id'] + '/', {
            **self.revisions(key), 'api_key': KEY, 'unsupported_field': KEY,
        }, format='json')
        self.assertEqual(response.status_code, 400)
        audits = list(AuditLog.objects.values())
        self.assertTrue(any(row['metadata'].get('audit_source') == 'request' for row in audits))
        self.assertTrue(any(row['metadata'].get('audit_source') == 'ai_credentials' for row in audits))
        self.assertNotIn(KEY, str(audits))
        self.assertNotIn(AIProviderCredential.objects.get(pk=key['id']).encrypted_key, str(audits))

    def test_synthetic_test_retains_failed_credentials_and_discards_untrusted_diagnostics(self):
        key = self.create()
        for answer, expected in (({'success': True, 'reason': ''}, ''),
                                 ({'success': False, 'reason': 'provider_request'}, 'provider_request'),
                                 ({'success': False, 'reason': KEY}, 'provider_unavailable')):
            self.probe.return_value = answer
            response = self.client.post(ROOT + key['id'] + '/test/', {
                'expected_revision': self.revisions(key)['expected_revision'], 'model': 'synthetic-model',
            }, format='json')
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['test']['reason'], expected)
            self.assertNotIn(KEY, str(response.data))
            self.assertEqual(get_provider_api_key('anthropic'), KEY)
            credential = self.probe.call_args.args[0]
            self.assertEqual(credential.api_key, KEY)
            self.assertNotIn(KEY, repr(credential))
        self.assertNotIn(KEY, str(list(AuditLog.objects.values())))

    def test_inflight_connection_test_cannot_overwrite_rotated_credential(self):
        key = self.create()
        revision = self.revisions(key)['expected_revision']
        def rotate_during_probe(credential):
            AIProviderCredential.objects.filter(pk=key['id']).update(revision=revision + 1)
            return {'success': True, 'reason': ''}
        self.probe.side_effect = rotate_during_probe
        response = self.client.post(ROOT + key['id'] + '/test/', {'expected_revision': revision, 'model': 'synthetic-model'}, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertIsNone(AIProviderCredential.objects.get(pk=key['id']).last_tested_at)

    def test_parallel_test_and_cache_failure_do_not_spend_provider_calls(self):
        key = self.create()
        payload = {'expected_revision': key['revision'], 'model': 'synthetic-model'}
        for failure, expected in ((False, 429), (RuntimeError('private cache detail'), 503)):
            options = {'side_effect': failure} if isinstance(failure, Exception) else {'return_value': failure}
            with patch('apps.core.ai_credential_views.cache.add', **options):
                response = self.client.post(ROOT + key['id'] + '/test/', payload, format='json')
            self.assertEqual(response.status_code, expected, response.data)
            self.assertNotIn('private', str(response.data))
        self.probe.assert_not_called()


class AICredentialProbeTests(SimpleTestCase):
    def test_known_rejection_categories_are_static_and_unknown_body_is_never_echoed(self):
        with patch('anthropic.Anthropic') as constructor:
            client = constructor.return_value.__enter__.return_value
            for message, reason in (
                ('Your credit balance is too low to access the Anthropic API. ' + KEY, 'credit_balance_exhausted'),
                ('model: synthetic-model is not available ' + KEY, 'model_unavailable'),
                ('output_config.format is unsupported ' + KEY, 'request_configuration_error'),
                ('Unexpected source or account diagnostic ' + KEY, 'provider_request'),
            ):
                error = type('BadRequestError', (Exception,), {})(KEY)
                error.body = {'error': {'message': message}}
                client.messages.create.side_effect = error
                result = probe_credential(TestCredential('anthropic', 'synthetic-model', KEY))
                self.assertEqual(result, {'success': False, 'reason': reason})
                self.assertNotIn(KEY, str(result))

    def test_anthropic_probe_is_synthetic_official_and_does_not_return_raw_failure(self):
        with patch('anthropic.Anthropic') as constructor:
            client = constructor.return_value.__enter__.return_value
            client.messages.create.return_value = SimpleNamespace(stop_reason='end_turn', content=[SimpleNamespace(type='text', text='{"ok":true}')])
            self.assertEqual(probe_credential(TestCredential('anthropic', 'synthetic-model', KEY)), {'success': True, 'reason': ''})
            self.assertEqual(constructor.call_args.kwargs['base_url'], 'https://api.anthropic.com')
            self.assertEqual(constructor.call_args.kwargs['max_retries'], 0)
            self.assertEqual(client.messages.create.call_args.kwargs['max_tokens'], 1024)
            self.assertNotIn(KEY, str(client.messages.create.call_args.kwargs))
            error = type('BadRequestError', (Exception,), {})(KEY)
            client.messages.create.side_effect = error
            self.assertEqual(probe_credential(TestCredential('anthropic', 'synthetic-model', KEY)),
                             {'success': False, 'reason': 'provider_request'})

    def test_openai_probe_rejects_truncation_without_echoing_model_content(self):
        with patch('openai.OpenAI') as constructor:
            client = constructor.return_value.__enter__.return_value
            client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(
                finish_reason='length', message=SimpleNamespace(content=KEY, refusal=None))])
            self.assertEqual(probe_credential(TestCredential('openai', 'synthetic-model', KEY)),
                             {'success': False, 'reason': 'provider_incomplete'})
            self.assertEqual(constructor.call_args.kwargs['organization'], '')
            self.assertEqual(constructor.call_args.kwargs['project'], '')

    def test_gemini_probe_has_fixed_host_header_key_and_no_user_content(self):
        with patch('requests.post') as post:
            post.return_value = SimpleNamespace(status_code=200, json=lambda: {
                'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': '{"ok":true}'}]}}]})
            self.assertEqual(probe_credential(TestCredential('gemini', 'synthetic-model', KEY)), {'success': True, 'reason': ''})
            self.assertEqual(post.call_args.args[0], 'https://generativelanguage.googleapis.com/v1beta/models/synthetic-model:generateContent')
            self.assertNotIn(KEY, post.call_args.args[0])
            self.assertEqual(post.call_args.kwargs['headers'], {'x-goog-api-key': KEY})
            post.return_value = SimpleNamespace(status_code=403)
            self.assertEqual(probe_credential(TestCredential('gemini', 'synthetic-model', KEY)),
                             {'success': False, 'reason': 'provider_permission'})
