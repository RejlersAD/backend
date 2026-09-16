from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.exceptions import PermissionDenied, ValidationError
from apps.procurement.tests.approval_fixtures import grant_approval, set_position

from apps.project_control.models import Estimate
from apps.sales.models import Client, Deal, FrameworkAgreement, OpportunityAuditEvent, Quote
from apps.sales.workflow import (
    HANDOVER_REQUIRED_ITEMS, close_opportunity, convert_to_project, decide_award, decide_handover,
    enter_negotiation, record_bid_decision, submit_award,
    submit_handover_for_acceptance, submit_qualification,
)


@override_settings(RADAI_BUSINESS_APPROVAL_ROUTES={
    'sales_opportunities.Deal.bid_decision': {
        'positions': ['Business Development Manager'], 'pending_states': ['qualified'], 'state_field': 'stage'},
    'sales_opportunities.Deal.approve_award': {
        'positions': ['Business Development Manager'], 'pending_states': ['award_pending'], 'state_field': 'stage',
        'submitter_field': 'award_submitted_by_id'},
    'sales_opportunities.Deal.reject_award': {
        'positions': ['Business Development Manager'], 'pending_states': ['award_pending'], 'state_field': 'stage',
        'submitter_field': 'award_submitted_by_id'},
})
class OpportunityWorkflowTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(username='sales-owner', email='owner@example.com', password='test')
        self.approver = User.objects.create_user(username='award-approver', email='approver@example.com', password='test')
        self.project_manager = User.objects.create_user(username='handover-pm', email='pm@example.com', password='test')
        grant_approval(self.project_manager, 'sales_handovers')
        set_position(self.project_manager, 'Project Manager')
        for user in (self.owner, self.approver):
            grant_approval(user, 'sales_opportunities', 'sales_handovers')
            set_position(user, 'Business Development Manager')
        self.client = Client.objects.create(
            client_code='CLT-001', company_name='National Energy Co',
            industry_type='oil_gas', account_manager=self.owner,
        )
        self.opportunity = Deal.objects.create(
            deal_code='OPP-2026-001', deal_name='Detailed Engineering Services',
            client=self.client, owner=self.owner, nominated_project_manager=self.project_manager, estimated_value=Decimal('1250000'),
            currency='AED', expected_close_date=date.today() + timedelta(days=60),
            submission_due_date=date.today() + timedelta(days=30),
            expected_start_date=date.today() + timedelta(days=90),
            scope_type='detailed_engineering', project_duration_months=12,
        )

    def test_governed_award_converts_exactly_once(self):
        submit_qualification(self.opportunity, self.owner)
        self.opportunity = record_bid_decision(self.opportunity, self.approver, 'bid', 'Strategic client and available capacity')
        Quote.objects.create(
            quote_number='PROP-001', deal=self.opportunity, client=self.client,
            status='sent', subtotal=Decimal('1200000'), tax_amount=Decimal('60000'),
            total_amount=Decimal('1260000'), currency='AED',
            valid_until=date.today() + timedelta(days=45), prepared_by=self.owner,
        )
        enter_negotiation(self.opportunity, self.owner)
        submit_award(
            self.opportunity, self.owner, reference='PO-7788', award_date=date.today(),
            award_value=Decimal('1235000'), handover_data={'payment_terms': '45 days'},
        )

        with self.assertRaises(PermissionDenied):
            decide_award(self.opportunity, self.owner, approved=True)

        self.opportunity = decide_award(self.opportunity, self.approver, approved=True)
        with self.assertRaises(ValidationError):
            convert_to_project(self.opportunity.pk, self.approver, project_code='5902001')
        handover = self.opportunity.project_handover
        handover.checklist = {item: True for item in HANDOVER_REQUIRED_ITEMS}
        handover.save(update_fields=['checklist', 'updated_at'])
        submit_handover_for_acceptance(handover, self.owner)
        decide_handover(handover, self.project_manager, accepted=True, comment='Delivery inputs verified')
        from apps.sales.serializers import DealCreateSerializer
        nomination = DealCreateSerializer(instance=self.opportunity,
            data={'nominated_project_manager': None}, partial=True)
        self.assertFalse(nomination.is_valid())
        self.assertIn('nominated_project_manager', nomination.errors)
        opportunity, project, created = convert_to_project(
            self.opportunity.pk, self.approver, project_code='5902001',
        )
        repeated, same_project, created_again = convert_to_project(
            self.opportunity.pk, self.approver, project_code='IGNORED',
        )

        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(project.pk, same_project.pk)
        self.assertEqual(opportunity.stage, 'converted')
        self.assertEqual(repeated.converted_project_id, project.pk)
        self.assertEqual(project.contract_value, Decimal('1235000'))
        self.assertEqual(project.scope_type, 'detailed_engineering')
        self.assertEqual(project.custom_fields['source_opportunity_code'], 'OPP-2026-001')
        self.assertTrue(Estimate.objects.filter(
            project=project, kind='awarded', status='approved', total_amount=Decimal('1235000'),
        ).exists())
        self.assertEqual(OpportunityAuditEvent.objects.filter(opportunity=self.opportunity).count(), 8)

    def test_qualification_reports_missing_gate_fields(self):
        incomplete = Deal.objects.create(
            deal_code='OPP-2026-002', deal_name='Incomplete lead', client=self.client,
            owner=self.owner, estimated_value=Decimal('100'), currency='AED',
            expected_close_date=date.today() + timedelta(days=10),
        )
        with self.assertRaises(ValidationError) as exc:
            submit_qualification(incomplete, self.owner)
        self.assertIn('scope_type', exc.exception.detail['missing_fields'])
        self.assertIn('submission_due_date', exc.exception.detail['missing_fields'])

    def test_missing_project_manager_never_falls_back_to_sales_owner(self):
        from apps.sales.workflow import require_project_manager
        for nominee in (None, self.owner):
            with self.assertRaises(ValidationError):
                require_project_manager(nominee)

    @override_settings(RADAI_BUSINESS_APPROVAL_ROUTES={})
    def test_missing_business_route_cannot_be_replaced_by_admin_access(self):
        self.approver.is_superuser = True
        self.approver.save(update_fields=['is_superuser'])
        submit_qualification(self.opportunity, self.owner)
        with self.assertRaises(PermissionDenied):
            record_bid_decision(self.opportunity, self.approver, 'bid')

    def test_stale_bid_decision_cannot_repeat_after_stage_advances(self):
        submit_qualification(self.opportunity, self.owner)
        stale = Deal.objects.get(pk=self.opportunity.pk)
        record_bid_decision(self.opportunity, self.approver, 'bid')
        with self.assertRaises(ValidationError):
            record_bid_decision(stale, self.approver, 'bid')

    def test_expired_framework_cannot_pass_qualification(self):
        framework = FrameworkAgreement.objects.create(
            framework_number='FA-OLD-001', title='Expired engineering services',
            client=self.client, owner=self.owner, status='active',
            effective_date=date.today() - timedelta(days=365),
            expiry_date=date.today() - timedelta(days=1), currency='AED',
        )
        self.opportunity.framework = framework
        self.opportunity.save(update_fields=['framework', 'updated_at'])

        with self.assertRaises(ValidationError) as exc:
            submit_qualification(self.opportunity, self.owner)

        self.assertIn('framework', exc.exception.detail)

    def test_lost_opportunity_requires_reason_and_is_audited(self):
        with self.assertRaises(ValidationError):
            close_opportunity(self.opportunity, self.owner, outcome='lost', reason='')

        close_opportunity(
            self.opportunity, self.owner, outcome='lost',
            reason='Client selected an incumbent supplier.',
        )
        self.assertEqual(self.opportunity.stage, 'lost')
        self.assertEqual(self.opportunity.probability, 0)
        self.assertTrue(OpportunityAuditEvent.objects.filter(
            opportunity=self.opportunity, event_type='opportunity_closed',
        ).exists())
