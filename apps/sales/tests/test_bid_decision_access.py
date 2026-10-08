"""Temporary bid-decision authority through the real HTTP and domain guards."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import include, path
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.procurement.tests.approval_fixtures import set_position
from apps.rbac.action_policy import module_action_allowed
from apps.rbac.models import (
    Module, Organization, Permission, Role, RoleModule, RolePermission,
    UserPermissionOverride, UserProfile, UserRole,
)
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints
from apps.sales.bid_decision_access import BID_DECISION_ROUTE
from apps.sales.models import Client, Deal, OpportunityAuditEvent
from apps.sales.views import DealViewSet
from apps.sales.workflow import record_bid_decision


router = DefaultRouter()
router.register('deals', DealViewSet, basename='bid-access-deals')
urlpatterns = [path('api/v1/sales/', include(router.urls))]
secure_module_endpoints(urlpatterns)

CONFIGURED_ROUTE = {
    BID_DECISION_ROUTE: {
        'positions': ['Business Development Manager'],
        'pending_states': ['qualified'],
        'state_field': 'stage',
    },
}


@override_settings(
    ROOT_URLCONF=__name__,
    SALES_BID_DECISION_RBAC_FALLBACK_ENABLED=True,
    RADAI_BUSINESS_APPROVAL_ROUTES={},
)
class BidDecisionAccessTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.organization = Organization.objects.create(
            code='bid-access-tests', name='Bid access tests',
        )
        self.actor = get_user_model().objects.create_user(
            username='bid-reader', email='bid-reader@example.test',
        )
        self.profile = UserProfile.objects.create(
            user=self.actor, organization=self.organization,
        )
        self.role = Role.objects.create(
            code='bid-read-only', name='Opportunity reader', level=4,
        )
        UserRole.objects.create(user_profile=self.profile, role=self.role)
        self.module, _ = Module.objects.get_or_create(
            code='sales_opportunities', defaults={'name': 'Opportunities'},
        )
        ensure_module_actions(Module, Permission, module_ids=[self.module.pk])
        RoleModule.objects.create(role=self.role, module=self.module)
        self.grant('read')
        self.customer = Client.objects.create(
            client_code='BID-ACCESS', company_name='Synthetic bid access client',
            industry_type='other', account_manager=self.actor,
        )
        self.deal = Deal.objects.create(
            deal_code='Q-BID-ACCESS', deal_name='Synthetic qualified opportunity',
            client=self.customer, owner=self.actor, stage='qualified',
            estimated_value=Decimal('1000.00'), currency='AED',
        )
        self.api = APIClient()
        self.api.force_authenticate(self.actor)
        self.url = f'/api/v1/sales/deals/{self.deal.pk}/bid-decision/'

    def grant(self, action):
        for permission in self.module.permissions.filter(action=action, is_active=True):
            RolePermission.objects.get_or_create(role=self.role, permission=permission)

    def decide(self, **payload):
        return self.api.post(self.url, {'decision': 'bid', **payload}, format='json')

    def assert_undecided(self, stage='qualified'):
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.stage, stage)
        self.assertEqual(self.deal.bid_decision, 'pending')
        self.assertIsNone(self.deal.bid_decided_by_id)
        self.assertIsNone(self.deal.bid_decided_at)
        self.assertFalse(OpportunityAuditEvent.objects.filter(opportunity=self.deal).exists())

    def assert_decided(self, decision, stage, authority='module_rbac'):
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.bid_decision, decision)
        self.assertEqual(self.deal.stage, stage)
        self.assertEqual(self.deal.bid_decided_by_id, self.actor.pk)
        self.assertIsNotNone(self.deal.bid_decided_at)
        event = OpportunityAuditEvent.objects.get(opportunity=self.deal, event_type='bid_decision')
        self.assertEqual(event.actor_id, self.actor.pk)
        self.assertEqual(event.from_stage, 'qualified')
        self.assertEqual(event.to_stage, stage)
        self.assertEqual(event.data, {'decision': decision, 'decision_authority': authority})

    def test_module_reader_can_decide_without_write_approval_or_hr_position(self):
        for action in ('create', 'update', 'approve'):
            self.assertFalse(module_action_allowed(self.actor, 'sales_opportunities', action))
        response = self.decide(reason='Reviewed opportunity')
        self.assertEqual(response.status_code, 200, response.data)
        self.assert_decided('bid', 'proposal')
        self.assertEqual(self.deal.bid_decision_reason, 'Reviewed opportunity')

    def test_conditional_and_no_bid_retain_required_reason_and_lifecycle(self):
        for decision, stage in (('conditional_bid', 'proposal'), ('no_bid', 'no_bid')):
            with self.subTest(decision=decision):
                response = self.decide(decision=decision)
                self.assertEqual(response.status_code, 400, response.data)
                self.assert_undecided()
                response = self.decide(decision=decision, reason='Recorded business rationale')
                self.assertEqual(response.status_code, 200, response.data)
                self.assert_decided(decision, stage)
                OpportunityAuditEvent.objects.filter(opportunity=self.deal).delete()
                Deal.objects.filter(pk=self.deal.pk).update(
                    stage='qualified', bid_decision='pending', bid_decided_by=None, bid_decided_at=None,
                )

    def test_high_risk_high_value_owner_uses_authorized_temporary_rule(self):
        Deal.objects.filter(pk=self.deal.pk).update(
            risk_level='critical', estimated_value=Decimal('9000000.00'),
        )
        response = self.decide()
        self.assertEqual(response.status_code, 200, response.data)
        self.assert_decided('bid', 'proposal')

    def test_role_module_membership_without_read_grant_cannot_decide(self):
        RolePermission.objects.filter(role=self.role).delete()
        response = self.decide()
        self.assertEqual(response.status_code, 403, response.data)
        self.assert_undecided()

    def test_explicit_read_or_approve_denial_overrides_fallback(self):
        for action in ('read', 'approve'):
            with self.subTest(action=action):
                denied = UserPermissionOverride.objects.create(
                    user_profile=self.profile,
                    permission=self.module.permissions.get(action=action, is_active=True),
                    allowed=False,
                )
                response = self.decide()
                self.assertEqual(response.status_code, 403, response.data)
                self.assert_undecided()
                denied.delete()

    def test_disabled_module_cannot_decide(self):
        Module.objects.filter(pk=self.module.pk).update(is_active=False)
        response = self.decide()
        self.assertEqual(response.status_code, 403, response.data)
        self.assert_undecided()

    def test_account_and_profile_revocation_are_rechecked_from_database(self):
        # force_authenticate intentionally retains the earlier active user object.
        changes = (
            (get_user_model(), self.actor.pk, {'is_active': False}, {'is_active': True}),
            (UserProfile, self.profile.pk, {'status': 'inactive'}, {'status': 'active'}),
            (UserProfile, self.profile.pk, {'is_deleted': True}, {'is_deleted': False}),
            (UserProfile, self.profile.pk, {'locked_until': timezone.now() + timedelta(hours=1)},
             {'locked_until': None}),
        )
        for model, pk, revoked, restored in changes:
            with self.subTest(revoked=revoked):
                model.objects.filter(pk=pk).update(**revoked)
                response = self.decide()
                self.assertEqual(response.status_code, 403, response.data)
                self.assert_undecided()
                model.objects.filter(pk=pk).update(**restored)

    def test_foreign_opportunity_is_not_visible_to_module_reader(self):
        outsider = get_user_model().objects.create_user(
            username='bid-outsider', email='bid-outsider@example.test',
        )
        Deal.objects.filter(pk=self.deal.pk).update(owner=outsider)
        response = self.decide()
        self.assertEqual(response.status_code, 404, response.data)
        self.assert_undecided()

    def test_known_foreign_client_organization_is_rechecked_in_command(self):
        other_org = Organization.objects.create(code='bid-other', name='Other organization')
        outsider = get_user_model().objects.create_user(
            username='foreign-client-owner', email='foreign-client-owner@example.test',
        )
        UserProfile.objects.create(user=outsider, organization=other_org)
        Client.objects.filter(pk=self.customer.pk).update(account_manager=outsider)
        response = self.decide()
        self.assertEqual(response.status_code, 404, response.data)
        self.assert_undecided()

    def test_direct_command_cannot_reuse_revoked_access_or_stale_record_scope(self):
        stale = Deal.objects.get(pk=self.deal.pk)
        RolePermission.objects.filter(role=self.role).delete()
        with self.assertRaises(PermissionDenied):
            record_bid_decision(stale, self.actor, 'bid')
        self.grant('read')
        outsider = get_user_model().objects.create_user(
            username='later-opportunity-owner', email='later-opportunity-owner@example.test',
        )
        Deal.objects.filter(pk=self.deal.pk).update(owner=outsider)
        with self.assertRaises(NotFound):
            record_bid_decision(stale, self.actor, 'bid')
        self.assert_undecided()

    @override_settings(SALES_BID_DECISION_RBAC_FALLBACK_ENABLED=False)
    def test_disabled_fallback_requires_business_route_even_with_approve_grant(self):
        self.grant('approve')
        response = self.decide()
        self.assertEqual(response.status_code, 403, response.data)
        self.assertIn('No business approval route', str(response.data))
        self.assert_undecided()

    @override_settings(RADAI_BUSINESS_APPROVAL_ROUTES=CONFIGURED_ROUTE)
    def test_explicit_route_requires_approve_grant_and_canonical_position(self):
        response = self.decide()
        self.assertEqual(response.status_code, 403, response.data)
        self.grant('approve')
        response = self.decide()
        self.assertEqual(response.status_code, 403, response.data)
        self.assert_undecided()
        set_position(self.actor, 'Business Development Manager')
        response = self.decide()
        self.assertEqual(response.status_code, 200, response.data)
        self.assert_decided('bid', 'proposal', 'configured_route')

    @override_settings(RADAI_BUSINESS_APPROVAL_ROUTES=CONFIGURED_ROUTE)
    def test_configured_route_preserves_high_risk_owner_restriction(self):
        self.grant('approve')
        set_position(self.actor, 'Business Development Manager')
        Deal.objects.filter(pk=self.deal.pk).update(risk_level='critical')
        response = self.decide()
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('independent management approval', str(response.data))
        self.assert_undecided()

    def test_present_invalid_route_and_malformed_config_never_enable_fallback(self):
        self.grant('approve')
        set_position(self.actor, 'Business Development Manager')
        for routes in (
            {BID_DECISION_ROUTE: None}, {BID_DECISION_ROUTE: {}},
            {BID_DECISION_ROUTE: {'positions': 'Manager', 'pending_states': ['qualified']}},
            [], 'invalid configuration',
        ):
            with self.subTest(routes=routes), override_settings(RADAI_BUSINESS_APPROVAL_ROUTES=routes):
                response = self.decide()
                self.assertEqual(response.status_code, 403, response.data)
                self.assert_undecided()

    @override_settings(RADAI_BUSINESS_APPROVAL_ROUTES=None)
    def test_malformed_environment_policy_fails_closed(self):
        self.grant('approve')
        with patch.dict('os.environ', {'RADAI_BUSINESS_APPROVAL_ROUTES': '{invalid'}):
            response = self.decide()
        self.assertEqual(response.status_code, 403, response.data)
        self.assert_undecided()

    @override_settings(RADAI_BUSINESS_APPROVAL_ROUTES={'other.Model.approve': {}})
    def test_unrelated_route_does_not_configure_bid_decision(self):
        response = self.decide()
        self.assertEqual(response.status_code, 200, response.data)
        self.assert_decided('bid', 'proposal')

    def test_invalid_decision_warns_for_missing_commercials_and_unqualified_stage_still_fails(self):
        response = self.decide(decision='invalid_bid')
        self.assertEqual(response.status_code, 400, response.data)
        self.assert_undecided()
        for fields in ({'estimated_value': None}, {'currency': ''}):
            with self.subTest(fields=fields):
                Deal.objects.filter(pk=self.deal.pk).update(
                    stage='qualified', bid_decision='pending', bid_decided_by=None, bid_decided_at=None,
                    estimated_value=Decimal('1000.00'), currency='AED',
                )
                Deal.objects.filter(pk=self.deal.pk).update(**fields)
                response = self.decide()
                self.assertEqual(response.status_code, 200, response.data)
                self.assert_decided('bid', 'proposal')
                self.assertEqual(
                    response.data.get('warnings'),
                    ['Complete the estimated value and currency to support downstream proposal and award controls.'],
                )
                OpportunityAuditEvent.objects.filter(opportunity=self.deal).delete()
        Deal.objects.filter(pk=self.deal.pk).update(
            stage='lead', bid_decision='pending', bid_decided_by=None, bid_decided_at=None,
            estimated_value=Decimal('1000.00'), currency='AED',
        )
        OpportunityAuditEvent.objects.filter(opportunity=self.deal).delete()
        response = self.decide()
        self.assertEqual(response.status_code, 400, response.data)
        self.assert_undecided(stage='lead')

    def test_repeated_decision_is_stale_and_cannot_add_another_audit(self):
        first = self.decide()
        self.assertEqual(first.status_code, 200, first.data)
        second = self.decide(decision='no_bid', reason='Late browser decision')
        self.assertEqual(second.status_code, 400, second.data)
        self.assert_decided('bid', 'proposal')
        self.assertEqual(OpportunityAuditEvent.objects.filter(opportunity=self.deal).count(), 1)

    def test_audit_failure_rolls_back_the_bid_decision(self):
        # Fail persistence, not the imported helper: a lazily loaded domain
        # module must not retain this test's mocked function after cleanup.
        with patch.object(OpportunityAuditEvent.objects, 'create', side_effect=RuntimeError('Synthetic audit failure')):
            with self.assertRaisesRegex(RuntimeError, 'Synthetic audit failure'):
                self.decide()
        self.assert_undecided()

    def test_award_approval_still_requires_its_separate_configured_route(self):
        self.grant('approve')
        Deal.objects.filter(pk=self.deal.pk).update(stage='award_pending')
        response = self.api.post(
            f'/api/v1/sales/deals/{self.deal.pk}/approve-award/', {}, format='json',
        )
        self.assertEqual(response.status_code, 403, response.data)
        self.assertIn('sales_opportunities.Deal.approve_award', str(response.data))
        self.assert_undecided(stage='award_pending')
