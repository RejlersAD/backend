"""Non-persisting PO approval preview keeps the procurement read boundary."""

from copy import deepcopy
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.test import APIClient

from apps.procurement.models import PODocument, PurchaseOrder, Vendor
from apps.procurement.services.po_pdf_approval import POApprovalPreviewError
from apps.rbac.models import (
    Module, Organization, Permission, Role, RoleModule, RolePermission,
    UserPermissionOverride, UserProfile, UserRole,
)
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints


urlpatterns = [path('api/v1/procurement/', include('apps.procurement.urls'))]
secure_module_endpoints(urlpatterns)

DETECT = 'apps.procurement.services.po_pdf_approval.preview_signed_po_approval'
PDF = b'%PDF-1.4\nExact original upload bytes\x00\xff\n%%EOF'
RESULT = {
    'source_sha256': 'a' * 64, 'page_count': 27, 'pages_inspected': [1, 2],
    'approval_evidence': {
        'approved_by_name': '', 'approved_by_title': '', 'approved_date': '',
        'signature_detected': False, 'stamp_detected': False,
        'requires_review': True, 'detection_meaning': 'candidate_only',
        'issues': ['Approver name was not reliably readable.'],
    },
}


@override_settings(ROOT_URLCONF=__name__)
class POApprovalPreviewAPITests(TestCase):
    url = '/api/v1/procurement/po-documents/preview_signed_pdf/'

    def setUp(self):
        cache.clear()
        self.actor = get_user_model().objects.create_user('po-preview-reader', email='preview-reader@example.test')
        org, _ = Organization.objects.get_or_create(code='po-preview', defaults={'name': 'PO preview tests'})
        self.profile, _ = UserProfile.objects.get_or_create(user=self.actor, defaults={'organization': org})
        self.profile.roles.clear()
        self.role = Role.objects.create(code='po-preview-reader', name='PO preview reader', level=3)
        UserRole.objects.create(user_profile=self.profile, role=self.role)
        self.module, _ = Module.objects.get_or_create(code='procurement_orders', defaults={'name': 'Purchase orders'})
        ensure_module_actions(Module, Permission, module_ids=[self.module.pk])
        RoleModule.objects.create(role=self.role, module=self.module)
        for permission in self.module.permissions.filter(action='read', is_active=True):
            RolePermission.objects.create(role=self.role, permission=permission)
        vendor = Vendor.objects.create(vendor_code='PREVIEW-VENDOR', name='Unchanged supplier')
        self.order = PurchaseOrder.objects.create(
            po_number='PREVIEW-UNCHANGED', vendor=vendor, title='Unchanged order', total_amount=100,
        )
        self.document = PODocument.objects.create(
            original_filename='Unchanged original.pdf', uploaded_by=self.actor,
            document_type='purchase_order', extraction_status='completed', confirmed_po=self.order,
            s3_key='private/unchanged-original.pdf', extracted_data={'status': 'unchanged'},
        )
        self.client = APIClient()
        self.client.force_authenticate(self.actor)
        extraction = patch('apps.procurement.services.signed_po_pdf_import.extract_signed_po_fields',
                           return_value={'po_number': 'RAD-PRJ-PUR-0088_2026', 'total_amount': '100.00',
                                         'vendor_name': 'Source supplier'})
        extraction.start()
        self.addCleanup(extraction.stop)

    def upload(self, content=PDF, filename='source.pdf', **extra):
        return self.client.post(self.url, {
            'file': SimpleUploadedFile(filename, content, content_type='application/pdf'), **extra,
        }, format='multipart')

    def snapshot(self):
        return {
            'orders': list(PurchaseOrder.objects.order_by('pk').values()),
            'documents': list(PODocument.objects.order_by('pk').values()),
        }

    def test_read_only_user_can_preview_exact_bytes_without_model_or_storage_writes(self):
        before = self.snapshot()
        with patch(DETECT, return_value=deepcopy(RESULT)) as detector, \
                patch('django.core.files.storage.default_storage.save') as storage_save, \
                patch('django.core.files.storage.default_storage.delete') as storage_delete, \
                patch('apps.procurement.models.PODocument.save') as document_save, \
                patch('apps.procurement.models.PurchaseOrder.save') as order_save:
            response = self.upload()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual({key: response.data[key] for key in RESULT}, RESULT)
        self.assertTrue(response.data['preview_only'])
        self.assertEqual(response.data['extracted_data']['vendor_name'], 'Source supplier')
        detector.assert_called_once_with(PDF)
        storage_save.assert_not_called()
        storage_delete.assert_not_called()
        document_save.assert_not_called()
        order_save.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(set(self.role.permissions.values_list('action', flat=True)), {'read'})

    def test_filename_and_manual_form_fields_cannot_infer_or_override_source_approval(self):
        with patch(DETECT, return_value=deepcopy(RESULT)) as detector:
            response = self.upload(
                filename='Approved by Invented Person CEO 2026-01-29 SIGNED.pdf',
                approved_by_name='Invented Person', approved_by_title='CEO',
                approved_date='2026-01-29', signature_verified='true', stamp_verified='true',
            )
        self.assertEqual(response.status_code, 200, response.data)
        detector.assert_called_once_with(PDF)
        self.assertEqual(response.data['approval_evidence'], RESULT['approval_evidence'])
        self.assertNotIn('Invented', str(response.data))

    def test_missing_file_is_rejected_before_detection(self):
        before = self.snapshot()
        with patch(DETECT) as detector:
            response = self.client.post(self.url, {}, format='multipart')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('Select a signed PDF', response.data['error'])
        detector.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_upload_limit_rejects_over_15mb_before_detection_and_accepts_exact_boundary(self):
        content = b'%PDF' + b'0' * (15 * 1024 * 1024 - 4)
        with patch(DETECT, return_value=deepcopy(RESULT)) as detector:
            too_large = self.upload(content + b'1')
            self.assertEqual(too_large.status_code, 400, too_large.data)
            self.assertIn('15 MB', too_large.data['error'])
            detector.assert_not_called()
            accepted = self.upload(content)
            self.assertEqual(accepted.status_code, 200, accepted.data)
            detector.assert_called_once_with(content)

    def test_invalid_pdf_and_detector_validation_errors_return_400_without_persisting(self):
        before = self.snapshot()
        response = self.upload(b'This is not a PDF', filename='signed.pdf')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data, {'error': 'Select a valid PDF file.'})
        with patch(DETECT, side_effect=POApprovalPreviewError('The PDF is password protected.')):
            response = self.upload()
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data, {'error': 'The PDF is password protected.'})
        self.assertEqual(self.snapshot(), before)

    def test_anonymous_or_nonprocurement_user_cannot_run_detection(self):
        with patch(DETECT) as detector:
            self.client.force_authenticate(None)
            self.assertIn(self.upload().status_code, (401, 403))
            self.client.force_authenticate(self.actor)
            RoleModule.objects.filter(role=self.role).delete()
            cache.clear()
            self.assertEqual(self.upload().status_code, 403)
            detector.assert_not_called()

    def test_missing_read_grant_or_explicit_deny_blocks_preview_even_for_admin(self):
        with patch(DETECT) as detector:
            RolePermission.objects.filter(role=self.role).delete()
            cache.clear()
            self.assertEqual(self.upload().status_code, 403)
            self.actor.is_superuser = True
            self.actor.save(update_fields=['is_superuser'])
            for permission in self.module.permissions.filter(action='read', is_active=True):
                UserPermissionOverride.objects.create(user_profile=self.profile, permission=permission, allowed=False)
            cache.clear()
            self.assertEqual(self.upload().status_code, 403)
            detector.assert_not_called()

    def test_preview_does_not_accept_get_in_place_of_file_upload(self):
        with patch(DETECT) as detector:
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405, response.data)
        detector.assert_not_called()
