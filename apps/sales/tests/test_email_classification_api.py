"""Classification remains a scoped, read-only proposal over actual source text."""

from copy import deepcopy
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.rbac.models import Permission, UserPermissionOverride
from apps.sales.microsoft_graph import SalesMicrosoftGraphService
from apps.sales.models import Client, Deal, OpportunityAuditEvent, SalesEmailIntake, SalesMailboxConnection

from .access_fixtures import grant_sales_actions
from .test_mailbox_browsing import graph_response
from .test_mailbox_capture import CAPTURE_MESSAGE


@override_settings(ROOT_URLCONF='apps.sales.tests.test_mailbox_capture',
                   SALES_MICROSOFT_GRAPH_BASE_URL='https://graph.microsoft.com/v1.0')
class EmailClassificationAPITests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.owner = get_user_model().objects.create_user('classification-owner', email='class-owner@example.test')
        self.other = get_user_model().objects.create_user('classification-other', email='class-other@example.test')
        for actor in (self.owner, self.other):
            grant_sales_actions(actor, 'sales_email_intake')
        self.mailbox = SalesMailboxConnection.objects.create(
            name='Synthetic classification mailbox', created_by=self.owner,
            mailbox_address='sales@example.test', tenant_id='synthetic-tenant',
            client_id='synthetic-client', auth_mode='application', enabled=False,
        )
        self.intake = SalesEmailIntake.objects.create(
            mailbox_connection=self.mailbox, captured_by=self.owner,
            source_mailbox_address=self.mailbox.mailbox_address, source_tenant_id=self.mailbox.tenant_id,
            source_message_id='synthetic-classification-message', conversation_id='synthetic-classification-chain',
            subject='Tender Addendum No. 03 - RFT-SYN-72', sender_name='Tender Desk',
            sender_email='buyer@example.test', received_at='2026-09-28T08:00:00Z',
            body_preview='Customer: Northbridge Utilities Ltd\nTender Addendum No. 03 has been issued.\n'
                         'Please review the revised scope.\nSubmission date: 2026-10-20\nDue date: 2026-10-23',
            status='under_review', reviewed_by=self.owner, reviewed_at=timezone.now(),
            resolution_note='Synthetic reviewer note must survive classification.',
        )
        self.url = f'/api/v1/sales/email-intakes/{self.intake.pk}/'
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def evidence_is_scoped(self, information):
        proposal = information['classification']
        source_ids = {source['id'] for source in information['analysis']['sources']}
        self.assertTrue(proposal['needs_review'])
        self.assertEqual(proposal['version'], 1)
        self.assertEqual(proposal['confidence']['method'], 'rule_evidence_v1')
        self.assertNotIn('percentage', proposal['confidence'])
        self.assertNotIn('score', proposal['confidence'])
        for item in proposal['evidence']:
            self.assertIn(item['source_id'], source_ids)
            self.assertIn(item['excerpt'], self.intake.subject if item['location'] == 'subject' else self.intake.body_preview)

    def test_saved_read_classifies_without_mutating_source_review_or_business_records(self):
        snapshot = SalesEmailIntake.objects.values().get(pk=self.intake.pk)
        with patch('requests.request') as network, patch('requests.post') as post:
            first = self.client.get(self.url)
            repeated = self.client.get(self.url)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(repeated.status_code, 200)
        information = first.data['extracted_information']
        self.assertEqual(information['classification']['code'], 'tender_addendum')
        self.assertEqual(information['classification'], repeated.data['extracted_information']['classification'])
        self.assertEqual(information['request_type_code'], 'RFT')
        self.assertEqual(information['organization_name'], 'Northbridge Utilities Ltd')
        self.assertEqual(information['submission_date'], '')
        self.assertEqual(information['stated_submission_date'], '2026-10-20')
        self.assertEqual(information['due_date'], '2026-10-23')
        self.assertEqual(information['analysis']['coverage']['status'], 'saved_content')
        self.evidence_is_scoped(information)
        self.assertIn('no-store', first['Cache-Control'])
        self.assertEqual(SalesEmailIntake.objects.values().get(pk=self.intake.pk), snapshot)
        self.assertFalse(any(model.objects.exists() for model in (Client, Deal, OpportunityAuditEvent)))
        network.assert_not_called()
        post.assert_not_called()

    def test_list_uses_the_same_scoped_proposal_and_keeps_review_status(self):
        response = self.client.get('/api/v1/sales/email-intakes/')
        self.assertEqual(response.status_code, 200)
        rows = response.data['results'] if isinstance(response.data, dict) else response.data
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['status'], 'under_review')
        self.assertEqual(rows[0]['extracted_information']['classification']['code'], 'tender_addendum')
        self.assertIn('no-store', response['Cache-Control'])

    def test_other_mailbox_owner_cannot_read_classification_or_evidence(self):
        self.client.force_authenticate(self.other)
        with patch('apps.sales.serializers.analyze_saved_email') as analyze:
            detail = self.client.get(self.url)
            listing = self.client.get('/api/v1/sales/email-intakes/')
        self.assertEqual(detail.status_code, 404)
        self.assertEqual(listing.status_code, 200)
        rows = listing.data['results'] if isinstance(listing.data, dict) else listing.data
        self.assertEqual(len(rows), 0)
        self.assertNotIn('Northbridge', str(detail.data))
        analyze.assert_not_called()

    def test_explicit_read_denial_runs_before_analysis(self):
        permission = Permission.objects.get(module__code='sales_email_intake', action='read')
        UserPermissionOverride.objects.create(user_profile=self.owner.rbac_profile, permission=permission, allowed=False)
        cache.clear()
        with patch('apps.sales.serializers.analyze_saved_email') as analyze:
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)
        analyze.assert_not_called()

    def test_saved_mailbox_identity_excludes_own_quoted_customer_claim(self):
        self.intake.subject = 'Re: RFQ-SYN-83 - Survey services'
        self.intake.body_preview = (
            'Please use the customer request below.\n\n-----Original Message-----\n'
            'From: Sales <sales@example.test>\nSent: Sat, 19 Sep 2026 08:00:00 +0000\n'
            'To: Buyer <buyer@example.test>\nSubject: RFP-SYN-11 - Internal planning\n\n'
            'Customer: Internal planning placeholder\nPlease submit your proposal.\n\n'
            '-----Original Message-----\nFrom: Buyer <buyer@example.test>\n'
            'Sent: Sun, 20 Sep 2026 08:00:00 +0000\nTo: Sales <sales@example.test>\n'
            'Subject: RFQ-SYN-83 - Survey services\n\n'
            'Customer: Northbridge Utilities Ltd\nPlease submit your quotation.\nDue date: 2026-10-23'
        )
        self.intake.save()
        snapshot = SalesEmailIntake.objects.values().get(pk=self.intake.pk)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        info = response.data['extracted_information']
        self.assertEqual(info['organization_name'], 'Northbridge Utilities Ltd')
        self.assertEqual(info['request_type_code'], 'RFQ')
        self.assertEqual(info['title'], 'RFQ-SYN-83 - Survey services')
        self.assertNotIn(info['classification']['code'], {'rfq', 'rfp', 'tender_opportunity'})
        self.assertEqual(SalesEmailIntake.objects.values().get(pk=self.intake.pk), snapshot)

    def test_live_detail_matches_saved_classification_without_additional_graph_calls(self):
        message = {
            **deepcopy(CAPTURE_MESSAGE), 'id': self.intake.source_message_id,
            'subject': self.intake.subject, 'conversationId': self.intake.conversation_id,
            'body': {'contentType': 'text', 'content': self.intake.body_preview},
        }
        with patch.object(SalesMicrosoftGraphService, 'token', return_value='synthetic-classification-token'), \
                patch('apps.sales.microsoft_graph.requests.request', side_effect=[graph_response(message), graph_response({'value': [message]})]) as network:
            live = self.client.get(f'/api/v1/sales/mailbox-connections/{self.mailbox.pk}/message/', {'message_id': message['id']})
        self.assertEqual(live.status_code, 200)
        saved = self.client.get(self.url)
        self.assertEqual(live.data['extracted_information']['classification'], saved.data['extracted_information']['classification'])
        self.assertEqual(network.call_count, 2)
        self.assertTrue(all(call.args[0] == 'GET' for call in network.call_args_list))
        self.assertFalse(any(model.objects.exists() for model in (Client, Deal, OpportunityAuditEvent)))
