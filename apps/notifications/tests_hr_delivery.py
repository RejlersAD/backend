"""HR approval alerts follow current business routing; result notices remain readable."""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.hr_core.models import HRWorkflowDefinition, HRWorkflowInstance, HRWorkflowStage, HRWorkflowTask
from apps.notifications.delivery import approval_assignment_issue, delivery_issue
from apps.notifications.models import Notification, NotificationCategory
from apps.notifications.serializers import NotificationListSerializer
from apps.notifications.services import NotificationService
from apps.payroll.models import LeaveRequest, LeaveType
from apps.procurement.tests.approval_fixtures import grant_approval, set_position
from apps.rbac.models import Organization, UserProfile, UserRole


@override_settings(TEAMS_APPROVAL_WEBHOOK_URL='', WEB_PUSH_VAPID_PRIVATE_KEY='')
class HRNotificationEligibilityTests(TestCase):
    def setUp(self):
        org = Organization.objects.create(name='HR notification gates', code='HR-NOTIFY-GATES')
        self.users = []
        self.employees = []
        for index, title in enumerate(('Engineer', 'Project Manager', 'HR Manager', 'CEO')):
            user = get_user_model().objects.create_user(
                username=f'hr-notify-{index}', email=f'hr-notify-{index}@example.test', is_superuser=index == 3,
            )
            UserProfile.objects.get_or_create(user=user, defaults={'organization': org})
            grant_approval(user, 'payroll', 'hr_management')
            self.users.append(user)
            self.employees.append(set_position(user, title))
        self.employee, self.manager, self.hr, self.ceo = self.users
        self.employees[0].manager = self.employees[1]
        self.employees[0].save(update_fields=['manager'])
        definition = HRWorkflowDefinition.objects.create(
            code='hr-notification-gates', name='HR notification gates', subject_type='payroll.leave_request',
        )
        self.manager_stage = HRWorkflowStage.objects.create(
            definition=definition, code='manager_review', name='Manager review', sequence=1,
            approver_type='employee_manager',
        )
        self.hr_stage = HRWorkflowStage.objects.create(
            definition=definition, code='hr_review', name='HR review', sequence=2,
            approver_type='role', approver_value='hr_manager',
        )
        leave_type = LeaveType.objects.create(code='GATES', name='Test leave')
        self.request = LeaveRequest.objects.create(
            employee=self.employee, canonical_employee=self.employees[0], employee_name='Employee',
            leave_type=leave_type, start_date=date(2026, 10, 1), end_date=date(2026, 10, 1),
            days_requested=1, status='PENDING',
        )
        self.instance = HRWorkflowInstance.objects.create(
            definition=definition, employee=self.employees[0], subject_type='payroll.leave_request',
            subject_id=str(self.request.pk), requested_by=self.employee, current_stage=self.manager_stage,
        )
        self.request.workflow_instance = self.instance
        self.request.save(update_fields=['workflow_instance'])
        self.manager_task = HRWorkflowTask.objects.create(
            instance=self.instance, stage=self.manager_stage, assigned_to=self.manager,
        )
        self.hr_task = HRWorkflowTask.objects.create(
            instance=self.instance, stage=self.hr_stage, assigned_role_code='hr_manager',
        )

    def notice(self, recipient, task=None, **kwargs):
        return Notification.objects.create(
            recipient=recipient, title=kwargs.pop('title', 'Review leave request'),
            message='A leave request needs review.', status='SENT',
            action_url=f'/approvals?leave={self.request.pk}', action_label=kwargs.pop('action_label', 'Review request'),
            metadata=kwargs.pop('metadata', {'workflow_task_id': str((task or self.manager_task).pk)}), **kwargs,
        )

    def advance_to_hr(self):
        HRWorkflowTask.objects.filter(pk=self.manager_task.pk).update(status='approved')
        HRWorkflowInstance.objects.filter(pk=self.instance.pk).update(current_stage=self.hr_stage)
        LeaveRequest.objects.filter(pk=self.request.pk).update(status='RM_APPROVED')

    def test_only_current_manager_receives_first_stage_and_hr_waits(self):
        self.assertEqual(approval_assignment_issue(self.notice(self.manager)), '')
        self.assertTrue(approval_assignment_issue(self.notice(self.hr, self.hr_task)))
        self.assertTrue(approval_assignment_issue(self.notice(self.ceo)))

    def test_hr_position_and_permission_are_required_after_manager_approval(self):
        self.advance_to_hr()
        notice = self.notice(self.hr, self.hr_task)
        self.assertEqual(approval_assignment_issue(notice), '')
        self.assertTrue(approval_assignment_issue(self.notice(self.ceo, self.hr_task)))
        UserRole.objects.filter(user_profile=self.hr.rbac_profile).delete()
        self.assertTrue(approval_assignment_issue(notice))

    def test_changing_current_stage_without_lower_approval_does_not_open_hr_delivery(self):
        HRWorkflowInstance.objects.filter(pk=self.instance.pk).update(current_stage=self.hr_stage)
        LeaveRequest.objects.filter(pk=self.request.pk).update(status='RM_APPROVED')
        self.assertTrue(approval_assignment_issue(self.notice(self.hr, self.hr_task)))

    def test_manager_reassignment_invalidates_old_recipient_and_keeps_new_manager_eligible(self):
        old_notice = self.notice(self.manager)
        self.employees[0].manager = self.employees[3]
        self.employees[0].save(update_fields=['manager'])
        self.assertTrue(approval_assignment_issue(old_notice))
        # A CEO may act only when explicitly assigned the real manager responsibility.
        self.assertEqual(approval_assignment_issue(self.notice(self.ceo)), '')

    def test_completed_cancelled_missing_task_and_wrong_stage_are_stale_for_every_channel(self):
        notice = self.notice(self.manager)
        for status in ('approved', 'rejected', 'cancelled'):
            HRWorkflowTask.objects.filter(pk=self.manager_task.pk).update(status=status)
            for channel in ('web_push', 'teams'):
                self.assertTrue(delivery_issue(notice, channel))
        self.manager_task.delete()
        self.assertTrue(approval_assignment_issue(notice))
        self.assertFalse(NotificationListSerializer(notice).data['metadata']['requires_action'])

    def test_legacy_hr_approval_alert_revalidates_but_employee_result_is_not_suppressed(self):
        self.advance_to_hr()
        approval = self.notice(self.hr, title='HR leave approval required', action_label='Review leave',
                               metadata={'leave_request_id': str(self.request.pk)})
        result = self.notice(self.employee, title='Leave request updated', action_label='View leave request',
                             metadata={'leave_request_id': str(self.request.pk)})
        self.assertEqual(approval_assignment_issue(approval), '')
        LeaveRequest.objects.filter(pk=self.request.pk).update(status='APPROVED')
        self.assertTrue(approval_assignment_issue(approval))
        self.assertEqual(approval_assignment_issue(result), '')
        self.assertEqual(delivery_issue(result, 'web_push'), '')

    def test_unknown_typed_approval_context_cannot_claim_to_be_actionable(self):
        notice = self.notice(self.hr, metadata={'requires_action': True, 'unregistered_workflow_id': 'example'})
        self.assertEqual(approval_assignment_issue(notice), 'approval_context_unsupported')

    def test_untyped_approval_category_requires_registered_business_context(self):
        category = NotificationCategory.objects.get_or_create(name='APPROVAL')[0]
        notice = self.notice(self.hr, metadata={}, category=category, title='Document approval required')
        self.assertEqual(approval_assignment_issue(notice), 'approval_context_unsupported')
        notice.metadata = {'requires_action': False, 'event_type': 'approval_result'}
        self.assertEqual(approval_assignment_issue(notice), '')

    def test_creation_rejects_wrong_business_recipient_before_in_app_row_exists(self):
        metadata = {'workflow_task_id': str(self.manager_task.pk), 'requires_action': True}
        kwargs = {'title': 'Manager approval', 'message': 'Review leave', 'category': 'APPROVAL', 'metadata': metadata}
        self.assertIsNone(NotificationService.create_notification(self.ceo, **kwargs))
        self.assertFalse(Notification.objects.filter(title='Manager approval', recipient=self.ceo).exists())
        self.assertIsNotNone(NotificationService.create_notification(self.manager, **kwargs))
