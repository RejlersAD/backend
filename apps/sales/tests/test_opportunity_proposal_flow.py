"""Guarded HTTP journey using real SQL persistence and recipient inbox actions."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import include, path
from django.utils import timezone
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.notifications.models import Notification
from apps.notifications.views import NotificationViewSet
from apps.procurement.tests.approval_fixtures import set_position
from apps.rbac.route_guard import secure_module_endpoints
from apps.sales.models import Client, Deal, OpportunityAuditEvent, Quote
from apps.sales.views import DealViewSet, QuoteViewSet
from .access_fixtures import grant_sales_actions

router = DefaultRouter()
router.register('deals', DealViewSet, basename='flow-deals')
router.register('quotes', QuoteViewSet, basename='flow-quotes')
urlpatterns = [path('api/v1/sales/', include(router.urls))]
secure_module_endpoints(urlpatterns)
urlpatterns += [path('api/v1/notifications/mark_as_read/',
                     NotificationViewSet.as_view({'post': 'mark_as_read'}))]


@override_settings(ROOT_URLCONF=__name__, RADAI_BUSINESS_APPROVAL_ROUTES={},
                   SALES_BID_DECISION_RBAC_FALLBACK_ENABLED=True,
                   SALES_WORKSPACE_ENABLED=False)
class OpportunityProposalFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        User = get_user_model()
        self.owner = User.objects.create_user('flow-sales', email='sales@flow.example.test')
        self.ceo = User.objects.create_user('flow-ceo', email='ceo@flow.example.test')
        self.outsider = User.objects.create_user('flow-outsider', email='outside@flow.example.test')
        for actor in (self.owner, self.ceo):
            grant_sales_actions(actor, 'sales', 'sales_clients', 'sales_opportunities', 'sales_proposals')
        set_position(self.ceo, 'Chief Executive Officer')
        self.customer = Client.objects.create(
            client_code='FLOW-CLIENT', company_name='Synthetic lifecycle client',
            industry_type='other', account_manager=self.owner, status='prospect',
            new_proposals_permitted=True,
        )
        self.api = APIClient()
        self.api.force_authenticate(self.owner)

    def create_opportunity(self):
        payload = {'registration_request_id': str(uuid4()), 'deal_name': 'Synthetic engineering enquiry',
                   'client': str(self.customer.pk), 'opportunity_type': 'rfq',
                   'submission_due_date': str(timezone.localdate() + timedelta(days=30))}
        response = self.api.post('/api/v1/sales/deals/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        retry = self.api.post('/api/v1/sales/deals/', payload, format='json')
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertEqual(response.data['id'], retry.data['id'])
        return Deal.objects.get(pk=response.data['id'])

    def quote_payload(self, deal):
        return {'quote_number': f'FLOW-{deal.deal_code}', 'deal': str(deal.pk),
                'client': str(self.customer.pk), 'status': 'draft', 'version': 1,
                'valid_until': str(timezone.localdate() + timedelta(days=60)),
                'currency': 'AED', 'subtotal': '1234.50', 'total_amount': '1234.50',
                'scope': 'Reviewed design scope', 'deliverables': ['Design report'],
                'assumptions': ['Client supplies design inputs'], 'exclusions': ['Construction']}

    def create_quote(self, deal):
        return self.api.post('/api/v1/sales/quotes/', self.quote_payload(deal), format='json')

    def journey(self, decision):
        deal = self.create_opportunity()
        base = f'/api/v1/sales/deals/{deal.pk}/'
        self.assertEqual(self.create_quote(deal).status_code, 400)
        response = self.api.post(base + 'submit-qualification/', {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.api.post(base + 'submit-qualification/', {}, format='json').status_code, 400)
        notification = Notification.objects.get(recipient=self.owner, metadata__opportunity_id=str(deal.pk))
        self.assertFalse(notification.is_read)
        self.assertEqual(notification.action_url, f'/sales/opportunities?record={deal.pk}')
        response = self.api.post(base + 'bid-decision/',
                                 {'decision': decision, 'reason': 'Reviewed capacity and design inputs'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.create_quote(deal).status_code, 400)
        self.api.force_authenticate(self.outsider)
        read = self.api.post('/api/v1/notifications/mark_as_read/',
                             {'notification_ids': [notification.pk]}, format='json')
        self.assertEqual(read.status_code, 200, read.data)
        notification.refresh_from_db()
        self.assertFalse(notification.is_read)
        self.assertIn(self.api.post(base + 'ceo-decision/', {'decision': 'go'}, format='json').status_code,
                      (403, 404))
        self.api.force_authenticate(self.owner)
        read = self.api.post('/api/v1/notifications/mark_as_read/',
                             {'notification_ids': [notification.pk]}, format='json')
        self.assertEqual(read.status_code, 200, read.data)
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)
        self.assertEqual(self.api.post(base + 'ceo-decision/', {'decision': 'go'}, format='json').status_code, 403)
        self.assertEqual(self.create_quote(deal).status_code, 400)
        self.api.force_authenticate(self.ceo)
        response = self.api.post(base + 'ceo-decision/', {'decision': 'go'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.api.force_authenticate(self.owner)
        ready = self.api.get('/api/v1/sales/quotes/preparation-opportunities/', {'pending_only': 'true'})
        self.assertEqual(ready.status_code, 200, ready.data)
        row = next(row for row in ready.data['results'] if row['id'] == str(deal.pk))
        self.assertTrue(row['can_create_proposal'], row)
        self.assertFalse(Quote.objects.filter(deal=deal).exists())
        response = self.create_quote(deal)
        self.assertEqual(response.status_code, 201, response.data)
        quote = Quote.objects.get(pk=response.data['id'])
        self.assertEqual((quote.deal_id, quote.client_id, quote.prepared_by_id),
                         (deal.pk, self.customer.pk, self.owner.pk))
        self.assertEqual(quote.status, 'draft')
        self.assertEqual(quote.deliverables, ['Design report'])
        self.assertEqual(self.create_quote(deal).status_code, 400)
        self.assertEqual(Quote.objects.filter(deal=deal).count(), 1)
        saved = self.api.get(f'/api/v1/sales/quotes/{quote.pk}/')
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(saved.data['scope'], 'Reviewed design scope')
        pending = self.api.get('/api/v1/sales/quotes/preparation-opportunities/', {'pending_only': 'true'})
        self.assertNotIn(str(deal.pk), [row['id'] for row in pending.data['results']])
        for event in ('opportunity_created', 'qualification_submitted', 'bid_decision',
                      'ceo_gate_decision', 'proposal_revision_created'):
            self.assertEqual(OpportunityAuditEvent.objects.filter(opportunity=deal, event_type=event).count(), 1)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.status, 'prospect')

    def test_bid_to_saved_draft(self):
        self.journey('bid')

    def test_conditional_bid_to_saved_draft(self):
        self.journey('conditional_bid')


@skipUnless(connection.vendor == 'postgresql', 'Real PostgreSQL row locks required')
@override_settings(ROOT_URLCONF=__name__, SALES_WORKSPACE_ENABLED=False)
class QualificationConcurrencyTests(TransactionTestCase):
    setUp = OpportunityProposalFlowTests.setUp
    create_opportunity = OpportunityProposalFlowTests.create_opportunity

    def test_concurrent_submissions_create_one_review_and_notification(self):
        deal = self.create_opportunity()
        actor_id = self.owner.pk
        barrier = Barrier(2)

        def submit():
            close_old_connections()
            try:
                api = APIClient()
                api.force_authenticate(get_user_model().objects.get(pk=actor_id))
                barrier.wait(timeout=10)
                response = api.post(f'/api/v1/sales/deals/{deal.pk}/submit-qualification/', {}, format='json')
                return response.status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit) for _ in range(2)]
            statuses = [future.result(timeout=30) for future in futures]
        self.assertEqual(sorted(statuses), [200, 400])
        self.assertEqual(OpportunityAuditEvent.objects.filter(
            opportunity=deal, event_type='qualification_submitted').count(), 1)
        self.assertEqual(Notification.objects.filter(metadata__opportunity_id=str(deal.pk)).count(), 1)
