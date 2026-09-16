"""Business assignment, effective access and current stage must all agree."""
from types import SimpleNamespace
from unittest.mock import patch

from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient

from apps.core.project_models import Project, ProjectMember
from apps.rbac.models import Organization, Module, Permission, Role, RoleModule, RolePermission, UserPermissionOverride, UserProfile, UserRole
from apps.users.models import User
from ..access import can_final_approve_defaults, can_decide_proposal_task
from ..governance_serializers import ScheduleReviewSerializer
from ..models import ScheduleReview, ScheduleReviewDecision, ScheduleVersion, TechnicalProposal, ScheduleBasis, GenerationPlan, DocumentIntelligenceRun, PlanningAuditEvent
from ..intelligence_serializers import ScheduleBasisSerializer, GenerationPlanSerializer
from ..services.schedule_basis import approve_schedule_basis
from ..services.generation_plan import approve_generation_plan
from ..proposal_serializers import TechnicalProposalSerializer
from ..services.cpm import calculate_schedule_version
from ..services.proposal_workflow import submit_for_review, reviewer_decision, approver_decision, reassign_reviewer
from ..services.schedule_approval import ScheduleApprovalError, approve_schedule_version, decide_schedule_review, can_baseline_schedule
from ..services.trustworthy_scheduling import run_schedule_assurance, approve_schedule_assurance
from .test_scheduling_engine import ScheduleFixture


def grant_test_approval(users):
    module, _ = Module.objects.get_or_create(code='planning_package', defaults={'name': 'Planning'})
    permission, _ = Permission.objects.get_or_create(code='gated_planning_approve', defaults={
        'module': module, 'name': 'Planning approve', 'action': 'approve',
    })
    role, _ = Role.objects.get_or_create(code='gated_planning', defaults={'name': 'Gated planning'})
    RoleModule.objects.get_or_create(role=role, module=module)
    for row in Permission.objects.filter(module=module, is_active=True):
        RolePermission.objects.get_or_create(role=role, permission=row)
    organization, _ = Organization.objects.get_or_create(code='GATED', defaults={'name': 'Gated organization'})
    for user in users:
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'status': 'active', 'organization': organization})
        UserRole.objects.get_or_create(user_profile=profile, role=role)
    return permission


