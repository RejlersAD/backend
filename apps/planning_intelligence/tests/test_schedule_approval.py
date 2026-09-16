"""Approval routes must share authority, assurance, state and atomic audit gates."""
import datetime as dt
from unittest.mock import patch

from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.project_models import Project, ProjectMember
from apps.users.models import User

from ..models import (
    GovernanceItem, PlanningAuditEvent, PlanningGeneration, ScheduleBaseline,
    ScheduleReview, ScheduleReviewDecision,
)
from ..services.audit import record_event
from ..services.cpm import calculate_schedule_version
from ..services.schedule_approval import (
    ScheduleApprovalError, approve_schedule_version, decide_schedule_review,
)
from ..services.trustworthy_scheduling import approve_schedule_assurance, run_schedule_assurance
from ..services.workable_plan import approve_workable_baseline
from .test_scheduling_engine import ScheduleFixture
from .test_business_approval_gates import grant_test_approval


class ScheduleApprovalTests(ScheduleFixture):
    def setUp(self):
        super().setUp()
        self.enterprise = Project.objects.create(code='APPROVAL-TEST', name='Approval test', owner=self.owner)
        self.project.enterprise_project = self.enterprise
        self.project.save(update_fields=['enterprise_project', 'updated_at'])
        self.reviewer = User.objects.create_user(username='approval-reviewer', email='approval-reviewer@example.com')
        ProjectMember.objects.create(project=self.enterprise, user=self.reviewer, role='reviewer')
        grant_test_approval((self.owner, self.reviewer))
        self.activity('APPROVAL-A', 2)
        calculate_schedule_version(self.version)
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.base_url = f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/'

    def assure(self, *, approve=True):
        assurance = run_schedule_assurance(self.version)
        if approve:
            assurance = approve_schedule_assurance(self.version, self.owner)
        return assurance

    def review(self, *users):
        review = ScheduleReview.objects.create(
            version=self.version, title='Approval test review', requested_by=self.owner,
            requested_at=timezone.now(),
        )
        for user in users or (self.owner,):
            ScheduleReviewDecision.objects.create(review=review, reviewer=user)
        return review

    def vote(self, review, *, user=None, decision='approved', comment='Accepted'):
        self.client.force_authenticate(user or self.owner)
        return self.client.post(self.base_url + 'review-decision/', {
            'review_id': review.pk, 'decision': decision, 'comment': comment,
        }, format='json')

    def assert_pending(self, review):
        self.version.refresh_from_db()
        review.refresh_from_db()
        self.assertEqual(self.version.status, 'calculated')
        self.assertEqual(review.status, 'pending')
        self.assertIsNone(review.completed_at)
        self.assertTrue(all(vote.status == 'pending' and vote.decided_at is None for vote in review.decisions.all()))
        self.assertFalse(PlanningAuditEvent.objects.filter(action='schedule.approved').exists())

    def test_direct_and_unanimous_review_require_current_approved_assurance(self):
        review = self.review()
        direct = self.client.post(self.base_url + 'approve/')
        unanimous = self.vote(review)
        for response in (direct, unanimous):
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.data['code'], 'schedule_assurance_required')
        self.assert_pending(review)

    def test_direct_approval_records_exact_assurance_and_actor_once(self):
        assurance = self.assure()
        response = self.client.post(self.base_url + 'approve/')
        self.assertEqual(response.status_code, 200, response.data)
        event = PlanningAuditEvent.objects.get(action='schedule.approved')
        self.assertEqual(event.actor, self.owner)
        self.assertEqual(event.before, {'status': 'calculated'})
        self.assertEqual(event.after['assurance_review_id'], assurance.pk)
        self.assertEqual(event.after['assurance_input_fingerprint'], assurance.input_fingerprint)
        self.assertEqual(event.metadata['approval_route'], 'direct')
        self.assertEqual(self.client.post(self.base_url + 'approve/').status_code, 409)
        self.assertEqual(PlanningAuditEvent.objects.filter(action='schedule.approved').count(), 1)

    def test_review_approves_only_after_reviewers_then_authority_and_audits_both(self):
        self.assure()
        review = self.review(self.reviewer, self.owner)
        first = self.vote(review, user=self.reviewer)
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data['status'], 'pending')
        self.version.refresh_from_db()
        self.assertEqual(self.version.status, 'calculated')
        final = self.vote(review)
        self.assertEqual(final.status_code, 200, final.data)
        self.assertEqual(final.data['status'], 'approved')
        event = PlanningAuditEvent.objects.get(action='schedule.approved')
        self.assertEqual(event.actor, self.owner)
        self.assertEqual(event.metadata, {'approval_route': 'governance_review', 'review_id': review.pk})
        self.assertEqual(PlanningAuditEvent.objects.filter(action='governance.review_decided').count(), 2)
        self.assertEqual(self.vote(review).status_code, 409)

    def test_authority_cannot_vote_before_remaining_reviewers(self):
        self.assure()
        review = self.review(self.reviewer, self.owner)
        response = self.vote(review)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'schedule_review_awaiting_reviewers')
        self.assertIn('Remaining reviewers must respond', response.data['error'])
        self.assert_pending(review)

    def test_unauthorized_final_vote_and_direct_approval_cannot_change_state(self):
        self.assure()
        # Legacy reviews may lack an authority; they must never bypass the gate.
        review = self.review(self.reviewer)
        response = self.vote(review, user=self.reviewer)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['code'], 'schedule_approval_forbidden')
        self.assert_pending(review)
        self.assertEqual(self.client.post(self.base_url + 'approve/').status_code, 403)

    def test_unassigned_staff_cannot_record_another_reviewers_vote(self):
        staff = User.objects.create_user(username='approval-admin', email='approval-admin@example.com', is_staff=True)
        review = self.review(self.owner)
        response = self.vote(review, user=staff)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['code'], 'schedule_review_unassigned')
        self.assert_pending(review)
        self.assertEqual(review.decisions.get().reviewer, self.owner)

    def test_new_review_requires_an_assigned_authority(self):
        response = self.client.post(self.base_url + 'reviews/', {
            'title': 'No authority', 'reviewer_ids': [self.reviewer.pk],
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], 'schedule_review_authority_required')
        self.assertFalse(ScheduleReview.objects.exists())
        response = self.client.post(self.base_url + 'reviews/', {
            'title': 'Authority included', 'reviewer_ids': [self.reviewer.pk, self.owner.pk],
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)

    def test_stale_or_archived_assurance_cannot_be_used_by_either_route(self):
        assurance = self.assure()
        review = self.review()
        for failure in ('stale', 'archived'):
            with self.subTest(failure=failure):
                if failure == 'stale':
                    assurance.input_fingerprint = 'outdated-input-state'
                    assurance.save(update_fields=['input_fingerprint'])
                else:
                    assurance = self.assure()
                    assurance.soft_delete()
                for response in (self.client.post(self.base_url + 'approve/'), self.vote(review)):
                    self.assertEqual(response.status_code, 409)
                    self.assertEqual(response.data['code'], 'schedule_assurance_required')
                self.assert_pending(review)

    def test_approved_assurance_cannot_hide_generation_findings_or_unconfirmed_gates(self):
        generation = PlanningGeneration.objects.create(
            project=self.project, version=1, generated_by=self.owner,
            validation=[{'severity': 'critical', 'message': 'Required design scope missing'}],
            logic_matrix=[{'source': 'dependency_template', 'requires_confirmation': True}],
        )
        self.version.source_generation = generation
        self.version.save(update_fields=['source_generation'])
        self.assure()
        review = self.review()
        for response in (self.client.post(self.base_url + 'approve/'), self.vote(review)):
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.data['code'], 'schedule_assurance_blocked')
            self.assertEqual(response.data['critical_finding_count'], 1)
            self.assertEqual(response.data['unconfirmed_gate_count'], 1)
        self.assert_pending(review)

    def test_approval_reloads_state_instead_of_trusting_old_version_instance(self):
        self.assure()
        self.version.__class__.objects.filter(pk=self.version.pk).update(status='draft')
        with self.assertRaises(ScheduleApprovalError) as error:
            approve_schedule_version(self.version, self.owner)
        self.assertEqual(error.exception.payload['code'], 'schedule_approval_state')
        self.version.refresh_from_db()
        self.assertEqual(self.version.status, 'draft')

    def test_recalculated_schedule_needs_a_new_governance_review(self):
        review = self.review()
        calculate_schedule_version(self.version)
        self.assure()
        response = self.vote(review)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'schedule_review_stale')
        self.assert_pending(review)

    def test_rejected_and_changes_requested_reviews_do_not_require_assurance_or_approve(self):
        for decision in ('rejected', 'changes_requested'):
            with self.subTest(decision=decision):
                review = self.review(self.reviewer, self.owner)
                response = self.vote(review, user=self.reviewer, decision=decision, comment='Revise the release logic.')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data['status'], decision)
                self.version.refresh_from_db()
                self.assertEqual(self.version.status, 'calculated')
        self.assertFalse(PlanningAuditEvent.objects.filter(action='schedule.approved').exists())

    def test_invalid_review_ids_are_validation_errors(self):
        for review_id in ('not-an-id', -1, None):
            response = self.client.post(self.base_url + 'review-decision/', {
                'review_id': review_id, 'decision': 'approved',
            }, format='json')
            self.assertEqual(response.status_code, 400)

    def test_direct_approval_rolls_back_if_audit_cannot_be_written(self):
        self.assure()
        with patch('apps.planning_intelligence.services.schedule_approval.record_event', side_effect=RuntimeError('audit failed')):
            with self.assertRaisesMessage(RuntimeError, 'audit failed'):
                approve_schedule_version(self.version, self.owner)
        self.version.refresh_from_db()
        self.assertEqual(self.version.status, 'calculated')
        self.assertFalse(PlanningAuditEvent.objects.filter(action='schedule.approved').exists())

    def test_review_audit_failure_rolls_back_vote_approval_and_approval_event(self):
        self.assure()
        review = self.review()

        def audit_or_fail(**kwargs):
            if kwargs['action'] == 'governance.review_decided':
                raise RuntimeError('review audit failed')
            return record_event(**kwargs)

        with patch('apps.planning_intelligence.services.schedule_approval.record_event', side_effect=audit_or_fail):
            with self.assertRaisesMessage(RuntimeError, 'review audit failed'):
                decide_schedule_review(self.version, review.pk, self.owner, decision='approved')
        self.assert_pending(review)

    def test_workable_baseline_uses_same_gate_and_rolls_back_assurance_on_failure(self):
        generation = PlanningGeneration.objects.create(
            project=self.project, version=1, generated_by=self.owner,
            logic_matrix=[{'source': 'dependency_template', 'requires_confirmation': True}],
        )
        self.version.source_generation = generation
        self.version.save(update_fields=['source_generation'])
        assurance = self.assure(approve=False)
        with self.assertRaises(ScheduleApprovalError) as error:
            approve_workable_baseline(self.project, self.owner, self.version.pk, 'Test baseline', lambda *_: None)
        self.assertEqual(error.exception.payload['code'], 'schedule_assurance_blocked')
        assurance.refresh_from_db()
        self.version.refresh_from_db()
        self.assertEqual(assurance.status, 'ready')
        self.assertEqual(self.version.status, 'calculated')
        self.assertFalse(ScheduleBaseline.objects.exists())
        self.assertFalse(PlanningAuditEvent.objects.filter(action__in=['schedule.approved', 'schedule.assurance_approved']).exists())

    def test_workable_baseline_requires_authority_and_audits_success(self):
        self.assure(approve=False)
        with self.assertRaises(ScheduleApprovalError) as error:
            approve_workable_baseline(self.project, self.reviewer, self.version.pk, 'Test baseline', lambda *_: None)
        self.assertEqual(error.exception.status_code, 403)
        result = approve_workable_baseline(self.project, self.owner, self.version.pk, 'Test baseline', lambda *_: None)
        self.assertEqual(result['state'], 'baselined')
        self.assertEqual(PlanningAuditEvent.objects.get(action='schedule.approved').metadata['approval_route'], 'workable_baseline')
        self.assertEqual(PlanningAuditEvent.objects.filter(action='schedule.baselined').count(), 1)
        repeated = approve_workable_baseline(self.project, self.owner, self.version.pk, 'Test baseline', lambda *_: None)
        self.assertEqual(repeated['baseline']['id'], result['baseline']['id'])
        self.assertEqual(PlanningAuditEvent.objects.filter(action='schedule.approved').count(), 1)

    def test_baseline_still_requires_current_assurance_and_no_critical_governance(self):
        self.assure()
        self.assertEqual(self.client.post(self.base_url + 'approve/').status_code, 200)
        self.project.planned_end_date = dt.date(2027, 1, 1)
        self.project.save(update_fields=['planned_end_date'])
        self.assertEqual(self.client.post(self.base_url + 'baseline/').status_code, 409)
        self.project.planned_end_date = None
        self.project.save(update_fields=['planned_end_date'])
        GovernanceItem.objects.create(version=self.version, item_type='issue', title='Blocking release', priority='critical')
        self.assertEqual(self.client.post(self.base_url + 'baseline/').status_code, 409)
        self.assertFalse(ScheduleBaseline.objects.exists())

    def test_baseline_audit_failure_rolls_back_snapshot_and_status(self):
        self.assure()
        self.assertEqual(self.client.post(self.base_url + 'approve/').status_code, 200)
        with patch('apps.planning_intelligence.schedule_views.record_event', side_effect=RuntimeError('baseline audit failed')):
            with self.assertRaisesMessage(RuntimeError, 'baseline audit failed'):
                self.client.post(self.base_url + 'baseline/')
        self.version.refresh_from_db()
        self.assertEqual(self.version.status, 'approved')
        self.assertFalse(ScheduleBaseline.objects.exists())
