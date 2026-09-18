"""Signed PO endpoint failures keep useful JSON and preserve access checks."""

import json
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.procurement.services.signed_po_pdf_import import SignedPOImportError
from apps.procurement.views import PODocumentViewSet


SERVICE = 'apps.procurement.services.signed_po_pdf_import.import_signed_po_pdf'
ACTION_POLICY = 'apps.rbac.action_policy.request_action_allowed'


class SignedPOImportResponseTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = SimpleNamespace(pk='reviewer', is_authenticated=True)
        policy = patch(ACTION_POLICY, return_value=True)
        self.policy = policy.start()
        self.addCleanup(policy.stop)
        importer = patch(SERVICE)
        self.importer = importer.start()
        self.addCleanup(importer.stop)

    def upload(self, include_file=True):
        data = {}
        if include_file:
            data['file'] = SimpleUploadedFile('signed.pdf', b'%PDF-original', content_type='application/pdf')
        request = self.factory.post('/api/v1/procurement/po-documents/import_signed_pdf/', data,
                                    format='multipart', HTTP_ACCEPT='application/json')
        force_authenticate(request, self.user)
        response = PODocumentViewSet.as_view({'post': 'import_signed_pdf'})(request)
        response.render()
        self.assertEqual(response['Content-Type'], 'application/json')
        return response, json.loads(response.content)

    def test_unexpected_failure_returns_safe_json_and_logs_matching_reference(self):
        internal_error = RuntimeError('Private storage configuration details')
        self.importer.side_effect = internal_error
        with self.assertLogs('apps.procurement.views', level='ERROR') as logs:
            response, payload = self.upload()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(payload['code'], 'signed_po_import_failed')
        self.assertRegex(payload['error_reference'], r'^[0-9a-f]{32}$')
        self.assertIn(payload['error_reference'], payload['error'])
        self.assertIn('contact support', payload['error'])
        self.assertNotIn(str(internal_error), response.content.decode())
        self.assertEqual(len(logs.records), 1)
        header = logs.records[0].getMessage()
        self.assertIn(payload['error_reference'], header)
        self.assertIn('exception_type=RuntimeError', header)
        self.assertRegex(header, r'location=views\.py:import_signed_pdf:\d+')
        self.assertNotIn(str(internal_error), header)
        self.assertNotIn('\n', header)
        self.assertIs(logs.records[0].exc_info[1], internal_error)

    def test_failure_header_identifies_deepest_application_frame_without_private_details(self):
        internal_error = RuntimeError(
            'Private document contents\nhttps://private-storage.invalid/source.pdf?token=secret'
        )

        def fail_source_save(*args, **kwargs):
            raise internal_error

        self.importer.side_effect = fail_source_save
        with self.assertLogs('apps.procurement.views', level='ERROR') as logs:
            response, payload = self.upload()

        self.assertEqual(response.status_code, 500)
        self.assertEqual(len(logs.records), 1)
        header = logs.records[0].getMessage()
        expected_location = (
            'location=test_signed_po_import_responses.py:fail_source_save:'
            f'{fail_source_save.__code__.co_firstlineno + 1}'
        )
        self.assertIn(payload['error_reference'], header)
        self.assertIn('exception_type=RuntimeError', header)
        self.assertIn(expected_location, header)
        self.assertNotIn('unittest', header)
        self.assertNotIn('\n', header)
        for private_value in ('Private document contents', 'private-storage.invalid', 'token=secret'):
            self.assertNotIn(private_value, header)
            self.assertNotIn(private_value, response.content.decode())
        self.assertIs(logs.records[0].exc_info[1], internal_error)
        self.assertIsNotNone(logs.records[0].exc_info[2])

    def test_source_validation_failure_remains_a_useful_bad_request(self):
        message = 'The uploaded file is not a valid PDF.'
        self.importer.side_effect = SignedPOImportError(message)
        response, payload = self.upload()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload, {'error': message})

    def test_api_errors_keep_their_original_status_and_details(self):
        for exception, expected_status, expected_payload in (
            (PermissionDenied('Updating this purchase order is not permitted.'), 403,
             {'detail': 'Updating this purchase order is not permitted.'}),
            (ValidationError({'approved_date': 'A valid date is required.'}), 400,
             {'approved_date': 'A valid date is required.'}),
        ):
            with self.subTest(exception=type(exception).__name__):
                self.importer.side_effect = exception
                response, payload = self.upload()
                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(payload, expected_payload)

    def test_missing_file_remains_a_bad_request_without_calling_importer(self):
        response, payload = self.upload(include_file=False)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload, {'error': 'Select a signed PDF file to import.'})
        self.importer.assert_not_called()

    def test_missing_create_permission_rejects_upload_before_import(self):
        self.policy.return_value = False
        response, payload = self.upload()
        self.assertEqual(response.status_code, 403)
        self.assertIn('detail', payload)
        self.importer.assert_not_called()

    def test_success_returns_import_result_and_preserves_update_permission(self):
        self.policy.side_effect = lambda request, module, action: action == 'create'
        result = {'success': True, 'document_id': 'saved-document', 'operation': 'uploaded',
                  'reconciliation_required': True}
        self.importer.return_value = result
        response, payload = self.upload()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload, result)
        self.assertFalse(self.importer.call_args.kwargs['allow_existing_update'])
