"""Source-bound AI proposals use existing review commands and never create on read."""

from copy import deepcopy
from unittest.mock import patch

from django.core import signing
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.sales.email_opportunity_evidence import MAX_SNAPSHOT_BYTES, reviewed_analysis_snapshot
from apps.sales.mailbox_opportunities import REVIEW_SALT, _source_digest
from apps.sales.models import Deal, OpportunityAuditEvent, SalesEmailIntake

from . import test_mailbox_opportunities as opportunity_fixtures
from .test_mailbox_browsing import MESSAGE, graph_response
from .test_email_ai_analysis import BODY, SUBJECT, oq_proposal


@override_settings(ROOT_URLCONF='apps.sales.tests.test_mailbox_opportunities',
                   SALES_MICROSOFT_GRAPH_BASE_URL='https://graph.microsoft.com/v1.0')
class EmailAIReviewIntegrationTests(TestCase):
    setUp = opportunity_fixtures.MailboxOpportunityAPITests.setUp
    review = opportunity_fixtures.MailboxOpportunityAPITests.review
    payload = opportunity_fixtures.MailboxOpportunityAPITests.payload
    post = opportunity_fixtures.MailboxOpportunityAPITests.post
    deny = opportunity_fixtures.MailboxOpportunityAPITests.deny

    def proposal(self, result, messages, **kwargs):
        result = deepcopy(result)
        fields = result['extracted_information']
        fields.update({
            'tender_reference': 'Tender_38669', 'source_portal': 'OQ Tawreed Portal',
            'deadline_at': '2026-10-02T01:59:00+04:00',
            'deadline_timezone': 'Asia/Muscat',
            'ai_review': {'status': 'validated', 'version': 1},
        })
        fields['evidence']['tender_reference'] = 'Tender Code Tender_38669'
        fields['field_sources']['tender_reference'] = ['m1-current']
        return result

    def saved(self):
        return SalesEmailIntake.objects.create(
            mailbox_connection=self.connection,
            source_mailbox_address=self.connection.mailbox_address,
            source_tenant_id=self.connection.tenant_id,
            source_message_id='saved-ai-message', subject='Synthetic tender reminder',
            sender_email='buyer@example.test', received_at=timezone.now(),
            body_preview='Tender Code Tender_38669\nDate: 2 Oct, 2026',
        )

    def test_live_detail_opts_in_but_conversion_never_invokes_analysis_provider(self):
        with patch('apps.sales.email_ai_analysis.enhance_email_analysis', side_effect=self.proposal) as enhance:
            payload = self.payload()
            self.assertTrue(enhance.call_args.kwargs['allow_provider'])
            self.assertIn(str(self.user.pk), enhance.call_args.kwargs['scope_key'])
            enhance.reset_mock()
            response = self.post(payload)
            self.assertEqual(response.status_code, 201, response.data)
            enhance.assert_not_called()
        snapshot = OpportunityAuditEvent.objects.get().data['reviewed_email_analysis']
        self.assertEqual(snapshot['deadline_at'], '2026-10-02T01:59:00+04:00')
        self.assertEqual(snapshot['tender_reference'], 'Tender_38669')
        self.assertEqual(str(Deal.objects.get().submission_due_date), self.fields['submission_due_date'])

    @override_settings(SALES_EMAIL_AI_ENABLED=True, SALES_EMAIL_AI_PROVIDER='openai',
                       SALES_EMAIL_AI_MODEL='gpt-4o-mini', SALES_EMAIL_AI_API_KEY='synthetic-only')
    def test_real_validator_oq_detail_match_create_retry_preserves_full_evidence(self):
        self.account.company_name = 'OQ'
        self.account.save(update_fields=['company_name'])
        self.message.update({
            'subject': SUBJECT,
            'from': {'emailAddress': {'name': 'OQ Tawreed', 'address': 'tawreed@oq.com'}},
            'body': {'contentType': 'text', 'content': BODY},
            'toRecipients': [{'emailAddress': {'address': self.connection.mailbox_address}}],
        })
        self.network.return_value = graph_response(self.message)
        with patch('apps.sales.email_ai_analysis.analyze_email_sources', return_value={
            'status': 'completed', 'proposal': oq_proposal(), 'provider': 'openai', 'model': 'gpt-4o-mini',
        }) as provider:
            detail = self.review().data
            info = detail['extracted_information']
            self.assertEqual(info['ai_review']['status'], 'validated')
            self.assertEqual(info['customer_name'], 'OQ')
            self.assertEqual(info['classification']['code'], 'tender_opportunity')
            self.assertEqual(info['due_date'], '2026-10-02')
            self.assertEqual(info['tender_reference'], 'Tender_38669')
            self.assertEqual(info['procurement_reference'], '6000062192')
            self.assertEqual(info['pr_reference'], '70059386')
            self.assertEqual(info['customer_match']['status'], 'matched')
            self.assertEqual(info['customer_match']['candidates'][0]['id'], str(self.account.pk))
            self.assertFalse(Deal.objects.exists())
            payload = {
                **self.fields, 'source_token': detail['source_token'],
                'classification_code': 'tender_opportunity',
                'client_reference': info['tender_reference'],
                'submission_due_date': info['due_date'], 'description': info['scope_summary'],
            }
            first = self.post(payload)
            self.assertEqual(first.status_code, 201, first.data)
            retry = self.post(payload)
            self.assertEqual(retry.status_code, 200, retry.data)
            provider.assert_called_once()
        self.assertEqual(Deal.objects.count(), 1)
        self.assertEqual(OpportunityAuditEvent.objects.count(), 1)
        snapshot = OpportunityAuditEvent.objects.get().data['reviewed_email_analysis']
        self.assertEqual(snapshot['pr_reference'], '70059386')
        self.assertEqual(snapshot['ai_review']['proposal']['deadline_at'], '2026-10-02T01:59:00+04:00')
        self.assertEqual(snapshot['ai_review']['model'], 'gpt-4o-mini')
        self.assertTrue(snapshot['ai_review']['analysis_id'])
        self.assertEqual(str(Deal.objects.get().submission_due_date), '2026-10-02')

    def test_source_digest_excludes_model_output_but_token_binds_reviewed_proposal(self):
        with patch('apps.sales.email_ai_analysis.enhance_email_analysis', side_effect=self.proposal):
            detail = self.review().data
        changed = deepcopy(detail)
        changed['extracted_information']['deadline_at'] = '2028-01-01T00:00:00Z'
        self.assertEqual(_source_digest(detail), _source_digest(changed))
        review = signing.loads(detail['source_token'], salt=REVIEW_SALT)
        self.assertEqual(review['analysis']['deadline_at'], '2026-10-02T01:59:00+04:00')

    def test_saved_list_defers_provider_detail_enables_it_and_posts_snapshot(self):
        intake = self.saved()
        url = f'/api/v1/sales/email-intakes/{intake.pk}/'
        with patch('apps.sales.email_ai_analysis.enhance_email_analysis', side_effect=self.proposal) as enhance:
            listed = self.client.get('/api/v1/sales/email-intakes/')
            self.assertEqual(listed.status_code, 200)
            enhance.assert_not_called()
            enhance.reset_mock()
            detail = self.client.get(url)
            self.assertEqual(detail.status_code, 200)
            self.assertTrue(enhance.call_args.kwargs['allow_provider'])
            self.assertTrue(detail.data['source_token'])
            enhance.reset_mock()
            response = self.client.post(url + 'convert-to-opportunity/', {
                **self.fields, 'source_token': detail.data['source_token'],
            }, format='json')
            self.assertEqual(response.status_code, 201, response.data)
            self.assertTrue(all(call.kwargs['allow_provider'] is False for call in enhance.call_args_list))
        self.assertEqual(OpportunityAuditEvent.objects.get().data['reviewed_email_analysis']['deadline_at'],
                         '2026-10-02T01:59:00+04:00')

    def test_saved_changed_source_rejects_token_without_business_write(self):
        intake = self.saved()
        url = f'/api/v1/sales/email-intakes/{intake.pk}/'
        with patch('apps.sales.email_ai_analysis.enhance_email_analysis', side_effect=self.proposal):
            token = self.client.get(url).data['source_token']
        SalesEmailIntake.objects.filter(pk=intake.pk).update(body_preview='Changed source evidence')
        response = self.client.post(url + 'convert-to-opportunity/', {
            **self.fields, 'source_token': token,
        }, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertFalse(Deal.objects.exists())
        self.assertFalse(OpportunityAuditEvent.objects.exists())

    def test_saved_legacy_manual_conversion_has_no_invented_ai_provenance(self):
        intake = self.saved()
        with patch('apps.sales.email_ai_analysis.enhance_email_analysis', side_effect=self.proposal) as enhance:
            response = self.client.post(f'/api/v1/sales/email-intakes/{intake.pk}/convert-to-opportunity/',
                                        self.fields, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(OpportunityAuditEvent.objects.get().data['reviewed_email_analysis'], {})
        self.assertTrue(all(call.kwargs['allow_provider'] is False for call in enhance.call_args_list))

    def test_distinct_reminder_same_client_and_source_tender_conflicts(self):
        with patch('apps.sales.email_ai_analysis.enhance_email_analysis', side_effect=self.proposal):
            payload = self.payload()
            payload['client_reference'] = 'Tender_38669'
            first = self.post(payload)
            self.assertEqual(first.status_code, 201, first.data)
            intake = self.saved()
            url = f'/api/v1/sales/email-intakes/{intake.pk}/'
            token = self.client.get(url).data['source_token']
            second = self.client.post(url + 'convert-to-opportunity/', {
                **self.fields, 'client_reference': 'Tender_38669', 'source_token': token,
            }, format='json')
        self.assertEqual(second.status_code, 409, second.data)
        self.assertEqual(str(second.data['code']), 'email_tender_already_exists')
        self.assertEqual(Deal.objects.count(), 1)
        self.assertEqual(OpportunityAuditEvent.objects.count(), 1)
        intake.refresh_from_db()
        self.assertIsNone(intake.opportunity_id)

    def test_mutable_opportunity_fields_cannot_erase_recorded_tender_identity(self):
        with patch('apps.sales.email_ai_analysis.enhance_email_analysis', side_effect=self.proposal):
            payload = self.payload()
            payload['client_reference'] = 'Tender_38669'
            self.assertEqual(self.post(payload).status_code, 201)
            Deal.objects.update(client_reference='Edited reference', custom_fields={
                'email_tender_identity': {'source_portal': 'Different portal'},
            })
            intake = self.saved()
            url = f'/api/v1/sales/email-intakes/{intake.pk}/'
            token = self.client.get(url).data['source_token']
            response = self.client.post(url + 'convert-to-opportunity/', {
                **self.fields, 'client_reference': 'Tender_38669', 'source_token': token,
            }, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(Deal.objects.count(), 1)

    def test_known_different_portals_may_use_same_reference(self):
        with patch('apps.sales.email_ai_analysis.enhance_email_analysis', side_effect=self.proposal):
            payload = self.payload()
            payload['client_reference'] = 'Tender_38669'
            self.assertEqual(self.post(payload).status_code, 201)
        intake = self.saved()
        url = f'/api/v1/sales/email-intakes/{intake.pk}/'

        def other_portal(*args, **kwargs):
            result = self.proposal(*args, **kwargs)
            result['extracted_information']['source_portal'] = 'Separate procurement portal'
            return result

        with patch('apps.sales.email_ai_analysis.enhance_email_analysis', side_effect=other_portal):
            token = self.client.get(url).data['source_token']
            response = self.client.post(url + 'convert-to-opportunity/', {
                **self.fields, 'client_reference': 'Tender_38669', 'source_token': token,
            }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Deal.objects.count(), 2)

    def test_denied_detail_does_not_invoke_ai(self):
        self.deny('sales_email_intake', 'read')
        with patch('apps.sales.email_ai_analysis.enhance_email_analysis') as enhance:
            response = self.client.get(self.detail_url, {'message_id': MESSAGE['id']})
        self.assertEqual(response.status_code, 403)
        enhance.assert_not_called()

    def test_oversized_snapshot_is_not_silently_truncated(self):
        with self.assertRaises(ValidationError):
            reviewed_analysis_snapshot({'ai_review': {'text': 'x' * MAX_SNAPSHOT_BYTES}})
