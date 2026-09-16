"""Saved source PDFs remain discoverable and protected before reconciliation."""
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.test import APIClient

from apps.procurement.models import PODocument, PurchaseOrder, PurchaseRequisition, Vendor
from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, RolePermission, UserProfile, UserRole
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints

urlpatterns = [path('api/v1/procurement/', include('apps.procurement.urls'))]
secure_module_endpoints(urlpatterns)
BASE = '/api/v1/procurement/po-documents/'


@override_settings(ROOT_URLCONF=__name__)
class SavedPODocumentTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user('po-document-reader', email='po-reader@example.test')
        self.other = get_user_model().objects.create_user('other-po-document-reader', email='other-po-reader@example.test')
        org, _ = Organization.objects.get_or_create(code='po-document-tests', defaults={'name': 'PO document tests'})
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'organization': org})
        profile.roles.clear()
        self.role = Role.objects.create(code='po-document-reader', name='PO document reader', level=3)
        UserRole.objects.create(user_profile=profile, role=self.role)
        module, _ = Module.objects.get_or_create(code='procurement_orders', defaults={'name': 'Purchase orders'})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        RoleModule.objects.get_or_create(role=self.role, module=module)
        for permission in module.permissions.filter(action='read', is_active=True):
            RolePermission.objects.get_or_create(role=self.role, permission=permission)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def document(self, **values):
        data = dict(original_filename='signed-po.pdf', s3_key='private/signed-po.pdf',
                    uploaded_by=self.user, document_type='purchase_order', extraction_status='completed',
                    extracted_data={'po_number': 'RAD-PRJ-PUR-0083_2026', 'reconciliation_required': True})
        data.update(values)
        return PODocument.objects.create(**data)

    def grant(self, action):
        for permission in Permission.objects.filter(module__code='procurement_orders', action=action, is_active=True):
            RolePermission.objects.get_or_create(role=self.role, permission=permission)
        cache.clear()

    def test_pending_register_only_returns_owned_unconfirmed_po_uploads(self):
        expected = self.document()
        self.document(uploaded_by=self.other)
        self.document(document_type='purchase_requisition')
        self.document(extracted_data={'reconciliation_required': False})
        vendor = Vendor.objects.create(vendor_code='DOC-TEST', name='Test vendor')
        order = PurchaseOrder.objects.create(po_number='PO-DOC-TEST', vendor=vendor, title='Confirmed order', total_amount=1)
        self.document(confirmed_po=order)
        response = self.client.get(BASE, {'pending_reconciliation': 'true'})
        self.assertEqual(response.status_code, 200, response.data)
        rows = response.data['results'] if isinstance(response.data, dict) else response.data
        self.assertEqual([row['id'] for row in rows], [str(expected.pk)])

    def test_saved_source_can_be_streamed_without_public_storage_access(self):
        document = self.document()
        source = b'%PDF-1.4 test source bytes'
        with patch('django.core.files.storage.default_storage.open', return_value=BytesIO(source)) as storage:
            response = self.client.get(f'{BASE}{document.pk}/content/')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(b''.join(response.streaming_content), source)
            self.assertEqual(response['Content-Type'], 'application/pdf')
            self.assertEqual(response['Cache-Control'], 'private, no-store')
            self.assertTrue(response['Content-Disposition'].startswith('inline;'))
            storage.assert_called_once_with(document.s3_key, 'rb')

    def test_other_uploaders_source_is_not_accessible(self):
        document = self.document(uploaded_by=self.other)
        with patch('django.core.files.storage.default_storage.open') as storage:
            self.assertEqual(self.client.get(f'{BASE}{document.pk}/content/').status_code, 404)
            storage.assert_not_called()

    def test_read_permission_and_authentication_are_required(self):
        document = self.document()
        RolePermission.objects.filter(role=self.role).delete()
        cache.clear()
        with patch('django.core.files.storage.default_storage.open') as storage:
            self.assertEqual(self.client.get(f'{BASE}{document.pk}/content/').status_code, 403)
            self.client.force_authenticate(None)
            self.assertIn(self.client.get(f'{BASE}{document.pk}/content/').status_code, (401, 403))
            storage.assert_not_called()

    def test_missing_source_returns_an_actionable_error(self):
        document = self.document(s3_key='')
        response = self.client.get(f'{BASE}{document.pk}/content/')
        self.assertEqual(response.status_code, 404)
        self.assertIn('unavailable', response.data['error'])

    def test_storage_failure_does_not_disclose_private_paths(self):
        document = self.document()
        with patch('django.core.files.storage.default_storage.open', side_effect=OSError('secret-storage-path')):
            response = self.client.get(f'{BASE}{document.pk}/content/')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('secret-storage-path', str(response.data))

    def test_legacy_seller_spillover_is_cleaned_for_display_without_rewriting_evidence(self):
        raw = 'Example Supplier LLC Seller Unit 01, Lake View Tower, Address: Abu Dhabi Invoicing: private address'
        document = self.document(extracted_data={'vendor_name': raw, 'reconciliation_required': True})
        response = self.client.get(f'{BASE}{document.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['extracted_data']['vendor_name'], 'Example Supplier LLC')
        self.assertEqual(response.data['extracted_data']['ocr_vendor_name_raw'], raw)
        document.refresh_from_db()
        self.assertEqual(document.extracted_data['vendor_name'], raw)

    def test_legacy_summary_displays_title_and_retains_source_evidence(self):
        raw = 'Total Purchase Price: 413.27 USD Approved by: Unrelated paragraph Page 2 PURCHASE ORDER: Supply of software licenses to engineering office. We, Rejlers are pleased to send this purchase order. SCOPE: Full detailed scope follows.'
        document = self.document(extracted_data={'summary': raw})
        response = self.client.get(f'{BASE}{document.pk}/')
        self.assertEqual(response.status_code, 200)
        summary = response.data['extracted_data']['summary']
        self.assertIn('Supply of software licenses', summary)
        self.assertNotIn('Approved by', summary)
        self.assertNotIn('Full detailed scope', summary)
        document.refresh_from_db()
        self.assertEqual(document.extracted_data['summary'], raw)

    def test_review_saves_fields_and_pr_link_preserving_source_without_creating_order(self):
        self.grant('update')
        document = self.document(extracted_data={
            'summary': 'Original source text', 'signature_verified': True, 'source_sha256': 'source-hash',
            'total_amount': '400.00', 'tax_amount': '20.00', 'gross_amount': '420.00',
        })
        pr = PurchaseRequisition.objects.create(pr_number='RAD-PRJ-PR-0002_2026', issued_by=self.user, requested_by=self.user)
        response = self.client.patch(f'{BASE}{document.pk}/', {
            'summary': 'Engineering licenses', 'currency': 'usd', 'total_amount': '413.27',
            'tax_amount': '20.66', 'gross_amount': '433.93', 'po_date': '2026-06-24',
            'entered_amount': '413.27', 'vat_basis': 'exclusive',
            'pr_id': str(pr.pk), 'po_number': 'RAD-PRJ-PUR-0083_JUN2026',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        fields = response.data['extracted_data']
        self.assertEqual(fields['summary'], 'Engineering licenses')
        self.assertEqual(fields['pr_id'], str(pr.pk))
        self.assertEqual(fields['pr_number'], pr.pr_number)
        self.assertEqual(fields['currency'], 'USD')
        self.assertEqual(fields['total_amount'], '400.00')
        self.assertEqual(fields['canonical_financials']['net_amount'], '413.27')
        self.assertEqual(fields['canonical_financials']['tax_amount'], '20.66')
        self.assertEqual(fields['canonical_financials']['total_amount'], '433.93')
        self.assertEqual(fields['source_extracted_data']['summary'], 'Original source text')
        self.assertEqual(fields['source_sha256'], 'source-hash')
        self.assertTrue(fields['signature_verified'])
        self.assertFalse(PurchaseOrder.objects.exists())
        document.refresh_from_db()
        self.assertEqual(document.s3_key, 'private/signed-po.pdf')
        self.assertIsNone(document.confirmed_po_id)
        cleared = self.client.patch(f'{BASE}{document.pk}/', {'pr_id': None}, format='json')
        self.assertEqual(cleared.status_code, 200)
        self.assertIsNone(cleared.data['extracted_data']['pr_id'])
        self.assertEqual(cleared.data['extracted_data']['source_extracted_data']['summary'], 'Original source text')

    def test_review_cannot_change_approval_or_source_fields(self):
        self.grant('update')
        document = self.document()
        response = self.client.patch(f'{BASE}{document.pk}/', {'confirmed_po': None, 'signature_verified': True, 's3_key': 'other-file'}, format='json')
        self.assertEqual(response.status_code, 400)
        document.refresh_from_db()
        self.assertNotIn('signature_verified', document.extracted_data)
        self.assertEqual(document.s3_key, 'private/signed-po.pdf')

    def test_invalid_financial_and_pr_fields_fail_without_partial_write(self):
        self.grant('update')
        document = self.document()
        response = self.client.patch(f'{BASE}{document.pk}/', {'total_amount': '-1', 'pr_id': 'bad-id', 'po_date': 'bad-date'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('total_amount', response.data)
        self.assertIn('pr_id', response.data)
        document.refresh_from_db()
        self.assertNotIn('reviewed_at', document.extracted_data)

    def test_update_delete_permissions_and_ownership_are_enforced(self):
        owned = self.document()
        other = self.document(uploaded_by=self.other)
        self.assertEqual(self.client.patch(f'{BASE}{owned.pk}/', {'summary': 'Edited'}, format='json').status_code, 403)
        self.assertEqual(self.client.delete(f'{BASE}{owned.pk}/').status_code, 403)
        self.grant('update')
        self.grant('delete')
        self.assertEqual(self.client.patch(f'{BASE}{other.pk}/', {'summary': 'Edited'}, format='json').status_code, 404)
        self.assertEqual(self.client.delete(f'{BASE}{other.pk}/').status_code, 404)
        self.assertTrue(PODocument.objects.filter(pk=owned.pk).exists())

    def test_delete_removes_only_owned_pending_document_and_source(self):
        self.grant('delete')
        document = self.document()
        with patch('django.core.files.storage.default_storage.delete') as storage:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.delete(f'{BASE}{document.pk}/')
            self.assertEqual(response.status_code, 204)
            self.assertFalse(PODocument.objects.filter(pk=document.pk).exists())
            storage.assert_called_once_with('private/signed-po.pdf')

    def test_confirmed_order_document_cannot_be_edited_or_deleted_through_upload_actions(self):
        self.grant('update')
        self.grant('delete')
        vendor = Vendor.objects.create(vendor_code='LINKED-DOC', name='Test vendor')
        order = PurchaseOrder.objects.create(po_number='PO-LINKED-DOC', vendor=vendor, title='Confirmed order', total_amount=1)
        document = self.document(confirmed_po=order)
        self.assertEqual(self.client.patch(f'{BASE}{document.pk}/', {'summary': 'Edited'}, format='json').status_code, 409)
        self.assertEqual(self.client.delete(f'{BASE}{document.pk}/').status_code, 409)
        self.assertTrue(PODocument.objects.filter(pk=document.pk).exists())
        self.assertTrue(PurchaseOrder.objects.filter(pk=order.pk).exists())
