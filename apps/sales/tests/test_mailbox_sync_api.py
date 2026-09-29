"""Real HTTP guards and safe projection for automatic mailbox sync settings."""
from uuid import uuid4
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.rbac.models import Permission, UserPermissionOverride
from apps.sales.models import SalesMailboxConnection, SalesMailboxSyncState, SalesMailboxSyncItem
from .access_fixtures import grant_sales_actions


@override_settings(
    ROOT_URLCONF='apps.sales.tests.test_mailbox_capture',
    SALES_MAILBOX_SYNC_ENABLED=True, CELERY_TASK_ALWAYS_EAGER=False,
    CELERY_BROKER_URL='memory://',
)
class MailboxSyncAPITests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.owner = users.objects.create_user('sync-api-owner', email='owner@example.test')
        self.other = users.objects.create_user('sync-api-other', email='other@example.test')
        for actor in (self.owner, self.other):
            grant_sales_actions(actor, 'sales_email_intake')
        self.connection = SalesMailboxConnection.objects.create(
            mailbox_address='sales@example.test', auth_mode='application',
            tenant_id='synthetic-tenant', client_id='synthetic-client', created_by=self.owner,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.base = f'/api/v1/sales/mailbox-connections/{self.connection.pk}/'
        self.url = self.base + 'configure-sync/'
        network = patch('requests.request', side_effect=AssertionError('No provider call in configuration or status'))
        self.network = network.start()
        self.addCleanup(network.stop)

    def configure(self, enabled=True):
        return self.client.post(self.url, {'enabled': enabled}, format='json')

    def test_explicit_enable_records_current_actor_and_queues_without_reading_graph(self):
        response = self.configure()
        self.assertEqual(response.status_code, 200, response.data)
        state = SalesMailboxSyncState.objects.get(connection=self.connection)
        self.assertEqual(state.authorized_by, self.owner)
        self.assertEqual(state.identity['mailbox_address'], self.connection.mailbox_address)
        self.assertEqual(state.status, 'queued')
        self.assertIsNotNone(state.next_attempt_at)
        self.assertTrue(response.data['sync']['enabled'])
        self.assertEqual(response.data['sync']['saved_count'], 0)
        self.assertFalse(response.data['sync']['initial_sync_complete'])
        self.assertIn('no-store', response['Cache-Control'])
        self.network.assert_not_called()

    def reviewed_identity(self):
        return {field: getattr(self.connection, field) for field in ('mailbox_address', 'tenant_id', 'client_id')}

    def test_reviewed_identity_can_enable_the_matching_saved_connection(self):
        response = self.client.post(self.url, {
            'enabled': True, 'expected_identity': self.reviewed_identity(),
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['enabled'])
        self.assertEqual(SalesMailboxSyncState.objects.get().authorized_by, self.owner)
        self.network.assert_not_called()

    def test_malformed_reviewed_identity_creates_no_sync_state(self):
        identity = self.reviewed_identity()
        for expected in (None, [], {}, {'mailbox_address': identity['mailbox_address']},
                         {**identity, 'tenant_id': ''}, {**identity, 'client_id': 123},
                         {**identity, 'client_id': 'x' * 101}, {**identity, 'authorized_by': str(self.other.pk)}):
            with self.subTest(expected=expected):
                response = self.client.post(self.url, {'enabled': True, 'expected_identity': expected}, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertFalse(SalesMailboxSyncState.objects.exists())
                self.connection.refresh_from_db()
                self.assertFalse(self.connection.enabled)

    def test_concurrent_setup_identity_change_conflicts_before_enabling(self):
        original = self.reviewed_identity()
        for field, changed in (('mailbox_address', 'changed@example.test'), ('tenant_id', 'changed-tenant'), ('client_id', 'changed-client')):
            with self.subTest(field=field):
                SalesMailboxConnection.objects.filter(pk=self.connection.pk).update(**{field: changed})
                response = self.client.post(self.url, {
                    'enabled': True, 'expected_identity': original,
                }, format='json')
                self.assertEqual(response.status_code, 409, response.data)
                self.assertFalse(SalesMailboxSyncState.objects.exists())
                self.connection.refresh_from_db()
                self.assertFalse(self.connection.enabled)
                self.assertEqual(getattr(self.connection, field), changed)
                self.assertNotIn(changed, str(response.data))
                SalesMailboxConnection.objects.filter(pk=self.connection.pk).update(**{field: original[field]})

    def test_reviewed_identity_conflict_preserves_existing_sync_authority_and_lease(self):
        self.assertEqual(self.configure().status_code, 200)
        state = SalesMailboxSyncState.objects.get(connection=self.connection)
        state.lease_token = uuid4()
        state.lease_expires_at = timezone.now()
        state.save()
        before = SalesMailboxSyncState.objects.values().get(pk=state.pk)
        response = self.client.post(self.url, {
            'enabled': False, 'expected_identity': {**self.reviewed_identity(), 'client_id': 'outdated-client'},
        }, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(SalesMailboxSyncState.objects.values().get(pk=state.pk), before)
        self.connection.refresh_from_db()
        self.assertTrue(self.connection.enabled)

    def test_identity_guard_uses_locked_database_record_instead_of_supplied_instance(self):
        from apps.sales.mailbox_capture import EmailCaptureConflict
        from apps.sales.mailbox_sync import configure_mailbox_sync
        reviewed = self.reviewed_identity()
        SalesMailboxConnection.objects.filter(pk=self.connection.pk).update(client_id='concurrently-changed-client')
        self.assertEqual(self.connection.client_id, reviewed['client_id'])
        with self.assertRaises(EmailCaptureConflict):
            configure_mailbox_sync(
                connection=self.connection, user=self.owner, enabled=True, expected_identity=reviewed,
            )
        self.assertFalse(SalesMailboxSyncState.objects.exists())
        self.connection.refresh_from_db()
        self.assertFalse(self.connection.enabled)
        self.assertEqual(self.connection.client_id, 'concurrently-changed-client')

    def test_pause_fences_a_running_worker_and_resume_preserves_checkpoints(self):
        self.assertEqual(self.configure().status_code, 200)
        state = SalesMailboxSyncState.objects.get(connection=self.connection)
        state.status = 'running'
        state.lease_token = uuid4()
        state.lease_expires_at = timezone.now()
        state.folder_cursor = 'synthetic-private-cursor'
        state.save()
        paused = self.configure(False)
        self.assertEqual(paused.status_code, 200, paused.data)
        state.refresh_from_db()
        self.assertIsNone(state.lease_token)
        self.assertFalse(paused.data['enabled'])
        self.assertEqual(state.folder_cursor, 'synthetic-private-cursor')
        self.assertEqual(self.configure().status_code, 200)
        state.refresh_from_db()
        self.assertEqual(state.folder_cursor, 'synthetic-private-cursor')
        self.assertEqual(state.status, 'queued')

    def test_other_owner_cannot_enable_or_inspect_sync(self):
        self.client.force_authenticate(self.other)
        self.assertEqual(self.configure().status_code, 404)
        self.assertEqual(self.client.get(self.base).status_code, 404)
        self.assertFalse(SalesMailboxSyncState.objects.exists())

    def test_read_create_and_update_grants_are_independent_requirements(self):
        for action in ('read', 'create', 'update'):
            with self.subTest(action=action):
                permission = Permission.objects.get(module__code='sales_email_intake', action=action)
                deny = UserPermissionOverride.objects.create(user_profile=self.owner.rbac_profile, permission=permission, allowed=False)
                self.assertEqual(self.configure().status_code, 403)
                self.assertFalse(SalesMailboxSyncState.objects.exists())
                deny.delete()

    def test_invalid_or_extra_settings_never_schedule_work(self):
        for data in ({}, {'enabled': 'true'}, {'enabled': 1}, {'enabled': True, 'authorized_by': str(self.other.pk)},
                     {'enabled': True, 'folder_cursor': 'https://other.example.test'}):
            self.assertEqual(self.client.post(self.url, data, format='json').status_code, 400)
        self.assertEqual(self.client.post(self.url + '?mailbox=other', {'enabled': True}, format='json').status_code, 400)
        self.assertFalse(SalesMailboxSyncState.objects.exists())

    def test_disabled_deployment_eager_runtime_and_delegated_mailboxes_cannot_enable(self):
        for settings in ({'SALES_MAILBOX_SYNC_ENABLED': False}, {'CELERY_TASK_ALWAYS_EAGER': True}, {'CELERY_BROKER_URL': ''}):
            with override_settings(**settings):
                self.assertEqual(self.configure().status_code, 400)
                self.assertFalse(SalesMailboxSyncState.objects.exists())
        self.connection.auth_mode = 'delegated'
        self.connection.save()
        self.assertEqual(self.configure().status_code, 400)

    def test_sync_projection_counts_saved_work_and_never_exposes_internal_state(self):
        self.assertEqual(self.configure().status_code, 200)
        state = SalesMailboxSyncState.objects.get(connection=self.connection)
        state.folder_cursor = 'sensitive-private-checkpoint'
        state.lease_token = uuid4()
        state.last_error_code = 'sensitive-provider-error-text'
        state.save()
        SalesMailboxSyncItem.objects.create(sync=state, message_id='private-pending-id')
        SalesMailboxSyncItem.objects.create(sync=state, message_id='private-error-id', status='error')
        response = self.client.get(self.base)
        self.assertEqual(response.status_code, 200)
        sync = response.data['sync']
        self.assertEqual(sync['pending_count'], 1)
        self.assertEqual(sync['failed_count'], 1)
        self.assertEqual(sync['saved_count'], 0)
        self.assertNotIn('sensitive-', str(response.data))
        self.assertNotIn(str(state.lease_token), str(response.data))
        self.assertNotIn('private-pending-id', str(response.data))
        self.assertIn('no-store', response['Cache-Control'])

    def test_identity_and_legacy_enable_patch_cannot_bypass_sync_commands(self):
        self.assertEqual(self.configure().status_code, 200)
        for data in ({'tenant_id': 'other'}, {'mailbox_address': 'other@example.test'}, {'enabled': False}):
            self.assertEqual(self.client.patch(self.base, data, format='json').status_code, 403)
        # Application setup edits now require administration; even an authorized
        # administrator cannot bypass the retained sync identity/command guards.
        self.owner.is_superuser = True
        self.owner.save(update_fields=['is_superuser'])
        for data in ({'tenant_id': 'other'}, {'mailbox_address': 'other@example.test'}, {'enabled': False}):
            self.assertEqual(self.client.patch(self.base, data, format='json').status_code, 400)
        self.assertEqual(self.client.delete(self.base).status_code, 409)
        self.assertEqual(self.client.post(self.base + 'connect-outlook/', {}, format='json').status_code, 409)
        self.connection.refresh_from_db()
        self.assertTrue(self.connection.enabled)
        self.assertEqual(self.connection.auth_mode, 'application')
