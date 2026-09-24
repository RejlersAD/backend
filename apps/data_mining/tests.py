"""Synthetic F01 regressions through the actual router and module/action guard."""
from copy import deepcopy
import io
import json
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import patch
import uuid

import pandas as pd
from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.test import TestCase, override_settings
from django.urls import include, path, resolve
from django.views.static import serve as serve_static_media
from rest_framework.test import APIClient

from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, RolePermission, UserProfile, UserRole
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import ModuleActionGuardMixin, secure_module_endpoints

from .models import DataMiningDocument, DataMiningProject, TransformationPipeline, TransformationStep
from .storage import master_storage


def public_media(request, path):
    # Same anonymous file-serving behavior as config.urls, using test-only roots.
    return serve_static_media(request, path, document_root=settings.MEDIA_ROOT)


urlpatterns = [
    path('api/v1/data-mining/', include('apps.data_mining.urls')),
    path('media/<path:path>', public_media),
]
secure_module_endpoints(urlpatterns)
BASE = '/api/v1/data-mining/projects/'


@override_settings(ROOT_URLCONF=__name__)
class DataMiningTruthfulOutputAPITests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        directory = TemporaryDirectory(prefix='radai-data-mining-tests-')
        self.addCleanup(directory.cleanup)
        self.storage = FileSystemStorage(location=directory.name)
        storage_patch = patch('apps.data_mining.views.master_storage', return_value=self.storage)
        storage_patch.start()
        self.addCleanup(storage_patch.stop)
        self.owner = get_user_model().objects.create_user('mining-owner', email='owner@mining.example.test')
        self.other = get_user_model().objects.create_user('mining-other', email='other@mining.example.test')
        self.role = Role.objects.create(name='Synthetic mining operator', code='mining-test-operator', level=4)
        self.module, _ = Module.objects.get_or_create(code='data_mining', defaults={'name': 'Data Mining'})
        ensure_module_actions(Module, Permission, module_ids=[self.module.pk])
        RoleModule.objects.create(role=self.role, module=self.module)
        for permission in self.module.permissions.filter(action__in=['read', 'create', 'update', 'export'], is_active=True):
            RolePermission.objects.create(role=self.role, permission=permission)
        organization = Organization.objects.create(name='Synthetic mining organization', code='MINING-TEST')
        for user in (self.owner, self.other):
            profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'status': 'active', 'organization': organization})
            UserRole.objects.create(user_profile=profile, role=self.role)
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.project = DataMiningProject.objects.create(
            name='Synthetic preserved source selection', created_by=self.owner,
            wrench_project_number='TEST-SOURCE', wrench_project_name='Synthetic project',
            description='Keep this configuration', master_file_format='csv', status='configuring',
        )
        self.document = DataMiningDocument.objects.create(
            project=self.project, wrench_doc_number='SYNTHETIC-001', wrench_doc_revision='B',
            wrench_transmittal_id='SYNTHETIC-TR', extraction_status='completed',
            extracted_data={'columns': ['Item', 'Quantity'], 'rows': [['TEST-ONLY', 3], ['SECOND', 5]]},
            row_count=2, column_count=2,
        )
        self.pipeline = TransformationPipeline.objects.create(
            project=self.project, canvas_config={'nodes': [{'id': 'preserve-position', 'x': 80}]},
            execution_log='Existing historical log remains.',
        )
        self.step = TransformationStep.objects.create(
            pipeline=self.pipeline, step_name='Selected existing data', operation_type='select',
            config={'columns': ['Item', 'Quantity']}, input_source=str(self.document.pk),
        )

    def url(self, action='', project=None):
        return f'{BASE}{(project or self.project).pk}/' + (f'{action}/' if action else '')

    def snapshot(self):
        return deepcopy({
            'project': list(DataMiningProject.objects.values()),
            'documents': list(DataMiningDocument.objects.values()),
            'pipelines': list(TransformationPipeline.objects.values()),
            'steps': list(TransformationStep.objects.values()),
        })

    def deny(self, action):
        RolePermission.objects.filter(role=self.role, permission__module=self.module, permission__action=action).delete()

    def stored_export(self, content=b'Item,Quantity\nPRIOR,2\n'):
        key = f'data-mining/{self.project.pk}/exports/{uuid.uuid4().hex}.csv'
        self.storage.save(key, ContentFile(content))
        self.project.master_file_path = key
        self.project.status = 'completed'
        self.project.total_rows_processed = 1
        self.project.save()
        return key

    def assert_failure_unchanged(self, response, expected_status, before):
        self.assertEqual(response.status_code, expected_status, getattr(response, 'data', None))
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn('master_file', response.data)
        self.assertNotIn('download_url', response.data)
        self.assertNotIn('artifact_available', response.data)

    def test_actual_routes_are_guarded_and_extraction_is_explicitly_unavailable_without_writes(self):
        self.assertTrue(issubclass(resolve(self.url('extract_data')).func.cls, ModuleActionGuardMixin))
        self.document.extraction_status = 'pending'
        self.document.extracted_data = None
        self.document.save()
        before = self.snapshot()
        for _ in range(2):
            response = self.client.post(self.url('extract_data'), {}, format='json')
            self.assert_failure_unchanged(response, 503, before)
            self.assertEqual(response.data['code'], 'extraction_unavailable')
        self.assertEqual(list(Path(self.storage.location).rglob('*')), [])

    def test_unavailable_extraction_preserves_existing_results_and_historical_artifact(self):
        key = self.stored_export()
        before = self.snapshot()
        response = self.client.post(self.url('extract_data'), {}, format='json')
        self.assert_failure_unchanged(response, 503, before)
        self.assertTrue(self.storage.exists(key))

    def test_execution_rejects_partial_or_missing_selected_source_data(self):
        for extraction_status, extracted_data in [('pending', None), ('failed', None), ('completed', None)]:
            with self.subTest(extraction_status=extraction_status):
                extra = DataMiningDocument.objects.create(
                    project=self.project, wrench_doc_number='SYNTHETIC-PENDING',
                    extraction_status=extraction_status, extracted_data=extracted_data,
                )
                before = self.snapshot()
                with patch.object(self.storage, 'save') as save:
                    response = self.client.post(self.url('execute_pipeline'), {}, format='json')
                self.assert_failure_unchanged(response, 503, before)
                self.assertEqual(response.data['code'], 'extraction_unavailable')
                save.assert_not_called()
                extra.delete()

    def test_existing_transformation_produces_real_csv_and_authorized_download(self):
        prior_key = self.stored_export()
        before = self.snapshot()
        # Exercise a previous step DataFrame as input, without ambiguous bool coercion.
        second = TransformationStep.objects.create(
            pipeline=self.pipeline, step_name='Rename actual output', operation_type='rename',
            config={'column_mapping': {'Quantity': 'Count'}}, input_source=str(self.step.pk), sequence_order=1,
        )
        response = self.client.post(self.url('execute_pipeline'), {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'completed')
        self.assertIs(response.data['artifact_available'], True)
        self.assertEqual(response.data['preview'], {'columns': ['Item', 'Count'], 'rows': [['TEST-ONLY', 3], ['SECOND', 5]]})
        self.project.refresh_from_db()
        self.assertNotEqual(self.project.master_file_path, prior_key)
        self.assertTrue(self.storage.exists(prior_key))
        download = self.client.get(self.url('download_master'))
        self.assertEqual(download.status_code, 200)
        content = b''.join(download.streaming_content)
        download.close()
        actual = pd.read_csv(io.BytesIO(content))
        self.assertEqual(actual.to_dict('records'), [{'Item': 'TEST-ONLY', 'Count': 3}, {'Item': 'SECOND', 'Count': 5}])
        self.assertIn('attachment;', download['Content-Disposition'])
        self.assertEqual(download['Cache-Control'], 'private, no-store')
        self.pipeline.refresh_from_db()
        self.assertTrue(self.pipeline.execution_log.startswith('Existing historical log remains.\n'))
        history = json.loads(self.pipeline.execution_log.splitlines()[-1])
        self.assertEqual(history['previous_result']['master_file_path'], prior_key)
        self.assertEqual(history['source_documents'][0]['id'], str(self.document.pk))
        self.assertEqual(history['previous_result']['steps'][0]['status'], 'pending')
        self.assertEqual(self.snapshot()['documents'], before['documents'])
        self.step.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(self.step.config, {'columns': ['Item', 'Quantity']})
        self.assertEqual(second.status, 'completed')

    def test_excel_artifact_contains_actual_prepared_input(self):
        self.project.master_file_format = 'excel'
        self.project.save()
        response = self.client.post(self.url('execute_pipeline'), {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['filename'], 'master.xlsx')
        download = self.client.get(self.url('download_master'))
        actual = pd.read_excel(io.BytesIO(b''.join(download.streaming_content)))
        download.close()
        self.assertEqual(actual.to_dict('records'), [{'Item': 'TEST-ONLY', 'Quantity': 3}, {'Item': 'SECOND', 'Quantity': 5}])

    def test_json_artifact_contains_actual_prepared_input(self):
        self.project.master_file_format = 'json'
        self.project.save()
        response = self.client.post(self.url('execute_pipeline'), {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        download = self.client.get(self.url('download_master'))
        self.assertEqual(json.loads(b''.join(download.streaming_content)), [
            {'Item': 'TEST-ONLY', 'Quantity': 3}, {'Item': 'SECOND', 'Quantity': 5},
        ])
        download.close()

    def test_excel_preserves_formula_like_source_text_as_literal_cells(self):
        from openpyxl import load_workbook
        self.project.master_file_format = 'excel'
        self.project.save()
        self.document.extracted_data = {
            'columns': ['=HEADER', 'Value'],
            'rows': [['=1+1', '=HYPERLINK("https://example.test")'], ['#N/A', 42]],
        }
        self.document.save()
        self.step.config = {'columns': ['=HEADER', 'Value']}
        self.step.save()
        response = self.client.post(self.url('execute_pipeline'), {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        download = self.client.get(self.url('download_master'))
        workbook = load_workbook(io.BytesIO(b''.join(download.streaming_content)), data_only=False)
        download.close()
        sheet = workbook.active
        for position, expected in {
            'A1': '=HEADER', 'A2': '=1+1',
            'B2': '=HYPERLINK("https://example.test")', 'A3': '#N/A',
        }.items():
            self.assertEqual(sheet[position].value, expected)
            self.assertEqual(sheet[position].data_type, 's')
        self.assertEqual(sheet['B3'].value, 42)
        workbook.close()

    def test_parquet_uses_real_writer_or_fails_without_changing_results(self):
        from importlib.util import find_spec
        self.project.master_file_format = 'parquet'
        self.project.save()
        before = self.snapshot()
        response = self.client.post(self.url('execute_pipeline'), {}, format='json')
        if not (find_spec('pyarrow') or find_spec('fastparquet')):
            self.assert_failure_unchanged(response, 500, before)
            self.assertEqual(response.data['code'], 'pipeline_execution_failed')
            return
        self.assertEqual(response.status_code, 200, response.data)
        download = self.client.get(self.url('download_master'))
        actual = pd.read_parquet(io.BytesIO(b''.join(download.streaming_content)))
        download.close()
        self.assertEqual(actual.to_dict('records'), [{'Item': 'TEST-ONLY', 'Quantity': 3}, {'Item': 'SECOND', 'Quantity': 5}])

    def test_failed_storage_preserves_all_records_and_prior_artifact(self):
        key = self.stored_export()
        before = self.snapshot()
        with patch.object(self.storage, 'save', side_effect=OSError('private storage detail')):
            response = self.client.post(self.url('execute_pipeline'), {}, format='json')
        self.assert_failure_unchanged(response, 503, before)
        self.assertEqual(response.data['code'], 'artifact_storage_unavailable')
        self.assertNotIn('private storage detail', str(response.data))
        self.assertTrue(self.storage.exists(key))

    def test_storage_false_success_or_mismatched_bytes_cannot_complete(self):
        before = self.snapshot()
        with patch.object(self.storage, 'exists', return_value=False):
            response = self.client.post(self.url('execute_pipeline'), {}, format='json')
        self.assert_failure_unchanged(response, 503, before)
        with patch.object(self.storage, 'open', return_value=io.BytesIO(b'incorrect bytes')):
            response = self.client.post(self.url('execute_pipeline'), {}, format='json')
        self.assert_failure_unchanged(response, 503, before)

    def test_storage_open_failure_cannot_publish_success(self):
        before = self.snapshot()
        with patch.object(self.storage, 'open', side_effect=OSError('test-only unavailable')):
            response = self.client.post(self.url('execute_pipeline'), {}, format='json')
        self.assert_failure_unchanged(response, 503, before)

    def test_transformation_failure_preserves_previous_results_and_configuration(self):
        self.stored_export()
        self.step.operation_type = 'unsupported-test-operation'
        self.step.status = 'completed'
        self.step.output_preview = {'columns': ['Prior'], 'rows': [['historical']]}
        self.step.save()
        before = self.snapshot()
        with patch.object(self.storage, 'save') as save:
            response = self.client.post(self.url('execute_pipeline'), {}, format='json')
        self.assert_failure_unchanged(response, 500, before)
        self.assertEqual(response.data['code'], 'pipeline_execution_failed')
        save.assert_not_called()

    def test_unavailable_serialization_dependency_preserves_previous_results(self):
        self.stored_export()
        self.project.master_file_format = 'parquet'
        self.project.save()
        before = self.snapshot()
        with patch('apps.data_mining.views.serialize_master', side_effect=ImportError('Synthetic missing writer')), patch.object(self.storage, 'save') as save:
            response = self.client.post(self.url('execute_pipeline'), {}, format='json')
        self.assert_failure_unchanged(response, 500, before)
        self.assertEqual(response.data['code'], 'pipeline_execution_failed')
        save.assert_not_called()

    def test_metadata_failure_rolls_back_all_result_updates(self):
        self.stored_export()
        before = self.snapshot()
        with patch.object(TransformationPipeline, 'save', side_effect=RuntimeError('metadata save failed')):
            response = self.client.post(self.url('execute_pipeline'), {}, format='json')
        self.assert_failure_unchanged(response, 500, before)

    def test_create_denial_blocks_both_operational_actions_before_storage(self):
        self.deny('create')
        before = self.snapshot()
        with patch.object(self.storage, 'save') as save:
            for action in ('extract_data', 'execute_pipeline'):
                response = self.client.post(self.url(action), {}, format='json')
                self.assert_failure_unchanged(response, 403, before)
        save.assert_not_called()

    def test_export_denial_blocks_execution_and_download(self):
        self.stored_export()
        self.deny('export')
        before = self.snapshot()
        with patch.object(self.storage, 'open') as opened, patch.object(self.storage, 'save') as save:
            self.assert_failure_unchanged(self.client.post(self.url('execute_pipeline'), {}, format='json'), 403, before)
            self.assertEqual(self.client.get(self.url('download_master')).status_code, 403)
        opened.assert_not_called()
        save.assert_not_called()

    def test_wrong_owner_cannot_extract_execute_or_download(self):
        self.stored_export()
        self.client.force_authenticate(self.other)
        before = self.snapshot()
        with patch.object(self.storage, 'open') as opened, patch.object(self.storage, 'save') as save:
            for action in ('extract_data', 'execute_pipeline'):
                self.assert_failure_unchanged(self.client.post(self.url(action), {}, format='json'), 404, before)
            self.assertEqual(self.client.get(self.url('download_master')).status_code, 404)
        opened.assert_not_called()
        save.assert_not_called()

    def test_anonymous_actions_and_download_require_authentication(self):
        self.client.force_authenticate(None)
        before = self.snapshot()
        for action in ('extract_data', 'execute_pipeline'):
            response = self.client.post(self.url(action), {}, format='json')
            self.assertIn(response.status_code, (401, 403))
        self.assertIn(self.client.get(self.url('download_master')).status_code, (401, 403))
        self.assertEqual(self.snapshot(), before)

    def test_download_does_not_trust_legacy_missing_cross_project_or_traversal_paths(self):
        for key in (
            '', f's3://data-mining/{self.project.pk}/master.csv',
            f'data-mining/{uuid.uuid4()}/exports/{uuid.uuid4().hex}.csv',
            f'data-mining/{self.project.pk}/exports/../../secret.csv',
            'https://example.test/private.csv',
            f'data-mining/{self.project.pk}/exports/{uuid.uuid4().hex}.csv',
        ):
            with self.subTest(key=key):
                self.project.master_file_path = key
                self.project.save()
                before = self.snapshot()
                response = self.client.get(self.url('download_master'))
                self.assert_failure_unchanged(response, 404, before)
                self.assertEqual(response.data['code'], 'artifact_unavailable')

    def test_download_storage_failure_is_explicit_and_does_not_change_history(self):
        self.stored_export()
        before = self.snapshot()
        with patch.object(self.storage, 'open', side_effect=OSError('private location unavailable')):
            response = self.client.get(self.url('download_master'))
        self.assert_failure_unchanged(response, 503, before)
        self.assertEqual(response.data['code'], 'artifact_storage_unavailable')
        self.assertNotIn('private location', str(response.data))

    def test_generic_edit_cannot_invent_completed_result_or_assign_arbitrary_artifact(self):
        response = self.client.patch(self.url(), {
            'name': 'Permitted configuration edit', 'status': 'completed',
            'master_file_path': 'private/other-project.csv', 'total_rows_processed': 999,
            'execution_time_seconds': 1, 'executed_at': '2026-09-24T00:00:00Z',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, 'Permitted configuration edit')
        self.assertEqual(self.project.status, 'configuring')
        self.assertEqual(self.project.master_file_path, '')
        self.assertEqual(self.project.total_rows_processed, 0)
        self.assertIsNone(self.project.executed_at)

    def test_download_rejects_stale_result_instead_of_serving_another_run(self):
        prior_key = self.stored_export()
        response = self.client.post(self.url('execute_pipeline'), {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        before = self.snapshot()
        with patch.object(self.storage, 'open') as opened:
            response = self.client.get(self.url('download_master'), {'expected_master_file': prior_key})
        self.assert_failure_unchanged(response, 409, before)
        self.assertEqual(response.data['code'], 'artifact_changed')
        opened.assert_not_called()

    def test_pipeline_history_and_previews_follow_project_owner_scope(self):
        base = '/api/v1/data-mining/pipelines/'
        self.client.force_authenticate(self.other)
        response = self.client.get(base)
        self.assertEqual(response.status_code, 200, response.data)
        rows = response.data.get('results', response.data) if isinstance(response.data, dict) else response.data
        self.assertEqual(rows, [])
        before = self.snapshot()
        self.assertEqual(self.client.get(f'{base}{self.pipeline.pk}/').status_code, 404)
        self.assertEqual(self.client.patch(f'{base}{self.pipeline.pk}/', {'execution_log': 'erase'}, format='json').status_code, 404)
        self.assertEqual(self.snapshot(), before)
        self.client.force_authenticate(self.owner)
        response = self.client.patch(f'{base}{self.pipeline.pk}/', {
            'name': 'Allowed configuration edit', 'execution_log': 'erase',
            'last_executed_at': '2026-09-24T00:00:00Z',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.pipeline.refresh_from_db()
        self.assertEqual(self.pipeline.name, 'Allowed configuration edit')
        self.assertEqual(self.pipeline.execution_log, 'Existing historical log remains.')
        self.assertIsNone(self.pipeline.last_executed_at)

    def test_private_local_artifact_cannot_be_fetched_from_anonymous_media_route(self):
        with TemporaryDirectory(prefix='radai-private-mining-') as temporary:
            base = Path(temporary) / 'application'
            public = Path(temporary) / 'public-media'
            with override_settings(BASE_DIR=base, MEDIA_ROOT=public):
                with patch('apps.data_mining.storage.default_storage', FileSystemStorage(location=public)):
                    private = master_storage()
                self.assertFalse(Path(private.location).is_relative_to(public))
                with patch('apps.data_mining.views.master_storage', return_value=private):
                    result = self.client.post(self.url('execute_pipeline'), {}, format='json')
                    self.assertEqual(result.status_code, 200, result.data)
                    key = result.data['master_file']
                    self.assertTrue(private.exists(key))
                    anonymous = APIClient()
                    self.assertEqual(anonymous.get(f'/media/{key}').status_code, 404)
                    self.assertIn(anonymous.get(self.url('download_master')).status_code, (401, 403))
                    download = self.client.get(self.url('download_master'), {'expected_master_file': key})
                    self.assertEqual(download.status_code, 200)
                    self.assertIn(b'TEST-ONLY', b''.join(download.streaming_content))
                    download.close()

    def test_storage_selection_rejects_public_remote_backend_and_public_local_root(self):
        for backend in (
            SimpleNamespace(default_acl='public-read', querystring_auth=True, object_parameters={}),
            SimpleNamespace(default_acl='private', querystring_auth=False, object_parameters={}),
            SimpleNamespace(default_acl='private', querystring_auth=True, object_parameters={'ACL': 'public-read'}),
        ):
            with patch('apps.data_mining.storage.default_storage', backend):
                with self.assertRaises(OSError):
                    master_storage()
        private_remote = SimpleNamespace(default_acl='private', querystring_auth=True, object_parameters={})
        with patch('apps.data_mining.storage.default_storage', private_remote):
            self.assertIs(master_storage(), private_remote)
        with override_settings(BASE_DIR=Path(self.storage.location), MEDIA_ROOT=Path(self.storage.location)):
            with patch('apps.data_mining.storage.default_storage', self.storage):
                with self.assertRaises(OSError):
                    master_storage()
