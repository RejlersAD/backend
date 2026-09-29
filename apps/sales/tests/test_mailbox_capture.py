"""Scoped durable capture through real guarded routes and synthetic Graph reads."""

from copy import deepcopy
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

import requests
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.rbac.models import Permission, UserPermissionOverride, UserProfile
from apps.rbac.route_guard import secure_module_endpoints
from apps.sales.intake_views import SalesEmailIntakeViewSet, sales_email_intake
from apps.sales.microsoft_graph import SalesMicrosoftGraphService
from apps.sales.models import Client, Deal, OpportunityAuditEvent, SalesEmailIntake, SalesMailboxConnection
from apps.sales.views import SalesMailboxConnectionViewSet

from .access_fixtures import grant_sales_actions
from .test_mailbox_browsing import graph_response


router = DefaultRouter()
router.register('mailbox-connections', SalesMailboxConnectionViewSet, basename='capture-mailboxes')
router.register('email-intakes', SalesEmailIntakeViewSet, basename='captured-intakes')
urlpatterns = [
    path('api/v1/sales/email-intake/', sales_email_intake),
    path('api/v1/sales/', include(router.urls)),
]
secure_module_endpoints(urlpatterns)

CAPTURE_MESSAGE = {
    'id': 'AAMk-Capture/Immutable+=',
    'internetMessageId': '<synthetic-capture@example.test>',
    'conversationId': 'Synthetic-Conversation-41',
    'subject': 'RFQ: Ventilation engineering services',
    'from': {'emailAddress': {'name': 'Synthetic Customer', 'address': 'customer@example.test'}},
    'sender': {'emailAddress': {'name': 'Synthetic Customer', 'address': 'customer@example.test'}},
    'toRecipients': [{'emailAddress': {'name': 'Sales', 'address': 'sales@example.test'}}],
    'ccRecipients': [],
    'receivedDateTime': '2026-09-28T08:01:00Z',
    'sentDateTime': '2026-09-28T08:00:00Z',
    'bodyPreview': 'A deliberately short preview.',
    'body': {'contentType': 'html', 'content': '<p>Customer: Sample Estates</p><p>Full source beyond the short preview.</p>'},
    'hasAttachments': True, 'isRead': False, 'isDraft': False, 'importance': 'normal',
    'privateProviderField': 'not-part-of-the-captured-record',
}


