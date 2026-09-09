"""Regression tests for the safety boundary around Spec Customization."""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from .models import PaperSpecDocument, PaperSpecExtractionJob
from .workbook_storage_service import bump_workbook_revision, get_workbook_revision
from .services.extraction_service import PaperSpecExtractionService


class JobAuthorizationTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(
            username='spec-owner', email='spec-owner@example.test', password='safe-password'
        )
        self.other_user = user_model.objects.create_user(
            username='spec-other', email='spec-other@example.test', password='safe-password'
        )
        document = PaperSpecDocument.objects.create(
            file=SimpleUploadedFile('source.pdf', b'%PDF-1.4 test'),
            original_filename='source.pdf',
            file_size_bytes=13,
            sha256_hash='a' * 64,
            uploaded_by=self.owner,
        )
        self.job = PaperSpecExtractionJob.objects.create(document=document, created_by=self.owner)
        self.client = APIClient()

    def test_other_authenticated_user_cannot_read_job(self):
        self.client.force_authenticate(self.other_user)

        response = self.client.get(f'/api/v1/spec-customization/paper-spec/jobs/{self.job.id}/')

        self.assertEqual(response.status_code, 403)

    def test_creator_can_read_job(self):
        self.client.force_authenticate(self.owner)

        response = self.client.get(f'/api/v1/spec-customization/paper-spec/jobs/{self.job.id}/')

        self.assertEqual(response.status_code, 200)


class WorkbookRevisionTests(TestCase):
    def test_revision_changes_after_a_workbook_write(self):
        cache.clear()
        job_id = '00000000-0000-0000-0000-000000000001'

        initial = get_workbook_revision(job_id, 'spec')
        updated = bump_workbook_revision(job_id, 'spec')

        self.assertGreater(updated, initial)
        self.assertEqual(get_workbook_revision(job_id, 'spec'), updated)


class ExtractionCompletenessTests(SimpleTestCase):
    def test_header_only_text_result_is_not_accepted_as_an_extraction(self):
        service = PaperSpecExtractionService()

        self.assertFalse(service._text_layer_result_is_complete([
            {"class_code": "A", "components": []},
        ]))

    def test_text_result_with_a_component_can_use_the_fast_path(self):
        service = PaperSpecExtractionService()

        self.assertTrue(service._text_layer_result_is_complete([
            {"class_code": "A", "components": [{"component_type": "valve"}]},
        ]))
