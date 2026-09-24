"""Receiving/invoice handoff races against isolated PostgreSQL, never live data."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Event, current_thread
from time import monotonic, sleep
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.db import connection, connections
from django.test import TransactionTestCase, override_settings
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.finance import tests_purchase_order_handoff as finance_fixtures
from apps.finance.models import InvoicePurchaseOrderAllocation
from apps.finance.views import InvoiceViewSet
from apps.procurement.models import Receipt
from apps.procurement.views import ReceiptViewSet
from apps.rbac.models import AuditLog, Module, Permission
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints
from .approval_fixtures import set_position


router = DefaultRouter()
router.register('invoices', InvoiceViewSet, basename='handoff-race-invoice')
receipt_router = DefaultRouter()
receipt_router.register('receipts', ReceiptViewSet, basename='handoff-race-receipt')
urlpatterns = [path('api/v1/finance/', include(router.urls)),
               path('api/v1/procurement/', include(receipt_router.urls))]
secure_module_endpoints(urlpatterns)
RECEIPT_ROUTES = {
    f'procurement_receipts.Receipt.{action}': {'positions': ['engineer'], 'pending_states': ['pending']}
    for action in ('accept', 'reject_delivery')
}


@skipUnless(connection.vendor == 'postgresql', 'Requires isolated PostgreSQL')
@override_settings(ROOT_URLCONF=__name__, RADAI_BUSINESS_APPROVAL_ROUTES=RECEIPT_ROUTES)
class HandoffConcurrencyTests(TransactionTestCase):
    actor = finance_fixtures.PurchaseOrderHandoffTests.actor
    po = finance_fixtures.PurchaseOrderHandoffTests.po
    make_invoice = finance_fixtures.PurchaseOrderHandoffTests.make_invoice

    def setUp(self):
        finance_fixtures.PurchaseOrderHandoffTests.setUp(self)
        module, _ = Module.objects.get_or_create(code='procurement_receipts', defaults={'name': 'Receipts'})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        self.modules['procurement_receipts'] = module
        self.receiver = self.actor('receiver', {
            'procurement_receipts': ('read', 'create', 'update', 'approve', 'delete'), 'procurement_orders': ('read',),
        })
        set_position(self.receiver)
        self.order.status = 'acknowledged'
        self.order.items = [{'line_number': 1, 'description': 'Synthetic equipment',
                             'quantity': '10', 'unit': 'ea'}]
        self.order.save(update_fields=['status', 'items', 'updated_at'])

    def race(self, *, target, original, winner, contender):
        """Pause after the winner takes real locks; prove the contender blocks."""
        winner_locked = Event()
        release_winner = Event()
        contender_connected = Event()
        pids = {}

        def pause_after_lock(*args, **kwargs):
            result = original(*args, **kwargs)
            if current_thread().name.endswith('_0') and not winner_locked.is_set():
                winner_locked.set()
                if not release_winner.wait(12):
                    raise AssertionError('Winner release timed out')
            return result

        def invoke(label, operation):
            connections.close_all()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    pids[label] = cursor.fetchone()[0]
                if label == 'contender':
                    contender_connected.set()
                return operation()
            finally:
                connections.close_all()

        with patch(target, side_effect=pause_after_lock), ThreadPoolExecutor(max_workers=2, thread_name_prefix='handoff') as pool:
            first = pool.submit(invoke, 'winner', winner)
            second = None
            try:
                self.assertTrue(winner_locked.wait(8), 'Winner did not acquire the domain lock')
                second = pool.submit(invoke, 'contender', contender)
                self.assertTrue(contender_connected.wait(5))
                blocked = False
                deadline = monotonic() + 6
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT pg_blocking_pids(%s)', [pids['contender']])
                        blockers = cursor.fetchone()[0]
                    if pids['winner'] in blockers:
                        blocked = True
                        break
                    if second.done():
                        self.fail(f'Contender completed without waiting: {second.result()}')
                    sleep(0.04)
                self.assertTrue(blocked, 'No real PostgreSQL row-lock wait was observed')
                self.assertNotEqual(pids['winner'], pids['contender'])
                print(f'HANDOFF_LOCK_EVIDENCE test={self._testMethodName} winner_pid={pids["winner"]} contender_pid={pids["contender"]} blocked=True')
            finally:
                release_winner.set()
            return first.result(timeout=15), second.result(timeout=15)

    @staticmethod
    def post(user, url, payload):
        client = APIClient()
        client.force_authenticate(user)
        response = client.post(url, payload, format='json')
        return response.status_code, response.data

    def receipt_payload(self, operation_key):
        return {'purchase_order': str(self.order.pk), 'operation_key': str(operation_key),
                'expected_po_updated_at': self.order.updated_at.isoformat(), 'status': 'pending',
                'items_received': [{'line_id': 'line:1', 'received_qty': '7', 'rejected_qty': '0'}]}

    def run_receipt_race(self, *, same_key):
        from apps.procurement.services import receiving
        key = uuid4()
        winner_payload = self.receipt_payload(key)
        contender_payload = self.receipt_payload(key if same_key else uuid4())
        return self.race(
            target='apps.procurement.services.receiving.lock_purchase_order', original=receiving.lock_purchase_order,
            winner=lambda: self.post(self.receiver, '/api/v1/procurement/receipts/', winner_payload),
            contender=lambda: self.post(self.receiver, '/api/v1/procurement/receipts/', contender_payload),
        )

    def test_competing_receipts_do_not_overreserve_the_order(self):
        winner, contender = self.run_receipt_race(same_key=False)
        self.assertEqual(winner[0], 201, winner)
        self.assertEqual(contender[0], 409, contender)
        self.assertEqual(Receipt.objects.count(), 1)
        receipt = Receipt.objects.get()
        self.assertEqual(receipt.status, 'pending')
        self.assertEqual(Decimal(receipt.items_received[0]['received_qty']), Decimal('7'))

    def test_simultaneous_retry_returns_one_receipt_and_one_history_entry(self):
        winner, contender = self.run_receipt_race(same_key=True)
        self.assertEqual(winner[0], 201, winner)
        self.assertEqual(contender[0], 201, contender)
        self.assertEqual(winner[1]['id'], contender[1]['id'])
        self.assertEqual(Receipt.objects.count(), 1)
        self.assertEqual(len(Receipt.objects.get().workflow_history), 1)

    def test_competing_invoices_recheck_remaining_po_value_under_lock(self):
        from apps.finance.services import purchase_order_handoff
        other = self.make_invoice('HANDOFF-OTHER')

        def payload(invoice):
            return {'purchase_order_id': str(self.order.pk), 'allocated_amount': '80.00',
                    'confirm_po_match': True, 'expected_updated_at': invoice.updated_at.isoformat(),
                    'reason': 'Synthetic concurrent supplier allocation'}

        winner, contender = self.race(
            target='apps.finance.services.purchase_order_handoff.validate_new_allocation',
            original=purchase_order_handoff.validate_new_allocation,
            winner=lambda: self.post(self.user, f'/api/v1/finance/invoices/{self.invoice.pk}/allocate-purchase-order/', payload(self.invoice)),
            contender=lambda: self.post(self.user, f'/api/v1/finance/invoices/{other.pk}/allocate-purchase-order/', payload(other)),
        )
        self.assertEqual(winner[0], 201, winner)
        self.assertEqual(contender[0], 400, contender)
        self.assertEqual(InvoicePurchaseOrderAllocation.objects.count(), 1)
        self.assertEqual(InvoicePurchaseOrderAllocation.objects.get().allocated_amount, Decimal('80.00'))

    def run_inspection_edit_race(self, *, edit_first, decision='accept'):
        from apps.procurement.services import receiving
        created = self.post(self.receiver, '/api/v1/procurement/receipts/', self.receipt_payload(uuid4()))
        self.assertEqual(created[0], 201, created)
        receipt = Receipt.objects.get(pk=created[1]['id'])
        token = receipt.updated_at.isoformat()
        url = f'/api/v1/procurement/receipts/{receipt.pk}/'

        def edit():
            client = APIClient()
            client.force_authenticate(self.receiver)
            response = client.patch(url, {'notes': 'Inspected package label', 'expected_updated_at': token}, format='json')
            return response.status_code, response.data

        def inspect():
            return self.post(self.receiver, url + decision + '/', {'expected_updated_at': token})

        winner, contender = self.race(
            target='apps.procurement.services.receiving.lock_purchase_order', original=receiving.lock_purchase_order,
            winner=edit if edit_first else inspect, contender=inspect if edit_first else edit,
        )
        self.assertEqual(winner[0], 200, winner)
        self.assertEqual(contender[0], 409 if edit_first else 400, contender)
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, 'pending' if edit_first else 'accepted')
        self.assertEqual(len(receipt.workflow_history), 2)
        self.assertEqual(receipt.workflow_history[-1]['action'], 'updated' if edit_first else decision)

    def test_receipt_edit_then_inspection_uses_consistent_lock_order(self):
        self.run_inspection_edit_race(edit_first=True)

    def test_receipt_inspection_then_edit_uses_consistent_lock_order(self):
        self.run_inspection_edit_race(edit_first=False)

    def test_receipt_edit_then_recorder_confirmation_rechecks_freshness(self):
        self.run_inspection_edit_race(edit_first=True, decision='confirm_delivery')

    def test_recorder_confirmation_then_edit_preserves_confirmed_evidence(self):
        self.run_inspection_edit_race(edit_first=False, decision='confirm_delivery')

    def test_simultaneous_delivery_confirmation_returns_one_decision(self):
        from apps.procurement.services import receiving
        created = self.post(self.receiver, '/api/v1/procurement/receipts/', self.receipt_payload(uuid4()))
        self.assertEqual(created[0], 201, created)
        url = f"/api/v1/procurement/receipts/{created[1]['id']}/confirm_delivery/"
        payload = {'expected_updated_at': created[1]['updated_at'], 'notes': 'Confirmed retained delivery evidence'}
        winner, contender = self.race(
            target='apps.procurement.services.receiving.lock_purchase_order', original=receiving.lock_purchase_order,
            winner=lambda: self.post(self.receiver, url, payload),
            contender=lambda: self.post(self.receiver, url, payload),
        )
        self.assertEqual(winner[0], 200, winner)
        self.assertEqual(contender[0], 200, contender)
        self.assertEqual(winner[1]['confirmation'], contender[1]['confirmation'])
        receipt = Receipt.objects.get(pk=created[1]['id'])
        self.assertEqual(receipt.status, 'accepted')
        self.assertEqual(len(receipt.workflow_history), 2)
        self.assertEqual(receipt.workflow_history[-1]['action'], 'confirm_delivery')
        self.assertIsNone(receipt.quality_check_passed)

    def run_receipt_delete_race(self, *, deletion_first, double_delete=False):
        from apps.procurement.services import receiving
        created = self.post(self.receiver, '/api/v1/procurement/receipts/', self.receipt_payload(uuid4()))
        self.assertEqual(created[0], 201, created)
        data = created[1]
        url = f"/api/v1/procurement/receipts/{data['id']}/"
        payload = {'expected_updated_at': data['updated_at']}

        def remove():
            client = APIClient()
            client.force_authenticate(self.receiver)
            response = client.delete(url, payload, format='json')
            return response.status_code, response.data

        def confirm():
            return self.post(self.receiver, url + 'confirm_delivery/', payload)

        winner, contender = self.race(
            target='apps.procurement.services.receiving.lock_purchase_order', original=receiving.lock_purchase_order,
            winner=remove if deletion_first else confirm,
            contender=remove if double_delete or not deletion_first else confirm,
        )
        self.assertEqual(winner[0], 204 if deletion_first else 200, winner)
        self.assertEqual(contender[0], 404 if deletion_first else 409, contender)
        self.assertEqual(Receipt.objects.filter(pk=data['id']).exists(), not deletion_first)
        self.assertEqual(AuditLog.objects.filter(resource_type='Receipt', resource_id=data['id'],
                                               metadata__command='delete_pending_receipt').count(), int(deletion_first))
        if not deletion_first:
            self.assertEqual(Receipt.objects.get(pk=data['id']).status, 'accepted')

    def test_delete_then_confirmation_returns_missing_without_recreating_receipt(self):
        self.run_receipt_delete_race(deletion_first=True)

    def test_confirmation_then_delete_preserves_confirmed_evidence(self):
        self.run_receipt_delete_race(deletion_first=False)

    def test_simultaneous_delete_has_one_audit_and_missing_second_result(self):
        self.run_receipt_delete_race(deletion_first=True, double_delete=True)
