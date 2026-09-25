"""Original-PDF approval corrections are bounded, audited, and permission checked."""

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import hashlib
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from botocore.exceptions import ClientError, NoCredentialsError
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import include, path
from django.utils import timezone
from rest_framework.test import APIClient

from apps.procurement.models import PurchaseRequisition
from apps.procurement.serializers import PurchaseRequisitionSerializer
from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, UserPermissionOverride, UserProfile, UserRole
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints


urlpatterns = [path('api/v1/procurement/', include('apps.procurement.urls'))]
secure_module_endpoints(urlpatterns)

PR_ID = UUID('8d42ac90-6709-43a1-aa91-17ae3a3a0591')
PR_NUMBER = 'RAD-PRJ-PR-0043_2026'
FILENAME = f'{PR_NUMBER}_Purchase_Requisition_2026-01-29.pdf'
SOURCE_KEY = f'procurement/signed_requisitions/{PR_ID}/2026/{FILENAME}'
PDF_BYTES = b'%PDF-1.4\nUnchanged original with source approval signatures\n%%EOF'
DIGEST = hashlib.sha256(PDF_BYTES).hexdigest()
SOURCE_TYPE = 'signed_purchase_requisition_pdf'
STORAGE_OPEN = 'apps.procurement.services.requisition_source_approvals.default_storage.open'


def source_row(role, name, *, verified=True):
    return {
        'role': role, 'user_name': name, 'user_id': None,
        'external': True, 'source': SOURCE_TYPE,
        'status': 'approved' if verified else 'not_recorded',
        'signature_verified': verified,
        'signature_source': 'original_pdf' if verified else '',
        'approved_at': '2026-01-29T09:15:00+00:00' if verified else None,
    }


