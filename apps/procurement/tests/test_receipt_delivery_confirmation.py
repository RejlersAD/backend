"""Recorder delivery confirmation through the real guarded receipt router."""
from copy import deepcopy
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.exceptions import ValidationError

from apps.procurement.models import PurchaseOrder, Receipt
from apps.procurement.services.receiving import INSPECTION_FLAGS, receiving_summary
from apps.rbac.models import Module, RolePermission
from . import test_receiving_handoff as fixtures


@override_settings(ROOT_URLCONF=fixtures.__name__, RADAI_BUSINESS_APPROVAL_ROUTES={})
class ReceiptDeliveryConfirmationTests(TestCase):
    setUp = fixtures.ReceivingHandoffTests.setUp
    grant = fixtures.ReceivingHandoffTests.grant
    deny = fixtures.ReceivingHandoffTests.deny
    order = fixtures.ReceivingHandoffTests.order
    payload = fixtures.ReceivingHandoffTests.payload
    record = fixtures.ReceivingHandoffTests.record
    decide = fixtures.ReceivingHandoffTests.decide

    def confirm(self, data, **extra):
        return self.decide(data, 'confirm_delivery', **extra)

    def detail(self, data):
        return self.client.get(fixtures.BASE + f"receipts/{data['id']}/")

    def remove_approval_grant(self):
        RolePermission.objects.filter(role=self.role, permission__module__code='procurement_receipts',
                                      permission__action='approve').delete()
        cache.clear()

    def test_recorder_without_approval_grant_confirms_and_records_identity(self):
        self.remove_approval_grant()
        self.user.first_name = 'Recorded'
        self.user.last_name = 'Receiver'
        self.user.save(update_fields=['first_name', 'last_name'])
        po = self.order()
        data = self.record(po, '10.50', notes='Original receipt remarks')
        self.assertEqual(data['status'], 'pending')
        self.assertTrue(data['confirmation']['can_confirm'])
        self.assertEqual(data['confirmation']['responsible_user_id'], self.user.pk)
        self.assertEqual(data['confirmation']['responsible_user_name'], 'Recorded Receiver')
        self.assertFalse(data['capabilities']['accept'])
        self.assertFalse(data['capabilities']['reject'])
        response = self.confirm(data, notes='Verified delivery note')
        self.assertEqual(response.status_code, 200, response.data)
        receipt = Receipt.objects.get(pk=data['id'])
        self.assertEqual(receipt.status, 'accepted')
        self.assertEqual(receipt.notes, 'Original receipt remarks')
        self.assertEqual(receipt.inspector_name, '')
        self.assertEqual(receipt.inspection_notes, '')
        for flag in INSPECTION_FLAGS:
            self.assertIsNone(getattr(receipt, flag))
        event = receipt.workflow_history[-1]
        self.assertEqual(event['action'], 'confirm_delivery')
        self.assertEqual(event['actor_id'], self.user.pk)
        self.assertEqual(event['reason'], 'Verified delivery note')
        confirmation = response.data['confirmation']
        self.assertFalse(confirmation['can_confirm'])
        self.assertEqual(confirmation['confirmed_by_id'], self.user.pk)
        self.assertEqual(confirmation['confirmed_by_name'], 'Recorded Receiver')
        self.assertEqual(confirmation['confirmed_at'], event['at'])
        po.refresh_from_db()
        self.assertEqual(po.status, 'sent')
        self.assertEqual(receiving_summary(po)['status'], 'complete')

    def test_recorder_does_not_gain_inspection_or_rejection_authority(self):
        self.remove_approval_grant()
        data = self.record(self.order())
        for action in ('accept', 'reject_delivery'):
            self.assertEqual(self.decide(data, action, reason='Recorded issue').status_code, 403)
        self.assertEqual(self.confirm(data).status_code, 200)

    def test_other_recorder_and_missing_recorder_are_denied_even_to_superuser(self):
        other = get_user_model().objects.create_user('other-receiver')
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        for owner in (other, None):
            with self.subTest(owner=owner):
                data = self.record(self.order())
                Receipt.objects.filter(pk=data['id']).update(received_by=owner)
                result = self.detail(data)
                self.assertEqual(result.status_code, 200)
                self.assertFalse(result.data['confirmation']['can_confirm'])
                self.assertEqual(result.data['confirmation']['responsible_user_id'], owner.pk if owner else None)
                self.assertEqual(self.confirm(data).status_code, 403)
                self.assertEqual(Receipt.objects.get(pk=data['id']).status, 'pending')

    def test_explicit_denials_apply_without_global_approval_grant_and_on_replay(self):
        self.remove_approval_grant()
        for module, action in (('procurement_receipts', 'create'), ('procurement_receipts', 'read'),
                               ('procurement_receipts', 'approve'), ('procurement_orders', 'read')):
            with self.subTest(module=module, action=action):
                self.profile.permission_overrides.all().delete()
                cache.clear()
                data = self.record(self.order())
                self.assertEqual(self.confirm(data).status_code, 200)
                self.deny(module, action)
                self.assertEqual(self.confirm(data).status_code, 403)
                self.assertEqual(len(Receipt.objects.get(pk=data['id']).workflow_history), 2)

    def test_pending_denial_is_projected_with_recorder_responsibility(self):
        data = self.record(self.order())
        self.deny('procurement_receipts', 'approve')
        result = self.detail(data)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertFalse(result.data['confirmation']['can_confirm'])
        self.assertIn('denied', result.data['confirmation']['blocked_reason'])
        self.assertEqual(result.data['confirmation']['responsible_user_id'], self.user.pk)
        self.assertEqual(self.confirm(data).status_code, 403)
        self.assertEqual(Receipt.objects.get(pk=data['id']).status, 'pending')

    def test_revoked_create_grant_inactive_identity_profile_or_module_denies(self):
        data = self.record(self.order())
        cases = (
            (self.user, 'is_active', False),
            (self.profile, 'status', 'inactive'),
            (self.profile, 'is_deleted', True),
            (Module.objects.get(code='procurement_receipts'), 'is_active', False),
        )
        for obj, field, value in cases:
            with self.subTest(field=field):
                previous = getattr(obj, field)
                setattr(obj, field, value)
                obj.save(update_fields=[field])
                self.client.force_authenticate(get_user_model().objects.get(pk=self.user.pk))
                cache.clear()
                self.assertEqual(self.confirm(data).status_code, 403)
                setattr(obj, field, previous)
                obj.save(update_fields=[field])
        self.client.force_authenticate(get_user_model().objects.get(pk=self.user.pk))
        RolePermission.objects.filter(role=self.role, permission__module__code='procurement_receipts',
                                      permission__action='create').delete()
        cache.clear()
        self.assertEqual(self.confirm(data).status_code, 403)
        self.assertEqual(Receipt.objects.get(pk=data['id']).status, 'pending')

    def test_po_source_blocks_match_projection_and_cannot_be_bypassed(self):
        for changes, reason in (({'approval_log': []}, 'approval route'), ({'items': []}, 'basis'),
                                ({'status': 'cancelled'}, 'issued')):
            with self.subTest(changes=changes):
                po = self.order()
                data = self.record(po)
                PurchaseOrder.objects.filter(pk=po.pk).update(**changes)
                result = self.detail(data).data['confirmation']
                self.assertFalse(result['can_confirm'])
                self.assertIn(reason, result['blocked_reason'])
                self.assertEqual(result['responsible_user_id'], self.user.pk)
                response = self.confirm(data)
                self.assertEqual(response.status_code, 400, response.data)
                receipt = Receipt.objects.get(pk=data['id'])
                self.assertEqual(receipt.status, 'pending')
                self.assertEqual(len(receipt.workflow_history), 1)

    def test_confirm_retries_once_and_conflicting_retry_preserves_evidence(self):
        data = self.record(self.order())
        first = self.confirm(data, notes='Delivery checked')
        self.assertEqual(first.status_code, 200, first.data)
        second = self.confirm(data, notes='Delivery checked')
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(first.data['updated_at'], second.data['updated_at'])
        self.assertEqual(first.data['confirmation'], second.data['confirmation'])
        self.assertEqual(self.confirm(data, notes='Changed note').status_code, 409)
        self.assertEqual(len(Receipt.objects.get(pk=data['id']).workflow_history), 2)

    def test_stale_or_missing_token_and_invalid_notes_preserve_pending_evidence(self):
        data = self.record(self.order())
        url = fixtures.BASE + f"receipts/{data['id']}/"
        for payload in ({}, {'expected_updated_at': 'invalid'}, {'expected_updated_at': data['updated_at'], 'notes': None},
                        {'expected_updated_at': data['updated_at'], 'notes': 'x' * 4001}):
            response = self.client.post(url + 'confirm_delivery/', payload, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        updated = self.client.patch(url, {'expected_updated_at': data['updated_at'], 'notes': 'Saved remarks'}, format='json')
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(self.confirm(data).status_code, 409)
        receipt = Receipt.objects.get(pk=data['id'])
        self.assertEqual(receipt.notes, 'Saved remarks')
        self.assertEqual(receipt.status, 'pending')
        self.assertEqual(self.confirm(updated.data).status_code, 200)

    def test_confirmation_cannot_write_quality_identity_or_other_receipt_fields(self):
        data = self.record(self.order())
        for field, value in {**{flag: True for flag in INSPECTION_FLAGS}, 'inspector_name': 'Forged inspector',
                             'inspection_notes': 'Pretend test', 'ndt_performed': True, 'received_by': self.user.pk,
                             'items_received': [], 'status': 'accepted', 'workflow_history': []}.items():
            with self.subTest(field=field):
                response = self.confirm(data, **{field: value})
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn(field, response.data)
        receipt = Receipt.objects.get(pk=data['id'])
        self.assertEqual(receipt.status, 'pending')
        self.assertEqual(len(receipt.workflow_history), 1)

    def test_non_object_payload_is_rejected_without_mutation(self):
        data = self.record(self.order())
        url = fixtures.BASE + f"receipts/{data['id']}/confirm_delivery/"
        for payload in (['unexpected'], 'unexpected', 123):
            response = self.client.post(url, payload, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(Receipt.objects.get(pk=data['id']).status, 'pending')

    def test_saved_technical_evidence_remains_unchanged_and_historical_acceptance_is_not_confirmation(self):
        data = self.record(self.order(), inspector_name='Actual independent inspector', inspection_notes='Existing evidence',
                           quality_check_passed=False, visual_inspection_passed=True)
        response = self.confirm(data)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['inspector_name'], 'Actual independent inspector')
        self.assertEqual(response.data['inspection_notes'], 'Existing evidence')
        self.assertFalse(response.data['quality_check_passed'])
        self.assertTrue(response.data['visual_inspection_passed'])
        legacy = self.record(self.order())
        with override_settings(RADAI_BUSINESS_APPROVAL_ROUTES=fixtures.ROUTES):
            accepted = self.decide(legacy)
        self.assertEqual(accepted.status_code, 200, accepted.data)
        self.assertIsNone(accepted.data['confirmation']['confirmed_at'])
        self.assertEqual(self.confirm(legacy).status_code, 400)

    def test_partial_and_service_confirmation_use_existing_decimal_balances(self):
        po = self.order(items=[], category='engineering_services', scope_of_services='Review specifications',
                        vat_basis='exclusive', net_amount='100.00', total_amount='105.00', currency='AED')
        data = self.record(po, items_received=[{'line_id': 'service:total', 'received_amount': '40.25', 'rejected_amount': '0.25'}])
        response = self.confirm(data)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'partial')
        self.assertEqual(receiving_summary(po)['lines'][0]['accepted'], '40.00')
        self.assertNotIn('received_qty', response.data['items_received'][0])
        self.assertIsNotNone(response.data['confirmation']['confirmed_at'])

    def test_corrupt_existing_balance_is_blocked_and_not_canonicalized_into_success(self):
        data = self.record(self.order())
        receipt = Receipt.objects.get(pk=data['id'])
        items = deepcopy(receipt.items_received)
        items[0]['accepted_qty'] = '1'
        Receipt.objects.filter(pk=receipt.pk).update(items_received=items)
        self.assertFalse(self.detail(data).data['confirmation']['can_confirm'])
        self.assertEqual(self.confirm(data).status_code, 400)
        receipt.refresh_from_db()
        self.assertEqual(receipt.items_received, items)
        self.assertEqual(receipt.status, 'pending')

    def test_audit_or_parent_save_failure_rolls_back_all_changes(self):
        for target in ('apps.procurement.services.receiving._history', 'apps.procurement.models.PurchaseOrder.save'):
            with self.subTest(target=target):
                po = self.order()
                data = self.record(po)
                po.refresh_from_db()
                before = po.updated_at
                with patch(target, side_effect=ValidationError('Synthetic transaction failure')):
                    response = self.confirm(data)
                self.assertEqual(response.status_code, 400, response.data)
                receipt = Receipt.objects.get(pk=data['id'])
                po.refresh_from_db()
                self.assertEqual(receipt.status, 'pending')
                self.assertEqual(len(receipt.workflow_history), 1)
                self.assertEqual(po.updated_at, before)
