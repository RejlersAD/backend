"""Regression for attachment format selection before DRF view dispatch."""
import io
import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from openpyxl import load_workbook
from rest_framework.test import APIRequestFactory, force_authenticate

from .models import PaperSpecDocument, PaperSpecExtractionJob
from .views import export_job


class ExportDownloadTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='export-owner', email='export-owner@example.test', is_superuser=True)
        document = PaperSpecDocument.objects.create(original_filename='source.pdf', sha256_hash='a' * 64)
        self.job = PaperSpecExtractionJob.objects.create(document=document, created_by=self.user)

    def request(self, query, authenticated=True):
        request = APIRequestFactory().get('/paper-spec/jobs/export/', query)
        if authenticated:
            force_authenticate(request, user=self.user)
        return export_job(request, job_id=self.job.pk)

    def test_xlsx_attachment_selector_reaches_handler(self):
        response = self.request({'export_format': 'xlsx'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('.xlsx', response['Content-Disposition'])
        book = load_workbook(io.BytesIO(response.content), read_only=True)
        try:
            self.assertEqual(book['Empty']['A1'].value, 'No piping classes extracted')
        finally:
            book.close()

    def test_json_selector_and_legacy_json_preserve_payload(self):
        for query in ({'export_format': 'json'}, {'format': 'json'}, {}):
            with self.subTest(query=query):
                response = self.request(query)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(json.loads(response.content)['job_id'], str(self.job.pk))

    def test_export_requires_authentication(self):
        self.assertIn(self.request({'export_format': 'xlsx'}, authenticated=False).status_code, (401, 403))
