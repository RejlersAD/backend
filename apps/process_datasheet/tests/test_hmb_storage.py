import os
import tempfile
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.process_datasheet.services.hmb_storage import store_hmb_source


class HMBStorageServiceTests(SimpleTestCase):
    @override_settings(USE_S3=True)
    @patch('apps.process_datasheet.services.hmb_storage.S3Service')
    def test_master_upload_uses_private_hmb_prefix_and_metadata(self, service_class):
        service_class.return_value.upload_file.return_value = {
            'success': True,
            'key': 'media/hmb/master-templates/project/file.xlsx',
            'size': 4,
        }
        handle, path = tempfile.mkstemp(suffix='.xlsx')
        try:
            os.write(handle, b'test')
            os.close(handle)

            result = store_hmb_source(
                path,
                upload_kind='master_template',
                original_filename='Master File.xlsx',
                project_id='project-id',
                user_id='user-id',
            )

            self.assertTrue(result['stored'])
            args, kwargs = service_class.return_value.upload_file.call_args
            self.assertEqual(args[1], 'hmb_master_templates')
            self.assertIn('project-id/', kwargs['filename'])
            self.assertEqual(kwargs['metadata']['upload-kind'], 'master_template')
        finally:
            if os.path.exists(path):
                os.unlink(path)