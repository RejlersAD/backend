"""Native orders, retained signed sources and linked recommendations share CRUD."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import fitz
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import ProtectedError
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.test import APIClient

from apps.procurement.models import PODocument, PurchaseOrder, PurchaseRequisition, Receipt, Vendor
from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, RolePermission, UserProfile, UserRole
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints


urlpatterns = [path('api/v1/procurement/', include('apps.procurement.urls'))]
secure_module_endpoints(urlpatterns)
BASE = '/api/v1/procurement/'


@override_settings(ROOT_URLCONF=__name__)
class ProcurementCRUDLifecycleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user('crud-procurement', email='crud@example.test')
        org, _ = Organization.objects.get_or_create(code='crud-procurement', defaults={'name': 'CRUD test'})
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'organization': org})
        profile.roles.clear()
        self.role = Role.objects.create(code='crud-procurement', name='CRUD procurement', level=3)
        UserRole.objects.create(user_profile=profile, role=self.role)
        for code in ('procurement_orders', 'procurement_requisitions'):
            module, _ = Module.objects.get_or_create(code=code, defaults={'name': code})
            ensure_module_actions(Module, Permission, module_ids=[module.pk])
            RoleModule.objects.get_or_create(role=self.role, module=module)
            for permission in module.permissions.filter(action__in=['read', 'create', 'update', 'delete'], is_active=True):
                RolePermission.objects.get_or_create(role=self.role, permission=permission)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.vendor = Vendor.objects.create(vendor_code='CRUD-01', name='CRUD supplier', status='active')
        self.pr = PurchaseRequisition.objects.create(
            pr_number='RAD-PRJ-PR-0091_2026', issued_by=self.user, requested_by=self.user,
            vendor=self.vendor, status='approved', total_price=Decimal('100.00'),
            price_remarks_data={'signed_document_verification': {'signed_off': True}},
        )

    def order(self, **values):
        fields = dict(po_number='RAD-PRJ-PUR-0091_2026', pr_reference=self.pr,
                      vendor=self.vendor, title='Saved PO', total_amount='100.00', created_by=self.user)
        fields.update(values)
        return PurchaseOrder.objects.create(**fields)

    def source(self, order=None, **values):
        fields = dict(original_filename='signed-po.pdf', document_type='purchase_order',
                      uploaded_by=self.user, confirmed_po=order, s3_key='crud-tests/signed-po.pdf',
                      extracted_data={'reconciliation_required': True})
        fields.update(values)
        return PODocument.objects.create(**fields)

    def delete(self, kind, record):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.delete(f'{BASE}{kind}/{record.pk}/')

    def test_create_edit_delete_order_restores_pr_and_removes_original(self):
        with self.captureOnCommitCallbacks(execute=True), patch('apps.procurement.serializers.notify_assigned_approvers'), patch('apps.procurement.serializers.notify_purchase_order_created'):
            created = self.client.post(f'{BASE}orders/', {
                'pr_reference': str(self.pr.pk), 'vendor': str(self.vendor.pk),
                'po_number': 'RAD-PRJ-PUR-0091_2026', 'title': 'Native PO', 'total_amount': '100.00',
                'vat_percentage': '0.00', 'category': 'other',
            }, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        order = PurchaseOrder.objects.get(pk=created.data['id'])
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'converted')
        key = default_storage.save('crud-tests/signed-po.pdf', ContentFile(b'%PDF original'))
        source = self.source(order, s3_key=key)
        with self.captureOnCommitCallbacks(execute=True), patch('apps.procurement.serializers.notify_assigned_approvers', side_effect=RuntimeError('mail unavailable')):
            updated = self.client.patch(f'{BASE}orders/{order.pk}/', {'title': 'Edited PO'}, format='json')
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(updated.data['vat_percentage'], '0.00')
        self.assertEqual(self.delete('orders', order).status_code, 204)
        self.assertFalse(PurchaseOrder.objects.filter(pk=order.pk).exists())
        self.assertFalse(PODocument.objects.filter(pk=source.pk).exists())
        self.assertFalse(default_storage.exists(key))
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'approved')
        self.assertEqual(self.pr.po_number_reference, '')
        self.assertEqual(self.client.get(f'{BASE}orders/{order.pk}/').status_code, 404)

    def test_deleting_one_of_multiple_orders_keeps_other_link(self):
        first = self.order()
        second = self.order(po_number='RAD-PRJ-PUR-0092_2026')
        self.pr.status = 'converted'
        self.pr.po_number_reference = first.po_number
        self.pr.save()
        self.assertEqual(self.delete('orders', first).status_code, 204)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'converted')
        self.assertEqual(self.pr.po_number_reference, second.po_number)
        self.assertTrue(PurchaseOrder.objects.filter(pk=second.pk).exists())

    def test_deleting_legacy_conversion_never_fabricates_pr_approval(self):
        order = self.order()
        self.pr.status = 'converted'
        self.pr.po_number_reference = order.po_number
        self.pr.price_remarks_data = {}
        self.pr.save()
        self.assertEqual(self.delete('orders', order).status_code, 204)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'draft')

    def test_pr_delete_retains_independent_po_and_shared_source(self):
        key = f'procurement/signed_requisitions/{self.pr.pk}/2026/{self.pr.pr_number}_Purchase_Requisition_2026-01-28.pdf'
        attachment = {'type': 'signed_purchase_requisition_pdf', 's3_key': key, 'filename': 'pr.pdf'}
        self.pr.attachments = [attachment]
        self.pr.save(update_fields=['attachments'])
        order = self.order(attachments=[attachment])
        with patch('django.core.files.storage.default_storage.delete') as storage:
            self.assertEqual(self.delete('requisitions', self.pr).status_code, 204)
            storage.assert_not_called()
        order.refresh_from_db()
        self.assertIsNone(order.pr_reference_id)
        self.assertFalse(PurchaseRequisition.objects.filter(pk=self.pr.pk).exists())
        with patch('django.core.files.storage.default_storage.delete') as storage:
            self.assertEqual(self.delete('orders', order).status_code, 204)
            storage.assert_called_once_with(key)

    def test_pr_delete_cleans_original_and_native_attachment(self):
        original = f'procurement/signed_requisitions/{self.pr.pk}/2026/{self.pr.pr_number}_Purchase_Requisition_2026-01-28.pdf'
        native = f'procurement/requisitions/{self.pr.pr_number}/quote.pdf'
        self.pr.attachments = [
            {'type': 'signed_purchase_requisition_pdf', 's3_key': original}, {'s3_key': native},
            {'s3_key': 'private/unrelated.pdf'},
        ]
        self.pr.save(update_fields=['attachments'])
        with patch('django.core.files.storage.default_storage.delete') as storage:
            self.assertEqual(self.delete('requisitions', self.pr).status_code, 204)
        self.assertEqual({call.args[0] for call in storage.call_args_list}, {original, native})

    def test_receipts_and_protected_dependencies_return_409_without_partial_delete(self):
        order = self.order()
        source = self.source(order)
        receipt = Receipt.objects.create(receipt_number='CRUD-GR-01', purchase_order=order, received_by=self.user)
        with patch('django.core.files.storage.default_storage.delete') as storage:
            response = self.delete('orders', order)
            self.assertEqual(response.status_code, 409)
            self.assertIn('goods receipts', response.data['error'])
            self.assertTrue(PODocument.objects.filter(pk=source.pk).exists())
            receipt.delete()
            with patch.object(PurchaseOrder, 'delete', side_effect=ProtectedError('linked invoice', [order])):
                response = self.delete('orders', order)
            self.assertEqual(response.status_code, 409)
            self.assertTrue(PODocument.objects.filter(pk=source.pk).exists())
            self.assertTrue(PurchaseOrder.objects.filter(pk=order.pk).exists())
            storage.assert_not_called()

    def test_pending_document_shared_by_order_keeps_bytes_on_delete(self):
        document = self.source()
        self.order(attachments=[{'s3_key': document.s3_key}])
        with patch('django.core.files.storage.default_storage.delete') as storage:
            self.assertEqual(self.delete('po-documents', document).status_code, 204)
            storage.assert_not_called()

    def test_read_permission_does_not_allow_create_update_or_delete(self):
        order = self.order()
        RolePermission.objects.filter(role=self.role).exclude(permission__action='read').delete()
        cache.clear()
        self.assertEqual(self.client.post(f'{BASE}orders/', {}, format='json').status_code, 403)
        self.assertEqual(self.client.patch(f'{BASE}orders/{order.pk}/', {'title': 'No'}, format='json').status_code, 403)
        self.assertEqual(self.client.delete(f'{BASE}orders/{order.pk}/').status_code, 403)
        self.assertEqual(self.client.delete(f'{BASE}requisitions/{self.pr.pk}/').status_code, 403)
        self.assertTrue(PurchaseOrder.objects.filter(pk=order.pk).exists())

    def test_pr_multipart_and_json_clear_optional_fields_preserving_evidence(self):
        source = {'signed_document_verification': {'signed_off': True}, 'source_approval_reviews': [{'reviewer': 'Original'}]}
        for encoding in ('multipart', 'json'):
            self.pr.vendor = self.vendor
            self.pr.supplier_name = 'Before'
            self.pr.purchase_recommendation = 'Before'
            self.pr.price_remarks_data = source
            self.pr.save()
            response = self.client.patch(f'{BASE}requisitions/{self.pr.pk}/', {
                'vendor': '' if encoding == 'multipart' else None,
                'supplier_name': '', 'purchase_recommendation': '',
                'total_price': '' if encoding == 'multipart' else None,
            }, format=encoding)
            self.assertEqual(response.status_code, 200, response.data)
            self.pr.refresh_from_db()
            self.assertIsNone(self.pr.vendor_id)
            self.assertIsNone(self.pr.total_price)
            self.assertEqual(self.pr.supplier_name, '')
            self.assertEqual(self.pr.purchase_recommendation, '')
            self.assertEqual(self.pr.price_remarks_data, source)

    def test_native_pr_attachment_uses_active_storage(self):
        pdf = fitz.open()
        pdf.new_page().insert_text((72, 72), 'Test quotation')
        upload = SimpleUploadedFile('quotation.pdf', pdf.tobytes(), content_type='application/pdf')
        pdf.close()
        response = self.client.post(f'{BASE}requisitions/{self.pr.pk}/upload_attachment/', {'files': upload}, format='multipart')
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        key = self.pr.attachments[0]['s3_key']
        self.assertTrue(default_storage.exists(key))
        self.assertEqual(self.delete('requisitions', self.pr).status_code, 204)
        self.assertFalse(default_storage.exists(key))

    def test_reassign_order_reconciles_both_recommendations(self):
        self.pr.status = 'converted'
        self.pr.po_number_reference = 'RAD-PRJ-PUR-0091_2026'
        self.pr.save()
        other = PurchaseRequisition.objects.create(pr_number='RAD-PRJ-PR-0092_2026', issued_by=self.user, status='draft')
        order = self.order()
        with self.captureOnCommitCallbacks(execute=True), patch('apps.procurement.serializers.notify_assigned_approvers'):
            response = self.client.patch(f'{BASE}orders/{order.pk}/', {'pr_reference': str(other.pk)}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.pr.status, 'approved')
        self.assertEqual(self.pr.po_number_reference, '')
        self.assertEqual(other.status, 'converted')
        self.assertEqual(other.po_number_reference, order.po_number)
        self.assertEqual(self.delete('orders', order).status_code, 204)
        other.refresh_from_db()
        self.assertEqual(other.status, 'draft')

    def test_edit_cannot_erase_imported_approval_or_original_source(self):
        history = [{'stage': 'Signed PO document approval', 'approver': 'Original Approver',
                    'status': 'Approved', 'evidence_document_id': 'source-1', 'signature_verified': True}]
        original = {'type': 'signed_purchase_order_pdf', 'document_id': 'source-1'}
        order = self.order(pr_reference=None, status='sent', approval_log=history,
                           approved_by_name='Original Approver', approved_date=date(2026, 1, 7),
                           approval_signature='/original.pdf#page=1', attachments=[original])
        with self.captureOnCommitCallbacks(execute=True), patch('apps.procurement.serializers.notify_assigned_approvers'):
            response = self.client.patch(f'{BASE}orders/{order.pk}/', {
                'title': 'Corrected order', 'approval_log': [], 'attachments': [],
                'approved_by_name': '', 'approved_date': None, 'approval_signature': '',
            }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        order.refresh_from_db()
        self.assertEqual(order.title, 'Corrected order')
        self.assertEqual(order.approval_log, history)
        self.assertEqual(order.attachments, [original])
        self.assertEqual(order.approved_by_name, 'Original Approver')
        self.assertEqual(order.approved_date, date(2026, 1, 7))
        self.assertEqual(order.approval_signature, '/original.pdf#page=1')

    def test_native_pr_create_update_read_and_delete_round_trip(self):
        payload = {'pr_number': 'RAD-PRJ-PR-0093_2026', 'requisition_type': 'general',
                   'product_service': 'Native software purchase', 'issued_date': '2026-01-28',
                   'total_price': '120.00', 'vendor': str(self.vendor.pk)}
        created = self.client.post(f'{BASE}requisitions/', payload, format='multipart')
        self.assertEqual(created.status_code, 201, created.data)
        record = PurchaseRequisition.objects.get(pk=created.data['id'])
        self.assertEqual(record.status, 'draft')
        updated = self.client.patch(f'{BASE}requisitions/{record.pk}/',
                                    {'purchase_recommendation': 'Reviewed commercial notes'}, format='json')
        self.assertEqual(updated.status_code, 200, updated.data)
        read = self.client.get(f'{BASE}requisitions/{record.pk}/')
        self.assertEqual(read.status_code, 200, read.data)
        self.assertEqual(read.data['purchase_recommendation'], 'Reviewed commercial notes')
        self.assertEqual(read.data['total_price'], '120.00')
        self.assertEqual(self.delete('requisitions', record).status_code, 204)
        self.assertEqual(self.client.get(f'{BASE}requisitions/{record.pk}/').status_code, 404)
        # Reusing a deleted manual number does not collide with a ghost record.
        recreated = self.client.post(f'{BASE}requisitions/', payload, format='multipart')
        self.assertEqual(recreated.status_code, 201, recreated.data)

    def test_native_pr_attachment_respects_same_owner_rule_as_edit(self):
        other = get_user_model().objects.create_user('other-issuer')
        self.pr.issued_by = other
        self.pr.save(update_fields=['issued_by'])
        response = self.client.post(f'{BASE}requisitions/{self.pr.pk}/upload_attachment/', {}, format='multipart')
        self.assertEqual(response.status_code, 403)

    def test_order_form_cannot_supply_cleanup_ownership(self):
        order = self.order()
        with self.captureOnCommitCallbacks(execute=True), patch('apps.procurement.serializers.notify_assigned_approvers'):
            response = self.client.patch(f'{BASE}orders/{order.pk}/', {
                'contact_persons': {'_retained_requisition_sources': ['private/unrelated.pdf']},
            }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        order.refresh_from_db()
        self.assertNotIn('_retained_requisition_sources', order.contact_persons)
        with patch('django.core.files.storage.default_storage.delete') as storage:
            self.assertEqual(self.delete('orders', order).status_code, 204)
            storage.assert_not_called()

    def test_link_existing_order_uses_order_update_permission(self):
        order = self.order(pr_reference=None)
        url = f'{BASE}requisitions/{self.pr.pk}/link-purchase-order/'
        permission = Permission.objects.get(module__code='procurement_orders', action='update')
        RolePermission.objects.filter(role=self.role, permission=permission).delete()
        cache.clear()
        denied = self.client.post(url, {'purchase_order_id': str(order.pk)}, format='json')
        self.assertEqual(denied.status_code, 403)
        RolePermission.objects.create(role=self.role, permission=permission)
        cache.clear()
        allowed = self.client.post(url, {'purchase_order_id': str(order.pk)}, format='json')
        self.assertEqual(allowed.status_code, 200, allowed.data)
        order.refresh_from_db()
        self.assertEqual(order.pr_reference_id, self.pr.pk)

    def test_renamed_pr_deletes_its_original_native_uploads(self):
        key = default_storage.save(f'procurement/requisitions/{self.pr.pr_number}/native.pdf', ContentFile(b'%PDF native PR attachment'))
        self.pr.attachments = [{'s3_key': key}]
        self.pr.save(update_fields=['attachments'])
        response = self.client.patch(f'{BASE}requisitions/{self.pr.pk}/', {
            'pr_number': 'RAD-PRJ-PR-0191_2026', 'price_remarks_data': {'commercial_note': 'Keep me'},
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        self.assertTrue(default_storage.exists(key))
        # Subsequent metadata updates retain the server-owned cleanup record.
        response = self.client.patch(f'{BASE}requisitions/{self.pr.pk}/', {'price_remarks_data': {'commercial_note': 'Updated'}}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.delete('requisitions', self.pr).status_code, 204)
        self.assertFalse(default_storage.exists(key))

    def test_renamed_po_deletes_original_uploads_and_cannot_claim_unrelated_keys(self):
        order = self.order()
        key = default_storage.save(f'procurement/orders/{order.po_number}/native.pdf', ContentFile(b'%PDF native PO attachment'))
        other_key = default_storage.save('private/unrelated-cleanup.pdf', ContentFile(b'%PDF unrelated'))
        order.attachments = [{'s3_key': key}, {'s3_key': other_key}]
        order.save(update_fields=['attachments'])
        with self.captureOnCommitCallbacks(execute=True), patch('apps.procurement.serializers.notify_assigned_approvers'):
            response = self.client.patch(f'{BASE}orders/{order.pk}/', {
                'po_number': 'RAD-PRJ-PUR-0191_2026',
                'contact_persons': {'_retained_attachment_sources': [other_key]},
            }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        order.refresh_from_db()
        self.assertEqual(order.contact_persons['_retained_attachment_sources'], [key])
        self.assertEqual(self.delete('orders', order).status_code, 204)
        self.assertFalse(default_storage.exists(key))
        self.assertTrue(default_storage.exists(other_key))

    def test_pr_form_cannot_forge_retained_attachment_ownership(self):
        key = default_storage.save('private/unrelated-pr.pdf', ContentFile(b'%PDF unrelated'))
        response = self.client.patch(f'{BASE}requisitions/{self.pr.pk}/', {
            'price_remarks_data': {'_retained_attachment_sources': [key]},
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        self.assertNotIn('_retained_attachment_sources', self.pr.price_remarks_data)
        self.assertEqual(self.delete('requisitions', self.pr).status_code, 204)
        self.assertTrue(default_storage.exists(key))
