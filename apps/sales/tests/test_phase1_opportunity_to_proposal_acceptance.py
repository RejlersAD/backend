"""Synthetic manual-intake acceptance path through an approved review draft."""
from datetime import date, timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

import pymupdf
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.rbac.route_guard import secure_module_endpoints
from apps.sales.models import Client, Deal, OpportunityAuditEvent, Quote
from apps.sales.private_attachments import upload_private_file
from apps.sales.views import DealViewSet, QuoteViewSet
from .access_fixtures import grant_sales_actions


router = DefaultRouter()
router.register('deals', DealViewSet, basename='phase1-acceptance-deals')
router.register('quotes', QuoteViewSet, basename='phase1-acceptance-quotes')
urlpatterns = [path('api/v1/sales/', include(router.urls))]
secure_module_endpoints(urlpatterns)


def _rft_pdf():
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), (
            'RFT No. 12345678\nAppendix 4\n'
            'Project methodology and execution strategy\n'
            'The contractor shall submit a technical method statement.'
        ))
        return document.tobytes()


@override_settings(
    ROOT_URLCONF=__name__,
    RADAI_BUSINESS_APPROVAL_ROUTES={},
    SALES_BID_DECISION_RBAC_FALLBACK_ENABLED=True,
    SALES_EMAIL_AI_ENABLED=True,
    SALES_EMAIL_AI_PROVIDER='openai',
    SALES_EMAIL_AI_MODEL='synthetic-phase1-acceptance',
    SALES_EMAIL_AI_API_KEY='synthetic-phase1-acceptance-key',
    SALES_EMAIL_AI_TIMEOUT_SECONDS=12,
    SALES_WORKSPACE_ENABLED=False,
)
class OpportunityToProposalAcceptanceTests(TestCase):
    def setUp(self):
        self.storage = TemporaryDirectory(prefix='sales-phase1-acceptance-')
        self.addCleanup(self.storage.cleanup)
        self.settings = override_settings(SALES_ATTACHMENT_ROOT=self.storage.name)
        self.settings.enable()
        self.addCleanup(self.settings.disable)

        actor = get_user_model().objects.create_user(
            'phase1-sales-acceptance', email='phase1-sales@example.test',
        )
        reviewer = get_user_model().objects.create_user(
            'phase1-reviewer-acceptance', email='phase1-reviewer@example.test',
        )
        approver = get_user_model().objects.create_user(
            'phase1-approver-acceptance', email='phase1-approver@example.test',
        )
        for user in (actor, reviewer, approver):
            grant_sales_actions(user, 'sales', 'sales_clients', 'sales_opportunities', 'sales_proposals')
        self.client_record = Client.objects.create(
            client_code='PHASE1-E2E-CLIENT', company_name='Synthetic acceptance client',
            industry_type='other', account_manager=actor, status='prospect',
            new_proposals_permitted=False,
        )
        self.actor = actor
        self.reviewer = reviewer
        self.approver = approver
        self.api = APIClient()
        self.api.force_authenticate(actor)
        self.reviewer_api = APIClient()
        self.reviewer_api.force_authenticate(reviewer)
        self.approver_api = APIClient()
        self.approver_api.force_authenticate(approver)
        self.provider = patch(
            'apps.sales.proposal_draft_ai.analyze_proposal_draft_sources',
            side_effect=self._suggest,
        )
        self.provider_mock = self.provider.start()
        self.addCleanup(self.provider.stop)
        self.credentials = patch('apps.core.ai_credentials._provider_record', return_value=None)
        self.credentials.start()
        self.addCleanup(self.credentials.stop)

    @staticmethod
    def _suggest(payload, schema, **kwargs):
        facts = payload['facts']
        source_text = facts['source_1']
        return {
            'status': 'completed',
            'proposal': {
                'field': payload['field'],
                'text': 'Proposed technical method, pending engineering review.',
                'evidence': [{'field': 'source_1', 'excerpt': source_text[:180]}],
            },
        }

    def test_manual_intake_to_human_approved_submission_preserves_provenance(self):
        created = self.api.post('/api/v1/sales/deals/', {
            'registration_request_id': str(uuid4()),
            'deal_name': 'Synthetic RFT technical proposal acceptance',
            'client': str(self.client_record.pk),
            'opportunity_type': 'rfq',
            'submission_due_date': str(date.today() + timedelta(days=30)),
        }, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        deal = Deal.objects.get(pk=created.data['id'])

        qualified = self.api.post(f'/api/v1/sales/deals/{deal.pk}/submit-qualification/', {}, format='json')
        self.assertEqual(qualified.status_code, 200, qualified.data)
        decision = self.api.post(
            f'/api/v1/sales/deals/{deal.pk}/bid-decision/',
            {'decision': 'bid', 'reason': 'Human decision based on reviewed capacity and scope.'},
            format='json',
        )
        self.assertEqual(decision.status_code, 200, decision.data)
        self.assertEqual(Deal.objects.get(pk=deal.pk).bid_decision, 'bid')

        source, _ = upload_private_file(
            deal, self.actor, 'tender',
            SimpleUploadedFile('Request for Tender 12345678.pdf', _rft_pdf(), content_type='application/pdf'),
            uuid4(),
        )
        source_id = source['id'][6:]
        catalog = self.api.get(
            f'/api/v1/sales/deals/{deal.pk}/proposal-draft-sources/',
            {'search': '12345678'},
        )
        self.assertEqual(catalog.status_code, 200, catalog.data)
        self.assertEqual(catalog.data['sources'][0]['id'], source_id)

        suggestion = self.api.post(
            f'/api/v1/sales/deals/{deal.pk}/proposal-draft-field/',
            {'field': 'scope', 'text': '', 'source_file_ids': [source_id]},
            format='json',
        )
        self.assertEqual(suggestion.status_code, 200, suggestion.data)
        provenance = suggestion.data['source_context']
        self.assertEqual(provenance['sources'][0]['id'], source_id)
        self.assertEqual(provenance['evidence'][0]['field'], 'source_1')
        self.assertEqual(Quote.objects.count(), 0)
        self.assertEqual(Deal.objects.get(pk=deal.pk).bid_decision, 'bid')
        self.assertEqual(self.provider_mock.call_count, 1)

        started = self.api.post('/api/v1/sales/quotes/start-draft/', {'deal': str(deal.pk)}, format='json')
        self.assertEqual(started.status_code, 201, started.data)
        quote_id = started.data['id']
        quote_url = f'/api/v1/sales/quotes/{quote_id}/'
        blank_approval = self.api.post(quote_url + 'approve/', {}, format='json')
        self.assertEqual(blank_approval.status_code, 400, blank_approval.data)
        self.assertIsNone(Quote.objects.get(pk=quote_id).total_amount)

        saved = self.api.patch(quote_url, {
            'scope': suggestion.data['text'],
            'deliverables': ['Technical method statement'],
            'assumptions': ['Client inputs remain subject to confirmation.'],
            'exclusions': ['Construction is excluded pending agreement.'],
            'estimated_hours': {'total': '40'},
            'subtotal': '12000.00',
            'total_amount': '12000.00',
            'estimated_cost': '8000.00',
            'valid_until': str(date.today() + timedelta(days=60)),
            'currency': 'AED',
        }, format='json')
        self.assertEqual(saved.status_code, 200, saved.data)

        basis = self.api.get(f'/api/v1/sales/quotes/{quote_id}/technical-basis/')
        self.assertEqual(basis.status_code, 200, basis.data)
        self.assertTrue(any(item['id'] == 'methodology' for item in basis.data['requirements']))
        self.assertTrue(any(item['value'] == '12345678' for item in basis.data['references']))

        draft_pdf = self.api.post(
            f'/api/v1/sales/quotes/{quote_id}/technical-proposal-pdf/',
            {'appendix_file_ids': []}, format='json',
        )
        self.assertEqual(draft_pdf.status_code, 200)
        pdf_bytes = b''.join(draft_pdf.streaming_content)
        self.assertTrue(pdf_bytes.startswith(b'%PDF-'))

        review_upload, _ = upload_private_file(
            deal, self.actor, 'proposal',
            SimpleUploadedFile('Acceptance technical draft.pdf', pdf_bytes, content_type='application/pdf'),
            uuid4(),
        )
        review_url = quote_url + 'review/'
        review_state = self.api.get(review_url)
        self.assertEqual(review_state.status_code, 200, review_state.data)
        bound = self.api.post(review_url + 'documents/', {
            'file_id': review_upload['id'],
            'request_id': str(uuid4()),
            'expected_document_id': None,
            'expected_quote_updated_at': review_state.data['quote']['updated_at'],
        }, format='json')
        self.assertEqual(bound.status_code, 201, bound.data)

        preapproval_submission = self.api.post(
            quote_url + 'send_to_client/',
            {'recipient': 'client@example.test', 'evidence': 'Synthetic delivery record'},
            format='json',
        )
        self.assertEqual(preapproval_submission.status_code, 400, preapproval_submission.data)

        sent = self.api.post(f'/api/v1/sales/quotes/{quote_id}/send-internal-review/', {
            'expected_updated_at': saved.data['updated_at'],
        }, format='json')
        self.assertEqual(sent.status_code, 200, sent.data)
        self.assertEqual(sent.data['quote']['status'], 'internal_review')

        review_state = self.reviewer_api.get(review_url)
        document_id = bound.data['document_id']
        reviewed = self.reviewer_api.post(
            review_url + f'documents/{document_id}/submit/',
            {
                'outcome': 'reviewed',
                'note': 'Synthetic internal review completed by a separate reviewer.',
                'request_id': str(uuid4()),
                'expected_version': review_state.data['selected_document']['feedback_version'],
            },
            format='json',
        )
        self.assertIn(reviewed.status_code, (200, 201), reviewed.data)
        review_state = self.reviewer_api.get(review_url)
        self.assertTrue(any(
            item['actor']['id'] == str(self.reviewer.pk)
            for item in review_state.data['submissions']
        ))

        approved = self.approver_api.post(quote_url + 'approve/', {}, format='json')
        self.assertEqual(approved.status_code, 200, approved.data)
        final_quote = Quote.objects.get(pk=quote_id)
        self.assertEqual(final_quote.status, 'ready_to_submit')
        self.assertEqual(final_quote.approved_by_id, self.approver.pk)
        self.assertTrue(final_quote.approved_at)
        self.assertEqual(final_quote.approval_history[-1]['actor_id'], str(self.approver.pk))
        self.assertEqual(
            OpportunityAuditEvent.objects.filter(
                opportunity=deal, event_type='proposal_revision_approved',
            ).count(),
            1,
        )

        missing_recipient = self.approver_api.post(quote_url + 'send_to_client/', {}, format='json')
        self.assertEqual(missing_recipient.status_code, 400, missing_recipient.data)
        submitted = self.approver_api.post(quote_url + 'send_to_client/', {
            'recipient': 'client@example.test',
            'evidence': 'Synthetic manual submission reference',
        }, format='json')
        self.assertEqual(submitted.status_code, 200, submitted.data)
        final_quote.refresh_from_db()
        self.assertEqual(final_quote.status, 'submitted')
        self.assertEqual(final_quote.submission_recipient, 'client@example.test')
        self.assertEqual(len(final_quote.submitted_version_hash), 64)
        self.assertEqual(
            OpportunityAuditEvent.objects.filter(
                opportunity=deal, event_type='proposal_revision_issued',
            ).count(),
            1,
        )
