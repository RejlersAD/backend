"""A paired signed PR/PO import commits both records and their independent sources."""

from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
from unittest.mock import patch

import pymupdf

from django.conf import settings
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.procurement.models import PODocument, PurchaseOrder, PurchaseRequisition, Vendor
from apps.procurement.services.signed_po_pdf_import import SignedPOImportError
from apps.rbac.models import Module, Permission, RoleModule, RolePermission
from apps.rbac.module_actions import ensure_module_actions

from . import test_signed_po_originating_pr as originating


@override_settings(ROOT_URLCONF=originating.__name__)
class PairedSignedImportTests(TestCase):
    def setUp(self):
        originating.SignedPOOriginatingPRTests.setUp(self)
        self.pr_number = self.pr.pr_number
        self.pr.delete()
        self.user.first_name, self.user.last_name = 'Pair', 'Issuer'
        self.user.save(update_fields=['first_name', 'last_name'])
        self.grant('procurement_requisitions', ['read', 'create', 'update'])
        self.pr_fields = {
            'pr_number': self.pr_number, 'issued_by_name': self.user.get_full_name(),
            'issued_date': date(2026, 1, 5), 'product_service': 'PR source description',
            'supplier_name': self.vendor.name, 'supplier_business_id': '',
            'project_department': '', 'project_number': '', 'project_numbers': [],
            'description_reason': 'Signed PR source reason', 'preferred_supplier': self.vendor.name,
            'price_lines': [], 'net_total': Decimal('100.00'), 'currency': 'USD',
            'budget_in_aed': '', 'net_total_aed': '', 'po_reference': self.fields['source_po_number'],
            'special_notes': '', 'attachment_reference': '', 'icv': '', 'extraction_issues': [],
        }
        self.pr_reviewed = {
            key: value.isoformat() if isinstance(value, date) else str(value)
            for key, value in self.pr_fields.items()
            if key in ('pr_number', 'issued_by_name', 'issued_date', 'product_service', 'supplier_name',
                       'description_reason', 'net_total', 'currency')
        }
        self.pr_evidence = {
            'table_detected': True, 'signatures': {role: True for role in ('pm', 'moe', 'mop', 'vp')},
            'approver_names': {role: f'PR Source {role.upper()}' for role in ('pm', 'moe', 'mop', 'vp')},
            'date_present': True, 'approval_date': date(2026, 1, 6),
        }
        for name, factory in (
            ('extract_signed_pr_fields', lambda *args, **kwargs: deepcopy(self.pr_fields)),
            ('detect_approval_evidence', lambda *args, **kwargs: deepcopy(self.pr_evidence)),
        ):
            patcher = patch(f'apps.procurement.services.signed_pr_pdf_import.{name}', side_effect=factory)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.pr_content = b'%PDF-1.4 independent PR signed source'
        self.po_reviewed = {'po_number': self.fields['po_number'], 'summary': self.fields['summary'],
                            'vendor_name': self.fields['vendor_name'], 'po_date': '2026-01-07',
                            'currency': self.fields['currency']}
        self.before_files = self.source_files()

    def grant(self, code, actions):
        module, _ = Module.objects.get_or_create(code=code, defaults={'name': code})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        RoleModule.objects.get_or_create(role=self.role, module=module)
        for permission in module.permissions.filter(action__in=actions, is_active=True):
            RolePermission.objects.get_or_create(role=self.role, permission=permission)
        cache.clear()

    def revoke(self, module, action):
        RolePermission.objects.filter(role=self.role, permission__module__code=module,
                                      permission__action=action).delete()
        cache.clear()

    def source_files(self):
        return {str(path) for path in Path(settings.MEDIA_ROOT).rglob('*') if path.is_file()}

    def upload(self, *, paired=True, **changes):
        data = {
            'file': SimpleUploadedFile('signed-pr.pdf', self.pr_content, content_type='application/pdf'),
            'create_new': 'true', 'manual_overrides': json.dumps(self.pr_reviewed),
        }
        if paired:
            data.update(
                po_file=SimpleUploadedFile('signed-po.pdf', self.content, content_type='application/pdf'),
                po_reviewed_fields=json.dumps(self.po_reviewed),
                po_signature_verified='true', po_stamp_verified='true',
                po_approved_by_name='Independent PO Signer', po_approved_by_title='PO Director',
                po_approved_date='2026-01-08',
            )
        data.update(changes)
        return self.client.post(f'{originating.BASE}requisitions/import-signed-pdf/', data, format='multipart')

    def assert_no_pair(self):
        self.assertFalse(PurchaseRequisition.objects.exists())
        self.assertFalse(PurchaseOrder.objects.exists())
        self.assertFalse(PODocument.objects.exists())
        self.assertEqual(self.source_files(), self.before_files)

    def test_one_save_creates_both_tables_links_them_and_preserves_separate_signed_evidence(self):
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.data)
        pr = PurchaseRequisition.objects.get(pk=response.data['requisition_id'])
        po = PurchaseOrder.objects.get(pk=response.data['purchase_order_id'])
        self.assertEqual(po.pr_reference_id, pr.pk)
        self.assertFalse(response.data['po_link']['manual_link_required'])
        self.assertEqual(response.data['purchase_order']['purchase_order_id'], str(po.pk))
        self.assertEqual(pr.status, 'converted')
        self.assertTrue(pr.price_remarks_data['signed_document_verification']['signed_off'])
        self.assertEqual(pr.approval_workflow_config[-1]['user_name'], 'PR Source VP')
        self.assertEqual(po.approved_by_name, 'Independent PO Signer')
        self.assertEqual(po.approved_date, date(2026, 1, 8))
        self.assertEqual(pr.total_price, Decimal('100.00'))
        self.assertEqual(po.total_amount, Decimal('100.00'))
        with default_storage.open(pr.attachments[0]['storage_key'], 'rb') as source:
            self.assertEqual(source.read(), self.pr_content)
        with default_storage.open(PODocument.objects.get().s3_key, 'rb') as source:
            self.assertEqual(source.read(), self.content)
        source_response = self.client.get(f'{originating.BASE}requisitions/{pr.pk}/uploaded-documents/0/content/')
        self.assertEqual(source_response.status_code, 200)
        self.assertEqual(b''.join(source_response.streaming_content), self.pr_content)

    def test_pair_preview_returns_independent_po_evidence_without_storing_anything(self):
        evidence = {'approved_by_name': 'Preview PO Signer', 'signature_detected': True,
                    'stamp_detected': False, 'issues': ['Verify the source PDF.']}
        with patch('apps.procurement.services.paired_signed_import.preview_signed_po_approval',
                   return_value={'approval_evidence': evidence, 'page_count': 2}):
            response = self.upload(preview_only='true')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['preview_only'])
        self.assertEqual(response.data['po_preview']['approval_evidence'], evidence)
        self.assertEqual(response.data['po_preview']['extracted_data']['po_number'], self.fields['po_number'])
        self.assert_no_pair()

    def test_created_pair_originals_are_readable_and_merged_in_pr_then_po_order(self):
        def pdf(label):
            with pymupdf.open() as document:
                for number in (1, 2):
                    page = document.new_page()
                    page.insert_text((40, 60), f'{label} ORIGINAL PAGE {number}')
                return document.tobytes()

        self.pr_content, self.content = pdf('PR'), pdf('PO')
        saved = self.upload()
        self.assertEqual(saved.status_code, 201, saved.data)
        url = f"{originating.BASE}requisitions/{saved.data['requisition_id']}/"
        original = self.client.get(url + 'uploaded-documents/0/content/')
        self.assertEqual(original.status_code, 200)
        self.assertEqual(b''.join(original.streaming_content), self.pr_content)
        merged = self.client.get(url + 'approval-record-pdf/')
        self.assertEqual(merged.status_code, 200)
        with pymupdf.open(stream=b''.join(merged.streaming_content), filetype='pdf') as document:
            self.assertEqual([page.get_text().strip() for page in document], [
                'PR ORIGINAL PAGE 1', 'PR ORIGINAL PAGE 2', 'PO ORIGINAL PAGE 1', 'PO ORIGINAL PAGE 2',
            ])

    def test_po_reviewed_fields_and_confirmed_vat_are_saved_before_materialization(self):
        reviewed = {**self.po_reviewed, 'summary': 'Reviewed PO title', 'entered_amount': '210.00', 'vat_basis': 'inclusive'}
        response = self.upload(po_reviewed_fields=json.dumps(reviewed))
        self.assertEqual(response.status_code, 201, response.data)
        po = PurchaseOrder.objects.get()
        self.assertEqual(po.title, 'Reviewed PO title')
        self.assertEqual(po.net_amount, Decimal('200.00'))
        self.assertEqual(po.total_amount, Decimal('210.00'))
        self.assertEqual(po.tax_amount, Decimal('10.00'))
        self.assertEqual(PurchaseRequisition.objects.get().total_price, Decimal('100.00'))

    def test_invalid_or_incomplete_po_rolls_back_pr_and_new_source_files(self):
        with patch('apps.procurement.services.signed_po_pdf_import.extract_signed_po_fields',
                   side_effect=SignedPOImportError('The PO cannot be read.')):
            response = self.upload()
        self.assertEqual(response.status_code, 400, response.data)
        self.assert_no_pair()
        self.fields['total_amount'] = Decimal('0.00')
        response = self.upload()
        self.assertEqual(response.status_code, 400, response.data)
        self.assert_no_pair()

    def test_pending_po_is_not_returned_as_success_and_both_new_sources_are_removed(self):
        self.fields['vendor_name'] = ''
        self.po_reviewed['vendor_name'] = ''
        response = self.upload()
        self.assertEqual(response.status_code, 400, response.data)
        self.assert_no_pair()

    def test_pr_only_save_keeps_existing_contract_without_po_permission(self):
        self.revoke('procurement_orders', 'create')
        response = self.upload(paired=False)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotIn('purchase_order_id', response.data)
        self.assertEqual(PurchaseRequisition.objects.count(), 1)
        self.assertFalse(PurchaseOrder.objects.exists())

    def test_missing_po_create_or_pr_create_permission_rejects_before_any_writes(self):
        self.revoke('procurement_orders', 'create')
        self.assertEqual(self.upload().status_code, 403)
        self.assert_no_pair()
        self.grant('procurement_orders', ['create'])
        self.revoke('procurement_requisitions', 'create')
        self.assertEqual(self.upload().status_code, 403)
        self.assert_no_pair()

    def test_existing_pr_requires_update_permission_and_rolls_back_on_po_failure(self):
        first = self.upload(paired=False)
        pr = PurchaseRequisition.objects.get(pk=first.data['pr_id'])
        before = PurchaseRequisition.objects.values().get(pk=pr.pk)
        before_files = self.source_files()
        self.revoke('procurement_requisitions', 'update')
        response = self.upload(create_new='false', expected_pr_number=pr.pr_number)
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(PurchaseRequisition.objects.values().get(pk=pr.pk), before)
        self.grant('procurement_requisitions', ['update'])
        with patch('apps.procurement.services.signed_po_pdf_import.extract_signed_po_fields',
                   side_effect=SignedPOImportError('Invalid PO.')):
            response = self.upload(create_new='false', expected_pr_number=pr.pr_number)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(PurchaseRequisition.objects.values().get(pk=pr.pk), before)
        self.assertEqual(self.source_files(), before_files)

    def test_existing_po_requires_update_permission_without_leaving_a_partial_pr_link(self):
        po = PurchaseOrder.objects.create(po_number=self.fields['po_number'], vendor=self.vendor,
                                          title='Existing completed PO', total_amount=100, status='completed')
        before = PurchaseOrder.objects.values().get(pk=po.pk)
        self.revoke('procurement_orders', 'update')
        response = self.upload()
        self.assertEqual(response.status_code, 403, response.data)
        self.assertFalse(PurchaseRequisition.objects.exists())
        self.assertEqual(PurchaseOrder.objects.values().get(pk=po.pk), before)
        self.assertFalse(PODocument.objects.exists())
        self.assertEqual(self.source_files(), self.before_files)

    def test_independent_po_confirmation_is_required_to_record_po_approval(self):
        response = self.upload(po_signature_verified='false', po_stamp_verified='false',
                               po_approved_by_name='', po_approved_date='')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data['document_signed_off'])
        po = PurchaseOrder.objects.get()
        self.assertEqual(po.approval_signature, '')
        self.assertEqual(po.approved_by_name, '')
        self.assertIsNone(po.approved_at)
        self.assertFalse(response.data['purchase_order']['signature_verified'])

    def test_invalid_po_approval_confirmation_is_not_replaced_by_pr_evidence(self):
        for changes in ({'po_approved_by_name': ''}, {'po_approved_date': 'invalid'}, {'po_signature_verified': 'yes please'}):
            with self.subTest(changes=changes):
                self.assertEqual(self.upload(**changes).status_code, 400)
                self.assert_no_pair()

    def test_printed_source_reference_conflicts_roll_back_both_records_and_files(self):
        self.pr_fields['po_reference'] = 'RAD-PRJ-PUR-9999_2026'
        response = self.upload()
        self.assertEqual(response.status_code, 409, response.data)
        self.assert_no_pair()
        self.pr_fields['po_reference'] = self.fields['source_po_number']
        self.fields['source_pr_numbers'] = ['RAD-PRJ-PR-9999_2026']
        response = self.upload()
        self.assertEqual(response.status_code, 409, response.data)
        self.assert_no_pair()

    def test_exact_retry_does_not_duplicate_or_rewrite_the_saved_pair(self):
        first = self.upload()
        self.assertEqual(first.status_code, 201, first.data)
        before_pr = PurchaseRequisition.objects.values().get()
        before_po = PurchaseOrder.objects.values().get()
        before_document = PODocument.objects.values().get()
        before_files = self.source_files()
        second = self.upload()
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(second.data['operation'], 'already_imported')
        self.assertEqual(second.data['purchase_order_id'], first.data['purchase_order_id'])
        self.assertEqual(PurchaseRequisition.objects.values().get(), before_pr)
        self.assertEqual(PurchaseOrder.objects.values().get(), before_po)
        self.assertEqual(PODocument.objects.values().get(), before_document)
        self.assertEqual(self.source_files(), before_files)

    def test_retry_reports_changed_current_pr_source_instead_of_claiming_old_pair_is_current(self):
        self.assertEqual(self.upload().status_code, 201)
        pr = PurchaseRequisition.objects.get()
        pr.price_remarks_data['signed_document_verification']['document_sha256'] = 'a' * 64
        pr.save(update_fields=['price_remarks_data'])
        response = self.upload()
        self.assertEqual(response.status_code, 409, response.data)
        self.assertIn('source changed', response.data['error'])

    def test_exact_pair_retry_restores_missing_same_digest_po_source_without_duplicate_records(self):
        first = self.upload()
        self.assertEqual(first.status_code, 201, first.data)
        document = PODocument.objects.get()
        previous_key = document.s3_key
        default_storage.delete(previous_key)
        self.revoke('procurement_orders', 'update')
        denied = self.upload()
        self.assertEqual(denied.status_code, 403, denied.data)
        self.assertFalse(default_storage.exists(previous_key))
        self.grant('procurement_orders', ['update'])
        restored = self.upload()
        self.assertEqual(restored.status_code, 200, restored.data)
        self.assertEqual(restored.data['operation'], 'already_imported')
        document.refresh_from_db()
        self.assertNotEqual(document.s3_key, previous_key)
        with default_storage.open(document.s3_key, 'rb') as source:
            self.assertEqual(source.read(), self.content)
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        self.assertEqual(PODocument.objects.count(), 1)

    def test_retry_retains_warnings_and_preserved_values_for_an_existing_completed_po(self):
        po = PurchaseOrder.objects.create(po_number=self.fields['po_number'], vendor=self.vendor,
                                          title='Existing completed PO', total_amount=200, status='completed')
        first = self.upload()
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(first.data['purchase_order']['operation'], 'attached')
        self.assertTrue(first.data['purchase_order']['reconciliation_required'])
        second = self.upload()
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(second.data['purchase_order']['operation'], 'attached')
        self.assertEqual(second.data['purchase_order']['reconciliation_issues'],
                         first.data['purchase_order']['reconciliation_issues'])
        po.refresh_from_db()
        self.assertEqual(po.status, 'completed')
        self.assertEqual(po.total_amount, Decimal('200.00'))

    def test_missing_supplier_is_registered_but_rolled_back_if_pair_cannot_complete(self):
        self.grant('procurement_vendors', ['create'])
        self.fields['vendor_name'] = 'Brand new source supplier'
        self.po_reviewed['vendor_name'] = self.fields['vendor_name']
        self.pr_fields['po_reference'] = 'RAD-PRJ-PUR-9999_2026'
        self.assertEqual(self.upload().status_code, 409)
        self.assertEqual(Vendor.objects.count(), 1)
        self.assert_no_pair()
        self.pr_fields['po_reference'] = self.fields['source_po_number']
        self.assertEqual(self.upload().status_code, 201)
        self.assertEqual(PurchaseOrder.objects.get().vendor.name, 'Brand new source supplier')
        self.assertEqual(Vendor.objects.count(), 2)

    def test_originating_pr_id_binds_preview_and_final_pair_to_existing_pr_without_register_read(self):
        original = self.upload(paired=False)
        pr = PurchaseRequisition.objects.get(pk=original.data['pr_id'])
        self.revoke('procurement_requisitions', 'read')
        with patch('apps.procurement.services.paired_signed_import.preview_signed_po_approval',
                   return_value={'approval_evidence': {}, 'page_count': 2}):
            preview = self.upload(preview_only='true', originating_pr_id=str(pr.pk))
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data['bound_pr_number'], pr.pr_number)
        result = self.upload(originating_pr_id=str(pr.pk))
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['pr_id'], str(pr.pk))
        self.assertEqual(PurchaseRequisition.objects.count(), 1)
        self.assertEqual(PurchaseOrder.objects.get().pr_reference_id, pr.pk)

    def test_originating_pr_id_rejects_different_expected_pr_and_requires_attachment_permission(self):
        original = self.upload(paired=False)
        pr = PurchaseRequisition.objects.get(pk=original.data['pr_id'])
        mismatch = self.upload(originating_pr_id=str(pr.pk), expected_pr_number='RAD-PRJ-PR-9999_2026')
        self.assertEqual(mismatch.status_code, 409, mismatch.data)
        self.assertFalse(PurchaseOrder.objects.exists())
        self.revoke('procurement_requisitions', 'update')
        self.assertEqual(self.upload(originating_pr_id=str(pr.pk)).status_code, 403)
        self.assertFalse(PurchaseOrder.objects.exists())
