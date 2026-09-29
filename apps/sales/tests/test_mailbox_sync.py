"""Synthetic sync sources with real permission, SQL queue and capture behavior."""

from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

from billiard.exceptions import SoftTimeLimitExceeded
from celery.app.task import Task
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models.query import QuerySet
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.rbac.models import Permission, UserPermissionOverride
from apps.sales import mailbox_sync as sync
from apps.sales.microsoft_graph import SalesMailboxReadError
from apps.sales.mailbox_sync_graph import SalesMailboxSyncError
from apps.sales.models import SalesEmailIntake, SalesMailboxConnection, SalesMailboxSyncItem
from apps.sales.schedule import mailbox_sync_beat_schedule
from apps.sales.tasks import dispatch_mailbox_sync

from .access_fixtures import grant_sales_actions


SOURCE = {
    'id': 'synthetic-immutable-1', 'subject': 'Synthetic request',
    'sender_name': 'Example contact', 'sender_email': 'contact@example.test',
    'received_at': '2026-09-28T08:00:00Z', 'body_text': 'Synthetic complete source.',
    'has_attachments': False, 'importance': 'normal',
    'conversation_id': 'synthetic-conversation', 'internet_message_id': '<synthetic@example.test>',
}


def page(ids=(), *, next_link=None, delta_link='https://graph.microsoft.com/synthetic-delta'):
    return {'records': [{'id': value, 'removed': False} for value in ids],
            'next_link': next_link, 'delta_link': None if next_link else delta_link}


