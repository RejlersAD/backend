"""Delivery declarations persist without granting inspection authority."""
from copy import deepcopy
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.procurement.models import Receipt
from apps.procurement.services.receiving import INSPECTION_FLAGS, receiving_summary
from . import test_receiving_handoff as fixtures


@override_settings(ROOT_URLCONF=fixtures.__name__, RADAI_BUSINESS_APPROVAL_ROUTES=fixtures.ROUTES)
class ReceiptDeliveryInformationTests(TestCase):
    setUp = fixtures.ReceivingHandoffTests.setUp
    grant = fixtures.ReceivingHandoffTests.grant
    deny = fixtures.ReceivingHandoffTests.deny
    order = fixtures.ReceivingHandoffTests.order
    payload = fixtures.ReceivingHandoffTests.payload
    record = fixtures.ReceivingHandoffTests.record
    decide = fixtures.ReceivingHandoffTests.decide

    def delivery(self, **extra):
        return {
            'delivery_location': 'Project receiving area',
            'supplier_reference': 'SUPPLIER-DELIVERY-42',
            'condition': 'good', 'delivery_status': 'full', 'exception_reason': '',
            **extra,
        }

    def post(self, payload):
        return self.client.post(fixtures.BASE + 'receipts/', payload, format='json')

    def update(self, data, **extra):
        return self.client.patch(fixtures.BASE + f"receipts/{data['id']}/", {
            'expected_updated_at': data['updated_at'], **extra,
        }, format='json')

    def test_round_trip_and_recorded_history_keep_delivery_separate_from_inspection(self):
        po = self.order()
        information = self.delivery()
        data = self.record(po, '10.50', **information)
        receipt = Receipt.objects.get(pk=data['id'])
        detail = self.client.get(fixtures.BASE + f"receipts/{data['id']}/")
        self.assertEqual(detail.status_code, 200)
        for field, value in information.items():
            self.assertEqual(getattr(receipt, field), value)
            self.assertEqual(detail.data[field], value)
        self.assertEqual(receipt.workflow_history[0]['delivery_information'], information)
        self.assertEqual(receipt.received_by, self.user)
        self.assertEqual(receipt.status, 'pending')
        for field in INSPECTION_FLAGS:
            self.assertIsNone(getattr(receipt, field))
        self.assertEqual(self.decide(data, 'confirm_delivery').status_code, 200)
        receipt.refresh_from_db()
        self.assertEqual(receipt.delivery_status, 'full')
        self.assertEqual(receipt.condition, 'good')
        self.assertEqual(receipt.status, 'accepted')
        for field in INSPECTION_FLAGS:
            self.assertIsNone(getattr(receipt, field))

    def test_full_coverage_checks_all_canonical_lines_and_pending_reservations(self):
        po = self.order(items=[
            {'description': 'Gauge', 'quantity': '3.25', 'unit': 'EA'},
            {'description': 'Cable', 'quantity': '2', 'unit': 'M'},
        ])
        self.record(po, '1.25')
        payload = self.payload(po, '2', **self.delivery())
        missing_line = self.post(payload)
        self.assertEqual(missing_line.status_code, 400)
        self.assertIn('delivery_status', missing_line.data)
        payload['items_received'].append({'line_id': 'line:2', 'received_qty': '2'})
        response = self.post(payload)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['delivery_status'], 'full')

    def test_partial_requires_reason_without_manufacturing_acceptance(self):
        po = self.order()
        payload = self.payload(po, '2.50', **self.delivery(delivery_status='partial'))
        response = self.post(payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn('exception_reason', response.data)
        self.assertFalse(Receipt.objects.exists())
        payload['exception_reason'] = 'The supplier will deliver the balance next week.'
        response = self.post(payload)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['status'], 'pending')
        self.assertEqual(response.data['items_received'][0]['accepted_qty'], '2.50')
        self.assertEqual(receiving_summary(po)['lines'][0]['accepted'], '0')

    def test_partial_rejection_and_all_rejected_declarations_use_real_line_evidence(self):
        po = self.order()
        data = self.record(po, '10.50', rejected='1.50', **self.delivery(
            condition='damaged', delivery_status='partial', exception_reason='One package was damaged.',
        ))
        self.assertEqual(data['items_received'][0]['accepted_qty'], '9.00')
        self.assertEqual(data['items_received'][0]['rejected_qty'], '1.50')
        self.assertEqual(data['status'], 'pending')
        other = self.order()
        rejected = self.record(other, '2.50', rejected='2.50', **self.delivery(
            condition='damaged', delivery_status='rejected', exception_reason='All delivered gauges were damaged.',
        ))
        self.assertEqual(rejected['status'], 'pending')
        self.assertFalse(rejected['confirmation']['can_confirm'])
        self.assertEqual(receiving_summary(other)['lines'][0]['available'], '10.50')
        self.assertEqual(self.decide(rejected, 'confirm_delivery').status_code, 400)
        receipt = Receipt.objects.get(pk=rejected['id'])
        self.assertEqual(receipt.status, 'pending')
        self.assertIsNone(receipt.quality_check_passed)

    def test_conflicting_status_and_invalid_declarations_are_rejected_atomically(self):
        po = self.order()
        initial_timestamp = po.updated_at
        cases = [
            self.delivery(delivery_status='full'),
            self.delivery(delivery_status='rejected', exception_reason='Rejected'),
            self.delivery(delivery_status='invented'),
            self.delivery(condition='invented'),
            self.delivery(delivery_location='x' * 301),
            self.delivery(supplier_reference='x' * 101),
            self.delivery(exception_reason='x' * 4001),
            self.delivery(delivery_status='partial', exception_reason='   '),
            self.delivery(delivery_location=''),
            self.delivery(condition=''),
        ]
        for information in cases:
            with self.subTest(information=information):
                response = self.post(self.payload(po, '2.50', **information))
                self.assertEqual(response.status_code, 400, response.data)
                self.assertFalse(Receipt.objects.exists())
        po.refresh_from_db()
        self.assertEqual(po.updated_at, initial_timestamp)

    def test_service_declarations_use_exact_net_values(self):
        po = self.order(items=[], category='engineering_services', scope_of_services='Engineering services',
                        vat_basis='none', currency='USD', net_amount='100.00')
        payload = self.payload(po, **self.delivery())
        payload['items_received'] = [{'line_id': 'service:total', 'received_amount': '100.00'}]
        response = self.post(payload)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['items_received'][0]['accepted_amount'], '100.00')
        self.assertEqual(response.data['delivery_status'], 'full')

    def test_retry_compares_every_delivery_field_and_retains_original_history(self):
        po = self.order()
        payload = self.payload(po, '10.50', **self.delivery())
        first = self.post(payload)
        self.assertEqual(first.status_code, 201, first.data)
        repeat = self.post(payload)
        self.assertEqual(repeat.status_code, 201, repeat.data)
        self.assertEqual(repeat.data['id'], first.data['id'])
        for field, value in self.delivery(delivery_location='Another dock', supplier_reference='Another reference',
                                          condition='damaged', delivery_status='partial', exception_reason='Changed').items():
            changed = deepcopy(payload)
            changed[field] = value
            with self.subTest(field=field):
                self.assertEqual(self.post(changed).status_code, 409)
        self.assertEqual(Receipt.objects.count(), 1)
        self.assertEqual(len(Receipt.objects.get().workflow_history), 1)

    def test_legacy_omission_remains_compatible_without_reason_or_backfill(self):
        po = self.order()
        payload = self.payload(po)
        first = self.post(payload)
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(self.post(payload).status_code, 201)
        for field in self.delivery():
            self.assertEqual(first.data[field], '')
        self.assertEqual(Receipt.objects.count(), 1)

    def test_pending_metadata_updates_are_fresh_audited_and_status_is_immutable(self):
        data = self.record(self.order(), '2.50', **self.delivery(
            delivery_status='partial', exception_reason='Remaining items to follow.',
        ))
        changed = self.update(data, delivery_location='Receiving dock B', supplier_reference='REFERENCE-B',
                              condition='not_inspected', exception_reason='Corrected delivery schedule.')
        self.assertEqual(changed.status_code, 200, changed.data)
        receipt = Receipt.objects.get(pk=data['id'])
        self.assertEqual(receipt.delivery_location, 'Receiving dock B')
        self.assertEqual(receipt.workflow_history[-1]['changes']['condition'], {'before': 'good', 'after': 'not_inspected'})
        self.assertEqual(self.update(data, delivery_location='Stale dock').status_code, 409)
        self.assertEqual(self.update(changed.data, delivery_status='full').status_code, 400)
        self.assertEqual(self.update(changed.data, exception_reason='').status_code, 400)
        self.assertEqual(self.update(changed.data, delivery_location='').status_code, 400)
        self.assertEqual(self.update(changed.data, condition='').status_code, 400)
        confirmed = self.decide(changed.data, 'confirm_delivery')
        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        self.assertEqual(self.update(confirmed.data, condition='damaged').status_code, 400)
        receipt.refresh_from_db()
        self.assertEqual(receipt.condition, 'not_inspected')

    def test_access_stale_source_and_server_owned_recorder_still_block_creation(self):
        po = self.order()
        stale = self.payload(po, '10.50', **self.delivery())
        po.save(update_fields=['updated_at'])
        self.assertEqual(self.post(stale).status_code, 409)
        forged = self.payload(po, '10.50', **self.delivery(), received_by=self.user.pk)
        self.assertEqual(self.post(forged).status_code, 400)
        self.deny('procurement_receipts', 'create')
        self.assertEqual(self.post(self.payload(po, '10.50', **self.delivery())).status_code, 403)
        self.assertFalse(Receipt.objects.exists())

    def test_history_failure_rolls_back_all_delivery_fields_and_parent_timestamp(self):
        po = self.order()
        timestamp = po.updated_at
        with patch('apps.procurement.services.receiving._history', side_effect=RuntimeError('Test audit failure')):
            with self.assertRaises(RuntimeError):
                self.post(self.payload(po, '10.50', **self.delivery()))
        self.assertFalse(Receipt.objects.exists())
        po.refresh_from_db()
        self.assertEqual(po.updated_at, timestamp)
