"""F04 checks through the actual guarded router, using synthetic adapters only."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import include, path, resolve
from django.utils import timezone
from rest_framework.test import APIClient

from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, RolePermission, UserProfile, UserRole
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import ModuleActionGuardMixin, secure_module_endpoints
from . import service
from .models import WrenchConfig, WrenchSyncLog, WrenchS3SyncJob

urlpatterns = [path('api/v1/wrench/', include('apps.wrench_integration.urls'))]
secure_module_endpoints(urlpatterns)
BASE = '/api/v1/wrench/'


def response(body=None, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = b'not a real network response'
    result.json = Mock(return_value=body)
    return result


def document_payload(count=1, total=None):
    return {
        'ObjectSearchResults': [
            [{'PropertyName': 'DOC_NO', 'PropertyValue': f'SYNTHETIC-{i}'}]
            for i in range(count)
        ],
        'TotalSearchResultCount': count if total is None else total,
        'OperationStatus': 0,
    }


@override_settings(ROOT_URLCONF=__name__)
class WrenchSyncOutcomeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        # Any missed mock is an explicit test failure, even if application code
        # catches its exception. Neither adapters nor async workers may go live.
        self.network = patch('requests.sessions.Session.request', side_effect=AssertionError('Live requests forbidden')).start()
        self.addCleanup(patch.stopall)
        self.addCleanup(self.network.assert_not_called)
        self.sleep = patch('apps.wrench_integration.service.time.sleep').start()
        self.user = get_user_model().objects.create_user('synthetic-wrench-admin', email='wrench@example.test')
        self.role = Role.objects.create(code='admin', name='Synthetic integration administrator', level=2)
        self.module, _ = Module.objects.get_or_create(code='wrench_integration', defaults={'name': 'Wrench Integration'})
        ensure_module_actions(Module, Permission, module_ids=[self.module.pk])
        RoleModule.objects.create(role=self.role, module=self.module)
        for permission in self.module.permissions.filter(action__in=['read', 'create', 'update', 'export']):
            RolePermission.objects.create(role=self.role, permission=permission)
        # Preserve the existing ViewSet service mapping and action policy:
        # module_required=data_mining; the route's "sync" word requires update.
        self.sync_module, _ = Module.objects.get_or_create(code='data_mining', defaults={'name': 'Data Mining'})
        ensure_module_actions(Module, Permission, module_ids=[self.sync_module.pk])
        RoleModule.objects.create(role=self.role, module=self.sync_module)
        for permission in self.sync_module.permissions.filter(action__in=['read', 'update']):
            RolePermission.objects.create(role=self.role, permission=permission)
        organization = Organization.objects.create(code='SYNTHETIC-WRENCH', name='Synthetic organization')
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'organization': organization, 'status': 'active'})
        UserRole.objects.create(user_profile=profile, role=self.role)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.config = WrenchConfig.objects.create(
            base_url='https://wrench.example.test', svc_url='https://svc.example.test',
            login_name='synthetic-reader', session_token='synthetic-test-token',
            token_obtained_at=timezone.now(), created_by=self.user,
        )

    def trigger(self, entity='document', direction='wrench_to_radai'):
        return self.client.post(BASE + 'sync/trigger/', {'direction': direction, 'entity_type': entity}, format='json')

    def assert_failed(self, result):
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data['status'], 'failed')
        self.assertIs(result.data['sync_details']['retrieval_validated'], False)
        self.assertEqual(result.data['records_synced'], 0)
        self.assertEqual(WrenchSyncLog.objects.get(pk=result.data['id']).status, 'failed')
        self.assertTrue(result.data['completed_at'])

    def test_real_router_keeps_admin_and_module_action_guards(self):
        self.assertTrue(issubclass(resolve(BASE + 'sync/trigger/').func.cls, ModuleActionGuardMixin))
        RolePermission.objects.filter(role=self.role, permission__module=self.sync_module, permission__action='update').delete()
        with patch.object(service, 'run_sync') as run:
            result = self.trigger()
        self.assertEqual(result.status_code, 403)
        run.assert_not_called()
        self.assertFalse(WrenchSyncLog.objects.exists())

    def test_module_grant_does_not_replace_existing_admin_requirement(self):
        self.role.code = 'synthetic-non-admin'
        self.role.save()
        with patch.object(service, 'run_sync') as run:
            result = self.trigger()
        self.assertEqual(result.status_code, 403)
        run.assert_not_called()

    def test_unauthenticated_request_is_denied_without_operation(self):
        self.client.force_authenticate(None)
        with patch.object(service, 'run_sync') as run:
            result = self.trigger()
        self.assertIn(result.status_code, (401, 403))
        run.assert_not_called()

    def test_unsupported_requests_create_no_logs_or_external_calls(self):
        before = deepcopy(list(WrenchConfig.objects.values()))
        with patch.object(service, '_get_active_config') as config:
            for direction, entity in [
                ('radai_to_wrench', 'document'), ('radai_to_wrench', 'all'),
                ('wrench_to_radai', 'project'), ('wrench_to_radai', 'user'), ('wrench_to_radai', 'all'),
            ]:
                with self.subTest(direction=direction, entity=entity):
                    result = self.trigger(entity, direction)
                    self.assertEqual(result.status_code, 400)
                    self.assertEqual(result.data['code'], 'sync_unsupported')
                    self.assertEqual(result.data['status'], 'unavailable')
                    with self.assertRaises(service.UnsupportedSyncOperation):
                        service.run_sync(direction, entity, self.user)
            config.assert_not_called()
        self.assertFalse(WrenchSyncLog.objects.exists())
        self.assertEqual(list(WrenchConfig.objects.values()), before)

    def test_configuration_capabilities_and_explicit_absence(self):
        result = self.client.get(BASE + 'config/')
        self.assertEqual(result.status_code, 200)
        self.assertIs(result.data['configured'], True)
        self.assertEqual(result.data['sync_capabilities'], service.SYNC_CAPABILITIES)
        self.assertNotIn('session_token', result.data['config'])
        self.config.is_active = False
        self.config.save()
        absent = self.client.get(BASE + 'config/')
        self.assertIs(absent.data['configured'], False)
        self.assertIsNone(absent.data['config'])
        self.assertEqual(self.trigger().status_code, 424)
        self.assertFalse(WrenchSyncLog.objects.exists())

    def test_document_preserves_genuine_retrieval(self):
        with patch.object(service.requests, 'post', return_value=response(document_payload())) as post:
            result = self.trigger('document')
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data['status'], 'success')
        self.assertEqual(result.data['records_synced'], 1)
        self.assertIs(result.data['sync_details']['retrieval_validated'], True)
        self.assertEqual(result.data['sync_details']['effect'], 'metadata_retrieval')
        self.assertEqual(result.data['sync_details']['canonical_records_imported'], 0)
        self.assertEqual(post.call_count, 1)

    def test_existing_internal_alias_is_not_a_new_public_api_operation(self):
        with patch.object(service.requests, 'post', return_value=response(document_payload())) as post:
            rejected = self.trigger('doc_search')
            self.assertEqual(rejected.status_code, 400)
            post.assert_not_called()
            self.assertFalse(WrenchSyncLog.objects.exists())
            log = service.run_sync('wrench_to_radai', 'doc_search', self.user)
        self.assertEqual(log.status, 'success')
        self.assertEqual(log.records_synced, 1)
        self.assertIs(log.sync_details['retrieval_validated'], True)
        self.assertEqual(post.call_count, 1)

    def test_explicit_empty_metadata_collection_is_genuine_zero(self):
        with patch.object(service.requests, 'post', return_value=response(document_payload(0))):
            result = self.trigger()
        self.assertEqual(result.data['status'], 'success')
        self.assertEqual(result.data['records_synced'], 0)
        self.assertIs(result.data['sync_details']['retrieval_validated'], True)

    def test_transmittal_page_is_partial_without_invented_failures_or_pending_work(self):
        payload = {'DataList': {'TRANSMITTAL_LIST': [
            [{'FieldName': 'ORDER_NO', 'Value': f'SYNTHETIC-{i}'}] for i in range(51)
        ]}, 'ProcessDetails': [{'ProcessStatus': 0}]}
        with patch.object(service.requests, 'post', return_value=response(payload)):
            result = self.trigger('transmittal')
        self.assertEqual(result.data['status'], 'partial')
        self.assertEqual(result.data['records_requested'], 51)
        self.assertEqual(result.data['records_synced'], 50)
        self.assertEqual(result.data['records_failed'], 0)
        self.assertEqual(result.data['sync_details']['remaining_available'], 1)
        self.assertIn('no further work is queued', result.data['sync_details']['message'])

    def test_transmittal_complete_page_is_success(self):
        payload = {'DataList': {'TRANSMITTAL_LIST': [[{'FieldName': 'ORDER_NO', 'Value': 'SYNTHETIC-1'}]]}}
        with patch.object(service.requests, 'post', return_value=response(payload)):
            result = self.trigger('transmittal')
        self.assertEqual(result.data['status'], 'success')
        self.assertEqual(result.data['records_synced'], 1)

    def test_success_shaped_and_explicit_error_responses_fail_closed(self):
        payloads = [
            {'success': True}, {'OperationStatus': 0, 'TotalSearchResultCount': 0},
            {**document_payload(), 'ErrorMsg': 'SENSITIVE DO NOT RECORD'},
            {**document_payload(), 'success': False}, {**document_payload(), 'status': 'pending'},
            {**document_payload(), 'TotalSearchResultCount': -1},
            {**document_payload(), 'ObjectSearchResults': [{}]},
            {**document_payload(), 'ObjectSearchResults': [[{'Value': 'no field name'}]]},
            document_payload(0, total=2),
        ]
        for payload in payloads:
            with self.subTest(payload=payload), patch.object(service.requests, 'post', return_value=response(payload)) as post:
                result = self.trigger()
            self.assert_failed(result)
            self.assertEqual(post.call_count, 1)
            self.assertNotIn('SENSITIVE DO NOT RECORD', str(result.data))

    def test_adapter_cannot_turn_missing_or_failed_evidence_into_success(self):
        payloads = [
            {'total': 0, 'success': True}, {'total': 1, 'documents': []},
            {'total': True, 'documents': []}, {'total': 1, 'documents': [{}]},
            {'total': 1, 'documents': [{'DOC_NO': 'SYNTHETIC'}], 'error_msg': 'failure'},
        ]
        for payload in payloads:
            with self.subTest(payload=payload), patch.object(service, 'search_documents', return_value=payload):
                result = self.trigger()
            self.assert_failed(result)

    def test_transient_failure_retries_at_most_three_times_and_preserves_config(self):
        before = deepcopy(list(WrenchConfig.objects.values()))
        with patch.object(service.requests, 'post', side_effect=requests.exceptions.Timeout('synthetic')) as post:
            result = self.trigger()
        self.assert_failed(result)
        self.assertEqual(post.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)
        self.assertEqual(result.data['sync_details']['attempts'], 3)
        self.assertEqual(list(WrenchConfig.objects.values()), before)

    def test_transient_read_can_recover_without_duplicate_logs(self):
        with patch.object(service.requests, 'post', side_effect=[requests.exceptions.Timeout(), response(document_payload())]) as post:
            result = self.trigger()
        self.assertEqual(result.data['status'], 'success')
        self.assertEqual(post.call_count, 2)
        self.assertEqual(result.data['sync_details']['attempts'], 2)
        self.assertEqual(WrenchSyncLog.objects.count(), 1)

    def test_authorization_failure_stops_immediately_without_fallback_or_retry(self):
        for code in (401, 403):
            with self.subTest(code=code), patch.object(service.requests, 'post', return_value=response({}, code)) as post:
                result = self.trigger()
            self.assert_failed(result)
            self.assertEqual(post.call_count, 1)
            self.assertIs(result.data['sync_details']['retryable'], False)
        self.sleep.assert_not_called()

    def test_retryable_http_failures_are_bounded_and_permanent_errors_are_not_retried(self):
        for code, expected_attempts in ((429, 3), (503, 3), (400, 1), (422, 1)):
            with self.subTest(code=code), patch.object(service.requests, 'post', return_value=response({}, code)) as post:
                result = self.trigger('transmittal')
            self.assert_failed(result)
            self.assertEqual(post.call_count, expected_attempts)

    def test_malformed_transmittal_error_payload_does_not_become_empty_success(self):
        for payload in ({'success': True}, {'DataList': {}}, {'DataList': {'TRANSMITTAL_LIST': []}, 'ErrorMsg': 'synthetic failure'}):
            with self.subTest(payload=payload), patch.object(service.requests, 'post', return_value=response(payload)):
                result = self.trigger('transmittal')
            self.assert_failed(result)

    def test_odata_authorization_failure_stops_discovery(self):
        with patch.object(service.requests, 'post', return_value=response({}, 404)) as post, patch.object(service.requests, 'get', return_value=response({}, 403)) as get:
            result = self.trigger()
        self.assert_failed(result)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(get.call_count, 1)
        self.sleep.assert_not_called()

    def test_odata_existing_success_path_validates_real_collection(self):
        with patch.object(service.requests, 'post', return_value=response({}, 404)), patch.object(service.requests, 'get', side_effect=[
            response({'d': {'EntitySets': ['Documents']}}),
            response({'d': {'results': [{'DOC_NO': 'SYNTHETIC-1'}], '__count': '1'}}),
        ]):
            result = self.trigger()
        self.assertEqual(result.data['status'], 'success')
        self.assertEqual(result.data['records_synced'], 1)

    def test_odata_malformed_collection_or_count_is_failed(self):
        for payload in ({'success': True}, {'d': {'results': [], '__count': 'invalid'}}, {'d': {'results': [], '__count': -2}}):
            with self.subTest(payload=payload), patch.object(service.requests, 'post', return_value=response({}, 404)), patch.object(service.requests, 'get', side_effect=[
                response({'d': {'EntitySets': ['Documents']}}), response(payload),
            ]):
                result = self.trigger()
            self.assert_failed(result)

    def test_historical_logs_survive_new_failed_attempt(self):
        historical = WrenchSyncLog.objects.create(config=self.config, status='success', direction='radai_to_wrench', entity_type='all', sync_details={'note': 'Historical evidence'})
        before = deepcopy(WrenchSyncLog.objects.filter(pk=historical.pk).values().get())
        with patch.object(service.requests, 'post', return_value=response({'success': True})):
            result = self.trigger()
        self.assert_failed(result)
        self.assertEqual(WrenchSyncLog.objects.filter(pk=historical.pk).values().get(), before)

    def test_existing_async_export_acceptance_remains_pending_not_completed(self):
        with patch('apps.wrench_integration.tasks.wrench_s3_batch_export.apply_async', return_value=SimpleNamespace(id='synthetic-task-id')) as dispatch:
            result = self.client.post(BASE + 's3-sync/start/', {'mode': 'batch', 'entity_type': 'documents'}, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data['status'], 'pending')
        self.assertIsNone(result.data['completed_at'])
        self.assertEqual(result.data['records_exported'], 0)
        self.assertEqual(WrenchS3SyncJob.objects.count(), 1)
        dispatch.assert_called_once()