@override_settings(
    SALES_MAILBOX_SYNC_ENABLED=True, CELERY_TASK_ALWAYS_EAGER=False,
    CELERY_BROKER_URL='memory://', CELERY_RESULT_BACKEND='cache+memory://',
)
class MailboxSyncTests(TestCase):
    def setUp(self):
        delivery = patch.object(Task, 'apply_async', side_effect=AssertionError('Unexpected synthetic test task dispatch'))
        delivery.start()
        self.addCleanup(delivery.stop)
        cache.clear()
        self.addCleanup(cache.clear)
        self.owner = get_user_model().objects.create_user('sync-owner', email='owner@example.test')
        grant_sales_actions(self.owner, 'sales_email_intake')
        self.connection = SalesMailboxConnection.objects.create(
            name='Synthetic sync mailbox', created_by=self.owner, mailbox_address='sales@example.test',
            tenant_id='synthetic-tenant', client_id='synthetic-client', auth_mode='application',
        )
        self.state = sync.configure_mailbox_sync(connection=self.connection, user=self.owner, enabled=True)
        self.graph_patch = patch.object(sync, 'SalesMailboxSyncGraphService')
        self.graph = self.graph_patch.start().return_value
        self.addCleanup(self.graph_patch.stop)
        self.graph.read_folder_changes.return_value = page(['folder-1', 'folder-2'])
        self.graph.read_message_changes.side_effect = lambda folder_id, **kw: page([SOURCE['id']])
        self.graph.get_message_for_capture.side_effect = lambda message_id: {**deepcopy(SOURCE), 'id': message_id}

    def refresh(self):
        self.state.refresh_from_db()
        self.connection.refresh_from_db()

    def run_sync(self, **kwargs):
        result = sync.run_mailbox_sync(self.connection.pk, **kwargs)
        self.refresh()
        return result

    def ready_item(self, message_id=SOURCE['id']):
        now = timezone.now()
        self.state.folder_discovery_completed_at = now
        self.state.folder_discovery_due_at = now + timedelta(hours=1)
        self.state.save()
        return self.state.items.create(message_id=message_id)

    def make_due(self):
        self.state.next_attempt_at = timezone.now() - timedelta(seconds=1)
        self.state.save()

    def test_initial_multifolder_duplicate_is_captured_once_and_reports_completed(self):
        result = self.run_sync()
        self.assertEqual(result['status'], 'up_to_date')
        self.assertEqual(SalesEmailIntake.objects.count(), 1)
        self.assertEqual(self.state.items.get().status, 'captured')
        self.assertEqual(self.state.folders.count(), 2)
        self.assertEqual(self.graph.get_message_for_capture.call_count, 1)
        view = sync.sync_projection(self.connection)
        self.assertEqual(view['saved_count'], 1)
        self.assertEqual(view['pending_count'], 0)
        self.assertTrue(view['initial_sync_complete'])
        self.assertIsNotNone(view['last_successful_sync_at'])
        self.assertNotIn('graph.microsoft.com', str(view))
        self.assertIsNone(self.state.lease_token)

    def test_page_resume_preserves_queue_and_signed_continuation_between_runs(self):
        self.graph.read_folder_changes.return_value = page(['folder-1'])
        next_url = 'https://graph.microsoft.com/next-message-page'
        self.graph.read_message_changes.side_effect = [page(['first'], next_link=next_url), page(['second'])]
        self.run_sync(max_steps=2)
        self.assertEqual(self.state.items.count(), 1)
        self.assertEqual(SalesEmailIntake.objects.count(), 0)
        self.assertNotEqual(self.state.folders.get().cursor, next_url)
        self.run_sync()
        self.assertEqual(SalesEmailIntake.objects.count(), 2)
        self.assertEqual(self.graph.read_message_changes.call_args.kwargs['cursor'], next_url)

    def test_sync_persists_genuine_sent_time_separately_from_received_time(self):
        self.graph.get_message_for_capture.side_effect = lambda message_id: {
            **deepcopy(SOURCE), 'id': message_id, 'sent_at': '2026-09-27T23:58:00-04:00',
        }
        self.run_sync()
        intake = SalesEmailIntake.objects.get()
        self.assertEqual(intake.sent_at.isoformat(), '2026-09-28T03:58:00+00:00')
        self.assertEqual(intake.received_at.isoformat(), '2026-09-28T08:00:00+00:00')
        self.assertEqual(self.state.items.get().status, 'captured')

    def test_invalid_sent_time_stays_in_retryable_ledger_without_a_false_capture(self):
        self.ready_item()
        self.graph.get_message_for_capture.side_effect = lambda message_id: {
            **deepcopy(SOURCE), 'id': message_id, 'sent_at': '2026-09-28T07:00:00',
        }
        self.run_sync()
        item = self.state.items.get()
        self.assertEqual(item.status, 'error')
        self.assertEqual(item.last_error_code, 'unsupported_message')
        self.assertIsNone(item.intake_id)
        self.assertGreater(item.next_attempt_at, timezone.now())
        self.assertFalse(SalesEmailIntake.objects.exists())
        self.assertIsNone(self.state.initial_sync_completed_at)

    def test_delta_replay_does_not_backfill_previously_captured_unknown_sent_time(self):
        self.run_sync()
        snapshot = SalesEmailIntake.objects.values().get()
        self.assertIsNone(snapshot['sent_at'])
        self.make_due()
        self.state.folders.update(next_attempt_at=timezone.now())
        self.graph.get_message_for_capture.side_effect = lambda message_id: {
            **deepcopy(SOURCE), 'id': message_id, 'sent_at': '2026-09-28T07:00:00Z',
        }
        self.run_sync()
        self.assertEqual(SalesEmailIntake.objects.values().get(), snapshot)
        self.assertEqual(self.graph.get_message_for_capture.call_count, 1)

    def test_queue_failure_rolls_back_every_id_and_cursor_in_the_page(self):
        self.graph.read_folder_changes.return_value = page(['folder-1'])
        self.graph.read_message_changes.side_effect = None
        self.graph.read_message_changes.return_value = page(['first', 'second'])
        original = QuerySet.get_or_create

        def fail_second(queryset, **kwargs):
            if queryset.model is SalesMailboxSyncItem and kwargs.get('message_id') == 'second':
                raise RuntimeError('synthetic provider-private-content')
            return original(queryset, **kwargs)

        with patch.object(QuerySet, 'get_or_create', new=fail_second):
            self.run_sync(max_steps=2)
        self.assertEqual(self.state.items.count(), 0)
        folder = self.state.folders.get()
        self.assertEqual(folder.cursor, '')
        self.assertEqual(folder.last_error_code, 'internal_error')
        self.assertNotIn('provider-private', str(sync.sync_projection(self.connection)))

    def test_capture_and_item_completion_rollback_together(self):
        item = self.ready_item()
        original = SalesMailboxSyncItem.save

        def fail_completion(record, *args, **kwargs):
            if record.status == 'captured':
                raise RuntimeError('synthetic completion failure')
            return original(record, *args, **kwargs)

        with patch.object(SalesMailboxSyncItem, 'save', new=fail_completion):
            self.run_sync()
        item.refresh_from_db()
        self.assertEqual(SalesEmailIntake.objects.count(), 0)
        self.assertEqual(item.status, 'error')
        self.assertIsNone(item.intake_id)

    def test_unsupported_item_is_durable_without_blocking_other_messages(self):
        self.ready_item('unsupported')
        self.state.items.create(message_id='valid')

        def message(message_id):
            if message_id == 'unsupported':
                raise SalesMailboxReadError('safe unsupported', code='unsupported_source')
            return {**SOURCE, 'id': message_id}

        self.graph.get_message_for_capture.side_effect = message
        self.run_sync()
        self.assertEqual(SalesEmailIntake.objects.count(), 1)
        failed = self.state.items.get(message_id='unsupported')
        self.assertEqual(failed.status, 'error')
        self.assertEqual(failed.last_error_code, 'unsupported_message')
        self.assertGreater(failed.next_attempt_at, timezone.now())
        self.assertEqual(self.state.status, 'retrying')
        self.assertIsNone(self.state.initial_sync_completed_at)
        self.assertIsNone(self.state.last_successful_sync_at)

    def test_skips_nonincoming_and_records_missing_without_creating_sources(self):
        self.ready_item('outgoing')
        self.state.items.create(message_id='missing')

        def message(message_id):
            if message_id == 'outgoing':
                raise SalesMailboxReadError('safe skip', status_code=400, code='not_incoming')
            raise SalesMailboxReadError('safe missing', status_code=404, code='source_unavailable')

        self.graph.get_message_for_capture.side_effect = message
        self.run_sync()
        self.assertEqual(self.state.items.get(message_id='outgoing').status, 'skipped')
        self.assertEqual(self.state.items.get(message_id='missing').status, 'unavailable')
        self.assertFalse(SalesEmailIntake.objects.exists())
        self.assertEqual(self.state.status, 'retrying')
        self.assertIsNone(self.state.initial_sync_completed_at)
        view = sync.sync_projection(self.connection)
        self.assertEqual(view['failed_count'], 1)
        self.assertEqual(view['error_code'], 'source_unavailable')

    def test_soft_task_limit_leaves_unfinished_capture_pending_and_releases_lease(self):
        self.ready_item()
        self.graph.get_message_for_capture.side_effect = SoftTimeLimitExceeded()
        self.run_sync()
        self.assertEqual(self.state.items.get().status, 'pending')
        self.assertEqual(self.state.last_error_code, '')
        self.assertIsNone(self.state.lease_token)
        self.assertEqual(self.state.status, 'queued')
        self.assertFalse(SalesEmailIntake.objects.exists())

    def test_existing_connection_without_authorization_is_not_configured(self):
        mailbox = SalesMailboxConnection.objects.create(
            name='Legacy enable flag', mailbox_address='legacy@example.test',
            auth_mode='application', enabled=True, created_by=self.owner,
        )
        self.assertEqual(sync.sync_projection(mailbox)['status'], 'not_configured')
        self.assertFalse(sync.sync_projection(mailbox)['enabled'])
        with override_settings(SALES_MAILBOX_SYNC_ENABLED=False):
            self.assertEqual(sync.sync_projection(mailbox)['status'], 'not_configured')

    def test_later_delta_requeues_skipped_missing_but_preserves_error_backoff(self):
        self.graph.read_folder_changes.return_value = page(['folder-1'])
        self.graph.read_message_changes.side_effect = None
        self.graph.read_message_changes.return_value = page(['skipped', 'unavailable', 'error'])
        for status in ('skipped', 'unavailable', 'error'):
            self.state.items.create(message_id=status, status=status, next_attempt_at=timezone.now() + timedelta(days=1))
        self.run_sync()
        self.assertEqual(SalesEmailIntake.objects.count(), 2)
        self.assertEqual(self.state.items.get(message_id='error').status, 'error')
        self.state.items.filter(message_id='error').update(next_attempt_at=timezone.now())
        self.make_due()
        self.run_sync()
        self.assertEqual(SalesEmailIntake.objects.count(), 3)
        self.assertEqual(self.state.items.filter(status='captured').count(), 3)

    def test_tombstone_does_not_delete_retained_source_and_move_deduplicates(self):
        self.run_sync()
        snapshot = SalesEmailIntake.objects.values().get()
        self.make_due()
        self.state.folders.update(next_attempt_at=timezone.now())
        self.graph.read_message_changes.side_effect = [
            {'records': [{'id': SOURCE['id'], 'removed': True}], 'next_link': None, 'delta_link': 'https://graph.microsoft.com/delta'},
            page([SOURCE['id']]),
        ]
        self.run_sync()
        self.assertEqual(SalesEmailIntake.objects.values().get(), snapshot)
        self.assertEqual(self.graph.get_message_for_capture.call_count, 1)

    def test_retry_after_is_persisted_without_sleep_or_early_redispatch(self):
        item = self.ready_item()
        error = SalesMailboxSyncError(status_code=429, code='throttled', retry_after=420)
        self.graph.get_message_for_capture.side_effect = error
        before = timezone.now()
        self.run_sync()
        item.refresh_from_db()
        self.assertGreaterEqual(self.state.next_attempt_at, before + timedelta(seconds=420))
        self.assertGreaterEqual(item.next_attempt_at, before + timedelta(seconds=420))
        self.assertEqual(sync.due_mailbox_ids(), [])
        self.assertEqual(self.run_sync()['status'], 'not_claimed')

    def test_provider_authorization_failure_blocks_until_explicit_enable(self):
        self.ready_item()
        self.graph.get_message_for_capture.side_effect = SalesMailboxReadError('safe denied', status_code=403)
        self.run_sync()
        self.assertEqual(self.state.status, 'blocked')
        self.assertEqual(self.state.last_error_code, 'authorization_required')
        self.assertIsNone(self.state.next_attempt_at)
        self.assertEqual(sync.due_mailbox_ids(), [])
        sync.configure_mailbox_sync(connection=self.connection, user=self.owner, enabled=True)
        self.refresh()
        self.assertEqual(self.state.status, 'queued')

    def test_excessive_provider_delay_blocks_instead_of_retrying_early(self):
        self.ready_item()
        self.graph.get_message_for_capture.side_effect = SalesMailboxSyncError(
            status_code=429, code='throttled', retry_after_exceeds_limit=True,
        )
        self.run_sync()
        self.assertEqual(self.state.status, 'blocked')
        self.assertIsNone(self.state.next_attempt_at)
        self.assertEqual(self.state.items.get().status, 'pending')

    def test_discovery_failure_does_not_claim_completed_success(self):
        self.graph.read_folder_changes.side_effect = SalesMailboxSyncError(code='invalid_response')
        self.run_sync()
        self.assertEqual(self.state.status, 'retrying')
        self.assertIsNone(self.state.initial_sync_completed_at)
        self.assertIsNone(self.state.last_successful_sync_at)
        self.assertEqual(sync.sync_projection(self.connection)['error_code'], 'invalid_response')

    def test_unresolved_folder_error_prevents_initial_completion_even_with_old_success(self):
        now = timezone.now()
        self.state.folder_discovery_completed_at = now
        self.state.folder_discovery_due_at = now + timedelta(hours=1)
        self.state.save()
        self.state.folders.create(
            folder_id='folder-1', last_synced_at=now, next_attempt_at=now + timedelta(minutes=5),
            last_error_code='provider_unavailable',
        )
        self.run_sync()
        self.assertEqual(self.state.status, 'retrying')
        self.assertIsNone(self.state.initial_sync_completed_at)
        self.assertIsNone(self.state.last_successful_sync_at)

    def test_later_folder_discovery_imports_new_folder_without_recapturing_old_message(self):
        self.run_sync()
        self.make_due()
        self.state.folder_discovery_due_at = timezone.now()
        self.state.save()
        self.graph.read_folder_changes.return_value = page(['folder-3'])
        self.graph.read_message_changes.side_effect = lambda folder_id, **kw: page(['new-message'])
        self.run_sync()
        self.assertEqual(self.state.folders.count(), 3)
        self.assertEqual(SalesEmailIntake.objects.count(), 2)
        self.assertEqual(self.graph.get_message_for_capture.call_count, 2)

    def test_revoke_actor_before_job_makes_no_network_requests(self):
        self.owner.is_active = False
        self.owner.save(update_fields=['is_active'])
        self.run_sync()
        self.assertEqual(self.state.status, 'blocked')
        self.graph.read_folder_changes.assert_not_called()

    def test_permission_revoked_during_fetch_blocks_capture_commit(self):
        self.ready_item()

        def revoke(_message_id):
            permission = Permission.objects.get(module__code='sales_email_intake', action='create', is_active=True)
            UserPermissionOverride.objects.create(user_profile=self.owner.rbac_profile, permission=permission, allowed=False)
            return deepcopy(SOURCE)

        self.graph.get_message_for_capture.side_effect = revoke
        self.run_sync()
        self.assertEqual(self.state.status, 'blocked')
        self.assertEqual(self.state.items.get().status, 'pending')
        self.assertFalse(SalesEmailIntake.objects.exists())

    def test_changed_owner_during_fetch_blocks_capture_commit(self):
        self.ready_item()
        other = get_user_model().objects.create_user('replacement-owner', email='replacement@example.test')

        def reassign(_message_id):
            SalesMailboxConnection.objects.filter(pk=self.connection.pk).update(created_by=other)
            return deepcopy(SOURCE)

        self.graph.get_message_for_capture.side_effect = reassign
        self.run_sync()
        self.assertEqual(self.state.status, 'blocked')
        self.assertFalse(SalesEmailIntake.objects.exists())

    def test_missing_authorizing_actor_is_dispatched_only_to_mark_blocked(self):
        self.state.authorized_by = None
        self.state.save()
        self.assertEqual(sync.due_mailbox_ids(), [self.connection.pk])
        self.run_sync()
        self.assertEqual(self.state.status, 'blocked')
        self.assertEqual(sync.sync_projection(self.connection)['status'], 'blocked')
        self.graph.read_folder_changes.assert_not_called()

    def test_pause_during_network_fences_capture_and_preserves_new_state(self):
        self.ready_item()

        def pause(_message_id):
            sync.configure_mailbox_sync(connection=self.connection, user=self.owner, enabled=False)
            return deepcopy(SOURCE)

        self.graph.get_message_for_capture.side_effect = pause
        self.assertEqual(self.run_sync()['status'], 'stopped')
        self.assertEqual(self.state.status, 'paused')
        self.assertIsNone(self.state.lease_token)
        self.assertFalse(SalesEmailIntake.objects.exists())

    def test_late_failure_after_pause_cannot_overwrite_pause(self):
        self.ready_item()

        def pause_and_fail(_message_id):
            sync.configure_mailbox_sync(connection=self.connection, user=self.owner, enabled=False)
            raise SalesMailboxSyncError(status_code=503, code='provider_unavailable')

        self.graph.get_message_for_capture.side_effect = pause_and_fail
        self.assertEqual(self.run_sync()['status'], 'stopped')
        self.assertEqual(self.state.status, 'paused')
        self.assertEqual(self.state.items.get().status, 'pending')
        self.assertEqual(self.state.last_error_code, '')

    def test_reclaimed_lease_fences_old_worker_and_cannot_clear_new_owner(self):
        self.ready_item()
        new_tokens = []

        def reclaim(_message_id):
            self.state.refresh_from_db()
            self.state.lease_expires_at = timezone.now() - timedelta(seconds=1)
            self.state.save(update_fields=['lease_expires_at'])
            new_tokens.append(sync._claim(self.connection.pk))
            return deepcopy(SOURCE)

        self.graph.get_message_for_capture.side_effect = reclaim
        self.assertEqual(self.run_sync()['status'], 'stopped')
        self.assertIsNotNone(new_tokens[0])
        self.assertEqual(self.state.lease_token, new_tokens[0])
        self.assertFalse(SalesEmailIntake.objects.exists())

    def test_live_lease_prevents_duplicate_worker_fetches(self):
        token = sync._claim(self.connection.pk)
        self.assertIsNotNone(token)
        self.assertEqual(self.run_sync()['status'], 'not_claimed')
        self.graph.read_folder_changes.assert_not_called()

    def test_tampered_or_cross_mailbox_checkpoint_blocks_before_provider_read(self):
        self.state.folder_cursor = sync._checkpoint(self.state, 'https://graph.microsoft.com/private-cursor') + 'tampered'
        self.state.save()
        self.run_sync()
        self.assertEqual(self.state.status, 'blocked')
        self.assertEqual(self.state.last_error_code, 'invalid_checkpoint')
        self.graph.read_folder_changes.assert_not_called()

    def test_changed_connection_identity_blocks_old_queue(self):
        self.connection.tenant_id = 'changed-synthetic-tenant'
        self.connection.save(update_fields=['tenant_id'])
        self.run_sync()
        self.assertEqual(self.state.last_error_code, 'configuration_changed')
        self.graph.read_folder_changes.assert_not_called()

    def test_valid_signed_cursor_cannot_be_replayed_for_a_different_folder(self):
        self.state.folder_discovery_completed_at = timezone.now()
        self.state.folder_discovery_due_at = timezone.now() + timedelta(hours=1)
        self.state.save()
        self.state.folders.create(folder_id='folder-2', cursor=sync._checkpoint(
            self.state, 'https://graph.microsoft.com/private-cursor', folder_id='folder-1',
        ))
        self.run_sync()
        self.assertEqual(self.state.status, 'blocked')
        self.graph.read_message_changes.assert_not_called()

    def test_bounded_run_keeps_backfill_incomplete_until_queue_drains(self):
        self.graph.read_folder_changes.return_value = page(['folder-1'])
        self.graph.read_message_changes.side_effect = None
        self.graph.read_message_changes.return_value = page([f'synthetic-{number}' for number in range(35)])
        self.run_sync(max_steps=3)
        self.assertEqual(SalesEmailIntake.objects.count(), 1)
        self.assertEqual(self.state.items.filter(status='pending').count(), 34)
        self.assertEqual(self.state.status, 'queued')
        self.assertIsNone(self.state.initial_sync_completed_at)
        self.assertIsNone(self.state.last_successful_sync_at)

    def test_expired_folder_checkpoint_resets_only_affected_folder(self):
        self.run_sync()
        snapshot = SalesEmailIntake.objects.values().get()
        folder = self.state.folders.first()
        self.make_due()
        folder.next_attempt_at = timezone.now()
        folder.save()
        self.graph.read_message_changes.side_effect = SalesMailboxSyncError(status_code=410, code='checkpoint_expired')
        self.run_sync()
        folder.refresh_from_db()
        self.assertEqual(folder.cursor, '')
        self.assertIsNone(folder.last_synced_at)
        self.assertEqual(self.state.folders.exclude(pk=folder.pk).filter(cursor='').count(), 0)
        self.assertEqual(SalesEmailIntake.objects.values().get(), snapshot)

    def test_dispatch_failure_leaves_due_sql_work_for_next_tick(self):
        with patch('apps.sales.tasks.sync_mailbox.delay', side_effect=RuntimeError('synthetic broker unavailable')):
            self.assertEqual(dispatch_mailbox_sync(), {'dispatched': 0})
        self.assertEqual(sync.due_mailbox_ids(), [self.connection.pk])
        with patch('apps.sales.tasks.sync_mailbox.delay') as publish:
            self.assertEqual(dispatch_mailbox_sync(), {'dispatched': 1})
            publish.assert_called_once_with(str(self.connection.pk))

    def test_environment_gate_and_eager_mode_cannot_enable_or_dispatch(self):
        for options in ({'SALES_MAILBOX_SYNC_ENABLED': False}, {'CELERY_TASK_ALWAYS_EAGER': True}, {'CELERY_BROKER_URL': ''}):
            with self.subTest(options=options), override_settings(**options):
                with self.assertRaises(ValidationError):
                    sync.configure_mailbox_sync(connection=self.connection, user=self.owner, enabled=True)
                self.assertEqual(sync.due_mailbox_ids(), [])
                self.assertEqual(self.run_sync()['status'], 'not_claimed')
        with override_settings(SALES_MAILBOX_SYNC_ENABLED=False):
            sync.configure_mailbox_sync(connection=self.connection, user=self.owner, enabled=False)

    def test_enable_requires_persisted_identity_instead_of_runtime_fallback(self):
        for field in ('tenant_id', 'client_id'):
            with self.subTest(field=field):
                original = getattr(self.connection, field)
                setattr(self.connection, field, '  ')
                self.connection.save(update_fields=[field])
                with self.assertRaises(ValidationError):
                    sync.configure_mailbox_sync(connection=self.connection, user=self.owner, enabled=True)
                setattr(self.connection, field, original)
                self.connection.save(update_fields=[field])

    def test_explicit_actor_read_create_edit_and_owner_scope_are_required(self):
        other = get_user_model().objects.create_user('sync-other', email='other@example.test')
        grant_sales_actions(other, 'sales_email_intake')
        from django.http import Http404
        with self.assertRaises(Http404):
            sync.configure_mailbox_sync(connection=self.connection, user=other, enabled=True)
        permission = Permission.objects.get(module__code='sales_email_intake', action='update', is_active=True)
        UserPermissionOverride.objects.create(user_profile=self.owner.rbac_profile, permission=permission, allowed=False)
        with self.assertRaises(PermissionDenied):
            sync.configure_mailbox_sync(connection=self.connection, user=self.owner, enabled=True)

    def test_schedule_is_opt_in_and_bounded(self):
        self.assertEqual(mailbox_sync_beat_schedule(), {})
        entry = mailbox_sync_beat_schedule(enabled=True, interval_seconds=1)['sales-mailbox-sync']
        self.assertEqual(entry['schedule'], 60)
        self.assertEqual(entry['task'], 'apps.sales.tasks.dispatch_mailbox_sync')
