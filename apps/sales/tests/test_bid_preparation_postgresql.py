"""Observed preparation contention on disposable PostgreSQL, never a live database."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase, override_settings

from apps.planning_intelligence.models import PlanningAuditEvent, TechnicalProposal
from apps.sales import bid_preparation as service
from apps.sales.models import BidPreparation, BidPreparationCommand, OpportunityAuditEvent, QuotePreparationRevision
from . import test_bid_preparation as fixtures


@skipUnless(connection.vendor == 'postgresql', 'Real PostgreSQL row locks required')
@override_settings(ROOT_URLCONF='apps.sales.tests.test_bid_preparation')
class BidPreparationConcurrencyTests(fixtures.PreparationFixtures, TransactionTestCase):
    def wait_for_lock(self):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND wait_event_type='Lock')")
                if cursor.fetchone()[0]:
                    return True
            time.sleep(.02)
        return False

    def execute(self, command, record_id, actor_id, payload):
        close_old_connections()
        try:
            return command(record_id, get_user_model().objects.get(pk=actor_id), payload)
        except service.PreparationConflict:
            return 'conflict'
        finally:
            close_old_connections()

    def competing(self, command, record_id, payload, *, same_request):
        second_actor = self.actor
        rival_payload = payload
        if not same_request:
            second_actor = get_user_model().objects.create_superuser(
                username='bid-second-reviewer', email='bid-second@example.test', password='synthetic')
            fixtures.grant_sales_actions(second_actor, 'sales', 'sales_opportunities', 'sales_proposals', 'planning_package')
            self.api.force_authenticate(second_actor)
            rival_payload = self.apply_payload(request_id=str(uuid4()), reason='Second reviewer')
            self.api.force_authenticate(self.actor)
        entered, release = threading.Event(), threading.Event()
        original = service._audit
        calls = []

        def held_audit(*args, **kwargs):
            if not calls:
                calls.append(True)
                entered.set()
                if not release.wait(12):
                    raise AssertionError('Preparation contention fixture did not release its first writer')
            return original(*args, **kwargs)

        with patch.object(service, '_audit', held_audit), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.execute, command, record_id, self.actor.pk, payload)
            rival = None
            try:
                self.assertTrue(entered.wait(6), 'First preparation command never reached its atomic audit')
                rival = pool.submit(self.execute, command, record_id, second_actor.pk, rival_payload)
                observed = self.wait_for_lock()
            finally:
                release.set()
            first_result = first.result(timeout=12)
            self.assertIsNotNone(rival)
            second_result = rival.result(timeout=12)
        self.assertTrue(observed, 'Rival never waited for an observed PostgreSQL row lock')
        self.assertFalse(first_result['replayed'])
        if same_request:
            self.assertTrue(second_result['replayed'])
        else:
            self.assertEqual(second_result, 'conflict')

    def test_duplicate_connection_waits_and_has_one_effect(self):
        payload = self.connect_payload(mode='create')
        payload.pop('planning_project_id')
        initial_projects = self.project.__class__.objects.count()
        self.competing(service.connect_preparation, self.deal.pk, payload, same_request=True)
        self.assertEqual(BidPreparation.objects.count(), 1)
        self.assertEqual(self.project.__class__.objects.count(), initial_projects + 1)
        self.assertEqual(BidPreparationCommand.objects.count(), 1)
        self.assertEqual(OpportunityAuditEvent.objects.filter(event_type='bid_preparation_connected').count(), 1)

    def test_duplicate_capture_waits_and_replays_once(self):
        self.attach()
        self.competing(service.apply_preparation, self.quote.pk, self.apply_payload(), same_request=True)
        self.assertEqual(QuotePreparationRevision.objects.count(), 1)
        self.assertEqual(BidPreparationCommand.objects.filter(action='prepare').count(), 1)
        self.assertEqual(OpportunityAuditEvent.objects.filter(event_type='proposal_preparation_captured').count(), 1)

    def test_competing_reviewers_cannot_overwrite_new_capture(self):
        self.attach()
        self.competing(service.apply_preparation, self.quote.pk, self.apply_payload(), same_request=False)
        self.assertEqual(QuotePreparationRevision.objects.count(), 1)
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.scope, 'Reviewed technical solution')

    def test_source_writer_can_finish_audit_while_capture_waits_then_capture_is_stale(self):
        self.attach()
        payload = self.apply_payload()
        entered, release = threading.Event(), threading.Event()

        def edit_source():
            close_old_connections()
            try:
                with transaction.atomic():
                    proposal = TechnicalProposal.objects.select_for_update().get(pk=self.technical.pk)
                    entered.set()
                    if not release.wait(12):
                        raise AssertionError('Source writer was not released')
                    proposal.title = 'A concurrent technical revision edit'
                    proposal.save(update_fields=['title', 'updated_at'])
                    PlanningAuditEvent.objects.create(project_id=self.project.pk, actor_id=self.actor.pk,
                        action='proposal_updated', entity_type='technical_proposal', entity_id=str(proposal.pk))
                    # Force FK checks while both transactions are still active.
                    with connection.cursor() as cursor:
                        cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            writer = pool.submit(edit_source)
            capture = None
            try:
                self.assertTrue(entered.wait(6))
                capture = pool.submit(self.execute, service.apply_preparation, self.quote.pk, self.actor.pk, payload)
                observed = self.wait_for_lock()
            finally:
                release.set()
            writer.result(timeout=12)
            self.assertIsNotNone(capture)
            self.assertEqual(capture.result(timeout=12), 'conflict')
        self.assertTrue(observed, 'Capture was not observed waiting on the selected technical proposal')
        self.assertFalse(QuotePreparationRevision.objects.exists())
        self.assertEqual(PlanningAuditEvent.objects.filter(action='proposal_updated').count(), 1)
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.scope, 'Authored commercial scope')
