"""Observed document-type contention; disposable PostgreSQL only."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings

from apps.sales import document_classification as service
from apps.sales.models import OpportunityAuditEvent, OpportunityDocumentClassification
from apps.sales.tests.test_private_attachments import PrivateFixtures
from apps.sales.tests.test_opportunity_workspace import CONFIG


@skipUnless(connection.vendor == 'postgresql', 'Requires actual PostgreSQL row locks')
@override_settings(**CONFIG)
class DocumentClassificationConcurrencyTests(PrivateFixtures, TransactionTestCase):
    def competing(self, same_request, custom_tag=False):
        response = self.upload(name='Synthetic.txt', content=b'Synthetic text')
        self.assertEqual(response.status_code, 201, response.data)
        file_id = response.data['id']
        payload = {'document_type': 'technical_proposal', 'expected_revision': 0, 'request_id': str(uuid4())}
        rival_payload = payload if same_request else {**payload, 'document_type': 'commercial_proposal', 'request_id': str(uuid4())}
        if custom_tag:
            payload = {'custom_tag': 'Retained custom label', 'expected_revision': 0, 'request_id': str(uuid4())}
            rival_payload = payload if same_request else {
                'document_type': 'commercial_proposal', 'expected_revision': 0, 'request_id': str(uuid4()),
            }
        entered, release = Event(), Event()
        real_audit, calls = service._audit, []

        def hold(*args, **kwargs):
            if not calls:
                calls.append(True)
                entered.set()
                if not release.wait(12):
                    raise AssertionError('Classification writer was not released')
            return real_audit(*args, **kwargs)

        def save(body):
            close_old_connections()
            try:
                return service.save_document_classification(self.opportunity.pk,
                    get_user_model().objects.get(pk=self.actor.pk), 'proposal', file_id, body)
            except service.ClassificationConflict:
                return 'conflict'
            finally:
                close_old_connections()

        observed = False
        with patch.object(service, '_audit', hold), ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(save, payload)
            rival = None
            try:
                self.assertTrue(entered.wait(6))
                rival = executor.submit(save, rival_payload)
                deadline = monotonic() + 6
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND wait_event_type='Lock')")
                        observed = cursor.fetchone()[0]
                    if observed:
                        break
                    sleep(.02)
            finally:
                release.set()
            self.assertFalse(first.result(timeout=12)['replayed'])
            self.assertIsNotNone(rival)
            other = rival.result(timeout=12)
        self.assertTrue(observed, 'A competing document writer must be observed waiting for a database lock')
        self.assertTrue(other['replayed']) if same_request else self.assertEqual(other, 'conflict')
        state = OpportunityDocumentClassification.objects.get()
        if custom_tag:
            self.assertEqual((state.revision, state.confirmed_type, state.custom_tag), (1, '', 'Retained custom label'))
            self.assertEqual(OpportunityAuditEvent.objects.filter(event_type='document_custom_tag_changed').count(), 1)
            self.assertFalse(OpportunityAuditEvent.objects.filter(event_type='document_type_confirmed').exists())
        else:
            self.assertEqual((state.revision, state.confirmed_type), (1, 'technical_proposal'))
            self.assertEqual(OpportunityAuditEvent.objects.filter(event_type='document_type_confirmed').count(), 1)

    def test_competing_corrections_wait_then_reject_stale_human_revision(self):
        self.competing(False)

    def test_identical_confirmation_waits_then_replays_without_duplicate_audit(self):
        self.competing(True)

    def test_custom_tag_and_type_compete_on_shared_revision_without_lost_edit(self):
        self.competing(False, custom_tag=True)

    def test_custom_tag_identical_retry_waits_and_has_one_audit(self):
        self.competing(True, custom_tag=True)
