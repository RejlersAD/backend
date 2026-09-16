"""Reviewed PDF imports use an isolated test database and mocked file storage."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.exceptions import ValidationError

from apps.procurement.models import PurchaseOrder, PurchaseRequisition, Vendor
from apps.procurement.services.requisition_conversion import RequisitionConversionService
from apps.procurement.serializers import PurchaseRequisitionSerializer
from apps.procurement.services.signed_pr_pdf_import import (
    SignedPRImportError, import_signed_pr_pdf, preview_signed_pr_pdf,
)
from apps.procurement.views import PurchaseRequisitionViewSet
from apps.rbac.models import Module, Organization, Permission, UserProfile
from apps.rbac.module_actions import ensure_module_actions


SERVICE = 'apps.procurement.services.signed_pr_pdf_import'


class SignedPRPdfCreationTests(TestCase):
    def setUp(self):
        self.issuer = get_user_model().objects.create_user(
            username='document-issuer', email='issuer@example.test',
            first_name='Document', last_name='Issuer',
        )
        self.reviewer = get_user_model().objects.create_user(
            username='pdf-reviewer', email='reviewer@example.test',
            first_name='PDF', last_name='Reviewer', is_superuser=True,
        )
        self.approvers = {}
        for role in ('pm', 'moe', 'mop', 'vp'):
            self.approvers[role] = get_user_model().objects.create_user(
                username=role, email=f'{role}@example.test', first_name=role.upper(), last_name='Approver',
            )
        self.vendor = Vendor.objects.create(vendor_code='PDF-001', name='Example Supplier', status='active')
        self.fields = {
            'pr_number': 'RAD-PRJ-PR-0002_2026', 'issued_by_name': 'Document Issuer',
            'issued_date': date(2026, 9, 15), 'product_service': 'Engineering service',
            'supplier_name': 'Example Supplier', 'supplier_business_id': '',
            'project_department': '5900985 EPC project', 'project_number': '5900985',
            'project_numbers': ['5900985'], 'description_reason': 'Engineering service for the EPC project',
            'preferred_supplier': 'Example Supplier', 'price_lines': [],
            'net_total': Decimal('1250.50'), 'currency': 'AED', 'budget_in_aed': '',
            'net_total_aed': '1250.50', 'po_reference': '', 'special_notes': '',
            'attachment_reference': '', 'icv': '', 'extraction_issues': [],
        }
        self.reviewed = {
            key: value.isoformat() if isinstance(value, date) else str(value)
            for key, value in self.fields.items()
            if key in ('pr_number', 'issued_by_name', 'issued_date', 'product_service',
                       'supplier_name', 'description_reason', 'net_total', 'currency')
        }
        self.evidence = {
            'table_detected': True, 'signatures': {role: True for role in self.approvers},
            'approver_names': {role: user.get_full_name() for role, user in self.approvers.items()},
            'date_present': True, 'approval_date': date(2026, 9, 14),
        }
        self.extract = self._patch('extract_signed_pr_fields', side_effect=lambda *args, **kwargs: deepcopy(self.fields))
        self.detect = self._patch('detect_approval_evidence', side_effect=lambda *args: deepcopy(self.evidence))
        self.storage = self._patch('default_storage')
        self.storage.save.return_value = 'test-only/signed.pdf'
        self.storage.url.return_value = '/test-only/signed.pdf'

    def _patch(self, name, **kwargs):
        patcher = patch(f'{SERVICE}.{name}', **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def _import(self, **kwargs):
        arguments = {'filename': 'signed.pdf', 'uploaded_by': self.reviewer,
                     'manual_overrides': self.reviewed, 'create_new': True}
        arguments.update(kwargs)
        return import_signed_pr_pdf(b'%PDF-test', **arguments)

    def test_missing_preview_offers_creation_without_modifying_database_or_storage(self):
        result = preview_signed_pr_pdf(b'%PDF-test', filename='signed.pdf')
        self.assertTrue(result['can_create'])
        self.assertFalse(result['database_match'])
        self.assertEqual(result['mapping_issues'], [])
        self.assertFalse(PurchaseRequisition.objects.exists())
        self.storage.save.assert_not_called()

    def test_missing_record_requires_explicit_creation_choice(self):
        with self.assertRaisesMessage(SignedPRImportError, 'Create reviewed PR'):
            self._import(create_new=False)
        self.assertFalse(PurchaseRequisition.objects.exists())
        self.storage.save.assert_not_called()

    def test_reviewed_creation_records_document_issuer_reviewer_and_real_evidence(self):
        result = self._import()
        pr = PurchaseRequisition.objects.get(pk=result['pr_id'])
        self.assertTrue(result['created'])
        self.assertEqual(pr.pr_number, self.fields['pr_number'])
        self.assertEqual(pr.attachments[0]['storage_key'], self.storage.save.return_value)
        self.assertTrue(self.storage.save.call_args.args[0].startswith(
            f'procurement/signed_requisitions/{pr.pk}/2026/',
        ))
        self.assertEqual(pr.issued_by, self.issuer)
        self.assertEqual(pr.requested_by, self.issuer)
        self.assertEqual(pr.vendor, self.vendor)
        self.assertEqual(pr.total_price, Decimal('1250.50'))
        self.assertEqual(pr.status, 'approved')
        self.assertEqual(pr.approved_by, self.approvers['vp'])
        self.assertEqual(pr.approved_at.date(), self.evidence['approval_date'])
        self.assertEqual(len(pr.approval_workflow_config), 4)
        self.assertEqual(pr.price_remarks_data['manual_ocr_review']['reviewed_by_id'], str(self.reviewer.pk))
        self.assertEqual(pr.price_remarks_data['import_operation'], 'create')
        self.assertEqual(len(pr.attachments), 1)
        self.assertTrue(pr.attachments[0]['sha256'])

    def test_creation_with_incomplete_signatures_remains_draft_even_if_verified_flag_true(self):
        self.evidence['signatures']['vp'] = False
        result = self._import(signatures_verified=True)
        pr = PurchaseRequisition.objects.get(pk=result['pr_id'])
        self.assertEqual(pr.status, 'draft')
        self.assertIsNone(pr.approved_at)
        self.assertEqual(pr.vp_op_approval_status, 'pending')
        self.assertFalse(result['signature_verified'])
        self.assertTrue(any('fully signed' in issue for issue in result['workflow_issues']))
        self.assertEqual(pr.approval_workflow_config, [])
        history = pr.price_remarks_data['signed_document_verification']['source_approval_rows']
        self.assertEqual([row['role_key'] for row in history], ['pm', 'moe', 'mop', 'vp'])
        self.assertEqual([row['status'] for row in history], ['approved'] * 3 + ['not_recorded'])
        self.assertEqual([row['signature_verified'] for row in history], [True] * 3 + [False])
        self.assertEqual(history[-1]['user_name'], self.approvers['vp'].get_full_name())

    def test_reviewed_import_persists_source_rows_remarks_and_evidence(self):
        self.fields['price_lines'] = [
            {'description': 'Engineering', 'total': '1000.00', 'currency': 'AED', 'remarks': 'Sales Budget AED 5,000.00'},
            {'description': 'Site support', 'total': '250.50', 'currency': 'AED', 'remarks': 'Two site visits'},
        ]
        self.fields['price_remarks'] = 'Sales Budget AED 5,000.00 | Two site visits'
        self.fields['field_provenance'] = {'price': {'source': 'labeled_net_total', 'evidence': 'Net Total AED 1,250.50'}}
        self.fields['extraction_method'] = 'native'
        self.fields['extraction_pages'] = [{'page': 1, 'method': 'native', 'characters': 500}]
        result = self._import()
        pr = PurchaseRequisition.objects.get(pk=result['pr_id'])
        self.assertEqual(pr.items, [
            {**line, 'quantity': '1', 'unit': 'LS', 'unit_price': line['total']}
            for line in self.fields['price_lines']
        ])
        self.assertEqual(pr.price_remarks, self.fields['price_remarks'])
        self.assertEqual(pr.total_price, Decimal('1250.50'))
        self.assertEqual(pr.price_remarks_data['ocr_source_price_lines'], self.fields['price_lines'])
        self.assertEqual(pr.price_remarks_data['ocr_text_method'], 'native')
        self.assertEqual(pr.price_remarks_data['ocr_field_provenance']['net_total']['evidence'], 'Net Total AED 1,250.50')

    def test_imported_total_only_item_survives_normal_draft_form_save(self):
        self.evidence['signatures']['vp'] = False
        self.fields['price_lines'] = [{
            'description': 'Recorded lump sum', 'total': '1250.50', 'currency': 'AED',
        }]
        result = self._import()
        pr = PurchaseRequisition.objects.get(pk=result['pr_id'])
        verification = deepcopy(pr.price_remarks_data['signed_document_verification'])
        evidence = deepcopy(pr.price_remarks_data['signed_approval_evidence'])
        source_rows = deepcopy(pr.price_remarks_data['ocr_source_price_lines'])
        self.assertEqual(pr.items[0]['quantity'], '1')
        self.assertEqual(pr.items[0]['unit'], 'LS')
        self.assertEqual(pr.items[0]['unit_price'], '1250.50')
        self.assertNotIn('quantity', source_rows[0])
        serializer = PurchaseRequisitionSerializer(pr, data={
            'description_reason': 'Corrected draft explanation', 'items': pr.items,
            'price_remarks_data': {'payment_terms': 'Net 30'},
        }, partial=True, context={'request': SimpleNamespace(user=self.issuer)})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        pr.refresh_from_db()
        self.assertEqual(pr.status, 'draft')
        self.assertEqual(pr.total_price, Decimal('1250.50'))
        self.assertEqual(pr.items[0]['total'], '1250.50')
        self.assertEqual(pr.price_remarks_data['signed_document_verification'], verification)
        self.assertEqual(pr.price_remarks_data['signed_approval_evidence'], evidence)

    def test_import_does_not_rewrite_partial_or_conflicting_quantity_rate_evidence(self):
        for pricing in ({'quantity': '2'}, {'unit_price': '10.00'},
                        {'qty': '2'}, {'price': '10.00'},
                        {'quantity': '2', 'unit_price': '10.00'}):
            with self.subTest(pricing=pricing):
                self.fields['price_lines'] = [{
                    'description': 'Incomplete pricing', 'total': '1250.50', 'currency': 'AED', **pricing,
                }]
                result = self._import()
                pr = PurchaseRequisition.objects.get(pk=result['pr_id'])
                self.assertNotIn('unit', pr.items[0])
                for key, value in pricing.items():
                    self.assertEqual(pr.items[0][key], value)
                serializer = PurchaseRequisitionSerializer(pr, data={'items': pr.items}, partial=True)
                self.assertFalse(serializer.is_valid())
                self.assertIn('items', serializer.errors)
                pr.delete()

    def test_signed_creation_without_approval_date_is_approved_with_actionable_date_issue(self):
        self.evidence['approval_date'] = None
        self.evidence['date_present'] = False
        result = self._import()
        self.assertEqual(result['status'], 'approved')
        pr = PurchaseRequisition.objects.get(pk=result['pr_id'])
        self.assertIsNone(pr.approved_at)
        self.assertTrue(all(stage['status'] == 'approved' for stage in pr.approval_workflow_config))
        self.assertTrue(any('approval date' in issue for issue in result['workflow_issues']))

    def test_signed_document_keeps_external_signer_name_without_requiring_a_radai_account(self):
        self.evidence['approver_names']['vp'] = 'ZXQ 123456'
        result = self._import()
        self.assertEqual(result['status'], 'approved')
        pr = PurchaseRequisition.objects.get(pk=result['pr_id'])
        self.assertIsNone(pr.vp_op_name)
        self.assertIsNone(pr.approved_by)
        stage = pr.approval_workflow_config[-1]
        self.assertEqual(stage['user_name'], 'ZXQ 123456')
        self.assertIsNone(stage['user_id'])
        self.assertEqual(stage['status'], 'approved')
        self.assertTrue(stage['external'])

    def _use_three_signed_source_rows(self):
        self.evidence['approval_rows'] = [
            {'source_role': source_role, 'role_key': role, 'name': self.approvers[role].get_full_name(),
             'signature_detected': True, 'page': 1}
            for source_role, role in (('PD', 'pm'), ('MoP', 'mop'), ('VP, Op', 'vp'))
        ]
        self.evidence['signatures']['moe'] = False
        self.evidence['approver_names']['moe'] = ''

    def test_three_signed_source_rows_approve_without_inventing_a_missing_moe_stage(self):
        self._use_three_signed_source_rows()
        result = self._import()
        pr = PurchaseRequisition.objects.get(pk=result['pr_id'])
        self.assertEqual(pr.status, 'approved')
        self.assertTrue(result['document_signed_off'])
        self.assertFalse(result['approval_detection']['all_four_signatures'])
        self.assertEqual([stage['role'] for stage in pr.approval_workflow_config], ['PD', 'MoP', 'VP, Op'])
        self.assertTrue(all(stage['status'] == 'approved' and stage['external'] for stage in pr.approval_workflow_config))
        self.assertIsNone(pr.eng_manager_name)
        self.assertEqual(pr.current_approval_step, 3)

    def _excel_record(self):
        return PurchaseRequisition.objects.create(
            pr_number=self.fields['pr_number'], title='Excel title', product_service='Excel service',
            issued_by=self.issuer, requested_by=self.issuer, issued_date=date(2025, 12, 1),
            required_date=date(2026, 1, 25), vendor=self.vendor, supplier_name='Excel supplier text',
            supplier_business_id='EXCEL-ID', project='5900985', project_department='Original Excel project',
            description_reason='Excel original requirement', preferred_supplier_if_any='Excel preferred supplier',
            currency='USD', total_price=Decimal('2500.00'), net_total_excl_vat=Decimal('2500.00'),
            estimated_budget=Decimal('3500.00'), purchase_recommendation='Excel source notes',
            items=[{'description': 'Excel line', 'quantity': 2, 'unit_price': '1250.00', 'total': '2500.00'}],
            price_remarks='Excel pricing remarks', status='draft',
            attachments=[{'type': 'pr_excel_import_source', 'filename': 'reference.xlsx'}],
            price_remarks_data={'import_source': 'pr_excel', 'payment_terms': 'Net 30', 'icv': 'Excel ICV',
                                'budget_in_aed': '12853.75', 'net_total_aed': '9181.25',
                                'price_lines': [{'description': 'Original metadata row', 'total': '2500.00'}],
                                'project_numbers': ['5900985'], 'attachment_reference': 'Original Excel attachment'},
        )

    def _attach(self, existing, **kwargs):
        return self._import(create_new=False, attach_only=True, expected_pr_number=existing.pr_number,
                            manual_overrides=kwargs.pop('manual_overrides', None), **kwargs)

    def test_attaching_signed_pdf_preserves_excel_business_fields_and_commercial_metadata(self):
        self._use_three_signed_source_rows()
        existing = self._excel_record()
        preserved_names = (
            'title', 'product_service', 'issued_by_id', 'requested_by_id', 'issued_date', 'required_date',
            'vendor_id', 'supplier_name', 'supplier_business_id', 'project', 'project_department',
            'description_reason', 'preferred_supplier_if_any', 'currency', 'total_price', 'net_total_excl_vat',
            'estimated_budget', 'purchase_recommendation', 'items', 'price_remarks',
        )
        before = {name: deepcopy(getattr(existing, name)) for name in preserved_names}
        metadata_before = deepcopy(existing.price_remarks_data)
        result = self._attach(existing)
        existing.refresh_from_db()
        self.assertEqual({name: getattr(existing, name) for name in preserved_names}, before)
        for key, value in metadata_before.items():
            self.assertEqual(existing.price_remarks_data[key], value, key)
        self.assertEqual(existing.status, 'approved')
        self.assertEqual([stage['role'] for stage in existing.approval_workflow_config], ['PD', 'MoP', 'VP, Op'])
        self.assertTrue(result['document_comparison']['has_mismatches'])
        self.assertTrue(existing.price_remarks_data['signed_document_verification']['attach_only'])
        self.assertEqual(len(existing.attachments), 2)
        self.assertEqual(existing.attachments[0]['filename'], 'reference.xlsx')

    def test_attach_only_rejects_raw_identity_mismatch_even_after_manual_number_override(self):
        existing = self._excel_record()
        self.fields['pr_number'] = 'RAD-PRJ-PR-9999_2026'
        with self.assertRaisesMessage(SignedPRImportError, 'edited record'):
            self._attach(existing, manual_overrides={**self.reviewed, 'pr_number': existing.pr_number})
        existing.refresh_from_db()
        self.assertEqual(existing.status, 'draft')
        self.assertEqual(len(existing.attachments), 1)
        self.storage.save.assert_not_called()

    def test_attach_only_rejects_identity_inferred_from_filename_or_manual_review(self):
        existing = self._excel_record()
        for provenance in ('filename', 'manual_review'):
            with self.subTest(provenance=provenance):
                self.fields['field_provenance'] = {'pr_number': {'source': provenance}}
                with self.assertRaisesMessage(SignedPRImportError, 'does not match'):
                    self._attach(existing)
        self.storage.save.assert_not_called()

    def test_repeated_attachment_preserves_excel_file_and_stores_signed_pdf_once(self):
        self._use_three_signed_source_rows()
        existing = self._excel_record()
        first = self._attach(existing)
        second = self._attach(existing)
        existing.refresh_from_db()
        self.assertEqual(len(existing.attachments), 2)
        self.assertEqual(self.storage.save.call_count, 1)
        self.assertEqual(first['pr_id'], second['pr_id'])
        self.assertEqual(existing.status, 'approved')

    def test_three_signed_source_rows_can_convert_without_an_extra_radai_approval_route(self):
        self._use_three_signed_source_rows()
        result = self._import()
        converted_pr, order = RequisitionConversionService.convert(result['pr_id'], self.reviewer)
        self.assertEqual(converted_pr.status, 'converted')
        self.assertEqual(order.pr_reference_id, converted_pr.pk)
        self.assertEqual([stage['stage'] for stage in order.approval_log], ['PD', 'MoP', 'VP, Op'])
        self.assertTrue(all(stage['status'] == 'Approved' for stage in order.approval_log))
        self.assertEqual(order.total_amount, Decimal('1250.50'))
        self.assertEqual(PurchaseOrder.objects.count(), 1)

    def test_unresolved_attach_only_financial_difference_blocks_po_creation(self):
        self._use_three_signed_source_rows()
        existing = self._excel_record()
        self._attach(existing)
        with self.assertRaises(ValidationError) as caught:
            RequisitionConversionService.convert(existing.pk, self.reviewer)
        self.assertIn('signed', str(caught.exception).lower())
        self.assertFalse(PurchaseOrder.objects.exists())
        existing.refresh_from_db()
        self.assertEqual(existing.status, 'approved')

    def test_reconciled_excel_values_allow_conversion_after_signed_pdf_comparison_is_rechecked(self):
        self._use_three_signed_source_rows()
        existing = self._excel_record()
        self._attach(existing)
        existing.refresh_from_db()
        existing.product_service = self.fields['product_service']
        existing.issued_date = self.fields['issued_date']
        existing.supplier_name = self.fields['supplier_name']
        existing.currency = self.fields['currency']
        existing.total_price = self.fields['net_total']
        existing.net_total_excl_vat = self.fields['net_total']
        existing.items = [{'description': 'Reviewed line', 'quantity': 1, 'unit_price': '1250.50', 'total': '1250.50'}]
        existing.save()
        converted_pr, order = RequisitionConversionService.convert(existing.pk, self.reviewer)
        self.assertEqual(converted_pr.status, 'converted')
        self.assertEqual(order.total_amount, Decimal('1250.50'))

    def test_normal_form_edit_preserves_completed_source_workflow_and_protected_pdf_evidence(self):
        self._use_three_signed_source_rows()
        result = self._import()
        existing = PurchaseRequisition.objects.get(pk=result['pr_id'])
        workflow_before = deepcopy(existing.approval_workflow_config)
        evidence_before = deepcopy(existing.price_remarks_data['signed_document_verification'])
        serializer = PurchaseRequisitionSerializer(existing, data={
            'description_reason': 'Updated commercial explanation',
            'approval_workflow_config': [{'role': 'New internal approval', 'status': 'pending'}],
            'price_remarks_data': {'payment_terms': 'Net 30', 'signed_document_verification': {
                'signed_off': False, 'comparison': {'has_mismatches': False},
            }, 'signed_approval_evidence': {}, 'signed_pdf_attached': False, 'po_link': {}},
        }, partial=True, context={'request': SimpleNamespace(user=self.issuer)})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        existing.refresh_from_db()
        self.assertEqual(existing.approval_workflow_config, workflow_before)
        self.assertEqual(existing.price_remarks_data['signed_document_verification'], evidence_before)
        self.assertEqual(existing.status, 'approved')
        self.assertEqual(existing.description_reason, 'Updated commercial explanation')

    def test_form_json_cannot_forge_signed_document_verification(self):
        existing = self._excel_record()
        serializer = PurchaseRequisitionSerializer(existing, data={'price_remarks_data': {
            'signed_document_verification': {'signed_off': True},
            'signed_approval_evidence': {'signatures': {'pm': True}}, 'signed_pdf_attached': True,
        }}, partial=True, context={'request': SimpleNamespace(user=self.issuer)})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        existing.refresh_from_db()
        self.assertEqual(existing.status, 'draft')
        self.assertNotIn('signed_document_verification', existing.price_remarks_data)
        self.assertNotIn('signed_approval_evidence', existing.price_remarks_data)
        self.assertNotIn('signed_pdf_attached', existing.price_remarks_data)

    def test_signed_pdf_creation_later_commercial_edit_requires_source_reconciliation(self):
        self._use_three_signed_source_rows()
        result = self._import()
        existing = PurchaseRequisition.objects.get(pk=result['pr_id'])
        existing.currency = 'USD'
        existing.save(update_fields=['currency'])
        with self.assertRaisesMessage(ValidationError, 'signed PDF'):
            RequisitionConversionService.convert(existing.pk, self.reviewer)
        self.assertFalse(PurchaseOrder.objects.exists())

    def test_conversion_checks_actual_total_price_even_when_net_total_still_matches_pdf(self):
        self._use_three_signed_source_rows()
        result = self._import()
        existing = PurchaseRequisition.objects.get(pk=result['pr_id'])
        existing.total_price = Decimal('2500.00')
        existing.save(update_fields=['total_price'])
        self.assertEqual(existing.net_total_excl_vat, Decimal('1250.50'))
        with self.assertRaisesMessage(ValidationError, 'Purchase order amount'):
            RequisitionConversionService.convert(existing.pk, self.reviewer)
        self.assertFalse(PurchaseOrder.objects.exists())

    def test_conversion_checks_selected_vendor_even_when_cached_supplier_text_matches_pdf(self):
        self._use_three_signed_source_rows()
        result = self._import()
        existing = PurchaseRequisition.objects.get(pk=result['pr_id'])
        existing.vendor = Vendor.objects.create(vendor_code='OTHER-001', name='Different Supplier', status='active')
        existing.save(update_fields=['vendor'])
        self.assertEqual(existing.supplier_name, self.fields['supplier_name'])
        with self.assertRaisesMessage(ValidationError, 'selected vendor does not match'):
            RequisitionConversionService.convert(existing.pk, self.reviewer)
        self.assertFalse(PurchaseOrder.objects.exists())

    def test_reviewed_ocr_correction_is_saved_as_approved_snapshot_for_conversion(self):
        self._use_three_signed_source_rows()
        result = self._import(manual_overrides={**self.reviewed, 'net_total': '1500.75'})
        existing = PurchaseRequisition.objects.get(pk=result['pr_id'])
        verification = existing.price_remarks_data['signed_document_verification']
        self.assertEqual(verification['source_fields']['net_total'], '1250.50')
        self.assertEqual(verification['approved_fields']['net_total'], '1500.75')
        converted, order = RequisitionConversionService.convert(existing.pk, self.reviewer)
        self.assertEqual(converted.status, 'converted')
        self.assertEqual(order.total_amount, Decimal('1500.75'))

    def test_creation_requires_reviewed_complete_fields(self):
        for corrections, message in ((None, 'Review the extracted fields'),
                                     ({**self.reviewed, 'supplier_name': ''}, 'Supplier'),
                                     ({**self.reviewed, 'net_total': '0'}, 'greater than zero'),
                                     ({**self.reviewed, 'net_total': '10000000000000'}, 'less than 10 trillion'),
                                     ({**self.reviewed, 'net_total': 'NaN'}, 'finite number')):
            with self.subTest(corrections=corrections), self.assertRaisesMessage(SignedPRImportError, message):
                self._import(manual_overrides=corrections)
        self.assertFalse(PurchaseRequisition.objects.exists())
        self.storage.save.assert_not_called()

    def test_creation_never_substitutes_uploader_for_unknown_issuer(self):
        with self.assertRaisesMessage(SignedPRImportError, 'exactly one active RADAI user'):
            self._import(manual_overrides={**self.reviewed, 'issued_by_name': 'Unregistered Issuer'})
        self.assertFalse(PurchaseRequisition.objects.exists())

    def test_creation_rejects_inactive_or_ambiguous_issuer(self):
        self.issuer.is_active = False
        self.issuer.save(update_fields=['is_active'])
        with self.assertRaisesMessage(SignedPRImportError, 'exactly one active RADAI user'):
            self._import()
        self.issuer.is_active = True
        self.issuer.save(update_fields=['is_active'])
        get_user_model().objects.create_user(username='duplicate-name', email='duplicate@example.test',
                                            first_name='Document', last_name='Issuer')
        with self.assertRaisesMessage(SignedPRImportError, 'exactly one active RADAI user'):
            self._import()
        self.assertFalse(PurchaseRequisition.objects.exists())

    def test_duplicate_creation_does_not_modify_existing_record(self):
        existing = PurchaseRequisition.objects.create(pr_number=self.fields['pr_number'], title='Keep this title')
        with self.assertRaisesMessage(SignedPRImportError, 'already exists'):
            self._import()
        existing.refresh_from_db()
        self.assertEqual(existing.title, 'Keep this title')
        self.assertEqual(PurchaseRequisition.objects.count(), 1)
        self.storage.save.assert_not_called()

    def test_concurrent_duplicate_is_reported_before_file_storage(self):
        with patch.object(PurchaseRequisition.objects, 'create', side_effect=IntegrityError('duplicate')):
            with self.assertRaisesMessage(SignedPRImportError, 'already registered'):
                self._import()
        self.assertFalse(PurchaseRequisition.objects.exists())
        self.storage.save.assert_not_called()

    def test_record_bound_import_cannot_create_or_change_document_identity(self):
        preview = preview_signed_pr_pdf(b'%PDF-test', filename='signed.pdf',
                                        expected_pr_number=self.fields['pr_number'])
        self.assertFalse(preview['can_create'])
        with self.assertRaisesMessage(SignedPRImportError, 'cannot create a new record'):
            self._import(expected_pr_number=self.fields['pr_number'])
        with self.assertRaisesMessage(SignedPRImportError, 'edited record'):
            self._import(create_new=False, expected_pr_number='RAD-PRJ-PR-9999_2026')
        self.assertFalse(PurchaseRequisition.objects.exists())

    def test_existing_update_keeps_identity_and_deduplicates_source_attachment(self):
        existing = PurchaseRequisition.objects.create(pr_number=self.fields['pr_number'], status='converted')
        first = self._import(create_new=False)
        second = self._import(create_new=False)
        existing.refresh_from_db()
        self.assertFalse(first['created'])
        self.assertEqual(first['pr_id'], str(existing.pk))
        self.assertEqual(second['pr_id'], str(existing.pk))
        self.assertEqual(existing.status, 'converted')
        self.assertEqual(len(existing.attachments), 1)
        self.assertEqual(self.storage.save.call_count, 1)

    def test_api_explicit_creation_returns_201_and_rejects_nonboolean_choice(self):
        self._grant_api_access()
        factory = APIRequestFactory()
        view = PurchaseRequisitionViewSet.as_view({'post': 'import_signed_pdf'})
        for flag, expected_status in (('yes please', 400), ('true', 201), ('true', 400)):
            request = factory.post('/api/v1/procurement/requisitions/import-signed-pdf/', {
                'file': SimpleUploadedFile('signed.pdf', b'%PDF-test', content_type='application/pdf'),
                'manual_overrides': json.dumps(self.reviewed), 'create_new': flag,
            }, format='multipart')
            force_authenticate(request, self.reviewer)
            response = view(request)
            self.assertEqual(response.status_code, expected_status, response.data)
        self.assertEqual(PurchaseRequisition.objects.count(), 1)

    def test_admin_multipart_edit_keeps_approved_and_converted_source_history_exact(self):
        self._grant_api_access()
        self.evidence['approver_names']['vp'] = 'External Source Signer'
        result = self._import()
        pr = PurchaseRequisition.objects.get(pk=result['pr_id'])
        history = deepcopy(pr.approval_workflow_config)
        verification = deepcopy(pr.price_remarks_data['signed_document_verification'])
        evidence = deepcopy(pr.price_remarks_data['signed_approval_evidence'])
        attachments = deepcopy(pr.attachments)
        self.assertIsNone(history[-1]['user_id'])
        factory = APIRequestFactory()
        view = PurchaseRequisitionViewSet.as_view({'patch': 'partial_update'})
        for lifecycle in ('approved', 'converted'):
            with self.subTest(status=lifecycle):
                pr.status = lifecycle
                pr.save(update_fields=['status'])
                request = factory.patch(f'/api/v1/procurement/requisitions/{pr.pk}/', {
                    'description_reason': f'Reviewed description ({lifecycle})',
                    'items': json.dumps(pr.items),
                    'po_applicable': 'true',
                    'approval_workflow_config': json.dumps([
                        {**stage, 'status': 'pending', 'approved_at': None} for stage in history
                    ]),
                    'price_remarks_data': json.dumps({
                        'signed_document_verification': {'signed_off': False},
                        'signed_approval_evidence': {},
                        'payment_terms': 'Net 30',
                    }),
                }, format='multipart')
                force_authenticate(request, self.reviewer)
                response = view(request, pk=pr.pk)
                self.assertEqual(response.status_code, 200, response.data)
                pr.refresh_from_db()
                self.assertEqual(pr.status, lifecycle)
                self.assertEqual(pr.description_reason, f'Reviewed description ({lifecycle})')
                self.assertEqual(pr.approval_workflow_config, history)
                self.assertEqual(pr.price_remarks_data['signed_document_verification'], verification)
                self.assertEqual(pr.price_remarks_data['signed_approval_evidence'], evidence)
                self.assertEqual(pr.attachments, attachments)
                self.assertEqual(response.data['approval_workflow_config'], history)
                self.assertEqual(response.data['approval_hierarchy'], history)

    def _grant_api_access(self):
        organization = Organization.objects.create(name='PDF import tests', code='pdf-import-tests')
        UserProfile.objects.get_or_create(user=self.reviewer, defaults={'organization': organization})
        module, _ = Module.objects.get_or_create(code='procurement_requisitions', defaults={'name': 'Recommendations'})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])

    def test_api_record_bound_creation_is_rejected_even_when_number_matches(self):
        self._grant_api_access()
        factory = APIRequestFactory()
        view = PurchaseRequisitionViewSet.as_view({'post': 'import_signed_pdf'})
        for number in (self.fields['pr_number'], 'RAD-PRJ-PR-9999_2026'):
            request = factory.post('/api/v1/procurement/requisitions/import-signed-pdf/', {
                'file': SimpleUploadedFile('signed.pdf', b'%PDF-test', content_type='application/pdf'),
                'manual_overrides': json.dumps(self.reviewed), 'create_new': 'true', 'expected_pr_number': number,
            }, format='multipart')
            force_authenticate(request, self.reviewer)
            response = view(request)
            self.assertEqual(response.status_code, 400, response.data)
            self.assertIn('cannot create a new record', response.data['error'])
        self.assertFalse(PurchaseRequisition.objects.exists())
        self.storage.save.assert_not_called()
