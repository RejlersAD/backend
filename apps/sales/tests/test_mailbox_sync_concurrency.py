"""Observe mailbox worker fencing on explicitly disposable PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from threading import Event, current_thread
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.sales import mailbox_sync as sync
from apps.sales.models import SalesEmailIntake, SalesMailboxConnection

from .access_fixtures import grant_sales_actions
from .test_mailbox_sync import SOURCE


@skipUnless(connection.vendor == 'postgresql', 'Requires explicitly disposable PostgreSQL')
@override_settings(SALES_MAILBOX_SYNC_ENABLED=True, CELERY_TASK_ALWAYS_EAGER=False,
                   CELERY_BROKER_URL='memory://', CELERY_RESULT_BACKEND='cache+memory://')
class MailboxSyncConcurrencyTests(TransactionTestCase):
    def setUp(self):
        # Fixture user signals can create notifications. No synthetic test may
        # dispatch those unrelated deliveries to a configured external worker.
        delivery = patch('celery.app.task.Task.apply_async')
        delivery.start()
        self.addCleanup(delivery.stop)
        cache.clear()
        self.addCleanup(cache.clear)
        self.actor = get_user_model().objects.create_user('sync-race-owner', email='sync-race@example.test')
        grant_sales_actions(self.actor, 'sales_email_intake')
        self.mailbox = SalesMailboxConnection.objects.create(
            name='Synthetic sync race', created_by=self.actor, mailbox_address='sales@example.test',
            tenant_id='synthetic-tenant', client_id='synthetic-client', auth_mode='application',
        )
        self.state = sync.configure_mailbox_sync(connection=self.mailbox, user=self.actor, enabled=True)
        now = timezone.now()
        self.state.folder_discovery_completed_at = now
        self.state.folder_discovery_due_at = now + timedelta(hours=1)
        self.state.save()
        self.item = self.state.items.create(message_id=SOURCE['id'])

    def invoke(self):
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_backend_pid()')
                pid = cursor.fetchone()[0]
            return pid, sync.run_mailbox_sync(self.mailbox.pk)
        finally:
            connections.close_all()

    def exercise(self, action):
        fetching, release = Event(), Event()

        def fetch(_message_id):
            if current_thread().name.endswith('_0'):
                fetching.set()
                if not release.wait(12):
                    raise AssertionError('Worker fetch release timed out')
            return deepcopy(SOURCE)

        with patch.object(sync, 'SalesMailboxSyncGraphService') as service:
            service.return_value.get_message_for_capture.side_effect = fetch
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix='sync-race') as pool:
                first = pool.submit(self.invoke)
                try:
                    self.assertTrue(fetching.wait(8), 'First worker did not reach unlocked Graph read')
                    self.state.refresh_from_db()
                    self.assertIsNotNone(self.state.lease_token)
                    second = action(pool)
                finally:
                    release.set()
                first_result = first.result(timeout=15)
            self.state.refresh_from_db()
            self.item.refresh_from_db()
            return first_result, second, service.return_value.get_message_for_capture.call_count

    def test_competing_workers_only_one_claim_fetch_and_capture(self):
        def contender(pool):
            result = pool.submit(self.invoke).result(timeout=6)
            self.assertEqual(result[1]['status'], 'not_claimed')
            return result

        first, second, reads = self.exercise(contender)
        self.assertNotEqual(first[0], second[0])
        self.assertEqual(first[1]['status'], 'up_to_date')
        self.assertEqual(reads, 1)
        self.assertEqual(SalesEmailIntake.objects.count(), 1)
        self.assertEqual(self.item.status, 'captured')
        self.assertIsNotNone(self.item.intake_id)
        self.assertIsNone(self.state.lease_token)

    def test_pause_during_unlocked_provider_fetch_prevents_source_commit(self):
        def pause(_pool):
            before = self.state.folder_cursor
            sync.configure_mailbox_sync(connection=self.mailbox, user=self.actor, enabled=False)
            return before

        first, cursor_before, reads = self.exercise(pause)
        self.assertEqual(first[1]['status'], 'stopped')
        self.assertEqual(reads, 1)
        self.assertFalse(SalesEmailIntake.objects.exists())
        self.assertEqual(self.item.status, 'pending')
        self.assertIsNone(self.item.intake_id)
        self.assertEqual(self.state.status, 'paused')
        self.assertEqual(self.state.folder_cursor, cursor_before)
        self.assertIsNone(self.state.initial_sync_completed_at)
        self.assertIsNone(self.state.lease_token)

    def test_reclaimed_expired_lease_fences_previous_worker_after_fetch(self):
        def reclaim(pool):
            self.state.lease_expires_at = timezone.now() - timedelta(seconds=1)
            self.state.save(update_fields=['lease_expires_at'])
            result = pool.submit(self.invoke).result(timeout=8)
            self.assertEqual(result[1]['status'], 'up_to_date')
            return result, SalesEmailIntake.objects.values().get()

        first, (second, snapshot), reads = self.exercise(reclaim)
        self.assertNotEqual(first[0], second[0])
        self.assertEqual(first[1]['status'], 'stopped')
        self.assertEqual(reads, 2)
        self.assertEqual(SalesEmailIntake.objects.count(), 1)
        self.assertEqual(SalesEmailIntake.objects.values().get(), snapshot)
        self.assertEqual(self.item.status, 'captured')
        self.assertEqual(self.item.attempts, 1)
        self.assertEqual(self.state.status, 'up_to_date')
        self.assertIsNone(self.state.lease_token)
