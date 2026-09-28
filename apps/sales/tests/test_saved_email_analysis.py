"""Saved conversation history must be complete within bounds and current scope."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.rbac.models import Permission, UserPermissionOverride
from apps.sales.email_permissions import visible_email_intakes
from apps.sales.models import Deal, OpportunityAuditEvent, SalesEmailIntake, SalesMailboxConnection
from apps.sales.serializers import SalesEmailIntakeSerializer
from .test_email_customer_matching import CustomerMatchingFixtures


@override_settings(ROOT_URLCONF='apps.sales.tests.test_mailbox_capture')
class SavedEmailConversationTests(CustomerMatchingFixtures, TestCase):
    def setUp(self):
        super().setUp()
        self.mailbox = SalesMailboxConnection.objects.create(
            name='Synthetic conversation', created_by=self.owner,
            mailbox_address='sales@example.test', tenant_id='synthetic-tenant',
            client_id='synthetic-app', auth_mode='application', enabled=False,
        )
        self.original = self.saved('original', 8, subject='RFQ 410: Equipment study', body=(
            'Customer: Northbridge Utilities Ltd\nPlease submit your quotation.\n'
            'Submission date: 12 October 2026\nDue date: 14 October 2026'
        ))
        self.reply = self.saved('reply', 9, subject='Re: RFQ 410: Equipment study', body='Thank you. We will send further information.')
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def saved(self, key, hour, *, subject='Re: RFQ 410: Equipment study', body='Thank you.', **extra):
        values = {
            'mailbox_connection': self.mailbox,
            'source_mailbox_address': self.mailbox.mailbox_address,
            'source_tenant_id': self.mailbox.tenant_id,
            'source_message_id': key, 'conversation_id': 'synthetic-chain',
            'subject': subject, 'sender_email': 'buyer@customer.test',
            'received_at': datetime(2026, 9, 28, hour, tzinfo=timezone.utc),
            'body_preview': body,
        }
        return SalesEmailIntake.objects.create(**{**values, **extra})

    def get(self, obj=None):
        return self.client.get(f'/api/v1/sales/email-intakes/{(obj or self.reply).pk}/')

    def test_latest_unquoted_reply_uses_saved_original_without_mutation_or_network(self):
        before = list(SalesEmailIntake.objects.order_by('pk').values())
        with patch('requests.request') as network, patch('requests.post') as post:
            response = self.get()
        self.assertEqual(response.status_code, 200)
        information = response.data['extracted_information']
        self.assertEqual(information['title'], self.original.subject)
        self.assertEqual(information['organization_name'], 'Northbridge Utilities Ltd')
        # Legacy snapshots have received_at but no retained sent timestamp.
        self.assertEqual(information['submission_date'], '')
        self.assertEqual(information['stated_submission_date'], '2026-10-12')
        self.assertEqual(information['customer_domain'], 'customer.test')
        self.assertEqual(information['customer_name'], 'Northbridge Utilities Ltd')
        self.assertEqual(information['due_date'], '2026-10-14')
        analysis = information['analysis']
        self.assertEqual(analysis['coverage']['messages_reviewed'], 2)
        self.assertEqual(analysis['coverage']['status'], 'saved_content')
        self.assertTrue(analysis['coverage']['original_identified'])
        sources = {item['id']: item for item in analysis['sources']}
        self.assertEqual(sources[analysis['selected_source_id']]['thread_role'], 'reply')
        self.assertEqual(sources[analysis['original_request_source_id']]['subject'], self.original.subject)
        self.assertNotEqual(analysis['selected_source_id'], analysis['original_request_source_id'])
        self.assertEqual(response.data['subject'], self.reply.subject)
        self.assertEqual(response.data['body_preview'], self.reply.body_preview)
        self.assertEqual(list(SalesEmailIntake.objects.order_by('pk').values()), before)
        self.assertFalse(Deal.objects.exists())
        self.assertFalse(OpportunityAuditEvent.objects.exists())
        network.assert_not_called()
        post.assert_not_called()
        self.assertIn('no-store', response['Cache-Control'])

    def test_saved_original_sent_timestamp_is_used_instead_of_latest_reply_or_received_time(self):
        self.original.sent_at = datetime(2026, 9, 27, 16, tzinfo=timezone.utc)
        self.original.save(update_fields=['sent_at'])
        self.reply.sent_at = datetime(2026, 9, 28, 9, tzinfo=timezone.utc)
        self.reply.save(update_fields=['sent_at'])
        information = self.get().data['extracted_information']
        self.assertEqual(information['submission_date'], '2026-09-27')
        self.assertEqual(information['stated_submission_date'], '2026-10-12')
        self.assertEqual(information['due_date'], '2026-10-14')

    def test_conversation_identity_never_crosses_mailbox_tenant_address_or_legacy_bucket(self):
        other_mailbox = SalesMailboxConnection.objects.create(
            name='Other mailbox', created_by=self.owner, mailbox_address='other@example.test',
            tenant_id='other-tenant', client_id='other-app',
        )
        variants = [
            {'mailbox_connection': other_mailbox}, {'source_tenant_id': 'different-tenant'},
            {'source_mailbox_address': 'different@example.test'}, {'conversation_id': 'different-chain'},
            {'mailbox_connection': None},
        ]
        for index, variant in enumerate(variants):
            self.saved(f'excluded-{index}', 7, subject='RFQ secret', body='Customer: Hidden Company', **variant)
        information = self.get().data['extracted_information']
        self.assertEqual(information['analysis']['coverage']['messages_reviewed'], 2)
        self.assertEqual(information['organization_name'], 'Northbridge Utilities Ltd')
        self.assertNotIn('Hidden Company', str(information))
        self.client.force_authenticate(self.other)
        self.assertEqual(self.get().status_code, 404)

    def test_legacy_blank_conversation_and_absent_actor_analyze_only_selected_text(self):
        legacy = self.saved('legacy', 10, mailbox_connection=None)
        blank = self.saved('blank', 10, conversation_id='')
        whitespace = self.saved('whitespace', 10, conversation_id='   ')
        for obj in (legacy, blank, whitespace):
            information = self.get(obj).data['extracted_information']
            self.assertEqual(information['analysis']['coverage']['messages_reviewed'], 1)
            self.assertFalse(information['analysis']['coverage']['original_identified'])
        output = SalesEmailIntakeSerializer(self.reply).data['extracted_information']
        self.assertEqual(output['analysis']['coverage']['messages_reviewed'], 1)
        self.assertFalse(output['analysis']['coverage']['original_identified'])

    def test_intake_read_revocation_prevents_history_query(self):
        permission = Permission.objects.get(module__code='sales_email_intake', action='read', is_active=True)
        UserPermissionOverride.objects.create(user_profile=self.owner.rbac_profile, permission=permission, allowed=False)
        with patch('apps.sales.saved_email_analysis.visible_email_intakes') as history:
            response = self.get()
        self.assertEqual(response.status_code, 403)
        history.assert_not_called()

    def test_request_local_history_cache_reused_but_not_shared_across_requests(self):
        context = {'request': SimpleNamespace(user=self.owner)}
        with patch('apps.sales.saved_email_analysis.visible_email_intakes', wraps=visible_email_intakes) as history:
            data = SalesEmailIntakeSerializer([self.reply, self.original], many=True, context=context).data
        self.assertEqual(history.call_count, 1)
        self.assertTrue(all(row['extracted_information']['analysis']['coverage']['messages_reviewed'] == 2 for row in data))
        self.saved('later', 10)
        self.assertEqual(self.get().data['extracted_information']['analysis']['coverage']['messages_reviewed'], 3)

    def test_message_cap_preserves_selected_reply_and_earliest_original(self):
        self.saved('middle', 9)
        with patch('apps.sales.saved_email_analysis.MAX_MESSAGES', 2):
            information = self.get().data['extracted_information']
        analysis = information['analysis']
        self.assertEqual(analysis['coverage']['status'], 'partial')
        self.assertEqual(analysis['coverage']['messages_reviewed'], 2)
        self.assertTrue(analysis['selected_source_id'])
        self.assertTrue(any(source['is_selected'] and source['thread_role'] == 'reply' for source in analysis['sources']))

    def test_response_text_bound_is_explicit_and_selected_text_is_retained(self):
        with patch('apps.sales.saved_email_analysis.RESPONSE_TEXT_LIMIT', 1):
            response = self.get()
            information = response.data['extracted_information']
        analysis = information['analysis']
        self.assertEqual(analysis['coverage']['status'], 'partial')
        selected = next(source for source in analysis['sources'] if source['is_selected'])
        self.assertEqual(selected['subject'], self.reply.subject)
        self.assertEqual(response.data['body_preview'], self.reply.body_preview)
        self.assertEqual(selected['thread_role'], 'reply')
        self.assertTrue(any('limit' in item for item in analysis['limitations']))

    def test_changed_connection_snapshot_does_not_load_siblings(self):
        SalesMailboxConnection.objects.filter(pk=self.mailbox.pk).update(tenant_id='changed-tenant')
        information = self.get().data['extracted_information']
        self.assertEqual(information['analysis']['coverage']['messages_reviewed'], 1)
        self.assertEqual(information['organization_name'], '')

    def test_display_capabilities_scale_per_response_and_revocation_is_fresh(self):
        # These fields depend on current actor grants and each row's status,
        # not a new authorization scan for every record on the 500-row page.
        for index in range(18):
            self.saved(f'bulk-{index}', 10)
        self.original.status = 'converted'
        self.original.save(update_fields=['status'])
        request = SimpleNamespace(user=self.owner)
        serializer = SalesEmailIntakeSerializer(context={'request': request})
        records = list(SalesEmailIntake.objects.all())
        with CaptureQueriesContext(connection) as queries:
            grants = [(row.status, serializer.get_can_create_opportunity(row), serializer.get_can_create_client(row))
                      for row in records]
        self.assertLess(len(queries), 80)
        self.assertTrue(all(opportunity and client for status, opportunity, client in grants if status == 'received'))
        self.assertTrue(all(not opportunity and not client for status, opportunity, client in grants if status == 'converted'))
        permission = Permission.objects.get(module__code='sales_clients', action='create', is_active=True)
        UserPermissionOverride.objects.create(user_profile=self.owner.rbac_profile, permission=permission, allowed=False)
        next_response = SalesEmailIntakeSerializer(context={'request': SimpleNamespace(user=self.owner)})
        self.assertFalse(next_response.get_can_create_client(self.reply))
        self.assertTrue(next_response.get_can_create_opportunity(self.reply))
