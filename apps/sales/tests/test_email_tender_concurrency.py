"""Distinct mailbox reminders serialize on the canonical client in PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event, current_thread
from time import monotonic, sleep
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, connections
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from apps.sales.mailbox_opportunities import email_review_token
from apps.sales.models import Deal, OpportunityAuditEvent, SalesMailboxConnection

from . import test_email_opportunity_concurrency as race_fixtures


@skipUnless(connection.vendor == 'postgresql', 'Requires disposable PostgreSQL')
@override_settings(ROOT_URLCONF='apps.sales.tests.test_email_opportunity_concurrency')
class EmailTenderConcurrencyTests(TransactionTestCase):
    setUp = race_fixtures.EmailOpportunityConcurrencyTests.setUp

    def test_distinct_mailboxes_same_tender_wait_on_client_and_create_once(self):
        second_mailbox = SalesMailboxConnection.objects.create(
            name='Second synthetic mailbox', mailbox_address='sales-two@example.test',
            auth_mode='application', tenant_id='synthetic-tenant', client_id='synthetic-client',
        )
        evidence = {
            'tender_reference': 'Tender_38669', 'source_portal': 'OQ Tawreed Portal',
            'evidence': {'tender_reference': 'Tender Code Tender_38669'},
            'field_sources': {'tender_reference': ['m1-current']},
        }
        first_message = {**self.message, 'extracted_information': evidence}
        second_message = {**first_message, 'id': 'synthetic-second-reminder'}
        first_payload = {
            **self.payload, 'client_reference': 'Tender_38669',
            'source_token': email_review_token(self.mailbox, self.actor, first_message),
        }
        second_payload = {
            **deepcopy(first_payload), 'message_id': second_message['id'],
            'source_token': email_review_token(second_mailbox, self.actor, second_message),
        }
        second_endpoint = f'/api/v1/sales/mailbox-connections/{second_mailbox.pk}/convert-to-opportunity/'
        winner_locked, release_winner, contender_connected = Event(), Event(), Event()
        pids = {}
        original_create = OpportunityAuditEvent.objects.create

        def pause_audit(**kwargs):
            event = original_create(**kwargs)
            if current_thread().name.endswith('_0') and not winner_locked.is_set():
                winner_locked.set()
                if not release_winner.wait(15):
                    raise AssertionError('Winner release timed out')
            return event

        def invoke(label, endpoint, payload):
            connections.close_all()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    pids[label] = cursor.fetchone()[0]
                client = APIClient()
                client.force_authenticate(get_user_model().objects.get(pk=self.actor.pk))
                if label == 'contender':
                    contender_connected.set()
                response = client.post(endpoint, payload, format='json')
                return response.status_code, response.data
            finally:
                connections.close_all()

        def message(message_id):
            return first_message if message_id == first_message['id'] else second_message

        with (
            patch('apps.sales.mailbox_opportunities.SalesMicrosoftGraphService.get_message', side_effect=message),
            patch.object(OpportunityAuditEvent.objects, 'create', side_effect=pause_audit),
            ThreadPoolExecutor(max_workers=2, thread_name_prefix='distinct-tender') as pool,
        ):
            winner = pool.submit(invoke, 'winner', self.endpoint, first_payload)
            contender = None
            try:
                self.assertTrue(winner_locked.wait(10), 'First request did not reach locked client/audit')
                contender = pool.submit(invoke, 'contender', second_endpoint, second_payload)
                self.assertTrue(contender_connected.wait(5))
                blocked = False
                deadline = monotonic() + 7
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT pg_blocking_pids(%s)', [pids['contender']])
                        blockers = cursor.fetchone()[0]
                    if pids['winner'] in blockers:
                        blocked = True
                        break
                    if contender.done():
                        self.fail(f'Contender completed without client lock wait: {contender.result()[0]}')
                    sleep(0.04)
                self.assertTrue(blocked, 'No PostgreSQL client-row wait observed')
                self.assertNotEqual(pids['winner'], pids['contender'])
            finally:
                release_winner.set()
            first = winner.result(timeout=20)
            second = contender.result(timeout=20)
        self.assertEqual(first[0], 201, first)
        self.assertEqual(second[0], 409, second)
        self.assertEqual(str(second[1]['code']), 'email_tender_already_exists')
        self.assertEqual(Deal.objects.count(), 1)
        self.assertEqual(OpportunityAuditEvent.objects.count(), 1)
        print('EMAIL_TENDER_CLIENT_LOCK_EVIDENCE distinct_mailboxes=True blocked=True created=1 conflict=1')
