"""Guarded large-file uploads and exact lossless storage round trips."""
import gzip
import hashlib
from io import BytesIO
from pathlib import Path
import random
from unittest.mock import patch
from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import include, path
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.routers import DefaultRouter

from apps.rbac.route_guard import secure_module_endpoints
from apps.rbac.models import RolePermission
from apps.sales.attachment_streams import CHUNK_BYTES, prepare_upload
from apps.sales.models import OpportunityAuditEvent, OpportunityWorkspaceUpload
from apps.sales.tests.test_private_attachments import PrivateFixtures
from apps.sales.tests.test_opportunity_workspace import CONFIG, FakeGraph, WorkspaceFixtures
from apps.sales.views import DealViewSet
from apps.sales.workspace_graph import WorkspaceError, WorkspaceGraph, workspace_config


router = DefaultRouter()
router.register('deals', DealViewSet, basename='streamed-attachment-deals')
urlpatterns = [path('api/v1/sales/', include(router.urls))]
secure_module_endpoints(urlpatterns)


@override_settings(**CONFIG, ROOT_URLCONF=__name__, SALES_WORKSPACE_MAX_UPLOAD_BYTES=0)
class StreamedAttachmentTests(PrivateFixtures, TestCase):
    def downloaded(self, result):
        response = self.api.get(self.file_url(result) + 'download/')
        self.assertEqual(response.status_code, 200, getattr(response, 'data', None))
        return b''.join(response.streaming_content)

    def test_above_ten_mib_compresses_and_downloads_original_without_current_upload_cap(self):
        content = b'Lossless synthetic source\x00\xff\n' * 460000
        self.assertGreater(len(content), 10 * 1024 * 1024)
        state = self.api.get(self.url).data['radai_storage']
        self.assertIsNone(state['max_upload_bytes'])
        self.assertEqual(state['automatic_compression'], 'lossless_if_smaller')
        result = self.upload(name='Source.dwg', content=content)
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data['size'], len(content))
        self.assertEqual(result.data['storage_encoding'], 'gzip')
        self.assertLess(result.data['stored_size'], len(content))
        attempt = OpportunityWorkspaceUpload.objects.get()
        stored = (Path(self.private_root.name) / attempt.storage_name).read_bytes()
        self.assertEqual(gzip.decompress(stored), content)
        self.assertEqual(attempt.sha256, hashlib.sha256(content).hexdigest())
        self.assertEqual(attempt.stored_sha256, hashlib.sha256(stored).hexdigest())
        with override_settings(SALES_WORKSPACE_MAX_UPLOAD_BYTES=5, SALES_WORKSPACE_MAX_DOWNLOAD_BYTES=5):
            self.assertEqual(self.downloaded(result), content)
            self.assertTrue(self.api.get(self.file_url(result)).data['can_download'])

    def test_mixed_extensions_and_incompressible_content_remain_exact_originals(self):
        content = random.Random(12).randbytes(16384)
        for extension in ('zip', 'png', 'pdf', 'docx', 'dwg', 'bin', 'exe'):
            result = self.upload(name='Source.' + extension, content=content)
            self.assertEqual(result.status_code, 201, result.data)
            self.assertEqual(result.data['storage_encoding'], 'identity')
            self.assertEqual(result.data['stored_size'], len(content))
            self.assertEqual(self.downloaded(result), content)
        self.assertEqual(self.api.get(self.url + 'folders/proposal/files/', {'storage': 'radai'}).data['item_count'], 7)

    def test_legacy_uncompressed_row_remains_downloadable(self):
        content = b'legacy raw source' * 1000
        result = self.upload(content=content)
        attempt = OpportunityWorkspaceUpload.objects.get()
        (Path(self.private_root.name) / attempt.storage_name).write_bytes(content)
        OpportunityWorkspaceUpload.objects.filter(pk=attempt.pk).update(
            storage_encoding='identity', stored_size=None, stored_sha256='')
        self.assertEqual(self.downloaded(result), content)
        detail = self.api.get(self.file_url(result)).data
        self.assertEqual((detail['storage_encoding'], detail['stored_size']), ('identity', len(content)))

    def test_encoded_and_original_corruption_fail_before_any_download_bytes(self):
        result = self.upload(content=b'original source' * 10000)
        attempt = OpportunityWorkspaceUpload.objects.get()
        target = Path(self.private_root.name) / attempt.storage_name
        good = target.read_bytes()
        cases = [
            (good[:-5], {}),
            (good[:-5], {'stored_size': len(good) - 5, 'stored_sha256': hashlib.sha256(good[:-5]).hexdigest()}),
            (good, {'size': attempt.size - 1}),
            (good, {'sha256': '0' * 64}),
            (good, {'stored_sha256': ''}),
        ]
        for encoded, changed in cases:
            with self.subTest(changed=changed):
                target.write_bytes(encoded)
                OpportunityWorkspaceUpload.objects.filter(pk=attempt.pk).update(
                    size=attempt.size, sha256=attempt.sha256, stored_size=attempt.stored_size,
                    stored_sha256=attempt.stored_sha256)
                OpportunityWorkspaceUpload.objects.filter(pk=attempt.pk).update(**changed)
                response = self.api.get(self.file_url(result) + 'download/')
                self.assertEqual(response.status_code, 424, response.data)
                self.assertEqual(response.data['code'], 'attachment_integrity_failed')
                self.assertFalse(response.streaming)

    def test_compressed_audit_failure_retry_preserves_one_representation_and_completion(self):
        from apps.sales.private_attachments import _audit
        content, request_id = b'repeated source data' * 10000, uuid4()
        def audit(opportunity, actor, event, **kwargs):
            if event == 'workspace_file_uploaded':
                raise RuntimeError('Synthetic audit failure')
            return _audit(opportunity, actor, event, **kwargs)
        with patch('apps.sales.private_attachments._audit', side_effect=audit):
            self.assertEqual(self.upload(request_id, content=content).status_code, 424)
        attempt = OpportunityWorkspaceUpload.objects.get()
        representation = (attempt.storage_encoding, attempt.stored_size, attempt.stored_sha256)
        self.assertEqual((attempt.status, attempt.storage_encoding), ('uncertain', 'gzip'))
        result = self.upload(request_id, content=content)
        replay = self.upload(request_id, content=content)
        self.assertEqual((result.status_code, replay.status_code), (201, 200))
        attempt.refresh_from_db()
        self.assertEqual(representation, (attempt.storage_encoding, attempt.stored_size, attempt.stored_sha256))
        self.assertEqual(self.downloaded(replay), content)
        self.assertEqual(OpportunityAuditEvent.objects.filter(event_type='workspace_file_uploaded').count(), 1)
        self.assertEqual(len(list(Path(self.private_root.name).rglob('original'))), 1)

    def test_oversize_config_and_declared_size_mismatch_never_write_storage(self):
        with override_settings(SALES_WORKSPACE_MAX_UPLOAD_BYTES=3):
            self.assertEqual(self.upload(content=b'1234').status_code, 400)
        from apps.sales.private_attachments import upload_private_file
        file = SimpleUploadedFile('bad.bin', b'1234')
        file.size = 8
        with self.assertRaises(ValidationError):
            upload_private_file(self.opportunity, self.actor, 'proposal', file, uuid4())
        self.assertFalse(OpportunityWorkspaceUpload.objects.exists())
        self.assertFalse(list(Path(self.private_root.name).rglob('original')))

    def test_storage_representation_change_during_download_is_rejected(self):
        from apps.sales.private_attachments import _read
        result = self.upload(content=b'data' * 10000)
        def changed(*args):
            output = _read(*args)
            OpportunityWorkspaceUpload.objects.filter(pk=args[0].pk).update(stored_sha256='0' * 64)
            return output
        with patch('apps.sales.private_attachments._read', side_effect=changed):
            response = self.api.get(self.file_url(result) + 'download/')
        self.assertEqual(response.status_code, 424)
        self.assertFalse(response.streaming)

    def test_guarded_retry_and_compressed_download_recheck_current_permissions(self):
        request_id, content = uuid4(), b'private source' * 10000
        result = self.upload(request_id, content=content)
        self.assertEqual(result.status_code, 201)
        RolePermission.objects.filter(permission__module__code='sales_opportunities', permission__action='update').delete()
        with patch('apps.sales.private_attachments.prepare_upload', side_effect=AssertionError('Denied upload must not read file')):
            self.assertEqual(self.upload(request_id, content=content).status_code, 403)
        RolePermission.objects.filter(permission__module__code='sales_opportunities', permission__action='export').delete()
        with patch('apps.sales.private_attachments._read', side_effect=AssertionError('Denied download must not read private bytes')):
            response = self.api.get(self.file_url(result) + 'download/')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.streaming)

    def test_download_temp_disk_failure_returns_safe_storage_error(self):
        result = self.upload(content=b'original' * 10000)
        with patch('apps.sales.private_attachments.TemporaryFile', side_effect=OSError('Synthetic private disk path')):
            response = self.api.get(self.file_url(result) + 'download/')
        self.assertEqual(response.status_code, 424)
        self.assertFalse(response.streaming)
        self.assertEqual(response.data['code'], 'private_storage_unavailable')
        self.assertNotIn('Synthetic', str(response.data))


