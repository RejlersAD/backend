"""Existing PO identifiers can be corrected without changing approval evidence."""

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
import hashlib
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.test import APIClient

from apps.procurement.models import PODocument, PurchaseOrder, PurchaseRequisition, Vendor
from apps.procurement.serializers import PurchaseOrderSerializer
from apps.procurement.services.approval_integrity import purchase_order_signature_issue
from apps.procurement.services.procurement_lifecycle import RETAINED_ATTACHMENTS, RETAINED_SOURCES
from apps.procurement.services.purchase_order_approvals import record_decision
from apps.procurement.services.purchase_order_content import purchase_order_content_fingerprint
from apps.procurement.services.purchase_order_document_preview import document_preview_order
from apps.procurement.tests.approval_fixtures import grant_approval, set_position
from apps.rbac.models import (
    AuditLog, Module, Organization, Permission, Role, RoleModule, RolePermission,
    UserPermissionOverride, UserProfile, UserRole,
)
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints

from . import test_signed_po_originating_pr as originating


urlpatterns = [path('api/v1/procurement/', include('apps.procurement.urls'))]
secure_module_endpoints(urlpatterns)
BASE = '/api/v1/procurement/orders/'
SERVICE = 'apps.procurement.services.purchase_order_number_correction'
CORRECTIONS = '_retained_po_number_corrections'
SIGNATURE = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j8uoAAAAASUVORK5CYII='


