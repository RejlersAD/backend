"""Order originals share order visibility and cannot cross document boundaries."""
from io import BytesIO
from unittest.mock import patch
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.test import APIClient

from apps.procurement.models import PODocument, PurchaseOrder, Vendor
from apps.rbac.models import (
    Module, Organization, Permission, Role, RoleModule, RolePermission,
    UserPermissionOverride, UserProfile, UserRole,
)
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints

urlpatterns = [path('api/v1/procurement/', include('apps.procurement.urls'))]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class OrderUploadedDocumentsTests(TestCase):
    def setUp(self):
        cache.clear()
        users = get_user_model()
        self.user = users.objects.create_user('po-source-reader', email='source-reader@example.test')
        self.other = users.objects.create_user('po-source-owner', email='source-owner@example.test')
        org, _ = Organization.objects.get_or_create(code='po-source-tests', defaults={'name': 'PO source tests'})
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'organization': org})
        self.other_profile, _ = UserProfile.objects.get_or_create(user=self.other, defaults={'organization': org})
        self.profile.roles.clear()
        self.other_profile.roles.clear()
        self.role = Role.objects.create(code='po-source-reader', name='PO source reader', level=3)
        UserRole.objects.create(user_profile=self.profile, role=self.role)
        module, _ = Module.objects.get_or_create(code='procurement_orders', defaults={'name': 'Purchase orders'})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        RoleModule.objects.get_or_create(role=self.role, module=module)
        for permission in module.permissions.filter(action='read', is_active=True):
            RolePermission.objects.get_or_create(role=self.role, permission=permission)
        vendor = Vendor.objects.create(vendor_code='SOURCE-VENDOR', name='Source vendor')
        self.order = PurchaseOrder.objects.create(
            po_number='PO-SOURCE-1', vendor=vendor, title='Imported order', total_amount=10,
        )
        self.other_order = PurchaseOrder.objects.create(
            po_number='PO-SOURCE-2', vendor=vendor, title='Other order', total_amount=20,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def url(self, document_id=None, order=None):
        base = f'/api/v1/procurement/orders/{(order or self.order).pk}/uploaded-documents/'
        return f'{base}{document_id}/content/' if document_id else base

    def document(self, **values):
        data = dict(original_filename='original signed PO.pdf', s3_key='private/original.pdf',
                    document_type='purchase_order', confirmed_po=self.order, uploaded_by=self.other)
        data.update(values)
        return PODocument.objects.create(**data)

    def save_attachments(self, values):
        self.order.attachments = values
        self.order.save(update_fields=['attachments'])

    def test_lists_linked_originals_without_duplicates_or_other_attachment_types(self):
        document = self.document()
        self.document(confirmed_po=self.other_order, s3_key='private/other.pdf')
        self.document(document_type='purchase_requisition', s3_key='private/pr.pdf')
        self.save_attachments([
            {'type': 'signed_purchase_order_pdf', 'document_id': str(document.pk), 'filename': document.original_filename},
            {'filename': 'quote.pdf', 's3_key': 'private/quote.pdf'},
            {'type': 'po_excel_import_source', 'filename': 'register.xlsx'},
        ])
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['count'], 1)
        entry = response.data['results'][0]
        self.assertEqual(entry['id'], str(document.pk))
        self.assertEqual(entry['filename'], document.original_filename)
        self.assertTrue(entry['content_url'].startswith('/api/v1/procurement/orders/'))
        self.assertEqual(urlsplit(entry['content_url']).path, self.url(document.pk))
        self.assertNotIn('private/', str(response.data))
        self.assertNotIn('s3_', str(response.data))

    def test_order_reader_can_view_linked_original_uploaded_by_another_user(self):
        document = self.document()
        original = b'%PDF-1.4 original signed content'
        with patch('django.core.files.storage.default_storage.open', return_value=BytesIO(original)) as storage:
            response = self.client.get(self.url(document.pk))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(b''.join(response.streaming_content), original)
            self.assertEqual(response['Content-Type'], 'application/pdf')
            self.assertEqual(response['Cache-Control'], 'private, no-store')
            self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
            self.assertTrue(response['Content-Disposition'].startswith('inline;'))
            storage.assert_called_once_with(document.s3_key, 'rb')

    def test_cross_order_or_unlinked_document_is_not_accessible(self):
        other = self.document(confirmed_po=self.other_order)
        pending = self.document(confirmed_po=None)
        with patch('django.core.files.storage.default_storage.open') as storage:
            self.assertEqual(self.client.get(self.url(other.pk)).status_code, 404)
            self.assertEqual(self.client.get(self.url(pending.pk)).status_code, 404)
            storage.assert_not_called()

    def test_forged_attachment_links_cannot_read_other_order_or_storage_paths(self):
        document = self.document(confirmed_po=self.other_order)
        self.save_attachments([
            {'type': 'signed_purchase_order_pdf', 'document_id': str(document.pk), 's3_key': document.s3_key},
            {'type': 'signed_purchase_order_pdf', 's3_key': 'private/secret.pdf'},
            {'type': 'signed_purchase_order_pdf', 's3_key': f'procurement/orders/{self.order.po_number}/../../secret.pdf'},
            {'type': 'signed_purchase_order_pdf', 'url': 'https://external.example.test/private.pdf'},
        ])
        with patch('django.core.files.storage.default_storage.open') as storage:
            for index in range(4):
                self.assertEqual(self.client.get(self.url(f'attachment-{index}')).status_code, 404)
            storage.assert_not_called()

    def test_legacy_signed_attachment_uses_order_bound_storage_and_unrelated_pdf_is_excluded(self):
        key = f'procurement/orders/{self.order.po_number}/legacy-signed.pdf'
        self.save_attachments([
            {'type': 'signed_purchase_order_pdf', 's3_key': key, 'filename': 'legacy-signed.pdf'},
            {'s3_key': key.replace('signed', 'quotation'), 'filename': 'quotation.pdf'},
        ])
        response = self.client.get(self.url())
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['id'], 'attachment-0')
        with patch('django.core.files.storage.default_storage.open', return_value=BytesIO(b'%PDF legacy')) as storage:
            content = self.client.get(self.url('attachment-0'))
            self.assertEqual(content.status_code, 200)
            self.assertEqual(b''.join(content.streaming_content), b'%PDF legacy')
            storage.assert_called_once_with(key, 'rb')

    def test_no_upload_returns_empty_without_generated_pdf(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'count': 0, 'results': []})

    def test_anonymous_and_unassigned_users_cannot_access_either_endpoint(self):
        document = self.document()
        with patch('django.core.files.storage.default_storage.open') as storage:
            self.client.force_authenticate(self.other)
            for url in (self.url(), self.url(document.pk)):
                self.assertEqual(self.client.get(url).status_code, 403)
            self.client.force_authenticate(None)
            for url in (self.url(), self.url(document.pk)):
                self.assertIn(self.client.get(url).status_code, (401, 403))
            storage.assert_not_called()

    def test_owner_and_assigned_approver_can_read_but_explicit_deny_wins(self):
        document = self.document()
        self.client.force_authenticate(self.other)
        self.order.created_by = self.other
        self.order.save(update_fields=['created_by'])
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.order.created_by = None
        self.order.approval_log = [{'user_id': str(self.other.pk), 'approver_email': self.other.email, 'status': 'Pending'}]
        self.order.save(update_fields=['created_by', 'approval_log'])
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        with patch('django.core.files.storage.default_storage.open', return_value=BytesIO(b'%PDF original')):
            response = self.client.get(self.url(document.pk))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(b''.join(response.streaming_content), b'%PDF original')
        permission = Permission.objects.filter(module__code='procurement_orders', action='read', is_active=True).first()
        self.order.created_by = self.other
        self.order.save(update_fields=['created_by'])
        UserPermissionOverride.objects.create(user_profile=self.other_profile, permission=permission, allowed=False)
        cache.clear()
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        self.assertEqual(self.client.get(self.url(document.pk)).status_code, 403)

    def test_missing_and_failed_storage_return_safe_errors(self):
        document = self.document(s3_key='')
        self.assertEqual(self.client.get(self.url(document.pk)).status_code, 404)
        document.s3_key = 'private/original.pdf'
        document.save(update_fields=['s3_key'])
        with patch('django.core.files.storage.default_storage.open', side_effect=FileNotFoundError('secret')):
            self.assertEqual(self.client.get(self.url(document.pk)).status_code, 404)
        with patch('django.core.files.storage.default_storage.open', side_effect=OSError('secret')):
            response = self.client.get(self.url(document.pk))
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('secret', str(response.data))