@override_settings(ROOT_URLCONF=__name__, MEDIA_URL='/media/')
class RequisitionSourceApprovalEditTests(TestCase):
    def setUp(self):
        cache.clear()
        users = get_user_model()
        self.owner = users.objects.create_user(
            'source-edit-owner', email='source-owner@example.test', first_name='Record', last_name='Owner',
        )
        self.reader = users.objects.create_user('source-edit-reader', email='source-reader@example.test')
        self.admin = users.objects.create_superuser(
            'source-edit-admin', email='source-admin@example.test', password='test-only-password',
        )
        org, _ = Organization.objects.get_or_create(code='source-edits', defaults={'name': 'Source edit tests'})
        self.profiles = {}
        for user in (self.owner, self.reader, self.admin):
            profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': org})
            profile.roles.clear()
            self.profiles[user.pk] = profile
        module, _ = Module.objects.get_or_create(
            code='procurement_requisitions', defaults={'name': 'Purchase requisitions'},
        )
        module.is_active = True
        module.save(update_fields=['is_active'])
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        role = Role.objects.create(code='source-edit-member', name='Source edit member', level=3)
        RoleModule.objects.create(role=role, module=module)
        for profile in self.profiles.values():
            UserRole.objects.create(user_profile=profile, role=role)
        self.permissions = {
            action: Permission.objects.get(module=module, action=action, is_active=True)
            for action in ('read', 'update')
        }
        for user in (self.owner, self.reader, self.admin):
            for action in ('read', 'update'):
                self.grant(user, action)
        self.rows = [
            source_row('PM', 'Pat Manager'),
            source_row('MoE', 'Erin Engineer'),
            source_row('MoP', 'Morgan Projects'),
            source_row('Vp, Op', 'Unreadable Vice President', verified=False),
        ]
        self.attachments = [{
            'type': SOURCE_TYPE, 'filename': FILENAME, 'sha256': DIGEST,
            'storage_key': SOURCE_KEY, 'url': '/media/' + SOURCE_KEY,
            'uploaded_at': '2026-01-30T10:00:00Z',
        }, {'type': 'quotation', 'filename': 'unchanged-quote.pdf', 'url': '/media/quote.pdf'}]
        self.metadata = {
            'import_source': 'signed_pr_pdf', 'signed_pdf_attached': True,
            'budget_allocation': 'Engineering', 'payment_terms': 'Net 30',
            'line_details': [{'description': 'Engineering support', 'quantity': 1, 'unit_price': 2052}],
            'signed_document_verification': {
                'document_sha256': DIGEST, 'signed_off': False,
                'source_approval_rows': deepcopy(self.rows), 'extraction_notes': ['Keep original extraction'],
            },
            'signed_approval_evidence': {
                'signatures': {'pm': True, 'moe': True, 'mop': True, 'vp': False},
                'reviewed_approver_names': {'pm': 'Pat Manager'},
            },
            'source_approval_reviews': [],
        }
        self.pr = PurchaseRequisition.objects.create(
            id=PR_ID, pr_number=PR_NUMBER, issued_by=self.owner, status='draft',
            supplier_name='Original supplier', product_service='Engineering support',
            total_price=Decimal('2052.00'), net_total_excl_vat=Decimal('2052.00'),
            currency='USD', price_remarks='Keep negotiated terms',
            items=[{'description': 'Engineering support', 'quantity': 1, 'unit_price': 2052}],
            attachments=deepcopy(self.attachments), price_remarks_data=deepcopy(self.metadata),
        )
        self.detail_url = f'/api/v1/procurement/requisitions/{self.pr.pk}/'
        self.url = self.detail_url + 'source-approvals/'
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.storage_open = patch(STORAGE_OPEN, side_effect=lambda *_args, **_kwargs: BytesIO(PDF_BYTES)).start()
        self.addCleanup(patch.stopall)
        patch('django.core.files.storage.default_storage.url', side_effect=lambda key: '/media/' + key).start()

    def grant(self, user, action, allowed=True):
        UserPermissionOverride.objects.update_or_create(
            user_profile=self.profiles[user.pk], permission=self.permissions[action], defaults={'allowed': allowed},
        )
        cache.clear()

    def payload(self, index=3, **changes):
        self.pr.refresh_from_db()
        payload = {
            'document_sha256': DIGEST, 'row_index': index,
            'expected_row': deepcopy(self.pr.price_remarks_data['signed_document_verification']['source_approval_rows'][index]),
            'approver_name': 'Val Verified', 'signature_verified': True, 'approval_date': '2026-01-29',
            'special_note': 'Name and Level reviewed against the original PDF.',
        }
        payload.update(changes)
        return payload

    def save_rows(self, rows):
        self.pr.price_remarks_data['signed_document_verification']['source_approval_rows'] = deepcopy(rows)
        self.pr.save(update_fields=['price_remarks_data'])

    def snapshot(self):
        return PurchaseRequisition.objects.filter(pk=self.pr.pk).values().get()

    def assert_rejected_unchanged(self, payload, status=400):
        before = self.snapshot()
        response = self.client.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, status, response.data)
        self.assertEqual(self.snapshot(), before)
        return response

    def test_final_signature_approves_and_audits_without_changing_commercial_data_or_original(self):
        before = self.snapshot()
        response = self.client.post(self.url, self.payload(), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'approved')
        self.assertEqual(self.pr.current_approval_step, 4)
        rows = self.pr.price_remarks_data['signed_document_verification']['source_approval_rows']
        self.assertEqual(rows[:3], self.rows[:3])
        self.assertEqual(rows[3]['user_name'], 'Val Verified')
        self.assertTrue(rows[3]['signature_verified'])
        self.assertEqual(rows[3]['status'], 'approved')
        self.assertEqual(rows[3]['signature_source'], 'manual')
        self.assertEqual(rows[3]['approved_at'][:10], '2026-01-29')
        self.assertEqual(self.pr.approval_workflow_config, rows)
        self.assertTrue(self.pr.price_remarks_data['signed_document_verification']['signed_off'])
        self.assertEqual(self.pr.vp_op_approval_status, 'approved')
        self.assertEqual(self.pr.vp_op_signature, self.detail_url + 'uploaded-documents/0/content/')
        for field in ('supplier_name', 'product_service', 'total_price', 'net_total_excl_vat', 'currency', 'items', 'price_remarks', 'attachments'):
            self.assertEqual(getattr(self.pr, field), before[field], field)
        for key in ('budget_allocation', 'payment_terms', 'line_details'):
            self.assertEqual(self.pr.price_remarks_data[key], self.metadata[key])
        audit = self.pr.price_remarks_data['source_approval_reviews']
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]['before'], self.rows[3])
        self.assertEqual(audit[0]['after'], rows[3])
        self.assertEqual(audit[0]['reviewed_by_id'], str(self.owner.pk))
        self.assertEqual(audit[0]['document_sha256'], DIGEST)
        self.assertTrue(audit[0]['reviewed_at'])
        self.storage_open.assert_called_once_with(SOURCE_KEY, 'rb')
        self.assertEqual(response.data['status'], 'approved')
        self.assertEqual(response.data['approval_workflow_config'], rows)
        self.assertEqual(response.data['attachments'][0]['sha256'], DIGEST)

    def test_name_only_on_unverified_row_preserves_signature_and_does_not_approve(self):
        response = self.client.post(self.url, self.payload(
            signature_verified=False, approval_date='', approver_name='Corrected Vice President',
        ), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'draft')
        self.assertEqual(self.pr.approval_workflow_config, [])
        row = self.pr.price_remarks_data['signed_document_verification']['source_approval_rows'][3]
        self.assertEqual(row, {**self.rows[3], 'user_name': 'Corrected Vice President', 'special_note': self.payload()['special_note']})
        self.assertFalse(self.pr.price_remarks_data['signed_document_verification']['signed_off'])

    def test_current_source_token_returns_fresh_token_for_the_next_form_save(self):
        token = self.pr.updated_at.isoformat()
        response = self.client.post(self.url, self.payload(
            expected_updated_at=token, signature_verified=False, approval_date='',
            approver_name='Corrected Vice President',
        ), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        next_token = response.data['updated_at']
        self.assertNotEqual(next_token, token)
        self.assertNotIn('expected_updated_at', response.data)
        self.pr.refresh_from_db()
        reviewed_metadata = deepcopy(self.pr.price_remarks_data)
        saved = self.client.patch(self.detail_url, {
            'expected_updated_at': next_token, 'notes': 'Preserved editor input after source review',
        }, format='json')
        self.assertEqual(saved.status_code, 200, saved.data)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.price_remarks_data, reviewed_metadata)
        self.assertEqual(self.pr.notes, 'Preserved editor input after source review')
        self.assertEqual(self.pr.status, 'draft')

    def test_stale_source_token_cannot_approve_after_concurrent_commercial_edit(self):
        token = self.pr.updated_at.isoformat()
        payload = self.payload(expected_updated_at=token)
        changed = self.client.patch(self.detail_url, {
            'expected_updated_at': token, 'price_remarks': 'New negotiated commercial terms',
        }, format='json')
        self.assertEqual(changed.status_code, 200, changed.data)
        before = self.snapshot()
        response = self.assert_rejected_unchanged(payload, 409)
        self.assertEqual(response.data['code'], 'stale_requisition')
        self.assertEqual(before['status'], 'draft')
        self.assertEqual(before['price_remarks_data']['source_approval_reviews'], [])
        self.storage_open.assert_not_called()
        self.client.force_authenticate(self.reader)
        self.assert_rejected_unchanged(payload, 403)
        self.storage_open.assert_not_called()

    def test_invalid_source_version_tokens_cannot_read_storage_or_change_evidence(self):
        for token in ('', None, 'not-a-timestamp'):
            with self.subTest(token=token):
                response = self.assert_rejected_unchanged(self.payload(expected_updated_at=token))
                self.assertIn('expected_updated_at', response.data)
        self.storage_open.assert_not_called()

    def test_partial_signature_stays_draft_until_last_outstanding_source_row_is_verified(self):
        rows = deepcopy(self.rows)
        rows[2] = source_row('MoP', 'Morgan Projects', verified=False)
        self.save_rows(rows)
        response = self.client.post(self.url, self.payload(), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'draft')
        self.assertEqual(self.pr.approval_workflow_config, [])
        self.assertFalse(self.pr.price_remarks_data['signed_document_verification']['signed_off'])
        response = self.client.post(self.url, self.payload(index=2, approver_name='Morgan Projects'), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'approved')
        self.assertEqual(len(self.pr.price_remarks_data['source_approval_reviews']), 2)

    def test_missing_name_on_verified_final_row_preserves_exact_signature_and_date(self):
        rows = deepcopy(self.rows)
        rows[3] = source_row('Vp, Op', '', verified=True)
        rows[3]['approved_at'] = '2026-01-28T07:32:45+04:00'
        self.save_rows(rows)
        existing_date = timezone.now() - timedelta(days=7)
        self.pr.vp_op_signature = '/media/original-existing-vp-signature.png'
        self.pr.vp_op_approved_at = existing_date
        self.pr.approved_at = existing_date
        self.pr.pm_signature = '/media/original-existing-pm-signature.png'
        self.pr.pm_approved_at = existing_date
        self.pr.pm_approval_status = 'approved'
        self.pr.pm_name = self.owner
        self.pr.save(update_fields=['vp_op_signature', 'vp_op_approved_at', 'approved_at', 'pm_signature', 'pm_approved_at', 'pm_approval_status', 'pm_name'])
        response = self.client.post(self.url, self.payload(signature_verified=False, approval_date=''), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        updated = self.pr.price_remarks_data['signed_document_verification']['source_approval_rows'][3]
        self.assertEqual(updated, {**rows[3], 'user_name': 'Val Verified', 'special_note': self.payload()['special_note']})
        self.assertEqual(self.pr.status, 'approved')
        self.assertFalse(self.pr.price_remarks_data['source_approval_reviews'][0]['signature_verified'])
        self.assertEqual(self.pr.vp_op_signature, '/media/original-existing-vp-signature.png')
        self.assertEqual(self.pr.vp_op_approved_at, existing_date)
        self.assertEqual(self.pr.approved_at, existing_date)
        self.assertEqual(self.pr.pm_signature, '/media/original-existing-pm-signature.png')
        self.assertEqual(self.pr.pm_approved_at, existing_date)
        self.assertEqual(self.pr.pm_name_id, self.owner.pk)

    def test_partial_existing_source_workflow_updates_only_exact_matching_row(self):
        rows = deepcopy(self.rows)
        rows[2] = source_row('MoP', 'Morgan Projects', verified=False)
        self.save_rows(rows)
        self.pr.approval_workflow_config = deepcopy(rows)
        self.pr.save(update_fields=['approval_workflow_config'])
        response = self.client.post(self.url, self.payload(), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'draft')
        self.assertEqual(self.pr.approval_workflow_config[:3], rows[:3])
        source_rows = self.pr.price_remarks_data['signed_document_verification']['source_approval_rows']
        self.assertEqual(self.pr.approval_workflow_config[3], source_rows[3])
        self.assertEqual(source_rows[:3], rows[:3])

    def test_partial_source_edit_leaves_unrelated_internal_workflow_untouched(self):
        rows = deepcopy(self.rows)
        rows[2] = source_row('MoP', 'Morgan Projects', verified=False)
        self.save_rows(rows)
        internal = [{'role': 'PM', 'user_id': str(self.reader.pk), 'status': 'pending'}]
        self.pr.approval_workflow_config = internal
        self.pr.save(update_fields=['approval_workflow_config'])
        response = self.client.post(self.url, self.payload(), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.approval_workflow_config, internal)
        self.assertEqual(self.pr.status, 'draft')

    def test_legacy_approved_missing_name_is_verified_without_explicit_flag(self):
        rows = deepcopy(self.rows)
        rows[3] = source_row('Vp, Op', '', verified=True)
        rows[3].pop('signature_verified')
        self.save_rows(rows)
        response = self.client.post(self.url, self.payload(signature_verified=False, approval_date=''), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'approved')
        self.assertEqual(self.pr.approval_workflow_config[3], {**rows[3], 'user_name': 'Val Verified', 'special_note': self.payload()['special_note']})

    def test_converted_record_keeps_converted_status_when_source_evidence_completes(self):
        self.pr.status = 'converted'
        self.pr.save(update_fields=['status'])
        response = self.client.post(self.url, self.payload(), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'converted')
        self.assertEqual(len(self.pr.approval_workflow_config), 4)

    def test_admin_with_update_grant_can_correct_another_issuers_source_evidence(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(self.url, self.payload(), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.price_remarks_data['source_approval_reviews'][0]['reviewed_by_id'], str(self.admin.pk))

    def test_read_permission_and_workflow_assignment_do_not_allow_nonissuer_mutation(self):
        self.pr.requested_by = self.reader
        self.pr.approval_workflow_config = [{'role': 'PM', 'user_id': str(self.reader.pk), 'status': 'pending'}]
        self.pr.save(update_fields=['requested_by', 'approval_workflow_config'])
        self.client.force_authenticate(self.reader)
        self.assert_rejected_unchanged(self.payload(), 403)
        self.storage_open.assert_not_called()

    def test_update_permission_is_required_even_for_issuer_and_admin(self):
        for actor in (self.owner, self.admin):
            with self.subTest(actor=actor.username):
                self.grant(actor, 'update', False)
                self.client.force_authenticate(actor)
                self.assert_rejected_unchanged(self.payload(), 403)
        self.storage_open.assert_not_called()

    def test_issuer_with_only_read_grant_cannot_correct_source_evidence(self):
        UserPermissionOverride.objects.filter(
            user_profile=self.profiles[self.owner.pk], permission=self.permissions['update'],
        ).delete()
        cache.clear()
        self.assert_rejected_unchanged(self.payload(), 403)
        self.storage_open.assert_not_called()

    def test_anonymous_request_cannot_read_storage_or_write_evidence(self):
        self.client.force_authenticate(None)
        before = self.snapshot()
        response = self.client.post(self.url, self.payload(), format='json')
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(self.snapshot(), before)
        self.storage_open.assert_not_called()

    def test_completed_source_row_is_immutable(self):
        for confirmed in (False, True):
            self.assert_rejected_unchanged(self.payload(
                index=0, signature_verified=confirmed, approval_date='2026-01-29' if confirmed else '',
            ), 409)
        self.storage_open.assert_not_called()

    def test_stale_row_or_document_digest_is_rejected_without_storage_access(self):
        stale = self.payload()
        stale['expected_row']['user_name'] = 'Stale extracted name'
        self.assert_rejected_unchanged(stale, 409)
        self.assert_rejected_unchanged(self.payload(document_sha256='f' * 64), 409)
        self.assert_rejected_unchanged(self.payload(row_index=99), 409)
        self.storage_open.assert_not_called()

    def test_invalid_payload_types_dates_and_extra_changes_are_rejected(self):
        invalid = [
            {'document_sha256': 'not-a-hash'}, {'row_index': True}, {'row_index': '3'}, {'row_index': -1},
            {'expected_row': []}, {'approver_name': ''}, {'approver_name': ' '}, {'approver_name': 7},
            {'approver_name': 'a' * 201}, {'approver_name': 'Name\nInjected'},
            {'signature_verified': 'true'}, {'signature_verified': 1}, {'signature_verified': None},
            {'approval_date': ''}, {'approval_date': '2026-02-30'}, {'approval_date': '29/01/2026'},
            {'approval_date': '2026-01-29T12:00:00Z'}, {'approval_date': None},
            {'approval_date': (timezone.localdate() + timedelta(days=1)).isoformat()},
            {'signature_verified': False, 'approval_date': '2026-01-29'},
            {'attachments': []},
        ]
        for changes in invalid:
            with self.subTest(changes=changes):
                self.assert_rejected_unchanged(self.payload(**changes))
        for key in ('document_sha256', 'row_index', 'expected_row', 'approver_name', 'signature_verified'):
            payload = self.payload()
            del payload[key]
            with self.subTest(missing=key):
                self.assert_rejected_unchanged(payload)
        # Only the read-only expected_row snapshot is exempt from the generic
        # approval-payload gate; attempted top-level approval still needs it.
        self.assert_rejected_unchanged(self.payload(status='approved'), 403)
        self.storage_open.assert_not_called()

    def test_internal_or_rejected_source_rows_cannot_be_promoted(self):
        variations = [
            {'external': 'true'}, {'external': False}, {'source': 'internal'},
            {'role': 'Unrecognized custom approver'},
            *({'status': value} for value in ('rejected', 'not_approved', 'declined', 'denied', 'cancelled')),
        ]
        for changes in variations:
            rows = deepcopy(self.rows)
            rows[3].update(changes)
            self.save_rows(rows)
            with self.subTest(changes=changes):
                self.assert_rejected_unchanged(self.payload())
        self.storage_open.assert_not_called()

    def test_complete_source_evidence_cannot_replace_unrelated_internal_workflow(self):
        self.pr.approval_workflow_config = [{'role': 'PM', 'user_id': str(self.reader.pk), 'status': 'pending'}]
        self.pr.save(update_fields=['approval_workflow_config'])
        self.assert_rejected_unchanged(self.payload(), 409)

    def test_missing_or_foreign_source_attachment_cannot_authorize_storage_reads(self):
        for attachments in (
            [],
            [{**self.attachments[0], 'sha256': 'f' * 64}],
            [{**self.attachments[0], 'type': 'quotation'}],
            [{**self.attachments[0], 'storage_key': SOURCE_KEY.replace(str(PR_ID), str(UUID(int=1)))}],
            [{**self.attachments[0], 'storage_key': 'private/another-document.pdf'}],
        ):
            self.pr.attachments = attachments
            self.pr.save(update_fields=['attachments'])
            with self.subTest(attachments=attachments):
                self.assert_rejected_unchanged(self.payload(), 404)
        self.storage_open.assert_not_called()

    def test_changed_pdf_bytes_and_non_pdf_content_reject_without_mutation(self):
        for content in (PDF_BYTES + b'changed', b'not a PDF'):
            self.storage_open.side_effect = lambda *_args, _content=content, **_kwargs: BytesIO(_content)
            with self.subTest(content=content):
                self.assert_rejected_unchanged(self.payload(), 409)

    def test_unavailable_storage_returns_retryable_or_missing_status_without_private_details(self):
        failures = (
            (FileNotFoundError('private internal key'), 404),
            (ClientError({'Error': {'Code': 'NoSuchKey', 'Message': 'private key'}}, 'GetObject'), 404),
            (NoCredentialsError(), 503),
            (ClientError({'Error': {'Code': 'AccessDenied', 'Message': 'private bucket'}}, 'GetObject'), 503),
        )
        for error, status in failures:
            self.storage_open.side_effect = error
            with self.subTest(error=type(error).__name__):
                response = self.assert_rejected_unchanged(self.payload(), status)
                self.assertNotIn('private', str(response.data))

    def test_normal_patch_cannot_forge_or_erase_source_review_audit_and_evidence(self):
        response = self.client.post(self.url, self.payload(), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        protected_keys = ('source_approval_reviews', 'signed_document_verification', 'signed_approval_evidence', 'signed_pdf_attached')
        protected = {key: deepcopy(self.pr.price_remarks_data[key]) for key in protected_keys}
        for metadata in ({'payment_terms': 'Net 60'}, {
            'payment_terms': 'Net 90', 'source_approval_reviews': [],
            'signed_document_verification': {'signed_off': False},
            'signed_approval_evidence': {}, 'signed_pdf_attached': False,
        }):
            response = self.client.patch(self.detail_url, {'price_remarks_data': metadata}, format='json')
            self.assertEqual(response.status_code, 200, response.data)
            self.pr.refresh_from_db()
            self.assertEqual(self.pr.price_remarks_data['payment_terms'], metadata['payment_terms'])
            self.assertEqual({key: self.pr.price_remarks_data[key] for key in protected_keys}, protected)
            self.assertEqual(self.pr.status, 'approved')
            self.assertEqual(self.pr.attachments, self.attachments)

    def test_stale_validated_form_save_cannot_overwrite_concurrent_source_approval(self):
        old_pr = PurchaseRequisition.objects.get(pk=self.pr.pk)
        old_metadata = deepcopy(old_pr.price_remarks_data)
        old_metadata['payment_terms'] = 'Net 45, edited before approval review'
        serializer = PurchaseRequisitionSerializer(
            old_pr,
            data={
                'price_remarks': 'Commercial edit must survive',
                'price_remarks_data': old_metadata,
                'approval_workflow_config': [],
            },
            partial=True,
            context={'request': SimpleNamespace(user=self.owner)},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        response = self.client.post(self.url, self.payload(), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        approved_metadata = deepcopy(self.pr.price_remarks_data)
        approved_workflow = deepcopy(self.pr.approval_workflow_config)
        serializer.save()
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'approved')
        self.assertEqual(self.pr.approval_workflow_config, approved_workflow)
        self.assertEqual(self.pr.price_remarks, 'Commercial edit must survive')
        self.assertEqual(self.pr.price_remarks_data['payment_terms'], old_metadata['payment_terms'])
        for key in ('source_approval_reviews', 'signed_document_verification', 'signed_approval_evidence', 'signed_pdf_attached'):
            self.assertEqual(self.pr.price_remarks_data[key], approved_metadata[key], key)
        self.assertEqual(self.pr.attachments, self.attachments)

    def test_stale_form_save_preserves_concurrent_partial_external_workflow_correction(self):
        rows = deepcopy(self.rows)
        rows[2] = source_row('MoP', 'Morgan Projects', verified=False)
        self.save_rows(rows)
        self.pr.approval_workflow_config = deepcopy(rows)
        self.pr.save(update_fields=['approval_workflow_config'])
        serializer = PurchaseRequisitionSerializer(
            PurchaseRequisition.objects.get(pk=self.pr.pk),
            data={
                'price_remarks': 'Preserved partial-record commercial edit',
                'price_remarks_data': deepcopy(self.pr.price_remarks_data),
                'approval_workflow_config': deepcopy(rows),
            },
            partial=True,
            context={'request': SimpleNamespace(user=self.owner)},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        response = self.client.post(self.url, self.payload(), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        updated_workflow = deepcopy(self.pr.approval_workflow_config)
        updated_metadata = deepcopy(self.pr.price_remarks_data)
        serializer.save()
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'draft')
        self.assertEqual(self.pr.price_remarks, 'Preserved partial-record commercial edit')
        self.assertEqual(self.pr.approval_workflow_config, updated_workflow)
        self.assertEqual(self.pr.price_remarks_data['source_approval_reviews'], updated_metadata['source_approval_reviews'])
        self.assertEqual(self.pr.price_remarks_data['signed_document_verification'], updated_metadata['signed_document_verification'])
        self.assertFalse(self.pr.price_remarks_data['signed_document_verification']['signed_off'])
