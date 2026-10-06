from datetime import date, timedelta
from decimal import Decimal
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.exceptions import PermissionDenied, ValidationError
from apps.procurement.tests.approval_fixtures import grant_approval, set_position
from apps.notifications.models import Notification
from apps.rbac.models import UserProfile

from apps.project_control.models import Estimate
from apps.sales.models import Client, Deal, FrameworkAgreement, OpportunityAuditEvent, Quote
from apps.sales.workspace_models import OpportunityWorkspace, OpportunityWorkspaceUpload
from apps.sales.workflow import (
    HANDOVER_REQUIRED_ITEMS, close_opportunity, convert_to_project, decide_award, decide_handover,
    enter_negotiation, record_bid_decision, record_ceo_decision, submit_award,
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
            grant_approval(user, 'sales', 'sales_opportunities', 'sales_handovers')
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
            opportunity_type='eoi',
            qualification_data={
                'event_type': 'RFI (Request for Information)',
                'published_at': '2026-10-01T16:22:00+04:00',
                'submission_deadline_at': '2026-10-08T12:00:00+04:00',
            },
        )
        self._attach_required_file(self.opportunity)

    def _attach_required_file(self, opportunity):
        workspace, _ = OpportunityWorkspace.objects.get_or_create(opportunity=opportunity)
        OpportunityWorkspaceUpload.objects.create(
            workspace=workspace,
            request_id=uuid.uuid4(),
            actor=self.owner,
            folder_key='proposal',
            name='qualification-support.pdf',
            size=2048,
            sha256='a' * 64,
            status='ready',
            result={'id': 'file-1'},
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
        self.assertEqual(project.client_id, self.client.pk)
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
        self.assertIn('submission_due_date', exc.exception.detail['missing_fields'])
        self.assertNotIn('scope_type', exc.exception.detail['missing_fields'])
        self.assertNotIn('required_attachment', exc.exception.detail['missing_fields'])

    def test_qualification_allows_missing_scope_and_attachment_with_warnings(self):
        no_file = Deal.objects.create(
            deal_code='OPP-2026-003', deal_name='No attachment lead', client=self.client,
            owner=self.owner, estimated_value=Decimal('1000'), currency='AED',
            expected_close_date=date.today() + timedelta(days=10),
            submission_due_date=date.today() + timedelta(days=8),
            scope_type='',
        )

        submitted = submit_qualification(no_file, self.owner)

        self.assertEqual(submitted.stage, 'qualified')
        self.assertEqual(submitted.submission_warnings, [
            {
                'field': 'scope_type',
                'message': 'Scope type has not been provided. You may continue with the submission.',
            },
            {
                'field': 'required_attachment',
                'message': 'No attachment has been provided. You may continue with the submission.',
            },
        ])
        self.assertTrue(OpportunityAuditEvent.objects.filter(
            opportunity=no_file,
            event_type='qualification_submitted',
            to_stage='qualified',
        ).exists())

    def test_qualification_notifies_assigned_team_owner_and_configured_manager(self):
        User = get_user_model()
        sales_member = User.objects.create_user(
            username='sales-peer', email='peer@example.com', password='test',
        )
        sales_manager = User.objects.create_user(
            username='sales-manager', email='manager@example.com', password='test',
        )
        unrelated_sales_user = User.objects.create_user(
            username='unrelated-sales', email='unrelated@example.com', password='test',
        )
        owner_profile, _ = UserProfile.objects.get_or_create(user=self.owner)
        manager_profile = UserProfile.objects.create(
            user=sales_manager,
            organization=owner_profile.organization,
        )
        owner_profile.manager = manager_profile
        owner_profile.save(update_fields=['manager', 'updated_at'])
        self.opportunity.team_members.add(sales_member, self.owner)

        submit_qualification(self.opportunity, self.owner, special_note='Prioritize this for Monday kickoff.')

        notifications = Notification.objects.filter(
            metadata__event_type='qualification_submitted',
            metadata__opportunity_id=str(self.opportunity.pk),
        )
        self.assertSetEqual(
            set(notifications.values_list('recipient_id', flat=True)),
            {self.owner.pk, sales_member.pk, sales_manager.pk},
        )
        self.assertFalse(notifications.filter(recipient=unrelated_sales_user).exists())
        for notification in notifications:
            self.assertEqual(notification.title, 'New Opportunity Submitted')
            self.assertEqual(
                notification.message,
                'Opportunity Detailed Engineering Services has been submitted for Internal Sales Review.',
            )
            self.assertEqual(notification.category.name, 'INFO')
            self.assertEqual(notification.status, 'SENT')
            self.assertFalse(notification.is_read)
            self.assertTrue(notification.send_in_app)
            self.assertEqual(notification.action_label, 'Open Opportunity Record')
            self.assertEqual(
                notification.action_url,
                f'/sales/opportunities?record={self.opportunity.pk}',
            )
            self.assertEqual(notification.sender, self.owner)
            self.assertEqual(
                notification.metadata['special_note'],
                'Prioritize this for Monday kickoff.',
            )

    def test_qualification_rolls_back_if_required_notifications_are_not_saved(self):
        with patch('apps.sales.workflow.NotificationService.bulk_notify', return_value=[]):
            with self.assertRaises(ValidationError) as exc:
                submit_qualification(self.opportunity, self.owner)

        self.assertIn('notification', exc.exception.detail)
        self.opportunity.refresh_from_db()
        self.assertEqual(self.opportunity.stage, 'lead')
        self.assertFalse(OpportunityAuditEvent.objects.filter(
            opportunity=self.opportunity,
            event_type='qualification_submitted',
        ).exists())

    def test_missing_project_manager_never_falls_back_to_sales_owner(self):
        from apps.sales.workflow import require_project_manager
        for nominee in (None, self.owner):
            with self.assertRaises(ValidationError):
                require_project_manager(nominee)

    @override_settings(RADAI_BUSINESS_APPROVAL_ROUTES={}, SALES_BID_DECISION_RBAC_FALLBACK_ENABLED=False)
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

    def test_only_ceo_can_record_ceo_decision(self):
        ceo = get_user_model().objects.create_user(
            username='chief-exec', email='ceo@example.com', password='test',
        )
        grant_approval(ceo, 'sales_opportunities')
        set_position(ceo, 'Chief Executive Officer')
        submit_qualification(self.opportunity, self.owner)
        self.opportunity = record_bid_decision(self.opportunity, self.approver, 'bid', 'Gate passed')

        with self.assertRaises(PermissionDenied):
            record_ceo_decision(self.opportunity, self.owner, 'go')

        updated = record_ceo_decision(self.opportunity, ceo, 'go')
        self.assertEqual(updated.stage, 'proposal')
        self.assertTrue(OpportunityAuditEvent.objects.filter(
            opportunity=self.opportunity,
            event_type='ceo_gate_decision',
            data__decision='approved',
        ).exists())

    def test_ceo_no_go_closes_as_no_bid_with_reason(self):
        ceo = get_user_model().objects.create_user(
            username='chief-stop', email='ceo.stop@example.com', password='test',
        )
        grant_approval(ceo, 'sales_opportunities')
        set_position(ceo, 'Chief Executive Officer')
        submit_qualification(self.opportunity, self.owner)
        self.opportunity = record_bid_decision(self.opportunity, self.approver, 'conditional_bid', 'Pending commercial clarifications')

        with self.assertRaises(ValidationError):
            record_ceo_decision(self.opportunity, ceo, 'no_go')

        updated = record_ceo_decision(
            self.opportunity, ceo, 'no_go',
            'Strategic priorities changed for this cycle.',
        )
        self.assertEqual(updated.stage, 'no_bid')
        self.assertEqual(updated.bid_decision, 'no_bid')
        self.assertEqual(updated.bid_decision_reason, 'Strategic priorities changed for this cycle.')
        self.assertTrue(OpportunityAuditEvent.objects.filter(
            opportunity=self.opportunity,
            event_type='ceo_gate_decision',
            data__decision='rejected',
        ).exists())
