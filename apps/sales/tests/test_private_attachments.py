"""Private opportunity attachments: real temporary files, synthetic scoped users."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.rbac.models import RolePermission
from apps.rbac.route_guard import ModuleActionGuardMixin
from apps.sales.attachment_storage import attachment_storage
from apps.sales.models import Deal, OpportunityAuditEvent, OpportunityWorkspaceUpload
from apps.sales.private_attachments import upload_private_file
from apps.sales.tests.test_opportunity_workspace import CONFIG, WorkspaceFixtures
from apps.sales.views import DealViewSet


class PrivateFixtures(WorkspaceFixtures):
    def setUp(self):
        super().setUp()
        self.private_root = TemporaryDirectory(prefix='radai-private-attachment-tests-')
        self.addCleanup(self.private_root.cleanup)
        settings = override_settings(SALES_WORKSPACE_ENABLED=False, SALES_ATTACHMENT_ROOT=self.private_root.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.upload_url = self.url + 'folders/proposal/upload/'

    def upload(self, request_id=None, name='Scope.pdf', content=b'synthetic attachment', **extra):
        return self.api.post(self.upload_url, {
            'storage': 'radai', 'upload_request_id': str(request_id or uuid4()),
            'file': SimpleUploadedFile(name, content, content_type='application/pdf'), **extra,
        }, format='multipart')

    def file_url(self, result):
        return self.url + 'folders/proposal/files/' + result.data['id'] + '/'


@override_settings(**CONFIG)
class PrivateAttachmentTests(PrivateFixtures, TestCase):
    def test_real_upload_list_metadata_version_download_without_graph_configuration(self):
        with patch('apps.sales.opportunity_workspace.WorkspaceGraph', side_effect=AssertionError('No Graph access')):
            before = self.api.get(self.url)
            self.assertEqual(before.data['status'], 'not_configured')
            self.assertTrue(before.data['radai_storage']['can_upload'])
            self.assertTrue(all(row['item_count'] == 0 for row in before.data['radai_storage']['folders']))
            result = self.upload()
            self.assertEqual(result.status_code, 201, result.data)
            self.assertEqual(result.data['storage_provider'], 'radai')
            self.assertIsNone(result.data['web_url'])
            listing = self.api.get(self.url + 'folders/proposal/files/', {'storage': 'radai'})
            self.assertEqual(listing.data['item_count'], 1)
            self.assertEqual(listing.data['files'][0]['id'], result.data['id'])
            url = self.file_url(result)
            detail = self.api.get(url)
            self.assertTrue(detail.data['can_download'])
            self.assertEqual(detail.data['version'], '1')
            self.assertEqual(self.api.get(url + 'versions/').data['versions'][0]['id'], '1')
            download = self.api.get(url + 'download/')
            self.assertEqual(b''.join(download.streaming_content), b'synthetic attachment')
            self.assertIn('Scope.pdf', download['Content-Disposition'])
            self.assertEqual(download['Cache-Control'], 'no-store, private')
            state = self.api.get(self.url).data['radai_storage']
            self.assertEqual(next(row['item_count'] for row in state['folders'] if row['key'] == 'proposal'), 1)
            self.assertEqual(len(list(Path(self.private_root.name).rglob('original'))), 1)
            self.assertNotIn('storage_name', str(detail.data))
            self.assertFalse(OpportunityAuditEvent.objects.filter(event_type='workspace_file_uploaded').exclude(data__storage_provider='radai').exists())

    def test_identical_retry_is_one_file_and_completed_audit(self):
        request_id = uuid4()
        first, replay = self.upload(request_id), self.upload(request_id)
        self.assertEqual((first.status_code, replay.status_code), (201, 200))
        self.assertEqual(first.data, replay.data)
        self.assertEqual(OpportunityWorkspaceUpload.objects.count(), 1)
        self.assertEqual(OpportunityAuditEvent.objects.filter(event_type='workspace_file_uploaded').count(), 1)
        self.assertEqual(len(list(Path(self.private_root.name).rglob('original'))), 1)

    def test_request_identity_rejects_changed_content_category_actor_and_provider(self):
        request_id = uuid4()
        self.assertEqual(self.upload(request_id).status_code, 201)
        self.assertEqual(self.upload(request_id, content=b'changed').status_code, 409)
        self.upload_url = self.url + 'folders/tender/upload/'
        self.assertEqual(self.upload(request_id).status_code, 409)
        self.upload_url = self.url + 'folders/proposal/upload/'
        attempt = OpportunityWorkspaceUpload.objects.get()
        attempt.provider = 'sharepoint'
        attempt.save(update_fields=['provider'])
        self.assertEqual(self.upload(request_id).status_code, 409)
        attempt.provider, attempt.actor = 'radai', self.other
        attempt.save(update_fields=['provider', 'actor'])
        self.assertEqual(self.upload(request_id).status_code, 409)

    def test_normalized_filename_conflict_never_replaces_existing_attachment(self):
        first = self.upload(name='Scope.pdf')
        self.assertEqual(self.upload(name='scope.PDF', content=b'other').status_code, 409)
        download = self.api.get(self.file_url(first) + 'download/')
        self.assertEqual(b''.join(download.streaming_content), b'synthetic attachment')

    def test_invalid_provider_category_and_file_rejected_before_storage(self):
        self.assertEqual(self.upload(storage='external').status_code, 400)
        self.assertEqual(self.upload(content=b'').status_code, 400)
        self.assertEqual(self.upload(name='Scope.pdf', content=b'x' * (10 * 1024 * 1024 + 1)).status_code, 400)
        self.upload_url = self.url + 'folders/foreign/upload/'
        self.assertEqual(self.upload().status_code, 400)
        self.assertEqual(self.api.get(self.url + 'folders/proposal/files/', {'storage': 'external'}).status_code, 400)
        self.assertFalse(OpportunityWorkspaceUpload.objects.exists())

    def test_private_file_id_and_folder_or_opportunity_cannot_escape_scope(self):
        result = self.upload()
        url = self.file_url(result)
        hidden = Deal.objects.create(deal_code='PRIVATE-HIDDEN', deal_name='Hidden', client=self.client_record, owner=self.other)
        for candidate in (url.replace('/proposal/', '/tender/'), url.replace(str(self.opportunity.pk), str(hidden.pk)),
                          url.replace(result.data['id'], 'radai-not-a-uuid'), url.replace(result.data['id'], 'radai-' + str(uuid4()))):
            for suffix in ('', 'versions/', 'download/'):
                self.assertEqual(self.api.get(candidate + suffix).status_code, 404)

    def test_upload_requires_read_create_update_and_download_requires_export(self):
        result = self.upload()
        for action in ('create', 'update', 'read'):
            with self.subTest(action=action), self.captureOnCommitCallbacks():
                permission = RolePermission.objects.filter(permission__module__code='sales_opportunities', permission__action=action).first()
                old_pk = permission.pk
                permission.delete()
                self.assertEqual(self.upload(name=action + '.pdf').status_code, 403)
                permission.pk = old_pk
                permission.save(force_insert=True)
        RolePermission.objects.filter(permission__module__code='sales_opportunities', permission__action='export').delete()
        self.assertFalse(self.api.get(self.file_url(result)).data['can_download'])
        self.assertEqual(self.api.get(self.file_url(result) + 'download/').status_code, 403)
        RolePermission.objects.filter(permission__module__code='sales_opportunities', permission__action='read').delete()
        for suffix in ('', 'versions/', 'download/'):
            self.assertEqual(self.api.get(self.file_url(result) + suffix).status_code, 403)

    def test_storage_failure_is_not_a_success_and_same_identity_can_retry(self):
        request_id = uuid4()
        storage, fingerprint = attachment_storage()
        with patch('apps.sales.private_attachments.attachment_storage', return_value=(storage, fingerprint)):
            with patch.object(storage, 'save', side_effect=OSError('sensitive storage connection')):
                failed = self.upload(request_id)
            self.assertEqual(failed.status_code, 424)
            self.assertNotIn('sensitive', str(failed.data))
            self.assertEqual(OpportunityWorkspaceUpload.objects.get().status, 'uncertain')
            self.assertEqual(self.api.get(self.url + 'folders/proposal/files/', {'storage': 'radai'}).data['item_count'], 0)
            self.assertEqual(self.upload(request_id).status_code, 201)
        self.assertEqual(len(list(Path(self.private_root.name).rglob('original'))), 1)

    def test_audit_rollback_retains_object_identity_and_retry_does_not_duplicate_bytes(self):
        from apps.sales.private_attachments import _audit
        request_id = uuid4()
        def fail_final(opportunity, actor, kind, **kwargs):
            if kind == 'workspace_file_uploaded':
                raise RuntimeError('Synthetic commit failure')
            return _audit(opportunity, actor, kind, **kwargs)
        with patch('apps.sales.private_attachments._audit', side_effect=fail_final):
            self.assertEqual(self.upload(request_id).status_code, 424)
        attempt = OpportunityWorkspaceUpload.objects.get()
        self.assertEqual(attempt.status, 'uncertain')
        self.assertFalse(OpportunityAuditEvent.objects.filter(event_type='workspace_file_uploaded').exists())
        self.assertEqual(len(list(Path(self.private_root.name).rglob('original'))), 1)
        storage, fingerprint = attachment_storage()
        with patch('apps.sales.private_attachments.attachment_storage', return_value=(storage, fingerprint)), patch.object(storage, 'save', side_effect=AssertionError('Must recover existing bytes')):
            self.assertEqual(self.upload(request_id).status_code, 201)

    def test_download_checks_content_integrity_missing_objects_and_storage_identity(self):
        result = self.upload()
        url = self.file_url(result)
        attempt = OpportunityWorkspaceUpload.objects.get()
        path = Path(self.private_root.name) / attempt.storage_name
        path.write_bytes(b'changed')
        self.assertEqual(self.api.get(url + 'download/').data['code'], 'attachment_integrity_failed')
        path.unlink()
        self.assertEqual(self.api.get(url + 'download/').status_code, 404)
        with override_settings(SALES_ATTACHMENT_ROOT=self.private_root.name + '-changed'):
            self.assertEqual(self.api.get(url).data['code'], 'private_storage_changed')

    def test_deleted_opportunity_and_parent_cannot_orphan_private_attachments(self):
        self.upload()
        self.assertEqual(self.api.delete(f'/api/v1/sales/deals/{self.opportunity.pk}/').status_code, 409)
        self.assertEqual(self.api.delete(f'/api/v1/sales/clients/{self.client_record.pk}/').status_code, 409)

    def test_local_cursor_rejects_forged_remote_cursor_and_changed_storage(self):
        self.upload()
        response = self.api.get(self.url + 'folders/proposal/files/', {'storage': 'radai', 'cursor': 'https://attacker.test'})
        self.assertEqual(response.status_code, 400)
        from django.core import signing
        _, fingerprint = attachment_storage()
        cursor = signing.dumps({'opportunity': str(self.opportunity.pk), 'folder': 'tender', 'provider': 'radai',
                                'storage': fingerprint, 'after': str(uuid4())}, salt='sales-private-attachments')
        self.assertEqual(self.api.get(self.url + 'folders/proposal/files/', {'storage': 'radai', 'cursor': cursor}).status_code, 400)

    def test_public_media_destination_is_unavailable_and_never_written(self):
        with override_settings(SALES_ATTACHMENT_ROOT=self.private_root.name, MEDIA_ROOT=self.private_root.name):
            self.assertEqual(self.api.get(self.url).data['radai_storage']['status'], 'unavailable')
            self.assertEqual(self.upload().status_code, 424)
        self.assertFalse(OpportunityWorkspaceUpload.objects.exists())

    def test_download_rechecks_permission_after_private_bytes_are_read(self):
        from apps.sales.private_attachments import _read
        result = self.upload()
        captured = []
        def read_then_revoke(*args):
            output = _read(*args)
            captured.append(output)
            RolePermission.objects.filter(permission__module__code='sales_opportunities', permission__action='export').delete()
            return output
        with patch('apps.sales.private_attachments._read', side_effect=read_then_revoke):
            response = self.api.get(self.file_url(result) + 'download/')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.streaming)
        self.assertTrue(captured[0].closed)

    def test_final_upload_rejects_changed_request_identity_without_clobbering_reconciliation(self):
        from apps.sales.private_attachments import _read
        def read_then_reconcile(*args):
            output = _read(*args)
            OpportunityWorkspaceUpload.objects.filter(pk=args[0].pk).update(status='uncertain', sha256='0' * 64)
            return output
        with patch('apps.sales.private_attachments._read', side_effect=read_then_reconcile):
            response = self.upload()
        self.assertEqual(response.status_code, 424)
        self.assertFalse(OpportunityAuditEvent.objects.filter(event_type='workspace_file_uploaded').exists())
        attempt = OpportunityWorkspaceUpload.objects.get()
        self.assertEqual((attempt.status, attempt.sha256), ('uncertain', '0' * 64))

    def test_private_pagination_cursor_is_bound_and_expires(self):
        self.upload()
        original = OpportunityWorkspaceUpload.objects.get()
        OpportunityWorkspaceUpload.objects.bulk_create([
            OpportunityWorkspaceUpload(workspace=original.workspace, actor=self.actor, request_id=uuid4(),
                provider='radai', folder_key='proposal', name=f'Synthetic-{index}.pdf', normalized_name=f'synthetic-{index}.pdf',
                size=1, sha256='1' * 64, status='ready', storage_fingerprint=original.storage_fingerprint)
            for index in range(102)
        ])
        url = self.url + 'folders/proposal/files/'
        first = self.api.get(url, {'storage': 'radai'})
        self.assertEqual(len(first.data['files']), 100)
        self.assertEqual(first.data['item_count'], 103)
        second = self.api.get(url, {'storage': 'radai', 'cursor': first.data['next_cursor']})
        self.assertEqual(len(second.data['files']), 3)
        self.assertFalse(set(row['id'] for row in first.data['files']) & set(row['id'] for row in second.data['files']))
        with patch('django.core.signing.time.time', return_value=9999999999):
            self.assertEqual(self.api.get(url, {'storage': 'radai', 'cursor': first.data['next_cursor']}).status_code, 400)
        with override_settings(SALES_ATTACHMENT_ROOT=self.private_root.name + '-changed'):
            self.assertEqual(self.api.get(url, {'storage': 'radai', 'cursor': first.data['next_cursor']}).status_code, 400)


class PrivateStorageBoundaryTests(SimpleTestCase):
    def test_public_unknown_object_storage_rejected(self):
        for storage in (SimpleNamespace(), SimpleNamespace(default_acl='public-read', querystring_auth=True, object_parameters={}),
                        SimpleNamespace(default_acl='private', querystring_auth=False, object_parameters={})):
            with patch('apps.sales.attachment_storage.default_storage', storage), self.assertRaises(OSError):
                attachment_storage()


@override_settings(**CONFIG)
class PrivateAttachmentDurabilityTests(PrivateFixtures, TransactionTestCase):
    def test_guarded_upload_commits_its_identity_before_private_file_io(self):
        storage, fingerprint = attachment_storage()
        save = storage.save
        def assert_committed(name, content, **kwargs):
            self.assertFalse(connection.in_atomic_block)
            self.assertEqual(OpportunityWorkspaceUpload.objects.get().status, 'uploading')
            return save(name, content, **kwargs)
        guarded = type('DealViewSet', (ModuleActionGuardMixin, DealViewSet), {'__module__': 'apps.sales.views'})
        callback = guarded.as_view({'post': 'workspace_upload'})
        request = APIRequestFactory().post(self.upload_url, {'storage': 'radai', 'upload_request_id': str(uuid4()),
                                          'file': SimpleUploadedFile('Scope.pdf', b'synthetic bytes')}, format='multipart')
        force_authenticate(request, self.actor)
        with patch('apps.sales.private_attachments.attachment_storage', return_value=(storage, fingerprint)), patch.object(storage, 'save', side_effect=assert_committed):
            response = callback(request, pk=self.opportunity.pk, folder_key='proposal')
        self.assertEqual(response.status_code, 201, response.data)

    @skipUnless(connection.vendor == 'postgresql', 'PostgreSQL row-lock verification')
    def test_competing_uploads_reserve_one_name_without_duplicate_objects(self):
        barrier = Barrier(2)
        def run():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                from apps.sales.opportunity_workspace import WorkspaceAPIError
                try:
                    upload_private_file(self.opportunity, self.actor, 'proposal', SimpleUploadedFile('Scope.pdf', b'same bytes'), uuid4())
                    return 201
                except WorkspaceAPIError as exc:
                    return exc.status_code
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = sorted(future.result(timeout=25) for future in [pool.submit(run), pool.submit(run)])
        self.assertEqual(statuses, [201, 409])
        self.assertEqual(OpportunityWorkspaceUpload.objects.count(), 1)
        self.assertEqual(len(list(Path(self.private_root.name).rglob('original'))), 1)
