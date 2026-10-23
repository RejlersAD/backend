from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.core.management import call_command
from apps.hr_core.models import HRWorkflowEvent
from apps.payroll.models import LeaveRequest, LeaveType
from apps.procurement.tests.approval_fixtures import grant_approval, set_position
from apps.payroll.tests import tests_hr_department_enhancements as fixtures


@override_settings(ROOT_URLCONF=fixtures.__name__)
class LeaveReferralTests(TestCase):
    setUp = fixtures.DepartmentEnhancementTests.setUp
    submit = fixtures.DepartmentEnhancementTests.submit
    act = fixtures.DepartmentEnhancementTests.act
    queue = fixtures.DepartmentEnhancementTests.queue

    def test_manager_referral_transfers_queue_without_decision_and_notifies(self):
        pk = self.submit().data['id']
        self.notifications.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.act(self.manager, pk, 'refer-to', approver_id=self.other.pk, note='Please cover my absence')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'PENDING')
        self.assertFalse(response.data['can_review'])
        self.assertEqual(response.data['referral_history'][0]['note'], 'Please cover my absence')
        leave = LeaveRequest.objects.get(pk=pk)
        task = leave.workflow_instance.tasks.get(stage__code='manager_review')
        self.assertEqual(task.status, 'pending')
        self.assertIsNone(task.decided_at)
        self.assertIsNone(leave.rm_reviewed_at)
        self.assertEqual(task.assigned_to, self.other)
        self.assertEqual([call.kwargs['recipient'] for call in self.notifications.call_args_list], [self.other])
        self.assertEqual(self.queue(self.manager), [])
        self.assertEqual(len(self.queue(self.other)), 1)
        self.assertEqual(self.act(self.manager, pk, 'rm-approve').status_code, 403)
        self.assertEqual(self.act(self.manager, pk, 'refer-to', approver_id=self.hr.pk).status_code, 403)
        self.assertEqual(self.act(self.other, pk, 'rm-approve').status_code, 200)
        self.assertEqual(self.act(self.hr, pk, 'approve').status_code, 200)
        self.client.force_authenticate(self.other)
        detail = self.client.get(f'/api/v1/payroll/leave-requests/{pk}/')
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(len(detail.data['referral_history']), 1)

    def test_hr_referral_preserves_prior_approval_and_hr_authority(self):
        alternate = get_user_model().objects.create_user(username='alternate', email='alternate@example.test')
        grant_approval(alternate, 'payroll')
        set_position(alternate, 'HR Manager')
        pk = self.submit().data['id']
        self.assertEqual(self.act(self.manager, pk, 'rm-approve').status_code, 200)
        self.assertEqual(self.act(self.hr, pk, 'refer-to', approver_id=self.other.pk).status_code, 400)
        self.notifications.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.act(self.hr, pk, 'refer-to', approver_id=alternate.pk)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'RM_APPROVED')
        self.assertEqual([call.kwargs['recipient'] for call in self.notifications.call_args_list], [alternate])
        self.assertEqual(self.queue(self.hr), [])
        self.assertEqual(len(self.queue(alternate)), 1)
        self.assertEqual(self.act(self.hr, pk, 'approve').status_code, 403)
        result = self.act(alternate, pk, 'approve')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['rm_reviewed_by'], self.manager.pk)
        self.assertEqual(result.data['reviewed_by'], alternate.pk)

    def test_referral_chain_audits_each_transfer_and_allows_rejection(self):
        pk = self.submit().data['id']
        for actor, target in [(self.manager, self.other), (self.other, self.hr), (self.hr, self.manager)]:
            result = self.act(actor, pk, 'refer-to', approver_id=target.pk)
            self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(len(result.data['referral_history']), 3)
        result = self.act(self.manager, pk, 'rm-reject', note='Cannot cover')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['status'], 'RM_REJECTED')
        self.assertEqual(self.act(self.manager, pk, 'refer-to', approver_id=self.other.pk).status_code, 403)

    def test_invalid_and_unprivileged_targets_do_not_change_assignment(self):
        unprivileged = get_user_model().objects.create_user(username='unprivileged')
        pk = self.submit().data['id']
        for target in (self.employee.pk, self.manager.pk, unprivileged.pk, 999999, 'bad', None):
            result = self.act(self.manager, pk, 'refer-to', approver_id=target)
            self.assertEqual(result.status_code, 400, result.data)
        self.other.is_active = False
        self.other.save()
        self.assertEqual(self.act(self.manager, pk, 'refer-to', approver_id=self.other.pk).status_code, 400)
        self.assertFalse(HRWorkflowEvent.objects.filter(event_type='referred').exists())
        self.assertEqual(len(self.queue(self.manager)), 1)

    def test_options_include_all_employees_and_accounts_but_require_current_reviewer(self):
        from datetime import date
        from apps.hr_core.models import EmployeeMaster
        unprivileged = get_user_model().objects.create_user(username='directory', email='directory@example.test')
        EmployeeMaster.objects.create(employee_number='DIRECTORY-1', employee_code='DIR1',
            emp_code='DIR1', first_name='Imported', last_name='Employee',
            email='imported@example.test', join_date=date(2020, 1, 1), department='Engineering')
        pk = self.submit().data['id']
        url = f'/api/v1/payroll/leave-requests/{pk}/referral-options/'
        self.client.force_authenticate(self.employee)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_authenticate(self.manager)
        result = self.client.get(url)
        self.assertEqual(result.status_code, 200, result.data)
        ids = [item['id'] for item in result.data['results']]
        self.assertIn(self.other.pk, ids)
        self.assertIn(self.manager.pk, ids)
        self.assertIn(self.employee.pk, ids)
        self.assertIn(unprivileged.pk, ids)
        imported = next(item for item in result.data['results'] if item['employee_code'] == 'DIR1')
        self.assertEqual(imported['department'], 'Engineering')
        self.assertIsNone(imported['id'])
        self.assertFalse(imported['eligible'])
        self.assertEqual(self.act(self.manager, pk, 'refer-to', approver_id=unprivileged.pk).status_code, 400)

    def test_audit_failure_rolls_back_assignment_and_notification(self):
        pk = self.submit().data['id']
        with patch.object(HRWorkflowEvent.objects, 'create', side_effect=RuntimeError('Audit unavailable')):
            with self.assertRaises(RuntimeError):
                self.act(self.manager, pk, 'refer-to', approver_id=self.other.pk)
        self.assertFalse(HRWorkflowEvent.objects.filter(event_type='referred').exists())
        self.assertEqual(len(self.queue(self.manager)), 1)

    def test_removed_approval_grant_blocks_referred_reviewer(self):
        pk = self.submit().data['id']
        self.assertEqual(self.act(self.manager, pk, 'refer-to', approver_id=self.other.pk).status_code, 200)
        with patch('apps.payroll.services.leave_approval.approval_access', return_value=False):
            self.assertEqual(self.queue(self.other), [])
            self.assertEqual(self.act(self.other, pk, 'rm-approve').status_code, 403)

    def test_referral_survives_original_manager_removal_and_reminders(self):
        from apps.hr_core.workflows import HRWorkflowService
        pk = self.submit().data['id']
        self.assertEqual(self.act(self.manager, pk, 'refer-to', approver_id=self.other.pk).status_code, 200)
        self.employee_record.manager = None
        self.employee_record.save()
        leave = LeaveRequest.objects.get(pk=pk)
        task = leave.workflow_instance.tasks.get(status='pending')
        self.assertEqual(HRWorkflowService._task_recipients(task), [self.other])
        self.assertEqual(self.act(self.other, pk, 'approve').status_code, 400)
        self.assertEqual(self.act(self.other, pk, 'rm-approve').status_code, 200)
        self.assertEqual(len(self.queue(self.hr)), 1)

    def test_flex_referral_completes_only_current_stage_then_continues_route(self):
        self.leave_type = LeaveType.objects.create(code='FU', name='Flex Utilization', category='flex_utilization')
        call_command('configure_flex_leave', jamal=self.manager.email, rafat=self.other.email, mohammed=self.hr.email)
        substitute = get_user_model().objects.create_user(username='substitute', email='substitute@example.test')
        grant_approval(substitute, 'payroll')
        pk = self.submit().data['id']
        result = self.act(self.manager, pk, 'refer-to', approver_id=substitute.pk)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(self.queue(self.manager), [])
        result = self.act(substitute, pk, 'rm-approve')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['review_stage'], 'rafat_review')
        self.assertEqual(result.data['status'], 'PENDING')
        self.assertEqual(len(self.queue(self.other)), 1)
        self.assertEqual(self.act(self.other, pk, 'rm-approve').status_code, 200)
        self.assertEqual(self.act(self.hr, pk, 'rm-approve').status_code, 200)
        self.assertEqual(self.act(self.hr, pk, 'approve').status_code, 200)
