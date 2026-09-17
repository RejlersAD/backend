"""The approval-record PDF contains only authorized current original pages."""

import hashlib
import json
from copy import deepcopy
from datetime import date
from decimal import Decimal
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.test import APIClient

from apps.procurement.models import PODocument, PurchaseOrder, PurchaseRequisition, Vendor
from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, RolePermission, UserPermissionOverride, UserProfile, UserRole
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints


urlpatterns = [path('api/v1/procurement/', include('apps.procurement.urls'))]
secure_module_endpoints(urlpatterns)


def pdf_bytes(*labels, password=None):
    with pymupdf.open() as document:
        for label in labels:
            document.new_page().insert_text((72, 72), label)
        options = {'encryption': pymupdf.PDF_ENCRYPT_AES_256, 'user_pw': password, 'owner_pw': password} if password else {}
        return document.tobytes(**options)


@override_settings(ROOT_URLCONF=__name__)
class RequisitionApprovalRecordPDFTests(TestCase):
    def setUp(self):
        cache.clear()
        media = TemporaryDirectory(prefix='approval-record-tests-')
        self.addCleanup(media.cleanup)
        storage = override_settings(MEDIA_ROOT=media.name, STORAGES={
            'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
            'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
        })
        storage.enable()
        self.addCleanup(storage.disable)
        self.user = get_user_model().objects.create_user('approval-record-reader', email='reader@example.test')
        self.other = get_user_model().objects.create_user('approval-record-other', email='other@example.test')
        org, _ = Organization.objects.get_or_create(code='approval-record-tests', defaults={'name': 'Approval records'})
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'organization': org})
        self.profile.roles.clear()
        self.role = Role.objects.create(code='approval-record-reader', name='Approval record reader', level=3)
        UserRole.objects.create(user_profile=self.profile, role=self.role)
        for code in ('procurement_requisitions', 'procurement_orders'):
            module, _ = Module.objects.get_or_create(code=code, defaults={'name': code})
            ensure_module_actions(Module, Permission, module_ids=[module.pk])
            RoleModule.objects.get_or_create(role=self.role, module=module)
            for permission in module.permissions.filter(action='read', is_active=True):
                RolePermission.objects.get_or_create(role=self.role, permission=permission)
        self.pr = PurchaseRequisition.objects.create(pr_number='RAD-PRJ-PR-0001_2026', issued_by=self.other)
        self.foreign_pr = PurchaseRequisition.objects.create(pr_number='RAD-PRJ-PR-0002_2026', issued_by=self.other)
        self.vendor = Vendor.objects.create(vendor_code='APPROVAL-SOURCE', name='Source supplier')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def url(self):
        return f'/api/v1/procurement/requisitions/{self.pr.pk}/approval-record-pdf/'

    def pr_source(self, content=None, *, current=True, uploaded_at=None):
        content = content if content is not None else pdf_bytes('PR page 1', 'PR page 2')
        digest = hashlib.sha256(content).hexdigest()
        key = default_storage.save(
            f'procurement/signed_requisitions/{self.pr.pk}/2026/{self.pr.pr_number}_Purchase_Requisition_2026-09-17.pdf',
            ContentFile(content),
        )
        source = {'type': 'signed_purchase_requisition_pdf', 'sha256': digest, 'storage_key': key, 'filename': 'Signed PR.pdf'}
        if uploaded_at:
            source['uploaded_at'] = uploaded_at
        self.pr.attachments = [*(self.pr.attachments or []), source]
        if current:
            self.pr.price_remarks_data = {'signed_document_verification': {'document_sha256': digest}}
        self.pr.save(update_fields=['attachments', 'price_remarks_data'])
        return source, content

    def order(self, *, pr=None, **values):
        return PurchaseOrder.objects.create(
            po_number=f'RAD-PRJ-PUR-{PurchaseOrder.objects.count() + 1:04d}_2026',
            pr_reference=pr or self.pr, vendor=self.vendor, title='Signed purchase order',
            total_amount=100, created_by=self.other, **values,
        )

    def po_source(self, order, content=None, *, signed=True):
        content = content if content is not None else pdf_bytes('PO page 1', 'PO page 2', 'PO page 3')
        digest = hashlib.sha256(content).hexdigest()
        key = default_storage.save(f'procurement/signed_documents/2026/{order.pk}.pdf', ContentFile(content))
        document = PODocument.objects.create(
            original_filename='Signed PO.pdf', s3_key=key, uploaded_by=self.other,
            document_type='purchase_order', confirmed_po=order, extracted_data={'source_sha256': digest},
        )
        if signed:
            order.attachments = [*(order.attachments or []), {'type': 'signed_purchase_order_pdf', 'document_id': str(document.pk), 'sha256': digest}]
            order.save(update_fields=['attachments'])
        return document, content

    def read(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200, getattr(response, 'data', ''))
        return response, b''.join(response.streaming_content)

    def labels(self, content):
        with pymupdf.open(stream=content, filetype='pdf') as document:
            return [page.get_text().strip() for page in document]

    def revoke(self, module):
        RolePermission.objects.filter(role=self.role, permission__module__code=module, permission__action='read').delete()
        cache.clear()

    def test_all_original_pages_in_pr_then_po_order_without_source_or_record_mutations(self):
        pr_source, pr_content = self.pr_source()
        order = self.order(status='completed')
        po_source, po_content = self.po_source(order)
        before = (PurchaseRequisition.objects.values().get(pk=self.pr.pk), PurchaseOrder.objects.values().get(pk=order.pk), PODocument.objects.values().get(pk=po_source.pk))
        response, content = self.read()
        self.assertEqual(self.labels(content), ['PR page 1', 'PR page 2', 'PO page 1', 'PO page 2', 'PO page 3'])
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('inline;', response['Content-Disposition'])
        self.assertIn('_Approval_Record.pdf', response['Content-Disposition'])
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response['X-Approval-Record-PO-Source'], 'uploaded_original')
        for key, original in ((pr_source['storage_key'], pr_content), (po_source.s3_key, po_content)):
            with default_storage.open(key, 'rb') as saved:
                self.assertEqual(saved.read(), original)
        self.assertEqual(before, (PurchaseRequisition.objects.values().get(pk=self.pr.pk), PurchaseOrder.objects.values().get(pk=order.pk), PODocument.objects.values().get(pk=po_source.pk)))

    def test_no_linked_order_returns_exact_pr_original_and_ignores_stale_metadata(self):
        _, original = self.pr_source()
        foreign = self.order(pr=self.foreign_pr)
        self.po_source(foreign)
        self.pr.price_remarks_data['po_link'] = {'po_id': str(foreign.pk), 'status': 'linked'}
        self.pr.po_number_reference = foreign.po_number
        self.pr.save(update_fields=['price_remarks_data', 'po_number_reference'])
        self.assertEqual(self.read()[1], original)

    def test_current_pr_evidence_digest_wins_over_newer_historical_attachment(self):
        _, original = self.pr_source(pdf_bytes('Current PR'), uploaded_at='2026-09-01T00:00:00Z')
        self.pr_source(pdf_bytes('Other historical PR'), current=False, uploaded_at='2026-09-16T00:00:00Z')
        self.assertEqual(self.read()[1], original)

    def test_without_current_digest_latest_uploaded_pr_is_used(self):
        self.pr_source(pdf_bytes('Older PR'), current=False, uploaded_at='2026-09-01T00:00:00Z')
        _, latest = self.pr_source(pdf_bytes('Latest PR'), current=False, uploaded_at='2026-09-16T00:00:00Z')
        self.assertEqual(self.read()[1], latest)

    def test_latest_actual_linked_po_wins_over_stale_saved_reference(self):
        self.pr_source(pdf_bytes('PR'))
        old = self.order()
        self.po_source(old, pdf_bytes('Old PO'))
        current = self.order()
        self.po_source(current, pdf_bytes('Current PO'))
        self.pr.price_remarks_data['po_link'] = {'po_id': str(old.pk)}
        self.pr.save(update_fields=['price_remarks_data'])
        self.assertEqual(self.labels(self.read()[1]), ['PR', 'Current PO'])

    def test_po_signed_digest_selects_current_source_without_appending_history(self):
        self.pr_source(pdf_bytes('PR'))
        order = self.order()
        original, _ = self.po_source(order, pdf_bytes('Current signed PO'))
        self.po_source(order, pdf_bytes('Later historical upload'), signed=False)
        self.assertEqual(self.labels(self.read()[1]), ['PR', 'Current signed PO'])
        self.assertEqual(order.source_documents.count(), 2)

    def test_newest_confirmed_document_is_used_without_signed_attachment_digest(self):
        self.pr_source(pdf_bytes('PR'))
        order = self.order()
        self.po_source(order, pdf_bytes('Older PO'), signed=False)
        self.po_source(order, pdf_bytes('Latest PO'), signed=False)
        self.assertEqual(self.labels(self.read()[1]), ['PR', 'Latest PO'])

    def test_foreign_po_document_attachment_cannot_authorize_its_source(self):
        self.pr_source()
        order = self.order()
        foreign = self.order(pr=self.foreign_pr)
        foreign_source, _ = self.po_source(foreign)
        order.attachments = [{'type': 'signed_purchase_order_pdf', 'document_id': str(foreign_source.pk), 's3_key': foreign_source.s3_key}]
        order.save(update_fields=['attachments'])
        with patch('apps.procurement.services.requisition_approval_record.default_storage.open') as opened:
            self.assertEqual(self.client.get(self.url()).status_code, 404)
            opened.assert_not_called()

    def test_missing_pr_or_linked_po_original_reports_unavailable(self):
        missing_pr = self.client.get(self.url())
        self.assertEqual(missing_pr.status_code, 404)
        self.assertEqual(missing_pr.data['code'], 'approval_record_source_missing')
        self.assertEqual(missing_pr.data['source'], 'pr')
        self.assertEqual(missing_pr.data['recovery'], 'upload_original_pr')
        self.assertEqual(missing_pr.data['requisition_id'], str(self.pr.pk))
        self.assertNotIn('purchase_order_id', missing_pr.data)
        self.pr_source()
        order = self.order(approval_log=[{'stage': 'Signed PO document approval', 'evidence_document_id': 'missing-original'}])
        missing_po = self.client.get(self.url())
        self.assertEqual(missing_po.status_code, 404)
        self.assertEqual(missing_po.data['code'], 'approval_record_source_missing')
        self.assertEqual(missing_po.data['source'], 'po')
        self.assertEqual(missing_po.data['reason'], 'not_attached')
        self.assertEqual(missing_po.data['recovery'], 'upload_original_po')
        self.assertEqual(missing_po.data['purchase_order_id'], str(order.pk))

    def test_missing_original_recovery_uploads_to_existing_po_then_combines_actual_sources(self):
        self.pr_source(pdf_bytes('Actual original PR'))
        order = self.order(status='completed', approval_log=[{'stage': 'Signed PO document approval', 'evidence_document_id': 'missing-original'}])
        self.assertEqual(self.client.get(self.url()).data['source'], 'po')
        module = Module.objects.get(code='procurement_orders')
        for permission in module.permissions.filter(action__in=['create', 'update'], is_active=True):
            RolePermission.objects.get_or_create(role=self.role, permission=permission)
        cache.clear()
        content = pdf_bytes('Actual uploaded PO')
        fields = {
            'source_po_number': order.po_number, 'po_number': order.po_number,
            'source_pr_numbers': [self.pr.pr_number], 'source_page_count': 1,
            'extracted_page_count': 1, 'extraction_truncated': False,
            'po_date': date(2026, 9, 17), 'vendor_name': self.vendor.name,
            'vendor_license_no': '', 'seller_reference': '', 'quote_ref': '',
            'project_number': '', 'summary': 'Source PO description', 'payment_terms': '',
            'payment_mode': '', 'delivery_terms': '', 'expected_delivery': None,
            'total_amount': Decimal('100.00'), 'tax_amount': Decimal('0.00'),
            'gross_amount': Decimal('100.00'), 'currency': order.currency, 'items': [],
        }
        def upload():
            with patch('apps.procurement.services.signed_po_pdf_import.extract_signed_po_fields', return_value=deepcopy(fields)):
                return self.client.post('/api/v1/procurement/po-documents/import_signed_pdf/', {
                    'file': SimpleUploadedFile('original-po.pdf', content, content_type='application/pdf'),
                    'pr_id': str(self.pr.pk), 'reviewed_fields': json.dumps({'summary': fields['summary']}),
                }, format='multipart')

        saved = upload()
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(saved.data['purchase_order_id'], str(order.pk))
        self.assertEqual(self.labels(self.read()[1]), ['Actual original PR', 'Actual uploaded PO'])
        order.refresh_from_db()
        self.assertEqual(order.status, 'completed')
        self.assertEqual(order.total_amount, Decimal('100.00'))
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        document = PODocument.objects.get(confirmed_po=order)
        original_document_id, original_key = document.pk, document.s3_key
        default_storage.delete(original_key)
        missing = self.client.get(self.url())
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.data['reason'], 'file_missing')
        restored = upload()
        self.assertEqual(restored.status_code, 200, restored.data)
        document.refresh_from_db()
        self.assertEqual(document.pk, original_document_id)
        self.assertEqual(document.confirmed_po_id, order.pk)
        self.assertNotEqual(document.s3_key, original_key)
        self.assertEqual(document.extracted_data['source_storage_history'][-1]['previous_storage_key'], original_key)
        self.assertEqual(self.labels(self.read()[1]), ['Actual original PR', 'Actual uploaded PO'])
        self.assertEqual(PODocument.objects.count(), 1)
        order.refresh_from_db()
        self.assertEqual(order.status, 'completed')
        self.assertEqual(order.total_amount, Decimal('100.00'))

        invalid_key = 'https://external.example.test/private-original.pdf'
        document.s3_key = invalid_key
        document.save(update_fields=['s3_key'])
        self.assertEqual(self.client.get(self.url()).data['reason'], 'invalid_reference')
        with patch('apps.procurement.services.signed_po_pdf_import.default_storage.open',
                   wraps=default_storage.open) as opened:
            restored = upload()
        self.assertEqual(restored.status_code, 200, restored.data)
        self.assertNotIn(invalid_key, [call.args[0] for call in opened.call_args_list])
        document.refresh_from_db()
        self.assertEqual(self.labels(self.read()[1]), ['Actual original PR', 'Actual uploaded PO'])

        # Existing bytes with the wrong digest must never be overwritten by recovery.
        with default_storage.open(document.s3_key, 'wb') as stored:
            stored.write(b'%PDF-different-content')
        conflict = upload()
        self.assertEqual(conflict.status_code, 409, conflict.data)
        with default_storage.open(document.s3_key, 'rb') as stored:
            self.assertEqual(stored.read(), b'%PDF-different-content')

    def test_missing_stored_po_file_reports_upload_recovery_not_storage_retry(self):
        self.pr_source()
        order = self.order()
        document, _ = self.po_source(order)
        default_storage.delete(document.s3_key)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data['source'], 'po')
        self.assertEqual(response.data['reason'], 'file_missing')
        self.assertEqual(response.data['recovery'], 'upload_original_po')

    def test_app_created_po_uses_official_renderer_without_uploading_or_recording_approval(self):
        self.pr_source(pdf_bytes('Original uploaded PR'))
        order = self.order(status='draft')
        before = PurchaseOrder.objects.values().get(pk=order.pk)
        from apps.procurement.services.purchase_order_exports import build_purchase_order_pdf
        with patch('apps.procurement.services.purchase_order_exports.build_purchase_order_pdf',
                   wraps=build_purchase_order_pdf) as renderer:
            response, content = self.read()
        self.assertEqual(response['X-Approval-Record-PO-Source'], 'radai_generated')
        self.assertEqual(response['X-Approval-Record-PR-Source'], 'uploaded_original')
        renderer.assert_called_once()
        pages = self.labels(content)
        self.assertEqual(pages[0], 'Original uploaded PR')
        self.assertIn(order.po_number, '\n'.join(pages[1:]))
        self.assertIn(order.title, '\n'.join(pages[1:]))
        self.assertIn('PO status: Draft', '\n'.join(pages[1:]))
        self.assertIn('Approval pending:', '\n'.join(pages[1:]))
        self.assertNotIn('Jarmo Suominen', '\n'.join(pages[1:]))
        self.assertNotIn('Approved by:', '\n'.join(pages[1:]))
        self.assertEqual(PurchaseOrder.objects.values().get(pk=order.pk), before)
        self.assertFalse(PODocument.objects.exists())

    def test_excel_data_without_pdf_provenance_can_use_official_po_renderer(self):
        self.pr_source(pdf_bytes('Original PR'))
        self.order(attachments=[{'type': 'po_excel_import_source', 'source_sheet': 'PO register', 'filename': 'register.xlsx'}])
        response, content = self.read()
        self.assertEqual(response['X-Approval-Record-PO-Source'], 'radai_generated')
        self.assertEqual(self.labels(content)[0], 'Original PR')
        self.assertNotIn('X-PO-Attachment-Warnings', response)
        self.assertNotIn('Attachment 1', '\n'.join(self.labels(content)))

    def test_missing_retained_pdf_never_falls_back_to_official_generated_po(self):
        self.pr_source()
        order = self.order()
        document, _ = self.po_source(order)
        default_storage.delete(document.s3_key)
        with patch('apps.procurement.services.purchase_order_exports.build_purchase_order_pdf') as renderer:
            response = self.client.get(self.url())
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data['source'], 'po')
        renderer.assert_not_called()

    def test_missing_current_pr_does_not_silently_substitute_older_original(self):
        self.pr_source(pdf_bytes('Older PR'), current=False)
        current, _ = self.pr_source(pdf_bytes('Current PR'))
        default_storage.delete(current['storage_key'])
        self.assertEqual(self.client.get(self.url()).status_code, 404)

    def test_arbitrary_url_or_other_pr_path_is_never_opened(self):
        for attachment in (
            {'type': 'signed_purchase_requisition_pdf', 'url': 'https://external.example.test/original.pdf'},
            {'type': 'signed_purchase_requisition_pdf', 'storage_key': f'procurement/signed_requisitions/{self.foreign_pr.pk}/2026/secret.pdf'},
        ):
            self.pr.attachments = [attachment]
            self.pr.save(update_fields=['attachments'])
            with patch('apps.procurement.services.requisition_approval_record.default_storage.open') as opened:
                self.assertEqual(self.client.get(self.url()).status_code, 404)
                opened.assert_not_called()

    def test_malformed_and_password_protected_originals_return_explicit_error(self):
        for content in (b'%PDF-1.7 corrupt original', pdf_bytes('Protected PR', password='private')):
            self.pr_source(content)
            response = self.client.get(self.url())
            self.assertEqual(response.status_code, 409, response.data)
            self.assertNotIn('private', str(response.data))

    def test_original_digest_change_is_not_silently_displayed(self):
        source, _ = self.pr_source()
        self.pr.attachments[0]['sha256'] = 'a' * 64
        self.pr.save(update_fields=['attachments'])
        self.assertEqual(self.client.get(self.url()).status_code, 409)

    def test_storage_failure_is_explicit_without_disclosing_internal_paths(self):
        self.pr_source()
        with patch('apps.procurement.services.requisition_approval_record.default_storage.open', side_effect=OSError('private-storage-details')):
            response = self.client.get(self.url())
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private-storage-details', str(response.data))

    def test_po_read_permission_is_checked_before_loading_either_original(self):
        self.pr_source()
        self.po_source(self.order())
        self.revoke('procurement_orders')
        with patch('apps.procurement.services.requisition_approval_record.default_storage.open') as opened:
            self.assertEqual(self.client.get(self.url()).status_code, 403)
            opened.assert_not_called()

    def test_po_owner_access_remains_available_but_explicit_read_deny_wins(self):
        self.pr_source()
        order = self.order()
        self.po_source(order)
        order.created_by = self.user
        order.save(update_fields=['created_by'])
        self.revoke('procurement_orders')
        self.read()
        permission = Permission.objects.filter(module__code='procurement_orders', action='read', is_active=True).first()
        UserPermissionOverride.objects.create(user_profile=self.profile, permission=permission, allowed=False)
        cache.clear()
        self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_pr_read_denial_and_anonymous_access_do_not_open_storage(self):
        self.pr_source()
        permission = Permission.objects.filter(module__code='procurement_requisitions', action='read', is_active=True).first()
        UserPermissionOverride.objects.create(user_profile=self.profile, permission=permission, allowed=False)
        cache.clear()
        with patch('apps.procurement.services.requisition_approval_record.default_storage.open') as opened:
            self.assertEqual(self.client.get(self.url()).status_code, 403)
            self.client.force_authenticate(None)
            self.assertIn(self.client.get(self.url()).status_code, (401, 403))
            opened.assert_not_called()
