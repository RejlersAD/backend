from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase
from rest_framework.test import APIClient

from apps.instrument_io_workflow.models import IOListDocument


class IOListOriginalPdfTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='pdf-preview-user',
            email='pdf-preview@example.com',
            password='test-password',
        )
        self.document = IOListDocument(
            document_number='TEST-PDF-001',
            revision_label='0',
            pdf_sha256='0' * 64,
            uploaded_by=self.user,
        )
        self.pdf_bytes = b'%PDF-1.4\n% preview endpoint test\n%%EOF\n'
        self.document.pdf_file.save(
            'preview-test.pdf',
            ContentFile(self.pdf_bytes),
            save=False,
        )
        self.document.save()
        self.url = (
            '/api/v1/instrument-io-workflow/documents/'
            f'{self.document.pk}/original-pdf/'
        )

    def tearDown(self):
        self.document.pdf_file.delete(save=False)

    def test_original_pdf_requires_authentication(self):
        response = APIClient().get(self.url)

        self.assertEqual(response.status_code, 401)

    def test_original_pdf_streams_inline_as_pdf(self):
        client = APIClient()
        client.force_authenticate(self.user)

        response = client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('inline', response['Content-Disposition'])
        self.assertEqual(b''.join(response.streaming_content), self.pdf_bytes)
