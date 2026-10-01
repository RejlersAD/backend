"""Reviewed pre-award preparation, synthetic domain records and production guards."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import Q
from django.test import TestCase, override_settings
from django.urls import include, path
from django.utils import timezone
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.core.project_models import Project
from apps.planning_intelligence.models import PlanningProject, Schedule, ScheduleVersion, TechnicalProposal
from apps.rbac.models import Organization, RolePermission
from apps.rbac.route_guard import secure_module_endpoints
from apps.sales.models import BidPreparation, BidPreparationCommand, Client, Deal, Quote, QuotePreparationRevision, OpportunityAuditEvent
from apps.sales.views import DealViewSet, QuoteViewSet
from .access_fixtures import grant_sales_actions


router = DefaultRouter()
router.register('deals', DealViewSet, basename='bid-test-deals')
router.register('quotes', QuoteViewSet, basename='bid-test-quotes')
urlpatterns = [path('api/v1/sales/', include(router.urls))]
secure_module_endpoints(urlpatterns)


class PreparationFixtures:
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.actor = get_user_model().objects.create_user(username='bid-author', email='bid@example.test')
        grant_sales_actions(self.actor, 'sales', 'sales_opportunities', 'sales_proposals', 'sales_clients', 'planning_package')
        self.customer = Client.objects.create(client_code='BID-CLIENT', company_name='Synthetic Bid Client',
            industry_type='other', account_manager=self.actor, status='active', new_proposals_permitted=True)
        self.deal = Deal.objects.create(deal_code='Q-BID-1', deal_name='Synthetic bid', client=self.customer,
            owner=self.actor, stage='proposal', bid_decision='bid', project_duration_months=6,
            scope_type='FEED', description='Opportunity scope', currency='AED',
            submission_due_date=date(2026, 11, 1), expected_start_date=date(2027, 1, 1))
        self.quote = Quote.objects.create(quote_number='BID-Q1', deal=self.deal, client=self.customer,
            prepared_by=self.actor, subtotal='100.00', total_amount='120.00', tax_amount='20.00',
            estimated_cost='60.00', currency='AED', valid_until=date(2027, 1, 1), scope='Authored commercial scope',
            assumptions=['Authored assumption'], estimated_hours={'total': '17'})
        self.project = PlanningProject.objects.create(name='Synthetic technical preparation', created_by=self.actor,
            client=self.customer.company_name, duration_months=6, scope_summary='Original planning scope')
        self.schedule = Schedule.objects.create(project=self.project, name='Synthetic plan', code='BID-S1',
            planned_start=date(2027, 1, 1), created_by=self.actor)
        self.version = ScheduleVersion.objects.create(schedule=self.schedule, version=1, created_by=self.actor)
        self.technical = TechnicalProposal.objects.create(project=self.project, schedule_version=self.version,
            proposal_number='TECH-BID-1', revision=1, title='Synthetic technical response', created_by=self.actor,
            sections=[{'key': 'scope', 'title': 'Scope', 'content': 'Reviewed technical solution', 'included': True},
                      {'key': 'assumptions', 'content': 'Client supplies approved inputs', 'included': True}],
            snapshot={'schedule': {'version_id': self.version.pk, 'version': 1},
                      'deliverables': [{'name': 'Design report'}], 'manhours': {'total': 80},
                      'disciplines': [{'name': 'Process', 'activity_count': 2}]})
        self.api = APIClient()
        self.api.force_authenticate(self.actor)
        self.bid_url = f'/api/v1/sales/deals/{self.deal.pk}/bid-preparation/'
        self.quote_url = f'/api/v1/sales/quotes/{self.quote.pk}/'

    def connect_payload(self, **extra):
        state = self.api.get(self.bid_url)
        self.assertEqual(state.status_code, 200, state.data)
        return {'request_id': str(uuid4()), 'expected_token': state.data['expected_token'],
                'mode': 'attach', 'planning_project_id': str(self.project.pk),
                'reason': 'Review existing planning ownership', **extra}

    def attach(self):
        payload = self.connect_payload()
        response = self.api.post(self.bid_url, payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response, payload

    def apply_payload(self, **extra):
        preview = self.api.post(self.quote_url + 'preparation-preview/',
            {'technical_proposal_id': str(self.technical.pk)}, format='json')
        self.assertEqual(preview.status_code, 200, preview.data)
        return {'request_id': str(uuid4()), 'expected_token': preview.data['expected_token'],
                'technical_proposal_id': str(self.technical.pk), 'selected_fields': ['scope'],
                'reason': 'Reviewed exact technical scope', **extra}


@override_settings(ROOT_URLCONF=__name__)
class BidPreparationTests(PreparationFixtures, TestCase):
    def test_read_does_not_create_and_known_source_create_has_no_delivery_project(self):
        state = self.api.get(self.bid_url)
        self.assertTrue(state.data['capabilities']['can_create'])
        self.assertFalse(BidPreparation.objects.exists())
        payload = self.connect_payload(mode='create')
        payload.pop('planning_project_id')
        first = self.api.post(self.bid_url, payload, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        row = BidPreparation.objects.get()
        self.assertEqual(row.planning_project.client, self.customer.company_name)
        self.assertEqual(row.planning_project.scope_summary, self.deal.description)
        self.assertEqual(row.planning_project.effective_date, self.deal.expected_start_date)
        self.assertEqual(row.planning_project.duration_months, Decimal('6'))
        self.assertEqual(row.source_basis['duration_basis'], 'opportunity')
        self.assertFalse(Project.objects.exists())
        self.assertTrue(self.api.post(self.bid_url, payload, format='json').data['replayed'])
        self.assertEqual(BidPreparationCommand.objects.count(), 1)

    def test_unknown_duration_requires_explicit_positive_assumption(self):
        self.deal.project_duration_months = None
        self.deal.save()
        payload = self.connect_payload(mode='create')
        payload.pop('planning_project_id')
        self.assertTrue(self.api.get(self.bid_url).data['requires_duration'])
        for value in (None, '0', '-1', 'NaN', 'Infinity', '10000', '1.00001'):
            bad = {**payload, **({'duration_months': value} if value is not None else {})}
            response = self.api.post(self.bid_url, bad, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(BidPreparation.objects.exists())
        response = self.api.post(self.bid_url, {**payload, 'duration_months': '3.5'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(BidPreparation.objects.get().source_basis['duration_basis'], 'reviewer_assumption')

    def test_attach_retry_and_conflicting_reuse_are_atomic(self):
        first, payload = self.attach()
        self.assertEqual(first.data['bid_preparation']['connection']['planning_project']['id'], str(self.project.pk))
        self.assertTrue(self.api.post(self.bid_url, payload, format='json').data['replayed'])
        self.assertEqual(self.api.post(self.bid_url, {**payload, 'reason': 'Different'}, format='json').status_code, 409)
        self.assertEqual(self.api.post(self.bid_url, {**payload, 'request_id': str(uuid4())}, format='json').status_code, 409)
        self.assertEqual(OpportunityAuditEvent.objects.filter(event_type='bid_preparation_connected').count(), 1)

    def test_preview_and_explicit_selected_apply_preserve_commercial_and_authored_fields(self):
        self.attach()
        payload = self.apply_payload()
        first = self.api.post(self.quote_url + 'prepare/', payload, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.scope, 'Reviewed technical solution')
        self.assertEqual(self.quote.assumptions, ['Authored assumption'])
        self.assertEqual(self.quote.estimated_hours, {'total': '17'})
        self.assertEqual(self.quote.total_amount, Decimal('120'))
        self.assertEqual(self.quote.tax_amount, Decimal('20'))
        self.assertEqual(self.quote.estimated_cost, Decimal('60'))
        self.assertEqual(self.quote.currency, 'AED')
        self.assertEqual(self.quote.status, 'draft')
        self.assertIsNone(self.quote.approved_at)
        capture = QuotePreparationRevision.objects.get()
        self.assertEqual(capture.before_fields, {'scope': 'Authored commercial scope'})
        self.assertEqual(capture.applied_fields, {'scope': 'Reviewed technical solution'})
        self.assertEqual(capture.technical_proposal_id, self.technical.pk)
        replay = self.api.post(self.quote_url + 'prepare/', payload, format='json')
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertTrue(replay.data['replayed'])
        self.assertEqual(QuotePreparationRevision.objects.count(), 1)

    def test_quote_and_source_staleness_prevent_capture(self):
        self.attach()
        payload = self.apply_payload()
        self.quote.notes = 'Concurrent author input'
        self.quote.save()
        self.assertEqual(self.api.post(self.quote_url + 'prepare/', payload, format='json').status_code, 409)
        payload = self.apply_payload()
        self.technical.sections = [{'key': 'scope', 'content': 'Changed technical scope'}]
        self.technical.save()
        self.assertEqual(self.api.post(self.quote_url + 'prepare/', payload, format='json').status_code, 409)
        self.assertFalse(QuotePreparationRevision.objects.exists())

    def test_saved_history_survives_source_change_and_stale_replay_rejected(self):
        self.attach()
        payload = self.apply_payload()
        self.assertEqual(self.api.post(self.quote_url + 'prepare/', payload, format='json').status_code, 200)
        before = QuotePreparationRevision.objects.get().evidence
        self.technical.title = 'Changed later'
        self.technical.save()
        state = self.api.get(self.quote_url + 'preparation/').data
        self.assertEqual(state['history'][0]['source_state'], 'changed')
        self.assertEqual(self.api.post(self.quote_url + 'prepare/', payload, format='json').status_code, 409)
        self.assertEqual(QuotePreparationRevision.objects.get().evidence, before)

    def test_scope_and_source_module_denial_including_replay(self):
        self.attach()
        payload = self.apply_payload()
        self.assertEqual(self.api.post(self.quote_url + 'prepare/', payload, format='json').status_code, 200)
        RolePermission.objects.filter(permission__module__code='planning_package', permission__action='read').delete()
        self.assertEqual(self.api.post(self.quote_url + 'prepare/', payload, format='json').status_code, 403)
        state = self.api.get(self.quote_url + 'preparation/').data
        self.assertEqual(state['history'][0]['source_state'], 'unavailable')
        self.assertEqual(state['history'][0]['source'], {})
        self.assertIsNone(state['connection'])

    def test_foreign_organization_and_unrelated_delivery_project_are_denied(self):
        outsider = get_user_model().objects.create_user(username='foreign-bid', email='foreign-bid@example.test')
        grant_sales_actions(outsider, 'planning_package')
        org = Organization.objects.create(code='foreign-bid', name='Other organization')
        profile = outsider.rbac_profile
        profile.organization = org
        profile.save(update_fields=['organization'])
        self.project.created_by = outsider
        self.project.save()
        self.assertEqual(self.api.post(self.bid_url, self.connect_payload(), format='json').status_code, 404)
        self.project.created_by = self.actor
        self.project.enterprise_project = Project.objects.create(code='UNRELATED', name='Unrelated', owner=self.actor, client=self.customer)
        self.project.save()
        self.assertEqual(self.api.post(self.bid_url, self.connect_payload(), format='json').status_code, 404)

    def test_bid_gate_denies_unapproved_preparation(self):
        self.deal.bid_decision = 'pending'
        self.deal.save()
        self.assertFalse(self.api.get(self.bid_url).data['capabilities']['can_attach'])
        self.assertEqual(self.api.post(self.bid_url, self.connect_payload(), format='json').status_code, 409)

    def test_generic_decision_and_identity_writes_cannot_bypass_commands(self):
        self.attach()
        for data in ({'approved_by': self.actor.pk}, {'approved_at': timezone.now().isoformat()},
                     {'approval_history': [{'decision': 'approved'}]}, {'status': 'ready_to_submit'},
                     {'submitted_version_hash': 'f' * 64}, {'version': 4}, {'quote_number': 'REASSIGNED'}):
            result = self.api.patch(self.quote_url, data, format='json')
            self.assertIn(result.status_code, (400, 403, 409), result.data)
        replacement = Client.objects.create(client_code='REPLACEMENT', company_name='Replacement', industry_type='other', account_manager=self.actor)
        result = self.api.patch(f'/api/v1/sales/deals/{self.deal.pk}/', {'client': str(replacement.pk)}, format='json')
        self.assertEqual(result.status_code, 400, result.data)
        self.quote.refresh_from_db()
        self.assertIsNone(self.quote.approved_at)
        self.assertEqual(self.quote.version, 1)

    def test_approved_quote_remains_readable_but_cannot_be_changed(self):
        self.attach()
        self.quote.approved_at = timezone.now()
        self.quote.approved_by = self.actor
        self.quote.status = 'ready_to_submit'
        self.quote.save()
        payload = self.apply_payload()
        self.assertEqual(self.api.post(self.quote_url + 'prepare/', payload, format='json').status_code, 409)
        self.assertEqual(self.api.patch(self.quote_url, {'scope': 'Overwrite'}, format='json').status_code, 409)
        self.assertFalse(self.api.get(self.quote_url + 'preparation/').data['capabilities']['can_prepare'])

    def test_audit_failure_rolls_back_connection_and_capture(self):
        payload = self.connect_payload()
        with patch('apps.sales.bid_preparation._audit', side_effect=RuntimeError('synthetic audit failure')):
            with self.assertRaises(RuntimeError):
                self.api.post(self.bid_url, payload, format='json')
        self.assertFalse(BidPreparation.objects.exists())
        self.attach()
        payload = self.apply_payload()
        with patch('apps.sales.bid_preparation._audit', side_effect=RuntimeError('synthetic audit failure')):
            with self.assertRaises(RuntimeError):
                self.api.post(self.quote_url + 'prepare/', payload, format='json')
        self.assertFalse(QuotePreparationRevision.objects.exists())
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.scope, 'Authored commercial scope')

    def test_empty_unknown_and_repeated_fields_are_rejected(self):
        self.attach()
        payload = self.apply_payload()
        for fields in ([], ['scope', 'scope'], ['total_amount'], ['risks']):
            result = self.api.post(self.quote_url + 'prepare/', {**payload, 'selected_fields': fields}, format='json')
            self.assertEqual(result.status_code, 400, result.data)
        self.assertFalse(QuotePreparationRevision.objects.exists())

    def test_blocked_client_cannot_use_preparation_to_bypass_draft_eligibility(self):
        payload = self.connect_payload()
        self.customer.new_proposals_permitted = False
        self.customer.save(update_fields=['new_proposals_permitted'])
        self.assertEqual(self.api.post(self.bid_url, payload, format='json').status_code, 400)
        self.assertFalse(self.api.get(self.bid_url).data['capabilities']['can_attach'])
        self.customer.new_proposals_permitted = True
        self.customer.save(update_fields=['new_proposals_permitted'])
        self.attach()
        payload = self.apply_payload()
        self.customer.status = 'inactive'
        self.customer.save(update_fields=['status'])
        self.assertEqual(self.api.post(self.quote_url + 'prepare/', payload, format='json').status_code, 400)
        self.assertFalse(self.api.get(self.quote_url + 'preparation/').data['capabilities']['can_prepare'])

    def test_direct_lineage_mutation_is_detected_and_captures_cannot_be_deleted(self):
        self.attach()
        self.assertEqual(self.api.post(self.quote_url + 'prepare/', self.apply_payload(), format='json').status_code, 200)
        self.assertEqual(self.api.delete(self.quote_url).status_code, 409)
        replacement = Client.objects.create(client_code='DIRECT', company_name='Changed client',
            industry_type='other', status='active', account_manager=self.actor)
        Deal.objects.filter(pk=self.deal.pk).update(client=replacement)
        Quote.objects.filter(pk=self.quote.pk).update(client=replacement)
        result = self.api.post(self.quote_url + 'preparation-preview/',
            {'technical_proposal_id': str(self.technical.pk)}, format='json')
        self.assertEqual(result.status_code, 409)

    def test_generic_draft_edit_works_and_quote_list_respects_known_organization(self):
        result = self.api.patch(self.quote_url, {'notes': 'Retained authored note'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        foreign = get_user_model().objects.create_user(username='foreign-owner', email='foreign-owner@example.test')
        grant_sales_actions(foreign, 'sales_opportunities')
        org = Organization.objects.create(code='foreign-owner', name='Foreign owner')
        profile = foreign.rbac_profile
        profile.organization = org
        profile.save(update_fields=['organization'])
        Deal.objects.filter(pk=self.deal.pk).update(owner=foreign)
        self.assertEqual(self.api.get(self.quote_url).status_code, 404)
        self.assertEqual(self.api.get('/api/v1/sales/quotes/').data['count'], 0)

    def test_unknown_organization_denies_capability_and_command_without_creating_work(self):
        self.actor.is_staff = True
        self.actor.save(update_fields=['is_staff'])
        unknown = get_user_model().objects.create_user(username='unknown-owner', email='unknown-owner@example.test')
        self.deal.owner = unknown
        self.deal.save(update_fields=['owner'])
        self.customer.account_manager = unknown
        self.customer.save(update_fields=['account_manager'])
        state = self.api.get(self.bid_url)
        self.assertEqual(state.status_code, 200, state.data)
        self.assertFalse(state.data['capabilities']['can_create'])
        self.assertFalse(state.data['capabilities']['can_attach'])
        self.assertIn('organization review', state.data['capabilities']['reason'])
        payload = self.connect_payload(mode='create')
        payload.pop('planning_project_id')
        result = self.api.post(self.bid_url, payload, format='json')
        self.assertEqual(result.status_code, 403, result.data)
        self.assertIn('organization review', str(result.data['detail']))
        self.assertFalse(BidPreparation.objects.exists())
        self.assertEqual(PlanningProject.objects.count(), 1)

    def test_inconsistent_known_organizations_deny_even_staff_capability_and_command(self):
        self.actor.is_staff = True
        self.actor.save(update_fields=['is_staff'])
        other = get_user_model().objects.create_user(username='inconsistent-owner', email='inconsistent@example.test')
        grant_sales_actions(other, 'sales_clients')
        org = Organization.objects.create(code='inconsistent', name='Inconsistent ownership')
        profile = other.rbac_profile
        profile.organization = org
        profile.save(update_fields=['organization'])
        self.customer.account_manager = other
        self.customer.save(update_fields=['account_manager'])
        state = self.api.get(self.bid_url)
        self.assertEqual(state.status_code, 200, state.data)
        self.assertFalse(state.data['capabilities']['can_create'])
        self.assertFalse(state.data['capabilities']['can_attach'])
        self.assertIn('organization review', state.data['capabilities']['reason'])
        result = self.api.post(self.bid_url, self.connect_payload(), format='json')
        self.assertEqual(result.status_code, 403, result.data)
        self.assertFalse(BidPreparation.objects.exists())

    def test_cancelled_unprotected_quote_can_be_deleted(self):
        Quote.objects.filter(pk=self.quote.pk).update(status='cancelled')
        result = self.api.delete(self.quote_url)
        self.assertEqual(result.status_code, 204, getattr(result, 'data', None))
        self.assertFalse(Quote.objects.filter(pk=self.quote.pk).exists())

    def test_cancelled_quote_with_approval_or_submission_evidence_cannot_be_deleted(self):
        empty = {'approved_by': None, 'approved_at': None, 'approval_history': [],
                 'submitted_version_hash': '', 'sent_date': None, 'viewed_date': None,
                 'response_date': None, 'submission_recipient': '', 'submission_evidence': ''}
        markers = {'approved_by': self.actor.pk, 'approved_at': timezone.now(),
                   'approval_history': [{'decision': 'approved'}], 'submitted_version_hash': 'a' * 64,
                   'sent_date': timezone.now(), 'viewed_date': timezone.now(), 'response_date': timezone.now(),
                   'submission_recipient': 'synthetic@example.test', 'submission_evidence': 'Retained receipt'}
        for field, value in markers.items():
            with self.subTest(marker=field):
                Quote.objects.filter(pk=self.quote.pk).update(status='cancelled', **{**empty, field: value})
                result = self.api.delete(self.quote_url)
                self.assertEqual(result.status_code, 409, result.data)
                self.assertTrue(Quote.objects.filter(pk=self.quote.pk).exists())

    def test_cancelled_quote_with_captured_preparation_cannot_be_deleted(self):
        self.attach()
        response = self.api.post(self.quote_url + 'prepare/', self.apply_payload(), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        Quote.objects.filter(pk=self.quote.pk).update(status='cancelled')
        self.assertEqual(self.api.delete(self.quote_url).status_code, 409)
        self.assertEqual(QuotePreparationRevision.objects.filter(quote=self.quote).count(), 1)

    def test_returned_review_history_preserves_draft_editing_and_delete_evidence(self):
        history = [{'decision': 'returned', 'comment': 'Correct the draft scope'}]
        Quote.objects.filter(pk=self.quote.pk).update(approval_history=history)
        result = self.api.patch(self.quote_url, {'scope': 'Corrected authored scope'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.scope, 'Corrected authored scope')
        self.assertEqual(self.quote.approval_history, history)
        self.assertEqual(self.api.delete(self.quote_url).status_code, 409)
