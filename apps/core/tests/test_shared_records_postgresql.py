"""Observed competing review commands on disposable PostgreSQL only."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings

from apps.core.shared_records import LinkConflict, link_record, version_token
from apps.core.shared_record_project import ProjectClientAdapter
from apps.core.shared_record_models import SharedRecordLinkCommand
from apps.core.project_models import Project
from apps.sales.models import Client
from apps.users.models import User
from apps.rbac.models import UserProfile
from . import test_shared_records as fixtures


@skipUnless(connection.vendor == 'postgresql', 'Real PostgreSQL row locks required')
@override_settings(ROOT_URLCONF='apps.core.tests.test_shared_records')
class SharedRecordConcurrencyTests(TransactionTestCase):
    setUp = fixtures.SharedRecordTests.setUp

    def competing(self, *, same_request):
        second = self.user
        if not same_request:
            second = User.objects.create_superuser(username='second-reviewer', email='second-reviewer@example.test', password='test')
            UserProfile.objects.update_or_create(user=second, defaults={'organization': self.user.rbac_profile.organization})
        alternative = Client.objects.create(client_code='CLIENT-ALT', company_name='Another client',
                                             account_manager=self.user, industry_type='oil_gas')
        adapter = ProjectClientAdapter()
        first_request = dict(request_id=uuid4(), expected_token=version_token(adapter, self.project, self.user),
                             reason='Verified source', targets={'client_id': str(self.client_record.pk)})
        second_request = first_request if same_request else dict(
            request_id=uuid4(), expected_token=version_token(adapter, self.project, second),
            reason='Competing review', targets={'client_id': str(alternative.pk)},
        )
        entered, release = threading.Event(), threading.Event()
        original = ProjectClientAdapter.apply
        first_call = []

        def held_apply(instance, row, actor, targets):
            if not first_call:
                first_call.append(True)
                entered.set()
                if not release.wait(10):
                    raise AssertionError('Competing test did not release the row lock')
            return original(instance, row, actor, targets)

        def run(user_id, payload):
            close_old_connections()
            try:
                return link_record('project_client', self.project.pk, User.objects.get(pk=user_id), **payload)
            except LinkConflict:
                return 'conflict'
            finally:
                close_old_connections()

        observed = False
        with patch.object(ProjectClientAdapter, 'apply', held_apply), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(run, self.user.pk, first_request)
            try:
                self.assertTrue(entered.wait(5), 'First writer never acquired the source row')
                rival = pool.submit(run, second.pk, second_request)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND wait_event_type='Lock')")
                        observed = cursor.fetchone()[0]
                    if observed:
                        break
                    time.sleep(.02)
            finally:
                release.set()
            first_result, second_result = first.result(timeout=10), rival.result(timeout=10)
        self.assertTrue(observed, 'The competing transaction was not observed waiting for a PostgreSQL lock')
        self.assertFalse(first_result['replayed'])
        if same_request:
            self.assertTrue(second_result['replayed'])
        else:
            self.assertEqual(second_result, 'conflict')
        self.project.refresh_from_db()
        self.assertEqual(self.project.client_id, self.client_record.pk)
        self.assertEqual(SharedRecordLinkCommand.objects.count(), 1)

    def test_two_reviewers_cannot_overwrite_the_first_link(self):
        self.competing(same_request=False)

    def test_same_command_retry_waits_then_replays_once(self):
        self.competing(same_request=True)
