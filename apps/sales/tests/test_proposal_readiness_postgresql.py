"""Observed proposal-create locking on a disposable PostgreSQL database."""
import time
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from apps.sales.models import Client, OpportunityAuditEvent, Quote
from .test_bid_preparation import PreparationFixtures


@skipUnless(connection.vendor == 'postgresql', 'Real PostgreSQL row locks required')
@override_settings(ROOT_URLCONF='apps.sales.tests.test_bid_preparation')
class ProposalReadinessConcurrencyTests(PreparationFixtures, TransactionTestCase):
    def create_proposal(self, actor_id, payload):
        close_old_connections()
        try:
            api = APIClient()
            api.force_authenticate(get_user_model().objects.get(pk=actor_id))
            response = api.post('/api/v1/sales/quotes/', payload, format='json')
            return response.status_code, response.data
        finally:
            close_old_connections()

    def wait_for_client_lock(self, pending):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with connection.cursor() as cursor:
                # Stats reads may reuse one snapshot inside this deliberate
                # blocker transaction. Refresh it so a later worker wait is
                # observable rather than repeatedly seeing the first poll.
                cursor.execute('SELECT pg_stat_clear_snapshot()')
                cursor.execute(
                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                    "WHERE datname=current_database() AND pid<>pg_backend_pid() "
                    "AND wait_event_type='Lock' "
                    "AND pg_backend_pid()=ANY(pg_blocking_pids(pid)))"
                )
                if cursor.fetchone()[0]:
                    return True
            if pending.done():
                return False
            time.sleep(.02)
        return False

    def test_client_block_committed_while_create_waits_prevents_quote_and_audit(self):
        """Initial validation sees the old client; the locked recheck sees the block."""
        Client.objects.filter(pk=self.customer.pk).update(status='prospect')
        payload = {
            'deal': str(self.deal.pk), 'client': str(self.customer.pk),
            'quote_number': 'CONCURRENT-PREPARATION', 'version': 1, 'status': 'draft',
            'valid_until': '2027-01-01', 'currency': 'AED', 'scope': 'Synthetic scope',
            'subtotal': '100.00', 'total_amount': '100.00', 'estimated_cost': '60.00',
            'deliverables': ['Design report'], 'estimated_hours': {'total': '10'},
        }
        original_quotes = Quote.objects.count()
        original_audits = OpportunityAuditEvent.objects.count()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                Client.objects.select_for_update().get(pk=self.customer.pk)
                Client.objects.filter(pk=self.customer.pk).update(new_proposals_permitted=False)
                pending = pool.submit(self.create_proposal, self.actor.pk, payload)
                observed = self.wait_for_client_lock(pending)
            status, body = pending.result(timeout=20)

        self.assertTrue(observed, f'The create request must wait on the client transaction; HTTP {status}: {body}')
        self.assertEqual(status, 400, body)
        self.assertIn('client', body)
        self.assertEqual(Quote.objects.count(), original_quotes)
        self.assertEqual(OpportunityAuditEvent.objects.count(), original_audits)
