"""Queued business tasks retain their original current assignment and authority."""

from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.core.project_models import Project, ProjectMember
from apps.notifications.delivery import approval_assignment_issue
from apps.notifications.models import Notification
from apps.onboarding.models import OffboardingRecord
from apps.planning_intelligence.models import (
    PlanningProject, ProposalWorkflowTask, Schedule, ScheduleVersion, TechnicalProposal,
)
from apps.procurement.tests.approval_fixtures import grant_approval, set_position
from apps.rbac.models import UserRole


class BusinessNotificationDeliveryTests(TestCase):
    def setUp(self):
        self.employee, self.manager, self.other = [get_user_model().objects.create_user(
            username=f'business-notify-{index}', email=f'business-notify-{index}@example.test',
            is_superuser=index == 2,
        ) for index in range(3)]
        for user in (self.employee, self.manager, self.other):
            grant_approval(user, 'planning_package', 'hr_onboarding')
            set_position(user)
        self.project = Project.objects.create(code='NOTICE', name='Notice project', owner=self.manager, status='active')
        ProjectMember.objects.create(project=self.project, user=self.employee, role='engineer', is_active=True)

    def notice(self, user, metadata):
        return Notification.objects.create(recipient=user, title='Business approval required', message='Review request',
                                           status='SENT', metadata={'requires_action': True, **metadata})

    def proposal(self):
        project = PlanningProject.objects.create(name='Proposal', enterprise_project=self.project, created_by=self.employee)
        schedule = Schedule.objects.create(project=project, name='Schedule', code='NOTICE', planned_start=date(2026, 1, 1))
        version = ScheduleVersion.objects.create(schedule=schedule)
        proposal = TechnicalProposal.objects.create(
            project=project, schedule_version=version, proposal_number='NOTICE-1', title='Proposal',
            status='internal_review', reviewer=self.manager, created_by=self.employee,
        )
        task = ProposalWorkflowTask.objects.create(proposal=proposal, task_type='review', assigned_to=self.manager)
        return proposal, task

    def test_proposal_queued_notice_requires_exact_current_task_recipient_and_permission(self):
        proposal, task = self.proposal()
        metadata = {'proposal_id': str(proposal.pk), 'proposal_task_id': str(task.pk), 'task_type': 'review'}
        notice = self.notice(self.manager, metadata)
        self.assertEqual(approval_assignment_issue(notice), '')
        self.assertTrue(approval_assignment_issue(self.notice(self.other, metadata)))
        UserRole.objects.filter(user_profile=self.manager.rbac_profile).delete()
        self.assertTrue(approval_assignment_issue(notice))

    def test_new_review_cycle_does_not_revive_old_task_notice_even_same_recipient(self):
        proposal, task = self.proposal()
        old = self.notice(self.manager, {'proposal_id': str(proposal.pk), 'proposal_task_id': str(task.pk)})
        legacy = self.notice(self.manager, {'proposal_id': str(proposal.pk)})
        ProposalWorkflowTask.objects.filter(pk=task.pk).update(status='cancelled')
        replacement = ProposalWorkflowTask.objects.create(proposal=proposal, task_type='review', assigned_to=self.manager)
        current = self.notice(self.manager, {'proposal_id': str(proposal.pk), 'proposal_task_id': str(replacement.pk)})
        self.assertTrue(approval_assignment_issue(old))
        self.assertTrue(approval_assignment_issue(legacy))
        self.assertEqual(approval_assignment_issue(current), '')
        ScheduleVersion.objects.filter(pk=proposal.schedule_version_id).update(status='superseded')
        self.assertTrue(approval_assignment_issue(current))

    def test_exit_notice_follows_current_project_manager_and_pending_decision(self):
        record = OffboardingRecord.objects.create(
            user=self.employee, employee_name='Employee', employee_email=self.employee.email,
            position='Engineer', department='Engineering', exit_reason='resignation',
            last_working_day=date(2026, 10, 1), target_completion_date=date(2026, 10, 1),
            project_manager_approval_status='pending',
        )
        metadata = {'offboarding_id': str(record.pk), 'action_type': 'offboarding_project_manager_decision'}
        notice = self.notice(self.manager, metadata)
        self.assertEqual(approval_assignment_issue(notice), '')
        self.assertTrue(approval_assignment_issue(self.notice(self.other, metadata)))
        Project.objects.filter(pk=self.project.pk).update(owner=self.other)
        self.assertTrue(approval_assignment_issue(notice))
        current = self.notice(self.other, metadata)
        self.assertEqual(approval_assignment_issue(current), '')
        OffboardingRecord.objects.filter(pk=record.pk).update(project_manager_approval_status='approved')
        self.assertTrue(approval_assignment_issue(current))

    def test_finance_payroll_notice_uses_current_named_reviewer_and_prior_stage_evidence(self):
        from apps.finance.payroll_workflow import PayrollWorkflow, WorkflowStage
        from apps.finance.salary_models import PayrollRun

        grant_approval(self.manager, 'payroll')
        run = PayrollRun.objects.create(run_code='NOTICE', month=10, year=2026,
                                        period_start=date(2026, 10, 1), period_end=date(2026, 10, 31))
        workflow = PayrollWorkflow.objects.create(payroll_run=run, current_stage=WorkflowStage.HR_REVIEW)
        metadata = {'finance_payroll_workflow_id': str(workflow.pk)}
        with patch.dict('apps.finance.payroll_workflow.WORKFLOW_STAKEHOLDERS', {
            'hr_manager': {'email': self.manager.email}, 'accounting': {'email': self.other.email},
        }):
            notice = self.notice(self.manager, metadata)
            self.assertEqual(approval_assignment_issue(notice), '')
            self.assertTrue(approval_assignment_issue(self.notice(self.other, metadata)))
            PayrollWorkflow.objects.filter(pk=workflow.pk).update(current_stage=WorkflowStage.ACCOUNTING_REVIEW)
            self.assertTrue(approval_assignment_issue(notice))
            # Even the next named reviewer cannot act without recorded HR approval.
            self.assertTrue(approval_assignment_issue(self.notice(self.other, metadata)))
