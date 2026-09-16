"""Explicit reconciliation turns a reviewed original into one protected saved PO."""

from copy import deepcopy
from datetime import date
from decimal import Decimal
import hashlib
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.test import APIClient

from apps.procurement.models import PODocument, PurchaseOrder, PurchaseRequisition, Vendor
from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, RolePermission, UserProfile, UserRole
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints


urlpatterns = [path('api/v1/procurement/', include('apps.procurement.urls'))]
secure_module_endpoints(urlpatterns)
BASE = '/api/v1/procurement/'


@override_settings(ROOT_URLCONF=__name__)
class PODocumentReconciliationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user('reconcile-po', email='reconcile-po@example.test')
        org, _ = Organization.objects.get_or_create(code='reconcile-po', defaults={'name': 'Reconcile test'})
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'organization': org})
        profile.roles.clear()
        self.role = Role.objects.create(code='reconcile-po', name='Reconcile PO', level=3)
        UserRole.objects.create(user_profile=profile, role=self.role)
        module, _ = Module.objects.get_or_create(code='procurement_orders', defaults={'name': 'Orders'})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        RoleModule.objects.create(role=self.role, module=module)
        for permission in module.permissions.filter(action__in=['read', 'create', 'update'], is_active=True):
            RolePermission.objects.create(role=self.role, permission=permission)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.vendor = Vendor.objects.create(vendor_code='RECONCILE', name='Selected supplier', status='active')
        self.pr = PurchaseRequisition.objects.create(pr_number='RAD-PRJ-PR-0051_2026', issued_by=self.user, status='approved')
        self.original = b'%PDF-1.4 exact retained original signed bytes'
        self.key = default_storage.save('reconcile-tests/original.pdf', ContentFile(self.original))
        self.fields = {
            'po_number': 'RAD-PRJ-PUR-0051_2026', 'source_po_number': 'RAD-PRJ-PUR-0051_JAN2026',
            'summary': 'Original parsed title', 'total_amount': '100.00', 'tax_amount': '0.00',
            'gross_amount': '100.00', 'currency': 'USD', 'po_date': '2026-01-07',
            'source_sha256': hashlib.sha256(self.original).hexdigest(),
            'signature_verified': True, 'stamp_verified': True,
            'approved_by_name': 'Source Approver', 'approved_by_title': 'Source Position',
            'approved_date': '2026-01-08', 'reconciliation_required': True,
            'vendor_name': 'Unmapped OCR supplier', 'pr_id': None,
        }
        self.document = PODocument.objects.create(
            uploaded_by=self.user, original_filename='original.pdf', document_type='purchase_order',
            s3_key=self.key, s3_url=default_storage.url(self.key), extracted_data=deepcopy(self.fields),
        )
        self.url = f'{BASE}po-documents/{self.document.pk}/reconcile/'
        self.mapping = {'vendor_id': str(self.vendor.pk), 'pr_id': str(self.pr.pk)}

    def reconcile(self, mapping=None):
        with self.captureOnCommitCallbacks(execute=True), patch('apps.procurement.serializers.notify_assigned_approvers'), patch('apps.procurement.serializers.notify_purchase_order_created'):
            return self.client.post(self.url, mapping if mapping is not None else self.mapping, format='json')

    def revoke(self, action):
        RolePermission.objects.filter(role=self.role, permission__module__code='procurement_orders', permission__action=action).delete()
        cache.clear()

    def existing(self, **values):
        fields = dict(po_number=self.fields['po_number'], vendor=self.vendor, pr_reference=self.pr,
                      title='Existing native title', total_amount='100.00', tax_amount='0.00', currency='USD')
        fields.update(values)
        return PurchaseOrder.objects.create(**fields)

    def test_review_then_reconcile_uses_saved_fields_and_exact_original_without_ocr_or_copy(self):
        saved = self.client.patch(f'{BASE}po-documents/{self.document.pk}/', {
            'summary': 'Reviewed title', 'total_amount': '125.00', 'tax_amount': '0.00',
            'gross_amount': '125.00', 'pr_id': str(self.pr.pk),
        }, format='json')
        self.assertEqual(saved.status_code, 200, saved.data)
        with patch('apps.procurement.services.signed_po_pdf_import.extract_signed_po_fields') as ocr, patch('django.core.files.storage.default_storage.save') as store:
            result = self.reconcile({'vendor_id': str(self.vendor.pk)})
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['operation'], 'created')
        ocr.assert_not_called()
        store.assert_not_called()
        order = PurchaseOrder.objects.get(pk=result.data['purchase_order_id'])
        self.assertEqual(order.title, 'Reviewed title')
        self.assertEqual(order.total_amount, Decimal('125.00'))
        self.assertEqual(order.vat_percentage, Decimal('0.00'))
        self.assertEqual(order.po_date, date(2026, 1, 7))
        self.assertEqual(order.approved_date, date(2026, 1, 8))
        self.assertEqual(order.approved_by_name, 'Source Approver')
        self.assertEqual(order.status, 'sent')
        self.document.refresh_from_db()
        self.assertEqual(self.document.s3_key, self.key)
        self.assertEqual(self.document.confirmed_po_id, order.pk)
        self.assertEqual(self.document.extracted_data['source_extracted_data'], self.fields)
        self.assertEqual(self.document.extracted_data['source_sha256'], self.fields['source_sha256'])
        self.assertFalse(self.document.extracted_data['reconciliation_required'])
        preview = self.client.get(f'{BASE}orders/{order.pk}/uploaded-documents/{self.document.pk}/content/')
        self.assertEqual(b''.join(preview.streaming_content), self.original)
        pending = self.client.get(f'{BASE}po-documents/', {'pending_reconciliation': 'true'})
        self.assertEqual(pending.data['count'], 0)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'converted')

    def test_repeated_completion_is_idempotent(self):
        first, second = self.reconcile(), self.reconcile()
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(second.data['operation'], 'already_reconciled')
        self.assertEqual(first.data['purchase_order_id'], second.data['purchase_order_id'])
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        self.assertEqual(PODocument.objects.count(), 1)
        self.assertEqual(len(PurchaseOrder.objects.get().attachments), 1)

    def test_update_permission_alone_cannot_materialize_order(self):
        self.revoke('create')
        saved = self.client.patch(f'{BASE}po-documents/{self.document.pk}/', {'summary': 'Saved review'}, format='json')
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(self.reconcile().status_code, 403)
        self.assertFalse(PurchaseOrder.objects.exists())

    def test_existing_order_requires_update_and_preserves_completed_business_data(self):
        order = self.existing(status='completed')
        self.revoke('update')
        self.assertEqual(self.reconcile().status_code, 403)
        self.document.refresh_from_db()
        self.assertIsNone(self.document.confirmed_po_id)
        permission = Permission.objects.get(module__code='procurement_orders', action='update')
        RolePermission.objects.create(role=self.role, permission=permission)
        cache.clear()
        result = self.reconcile()
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['operation'], 'attached')
        order.refresh_from_db()
        self.assertEqual(order.status, 'completed')
        self.assertEqual(order.title, 'Existing native title')
        self.assertEqual(PurchaseOrder.objects.count(), 1)

    def test_existing_number_with_different_amount_returns_conflict_without_partial_changes(self):
        order = self.existing(total_amount='999.00')
        result = self.reconcile()
        self.assertEqual(result.status_code, 409, result.data)
        self.assertIn('amount', result.data['error'])
        order.refresh_from_db()
        self.document.refresh_from_db()
        self.assertEqual(order.total_amount, Decimal('999.00'))
        self.assertEqual(order.attachments, [])
        self.assertIsNone(self.document.confirmed_po_id)

    def test_legacy_month_number_is_matched_without_creating_duplicate(self):
        order = self.existing(po_number=self.fields['source_po_number'])
        result = self.reconcile()
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['purchase_order_id'], str(order.pk))
        self.assertEqual(PurchaseOrder.objects.count(), 1)

    def test_missing_mapping_and_incomplete_values_return_field_errors(self):
        self.assertEqual(self.reconcile({}).status_code, 400)
        missing_pr = self.reconcile({'vendor_id': str(self.vendor.pk)})
        self.assertEqual(missing_pr.status_code, 400)
        self.assertIn('pr_id', missing_pr.data)
        for field, value in [('total_amount', '0'), ('po_date', None), ('currency', ''), ('gross_amount', '500')]:
            self.document.extracted_data = {**self.fields, field: value}
            self.document.save(update_fields=['extracted_data'])
            result = self.reconcile()
            self.assertEqual(result.status_code, 400, result.data)
            self.assertIn(field, result.data)
        self.assertFalse(PurchaseOrder.objects.exists())

    def test_original_digest_mismatch_and_missing_file_do_not_create_order(self):
        self.document.extracted_data = {**self.fields, 'source_sha256': '0' * 64}
        self.document.save(update_fields=['extracted_data'])
        self.assertEqual(self.reconcile().status_code, 409)
        self.document.s3_key = 'missing/original.pdf'
        self.document.save(update_fields=['s3_key'])
        self.assertEqual(self.reconcile().status_code, 400)
        self.assertFalse(PurchaseOrder.objects.exists())

    def test_unsigned_source_stays_unsigned_and_client_cannot_supply_approval_flags(self):
        forged = self.reconcile({**self.mapping, 'signature_verified': True})
        self.assertEqual(forged.status_code, 400)
        self.document.extracted_data = {**self.fields, 'signature_verified': False, 'stamp_verified': False}
        self.document.save(update_fields=['extracted_data'])
        result = self.reconcile()
        self.assertEqual(result.status_code, 200, result.data)
        order = PurchaseOrder.objects.get()
        self.assertEqual(order.status, 'draft')
        self.assertEqual(order.approval_signature, '')
        self.assertEqual(order.approved_by_name, '')
        self.assertIsNone(order.approved_at)

    def test_owner_is_required_and_concurrent_create_returns_conflict(self):
        other = get_user_model().objects.create_user('other-source-owner', email='other-source@example.test')
        self.document.uploaded_by = other
        self.document.save(update_fields=['uploaded_by'])
        self.assertEqual(self.reconcile().status_code, 404)
        self.document.uploaded_by = self.user
        self.document.save(update_fields=['uploaded_by'])
        with patch('apps.procurement.serializers.PurchaseOrderSerializer.save', side_effect=IntegrityError('duplicate')):
            self.assertEqual(self.reconcile().status_code, 409)
        self.document.refresh_from_db()
        self.assertIsNone(self.document.confirmed_po_id)
        self.assertFalse(PurchaseOrder.objects.exists())

    def test_create_only_user_cannot_reupload_over_existing_order(self):
        self.existing()
        self.revoke('update')
        with patch('apps.procurement.services.signed_po_pdf_import.extract_signed_po_fields', return_value=self.fields):
            response = self.client.post(f'{BASE}po-documents/import_signed_pdf/', {
                'file': SimpleUploadedFile('same.pdf', self.original, content_type='application/pdf'),
            }, format='multipart')
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(PODocument.objects.count(), 1)
        self.assertEqual(PurchaseOrder.objects.get().attachments, [])