class BusinessApprovalGateTests(ScheduleFixture):
    def setUp(self):
        super().setUp()
        self.enterprise = Project.objects.create(code='GATED', name='Gated approvals', owner=self.owner)
        self.project.enterprise_project = self.enterprise
        self.project.save(update_fields=['enterprise_project'])
        self.reviewer = User.objects.create_user(username='gatedreview', email='review@example.test')
        self.manager = User.objects.create_user(username='gatedmanager', email='manager@example.test')
        self.admin = User.objects.create_user(username='unassignedadmin', email='admin@example.test', is_staff=True, is_superuser=True)
        ProjectMember.objects.create(project=self.enterprise, user=self.reviewer, role='reviewer')
        ProjectMember.objects.create(project=self.enterprise, user=self.manager, role='project_manager')
        module, _ = Module.objects.get_or_create(code='planning_package', defaults={'name': 'Planning'})
        self.permission, _ = Permission.objects.get_or_create(code='gated_planning_approve', defaults={
            'module': module, 'name': 'Planning approve', 'action': 'approve',
        })
        role, _ = Role.objects.get_or_create(code='gated_planning', defaults={'name': 'Gated planning'})
        RoleModule.objects.get_or_create(role=role, module=module)
        for permission in Permission.objects.filter(module=module, is_active=True):
            RolePermission.objects.get_or_create(role=role, permission=permission)
        organization = Organization.objects.create(code='GATED', name='Gated organization')
        for user in (self.owner, self.reviewer, self.manager, self.admin):
            profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'status': 'active', 'organization': organization})
            UserRole.objects.get_or_create(user_profile=profile, role=role)
        self.activity('GATED-A', 2)
        calculate_schedule_version(self.version)
        self.version.refresh_from_db()
        self.client = APIClient()

    def deny(self, user):
        return UserPermissionOverride.objects.create(user_profile=UserProfile.objects.get(user=user), permission=self.permission, allowed=False)

    def assure(self):
        run_schedule_assurance(self.version)
        return approve_schedule_assurance(self.version, self.owner)

    def review(self):
        review = ScheduleReview.objects.create(version=self.version, title='Assigned review', requested_by=self.owner, requested_at=timezone.now())
        for user in (self.reviewer, self.owner):
            ScheduleReviewDecision.objects.create(review=review, reviewer=user)
        return review

    def capability(self, review, user):
        return ScheduleReviewSerializer(review, context={'request': SimpleNamespace(user=user)}).data['can_decide']

    def test_staff_superuser_never_substitutes_for_project_assignment(self):
        self.assertFalse(can_final_approve_defaults(self.admin, self.project))
        with self.assertRaises(ScheduleApprovalError):
            approve_schedule_assurance(self.version, self.admin)
        self.assertTrue(can_final_approve_defaults(self.owner, self.project))
        self.assertTrue(can_final_approve_defaults(self.manager, self.project))
        self.deny(self.owner)
        self.assertFalse(can_final_approve_defaults(self.owner, self.project))

    def test_solo_workspace_creator_without_controlled_project_assignment_fails_closed(self):
        self.project.enterprise_project = None
        self.project.save(update_fields=['enterprise_project'])
        self.assertFalse(can_final_approve_defaults(self.owner, self.project))

    def test_direct_approval_cannot_bypass_pending_required_reviewers(self):
        self.assure()
        review = self.review()
        with self.assertRaises(ScheduleApprovalError) as failure:
            approve_schedule_version(self.version, self.owner)
        self.assertEqual(failure.exception.payload['code'], 'schedule_review_pending')
        self.assertFalse(self.capability(review, self.owner))
        self.assertTrue(self.capability(review, self.reviewer))
        self.assertFalse(self.capability(review, self.admin))

    def test_ordered_review_then_authority_and_no_repeat_decision(self):
        self.assure()
        review = self.review()
        decide_schedule_review(self.version, review.pk, self.reviewer, decision='approved')
        self.assertTrue(self.capability(review, self.owner))
        with self.assertRaises(ScheduleApprovalError):
            decide_schedule_review(self.version, review.pk, self.reviewer, decision='rejected', comment='Replay')
        decide_schedule_review(self.version, review.pk, self.owner, decision='approved')
        self.version.refresh_from_db()
        review.refresh_from_db()
        self.assertEqual(self.version.status, 'approved')
        self.assertEqual(review.status, 'approved')

    def test_baseline_capability_requires_project_authority_access_and_current_approved_parent(self):
        self.assure()
        self.assertFalse(can_baseline_schedule(self.version, self.owner))
        self.version = approve_schedule_version(self.version, self.owner)
        self.assertTrue(can_baseline_schedule(self.version, self.owner))
        self.assertFalse(can_baseline_schedule(self.version, self.admin))
        self.deny(self.owner)
        self.assertFalse(can_baseline_schedule(self.version, self.owner))
        self.assertTrue(can_baseline_schedule(self.version, self.manager))
        ScheduleVersion.objects.create(schedule=self.schedule, version=2, parent_version=self.version, created_by=self.owner)
        self.assertFalse(can_baseline_schedule(self.version, self.manager))

    def test_reviewer_approval_grant_revocation_is_immediate(self):
        review = self.review()
        self.deny(self.reviewer)
        self.assertFalse(self.capability(review, self.reviewer))
        with self.assertRaises(ScheduleApprovalError):
            decide_schedule_review(self.version, review.pk, self.reviewer, decision='approved')
        self.assertFalse(review.decisions.exclude(status='pending').exists())

    def test_recalculated_review_allows_only_assigned_current_stage_rejection_with_audit(self):
        review = self.review()
        calculate_schedule_version(self.version)
        self.version.refresh_from_db()
        review.refresh_from_db()
        self.assertFalse(self.capability(review, self.reviewer))
        capability = ScheduleReviewSerializer(review, context={'request': SimpleNamespace(user=self.reviewer)}).data
        self.assertTrue(capability['can_reject'])
        with self.assertRaises(ScheduleApprovalError):
            decide_schedule_review(self.version, review.pk, self.owner, decision='rejected')
        with self.assertRaises(ScheduleApprovalError):
            decide_schedule_review(self.version, review.pk, self.admin, decision='rejected')
        decide_schedule_review(self.version, review.pk, self.reviewer, decision='rejected', comment='Recalculated; review again')
        review.refresh_from_db()
        self.assertEqual(review.status, 'rejected')
        audit = PlanningAuditEvent.objects.get(action='governance.review_decided')
        self.assertTrue(audit.metadata['closed_after_recalculation'])

    def test_new_parent_version_invalidates_old_review_and_assurance(self):
        self.assure()
        review = self.review()
        ScheduleVersion.objects.create(schedule=self.schedule, version=2, parent_version=self.version, created_by=self.owner)
        self.assertFalse(self.capability(review, self.reviewer))
        with self.assertRaises(ScheduleApprovalError):
            decide_schedule_review(self.version, review.pk, self.reviewer, decision='rejected', comment='Old version')
        with self.assertRaises(ScheduleApprovalError):
            approve_schedule_version(self.version, self.owner)

    def test_api_schedule_capability_matches_final_decision_actor(self):
        self.assure()
        url = f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/'
        self.client.force_authenticate(self.admin)
        self.assertFalse(self.client.get(url).data['can_approve'])
        self.assertEqual(self.client.post(url + 'approve/').status_code, 403)
        self.client.force_authenticate(self.owner)
        self.assertTrue(self.client.get(url).data['can_approve'])
        self.assertEqual(self.client.post(url + 'approve/').status_code, 200)
        self.assertFalse(self.client.get(url).data['can_approve'])

    def test_basis_and_generation_plan_require_authority_access_and_current_parent(self):
        run = DocumentIntelligenceRun.objects.create(project=self.project, started_at=timezone.now(), status='succeeded')
        basis = ScheduleBasis.objects.create(project=self.project, source_run=run, status='ready', readiness={'ready': True})
        context = lambda user: {'request': SimpleNamespace(user=user)}
        self.assertFalse(ScheduleBasisSerializer(basis, context=context(self.admin)).data['can_approve'])
        self.assertTrue(ScheduleBasisSerializer(basis, context=context(self.owner)).data['can_approve'])
        with self.assertRaises(PermissionDenied):
            approve_schedule_basis(basis, self.admin)
        denied = self.deny(self.owner)
        with self.assertRaises(PermissionDenied):
            approve_schedule_basis(basis, self.owner)
        denied.delete()
        with patch('apps.planning_intelligence.services.schedule_basis.refresh_basis_readiness', return_value={'ready': True}):
            basis = approve_schedule_basis(basis, self.owner)
        plan = GenerationPlan.objects.create(project=self.project, basis=basis, status='ready', readiness={'ready': True})
        self.assertTrue(GenerationPlanSerializer(plan, context=context(self.owner)).data['can_approve'])
        with self.assertRaises(PermissionDenied):
            approve_generation_plan(plan, self.admin)
        basis.status = 'superseded'
        basis.save(update_fields=['status'])
        plan.basis = basis
        self.assertFalse(GenerationPlanSerializer(plan, context=context(self.owner)).data['can_approve'])
        with self.assertRaises(ValidationError):
            approve_generation_plan(plan, self.owner)

    @patch('apps.planning_intelligence.services.proposal_workflow.NotificationService.create_notification')
    def test_proposal_requires_live_exact_task_grant_and_completed_prior_review(self, notify):
        proposal = TechnicalProposal.objects.create(project=self.project, schedule_version=self.version, proposal_number='GATED-PROP', title='Proposal', created_by=self.owner)
        proposal = submit_for_review(proposal.pk, self.owner, reviewer_id=self.reviewer.pk)
        review_task = proposal.workflow_tasks.get(status='pending')
        self.assertTrue(can_decide_proposal_task(proposal, self.reviewer, review_task))
        self.assertFalse(can_decide_proposal_task(proposal, self.admin))
        denied = self.deny(self.reviewer)
        with self.assertRaises(PermissionDenied):
            reviewer_decision(proposal.pk, self.reviewer, decision='complete', approver_id=self.manager.pk)
        denied.delete()
        proposal = reviewer_decision(proposal.pk, self.reviewer, decision='complete', approver_id=self.manager.pk)
        self.assertFalse(can_decide_proposal_task(proposal, self.reviewer, review_task))
        self.assertTrue(can_decide_proposal_task(proposal, self.manager))
        flags = TechnicalProposalSerializer(proposal, context={'request': SimpleNamespace(user=self.manager)}).data['workflow_permissions']
        self.assertTrue(flags['can_approve'])
        proposal = approver_decision(proposal.pk, self.manager, decision='approve')
        self.assertEqual(proposal.approved_by, self.manager)
        self.assertFalse(can_decide_proposal_task(proposal, self.manager))
        self.assertEqual(notify.call_args_list[0].kwargs['metadata']['proposal_task_id'], review_task.pk)

    @patch('apps.planning_intelligence.services.proposal_workflow.NotificationService.create_notification')
    def test_reassigned_proposal_task_never_reuses_old_notification_authority(self, _notify):
        proposal = TechnicalProposal.objects.create(project=self.project, schedule_version=self.version, proposal_number='REASSIGN-PROP', title='Proposal', created_by=self.owner)
        proposal = submit_for_review(proposal.pk, self.owner, reviewer_id=self.reviewer.pk)
        old_task = proposal.workflow_tasks.get(status='pending')
        proposal = reassign_reviewer(proposal.pk, self.owner, reviewer_id=self.manager.pk)
        self.assertFalse(can_decide_proposal_task(proposal, self.reviewer, old_task))
        self.assertFalse(can_decide_proposal_task(proposal, self.manager, old_task))
        self.assertTrue(can_decide_proposal_task(proposal, self.manager))
