"""Project document permissions, source retention and protected file delivery."""
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.project_models import Project, ProjectMember
from apps.users.models import User
from ..models import ChangeEvent, Estimate, ProjectDocument
from ..tasks import parse_uploaded_document
from ..views import ProjectDocumentViewSet


@override_settings(ROOT_URLCONF='config.urls_test', DEFAULT_FILE_STORAGE='django.core.files.storage.FileSystemStorage')
class DocumentControlAPITests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory(prefix='radai-document-control-')
        self.addCleanup(self.media.cleanup)
        self.media_settings = override_settings(MEDIA_ROOT=self.media.name)
        self.media_settings.enable()
        self.addCleanup(self.media_settings.disable)
        self.owner = User.objects.create_user(username='document-owner', email='document-owner@example.test')
        self.viewer = User.objects.create_user(username='document-viewer', email='document-viewer@example.test')
        self.outsider = User.objects.create_user(username='document-outsider', email='document-outsider@example.test')
        self.project = Project.objects.create(code='DOC-CONTROL', name='Document project', owner=self.owner)
        self.other = Project.objects.create(code='DOC-OTHER', name='Unrelated project', owner=self.outsider)
        self.second = Project.objects.create(code='DOC-SECOND', name='Second owned project', owner=self.owner)
        ProjectMember.objects.create(project=self.project, user=self.viewer, role='viewer')
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.base = '/api/v1/project-control/documents/'
        self.queue = patch('apps.project_control.views.parse_uploaded_document.delay')
        self.queue.start()
        self.addCleanup(self.queue.stop)

    def upload(self, **data):
        payload = {'project': self.project.pk, 'title': 'Design basis', 'kind': 'specification',
                   'file': SimpleUploadedFile('design-basis.txt', b'Engineering source text', content_type='text/plain')}
        payload.update(data)
        return self.client.post(self.base, payload, format='multipart')

    def test_empty_list_exposes_authoritative_upload_capability(self):
        response = self.client.get(self.base, {'project': self.project.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['results'], [])
        self.assertTrue(response.data['capabilities']['can_upload'])
        self.assertGreater(response.data['capabilities']['max_document_bytes'], 0)
        self.assertIn({'value': 'drawing', 'label': 'Drawing'}, response.data['capabilities']['document_kinds'])
        self.client.force_authenticate(self.viewer)
        self.assertFalse(self.client.get(self.base, {'project': self.project.pk}).data['capabilities']['can_upload'])

    def test_upload_preserves_real_file_metadata_and_readonly_parser_fields(self):
        response = self.upload(parse_status='done', size_bytes=1, original_filename='forged.exe')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['original_filename'], 'design-basis.txt')
        self.assertEqual(response.data['size_bytes'], len(b'Engineering source text'))
        self.assertEqual(response.data['content_type'], 'text/plain')
        self.assertEqual(response.data['parse_status'], 'queued')
        self.assertEqual(response.data['uploaded_by'], self.owner.pk)
        self.assertTrue(response.data['has_file'])
        for capability in ('can_edit', 'can_delete', 'can_download', 'can_upload'):
            self.assertTrue(response.data[capability])
        self.assertEqual(response.data['file_url'], f"{self.base}{response.data['id']}/download/")
        self.assertNotIn('/media/', response.data['download_url'])

    def test_json_metadata_edit_preserves_file_project_and_parser_provenance(self):
        row = self.upload().data
        path = f"{self.base}{row['id']}/"
        response = self.client.patch(path, {'title': 'Updated title', 'kind': 'drawing', 'size_bytes': 900, 'parse_status': 'done'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['title'], 'Updated title')
        self.assertEqual(response.data['kind'], 'drawing')
        self.assertEqual(response.data['size_bytes'], row['size_bytes'])
        self.assertEqual(response.data['parse_status'], 'queued')
        self.assertEqual(self.client.patch(path, {'project': self.second.pk}, format='json').status_code, 400)
        replacement = SimpleUploadedFile('replacement.txt', b'Different file')
        self.assertEqual(self.client.patch(path, {'file': replacement}, format='multipart').status_code, 400)
        document = ProjectDocument.objects.get(pk=row['id'])
        self.assertEqual(document.project_id, self.project.pk)
        with document.file.open('rb') as stored:
            self.assertEqual(stored.read(), b'Engineering source text')

    def test_viewer_can_read_download_but_cannot_mutate(self):
        row = self.upload().data
        path = f"{self.base}{row['id']}/"
        self.client.force_authenticate(self.viewer)
        detail = self.client.get(path)
        self.assertEqual(detail.status_code, 200)
        self.assertTrue(detail.data['can_download'])
        self.assertFalse(detail.data['can_edit'])
        self.assertFalse(detail.data['can_delete'])
        self.assertFalse(detail.data['can_upload'])
        self.assertEqual(self.client.patch(path, {'title': 'Forbidden'}, format='json').status_code, 403)
        self.assertEqual(self.client.delete(path).status_code, 403)
        self.assertEqual(self.upload().status_code, 403)
        response = self.client.get(path + 'download/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'Engineering source text')
        self.assertTrue(response.closed)

    def test_cross_project_and_unauthenticated_access_are_rejected(self):
        row = self.upload().data
        path = f"{self.base}{row['id']}/"
        self.assertEqual(self.upload(project=self.other.pk).status_code, 400)
        self.client.force_authenticate(self.outsider)
        for suffix in ('', 'download/', 'presign-download/'):
            self.assertEqual(self.client.get(path + suffix).status_code, 404)
        self.assertEqual(self.client.get(self.base, {'project': self.project.pk}).data['results'], [])
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(path + 'download/').status_code, (401, 403))

    def test_download_uses_attachment_headers_and_presign_retains_authentication(self):
        row = self.upload().data
        path = f"{self.base}{row['id']}/"
        response = self.client.get(path + 'download/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/octet-stream')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertIn('design-basis.txt', response['Content-Disposition'])
        self.assertEqual(b''.join(response.streaming_content), b'Engineering source text')
        self.assertTrue(response.closed)
        presign = self.client.get(path + 'presign-download/')
        self.assertEqual(presign.data['download_url'], path + 'download/')
        self.assertTrue(presign.data['requires_auth'])

    def test_soft_delete_retains_estimate_and_change_source_and_blocks_download(self):
        row = self.upload().data
        document = ProjectDocument.objects.get(pk=row['id'])
        estimate = Estimate.objects.create(project=self.project, source_document=document, title='Source estimate')
        change = ChangeEvent.objects.create(project=self.project, source_document=document, summary='Source finding')
        path = f"{self.base}{row['id']}/"
        self.assertEqual(self.client.delete(path).status_code, 204)
        document.refresh_from_db(); estimate.refresh_from_db(); change.refresh_from_db()
        self.assertTrue(document.is_deleted)
        self.assertIsNotNone(document.deleted_at)
        self.assertEqual(estimate.source_document_id, document.pk)
        self.assertEqual(change.source_document_id, document.pk)
        self.assertTrue(document.file.storage.exists(document.file.name))
        self.assertEqual(self.client.get(self.base, {'project': self.project.pk}).data['count'], 0)
        for suffix in ('', 'download/', 'presign-download/'):
            self.assertEqual(self.client.get(path + suffix).status_code, 404)

    def test_metadata_task_does_not_process_removed_documents(self):
        row = self.upload().data
        document = ProjectDocument.objects.get(pk=row['id'])
        document.soft_delete()
        self.assertEqual(parse_uploaded_document(document.pk)['error'], 'not_found')
        document.refresh_from_db()
        self.assertEqual(document.parse_status, 'queued')
        self.assertEqual(document.parsed_data, {})

    def test_metadata_only_task_records_no_approval_or_extraction_result(self):
        row = self.upload().data
        self.assertEqual(parse_uploaded_document(row['id'])['parse_status'], 'done')
        document = ProjectDocument.objects.get(pk=row['id'])
        self.assertEqual(document.parsed_data['phase'], 1)
        self.assertIn('Metadata-only', document.parsed_data['note'])
        self.assertNotIn('approval', document.parsed_data)
        self.assertNotIn('extracted_text', document.parsed_data)

    def test_missing_file_and_storage_failure_are_distinct(self):
        row = self.upload().data
        document = ProjectDocument.objects.get(pk=row['id'])
        path = f"{self.base}{row['id']}/download/"
        with patch('django.core.files.storage.FileSystemStorage.open', side_effect=RuntimeError('storage offline')):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 503)
            self.assertNotIn('storage offline', response.data['detail'])
        document.file.storage.delete(document.file.name)
        self.assertEqual(self.client.get(path).status_code, 404)

    def test_concurrent_removal_cannot_be_undone_by_metadata_edit(self):
        row = self.upload().data
        original = ProjectDocumentViewSet.get_object
        def remove_after_read(view):
            document = original(view)
            ProjectDocument.objects.get(pk=document.pk).soft_delete()
            return document
        with patch.object(ProjectDocumentViewSet, 'get_object', remove_after_read):
            response = self.client.patch(f"{self.base}{row['id']}/", {'title': 'Late edit'}, format='json')
        self.assertEqual(response.status_code, 404)
        document = ProjectDocument.objects.get(pk=row['id'])
        self.assertTrue(document.is_deleted)
        self.assertEqual(document.title, 'Design basis')

    def test_invalid_project_filter_and_upload_limit_are_validated(self):
        self.assertEqual(self.client.get(self.base, {'project': 'invalid'}).status_code, 400)
        with patch('apps.project_control.serializers.MAX_DOCUMENT_BYTES', 2):
            self.assertEqual(self.upload().status_code, 400)
