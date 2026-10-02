"""Canonical post-Go preparation rows and explicit proposal creation guards."""
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import include, path
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.notifications.models import Notification
from apps.rbac.models import Organization, RolePermission, UserPermissionOverride, UserProfile
from apps.rbac.route_guard import secure_module_endpoints
from apps.sales.models import BidPreparation, Client, Deal, OpportunityAuditEvent, Quote
from apps.sales.proposal_readiness import require_proposal_creation
from apps.sales.views import DealViewSet, QuoteViewSet
from .access_fixtures import grant_sales_actions
from .test_bid_preparation import PreparationFixtures


router = DefaultRouter()
router.register('quotes', QuoteViewSet, basename='ready-quotes')
router.register('deals', DealViewSet, basename='ready-deals')
urlpatterns = [path('api/v1/sales/', include(router.urls))]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class ProposalReadinessTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.actor = get_user_model().objects.create_user(username='proposal-starter', email='starter@example.test')
        grant_sales_actions(self.actor, 'sales', 'sales_opportunities', 'sales_clients', 'sales_proposals')
        self.customer = Client.objects.create(
            client_code='READY-CLIENT', company_name='Synthetic readiness client',
            industry_type='other', account_manager=self.actor, status='active', new_proposals_permitted=True,
        )
        self.deal = self.make_deal(1)
        self.api = APIClient()
        self.api.force_authenticate(self.actor)
        self.url = '/api/v1/sales/quotes/preparation-opportunities/'

    def make_deal(self, number, **extra):
        deal = Deal.objects.create(**{
            'deal_code': f'Q-READY-{number}', 'deal_name': f'Synthetic readiness {number}',
            'client': self.customer, 'owner': self.actor, 'stage': 'proposal', 'bid_decision': 'bid',
            'opportunity_type': 'rfq', 'currency': 'AED', 'service_categories': ['engineering_design'],
            'submission_due_date': date(2026, 12, 1),
            'stage_entered_at': timezone.now() + timedelta(minutes=number), **extra,
        })
        self._mark_internal_notification_acted(deal)
        self._mark_ceo_gate_approved(deal)
        return deal

    def _mark_internal_notification_acted(self, deal):
        Notification.objects.create(
            recipient=self.actor,
            title=f'Qualification submitted for {deal.deal_code}',
            message='Sales qualification notification acknowledged.',
            type='qualification_submitted',
            status='READ',
            is_read=True,
            metadata={
                'module': 'sales',
                'department': 'sales',
                'event_type': 'qualification_submitted',
                'opportunity_id': str(deal.pk),
            },
        )

    def _mark_ceo_gate_approved(self, deal):
        OpportunityAuditEvent.objects.create(
            opportunity=deal,
            actor=self.actor,
            event_type='ceo_gate_decision',
            from_stage='proposal',
            to_stage='proposal',
            data={'decision': 'approved'},
        )

    def payload(self, **extra):
        return {'quote_number': 'READY-PROP-1', 'deal': str(self.deal.pk), 'client': str(self.customer.pk),
                'subtotal': '100.00', 'total_amount': '100.00', 'valid_until': '2027-01-01',
                'currency': 'AED', 'status': 'draft', **extra}

    def create(self, **extra):
        return self.api.post('/api/v1/sales/quotes/', self.payload(**extra), format='json')

    def revoke(self, module, action):
        RolePermission.objects.filter(permission__module__code=module, permission__action=action).delete()

    def test_go_rows_appear_without_quote_creation_or_fabricated_commercial_values(self):
        before = Deal.objects.filter(pk=self.deal.pk).values().get()
        response = self.api.get(self.url, {'pending_only': 'true'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        row = response.data['results'][0]
        self.assertEqual(row['id'], str(self.deal.pk))
        self.assertEqual(row['deal_code'], self.deal.deal_code)
        self.assertEqual(row['client'], str(self.customer.pk))
        self.assertEqual(row['client_name'], self.customer.company_name)
        self.assertEqual(row['opportunity_type'], 'rfq')
        self.assertTrue(row['can_create_proposal'])
        self.assertFalse(row['has_proposal'])
        self.assertIsNone(row['blocked_reason'])
        for field in ('quote_number', 'version', 'total_amount', 'estimated_cost', 'valid_until'):
            self.assertNotIn(field, row)
        self.assertEqual(Deal.objects.filter(pk=self.deal.pk).values().get(), before)
        self.assertFalse(Quote.objects.exists())
        self.assertFalse(BidPreparation.objects.exists())
        self.assertFalse(OpportunityAuditEvent.objects.exists())

    def test_only_recorded_go_in_proposal_stage_is_listed(self):
        conditional = self.make_deal(2, bid_decision='conditional_bid')
        self.make_deal(3, bid_decision='pending')
        self.make_deal(4, bid_decision='no_bid')
        self.make_deal(5, stage='qualified')
        self.make_deal(6, stage='negotiation')
        rows = self.api.get(self.url).data['results']
        self.assertEqual({row['id'] for row in rows}, {str(self.deal.pk), str(conditional.pk)})

    def test_pagination_and_search_are_scoped_to_full_server_candidate_set(self):
        other_client = Client.objects.create(
            client_code='READY-LATER', company_name='Later page client', industry_type='other',
            account_manager=self.actor, status='prospect', new_proposals_permitted=True,
        )
        second = self.make_deal(2, client=other_client)
        first_page = self.api.get(self.url, {'page_size': 1})
        self.assertEqual(first_page.status_code, 200, first_page.data)
        self.assertEqual(first_page.data['count'], 2)
        self.assertEqual(first_page.data['results'][0]['id'], str(second.pk))
        self.assertTrue(first_page.data['results'][0]['can_create_proposal'])
        second_page = self.api.get(first_page.data['next'])
        self.assertEqual(second_page.data['results'][0]['id'], str(self.deal.pk))
        self.assertIsNone(second_page.data['next'])
        self.assertTrue(second_page.data['previous'].startswith(self.url))
        matches = self.api.get(self.url, {'search': 'Later page client'}).data['results']
        self.assertEqual([row['id'] for row in matches], [str(second.pk)])

    def test_invalid_filters_fail_clearly_and_empty_first_page_is_valid(self):
        for query in ({'page': '0'}, {'page_size': 501}, {'page_size': '1.5'},
                      {'pending_only': 'yes'}, {'search': 'x' * 201}, {'stage': 'lead'}):
            with self.subTest(query=query):
                self.assertEqual(self.api.get(self.url, query).status_code, 400)
        self.assertEqual(self.api.get(self.url, {'page': 2}).status_code, 404)
        empty = self.api.get(self.url, {'search': 'unmatched'})
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.data, {'count': 0, 'next': None, 'previous': None, 'results': []})

    def test_active_and_prospect_clients_can_prepare_without_status_mutation(self):
        for status in ('active', 'prospect'):
            with self.subTest(status=status):
                Client.objects.filter(pk=self.customer.pk).update(status=status)
                row = self.api.get(self.url).data['results'][0]
                self.assertTrue(row['can_create_proposal'])
                result = self.create(quote_number=f'READY-{status}')
                self.assertEqual(result.status_code, 201, result.data)
                self.assertEqual(result.data['issue_date'], timezone.localdate().isoformat())
                self.customer.refresh_from_db()
                self.assertEqual(self.customer.status, status)

    def test_blocked_client_still_appears_with_reason_and_cannot_create(self):
        for changes in ({'status': 'inactive'}, {'status': 'blacklisted'},
                        {'status': 'prospect', 'new_proposals_permitted': False}):
            with self.subTest(changes=changes):
                Client.objects.filter(pk=self.customer.pk).update(**changes)
                row = self.api.get(self.url).data['results'][0]
                self.assertFalse(row['can_create_proposal'])
                self.assertIn('not currently permitted', row['blocked_reason'])
                result = self.create()
                self.assertEqual(result.status_code, 400, result.data)
        self.assertFalse(Quote.objects.exists())
        self.assertFalse(OpportunityAuditEvent.objects.exists())

    def test_creation_replaces_pending_row_but_explicit_additional_revisions_remain_possible(self):
        created = self.create()
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(self.api.get(self.url, {'pending_only': 'true'}).data['results'], [])
        row = self.api.get(self.url).data['results'][0]
        self.assertTrue(row['has_proposal'])
        self.assertTrue(row['can_create_proposal'])
        second = self.create(quote_number='READY-PROP-REV2', version=2)
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(Quote.objects.filter(deal=self.deal).count(), 2)
        self.assertEqual(OpportunityAuditEvent.objects.filter(event_type='proposal_revision_created').count(), 2)

    def test_same_unique_proposal_number_retry_cannot_duplicate_quote_or_audit(self):
        self.assertEqual(self.create().status_code, 201)
        retry = self.create()
        self.assertEqual(retry.status_code, 400, retry.data)
        self.assertEqual(Quote.objects.count(), 1)
        self.assertEqual(OpportunityAuditEvent.objects.filter(event_type='proposal_revision_created').count(), 1)

    def test_readers_without_create_grant_see_rows_with_permission_reason(self):
        self.revoke('sales_proposals', 'create')
        row = self.api.get(self.url).data['results'][0]
        self.assertFalse(row['can_create_proposal'])
        self.assertIn('create access', row['blocked_reason'])
        self.assertEqual(self.create().status_code, 403)

    def test_both_read_grants_and_current_profile_are_required(self):
        for module in ('sales_proposals', 'sales_opportunities'):
            permission = self.actor.rbac_profile.roles.first().permissions.get(module__code=module, action='read')
            denied = UserPermissionOverride.objects.create(
                user_profile=self.actor.rbac_profile, permission=permission, allowed=False,
            )
            self.assertEqual(self.api.get(self.url).status_code, 403)
            denied.delete()
        UserProfile.objects.filter(user=self.actor).update(locked_until=timezone.now() + timedelta(hours=1))
        self.assertEqual(self.api.get(self.url).status_code, 403)

    def test_inaccessible_client_is_redacted_and_cannot_be_used_for_search_or_creation(self):
        self.revoke('sales_clients', 'read')
        response = self.api.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        row = response.data['results'][0]
        self.assertIsNone(row['client'])
        self.assertIsNone(row['client_name'])
        self.assertFalse(row['can_create_proposal'])
        self.assertIn('unavailable', row['blocked_reason'])
        self.assertEqual(self.api.get(self.url, {'search': self.customer.company_name}).data['count'], 0)
        self.assertEqual(self.create().status_code, 400)

    def test_foreign_opportunity_and_known_foreign_organization_are_excluded(self):
        outsider = get_user_model().objects.create_user(username='readiness-outsider', email='outside@example.test')
        foreign_org = Organization.objects.create(code='ready-other', name='Other organization')
        UserProfile.objects.create(user=outsider, organization=foreign_org)
        hidden = self.make_deal(2, owner=outsider)
        self.assertNotIn(str(hidden.pk), {row['id'] for row in self.api.get(self.url).data['results']})
        Client.objects.filter(pk=self.customer.pk).update(account_manager=outsider)
        self.assertEqual(self.api.get(self.url).data['results'], [])
        self.assertEqual(self.create().status_code, 404)

    def test_creation_rejects_stage_only_go_and_mismatching_client(self):
        for changes in ({'bid_decision': 'pending'}, {'bid_decision': 'no_bid'},
                        {'bid_decision': 'bid', 'stage': 'qualified'}, {'stage': 'negotiation'}):
            Deal.objects.filter(pk=self.deal.pk).update(**changes)
            result = self.create()
            self.assertEqual(result.status_code, 400, result.data)
        Deal.objects.filter(pk=self.deal.pk).update(stage='proposal', bid_decision='bid')
        other = Client.objects.create(client_code='READY-MISMATCH', company_name='Mismatch', industry_type='other',
                                      account_manager=self.actor, status='active')
        self.assertEqual(self.create(client=str(other.pk)).status_code, 400)
        self.assertFalse(Quote.objects.exists())

    def test_creation_requires_internal_notification_action(self):
        Notification.objects.filter(
            metadata__event_type='qualification_submitted',
            metadata__opportunity_id=str(self.deal.pk),
        ).delete()
        row = self.api.get(self.url).data['results'][0]
        self.assertFalse(row['can_create_proposal'])
        self.assertIn('internal qualification notification', row['blocked_reason'])
        blocked = self.create()
        self.assertEqual(blocked.status_code, 400, blocked.data)
        self._mark_internal_notification_acted(self.deal)
        allowed = self.create()
        self.assertEqual(allowed.status_code, 201, allowed.data)

    def test_creation_requires_ceo_gate_approval(self):
        OpportunityAuditEvent.objects.filter(
            opportunity=self.deal,
            event_type='ceo_gate_decision',
        ).delete()
        row = self.api.get(self.url).data['results'][0]
        self.assertFalse(row['can_create_proposal'])
        self.assertIn('CEO go-ahead', row['blocked_reason'])
        blocked = self.create()
        self.assertEqual(blocked.status_code, 400, blocked.data)
        self._mark_ceo_gate_approved(self.deal)
        allowed = self.create()
        self.assertEqual(allowed.status_code, 201, allowed.data)

    def test_domain_helper_reloads_stale_actor_client_and_bid_state(self):
        Client.objects.filter(pk=self.customer.pk).update(new_proposals_permitted=False)
        with self.assertRaises(ValidationError):
            require_proposal_creation(self.actor, self.deal, self.customer)
        Client.objects.filter(pk=self.customer.pk).update(new_proposals_permitted=True)
        Deal.objects.filter(pk=self.deal.pk).update(bid_decision='pending')
        with self.assertRaises(ValidationError):
            require_proposal_creation(self.actor, self.deal, self.customer)
        Deal.objects.filter(pk=self.deal.pk).update(bid_decision='bid')
        get_user_model().objects.filter(pk=self.actor.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            require_proposal_creation(self.actor, self.deal, self.customer)

    def test_creation_rechecks_client_under_lock_after_initial_serializer_validation(self):
        original = QuoteViewSet.perform_create

        def restrict_client(view, serializer):
            Client.objects.filter(pk=self.customer.pk).update(new_proposals_permitted=False)
            return original(view, serializer)

        with patch.object(QuoteViewSet, 'perform_create', restrict_client):
            response = self.create()
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Quote.objects.exists())
        self.assertFalse(OpportunityAuditEvent.objects.exists())

    def test_required_audit_failure_rolls_back_explicit_creation(self):
        with patch('apps.sales.views._audit', side_effect=RuntimeError('Synthetic audit failure')):
            with self.assertRaisesRegex(RuntimeError, 'Synthetic audit failure'):
                self.create()
        self.assertFalse(Quote.objects.exists())
        self.assertEqual(self.api.get(self.url, {'pending_only': 'true'}).data['count'], 1)

    def test_prospect_allows_editable_narrative_but_does_not_unlock_approved_quote(self):
        Client.objects.filter(pk=self.customer.pk).update(status='prospect')
        created = self.create()
        self.assertEqual(created.status_code, 201, created.data)
        url = f'/api/v1/sales/quotes/{created.data["id"]}/'
        response = self.api.patch(url, {'scope': 'Reviewed proposed approach'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        Quote.objects.filter(pk=created.data['id']).update(issue_date=date(2026, 1, 1))
        replaced = self.api.put(url, self.payload(scope='Reviewed complete draft'), format='json')
        self.assertEqual(replaced.status_code, 200, replaced.data)
        self.assertEqual(replaced.data['issue_date'], '2026-01-01')
        Quote.objects.filter(pk=created.data['id']).update(status='ready_to_submit', approved_at=timezone.now())
        self.assertEqual(self.api.patch(url, {'scope': 'Attempted overwrite'}, format='json').status_code, 409)


@override_settings(ROOT_URLCONF=__name__)
class ProspectPreparationTests(PreparationFixtures, TestCase):
    def test_prospect_can_connect_and_apply_reviewed_preparation_without_status_change(self):
        Client.objects.filter(pk=self.customer.pk).update(status='prospect')
        state = self.api.get(self.bid_url)
        self.assertTrue(state.data['capabilities']['can_attach'])
        self.attach()
        response = self.api.post(self.quote_url + 'prepare/', self.apply_payload(), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.status, 'prospect')
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.scope, 'Reviewed technical solution')
        self.assertIsNone(self.quote.approved_at)
