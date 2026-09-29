"""Observed row-lock races in a disposable PostgreSQL database only."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event, current_thread
from time import monotonic, sleep
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, connections
from django.test import TransactionTestCase, override_settings
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.rbac.route_guard import secure_module_endpoints
from apps.sales.intake_views import SalesEmailIntakeViewSet
from apps.sales.mailbox_opportunities import email_review_token
from apps.sales.models import Client, Deal, OpportunityAuditEvent, SalesEmailIntake, SalesMailboxConnection
from apps.sales.views import SalesMailboxConnectionViewSet

from .access_fixtures import grant_sales_actions


router = DefaultRouter()
router.register('mailbox-connections', SalesMailboxConnectionViewSet, basename='email-race-mailboxes')
router.register('email-intakes', SalesEmailIntakeViewSet, basename='email-pg-intakes')
urlpatterns = [path('api/v1/sales/', include(router.urls))]
secure_module_endpoints(urlpatterns)


@skipUnless(connection.vendor == 'postgresql', 'Requires disposable PostgreSQL')
@override_settings(ROOT_URLCONF=__name__)
class EmailOpportunityConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username='email-race-reviewer', email='reviewer@example.test', password='synthetic-only',
        )
        grant_sales_actions(self.actor, 'sales_email_intake', 'sales_opportunities', 'sales_clients')
        self.mailbox = SalesMailboxConnection.objects.create(
            name='Synthetic shared mailbox', mailbox_address='sales@example.test',
            auth_mode='application', tenant_id='synthetic-tenant', client_id='synthetic-client',
        )
        self.account = Client.objects.create(
            client_code='EMAIL-RACE-001', company_name='Synthetic Energy', industry_type='other',
        )
        self.message = {
            'id': 'synthetic-immutable-message', 'subject': 'RFT for engineering',
            'sender_name': 'Synthetic Contact', 'sender_email': 'contact@example.test',
            'received_at': '2026-09-28T08:00:00Z', 'sent_at': '2026-09-28T07:59:00Z',
            'body_text': 'Customer name: Synthetic Energy\nDue date: 2026-10-23',
        }
        self.payload = {
            'message_id': self.message['id'],
            'source_token': email_review_token(self.mailbox, self.actor, self.message),
            'deal_name': 'Reviewed synthetic engineering work', 'client': str(self.account.pk),
            'estimated_value': '250000.50', 'currency': 'AED',
            'expected_close_date': '2026-11-30', 'submission_due_date': '2026-10-23',
            'scope_type': 'feed', 'client_reference': 'RFT-2026-001',
            'description': 'Reviewed synthetic scope',
            'classification_code': 'rft', 'classification_confirmed': True,
        }
        self.endpoint = f'/api/v1/sales/mailbox-connections/{self.mailbox.pk}/convert-to-opportunity/'

    def race(self, *, change_payload=False, change_classification=False):
        winner_locked, release_winner, contender_connected = Event(), Event(), Event()
        pids = {}
        original_create = OpportunityAuditEvent.objects.create

        def create_and_pause(**kwargs):
            event = original_create(**kwargs)
            if current_thread().name.endswith('_0') and not winner_locked.is_set():
                winner_locked.set()
                if not release_winner.wait(12):
                    raise AssertionError('Winner release timed out')
            return event

        def invoke(label, payload):
            connections.close_all()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    pids[label] = cursor.fetchone()[0]
                client = APIClient()
                client.force_authenticate(get_user_model().objects.get(pk=self.actor.pk))
                if label == 'contender':
                    contender_connected.set()
                response = client.post(self.endpoint, payload, format='json')
                return response.status_code, response.data
            finally:
                connections.close_all()

        contender_payload = deepcopy(self.payload)
        if change_payload:
            contender_payload['estimated_value'] = '260000.50'
        if change_classification:
            contender_payload['classification_code'] = 'rfq'
        with (
            patch('apps.sales.mailbox_opportunities.SalesMicrosoftGraphService.get_message', return_value=self.message),
            patch.object(OpportunityAuditEvent.objects, 'create', side_effect=create_and_pause),
            ThreadPoolExecutor(max_workers=2, thread_name_prefix='email-conversion') as pool,
        ):
            first = pool.submit(invoke, 'winner', self.payload)
            second = None
            try:
                self.assertTrue(winner_locked.wait(8), 'Winner did not hold the connection lock')
                second = pool.submit(invoke, 'contender', contender_payload)
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
                        self.fail(f'Contender finished without a row-lock wait: {second.result()[0]}')
                    sleep(0.04)
                self.assertTrue(blocked, 'No real PostgreSQL row-lock wait observed')
                self.assertNotEqual(pids['winner'], pids['contender'])
                print(f'EMAIL_CONVERSION_LOCK_EVIDENCE test={self._testMethodName} blocked=True distinct_connections=True')
            finally:
                release_winner.set()
            return first.result(timeout=15), second.result(timeout=15)

    def test_simultaneous_identical_submit_creates_one_opportunity_and_audit(self):
        winner, contender = self.race()
        self.assertEqual(winner[0], 201, winner)
        self.assertEqual(contender[0], 200, contender)
        self.assertTrue(winner[1]['created'])
        self.assertFalse(contender[1]['created'])
        self.assertEqual(winner[1]['opportunity']['id'], contender[1]['opportunity']['id'])
        self.assertEqual(Deal.objects.count(), 1)
        self.assertEqual(OpportunityAuditEvent.objects.filter(event_type='opportunity_created_from_email').count(), 1)

    def test_simultaneous_changed_submit_conflicts_without_a_second_opportunity(self):
        winner, contender = self.race(change_payload=True)
        self.assertEqual(winner[0], 201, winner)
        self.assertEqual(contender[0], 409, contender)
        self.assertEqual(Deal.objects.count(), 1)
        self.assertEqual(OpportunityAuditEvent.objects.filter(event_type='opportunity_created_from_email').count(), 1)

    def test_simultaneous_changed_classification_conflicts_and_preserves_winner_review(self):
        winner, contender = self.race(change_classification=True)
        self.assertEqual(winner[0], 201, winner)
        self.assertEqual(contender[0], 409, contender)
        self.assertEqual(Deal.objects.count(), 1)
        event = OpportunityAuditEvent.objects.get(event_type='opportunity_created_from_email')
        self.assertEqual(event.data['reviewed_classification']['code'], 'rft')
        self.assertEqual(event.data['reviewed_classification']['confirmed_by'], str(self.actor.pk))

    def test_imported_conversion_locks_the_scoped_intake_without_nullable_joins(self):
        intake = SalesEmailIntake.objects.create(
            source_message_id='synthetic-imported-pg', subject='Reviewed imported enquiry',
            sender_email='contact@example.test', received_at='2026-09-28T08:00:00Z',
        )
        client = APIClient()
        client.force_authenticate(self.actor)
        payload = {
            key: value for key, value in self.payload.items()
            if key not in {'message_id', 'source_token'}
        }
        endpoint = f'/api/v1/sales/email-intakes/{intake.pk}/convert-to-opportunity/'
        with patch('apps.sales.microsoft_graph.SalesMicrosoftGraphService.get_message') as graph:
            first = client.post(endpoint, payload, format='json')
            repeated = client.post(endpoint, payload, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(repeated.status_code, 200, repeated.data)
        self.assertTrue(first.data['created'])
        self.assertFalse(repeated.data['created'])
        self.assertEqual(first.data['opportunity']['id'], repeated.data['opportunity']['id'])
        intake.refresh_from_db()
        self.assertEqual(intake.status, 'converted')
        self.assertEqual(intake.opportunity_id, Deal.objects.get().pk)
        self.assertEqual(OpportunityAuditEvent.objects.filter(event_type='opportunity_created_from_email').count(), 1)
        graph.assert_not_called()
