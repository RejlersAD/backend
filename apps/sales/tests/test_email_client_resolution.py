"""Reviewed customer reuse/creation through live and saved email commands."""

from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.rbac.route_guard import secure_module_endpoints
from apps.sales.intake_views import SalesEmailIntakeViewSet
from apps.sales.models import Client, Contact, Deal, OpportunityAuditEvent, SalesEmailIntake
from apps.sales.views import ClientViewSet, SalesMailboxConnectionViewSet

from . import test_mailbox_opportunities as fixtures
from .test_email_ai_analysis import BODY, SUBJECT, fact, oq_proposal
from .test_mailbox_browsing import graph_response


router = DefaultRouter()
router.register('mailbox-connections', SalesMailboxConnectionViewSet, basename='customer-mailboxes')
router.register('email-intakes', SalesEmailIntakeViewSet, basename='customer-intakes')
router.register('clients', ClientViewSet, basename='customer-directory')
urlpatterns = [path('api/v1/sales/', include(router.urls))]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__, SALES_EMAIL_AI_ENABLED=False,
                   SALES_MICROSOFT_GRAPH_BASE_URL='https://graph.microsoft.com/v1.0')
class EmailClientResolutionTests(TestCase):
    setUp = fixtures.MailboxOpportunityAPITests.setUp
    review = fixtures.MailboxOpportunityAPITests.review
    post = fixtures.MailboxOpportunityAPITests.post
    deny = fixtures.MailboxOpportunityAPITests.deny

    def named_payload(self, name='New Customer LLC'):
        self.message['body'] = {'contentType': 'text', 'content': f'Company name: {name}\nDue date: 21 November 2026'}
        self.network.return_value = graph_response(self.message)
        detail = self.review().data
        payload = {key: value for key, value in self.fields.items() if key != 'client'}
        payload.update(source_token=detail['source_token'], new_client={'company_name': name})
        return payload

    def saved_payload(self, name='Saved Customer LLC'):
        intake = SalesEmailIntake.objects.create(
            source_message_id='new-customer-saved', subject='RFQ for engineering study',
            sender_email='portal@example.test', received_at='2026-09-29T08:00:00Z',
            body_preview=f'Company name: {name}\nDue date: 21 November 2026',
        )
        url = f'/api/v1/sales/email-intakes/{intake.pk}/'
        detail = self.client.get(url)
        self.assertEqual(detail.status_code, 200, detail.data)
        payload = {key: value for key, value in self.fields.items() if key not in {'client', 'message_id'}}
        payload.update(source_token=detail.data['source_token'], new_client={'company_name': name})
        return intake, url + 'convert-to-opportunity/', payload

    def assert_no_new_records(self, clients=2):
        self.assertEqual(Client.objects.count(), clients)
        self.assertFalse(Deal.objects.exists())
        self.assertFalse(OpportunityAuditEvent.objects.exists())

    def test_live_creates_minimal_client_and_retry_never_recreates_after_rename(self):
        payload = self.named_payload()
        self.assertTrue(self.review().data['can_create_client'])
        first = self.post(payload)
        self.assertEqual(first.status_code, 201, first.data)
        client = Client.objects.get(company_name='New Customer LLC')
        self.assertEqual(client.account_manager, self.user)
        self.assertEqual((client.industry_type, client.status, client.verification_status), ('other', 'prospect', 'unverified'))
        self.assertEqual((client.legal_name, client.trading_name, client.email, client.website), ('', '', '', ''))
        self.assertFalse(Contact.objects.exists())
        event = OpportunityAuditEvent.objects.get()
        self.assertEqual(event.data['reviewed_customer_resolution']['client_id'], str(client.pk))
        self.assertTrue(event.data['reviewed_customer_resolution']['created'])
        client.company_name = 'Reviewed replacement name'
        client.save(update_fields=['company_name'])
        repeated = self.post(payload)
        self.assertEqual(repeated.status_code, 200, repeated.data)
        self.assertFalse(repeated.data['created'])
        changed = self.post({**payload, 'estimated_value': '999.00'})
        self.assertEqual(changed.status_code, 409, changed.data)
        self.assertEqual(str(changed.data['code']), 'email_already_converted')
        self.assertEqual(Client.objects.count(), 3)
        self.assertEqual(Deal.objects.count(), 1)
        self.assertEqual(OpportunityAuditEvent.objects.count(), 1)

    def test_unique_legal_name_reuse_requires_no_create_permission(self):
        self.account.legal_name = 'Canonical Buyer LLC'
        self.account.save(update_fields=['legal_name'])
        payload = self.named_payload('Canonical Buyer LLC')
        payload['new_client']['company_name'] = '  canonical   buyer LLC  '
        self.deny('sales_clients', 'create')
        self.assertFalse(self.review().data['can_create_client'])
        response = self.post(payload)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Deal.objects.get().client_id, self.account.pk)
        self.assertEqual(Client.objects.count(), 2)
        self.assertEqual(OpportunityAuditEvent.objects.get().data['reviewed_customer_resolution']['mode'], 'exact_name')

    def test_ambiguous_company_and_trading_names_conflict_without_identity_disclosure(self):
        self.account.trading_name = 'Shared Buyer'
        self.account.save(update_fields=['trading_name'])
        duplicate = Client.objects.create(client_code='DUP-BUYER', company_name='Shared Buyer',
                                          industry_type='other', account_manager=self.user)
        response = self.post(self.named_payload('Shared Buyer'))
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(str(response.data['code']), 'email_customer_conflict')
        self.assertNotIn(str(duplicate.pk), str(response.data))
        self.assert_no_new_records(clients=3)

    def test_hidden_matching_alias_conflicts_without_linking_or_creating(self):
        self.hidden_account.trading_name = 'Hidden Buyer'
        self.hidden_account.save(update_fields=['trading_name'])
        response = self.post(self.named_payload('Hidden Buyer'))
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(str(response.data['code']), 'email_customer_conflict')
        self.assertNotIn(self.hidden_account.company_name, str(response.data))
        self.assertNotIn(str(self.hidden_account.pk), str(response.data))
        self.assert_no_new_records()

    def test_live_name_must_match_signed_source_and_cannot_add_protected_metadata(self):
        payload = self.named_payload()
        self.network.reset_mock()
        for proposed in (
            {'company_name': 'Different Company'},
            {'company_name': 'New Customer LLC', 'status': 'active'},
            {'company_name': 'New Customer LLC', 'email': 'forged@example.test'},
        ):
            with self.subTest(proposed=proposed):
                response = self.post({**payload, 'new_client': proposed})
                self.assertEqual(response.status_code, 400, response.data)
        self.network.assert_not_called()
        self.assert_no_new_records()

    def test_domain_display_name_is_not_explicit_customer_evidence(self):
        self.message['body'] = {'contentType': 'text', 'content': 'Please submit your proposal. Due date: 21 November 2026'}
        self.network.return_value = graph_response(self.message)
        token = self.review().data['source_token']
        payload = {key: value for key, value in self.fields.items() if key != 'client'}
        response = self.post({**payload, 'source_token': token, 'new_client': {'company_name': 'Example'}})
        self.assertEqual(response.status_code, 400, response.data)
        self.assert_no_new_records()

    def test_new_customer_requires_current_create_grant(self):
        payload = self.named_payload()
        self.deny('sales_clients', 'create')
        response = self.post(payload)
        self.assertEqual(response.status_code, 403, response.data)
        self.assert_no_new_records()

    def test_live_changed_source_and_invalid_commercial_fields_create_no_client(self):
        payload = self.named_payload()
        for change in ({'estimated_value': 'invalid'}, {'expected_close_date': '2026-02-30'}):
            response = self.post({**payload, **change})
            self.assertEqual(response.status_code, 400, response.data)
        self.message['body']['content'] += '\nChanged source'
        self.network.return_value = graph_response(self.message)
        response = self.post(payload)
        self.assertEqual(response.status_code, 409, response.data)
        self.assert_no_new_records()

    def test_live_audit_failure_rolls_back_client_and_opportunity(self):
        payload = self.named_payload()
        with patch('apps.sales.mailbox_opportunities.OpportunityAuditEvent.objects.create', side_effect=RuntimeError('synthetic failure')):
            response = self.post(payload)
        self.assertEqual(response.status_code, 503, response.data)
        self.assert_no_new_records()

    def test_saved_source_bound_name_creates_and_retries_atomically(self):
        intake, url, payload = self.saved_payload()
        first = self.client.post(url, payload, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        repeated = self.client.post(url, payload, format='json')
        self.assertEqual(repeated.status_code, 200, repeated.data)
        intake.refresh_from_db()
        created = Client.objects.get(company_name='Saved Customer LLC')
        self.assertEqual(intake.opportunity.client_id, created.pk)
        self.assertEqual(created.legal_name, '')
        self.assertEqual(OpportunityAuditEvent.objects.get().data['reviewed_customer_resolution']['mode'], 'created')
        self.assertEqual(Client.objects.count(), 3)
        self.assertEqual(Deal.objects.count(), 1)
        changed = self.client.post(url, {**payload, 'estimated_value': '888.00'}, format='json')
        self.assertEqual(changed.status_code, 409, changed.data)
        self.assertEqual(str(changed.data['code']), 'email_already_converted')

    def test_saved_signed_fields_are_required_and_blank_description_is_preserved(self):
        intake, url, payload = self.saved_payload()
        for change in ({'deal_name': ''},):
            response = self.client.post(url, {**payload, **change}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
            self.assert_no_new_records()
        response = self.client.post(url, {**payload, 'description': ''}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Deal.objects.get().description, '')

    def test_saved_retry_rechecks_customer_visibility(self):
        intake, url, payload = self.saved_payload()
        self.assertEqual(self.client.post(url, payload, format='json').status_code, 201)
        Client.objects.filter(company_name='Saved Customer LLC').update(account_manager=self.outsider)
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertNotIn('client_details', str(response.data))
        self.assertEqual(Deal.objects.count(), 1)

    def test_saved_initial_registration_keeps_unknown_commercial_fields_blank(self):
        intake, url, payload = self.saved_payload()
        for field in ('estimated_value', 'expected_close_date', 'currency', 'scope_type'):
            payload.pop(field, None)
        payload['opportunity_type'] = 'tender'
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        deal = Deal.objects.get()
        self.assertEqual(deal.deal_code, 'Q-102101')
        self.assertEqual(deal.created_by, self.user)
        self.assertIsNotNone(deal.open_date)
        self.assertIsNone(deal.estimated_value)
        self.assertIsNone(deal.expected_close_date)
        self.assertEqual(deal.currency, '')
        self.assertEqual(deal.scope_type, '')
        self.assertEqual(self.client.post(url, payload, format='json').status_code, 200)
        self.assertEqual(Deal.objects.count(), 1)

    def test_saved_name_mismatch_stale_source_and_invalid_deal_leave_no_client(self):
        intake, url, payload = self.saved_payload()
        for change in ({'new_client': {'company_name': 'Unreviewed Buyer'}}, {'expected_close_date': ''}):
            response = self.client.post(url, {**payload, **change}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
            self.assert_no_new_records()
        intake.body_preview += '\nUpdated buyer instructions'
        intake.save(update_fields=['body_preview'])
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assert_no_new_records()

    def test_saved_audit_failure_rolls_back_new_customer(self):
        intake, url, payload = self.saved_payload()
        with patch('apps.sales.intake_views.OpportunityAuditEvent.objects.create', side_effect=RuntimeError('synthetic failure')):
            with self.assertRaises(RuntimeError):
                self.client.post(url, payload, format='json')
        intake.refresh_from_db()
        self.assertIsNone(intake.opportunity_id)
        self.assert_no_new_records()

    def test_legacy_saved_reuses_unicode_alias_without_create_grant(self):
        self.account.trading_name = 'Caf\u00e9 Buyer LLC'
        self.account.save(update_fields=['trading_name'])
        intake, url, payload = self.saved_payload()
        payload.pop('source_token')
        payload['new_client'] = {'company_name': 'CAFE\u0301   BUYER LLC'}
        self.deny('sales_clients', 'create')
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Deal.objects.get().client_id, self.account.pk)
        self.assertEqual(Client.objects.count(), 2)

    @override_settings(SALES_EMAIL_AI_ENABLED=True, SALES_EMAIL_AI_PROVIDER='anthropic',
                       SALES_EMAIL_AI_MODEL='claude-sonnet-5-5', SALES_EMAIL_AI_API_KEY='synthetic-only')
    def test_all_source_fields_real_validator_create_missing_customer_and_directory_visibility(self):
        Client.objects.all().delete()
        value = 'Estimated contract value: AED 125,000.50'
        award = 'Expected award date: 10 November 2026'
        project = 'Project name: Laboratory Reagents Procurement'
        scope = 'Scope type: Other'
        self.message.update({
            'subject': SUBJECT,
            'from': {'emailAddress': {'name': 'OQ Tawreed', 'address': 'tawreed@oq.com'}},
            'body': {'contentType': 'text', 'content': '\n'.join((BODY, value, award, project, scope))},
            'toRecipients': [{'emailAddress': {'address': self.connection.mailbox_address}}],
        })
        self.network.return_value = graph_response(self.message)
        proposal = oq_proposal()
        proposal['fields'] += [fact('estimated_value', '125000.50', value), fact('currency', 'AED', value),
                               fact('expected_award_date', '2026-11-10', award),
                               fact('project_name', 'Laboratory Reagents Procurement', project), fact('scope_type', 'other', scope)]
        with patch('apps.sales.email_ai_analysis.analyze_email_sources', return_value={
            'status': 'completed', 'proposal': proposal, 'provider': 'anthropic', 'model': 'claude-sonnet-5-5',
        }) as provider:
            detail = self.review().data
            info = detail['extracted_information']
            self.assertEqual(info['ai_review']['status'], 'validated')
            self.assertEqual(info['customer_match']['status'], 'no_match')
            expected = {'organization_name': 'OQ', 'tender_reference': 'Tender_38669',
                        'estimated_value': '125000.50', 'currency': 'AED', 'expected_award_date': '2026-11-10',
                        'due_date': '2026-10-02', 'scope_type': 'other', 'project_name': 'Laboratory Reagents Procurement'}
            for field, value in expected.items():
                self.assertEqual(info[field], value, field)
            payload = {
                'message_id': self.message['id'], 'source_token': detail['source_token'],
                'new_client': {'company_name': info['organization_name']}, 'deal_name': info['project_name'],
                'client_reference': info['tender_reference'], 'estimated_value': info['estimated_value'],
                'currency': info['currency'], 'expected_close_date': info['expected_award_date'],
                'submission_due_date': info['due_date'], 'scope_type': info['scope_type'],
                'description': info['scope_summary'], 'classification_code': 'tender_opportunity',
                'classification_confirmed': True,
            }
            first = self.post(payload)
            self.assertEqual(first.status_code, 201, first.data)
            self.assertEqual(self.post(payload).status_code, 200)
            provider.assert_called_once()
        deal = Deal.objects.get()
        customer = Client.objects.get()
        self.assertEqual(deal.client_id, customer.pk)
        self.assertEqual(deal.deal_name, expected['project_name'])
        self.assertEqual(str(deal.estimated_value), expected['estimated_value'])
        self.assertEqual(str(deal.expected_close_date), expected['expected_award_date'])
        self.assertEqual(str(deal.submission_due_date), expected['due_date'])
        self.assertEqual(deal.client_reference, expected['tender_reference'])
        self.assertEqual(deal.description, info['scope_summary'])
        event = OpportunityAuditEvent.objects.get()
        self.assertEqual(event.data['reviewed_email_analysis']['ai_review']['proposal']['deadline_at'], '2026-10-02T01:59:00+04:00')
        listed = self.client.get('/api/v1/sales/clients/')
        self.assertEqual(listed.status_code, 200, listed.data)
        rows = listed.data['results'] if isinstance(listed.data, dict) else listed.data
        self.assertEqual([str(row['id']) for row in rows], [str(customer.pk)])
        self.assertEqual(self.client.get(f'/api/v1/sales/clients/{customer.pk}/').status_code, 200)
