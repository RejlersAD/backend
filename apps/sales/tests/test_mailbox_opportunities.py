"""Guarded, synthetic email conversion and retry behavior; no live Graph."""

from copy import deepcopy
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.rbac.models import Permission, UserPermissionOverride, UserProfile
from apps.rbac.route_guard import secure_module_endpoints
from apps.sales.intake_views import SalesEmailIntakeViewSet
from apps.sales.mailbox_opportunities import REVIEW_MAX_AGE, REVIEW_SALT
from apps.sales.microsoft_graph import SalesMicrosoftGraphService
from apps.sales.models import Client, Deal, OpportunityAuditEvent, SalesEmailIntake, SalesMailboxConnection
from apps.sales.views import SalesMailboxConnectionViewSet

from .access_fixtures import grant_sales_actions
from .test_mailbox_browsing import MESSAGE, graph_response


router = DefaultRouter()
router.register('mailbox-connections', SalesMailboxConnectionViewSet, basename='convert-mailboxes')
router.register('email-intakes', SalesEmailIntakeViewSet, basename='convert-intakes')
urlpatterns = [path('api/v1/sales/', include(router.urls))]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__, SALES_MICROSOFT_GRAPH_BASE_URL='https://graph.microsoft.com/v1.0')
class MailboxOpportunityAPITests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        users = get_user_model()
        self.user = users.objects.create_user('converter', email='converter@example.test')
        grant_sales_actions(self.user, 'sales_email_intake', 'sales_opportunities', 'sales_clients')
        self.other = users.objects.create_user('other-converter', email='other@example.test')
        grant_sales_actions(self.other, 'sales_email_intake', 'sales_opportunities', 'sales_clients')
        self.outsider = users.objects.create_user('outsider', email='outsider@example.test')
        UserProfile.objects.get_or_create(
            user=self.outsider, defaults={'organization': self.user.rbac_profile.organization},
        )
        self.account = Client.objects.create(
            client_code='REVIEWED-CLIENT', company_name='Reviewed Company',
            account_manager=self.user, industry_type='other',
        )
        self.hidden_account = Client.objects.create(
            client_code='HIDDEN-CLIENT', company_name='Inaccessible Company',
            account_manager=self.outsider, industry_type='other',
        )
        self.connection = SalesMailboxConnection.objects.create(
            name='Synthetic shared mailbox', tenant_id='synthetic-tenant', client_id='synthetic-client',
            mailbox_address='sales@example.test', auth_mode='application', enabled=False,
            created_by=self.user,
        )
        prefix = f'/api/v1/sales/mailbox-connections/{self.connection.pk}'
        self.detail_url = f'{prefix}/message/'
        self.convert_url = f'{prefix}/convert-to-opportunity/'
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        token = patch.object(SalesMicrosoftGraphService, 'token', return_value='synthetic-token')
        token.start()
        self.addCleanup(token.stop)
        network = patch('apps.sales.microsoft_graph.requests.request')
        self.network = network.start()
        self.addCleanup(network.stop)
        self.message = {
            **deepcopy(MESSAGE), 'body': {'contentType': 'html', 'content': '<p>Customer name: Reviewed Company</p><p>Due date: 21 November 2026</p>'},
        }
        self.network.return_value = graph_response(self.message)
        self.fields = {
            'message_id': MESSAGE['id'], 'deal_name': 'Reviewed engineering study',
            'client': str(self.account.pk), 'client_reference': 'RFT-2026-001',
            'estimated_value': '250000.15', 'currency': 'AED',
            'expected_close_date': '2026-12-31', 'submission_due_date': '2026-11-21',
            'scope_type': 'feasibility', 'description': 'The scope reviewed by the user.',
        }

    def review(self):
        response = self.client.get(self.detail_url, {'message_id': MESSAGE['id']})
        self.assertEqual(response.status_code, 200)
        return response

    def payload(self):
        return {**self.fields, 'source_token': self.review().data['source_token']}

    def deny(self, module, action, user=None):
        permission = Permission.objects.filter(module__code=module, action=action, is_active=True).first()
        self.assertIsNotNone(permission)
        return UserPermissionOverride.objects.create(
            user_profile=(user or self.user).rbac_profile, permission=permission, allowed=False,
        )

    def post(self, payload):
        return self.client.post(self.convert_url, payload, format='json')

    def assert_no_conversion(self):
        self.assertEqual(Deal.objects.count(), 0)
        self.assertEqual(OpportunityAuditEvent.objects.count(), 0)
        self.assertEqual(SalesEmailIntake.objects.count(), 0)

    def test_detail_has_evidence_and_capability_without_writes(self):
        detail = self.review()
        self.assertTrue(detail.data['can_create_opportunity'])
        self.assertEqual(detail.data['extracted_information']['customer_name'], 'Reviewed Company')
        self.assertTrue(detail.data['source_token'])
        self.assert_no_conversion()

    def test_explicit_creation_identical_retry_and_canonical_values(self):
        before = SalesMailboxConnection.objects.values().get(pk=self.connection.pk)
        payload = self.payload()
        first = self.post(payload)
        retry = self.post(payload)
        self.assertEqual(first.status_code, 201, first.data)
        self.assertTrue(first.data['created'])
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertFalse(retry.data['created'])
        self.assertEqual(first.data['opportunity']['id'], retry.data['opportunity']['id'])
        opportunity = Deal.objects.get()
        self.assertEqual(str(opportunity.estimated_value), '250000.15')
        self.assertEqual(opportunity.owner, self.user)
        self.assertEqual(opportunity.client, self.account)
        self.assertEqual(opportunity.stage, 'lead')
        self.assertEqual(opportunity.description, self.fields['description'])
        self.assertEqual(str(opportunity.expected_close_date), '2026-12-31')
        self.assertEqual(str(opportunity.submission_due_date), '2026-11-21')
        self.assertNotIn('body', str(opportunity.custom_fields.keys()))
        self.assertEqual(OpportunityAuditEvent.objects.count(), 1)
        self.assertEqual(SalesEmailIntake.objects.count(), 0)
        self.assertEqual(before, SalesMailboxConnection.objects.values().get(pk=self.connection.pk))
        self.assertIn('no-store', first['Cache-Control'])
        self.assertTrue(all(call.args[0] == 'GET' for call in self.network.call_args_list))
        self.assertTrue(all('IdType="ImmutableId"' in call.kwargs['headers']['Prefer'] for call in self.network.call_args_list))

    def test_changed_fields_conflict_and_mutable_custom_fields_do_not_erase_retry(self):
        payload = self.payload()
        self.assertEqual(self.post(payload).status_code, 201)
        Deal.objects.update(custom_fields={})
        self.assertEqual(self.post(payload).status_code, 200)
        changed = self.post({**payload, 'estimated_value': '250001.00'})
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(changed.data['code'], 'email_already_converted')
        self.assertEqual(Deal.objects.count(), 1)
        self.assertEqual(OpportunityAuditEvent.objects.count(), 1)

    def test_app_configuration_change_requires_review_without_duplicating_source(self):
        payload = self.payload()
        self.assertEqual(self.post(payload).status_code, 201)
        SalesMailboxConnection.objects.filter(pk=self.connection.pk).update(client_id='replacement-synthetic-client')
        self.assertEqual(self.post(payload).status_code, 400)
        reviewed_again = self.payload()
        self.assertEqual(self.post(reviewed_again).status_code, 200)
        self.assertEqual(Deal.objects.count(), 1)
        self.assertEqual(OpportunityAuditEvent.objects.count(), 1)

    def test_each_extra_permission_denial_blocks_before_graph(self):
        payload = self.payload()
        for module, action in [
            ('sales_email_intake', 'read'), ('sales_email_intake', 'create'),
            ('sales_opportunities', 'read'), ('sales_opportunities', 'create'),
            ('sales_clients', 'read'),
        ]:
            with self.subTest(module=module, action=action):
                override = self.deny(module, action)
                self.network.reset_mock()
                response = self.post(payload)
                self.assertEqual(response.status_code, 403, response.data)
                self.assertIn('no-store', response['Cache-Control'])
                self.network.assert_not_called()
                override.delete()
        self.assert_no_conversion()

    def test_read_only_user_can_preview_with_creation_unavailable(self):
        self.deny('sales_opportunities', 'create')
        self.assertFalse(self.review().data['can_create_opportunity'])
        self.assert_no_conversion()

    def test_other_owner_and_missing_module_cannot_reach_source(self):
        payload = self.payload()
        for user, status in [(self.other, 404), (self.outsider, 403)]:
            self.client.force_authenticate(user)
            self.network.reset_mock()
            self.assertEqual(self.post(payload).status_code, status)
            self.network.assert_not_called()
        self.assert_no_conversion()

    def test_inaccessible_client_and_forged_provenance_are_rejected(self):
        payload = self.payload()
        for changed in [
            {'client': str(self.hidden_account.pk)}, {'client': 'invalid-client'},
            {'body_text': 'Forged source'}, {'custom_fields': {'stage': 'awarded'}},
            {'source_mailbox_address': 'other@example.test'}, {'stage': 'awarded'},
            {'new_client': {'company_name': 'Unreviewed Company'}},
        ]:
            with self.subTest(field=next(iter(changed))):
                self.network.reset_mock()
                self.assertEqual(self.post({**payload, **changed}).status_code, 400)
                self.network.assert_not_called()
        self.assert_no_conversion()

    def test_missing_amount_date_and_invalid_date_are_not_fabricated(self):
        payload = self.payload()
        for changed in [{'estimated_value': ''}, {'expected_close_date': ''}, {'expected_close_date': '2026-02-30'}, {'currency': ''}]:
            self.assertEqual(self.post({**payload, **changed}).status_code, 400)
        self.assert_no_conversion()

    def test_review_token_tamper_expiry_actor_and_message_binding(self):
        payload = self.payload()
        self.assertEqual(self.post({**payload, 'source_token': 'tampered'}).status_code, 400)
        self.assertEqual(self.post({**payload, 'message_id': 'another-message'}).status_code, 400)
        review = signing.loads(payload['source_token'], salt=REVIEW_SALT)
        review['actor'] = str(self.other.pk)
        self.assertEqual(self.post({**payload, 'source_token': signing.dumps(review, salt=REVIEW_SALT)}).status_code, 400)
        with patch('django.core.signing.time.time', return_value=0):
            expired = signing.dumps(review, salt=REVIEW_SALT)
        self.assertEqual(self.post({**payload, 'source_token': expired}).status_code, 410)
        self.assertEqual(REVIEW_MAX_AGE, 1800)
        self.assert_no_conversion()

    def test_source_change_and_connection_repoint_during_refetch_conflict(self):
        payload = self.payload()
        self.message['body']['content'] = '<p>Changed after review</p>'
        self.network.return_value = graph_response(self.message)
        self.assertEqual(self.post(payload).status_code, 409)
        self.network.return_value = graph_response({**MESSAGE, 'body': {'contentType': 'text', 'content': 'Stable'}})
        payload = self.payload()

        def repoint(*args, **kwargs):
            SalesMailboxConnection.objects.filter(pk=self.connection.pk).update(mailbox_address='changed@example.test')
            return graph_response({**MESSAGE, 'body': {'contentType': 'text', 'content': 'Stable'}})

        self.network.side_effect = repoint
        self.assertEqual(self.post(payload).status_code, 409)
        self.assert_no_conversion()

    def test_current_deal_visibility_is_checked_on_retry(self):
        payload = self.payload()
        self.assertEqual(self.post(payload).status_code, 201)
        Deal.objects.update(owner=self.outsider)
        retry = self.post(payload)
        self.assertEqual(retry.status_code, 403)
        self.assertNotIn('opportunity', retry.data)
        self.assertEqual(Deal.objects.count(), 1)

    def test_connection_ownership_and_write_access_are_rechecked_after_graph(self):
        payload = self.payload()

        def revoke_owner(*args, **kwargs):
            SalesMailboxConnection.objects.filter(pk=self.connection.pk).update(created_by=self.other)
            return graph_response(self.message)

        self.network.side_effect = revoke_owner
        response = self.post(payload)
        self.assertEqual(response.status_code, 404)
        self.assertIn('no-store', response['Cache-Control'])
        SalesMailboxConnection.objects.filter(pk=self.connection.pk).update(created_by=self.user)

        def revoke_create(*args, **kwargs):
            self.deny('sales_opportunities', 'create')
            return graph_response(self.message)

        self.network.side_effect = revoke_create
        self.assertEqual(self.post(payload).status_code, 403)
        self.assert_no_conversion()

    def test_read_flag_changes_do_not_invalidate_review_but_safe_link_changes_do(self):
        payload = self.payload()
        self.message['isRead'] = True
        self.network.return_value = graph_response(self.message)
        self.assertEqual(self.post(payload).status_code, 201)
        self.message['body']['content'] = '<p><a href="https://example.test/first">Reference</a></p>'
        self.network.return_value = graph_response(self.message)
        payload = self.payload()
        self.message['body']['content'] = '<p><a href="https://example.test/changed">Reference</a></p>'
        self.network.return_value = graph_response(self.message)
        response = self.post(payload)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'email_review_changed')
        self.assertEqual(Deal.objects.count(), 1)

    def test_provider_failure_and_audit_failure_leave_no_opportunity(self):
        payload = self.payload()
        self.network.return_value = graph_response({'error': 'sensitive provider detail'}, status=503)
        failed = self.post(payload)
        self.assertEqual(failed.status_code, 503)
        self.assertNotIn('sensitive', str(failed.data))
        self.network.return_value = graph_response(self.message)
        with patch('apps.sales.mailbox_opportunities.OpportunityAuditEvent.objects.create', side_effect=RuntimeError('sensitive audit failure')):
            failed = self.post(payload)
        self.assertEqual(failed.status_code, 503)
        self.assertNotIn('sensitive', str(failed.data))
        self.assert_no_conversion()

    def make_intake(self):
        return SalesEmailIntake.objects.create(
            source_message_id='synthetic-imported', subject='Imported enquiry',
            sender_email='client@example.test', received_at='2026-09-28T08:00:00Z',
        )

    def test_imported_conversion_applies_extra_authority_and_client_scope(self):
        intake = self.make_intake()
        url = f'/api/v1/sales/email-intakes/{intake.pk}/convert-to-opportunity/'
        fields = {key: value for key, value in self.fields.items() if key != 'message_id'}
        denied = self.deny('sales_opportunities', 'create')
        self.assertEqual(self.client.post(url, fields, format='json').status_code, 403)
        denied.delete()
        self.assertEqual(self.client.post(url, {**fields, 'client': str(self.hidden_account.pk)}, format='json').status_code, 400)
        created = self.client.post(url, fields, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(self.client.post(url, fields, format='json').status_code, 200)
        self.network.assert_not_called()

    def test_imported_new_client_path_requires_client_creation_authority(self):
        intake = self.make_intake()
        self.deny('sales_clients', 'create')
        fields = {key: value for key, value in self.fields.items() if key not in {'message_id', 'client'}}
        fields['new_client'] = {'company_name': 'Explicit new client'}
        response = self.client.post(
            f'/api/v1/sales/email-intakes/{intake.pk}/convert-to-opportunity/', fields, format='json',
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Deal.objects.count(), 0)
        self.assertFalse(Client.objects.filter(company_name='Explicit new client').exists())
