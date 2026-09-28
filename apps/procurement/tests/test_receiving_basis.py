"""Explicit receiving-basis recovery preserves approved PO evidence and guards."""
from copy import deepcopy
from decimal import Decimal
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings

from apps.procurement.models import PurchaseOrder, Receipt
from apps.procurement.services.receiving import INSPECTION_FLAGS, receiving_summary
from apps.rbac.models import RolePermission, UserPermissionOverride, UserProfile, UserRole
from . import test_receiving_handoff as fixtures
from . import test_handoff_concurrency_postgresql as concurrency_fixtures


@override_settings(ROOT_URLCONF=fixtures.__name__, RADAI_BUSINESS_APPROVAL_ROUTES=fixtures.ROUTES)
class ReceivingBasisTests(TestCase):
    setUp = fixtures.ReceivingHandoffTests.setUp
    grant = fixtures.ReceivingHandoffTests.grant
    deny = fixtures.ReceivingHandoffTests.deny
    order = fixtures.ReceivingHandoffTests.order
    receipt_payload = fixtures.ReceivingHandoffTests.payload
    decide = fixtures.ReceivingHandoffTests.decide

    def missing_order(self, **extra):
        return self.order(**{
            'items': [], 'category': 'other', 'description': 'Uploaded order scope',
            'scope_of_services': '', 'vat_basis': 'unconfirmed', 'net_amount': None,
            **extra,
        })

    def command(self, po, **extra):
        po.refresh_from_db()
        return {
            'operation_key': str(uuid4()), 'expected_updated_at': po.updated_at.isoformat(),
            'basis': 'quantity',
            'lines': [{'description': 'Reviewed delivery item', 'uom': 'EA', 'ordered': '10.50'}],
            **extra,
        }

    def post(self, po, payload):
        return self.client.post(fixtures.BASE + f'orders/{po.pk}/receiving-basis/', payload, format='json')

    def recover(self, po, **extra):
        response = self.post(po, self.command(po, **extra))
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def record_reviewed(self, po, received='2.50', rejected='0', **extra):
        summary = receiving_summary(po)
        suffix = 'amount' if summary['basis'] == 'service_value' else 'qty'
        payload = self.receipt_payload(po, items_received=[{
            'line_id': summary['lines'][0]['line_id'],
            f'received_{suffix}': received, f'rejected_{suffix}': rejected,
        }], **extra)
        response = self.client.post(fixtures.BASE + 'receipts/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def source_snapshot(self, po):
        fields = [field.attname for field in PurchaseOrder._meta.concrete_fields
                  if field.name not in {'receiving_basis', 'updated_at'}]
        return PurchaseOrder.objects.filter(pk=po.pk).values(*fields).get()

    def test_missing_uploaded_goods_can_be_reviewed_recorded_and_confirmed_without_source_edits(self):
        po = self.missing_order()
        original = self.source_snapshot(po)
        before = self.client.get(fixtures.BASE + f'orders/{po.pk}/receiving-summary/')
        self.assertTrue(before.data['needs_basis_review'])
        self.assertTrue(before.data['can_review_basis'])
        self.assertFalse(before.data['can_record'])
        reviewed = self.recover(po)
        self.assertEqual(reviewed['basis'], 'quantity')
        self.assertTrue(reviewed['can_record'])
        self.assertFalse(reviewed['needs_basis_review'])
        self.assertFalse(reviewed['can_review_basis'])
        self.assertTrue(reviewed['basis_source'])
        self.assertEqual(Decimal(reviewed['lines'][0]['ordered']), Decimal('10.50'))
        po.refresh_from_db()
        saved_line = reviewed['lines'][0]['line_id']
        self.assertEqual(receiving_summary(po)['lines'][0]['line_id'], saved_line)
        self.assertTrue(po.receiving_basis)
        receipt = self.record_reviewed(po, '10.50')
        self.assertEqual(receipt['status'], 'pending')
        confirmed = self.decide(receipt, 'confirm_delivery')
        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        self.assertEqual(confirmed.data['status'], 'accepted')
        self.assertEqual(receiving_summary(po)['status'], 'complete')
        for field in INSPECTION_FLAGS:
            self.assertIsNone(confirmed.data[field])
        self.assertEqual(self.source_snapshot(po), original)

    def test_service_net_is_reviewed_explicitly_without_rewriting_unconfirmed_po_commercials(self):
        po = self.missing_order(currency='USD', total_amount='157.50')
        original = self.source_snapshot(po)
        summary = self.recover(po, basis='service_value', lines=[{
            'description': 'Reviewed service acceptance', 'uom': 'USD', 'ordered': '150.00',
        }])
        self.assertEqual(summary['basis'], 'service_value')
        self.assertEqual(summary['value_basis'], 'net_excluding_vat')
        self.assertEqual(Decimal(summary['lines'][0]['ordered']), Decimal('150.00'))
        po.refresh_from_db()
        receipt = self.record_reviewed(po, '150.00')
        self.assertEqual(receipt['items_received'][0]['accepted_amount'], '150.00')
        self.assertEqual(self.decide(receipt, 'confirm_delivery').status_code, 200)
        self.assertEqual(self.source_snapshot(po), original)
        self.assertEqual(po.items, [])
        self.assertIsNone(po.net_amount)
        self.assertEqual(po.vat_basis, 'unconfirmed')

    def test_review_permission_uses_receipt_creation_without_po_commercial_update_or_approval_grant(self):
        RolePermission.objects.filter(role=self.role, permission__module__code='procurement_orders',
                                      permission__action='update').delete()
        RolePermission.objects.filter(role=self.role, permission__module__code='procurement_receipts',
                                      permission__action='approve').delete()
        cache.clear()
        self.assertTrue(self.recover(self.missing_order())['can_record'])

    def test_denied_po_read_or_receipt_create_cannot_save_reviewed_basis(self):
        for module, action in (('procurement_orders', 'read'), ('procurement_receipts', 'create')):
            with self.subTest(module=module):
                po = self.missing_order()
                payload = self.command(po)
                timestamp = po.updated_at
                self.deny(module, action)
                response = self.post(po, payload)
                self.assertEqual(response.status_code, 403, response.data)
                po.refresh_from_db()
                self.assertFalse(po.receiving_basis)
                self.assertEqual(po.updated_at, timestamp)
                UserPermissionOverride.objects.filter(user_profile=self.profile).delete()
                cache.clear()

    def test_anonymous_recovery_is_denied_without_mutation(self):
        po = self.missing_order()
        self.client.force_authenticate(None)
        response = self.post(po, self.command(po))
        self.assertIn(response.status_code, (401, 403))
        po.refresh_from_db()
        self.assertFalse(po.receiving_basis)

    def test_draft_cancelled_and_missing_or_pending_approval_cannot_be_recovered(self):
        for values in ({'status': 'draft'}, {'status': 'cancelled'}, {'approval_log': []},
                       {'approval_log': [{'stage': 'Approval', 'approver': 'Named signer', 'status': 'Pending'}]}):
            with self.subTest(values=values):
                po = self.missing_order(**values)
                response = self.post(po, self.command(po))
                self.assertEqual(response.status_code, 400, response.data)
                po.refresh_from_db()
                self.assertFalse(po.receiving_basis)

    def test_existing_valid_quantity_or_service_basis_cannot_be_replaced(self):
        orders = [self.order(), self.order(items=[], category='engineering_services',
                  scope_of_services='Service scope', vat_basis='none', net_amount='100.00')]
        for po in orders:
            with self.subTest(po=po.pk):
                po.refresh_from_db()
                before = deepcopy(receiving_summary(po))
                response = self.post(po, self.command(po))
                self.assertIn(response.status_code, (400, 409), response.data)
                po.refresh_from_db()
                self.assertFalse(po.receiving_basis)
                self.assertEqual(receiving_summary(po), before)

    def test_existing_receipt_including_rejected_prevents_new_basis(self):
        for status in ('pending', 'accepted', 'partial', 'rejected'):
            with self.subTest(status=status):
                po = self.missing_order()
                receipt = Receipt.objects.create(purchase_order=po, received_by=self.user, status=status,
                    receipt_number='LEGACY-' + str(uuid4()), items_received=[{'line_id': 'legacy', 'received_qty': '1'}])
                response = self.post(po, self.command(po))
                self.assertIn(response.status_code, (400, 409), response.data)
                po.refresh_from_db()
                self.assertFalse(po.receiving_basis)
                self.assertTrue(Receipt.objects.filter(pk=receipt.pk).exists())

    def test_identical_retry_retains_ids_actor_and_timestamp_and_conflicting_retry_is_rejected(self):
        po = self.missing_order()
        payload = self.command(po)
        first = self.post(po, payload)
        self.assertEqual(first.status_code, 200, first.data)
        po.refresh_from_db()
        saved = deepcopy(po.receiving_basis)
        timestamp = po.updated_at
        retry = self.post(po, payload)
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertEqual(retry.data['lines'], first.data['lines'])
        changed = deepcopy(payload)
        changed['lines'][0]['ordered'] = '11'
        self.assertEqual(self.post(po, changed).status_code, 409)
        po.refresh_from_db()
        self.assertEqual(po.receiving_basis, saved)
        self.assertEqual(po.updated_at, timestamp)

    def test_other_actor_cannot_replay_and_current_permission_is_rechecked(self):
        po = self.missing_order()
        payload = self.command(po)
        self.assertEqual(self.post(po, payload).status_code, 200)
        other = get_user_model().objects.create_user('other-basis-reviewer', email='other-basis@example.test')
        profile, _ = UserProfile.objects.get_or_create(user=other, defaults={'organization': self.profile.organization})
        UserRole.objects.create(user_profile=profile, role=self.role)
        self.client.force_authenticate(other)
        self.assertEqual(self.post(po, payload).status_code, 409)
        self.client.force_authenticate(self.user)
        self.deny('procurement_receipts', 'create')
        self.assertEqual(self.post(po, payload).status_code, 403)

    def test_stale_version_cannot_record_or_overwrite_basis(self):
        po = self.missing_order()
        payload = self.command(po)
        po.save(update_fields=['updated_at'])
        response = self.post(po, payload)
        self.assertEqual(response.status_code, 409, response.data)
        po.refresh_from_db()
        self.assertFalse(po.receiving_basis)

    def test_recorded_receipt_does_not_break_identical_basis_retry_or_allow_replacement(self):
        po = self.missing_order()
        payload = self.command(po)
        self.assertEqual(self.post(po, payload).status_code, 200)
        po.refresh_from_db()
        saved = deepcopy(po.receiving_basis)
        self.record_reviewed(po, '2.50')
        retry = self.post(po, payload)
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertEqual(Decimal(retry.data['lines'][0]['pending']), Decimal('2.50'))
        changed = self.command(po, lines=[{'description': 'Changed line', 'uom': 'EA', 'ordered': '99'}])
        self.assertIn(self.post(po, changed).status_code, (400, 409))
        po.refresh_from_db()
        self.assertEqual(po.receiving_basis, saved)
        self.assertEqual(Receipt.objects.count(), 1)

    def test_incomplete_extracted_items_can_be_reviewed_without_modifying_original_json(self):
        original_items = [{'description': 'Imported scope without quantity or unit'}]
        po = self.missing_order(items=original_items)
        self.assertFalse(receiving_summary(po)['can_record'])
        self.assertTrue(self.recover(po)['can_record'])
        po.refresh_from_db()
        self.assertEqual(po.items, original_items)

    def test_multiple_reviewed_lines_have_stable_unique_ids_and_require_all_balances(self):
        po = self.missing_order()
        summary = self.recover(po, lines=[
            {'description': 'Reviewed cable', 'uom': 'M', 'ordered': '2.000001'},
            {'description': 'Reviewed cable', 'uom': 'M', 'ordered': '3.5'},
        ])
        identifiers = [line['line_id'] for line in summary['lines']]
        self.assertEqual(len(set(identifiers)), 2)
        po.refresh_from_db()
        self.assertEqual([line['line_id'] for line in receiving_summary(po)['lines']], identifiers)
        payload = self.receipt_payload(po, items_received=[{'line_id': identifiers[0], 'received_qty': '2.000001'}])
        payload.update(delivery_location='Receiving dock', condition='good', delivery_status='full')
        response = self.client.post(fixtures.BASE + 'receipts/', payload, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Receipt.objects.exists())

    def test_pending_reservations_block_overreceipt_and_rejection_releases_reviewed_balance(self):
        po = self.missing_order()
        self.recover(po)
        po.refresh_from_db()
        first = self.record_reviewed(po, '8')
        self.assertEqual(Decimal(receiving_summary(po)['lines'][0]['available']), Decimal('2.50'))
        line = receiving_summary(po)['lines'][0]['line_id']
        over = self.receipt_payload(po, items_received=[{'line_id': line, 'received_qty': '3'}])
        self.assertEqual(self.client.post(fixtures.BASE + 'receipts/', over, format='json').status_code, 400)
        rejected = self.decide(first, 'reject_delivery', reason='Damaged delivery')
        self.assertEqual(rejected.status_code, 200, rejected.data)
        self.assertEqual(Decimal(receiving_summary(po)['lines'][0]['available']), Decimal('10.50'))
        partial = self.record_reviewed(po, '10.50', rejected='1.50', delivery_location='Receiving dock',
            condition='damaged', delivery_status='partial', exception_reason='Damaged part of delivery')
        confirmed = self.decide(partial, 'confirm_delivery')
        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        self.assertEqual(confirmed.data['status'], 'partial')
        self.assertEqual(Decimal(receiving_summary(po)['lines'][0]['remaining']), Decimal('1.50'))

    def test_completed_order_still_requires_reconciliation_and_retains_completed_state(self):
        po = self.missing_order(status='completed')
        summary = self.recover(po)
        self.assertTrue(summary['can_reconcile'])
        self.assertFalse(summary['can_record'])
        line = summary['lines'][0]['line_id']
        payload = self.receipt_payload(po, items_received=[{'line_id': line, 'received_qty': '10.50'}])
        self.assertEqual(self.client.post(fixtures.BASE + 'receipts/', payload, format='json').status_code, 400)
        payload['reason'] = 'Retained historical delivery evidence'
        response = self.client.post(fixtures.BASE + 'receipts/reconcile/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        po.refresh_from_db()
        self.assertEqual(po.status, 'completed')

    def test_reviewed_receiving_evidence_cannot_manufacture_verified_finance_line_matching(self):
        from apps.core.project_models import Project
        from apps.finance import tests_purchase_order_handoff as finance_fixtures
        from apps.finance.models import Invoice, InvoiceLineItem
        project = Project.objects.create(code='RECEIVING-BASIS-FINANCE', name='Synthetic receiving project')
        for action in ('read', 'create', 'update'):
            self.grant('finance_incoming', action)
        for basis in ('quantity', 'service_value'):
            with self.subTest(basis=basis):
                po = self.missing_order(currency='USD', enterprise_project=project)
                service = basis == 'service_value'
                self.recover(po, basis=basis, lines=[{
                    'description': 'Reviewed source scope', 'uom': 'USD' if service else 'EA',
                    'ordered': '100.00' if service else '10.50',
                }])
                po.refresh_from_db()
                receipt = self.record_reviewed(po, '100.00' if service else '10.50')
                self.assertEqual(self.decide(receipt, 'confirm_delivery').status_code, 200)
                invoice = Invoice.objects.create(invoice_number='REVIEWED-BASIS-' + basis,
                    vendor=self.vendor, vendor_name=self.vendor.name, currency='USD',
                    total_amount=Decimal('100.00'), submitted_by=self.user,
                    original_filename='synthetic.pdf', file_path='', procurement_status='ready_for_matching')
                InvoiceLineItem.objects.create(invoice=invoice, line_number=1,
                    po_item_reference='1', quantity=Decimal('10.50'))
                with override_settings(ROOT_URLCONF=finance_fixtures.__name__):
                    response = self.client.post(finance_fixtures.BASE + f'{invoice.pk}/allocate-purchase-order/', {
                        'purchase_order_id': str(po.pk), 'allocated_amount': '100.00',
                        'confirm_po_match': True, 'expected_updated_at': invoice.updated_at.isoformat(),
                        'reason': 'Reviewed invoice source',
                    }, format='json')
                self.assertEqual(response.status_code, 201, response.data)
                self.assertEqual(response.data['match_status'], 'exception')
                self.assertTrue(response.data['exception_codes'])
                po.refresh_from_db()
                self.assertEqual(po.items, [])

    def test_invalid_quantity_lines_and_protected_fields_are_rejected_atomically(self):
        po = self.missing_order()
        original = self.source_snapshot(po)
        cases = [
            {'lines': []}, {'lines': 'invalid'}, {'lines': [None]}, {'basis': 'invented'},
            {'basis': []}, {'basis': {}},
            *({'lines': [{'description': 'Item', 'uom': 'EA', 'ordered': value}]} for value in
              ('0', '-1', 'NaN', '1e3', '0.1234567', '1234567890123456789', True)),
            {'lines': [{'description': '', 'uom': 'EA', 'ordered': '1'}]},
            {'lines': [{'description': 'Item', 'uom': '', 'ordered': '1'}]},
            {'lines': [{'description': 'Item', 'uom': 'EA', 'ordered': '1', 'line_id': 'forged'}]},
            {'lines': [{'description': 'Item', 'uom': 'EA', 'ordered': '1'}] * 101},
            {'actor_id': self.user.pk}, {'status': 'completed'}, {'items': []},
            {'receiving_basis': {}}, {'approval_log': []}, {'total_amount': '0'},
            {'operation_key': 'invalid'}, {'expected_updated_at': 'invalid'},
        ]
        for extra in cases:
            with self.subTest(extra=extra):
                response = self.post(po, self.command(po, **extra))
                self.assertEqual(response.status_code, 400, response.data)
                po.refresh_from_db()
                self.assertFalse(po.receiving_basis)
        self.assertEqual(self.source_snapshot(po), original)

    def test_malformed_command_objects_and_missing_keys_are_rejected_without_mutation(self):
        po = self.missing_order()
        valid = self.command(po)
        cases = [[], 'invalid', None, *({key: value for key, value in valid.items() if key != missing}
                                      for missing in valid)]
        for data in cases:
            with self.subTest(data=data):
                response = self.post(po, data)
                self.assertEqual(response.status_code, 400, response.data)
                po.refresh_from_db()
                self.assertFalse(po.receiving_basis)

    def test_malformed_saved_basis_fails_closed_instead_of_being_treated_as_missing(self):
        for stored in ([], '', 0, False, {'version': 99}, {'version': 1, 'lines': []}):
            with self.subTest(stored=stored):
                po = self.missing_order(receiving_basis=stored)
                summary = self.client.get(fixtures.BASE + f'orders/{po.pk}/receiving-summary/')
                self.assertEqual(summary.status_code, 200, summary.data)
                self.assertFalse(summary.data['can_review_basis'])
                self.assertFalse(summary.data['can_record'])
                self.assertIn(self.post(po, self.command(po)).status_code, (400, 409))
                po.refresh_from_db()
                self.assertEqual(po.receiving_basis, stored)

    def test_malformed_saved_identity_and_version_return_blocked_summary_without_server_error(self):
        cases = [{'identity': identity} for identity in ({}, [], 42, None, 'invalid')]
        cases.append({'version': True})
        for values in cases:
            with self.subTest(values=values):
                po = self.missing_order()
                self.recover(po)
                po.refresh_from_db()
                malformed = {**po.receiving_basis, **values}
                PurchaseOrder.objects.filter(pk=po.pk).update(receiving_basis=malformed)
                summary = self.client.get(fixtures.BASE + f'orders/{po.pk}/receiving-summary/')
                self.assertEqual(summary.status_code, 200, summary.data)
                self.assertFalse(summary.data['can_record'])
                self.assertFalse(summary.data['can_review_basis'])
                self.assertIn(self.post(po, self.command(po)).status_code, (400, 409))
                po.refresh_from_db()
                self.assertEqual(po.receiving_basis, malformed)

    def test_service_requires_one_positive_cent_precision_value_in_po_currency(self):
        po = self.missing_order(currency='USD')
        valid = {'description': 'Reviewed service', 'uom': 'USD', 'ordered': '10.00'}
        cases = [[{**valid, 'ordered': value}] for value in ('0', '-1', 'NaN', '1.001')]
        cases += [[{**valid, 'uom': unit}] for unit in ('EUR', 'EA', '', 'USDD')]
        cases += [[valid, valid]]
        for lines in cases:
            with self.subTest(lines=lines):
                response = self.post(po, self.command(po, basis='service_value', lines=lines))
                self.assertEqual(response.status_code, 400, response.data)
                po.refresh_from_db()
                self.assertFalse(po.receiving_basis)

    def test_generic_po_write_cannot_forge_or_erase_reviewed_basis(self):
        po = self.missing_order(status='draft', approval_log=[])
        response = self.client.patch(fixtures.BASE + f'orders/{po.pk}/', {
            'receiving_basis': {'basis': 'quantity', 'lines': []},
        }, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        po.refresh_from_db()
        self.assertFalse(po.receiving_basis)
        issued = self.missing_order()
        self.recover(issued)
        issued.refresh_from_db()
        saved = deepcopy(issued.receiving_basis)
        response = self.client.patch(fixtures.BASE + f'orders/{issued.pk}/', {'receiving_basis': {}}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        issued.refresh_from_db()
        self.assertEqual(issued.receiving_basis, saved)

    def test_audit_failure_rolls_back_reviewed_basis_and_po_timestamp(self):
        po = self.missing_order()
        payload = self.command(po)
        timestamp = po.updated_at
        with patch('apps.procurement.services.receiving.create_audit_log', side_effect=RuntimeError('Synthetic audit failure')):
            with self.assertRaises(RuntimeError):
                self.post(po, payload)
        po.refresh_from_db()
        self.assertFalse(po.receiving_basis)
        self.assertEqual(po.updated_at, timestamp)
        self.assertFalse(Receipt.objects.exists())


@skipUnless(connection.vendor == 'postgresql', 'Requires isolated PostgreSQL')
@override_settings(ROOT_URLCONF=fixtures.__name__, RADAI_BUSINESS_APPROVAL_ROUTES=fixtures.ROUTES)
class ReceivingBasisConcurrencyTests(TransactionTestCase):
    setUp = fixtures.ReceivingHandoffTests.setUp
    grant = fixtures.ReceivingHandoffTests.grant
    order = fixtures.ReceivingHandoffTests.order
    missing_order = ReceivingBasisTests.missing_order
    command = ReceivingBasisTests.command
    race = concurrency_fixtures.HandoffConcurrencyTests.race

    def run_review_race(self, *, retry):
        from apps.procurement.services import receiving
        po = self.missing_order()
        first = self.command(po)
        second = deepcopy(first) if retry else self.command(po)
        if not retry:
            second['lines'][0]['ordered'] = '99'
        post = concurrency_fixtures.HandoffConcurrencyTests.post
        url = fixtures.BASE + f'orders/{po.pk}/receiving-basis/'
        winner, contender = self.race(
            target='apps.procurement.services.receiving.lock_purchase_order',
            original=receiving.lock_purchase_order,
            winner=lambda: post(self.user, url, first),
            contender=lambda: post(self.user, url, second),
        )
        self.assertEqual(winner[0], 200, winner)
        po.refresh_from_db()
        self.assertEqual(Decimal(receiving_summary(po)['lines'][0]['ordered']), Decimal('10.50'))
        self.assertEqual(po.items, [])
        self.assertFalse(Receipt.objects.exists())
        return winner, contender

    def test_competing_basis_reviews_cannot_replace_the_first_saved_evidence(self):
        _, contender = self.run_review_race(retry=False)
        self.assertEqual(contender[0], 409, contender)

    def test_simultaneous_identical_basis_retry_returns_the_same_saved_line_ids(self):
        winner, contender = self.run_review_race(retry=True)
        self.assertEqual(contender[0], 200, contender)
        self.assertEqual(winner[1]['lines'], contender[1]['lines'])
