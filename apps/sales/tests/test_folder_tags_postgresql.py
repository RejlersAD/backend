"""Observed competing tag saves against disposable PostgreSQL only."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings

from apps.sales import folder_tags as service
from apps.sales.models import OpportunityAuditEvent, OpportunityFolderTag
from . import test_folder_tags as fixtures


@skipUnless(connection.vendor == 'postgresql', 'Real PostgreSQL row locks required')
@override_settings(ROOT_URLCONF='apps.sales.tests.test_folder_tags', SALES_WORKSPACE_ENABLED=False)
class FolderTagConcurrencyTests(fixtures.FolderTagFixtures, TransactionTestCase):
    def competing(self, same_request):
        payload = self.payload('First tag')
        rival_actor, rival_payload = self.actor, payload
        if not same_request:
            rival_actor = get_user_model().objects.create_user(username='rival-tag-editor', email='rival-tag@example.test')
            fixtures.grant_sales_actions(rival_actor, 'sales', 'sales_opportunities')
            self.api.force_authenticate(rival_actor)
            rival_payload = self.payload('Competing tag')
            self.api.force_authenticate(self.actor)
        entered, release = threading.Event(), threading.Event()
        original, calls = service._audit, []

        def held_audit(*args, **kwargs):
            if not calls:
                calls.append(True)
                entered.set()
                if not release.wait(12):
                    raise AssertionError('Tag contention fixture did not release the first writer')
            return original(*args, **kwargs)

        def save(actor_id, body):
            close_old_connections()
            try:
                return service.save_folder_tag(self.deal.pk, get_user_model().objects.get(pk=actor_id), 'tender', body)
            except service.FolderTagConflict:
                return 'conflict'
            finally:
                close_old_connections()

        observed = False
        with patch.object(service, '_audit', held_audit), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(save, self.actor.pk, payload)
            rival = None
            try:
                self.assertTrue(entered.wait(6), 'First writer did not reach its atomic tag audit')
                rival = pool.submit(save, rival_actor.pk, rival_payload)
                deadline = time.monotonic() + 6
                while time.monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND wait_event_type='Lock')")
                        observed = cursor.fetchone()[0]
                    if observed:
                        break
                    time.sleep(.02)
            finally:
                release.set()
            first_result = first.result(timeout=12)
            self.assertIsNotNone(rival)
            rival_result = rival.result(timeout=12)
        self.assertTrue(observed, 'The competing request was not observed waiting on a PostgreSQL row lock')
        self.assertFalse(first_result['replayed'])
        if same_request:
            self.assertTrue(rival_result['replayed'])
        else:
            self.assertEqual(rival_result, 'conflict')
        row = OpportunityFolderTag.objects.get()
        self.assertEqual(row.tag, 'First tag')
        self.assertEqual(row.revision, 1)
        self.assertEqual(OpportunityAuditEvent.objects.filter(event_type='workspace_folder_tag_changed').count(), 1)

    def test_competing_values_wait_and_only_one_tag_is_saved(self):
        self.competing(same_request=False)

    def test_identical_retry_waits_and_replays_with_one_audit(self):
        self.competing(same_request=True)
