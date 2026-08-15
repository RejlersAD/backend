from django.core.files.storage import Storage
from django.test import SimpleTestCase

from apps.instrument_io_workflow.models import IOListDocument


class IOListDocumentStorageTests(SimpleTestCase):
    def test_pdf_file_uses_a_storage_backend_not_a_string(self):
        field = IOListDocument._meta.get_field('pdf_file')

        self.assertIsInstance(field.storage, Storage)
        self.assertTrue(callable(field.storage.generate_filename))

    def test_storage_generates_the_dated_upload_path(self):
        field = IOListDocument._meta.get_field('pdf_file')
        document = IOListDocument()

        filename = field.generate_filename(document, 'sample.pdf')

        self.assertRegex(
            filename,
            r'^instrument_io_workflow/\d{4}/\d{2}/sample(?:_[A-Za-z0-9]+)?\.pdf$',
        )