class UploadPreparationTests(SimpleTestCase):
    def test_fixed_chunk_reads_and_temp_cleanup_after_success_and_failure(self):
        class BoundedUpload(BytesIO):
            name = 'source.bin'
            def read(self, size=-1):
                if not 0 < size <= CHUNK_BYTES:
                    raise AssertionError('Unbounded source read')
                return super().read(size)
        source = BoundedUpload(b'x' * (CHUNK_BYTES * 3 + 7))
        source.size = len(source.getvalue())
        with prepare_upload(source, compress=True) as result:
            original, stored = result['original'], result['stored']
            self.assertEqual(gzip.decompress(stored.read()), source.getvalue())
        self.assertTrue(original.closed)
        self.assertTrue(stored.closed)
        source.seek(0)
        with self.assertRaises(RuntimeError):
            with prepare_upload(source, compress=True) as result:
                original, stored = result['original'], result['stored']
                raise RuntimeError('Synthetic command failure')
        self.assertTrue(original.closed)
        self.assertTrue(stored.closed)


@override_settings(**CONFIG)
class ChunkedGraphUploadTests(SimpleTestCase):
    def graph(self, size):
        graph = WorkspaceGraph(workspace_config())
        item = FakeGraph(graph.config).make('uploaded', 'source.bin', 'parent', folder=False)
        item['size'] = size
        return graph, item

    def test_sequential_aligned_fragments_preserve_all_bytes_and_check_scope_each_time(self):
        content = b'z' * (11 * 1024 * 1024 + 3)
        graph, item = self.graph(len(content))
        with patch.object(graph, '_json', side_effect=[{'uploadUrl': 'https://synthetic.sharepoint.com/upload'},
                {'nextExpectedRanges': ['5242880-']}, {'nextExpectedRanges': ['10485760-']}, item]) as request:
            checks = []
            result = graph.upload('parent', 'source.bin', BytesIO(content), size=len(content), before_chunk=lambda: checks.append(True))
        calls = request.call_args_list[1:]
        self.assertEqual(result['id'], 'uploaded')
        self.assertEqual(len(checks), 4)
        self.assertEqual(b''.join(call.kwargs['data'] for call in calls), content)
        self.assertEqual([call.kwargs['headers']['Content-Range'] for call in calls], [
            f'bytes 0-5242879/{len(content)}', f'bytes 5242880-10485759/{len(content)}', f'bytes 10485760-{len(content)-1}/{len(content)}'])
        self.assertTrue(all(call.kwargs['authenticated'] is False for call in calls))

    def test_revocation_after_first_fragment_is_uncertain_and_stops_transfer(self):
        graph, _ = self.graph(6 * 1024 * 1024)
        checks = []
        def authority():
            checks.append(True)
            if len(checks) == 3:
                raise PermissionDenied('Revoked')
        with patch.object(graph, '_json', side_effect=[{'uploadUrl': 'https://synthetic.sharepoint.com/upload'},
                {'nextExpectedRanges': ['5242880-']}]) as request:
            with self.assertRaises(PermissionDenied) as failure:
                graph.upload('parent', 'source.bin', BytesIO(b'x' * (6 * 1024 * 1024)), size=6 * 1024 * 1024, before_chunk=authority)
        self.assertTrue(failure.exception.upload_started)
        self.assertEqual(request.call_count, 2)

    def test_bad_progress_or_final_size_is_not_successful(self):
        for size, response in [(6 * 1024 * 1024, {'nextExpectedRanges': ['0-']}), (5, None), (1, None)]:
            graph, item = self.graph(size + 1)
            if size == 1:
                item['size'] = True
            with patch.object(graph, '_json', side_effect=[{'uploadUrl': 'https://synthetic.sharepoint.com/upload'}, response or item]):
                with self.assertRaises(WorkspaceError) as failure:
                    graph.upload('parent', 'source.bin', BytesIO(b'x' * size), size=size)
            self.assertTrue(failure.exception.upload_started)


@override_settings(**CONFIG, ROOT_URLCONF=__name__, SALES_WORKSPACE_MAX_UPLOAD_BYTES=0)
class SharePointUploadRecoveryTests(WorkspaceFixtures, TestCase):
    def test_partial_transfer_failure_cannot_blindly_start_another_remote_upload(self):
        self.ready()
        request_id = uuid4()
        failure = WorkspaceError('remote_busy')
        failure.upload_started = True
        payload = lambda: {'upload_request_id': str(request_id), 'file': SimpleUploadedFile('source.bin', b'content')}
        with patch.object(self.graph, 'upload', side_effect=failure) as remote:
            self.assertEqual(self.api.post(self.url + 'folders/tender/upload/', payload()).status_code, 424)
            self.assertEqual(self.api.post(self.url + 'folders/tender/upload/', payload()).status_code, 409)
        self.assertEqual(remote.call_count, 1)
        self.assertEqual(OpportunityWorkspaceUpload.objects.get().status, 'uncertain')