@override_settings(ROOT_URLCONF=__name__, TEAMS_APPROVAL_WEBHOOK_URL='', WEB_PUSH_VAPID_PRIVATE_KEY='')
class PurchaseOrderNumberCorrectionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        for target in (
            'apps.notifications.services.send_notification_email.delay',
            'apps.notifications.teams.send_teams_approval_assignment.delay',
            'apps.notifications.services.send_web_push_notification.delay',
        ):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        users = get_user_model()
        self.editor = users.objects.create_user('number-editor', email='editor@number.example.test')
        self.signer = users.objects.create_user('number-signer', email='signer@number.example.test')
        organization = Organization.objects.create(code='number-tests', name='PO number tests')
        self.profile, _ = UserProfile.objects.get_or_create(
            user=self.editor, defaults={'organization': organization},
        )
        self.profile.roles.clear()
        self.module, _ = Module.objects.get_or_create(
            code='procurement_orders', defaults={'name': 'Purchase orders'},
        )
        ensure_module_actions(Module, Permission, module_ids=[self.module.pk])
        self.role = Role.objects.create(code='number-editor', name='PO number editor', level=3)
        UserRole.objects.create(user_profile=self.profile, role=self.role)
        RoleModule.objects.create(role=self.role, module=self.module)
        for permission in self.module.permissions.filter(action__in=['read', 'create', 'update'], is_active=True):
            RolePermission.objects.create(role=self.role, permission=permission)
        grant_approval(self.signer, 'procurement_orders')
        set_position(self.signer, 'Engineer')
        signer_profile = self.signer.rbac_profile
        signer_profile.signature_image = SIGNATURE
        signer_profile.save(update_fields=['signature_image'])
        self.vendor = Vendor.objects.create(vendor_code='NUMBER', name='Number correction supplier')
        self.pr = PurchaseRequisition.objects.create(
            pr_number='RAD-PRJ-PR-0071_2026', status='converted', total_price='900.00',
            currency='AED', price_remarks_data={'payment_terms': 'Retained PR terms'},
        )
        self.old_number = 'RAD-PRJ-PUR-0085_2026'
        self.new_number = 'RAD-PRJ-PUR-0085_JUL2026'
        self.order = PurchaseOrder.objects.create(
            po_number=self.old_number, pr_reference=self.pr, vendor=self.vendor,
            title='Retained engineering study', category='other', created_by=self.editor,
            total_amount='100.00', net_amount='100.00', tax_amount=0, vat_percentage=0,
            vat_basis='none', currency='AED', marking='RAD-PRJ-PUR-0085',
            contact_persons={'order_introduction': 'Retained buyer and seller introduction'},
            approval_log=[{
                'stage': 'Technical Approval', 'level': 0, 'user_id': str(self.signer.pk),
                'approver_email': self.signer.email, 'status': 'Pending', 'business_position': 'engineer',
            }],
        )
        self.pr.po_number_reference = self.old_number
        self.pr.price_remarks_data['po_link'] = {
            'po_id': str(self.order.pk), 'po_number': self.old_number,
            'source': 'existing-source', 'linked_at': '2026-07-01T09:00:00Z',
        }
        self.pr.save(update_fields=['po_number_reference', 'price_remarks_data'])
        self.url = f'{BASE}{self.order.pk}/'
        self.client = APIClient()
        self.client.force_authenticate(self.editor)

    def payload(self, **changes):
        return {
            'po_number': self.new_number,
            'expected_updated_at': self.order.updated_at.isoformat(),
            **changes,
        }

    def correct(self, payload=None):
        return self.client.post(self.url + 'correct-number/', self.payload() if payload is None else payload, format='json')

    def audits(self):
        return AuditLog.objects.filter(
            resource_type='PurchaseOrder', resource_id=self.order.pk,
            metadata__operation='correct_po_number',
        )

    def approve(self):
        self.order, _ = record_decision(self.order, self.signer, 'approve', require_signature=True)

    def snapshots(self):
        return (
            PurchaseOrder.objects.values().get(pk=self.order.pk),
            PurchaseRequisition.objects.values().get(pk=self.pr.pk),
        )

    def assert_unchanged(self, before):
        self.assertEqual(self.snapshots(), before)
        self.assertFalse(self.audits().exists())

    def test_draft_correction_persists_exact_number_and_current_pr_references(self):
        before_order, before_pr = self.snapshots()
        response = self.correct()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['id'], str(self.order.pk))
        self.assertEqual(response.data['po_number'], self.new_number)
        self.assertEqual(response.data['title'], self.order.title)
        self.order.refresh_from_db()
        self.pr.refresh_from_db()
        self.assertEqual(self.order.po_number, self.new_number)
        self.assertGreater(self.order.updated_at, before_order['updated_at'])
        for field, value in before_order.items():
            if field not in {'po_number', 'contact_persons', 'updated_at'}:
                self.assertEqual(getattr(self.order, field), value, field)
        self.assertEqual(self.order.contact_persons['order_introduction'],
                         before_order['contact_persons']['order_introduction'])
        self.assertEqual(self.pr.po_number_reference, self.new_number)
        expected_metadata = deepcopy(before_pr['price_remarks_data'])
        expected_metadata['po_link']['po_number'] = self.new_number
        self.assertEqual(self.pr.price_remarks_data, expected_metadata)
        for field, value in before_pr.items():
            if field not in {'po_number_reference', 'price_remarks_data', 'updated_at'}:
                self.assertEqual(getattr(self.pr, field), value, field)
        audit = self.audits().get()
        self.assertEqual(audit.action, 'update')
        self.assertEqual(audit.user_id, self.editor.pk)
        self.assertEqual(audit.changes['po_number'], {'before': self.old_number, 'after': self.new_number})

    def test_completed_order_number_can_be_corrected_without_reopening(self):
        self.order.status = 'completed'
        self.order.save(update_fields=['status', 'updated_at'])
        response = self.correct()
        self.assertEqual(response.status_code, 200, response.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'completed')
        self.assertEqual(self.order.po_number, self.new_number)

    def test_approved_number_correction_preserves_decisions_and_valid_signature(self):
        self.approve()
        original_evidence = deepcopy(self.order.approval_log)
        original_approved_at = self.order.approved_at
        response = self.correct()
        self.assertEqual(response.status_code, 200, response.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.approval_log, original_evidence)
        self.assertEqual(self.order.approved_at, original_approved_at)
        self.assertEqual(self.order.approved_by_id, self.signer.pk)
        self.assertEqual(self.order.approval_signature, SIGNATURE)
        self.assertEqual(purchase_order_signature_issue(self.order), '')
        self.assertFalse(response.data.get('signature_review_required', False))
        self.assertTrue(response.data['commercial_edit_locked'])
        self.assertTrue(response.data['can_send_to_vendor'])
        self.assertNotEqual(purchase_order_content_fingerprint(self.order),
                            original_evidence[0]['content_fingerprint'])
        denied = self.client.patch(self.url, {'entered_amount': '1000', 'vat_basis': 'none'}, format='json')
        self.assertEqual(denied.status_code, 400, denied.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.total_amount, Decimal('100.00'))

    def test_multiple_corrections_keep_original_approval_fingerprint_valid(self):
        self.approve()
        original_evidence = deepcopy(self.order.approval_log)
        self.assertEqual(self.correct().status_code, 200)
        self.order.refresh_from_db()
        response = self.correct(self.payload(po_number='RAD-PRJ-PUR-0085_AUG2026'))
        self.assertEqual(response.status_code, 200, response.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.approval_log, original_evidence)
        self.assertEqual(purchase_order_signature_issue(self.order), '')
        self.assertEqual(len(self.order.contact_persons[CORRECTIONS]), 2)
        self.assertEqual(self.audits().count(), 2)

    def test_original_document_and_legacy_attachment_remain_accessible_and_unchanged(self):
        source_bytes = b'%PDF-1.4 original signed PO evidence'
        key = default_storage.save(
            f'procurement/orders/{self.old_number}/number-correction-{uuid4()}.pdf',
            ContentFile(source_bytes),
        )
        self.addCleanup(default_storage.delete, key)
        document_key = default_storage.save(
            f'procurement/source-documents/number-correction-{uuid4()}.pdf', ContentFile(source_bytes),
        )
        self.addCleanup(default_storage.delete, document_key)
        document = PODocument.objects.create(
            original_filename='original-number.pdf', s3_key=document_key, document_type='purchase_order',
            confirmed_po=self.order, uploaded_by=self.editor,
            extracted_data={'source_po_number': self.new_number, 'po_number': self.old_number},
        )
        self.order.attachments = [
            {'type': 'signed_purchase_order_pdf', 's3_key': key, 'filename': 'original-number.pdf'},
        ]
        self.order.save(update_fields=['attachments', 'updated_at'])
        original_attachments = deepcopy(self.order.attachments)
        original_document = PODocument.objects.values().get(pk=document.pk)
        self.assertEqual(self.correct().status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.attachments, original_attachments)
        self.assertEqual(PODocument.objects.values().get(pk=document.pk), original_document)

        with default_storage.open(key, 'rb') as original:
            self.assertEqual(original.read(), source_bytes)
        for identifier in (str(document.pk), 'attachment-0'):
            response = self.client.get(self.url + f'uploaded-documents/{identifier}/content/')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(b''.join(response.streaming_content), source_bytes)
            self.assertTrue(response.closed)

    def test_read_or_create_permission_without_update_does_not_allow_correction(self):
        before = self.snapshots()
        RolePermission.objects.filter(role=self.role, permission__action='update').delete()
        cache.clear()
        self.assertEqual(self.correct().status_code, 403)
        RolePermission.objects.filter(role=self.role, permission__action='create').delete()
        cache.clear()
        self.assertEqual(self.correct().status_code, 403)
        self.assert_unchanged(before)

    def test_explicit_update_denial_overrides_role_grant(self):
        before = self.snapshots()
        permission = self.module.permissions.get(action='update')
        UserPermissionOverride.objects.create(user_profile=self.profile, permission=permission, allowed=False)
        cache.clear()
        self.assertEqual(self.correct().status_code, 403)
        self.assert_unchanged(before)

    def test_anonymous_request_cannot_correct_existing_number(self):
        before = self.snapshots()
        self.client.force_authenticate(None)
        self.assertIn(self.correct().status_code, (401, 403))
        self.assert_unchanged(before)

    def test_stale_request_preserves_database_and_has_no_audit(self):
        before = self.snapshots()
        payload = self.payload(expected_updated_at=(self.order.updated_at - timedelta(seconds=1)).isoformat())
        response = self.correct(payload)
        self.assertEqual(response.status_code, 409, response.data)
        self.assert_unchanged(before)

    def test_missing_or_malformed_fields_are_rejected_without_mutation(self):
        before = self.snapshots()
        for payload in (
            {}, {'po_number': self.new_number}, {'expected_updated_at': self.order.updated_at.isoformat()},
            self.payload(expected_updated_at='invalid'), self.payload(expected_updated_at=None),
            self.payload(po_number=''), self.payload(po_number=None), ['invalid'], 'invalid',
        ):
            with self.subTest(payload=payload):
                response = self.correct(payload)
                self.assertEqual(response.status_code, 400, response.data)
                self.assert_unchanged(before)

    def test_unknown_commercial_status_and_evidence_fields_are_rejected_atomically(self):
        self.approve()
        before = self.snapshots()
        for extra in (
            {'entered_amount': '100000'}, {'status': 'completed'}, {'approval_log': []},
            {'contact_persons': {CORRECTIONS: []}}, {'vendor': str(self.vendor.pk)},
        ):
            with self.subTest(extra=extra):
                response = self.correct(self.payload(**extra))
                self.assertEqual(response.status_code, 400, response.data)
                self.assert_unchanged(before)

    def test_invalid_number_and_mismatched_pr_scope_or_year_cannot_save(self):
        before = self.snapshots()
        for number in (
            'RAD-PRJ-PUR-0085_JULL2026', 'RAD-PRJ-PUR-0085_JUL2026-extra',
            'RAD-GEN-PUR-0085_JUL2026', 'RAD-PRJ-PUR-0085_JUL2025',
            'RAD-PRJ-PUR-' + '8' * 50 + '_JUL2026',
        ):
            with self.subTest(number=number):
                response = self.correct(self.payload(po_number=number))
                self.assertEqual(response.status_code, 400, response.data)
                self.assert_unchanged(before)

    def test_duplicate_number_returns_conflict_without_partial_pr_update(self):
        PurchaseOrder.objects.create(
            po_number=self.new_number, vendor=self.vendor, title='Existing other PO', total_amount='90.00',
        )
        before = self.snapshots()
        response = self.correct()
        self.assertEqual(response.status_code, 409, response.data)
        self.assert_unchanged(before)

    def test_same_request_retry_returns_current_record_without_duplicate_audit(self):
        payload = self.payload()
        first = self.correct(payload)
        self.assertEqual(first.status_code, 200, first.data)
        after_first = self.snapshots()
        second = self.correct(payload)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(second.data['po_number'], self.new_number)
        self.assertEqual(second.data['updated_at'], first.data['updated_at'])
        self.assertEqual(self.snapshots(), after_first)
        self.assertEqual(self.audits().count(), 1)

    def test_retry_rechecks_current_update_permission(self):
        payload = self.payload()
        self.assertEqual(self.correct(payload).status_code, 200)
        after_first = self.snapshots()
        RolePermission.objects.filter(role=self.role, permission__action='update').delete()
        cache.clear()
        self.assertEqual(self.correct(payload).status_code, 403)
        self.assertEqual(self.snapshots(), after_first)
        self.assertEqual(self.audits().count(), 1)

    def test_audit_failure_rolls_back_order_pr_references_and_correction_history(self):
        before = self.snapshots()
        with patch(f'{SERVICE}.create_audit_log', side_effect=RuntimeError('Audit unavailable')):
            with self.assertRaisesMessage(RuntimeError, 'Audit unavailable'):
                self.correct()
        self.assert_unchanged(before)

    def test_existing_unapproved_commercial_change_cannot_be_certified_by_correction(self):
        self.approve()
        PurchaseOrder.objects.filter(pk=self.order.pk).update(total_amount=Decimal('100000'))
        before = self.snapshots()
        response = self.correct()
        self.assertEqual(response.status_code, 409, response.data)
        self.assert_unchanged(before)
        self.order.refresh_from_db()
        self.assertIn('commercial details differ', purchase_order_signature_issue(self.order))

    def test_other_orders_reference_metadata_on_same_pr_is_not_rewritten(self):
        other = PurchaseOrder.objects.create(
            po_number='RAD-PRJ-PUR-0099_2026', vendor=self.vendor,
            title='Different order', total_amount='50.00', pr_reference=self.pr,
        )
        self.pr.po_number_reference = other.po_number
        self.pr.price_remarks_data['po_link'].update(po_id=str(other.pk), po_number=other.po_number)
        self.pr.save(update_fields=['po_number_reference', 'price_remarks_data'])
        before_pr = PurchaseRequisition.objects.values().get(pk=self.pr.pk)
        response = self.correct()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(PurchaseRequisition.objects.values().get(pk=self.pr.pk), before_pr)

    def test_money_or_title_tampering_after_correction_still_invalidates_approval(self):
        self.approve()
        self.assertEqual(self.correct().status_code, 200)
        for changes in ({'total_amount': Decimal('100000')}, {'title': 'Unapproved replacement title'}):
            with self.subTest(changes=changes):
                PurchaseOrder.objects.filter(pk=self.order.pk).update(**changes)
                self.order.refresh_from_db()
                self.assertIn('commercial details differ', purchase_order_signature_issue(self.order))
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertTrue(response.data['signature_review_required'])
                self.assertEqual(response.data['approval_signature'], '')
                self.assertFalse(response.data['can_send_to_vendor'])
                PurchaseOrder.objects.filter(pk=self.order.pk).update(
                    total_amount=Decimal('100.00'), title='Retained engineering study',
                )

    def test_generic_contact_write_cannot_remove_or_replace_correction_history(self):
        self.approve()
        self.assertEqual(self.correct().status_code, 200)
        self.order.refresh_from_db()
        retained = deepcopy(self.order.contact_persons[CORRECTIONS])
        for entries in ([], [{'old_number': self.new_number, 'new_number': 'forged'}]):
            contacts = deepcopy(self.order.contact_persons)
            contacts[CORRECTIONS] = entries
            response = self.client.patch(self.url, {'contact_persons': contacts}, format='json')
            self.assertIn(response.status_code, (200, 400), response.data)
            self.order.refresh_from_db()
            self.assertEqual(self.order.contact_persons[CORRECTIONS], retained)
            self.assertEqual(purchase_order_signature_issue(self.order), '')

    def test_forged_chain_cannot_hide_money_change_even_when_hashes_are_adjusted(self):
        self.approve()
        self.assertEqual(self.correct().status_code, 200)
        self.order.refresh_from_db()
        self.order.total_amount = Decimal('100000')
        contacts = deepcopy(self.order.contact_persons)
        contacts[CORRECTIONS][-1]['after_fingerprint'] = purchase_order_content_fingerprint(self.order)
        PurchaseOrder.objects.filter(pk=self.order.pk).update(
            total_amount=self.order.total_amount, contact_persons=contacts,
        )
        self.order.refresh_from_db()
        self.assertIn('commercial details differ', purchase_order_signature_issue(self.order))

    def test_preview_preserves_saved_private_history_and_discards_client_inventions(self):
        self.approve()
        self.assertEqual(self.correct().status_code, 200)
        self.order.refresh_from_db()
        original_contacts = deepcopy(self.order.contact_persons)
        self.order.contact_persons[RETAINED_SOURCES] = ['procurement/signed-pdfs/original.pdf']
        protected_keys = (CORRECTIONS, RETAINED_ATTACHMENTS, RETAINED_SOURCES)
        contacts = deepcopy(self.order.contact_persons)
        for key in protected_keys:
            contacts[key] = ['client-invented-history-or-storage-key']
        before = self.snapshots()
        preview = document_preview_order({'contact_persons': contacts}, base=self.order)
        for key in protected_keys:
            self.assertEqual(preview.contact_persons[key], self.order.contact_persons[key])
        self.assertEqual(preview.approval_signature, SIGNATURE)
        self.assertEqual(purchase_order_signature_issue(preview), '')
        fresh = document_preview_order({'contact_persons': contacts})
        for key in protected_keys:
            self.assertNotIn(key, fresh.contact_persons)
        self.assertEqual(self.snapshots(), before)
        self.order.refresh_from_db()
        self.assertEqual(self.order.contact_persons, original_contacts)

    def test_generic_create_discards_forged_correction_and_source_history(self):
        forged = {
            CORRECTIONS: [{
                'old_number': self.old_number, 'new_number': self.new_number,
                'before_fingerprint': 'po-v1:forged-before', 'after_fingerprint': 'po-v1:forged-after',
                'actor_id': str(self.editor.pk),
            }],
            RETAINED_ATTACHMENTS: ['procurement/orders/another-order/private.pdf'],
            RETAINED_SOURCES: ['procurement/signed-pdfs/another-original.pdf'],
            'order_introduction': 'New order introduction',
        }
        response = self.client.post(BASE, {
            'po_number': self.new_number, 'pr_reference': str(self.pr.pk),
            'vendor': str(self.vendor.pk), 'title': 'New native order', 'category': 'other',
            'total_amount': '100.00', 'entered_amount': '100.00', 'vat_basis': 'none',
            'currency': 'AED', 'approval_log': deepcopy(self.order.approval_log),
            'contact_persons': forged,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        created = PurchaseOrder.objects.get(pk=response.data['id'])
        for key in (CORRECTIONS, RETAINED_ATTACHMENTS, RETAINED_SOURCES):
            self.assertNotIn(key, created.contact_persons)
            self.assertNotIn(key, response.data['contact_persons'])
        self.assertEqual(created.contact_persons['order_introduction'], 'New order introduction')
        self.assertEqual(created.approval_log[0]['status'], 'Pending')

    def test_prevalidated_contact_edit_preserves_correction_history_created_before_save(self):
        self.approve()
        serializer = PurchaseOrderSerializer(
            self.order,
            data={'contact_persons': deepcopy(self.order.contact_persons), 'notes': 'Late internal note'},
            partial=True, context={'request': SimpleNamespace(user=self.editor)},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertNotIn(CORRECTIONS, serializer.validated_data['contact_persons'])
        response = self.correct()
        self.assertEqual(response.status_code, 200, response.data)
        corrected = PurchaseOrder.objects.get(pk=self.order.pk)
        correction_history = deepcopy(corrected.contact_persons[CORRECTIONS])
        approval_evidence = deepcopy(corrected.approval_log)
        self.assertEqual(serializer.instance.po_number, self.old_number)
        serializer.save()
        self.order.refresh_from_db()
        self.assertEqual(self.order.po_number, self.new_number)
        self.assertEqual(self.order.notes, 'Late internal note')
        self.assertEqual(self.order.contact_persons[CORRECTIONS], correction_history)
        self.assertEqual(self.order.approval_log, approval_evidence)
        self.assertEqual(purchase_order_signature_issue(self.order), '')
        self.assertEqual(self.audits().count(), 1)

    def test_reupload_of_original_pdf_cannot_duplicate_or_rebind_renamed_order(self):
        content = b'%PDF-1.4 retained original number before correction'
        document = PODocument.objects.create(
            original_filename='retained-original.pdf', document_type='purchase_order',
            confirmed_po=self.order, uploaded_by=self.editor,
            extracted_data={
                'source_sha256': hashlib.sha256(content).hexdigest(),
                'source_po_number': self.old_number, 'po_number': self.old_number,
            },
        )
        response = self.correct(self.payload(po_number='RAD-PRJ-PUR-0099_JUL2026'))
        self.assertEqual(response.status_code, 200, response.data)
        after_correction = self.snapshots()
        original_document = PODocument.objects.values().get(pk=document.pk)
        fields = {
            'source_po_number': self.old_number, 'po_number': self.old_number,
            'source_pr_numbers': [], 'source_page_count': 2, 'extracted_page_count': 2,
            'extraction_truncated': False, 'po_date': date(2026, 7, 7),
            'vendor_name': self.vendor.name, 'vendor_license_no': '',
            'seller_reference': '', 'quote_ref': '', 'project_number': '',
            'summary': 'Retained engineering study', 'payment_terms': 'Net 45',
            'payment_mode': 'Bank Transfer', 'delivery_terms': '', 'expected_delivery': None,
            'total_amount': Decimal('100.00'), 'tax_amount': Decimal('0.00'),
            'gross_amount': Decimal('100.00'), 'currency': 'AED', 'items': [],
        }
        with (
            patch('apps.procurement.services.signed_po_pdf_import.extract_signed_po_fields',
                  return_value=fields),
            patch('django.core.files.storage.default_storage.save') as store,
        ):
            response = self.client.post('/api/v1/procurement/po-documents/import_signed_pdf/', {
                'file': SimpleUploadedFile('retained-original.pdf', content, content_type='application/pdf'),
            }, format='multipart')
        self.assertEqual(response.status_code, 409, response.data)
        store.assert_not_called()
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        self.assertEqual(PODocument.objects.count(), 1)
        self.assertEqual(self.snapshots(), after_correction)
        self.assertEqual(PODocument.objects.values().get(pk=document.pk), original_document)


@override_settings(ROOT_URLCONF=__name__, TEAMS_APPROVAL_WEBHOOK_URL='', WEB_PUSH_VAPID_PRIVATE_KEY='')
class ImportedPurchaseOrderNumberCorrectionTests(TestCase):
    def setUp(self):
        originating.SignedPOOriginatingPRTests.setUp(self)
        self.addCleanup(cache.clear)

    def test_confirmed_signed_upload_corrects_number_without_unlocking_commercial_fields(self):
        old_number = 'RAD-PRJ-PUR-0085_2026'
        corrected_number = 'RAD-PRJ-PUR-0085_JUL2026'
        # Reproduce the legacy extraction result through the real import command.
        # OCR is isolated; the retained bytes and every database write are real.
        self.content = b'%PDF-1.4 retained source RAD-PRJ-PUR-0085_JUL2026'
        self.fields.update(source_po_number=old_number, po_number=old_number)
        imported = originating.SignedPOOriginatingPRTests.upload(self)
        self.assertEqual(imported.status_code, 200, imported.data)
        self.assertEqual(imported.data['operation'], 'created')
        order = PurchaseOrder.objects.get(pk=imported.data['purchase_order_id'])
        document = PODocument.objects.get(pk=imported.data['document_id'])
        self.addCleanup(default_storage.delete, document.s3_key)
        self.assertEqual(document.confirmed_po_id, order.pk)
        self.assertEqual(order.po_number, old_number)
        self.assertTrue(document.extracted_data['signature_verified'])
        self.assertEqual(order.approval_log[0]['status'], 'Approved')
        self.assertEqual(purchase_order_signature_issue(order), '')
        original_order = PurchaseOrder.objects.values().get(pk=order.pk)
        original_document = PODocument.objects.values().get(pk=document.pk)
        url = f'{BASE}{order.pk}/'

        generic_edit = self.client.patch(url, {'po_number': corrected_number}, format='json')
        self.assertEqual(generic_edit.status_code, 400, generic_edit.data)
        self.assertEqual(str(generic_edit.data['po_number'][0]),
                         'Approved commercial details are locked. Create a revised purchase order for commercial changes.')
        self.assertEqual(PurchaseOrder.objects.values().get(pk=order.pk), original_order)

        corrected = self.client.post(url + 'correct-number/', {
            'po_number': corrected_number, 'expected_updated_at': order.updated_at.isoformat(),
        }, format='json')
        self.assertEqual(corrected.status_code, 200, corrected.data)
        order.refresh_from_db()
        self.assertEqual(order.po_number, corrected_number)
        self.assertEqual(self.client.get(url).data['po_number'], corrected_number)
        self.assertTrue(corrected.data['commercial_edit_locked'])
        self.assertEqual(purchase_order_signature_issue(order), '')
        for field, value in original_order.items():
            if field not in {'po_number', 'contact_persons', 'updated_at'}:
                self.assertEqual(getattr(order, field), value, field)
        self.assertEqual(PODocument.objects.values().get(pk=document.pk), original_document)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.po_number_reference, corrected_number)
        self.assertEqual(self.pr.price_remarks_data['po_link']['po_number'], corrected_number)
        self.assertEqual(AuditLog.objects.filter(
            resource_type='PurchaseOrder', resource_id=order.pk,
            metadata__operation='correct_po_number',
        ).count(), 1)
        source = self.client.get(url + f'uploaded-documents/{document.pk}/content/')
        self.assertEqual(source.status_code, 200)
        self.assertEqual(b''.join(source.streaming_content), self.content)
        self.assertTrue(source.closed)

        commercial_edit = self.client.patch(url, {'payment_terms': 'Unapproved payment terms'}, format='json')
        self.assertEqual(commercial_edit.status_code, 400, commercial_edit.data)
        order.refresh_from_db()
        self.assertEqual(order.payment_terms, original_order['payment_terms'])
        self.assertEqual(order.approval_log, original_order['approval_log'])
