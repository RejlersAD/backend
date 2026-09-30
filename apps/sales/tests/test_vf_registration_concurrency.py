"""Real PostgreSQL waits for VF allocation and actor-bound registration retries."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, current_thread
from time import monotonic, sleep
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection, connections
from django.test import TransactionTestCase, override_settings
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.rbac.route_guard import secure_module_endpoints
from apps.sales.models import Client, Deal, OpportunityAuditEvent, OpportunityNumberSequence
from apps.sales.views import DealViewSet
from apps.sales.workflow import _audit

from .access_fixtures import grant_sales_actions


router = DefaultRouter()
router.register('deals', DealViewSet, basename='vf-pg-deals')
urlpatterns = [path('api/v1/sales/', include(router.urls))]
secure_module_endpoints(urlpatterns)


@skipUnless(connection.vendor == 'postgresql', 'Requires disposable PostgreSQL')
@override_settings(ROOT_URLCONF=__name__)
class VFRegistrationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.actors = [get_user_model().objects.create_superuser(
            username=f'vf-pg-reviewer-{index}', email=f'vf-{index}@example.test', password='synthetic-only',
        ) for index in (1, 2)]
        for actor in self.actors:
            grant_sales_actions(actor, 'sales_opportunities', 'sales_clients')
        self.customer = Client.objects.create(
            client_code='VF-PG-BUYER', company_name='Synthetic VF buyer',
            industry_type='other', account_manager=self.actors[0],
        )
        OpportunityNumberSequence.objects.update_or_create(pk=1, defaults={'next_number': 102101})
        self.payload = {
            'registration_request_id': str(uuid4()),
            'deal_name': 'Concurrent synthetic registration', 'client': str(self.customer.pk),
            'opportunity_type': 'rfq', 'open_date': '2026-09-30',
        }

    def race(self, *, same_actor=False, changed_payload=False, rollback_winner=False):
        winner_created, release_winner, contender_connected = Event(), Event(), Event()
        pids = {}

        def audit_and_pause(*args, **kwargs):
            event = _audit(*args, **kwargs)
            if current_thread().name.endswith('_0') and not winner_created.is_set():
                winner_created.set()
                if not release_winner.wait(12):
                    raise AssertionError('Winner release timed out')
                if rollback_winner:
                    raise RuntimeError('synthetic VF audit rollback')
            return event

        def invoke(label, actor_id, payload):
            connections.close_all()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    pids[label] = cursor.fetchone()[0]
                client = APIClient()
                # Django's test exception signal is process-global. During the
                # deliberate rollback another thread's client can observe that
                # exception too; assert actual HTTP results instead of re-raising
                # a signal captured from a different request.
                client.raise_request_exception = False
                client.force_authenticate(get_user_model().objects.get(pk=actor_id))
                if label == 'contender':
                    contender_connected.set()
                response = client.post('/api/v1/sales/deals/', payload, format='json')
                if rollback_winner and label == 'winner':
                    self.assertEqual(response.status_code, 500)
                    return 'rolled_back', None
                return response.status_code, getattr(response, 'data', None)
            finally:
                connections.close_all()

        contender_payload = dict(self.payload)
        if changed_payload:
            contender_payload['deal_name'] = 'Different reviewed registration'
        contender_actor = self.actors[0] if same_actor else self.actors[1]
        with (
            patch('apps.sales.views._audit', side_effect=audit_and_pause),
            ThreadPoolExecutor(max_workers=2, thread_name_prefix='vf-registration') as pool,
        ):
            first = pool.submit(invoke, 'winner', self.actors[0].pk, dict(self.payload))
            second = None
            try:
                self.assertTrue(winner_created.wait(8), 'Winner did not create inside its transaction')
                second = pool.submit(invoke, 'contender', contender_actor.pk, contender_payload)
                self.assertTrue(contender_connected.wait(5), 'Contender did not open its connection')
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
                        self.fail(f'Contender completed without a real row-lock wait: {second.result()[0]}')
                    sleep(0.04)
                self.assertTrue(blocked, 'No PostgreSQL lock wait observed')
                self.assertNotEqual(pids['winner'], pids['contender'])
                print(f'VF_REGISTRATION_LOCK_EVIDENCE test={self._testMethodName} '
                      'blocked=True distinct_connections=True')
            finally:
                release_winner.set()
            return first.result(timeout=15), second.result(timeout=15)

    def test_distinct_actors_receive_consecutive_codes_with_the_same_request_uuid(self):
        first, second = self.race()
        self.assertEqual((first[0], second[0]), (201, 201))
        self.assertEqual([first[1]['deal_code'], second[1]['deal_code']], ['Q-102101', 'Q-102102'])
        self.assertNotEqual(first[1]['id'], second[1]['id'])
        self.assertEqual(Deal.objects.count(), 2)
        self.assertEqual(OpportunityAuditEvent.objects.count(), 2)
        self.assertEqual(OpportunityNumberSequence.objects.get(pk=1).next_number, 102103)

    def test_same_actor_and_request_uuid_return_one_opportunity_and_audit(self):
        first, second = self.race(same_actor=True)
        self.assertEqual((first[0], second[0]), (201, 200))
        self.assertEqual(first[1]['id'], second[1]['id'])
        self.assertEqual(Deal.objects.count(), 1)
        self.assertEqual(OpportunityAuditEvent.objects.count(), 1)
        self.assertEqual(OpportunityNumberSequence.objects.get(pk=1).next_number, 102102)

    def test_cross_owner_assignment_does_not_deadlock_actor_retry_and_sequence_locks(self):
        # The winner references the contender's user row. An UPDATE-strength
        # retry lock on that row would block the winner's deferred FK check
        # while the contender waits on its sequence, creating a lock cycle.
        self.payload['owner'] = str(self.actors[1].pk)
        first, second = self.race()
        self.assertEqual((first[0], second[0]), (201, 201))
        self.assertEqual([first[1]['deal_code'], second[1]['deal_code']], ['Q-102101', 'Q-102102'])
        self.assertEqual(set(Deal.objects.values_list('owner_id', flat=True)), {self.actors[1].pk})
        self.assertEqual(OpportunityAuditEvent.objects.count(), 2)

    def test_changed_concurrent_retry_conflicts_without_consuming_another_number(self):
        first, second = self.race(same_actor=True, changed_payload=True)
        self.assertEqual((first[0], second[0]), (201, 409))
        self.assertEqual(Deal.objects.count(), 1)
        self.assertEqual(OpportunityAuditEvent.objects.count(), 1)
        self.assertEqual(OpportunityNumberSequence.objects.get(pk=1).next_number, 102102)

    def test_waiting_registration_reuses_only_an_uncommitted_rolled_back_number(self):
        first, second = self.race(rollback_winner=True)
        self.assertEqual(first[0], 'rolled_back')
        self.assertEqual(second[0], 201, second)
        self.assertEqual(second[1]['deal_code'], 'Q-102101')
        self.assertEqual(Deal.objects.count(), 1)
        self.assertEqual(OpportunityAuditEvent.objects.count(), 1)
        self.assertEqual(OpportunityAuditEvent.objects.get().actor_id, self.actors[1].pk)
        self.assertEqual(OpportunityNumberSequence.objects.get(pk=1).next_number, 102102)
        retry_client = APIClient()
        retry_client.force_authenticate(self.actors[0])
        retry = retry_client.post('/api/v1/sales/deals/', self.payload, format='json')
        self.assertEqual(retry.status_code, 201, retry.data)
        self.assertEqual(retry.data['deal_code'], 'Q-102102')
        self.assertEqual(OpportunityAuditEvent.objects.count(), 2)
        self.assertEqual(OpportunityNumberSequence.objects.get(pk=1).next_number, 102103)