@override_settings(
    ROOT_URLCONF=__name__,
    SALES_MICROSOFT_GRAPH_BASE_URL='https://graph.microsoft.com/v1.0',
    SALES_EMAIL_INTAKE_WEBHOOK_KEY='synthetic-capture-webhook-key',
)
class MailboxCaptureAPITests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        users = get_user_model()
        self.owner = users.objects.create_user('capture-owner', email='owner@example.test')
        self.other = users.objects.create_user('capture-other', email='other@example.test')
        self.admin = users.objects.create_superuser('capture-admin', email='admin@example.test', password='synthetic-only')
        for user in (self.owner, self.other, self.admin):
            grant_sales_actions(user, 'sales_email_intake')
        self.outsider = users.objects.create_user('capture-outsider', email='outsider@example.test')
        UserProfile.objects.get_or_create(user=self.outsider, defaults={'organization': self.owner.rbac_profile.organization})
        self.connection = self.make_connection(self.owner, 'sales@example.test')
        self.endpoint = f'/api/v1/sales/mailbox-connections/{self.connection.pk}/capture-message/'
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.message = deepcopy(CAPTURE_MESSAGE)
        token = patch.object(SalesMicrosoftGraphService, 'token', return_value='synthetic-capture-token')
        self.token = token.start()
        self.addCleanup(token.stop)
        transport = patch('apps.sales.microsoft_graph.requests.request')
        self.network = transport.start()
        self.addCleanup(transport.stop)
        self.network.side_effect = lambda *_args, **_kwargs: graph_response(self.message)

    @staticmethod
    def make_connection(owner, address):
        return SalesMailboxConnection.objects.create(
            name='Synthetic capture mailbox', tenant_id='synthetic-capture-tenant',
            client_id='synthetic-capture-client', mailbox_address=address,
            auth_mode='application', enabled=False, created_by=owner,
        )

    def saved(self, connection=None, **overrides):
        values = {
            'source_message_id': 'synthetic-saved-message', 'subject': 'Synthetic saved enquiry',
            'sender_email': 'customer@example.test', 'received_at': '2026-09-20T08:00:00Z',
            'body_preview': 'Synthetic retained source text.',
        }
        if connection is not None:
            values.update(
                mailbox_connection=connection, source_mailbox_address=connection.mailbox_address,
                source_tenant_id=connection.tenant_id, conversation_id='saved-synthetic-conversation',
                captured_by=connection.created_by,
            )
        values.update(overrides)
        return SalesEmailIntake.objects.create(**values)

    def post(self, data=None, endpoint=None):
        return self.client.post(endpoint or self.endpoint, {'message_id': self.message['id']} if data is None else data, format='json')

    def assert_private(self, response):
        self.assertIn('private', response['Cache-Control'])
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['Pragma'], 'no-cache')
        self.assertIn('Authorization', response['Vary'])

    def assert_no_business_creation(self):
        self.assertEqual(Deal.objects.count(), 0)
        self.assertEqual(Client.objects.count(), 0)
        self.assertEqual(OpportunityAuditEvent.objects.count(), 0)

    def deny(self, user, action):
        permission = Permission.objects.get(module__code='sales_email_intake', action=action, is_active=True)
        return UserPermissionOverride.objects.create(user_profile=user.rbac_profile, permission=permission, allowed=False)

    def test_capture_persists_one_full_source_snapshot_and_only_reads_the_selected_graph_message(self):
        before = SalesMailboxConnection.objects.values().get(pk=self.connection.pk)
        response = self.post()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data['created'])
        intake = SalesEmailIntake.objects.get()
        self.assertEqual(str(intake.pk), str(response.data['intake']['id']))
        self.assertEqual(intake.mailbox_connection, self.connection)
        self.assertEqual(intake.source_mailbox_address, 'sales@example.test')
        self.assertEqual(intake.source_tenant_id, self.connection.tenant_id)
        self.assertEqual(intake.source_message_id, CAPTURE_MESSAGE['id'])
        self.assertEqual(intake.internet_message_id, CAPTURE_MESSAGE['internetMessageId'])
        self.assertEqual(intake.conversation_id, CAPTURE_MESSAGE['conversationId'])
        self.assertEqual(intake.captured_by, self.owner)
        self.assertEqual(intake.status, 'received')
        self.assertIsNone(intake.reviewed_by_id)
        self.assertIsNone(intake.opportunity_id)
        self.assertIsNotNone(intake.received_at.utcoffset())
        self.assertEqual(intake.received_at.isoformat(), '2026-09-28T08:01:00+00:00')
        self.assertEqual(intake.sent_at.isoformat(), '2026-09-28T08:00:00+00:00')
        self.assertIn('Full source beyond the short preview.', intake.body_preview)
        self.assertNotIn('<p>', intake.body_preview)
        self.assertNotIn('not-part-of-the-captured-record', str(response.data))
        self.assertEqual(before, SalesMailboxConnection.objects.values().get(pk=self.connection.pk))
        self.assertEqual(self.network.call_count, 1)
        call = self.network.call_args
        self.assertEqual(call.args[0], 'GET')
        self.assertIn('/users/sales%40example.test/messages/AAMk-Capture%2FImmutable%2B%3D', call.args[1])
        self.assertIn('IdType="ImmutableId"', call.kwargs['headers']['Prefer'])
        self.assertFalse(call.kwargs['allow_redirects'])
        self.assertTrue({'body', 'conversationId', 'internetMessageId'}.issubset(set(call.kwargs['params']['$select'].split(','))))
        self.assertNotIn('bccRecipients', call.kwargs['params']['$select'])
        self.assert_private(response)
        self.assert_no_business_creation()

    def test_retry_refetches_but_never_overwrites_saved_source_review_state_or_capturing_actor(self):
        self.assertEqual(self.post().status_code, 201)
        intake = SalesEmailIntake.objects.get()
        review = self.client.post(f'/api/v1/sales/email-intakes/{intake.pk}/reject/', {'reason': 'Retained reviewer decision'}, format='json')
        self.assertEqual(review.status_code, 200, review.data)
        before = SalesEmailIntake.objects.values().get(pk=intake.pk)
        self.message.update(
            subject='Changed Microsoft subject', conversationId='Changed-conversation',
            sentDateTime='2026-09-27T17:00:00Z',
        )
        self.message['body']['content'] = '<p>Changed later source text.</p>'
        self.client.force_authenticate(self.admin)
        retry = self.post()
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertFalse(retry.data['created'])
        self.assertEqual(str(retry.data['intake']['id']), str(intake.pk))
        self.assertEqual(SalesEmailIntake.objects.values().get(pk=intake.pk), before)
        self.assertEqual(self.network.call_count, 2)
        self.assert_private(retry)
        self.assert_no_business_creation()

    def test_missing_sent_time_stays_unknown_and_retry_does_not_backfill_it(self):
        self.message.pop('sentDateTime')
        self.assertEqual(self.post().status_code, 201)
        intake = SalesEmailIntake.objects.get()
        self.assertIsNone(intake.sent_at)
        before = SalesEmailIntake.objects.values().get(pk=intake.pk)
        self.message['sentDateTime'] = '2026-09-28T08:00:00Z'
        retry = self.post()
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertFalse(retry.data['created'])
        self.assertEqual(SalesEmailIntake.objects.values().get(pk=intake.pk), before)

    def test_invalid_or_naive_sent_time_rejects_capture_without_source_or_business_writes(self):
        for sent_at in ('', '2026-09-28', '2026-09-28T08:00:00', '2026-02-30T08:00:00Z',
                        'private-invalid-sent-time', 1727500000, True, {'private': 'invalid-timestamp'}):
            with self.subTest(value_type=type(sent_at).__name__):
                self.message['sentDateTime'] = sent_at
                response = self.post()
                self.assertEqual(response.status_code, 502, response.data)
                self.assertNotIn('private-invalid-sent-time', str(response.data))
                self.assertFalse(SalesEmailIntake.objects.exists())
                self.assert_no_business_creation()
                self.assert_private(response)

    def test_retry_does_not_disclose_retained_snapshot_when_current_graph_read_fails(self):
        self.assertEqual(self.post().status_code, 201)
        before = list(SalesEmailIntake.objects.values())
        self.network.side_effect = requests.Timeout('private-provider-timeout')
        response = self.post()
        self.assertEqual(response.status_code, 503, response.data)
        self.assertNotIn('intake', response.data)
        self.assertNotIn('private-provider-timeout', str(response.data))
        self.assertEqual(list(SalesEmailIntake.objects.values()), before)
        self.assert_private(response)

    def test_same_immutable_id_in_different_mailboxes_creates_separate_scoped_records(self):
        first = self.post()
        second_connection = self.make_connection(self.owner, 'projects@example.test')
        self.message['toRecipients'][0]['emailAddress']['address'] = 'projects@example.test'
        second = self.post(endpoint=f'/api/v1/sales/mailbox-connections/{second_connection.pk}/capture-message/')
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201, second.data)
        self.assertNotEqual(first.data['intake']['id'], second.data['intake']['id'])
        self.assertEqual(SalesEmailIntake.objects.count(), 2)
        self.assertSetEqual(set(SalesEmailIntake.objects.values_list('source_mailbox_address', flat=True)), {'sales@example.test', 'projects@example.test'})

    def test_message_identity_remains_case_sensitive_and_internet_id_is_not_a_deduplication_key(self):
        self.assertEqual(self.post().status_code, 201)
        self.message['id'] = self.message['id'].lower()
        second = self.post()
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(SalesEmailIntake.objects.count(), 2)
        self.assertEqual(SalesEmailIntake.objects.values('internet_message_id').distinct().count(), 1)

    def test_invalid_input_and_unknown_fields_are_rejected_before_graph(self):
        payloads = [
            {}, [], {'message_id': ''}, {'message_id': None}, {'message_id': 17},
            {'message_id': 'x' * 513}, {'message_id': '../another-mailbox'},
            {'message_id': CAPTURE_MESSAGE['id'], 'subject': 'Forged source'},
            {'message_id': CAPTURE_MESSAGE['id'], 'status': 'converted'},
            {'message_id': CAPTURE_MESSAGE['id'], 'mailbox_connection': str(self.connection.pk)},
        ]
        for payload in payloads:
            with self.subTest(payload_type=type(payload).__name__):
                response = self.post(payload)
                self.assertEqual(response.status_code, 400, response.data)
                self.assert_private(response)
        self.assertEqual(self.post(endpoint=self.endpoint + '?mailbox=forged').status_code, 400)
        self.network.assert_not_called()
        self.assertEqual(SalesEmailIntake.objects.count(), 0)

    def test_missing_grants_other_owner_and_anonymous_capture_never_read_graph(self):
        for actor, statuses in ((self.other, {404}), (self.outsider, {403}), (None, {401, 403})):
            with self.subTest(actor=actor and actor.username):
                self.client.force_authenticate(actor)
                response = self.post()
                self.assertIn(response.status_code, statuses, response.data)
                self.assert_private(response)
        self.network.assert_not_called()
        self.assertEqual(SalesEmailIntake.objects.count(), 0)

    def test_both_read_and_create_are_required_even_for_an_administrator(self):
        for actor in (self.owner, self.admin):
            for action in ('read', 'create'):
                with self.subTest(actor=actor.username, action=action):
                    denial = self.deny(actor, action)
                    cache.clear()
                    self.client.force_authenticate(actor)
                    response = self.post()
                    self.assertEqual(response.status_code, 403, response.data)
                    self.assert_private(response)
                    denial.delete()
                    cache.clear()
        self.network.assert_not_called()
        self.assertEqual(SalesEmailIntake.objects.count(), 0)

    def test_administrator_can_capture_a_system_owned_application_connection(self):
        self.connection.created_by = None
        self.connection.save(update_fields=['created_by'])
        self.client.force_authenticate(self.admin)
        response = self.post()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(SalesEmailIntake.objects.get().captured_by, self.admin)

    def test_only_evidenced_incoming_non_draft_messages_can_be_captured(self):
        variants = [
            {'isDraft': True},
            {'from': {'emailAddress': {'address': 'sales@example.test'}}},
            {'sender': {'emailAddress': {'address': 'sales@example.test'}}},
            {'toRecipients': [], 'ccRecipients': []},
            {'toRecipients': [{'emailAddress': {'address': 'unproven-alias@example.test'}}]},
        ]
        for fields in variants:
            with self.subTest(fields=tuple(fields)):
                self.message = {**deepcopy(CAPTURE_MESSAGE), **fields}
                response = self.post()
                self.assertEqual(response.status_code, 400, response.data)
                self.assert_private(response)
        self.assertEqual(SalesEmailIntake.objects.count(), 0)
        self.assertTrue(all(call.args[0] == 'GET' for call in self.network.call_args_list))

    def test_malformed_or_oversized_source_never_creates_a_partial_or_truncated_snapshot(self):
        variants = [
            {'id': 'different-message'}, {'conversationId': ''}, {'conversationId': None},
            {'conversationId': 'x' * 513}, {'internetMessageId': 'x' * 513},
            {'receivedDateTime': None}, {'receivedDateTime': 'not-a-date'},
            {'receivedDateTime': '2026-09-28T08:00:00'}, {'subject': 'x' * 501},
            {'from': {'emailAddress': {'name': 'x' * 256, 'address': 'customer@example.test'}}},
            {'body': None}, {'body': {'contentType': 'html', 'content': 12}},
            {'body': {'contentType': 'text', 'content': 'x' * 1_000_001}},
        ]
        for fields in variants:
            with self.subTest(fields=tuple(fields)):
                self.message = {**deepcopy(CAPTURE_MESSAGE), **fields}
                response = self.post({'message_id': CAPTURE_MESSAGE['id']})
                self.assertIn(response.status_code, {400, 502}, response.data)
                self.assert_private(response)
                self.assertEqual(SalesEmailIntake.objects.count(), 0)
        self.assert_no_business_creation()

    def test_empty_subject_and_optional_internet_id_are_retained_without_inventing_source_values(self):
        self.message['subject'] = ''
        self.message.pop('internetMessageId')
        response = self.post()
        self.assertEqual(response.status_code, 201, response.data)
        intake = SalesEmailIntake.objects.get()
        self.assertEqual(intake.subject, '')
        self.assertEqual(intake.internet_message_id, '')

    def test_upstream_failures_are_safe_private_and_never_write_source_or_business_records(self):
        for status_code in (403, 404, 429, 500):
            with self.subTest(status=status_code):
                self.network.side_effect = None
                self.network.return_value = graph_response({'error': {'message': 'private-provider-payload'}}, status=status_code)
                response = self.post()
                self.assertGreaterEqual(response.status_code, 400)
                self.assertLess(response.status_code, 600)
                self.assertNotIn('private-provider-payload', str(response.data))
                self.assert_private(response)
        self.network.side_effect = requests.Timeout('private-transport-details')
        response = self.post()
        self.assertEqual(response.status_code, 503, response.data)
        self.assertNotIn('private-transport-details', str(response.data))
        self.assertEqual(SalesEmailIntake.objects.count(), 0)
        self.assert_no_business_creation()

    def test_connection_repoint_during_graph_read_conflicts_before_capture(self):
        for field, changed in (
            ('mailbox_address', 'repointed@example.test'), ('tenant_id', 'changed-tenant'),
            ('client_id', 'changed-client'), ('auth_mode', 'delegated'),
        ):
            with self.subTest(field=field):
                original = getattr(self.connection, field)
                def changed_source(*_args, **_kwargs):
                    SalesMailboxConnection.objects.filter(pk=self.connection.pk).update(**{field: changed})
                    return graph_response(self.message)
                self.network.side_effect = changed_source
                response = self.post()
                self.assertEqual(response.status_code, 409, response.data)
                self.assert_private(response)
                self.assertEqual(SalesEmailIntake.objects.count(), 0)
                SalesMailboxConnection.objects.filter(pk=self.connection.pk).update(**{field: original})

    def test_connection_ownership_loss_during_graph_read_is_rechecked_before_capture(self):
        def lost_scope(*_args, **_kwargs):
            SalesMailboxConnection.objects.filter(pk=self.connection.pk).update(created_by=self.other)
            return graph_response(self.message)
        self.network.side_effect = lost_scope
        response = self.post()
        self.assertEqual(response.status_code, 404, response.data)
        self.assertEqual(SalesEmailIntake.objects.count(), 0)
        self.assert_private(response)

    def test_permission_revoked_during_graph_read_is_rechecked_before_capture(self):
        def revoked(*_args, **_kwargs):
            self.deny(self.owner, 'create')
            cache.clear()
            return graph_response(self.message)
        self.network.side_effect = revoked
        response = self.post()
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(SalesEmailIntake.objects.count(), 0)
        self.assert_private(response)

    def test_storage_failure_rolls_back_the_source_and_returns_a_safe_retryable_error(self):
        original_save = SalesEmailIntake.save
        def failed_save(instance, *args, **kwargs):
            original_save(instance, *args, **kwargs)
            raise RuntimeError('private-storage-diagnostic')
        with patch.object(SalesEmailIntake, 'save', autospec=True, side_effect=failed_save):
            response = self.post()
        self.assertEqual(response.status_code, 503, response.data)
        self.assertNotIn('private-storage-diagnostic', str(response.data))
        self.assertEqual(SalesEmailIntake.objects.count(), 0)
        self.assert_private(response)
        self.assert_no_business_creation()

    def test_saved_list_search_detail_and_review_actions_respect_current_mailbox_scope(self):
        own = self.saved(self.connection, source_message_id='own-scoped-message')
        foreign_connection = self.make_connection(self.other, 'foreign@example.test')
        hidden = self.saved(foreign_connection, source_message_id='hidden-scoped-message', subject='Private other mailbox marker')
        legacy = self.saved(source_message_id='legacy-visible-message')
        listed = self.client.get('/api/v1/sales/email-intakes/')
        self.assertEqual(listed.status_code, 200)
        records = listed.data['results'] if isinstance(listed.data, dict) else listed.data
        self.assertSetEqual({str(record['id']) for record in records}, {str(own.pk), str(legacy.pk)})
        self.assertNotIn('Private other mailbox marker', str(listed.data))
        searched = self.client.get('/api/v1/sales/email-intakes/', {'search': 'Private other mailbox marker'})
        records = searched.data['results'] if isinstance(searched.data, dict) else searched.data
        self.assertEqual(len(records), 0)
        self.assertEqual(self.client.get(f'/api/v1/sales/email-intakes/{hidden.pk}/').status_code, 404)
        for action, payload in (('start-review', {}), ('reject', {'reason': 'Not permitted'}), ('mark-duplicate', {})):
            response = self.client.post(f'/api/v1/sales/email-intakes/{hidden.pk}/{action}/', payload, format='json')
            self.assertEqual(response.status_code, 404, response.data)
        SalesMailboxConnection.objects.filter(pk=self.connection.pk).update(created_by=self.other)
        self.assertEqual(self.client.get(f'/api/v1/sales/email-intakes/{own.pk}/').status_code, 404)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(f'/api/v1/sales/email-intakes/{own.pk}/').status_code, 200)
        self.assertEqual(self.client.get(f'/api/v1/sales/email-intakes/{hidden.pk}/').status_code, 200)
        self.network.assert_not_called()

    def test_duplicate_targets_must_share_the_visible_mailbox_bucket(self):
        current = self.saved(self.connection, source_message_id='duplicate-current')
        second_connection = self.make_connection(self.owner, 'another-owned@example.test')
        other_bucket = self.saved(second_connection, source_message_id='duplicate-other-bucket')
        hidden_connection = self.make_connection(self.other, 'hidden@example.test')
        hidden = self.saved(hidden_connection, source_message_id='duplicate-hidden')
        legacy = self.saved(source_message_id='duplicate-legacy')
        endpoint = f'/api/v1/sales/email-intakes/{current.pk}/mark-duplicate/'
        for target in (other_bucket, hidden, legacy):
            response = self.client.post(endpoint, {'duplicate_of': str(target.pk)}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
            current.refresh_from_db()
            self.assertEqual(current.status, 'received')
            self.assertIsNone(current.duplicate_of_id)
        automatic = self.client.post(endpoint, {}, format='json')
        self.assertEqual(automatic.status_code, 200, automatic.data)
        current.refresh_from_db()
        self.assertIsNone(current.duplicate_of_id)
        same_bucket = self.saved(self.connection, source_message_id='duplicate-original')
        current.status = 'received'
        current.save(update_fields=['status'])
        permitted = self.client.post(endpoint, {'duplicate_of': str(same_bucket.pk)}, format='json')
        self.assertEqual(permitted.status_code, 200, permitted.data)
        current.refresh_from_db()
        self.assertEqual(current.duplicate_of_id, same_bucket.pk)

    def test_legacy_webhook_collision_cannot_find_or_overwrite_a_scoped_capture(self):
        self.assertEqual(self.post().status_code, 201)
        captured = SalesEmailIntake.objects.get()
        before = SalesEmailIntake.objects.values().get(pk=captured.pk)
        payload = {
            'source_message_id': CAPTURE_MESSAGE['id'], 'subject': 'Legacy webhook snapshot',
            'sender_email': 'legacy@example.test', 'received_at': '2026-09-20T08:00:00Z',
            'body_preview': 'Legacy body remains separate.',
        }
        client = APIClient()
        self.assertEqual(client.post('/api/v1/sales/email-intake/', payload, format='json').status_code, 403)
        headers = {'HTTP_X_RADAI_WEBHOOK_KEY': 'synthetic-capture-webhook-key'}
        first = client.post('/api/v1/sales/email-intake/', payload, format='json', **headers)
        repeated = client.post('/api/v1/sales/email-intake/', payload, format='json', **headers)
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(repeated.status_code, 200, repeated.data)
        self.assertTrue(repeated.data['duplicate'])
        self.assertEqual(first.data['intake_id'], repeated.data['intake_id'])
        self.assertNotEqual(str(first.data['intake_id']), str(captured.pk))
        self.assertEqual(SalesEmailIntake.objects.count(), 2)
        self.assertEqual(SalesEmailIntake.objects.values().get(pk=captured.pk), before)
        self.assertIsNone(SalesEmailIntake.objects.get(pk=first.data['intake_id']).mailbox_connection_id)

    def test_preexisting_legacy_id_does_not_prevent_an_authorized_scoped_capture(self):
        legacy = self.saved(source_message_id=CAPTURE_MESSAGE['id'])
        response = self.post()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotEqual(str(response.data['intake']['id']), str(legacy.pk))
        self.assertEqual(SalesEmailIntake.objects.count(), 2)

    def test_database_uniqueness_preserves_both_legacy_and_scoped_identity_rules(self):
        self.saved(source_message_id='shared-identity')
        self.saved(self.connection, source_message_id='shared-identity')
        for connection in (None, self.connection):
            with self.subTest(scoped=connection is not None):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    self.saved(connection, source_message_id='shared-identity')
        other = self.make_connection(self.owner, 'independent@example.test')
        self.saved(other, source_message_id='shared-identity')
        self.assertEqual(SalesEmailIntake.objects.count(), 3)

    def test_captured_provenance_protects_connection_deletion_and_repointing(self):
        self.assertEqual(self.post().status_code, 201)
        with self.assertRaises(ProtectedError), transaction.atomic():
            self.connection.delete()
        self.client.force_authenticate(self.admin)
        endpoint = f'/api/v1/sales/mailbox-connections/{self.connection.pk}/'
        before = SalesMailboxConnection.objects.values().get(pk=self.connection.pk)
        for field, value in (('mailbox_address', 'different@example.test'), ('tenant_id', 'different-tenant'), ('client_id', 'different-client'), ('auth_mode', 'delegated')):
            response = self.client.patch(endpoint, {field: value}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
            self.assertEqual(SalesMailboxConnection.objects.values().get(pk=self.connection.pk), before)
        self.assertEqual(self.client.delete(endpoint).status_code, 409)
        self.assertEqual(self.client.post(endpoint + 'connect-outlook/', {}, format='json').status_code, 409)
        self.assertEqual(SalesEmailIntake.objects.get().mailbox_connection_id, self.connection.pk)

    def test_reverse_migration_guard_allows_legacy_only_and_refuses_captured_provenance_loss(self):
        migration = import_module('apps.sales.migrations.0008_mailbox_scoped_capture')
        editor = SimpleNamespace(connection=SimpleNamespace(alias='default'))
        self.saved(source_message_id='legacy-rollback-guard')
        migration.refuse_loss_of_captured_sources(apps, editor)
        captured = self.saved(self.connection, source_message_id='captured-rollback-guard')
        before = list(SalesEmailIntake.objects.order_by('id').values())
        with self.assertRaisesRegex(RuntimeError, 'Cannot reverse Sales mailbox capture'):
            migration.refuse_loss_of_captured_sources(apps, editor)
        self.assertEqual(list(SalesEmailIntake.objects.order_by('id').values()), before)
        self.assertEqual(SalesEmailIntake.objects.get(pk=captured.pk).mailbox_connection, self.connection)

    def test_pending_oauth_callback_cannot_repoint_an_already_captured_connection(self):
        self.assertEqual(self.post().status_code, 201)
        state = {
            'connection_id': str(self.connection.pk), 'user_id': str(self.owner.pk),
            'nonce': 'synthetic-pending-callback',
        }
        cache.set('sales-graph-oauth:' + state['nonce'], state, timeout=600)
        token = signing.dumps(state, salt='sales-graph-oauth')
        before = SalesMailboxConnection.objects.values().get(pk=self.connection.pk)
        self.network.reset_mock()
        with patch.object(SalesMicrosoftGraphService, 'complete_delegated_authorization') as authorize:
            response = self.client.get('/api/v1/sales/mailbox-connections/oauth/callback/', {
                'state': token, 'code': 'synthetic-authorization-code',
            })
        self.assertEqual(response.status_code, 302)
        self.assertIn('outlook=error', response['Location'])
        authorize.assert_not_called()
        self.network.assert_not_called()
        self.assertEqual(SalesMailboxConnection.objects.values().get(pk=self.connection.pk), before)
        self.assertEqual(SalesEmailIntake.objects.count(), 1)
