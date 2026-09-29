"""Observe concurrent new-customer resolution on disposable PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, current_thread
from time import monotonic, sleep
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, connections, transaction
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from apps.sales.mailbox_opportunities import email_review_token
from apps.sales.models import Client, Deal, OpportunityAuditEvent, SalesMailboxConnection

from . import test_email_opportunity_concurrency as fixtures


@skipUnless(connection.vendor == 'postgresql', 'Requires disposable PostgreSQL')
@override_settings(ROOT_URLCONF='apps.sales.tests.test_email_opportunity_concurrency')
class EmailClientConcurrencyTests(TransactionTestCase):
    setUp = fixtures.EmailOpportunityConcurrencyTests.setUp

    def race(self, *, same_tender):
        self.account.delete()
        second_mailbox = SalesMailboxConnection.objects.create(
            name='Second synthetic mailbox', mailbox_address='second@example.test',
            auth_mode='application', tenant_id='synthetic-tenant', client_id='synthetic-client',
        )
        messages, payloads = {}, []
        for index, mailbox in enumerate((self.mailbox, second_mailbox)):
            reference = 'Tender_38669' if same_tender or index == 0 else 'Tender_38670'
            evidence = {
                'detection_version': 2, 'organization_name': 'New Concurrent Buyer',
                'tender_reference': reference, 'source_portal': 'Synthetic Tender Portal',
                'evidence': {'organization_name': 'Company name: New Concurrent Buyer',
                             'tender_reference': f'Tender Code {reference}'},
                'field_sources': {'organization_name': ['m1-current'], 'tender_reference': ['m1-current']},
            }
            message = {**self.message, 'id': f'synthetic-new-client-{index}', 'extracted_information': evidence}
            messages[message['id']] = message
            payload = {key: value for key, value in self.payload.items() if key != 'client'}
            payload.update(message_id=message['id'], source_token=email_review_token(mailbox, self.actor, message),
                           new_client={'company_name': 'New Concurrent Buyer'}, client_reference=reference)
            payloads.append((f'/api/v1/sales/mailbox-connections/{mailbox.pk}/convert-to-opportunity/', payload))

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

        with (
            patch('apps.sales.mailbox_opportunities.SalesMicrosoftGraphService.get_message', side_effect=messages.__getitem__),
            patch.object(OpportunityAuditEvent.objects, 'create', side_effect=pause_audit),
            ThreadPoolExecutor(max_workers=2, thread_name_prefix='new-email-customer') as pool,
        ):
            first = pool.submit(invoke, 'winner', *payloads[0])
            second = None
            try:
                self.assertTrue(winner_locked.wait(10), 'Winner did not reach customer/audit transaction')
                second = pool.submit(invoke, 'contender', *payloads[1])
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
                    if second.done():
                        self.fail(f'Contender completed without a database lock wait: {second.result()[0]}')
                    sleep(0.04)
                self.assertTrue(blocked, 'No PostgreSQL customer resolution lock wait observed')
                self.assertNotEqual(pids['winner'], pids['contender'])
            finally:
                release_winner.set()
            results = first.result(timeout=20), second.result(timeout=20)
        self.assertEqual(Client.objects.count(), 1)
        print(f'EMAIL_CUSTOMER_LOCK_EVIDENCE same_tender={same_tender} distinct_mailboxes=True blocked=True clients=1')
        return results

    def test_distinct_sources_new_customer_create_once_and_reuse_for_different_tenders(self):
        first, second = self.race(same_tender=False)
        self.assertEqual((first[0], second[0]), (201, 201), (first, second))
        self.assertEqual(Deal.objects.count(), 2)
        self.assertEqual(OpportunityAuditEvent.objects.count(), 2)
        self.assertEqual(Deal.objects.values('client_id').distinct().count(), 1)
        modes = list(OpportunityAuditEvent.objects.values_list('data__reviewed_customer_resolution__mode', flat=True))
        self.assertCountEqual(modes, ['created', 'exact_name'])

    def test_distinct_sources_same_new_customer_and_tender_create_one_opportunity(self):
        first, second = self.race(same_tender=True)
        self.assertEqual((first[0], second[0]), (201, 409), (first, second))
        self.assertEqual(str(second[1]['code']), 'email_tender_already_exists')
        self.assertEqual(Deal.objects.count(), 1)
        self.assertEqual(OpportunityAuditEvent.objects.count(), 1)

    def test_pending_ordinary_client_insert_is_seen_after_resolution_lock_wait(self):
        self.account.delete()
        message = {**self.message, 'extracted_information': {
            'detection_version': 2, 'organization_name': 'Concurrent Ordinary Buyer',
            'evidence': {'organization_name': 'Company name: Concurrent Ordinary Buyer'},
            'field_sources': {'organization_name': ['m1-current']},
        }}
        payload = {key: value for key, value in self.payload.items() if key != 'client'}
        payload.update(source_token=email_review_token(self.mailbox, self.actor, message),
                       new_client={'company_name': 'Concurrent Ordinary Buyer'})
        inserted, release_insert, contender_connected = Event(), Event(), Event()
        pids = {}

        def insert_customer():
            connections.close_all()
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT pg_backend_pid()')
                        pids['writer'] = cursor.fetchone()[0]
                    customer = Client.objects.create(
                        client_code='ORDINARY-CONCURRENT', company_name='Concurrent Ordinary Buyer',
                        industry_type='other', account_manager_id=self.actor.pk,
                    )
                    inserted.set()
                    if not release_insert.wait(15):
                        raise AssertionError('Ordinary insertion release timed out')
                return customer.pk
            finally:
                connections.close_all()

        def convert():
            connections.close_all()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    pids['converter'] = cursor.fetchone()[0]
                api = APIClient()
                api.force_authenticate(get_user_model().objects.get(pk=self.actor.pk))
                contender_connected.set()
                response = api.post(self.endpoint, payload, format='json')
                return response.status_code, response.data
            finally:
                connections.close_all()

        with (
            patch('apps.sales.mailbox_opportunities.SalesMicrosoftGraphService.get_message', return_value=message),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            writer = pool.submit(insert_customer)
            converter = None
            try:
                self.assertTrue(inserted.wait(8))
                converter = pool.submit(convert)
                self.assertTrue(contender_connected.wait(5))
                blocked = False
                deadline = monotonic() + 7
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT pg_blocking_pids(%s)', [pids['converter']])
                        blockers = cursor.fetchone()[0]
                    if pids['writer'] in blockers:
                        blocked = True
                        break
                    if converter.done():
                        self.fail(f'Conversion did not wait for ordinary client insertion: {converter.result()[0]}')
                    sleep(0.04)
                self.assertTrue(blocked, 'No lock wait on ordinary Client writer observed')
            finally:
                release_insert.set()
            customer_id = writer.result(timeout=20)
            response = converter.result(timeout=20)
        self.assertEqual(response[0], 201, response)
        self.assertEqual(Client.objects.count(), 1)
        self.assertEqual(Deal.objects.get().client_id, customer_id)
        self.assertEqual(OpportunityAuditEvent.objects.get().data['reviewed_customer_resolution']['mode'], 'exact_name')
        print('EMAIL_CUSTOMER_GENERIC_WRITE_LOCK_EVIDENCE blocked=True clients=1 reused=True')
