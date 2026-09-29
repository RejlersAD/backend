"""Observed capture contention against explicitly disposable PostgreSQL only."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event, current_thread
from time import monotonic, sleep
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection, connections
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from apps.rbac.models import Permission, UserPermissionOverride
from apps.sales.microsoft_graph import SalesMicrosoftGraphService
from apps.sales.models import Deal, OpportunityAuditEvent, SalesEmailIntake, SalesMailboxConnection

from .access_fixtures import grant_sales_actions
from .test_mailbox_browsing import graph_response
from .test_mailbox_capture import CAPTURE_MESSAGE


@skipUnless(connection.vendor == 'postgresql', 'Requires explicitly disposable PostgreSQL')
@override_settings(
    ROOT_URLCONF='apps.sales.tests.test_mailbox_capture',
    SALES_MICROSOFT_GRAPH_BASE_URL='https://graph.microsoft.com/v1.0',
)
class MailboxCaptureConcurrencyTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.actor = get_user_model().objects.create_user('capture-race-owner', email='capture-race@example.test')
        grant_sales_actions(self.actor, 'sales_email_intake')
        self.mailbox = SalesMailboxConnection.objects.create(
            name='Synthetic capture race mailbox', tenant_id='synthetic-race-tenant',
            client_id='synthetic-race-client', mailbox_address='sales@example.test',
            auth_mode='application', enabled=False, created_by=self.actor,
        )
        self.endpoint = f'/api/v1/sales/mailbox-connections/{self.mailbox.pk}/capture-message/'

    def race(self, *, changed_later_source=False, revoke_after_wait=False):
        winner_locked, release_winner, contender_connected = Event(), Event(), Event()
        pids = {}
        original_save = SalesEmailIntake.save

        def save_and_hold(instance, *args, **kwargs):
            adding = instance._state.adding
            result = original_save(instance, *args, **kwargs)
            if adding and current_thread().name.endswith('_0'):
                winner_locked.set()
                if not release_winner.wait(12):
                    raise AssertionError('Capture winner release timed out')
            return result

        def graph_read(*_args, **_kwargs):
            message = deepcopy(CAPTURE_MESSAGE)
            message['body'] = {'contentType': 'text', 'content': 'Original captured source.'}
            if changed_later_source and current_thread().name.endswith('_1'):
                message['subject'] = 'Later concurrent source subject'
                message['body']['content'] = 'Later concurrent source must not overwrite the captured snapshot.'
            return graph_response(message)

        def invoke(label):
            connections.close_all()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    pids[label] = cursor.fetchone()[0]
                client = APIClient()
                client.force_authenticate(get_user_model().objects.get(pk=self.actor.pk))
                if label == 'contender':
                    contender_connected.set()
                response = client.post(self.endpoint, {'message_id': CAPTURE_MESSAGE['id']}, format='json')
                return response.status_code, response.data
            finally:
                connections.close_all()

        with (
            patch.object(SalesMicrosoftGraphService, 'token', return_value='synthetic-capture-race-token'),
            patch('apps.sales.microsoft_graph.requests.request', side_effect=graph_read) as network,
            patch.object(SalesEmailIntake, 'save', autospec=True, side_effect=save_and_hold),
            ThreadPoolExecutor(max_workers=2, thread_name_prefix='capture-race') as pool,
        ):
            winner = pool.submit(invoke, 'winner')
            contender = None
            try:
                self.assertTrue(winner_locked.wait(8), 'Winner did not store a snapshot while holding the connection lock')
                contender = pool.submit(invoke, 'contender')
                self.assertTrue(contender_connected.wait(5), 'Contender did not connect')
                blocked = False
                deadline = monotonic() + 6
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT pg_blocking_pids(%s)', [pids['contender']])
                        blockers = cursor.fetchone()[0]
                    if pids['winner'] in blockers:
                        blocked = True
                        break
                    if contender.done():
                        self.fail(f'Contender finished without a row-lock wait: {contender.result()[0]}')
                    sleep(0.04)
                self.assertTrue(blocked, 'No real PostgreSQL row-lock wait was observed')
                self.assertNotEqual(pids['winner'], pids['contender'])
                print(f'MAILBOX_CAPTURE_LOCK_EVIDENCE test={self._testMethodName} blocked=True distinct_connections=True')
                if revoke_after_wait:
                    permission = Permission.objects.get(
                        module__code='sales_email_intake', action='create', is_active=True,
                    )
                    UserPermissionOverride.objects.create(
                        user_profile=self.actor.rbac_profile, permission=permission, allowed=False,
                    )
                    cache.clear()
            finally:
                release_winner.set()
            first = winner.result(timeout=15)
            second = contender.result(timeout=15)
            self.assertEqual(network.call_count, 2)
            self.assertTrue(all(call.args[0] == 'GET' for call in network.call_args_list))
            self.assertTrue(all('IdType="ImmutableId"' in call.kwargs['headers']['Prefer'] for call in network.call_args_list))
        return first, second

    def assert_one_snapshot(self, first, second, *, contender_denied=False):
        self.assertEqual(first[0], 201, first)
        self.assertTrue(first[1]['created'])
        if contender_denied:
            self.assertEqual(second[0], 403, second)
            self.assertNotIn('intake', second[1])
        else:
            self.assertEqual(second[0], 200, second)
            self.assertFalse(second[1]['created'])
            self.assertEqual(first[1]['intake']['id'], second[1]['intake']['id'])
        self.assertEqual(SalesEmailIntake.objects.count(), 1)
        intake = SalesEmailIntake.objects.get()
        self.assertEqual(intake.mailbox_connection_id, self.mailbox.pk)
        self.assertEqual(intake.captured_by_id, self.actor.pk)
        self.assertEqual(intake.body_preview, 'Original captured source.')
        self.assertEqual(intake.subject, CAPTURE_MESSAGE['subject'])
        self.assertEqual(intake.status, 'received')
        self.assertEqual(Deal.objects.count(), 0)
        self.assertEqual(OpportunityAuditEvent.objects.count(), 0)
        self.mailbox.refresh_from_db()
        self.assertFalse(self.mailbox.enabled)

    def test_simultaneous_capture_returns_created_then_existing_with_one_snapshot(self):
        self.assert_one_snapshot(*self.race())

    def test_waiting_capture_with_changed_source_keeps_the_first_authoritative_snapshot(self):
        self.assert_one_snapshot(*self.race(changed_later_source=True))

    def test_create_permission_revoked_during_observed_lock_wait_blocks_the_contender(self):
        self.assert_one_snapshot(*self.race(revoke_after_wait=True), contender_denied=True)
