"""Pending receipt deletion keeps decided evidence, authority and durable audit."""
from copy import deepcopy
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.procurement.models import ProcurementNumberSequence, PurchaseOrder, Receipt
from apps.procurement.services.receiving import receiving_summary
from apps.rbac.models import AuditLog, Module, RolePermission
from . import test_receiving_handoff as fixtures


@override_settings(ROOT_URLCONF=fixtures.__name__, RADAI_BUSINESS_APPROVAL_ROUTES={})
class ReceiptDeletionTests(TestCase):
    grant = fixtures.ReceivingHandoffTests.grant
    deny = fixtures.ReceivingHandoffTests.deny
    order = fixtures.ReceivingHandoffTests.order
    payload = fixtures.ReceivingHandoffTests.payload
    record = fixtures.ReceivingHandoffTests.record
    decide = fixtures.ReceivingHandoffTests.decide

    def setUp(self):
        fixtures.ReceivingHandoffTests.setUp(self)
        self.grant('procurement_receipts', 'delete')

    def remove(self, data, **extra):
        return self.client.delete(fixtures.BASE + f"receipts/{data['id']}/",
                                  {'expected_updated_at': data['updated_at'], **extra}, format='json')

    def detail(self, data):
        return self.client.get(fixtures.BASE + f"receipts/{data['id']}/")

    def audits(self, data):
        return AuditLog.objects.filter(resource_type='Receipt', resource_id=data['id'],
                                       metadata__command='delete_pending_receipt')

    def test_delete_releases_reservation_and_retains_actor_receipt_and_history(self):
        po = self.order()
        data = self.record(po, '4.25', delivery_note_number='SYNTHETIC-DN', notes='Incorrect pending entry')
        before = Receipt.objects.get(pk=data['id'])
        self.assertTrue(data['deletion']['can_delete'])
        self.assertTrue(data['capabilities']['delete'])
        self.assertEqual(receiving_summary(po)['lines'][0]['pending'], '4.25')
        po.refresh_from_db()
        old_timestamp, old_approval = po.updated_at, deepcopy(po.approval_log)
        response = self.remove(data)
        self.assertEqual(response.status_code, 204, response.data)
        self.assertFalse(Receipt.objects.filter(pk=data['id']).exists())
        self.assertEqual(self.detail(data).status_code, 404)
        self.assertEqual(receiving_summary(po)['lines'][0]['available'], '10.50')
        po.refresh_from_db()
        self.assertGreater(po.updated_at, old_timestamp)
        self.assertEqual(po.status, 'sent')
        self.assertEqual(po.approval_log, old_approval)
        audit = self.audits(data).get()
        self.assertEqual(audit.action, 'delete')
        self.assertEqual(audit.user, self.user)
        self.assertEqual(audit.user_email, self.user.email)
        self.assertIsNotNone(audit.timestamp)
        self.assertEqual(audit.changes['before']['receipt_number'], data['receipt_number'])
        self.assertEqual(audit.changes['before']['items_received'], before.items_received)
        self.assertEqual(audit.changes['before']['workflow_history'], before.workflow_history)
        self.assertEqual(audit.changes['before']['received_by_id'], self.user.pk)
        self.assertEqual(audit.metadata['operation_key'], str(before.operation_key))
        self.assertIsNone(audit.changes['after'])

    def test_pending_receipt_with_missing_source_approval_or_basis_can_be_removed(self):
        for change in ({'approval_log': []}, {'items': []}, {'status': 'cancelled'}):
            with self.subTest(change=change):
                po = self.order()
                data = self.record(po)
                PurchaseOrder.objects.filter(pk=po.pk).update(**change)
                detail = self.detail(data).data
                self.assertFalse(detail['confirmation']['can_confirm'])
                self.assertTrue(detail['deletion']['can_delete'])
                self.assertEqual(self.remove(data).status_code, 204)
                po.refresh_from_db()
                for field, value in change.items():
                    self.assertEqual(getattr(po, field), value)

    def test_delete_uses_existing_delete_authority_not_recorder_or_create_approval(self):
        data = self.record(self.order())
        other = get_user_model().objects.create_user('original-receipt-recorder')
        Receipt.objects.filter(pk=data['id']).update(received_by=other)
        RolePermission.objects.filter(role=self.role, permission__module__code='procurement_receipts',
                                      permission__action__in=['create', 'approve', 'update']).delete()
        cache.clear()
        self.assertTrue(self.detail(data).data['deletion']['can_delete'])
        self.assertEqual(self.remove(data).status_code, 204)
        self.assertEqual(self.audits(data).get().changes['before']['received_by_id'], other.pk)

    def test_missing_delete_grant_and_explicit_denials_prevent_deletion(self):
        data = self.record(self.order())
        RolePermission.objects.filter(role=self.role, permission__module__code='procurement_receipts',
                                      permission__action='delete').delete()
        cache.clear()
        detail = self.detail(data).data
        self.assertFalse(detail['deletion']['can_delete'])
        self.assertIn('access', detail['deletion']['blocked_reason'])
        self.assertEqual(self.remove(data).status_code, 403)
        self.grant('procurement_receipts', 'delete')
        for module, action in (('procurement_receipts', 'delete'), ('procurement_receipts', 'read'),
                               ('procurement_orders', 'read')):
            with self.subTest(module=module, action=action):
                self.profile.permission_overrides.all().delete()
                self.deny(module, action)
                self.assertEqual(self.remove(data).status_code, 403)
        self.assertTrue(Receipt.objects.filter(pk=data['id']).exists())
        self.assertFalse(self.audits(data).exists())

    def test_inactive_account_profile_and_module_cannot_delete(self):
        data = self.record(self.order())
        for obj, field, value in ((self.user, 'is_active', False), (self.profile, 'status', 'inactive'),
                                  (self.profile, 'is_deleted', True),
                                  (Module.objects.get(code='procurement_receipts'), 'is_active', False)):
            with self.subTest(field=field):
                old = getattr(obj, field)
                setattr(obj, field, value)
                obj.save(update_fields=[field])
                self.client.force_authenticate(get_user_model().objects.get(pk=self.user.pk))
                cache.clear()
                self.assertEqual(self.remove(data).status_code, 403)
                setattr(obj, field, old)
                obj.save(update_fields=[field])
        self.assertTrue(Receipt.objects.filter(pk=data['id']).exists())

    def test_confirmed_partial_rejected_and_prior_decision_evidence_is_retained(self):
        for state in ('accepted', 'partial', 'rejected'):
            with self.subTest(state=state):
                data = self.record(self.order())
                Receipt.objects.filter(pk=data['id']).update(status=state)
                self.assertFalse(self.detail(data).data['deletion']['can_delete'])
                self.assertEqual(self.remove(data).status_code, 409)
                self.assertTrue(Receipt.objects.filter(pk=data['id'], status=state).exists())
        for history in ([{'action': 'confirm_delivery'}], [{'action': 'accept'}],
                        [{'action': 'reject_delivery'}], [{'status': 'accepted'}], ['unreadable'], {}):
            data = self.record(self.order())
            Receipt.objects.filter(pk=data['id']).update(workflow_history=history)
            self.assertFalse(self.detail(data).data['deletion']['can_delete'])
            self.assertEqual(self.remove(data).status_code, 409)
            self.assertFalse(self.audits(data).exists())

    def test_real_confirmation_cannot_be_deleted_and_requires_original_po_guards(self):
        data = self.record(self.order())
        result = self.decide(data, 'confirm_delivery')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertFalse(result.data['deletion']['can_delete'])
        self.assertEqual(self.remove(result.data).status_code, 409)
        self.assertEqual(Receipt.objects.get(pk=data['id']).status, 'accepted')

    def test_missing_malformed_extra_fields_and_stale_input_have_no_effect(self):
        data = self.record(self.order())
        url = fixtures.BASE + f"receipts/{data['id']}/"
        for body in ({}, {'expected_updated_at': 'invalid'}, ['bad'], 'bad', 123,
                     {'expected_updated_at': data['updated_at'], 'status': 'pending'}):
            response = self.client.delete(url, body, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        update = self.client.patch(url, {'expected_updated_at': data['updated_at'], 'notes': 'Updated remarks'}, format='json')
        self.assertEqual(update.status_code, 200, update.data)
        self.assertEqual(self.remove(data).status_code, 409)
        self.assertEqual(Receipt.objects.get(pk=data['id']).notes, 'Updated remarks')
        self.assertFalse(self.audits(data).exists())
        self.assertEqual(self.remove(update.data).status_code, 204)

    def test_missing_target_and_repeated_delete_are_404_with_single_audit(self):
        data = self.record(self.order())
        self.assertEqual(self.remove(data).status_code, 204)
        self.assertEqual(self.remove(data).status_code, 404)
        self.assertEqual(self.remove({**data, 'id': str(uuid4())}).status_code, 404)
        self.assertEqual(self.audits(data).count(), 1)

    def test_deleted_creation_retry_cannot_resurrect_receipt_and_number_is_not_reused(self):
        po = self.order()
        payload = self.payload(po)
        first = self.client.post(fixtures.BASE + 'receipts/', payload, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(self.remove(first.data).status_code, 204)
        po.refresh_from_db()
        payload['expected_po_updated_at'] = po.updated_at.isoformat()
        response = self.client.post(fixtures.BASE + 'receipts/', payload, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertIn('deleted', str(response.data))
        replacement = self.record(po)
        self.assertNotEqual(replacement['receipt_number'], first.data['receipt_number'])
        self.assertNotEqual(replacement['id'], first.data['id'])

    def test_legacy_highest_number_is_reserved_before_deleting(self):
        data = self.record(self.order())
        year = timezone.localdate().year
        Receipt.objects.filter(pk=data['id']).update(receipt_number=f'RAD-GR-9999_{year}')
        ProcurementNumberSequence.objects.filter(document_type='GR', year=year).delete()
        self.assertEqual(self.remove(data).status_code, 204)
        self.assertEqual(self.record(self.order())['receipt_number'], f'RAD-GR-10000_{year}')

    def test_retained_attachment_metadata_does_not_delete_storage(self):
        data = self.record(self.order(), attachments=[{'name': 'Evidence.pdf', 's3_key': 'synthetic/receipt-evidence.pdf'}])
        with patch('django.core.files.storage.default_storage.delete') as storage_delete:
            self.assertEqual(self.remove(data).status_code, 204)
        storage_delete.assert_not_called()
        self.assertEqual(self.audits(data).get().changes['before']['attachments'], data['attachments'])

    def test_later_order_cleanup_preserves_retained_receipt_attachment_bytes(self):
        from apps.procurement.services.procurement_lifecycle import delete_order
        po = self.order()
        key = default_storage.save(f'procurement/orders/{po.po_number}/synthetic-proof.pdf',
                                   ContentFile(b'synthetic receipt evidence'))
        self.addCleanup(default_storage.delete, key)
        attachments = [{'name': 'Synthetic proof.pdf', 's3_key': key}]
        po.attachments = attachments
        po.save(update_fields=['attachments', 'updated_at'])
        data = self.record(po, attachments=attachments)
        self.assertEqual(self.remove(data).status_code, 204)
        with self.captureOnCommitCallbacks(execute=True):
            delete_order(po.pk)
        self.assertFalse(PurchaseOrder.objects.filter(pk=po.pk).exists())
        self.assertTrue(default_storage.exists(key))
        self.assertEqual(self.audits(data).get().changes['before']['attachments'], attachments)

    def test_service_value_deletion_releases_exact_pending_net_value(self):
        po = self.order(items=[], category='engineering_services', scope_of_services='Review specifications',
                        vat_basis='exclusive', net_amount='100.00', total_amount='105.00', currency='AED')
        data = self.record(po, items_received=[{'line_id': 'service:total', 'received_amount': '40.25'}])
        self.assertEqual(receiving_summary(po)['lines'][0]['pending'], '40.25')
        self.assertEqual(self.remove(data).status_code, 204)
        self.assertEqual(receiving_summary(po)['lines'][0]['available'], '100.00')
        self.assertEqual(self.audits(data).get().changes['before']['items_received'][0]['received_amount'], '40.25')

    def test_audit_delete_and_parent_save_failures_roll_back_receipt_and_balance(self):
        for target in ('apps.procurement.services.receiving.create_audit_log',
                       'apps.procurement.models.Receipt.delete', 'apps.procurement.models.PurchaseOrder.save'):
            with self.subTest(target=target):
                po = self.order()
                data = self.record(po)
                po.refresh_from_db()
                before = po.updated_at
                with patch(target, side_effect=ValidationError('Synthetic transaction failure')):
                    response = self.remove(data)
                self.assertEqual(response.status_code, 400, response.data)
                self.assertTrue(Receipt.objects.filter(pk=data['id']).exists())
                self.assertFalse(self.audits(data).exists())
                po.refresh_from_db()
                self.assertEqual(po.updated_at, before)
                self.assertEqual(receiving_summary(po)['lines'][0]['pending'], '2.50')
