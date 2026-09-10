from datetime import date
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from apps.hr_core.models import EmployeeMaster, HRWorkflowDefinition, HRWorkflowStage, HRWorkflowTask
from apps.payroll.models import LeaveRequest, LeaveType
from apps.rbac.models import Role, UserProfile, Organization


class LeaveApprovalTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        User = get_user_model()
        self.employee = User.objects.create_user(username='employee', email='employee@example.com', password='test')
        self.manager = User.objects.create_user(username='manager', email='manager@example.com', password='test')
        self.hr = User.objects.create_user(username='hr', email='hr@example.com', password='test')
        self.other = User.objects.create_user(username='other', email='other@example.com', password='test')
        self.manager_record = EmployeeMaster.objects.create(user=self.manager, join_date=date(2020, 1, 1), employee_number='M1', employee_code='M1', emp_code='M1', email=self.manager.email, first_name='Line', last_name='Manager')
        self.employee_record = EmployeeMaster.objects.create(user=self.employee, join_date=date(2020, 1, 1), employee_number='E1', employee_code='E1', emp_code='E1', email=self.employee.email, first_name='Test', last_name='Employee', manager=self.manager_record)
        role = Role.objects.create(code='hr_manager', name='HR manager')
        organization = Organization.objects.create(code='TEST', name='Test organization')
        profile, _ = UserProfile.objects.get_or_create(user=self.hr, defaults={'organization': organization})
        profile.roles.add(role)
        self.leave_type = LeaveType.objects.create(code='OTHER', name='Other', category='other')
        self.definition = HRWorkflowDefinition.objects.create(code='leave_request_v1', version=1, name='Leave', subject_type='payroll.leave_request')
        HRWorkflowStage.objects.create(definition=self.definition, code='manager_review', name='Manager review', sequence=1, approver_type='employee_manager', require_comment_on_reject=True)
        HRWorkflowStage.objects.create(definition=self.definition, code='hr_review', name='HR review', sequence=2, approver_type='role', approver_value='hr_manager', require_comment_on_reject=True)
        self.notifications = patch('apps.notifications.services.NotificationService.create_notification').start()
        self.addCleanup(patch.stopall)

    def submit(self, user=None, **extra):
        self.client.force_authenticate(user or self.employee)
        return self.client.post('/api/v1/payroll/leave-requests/', {'leave_type': self.leave_type.pk, 'employee_name': 'Spoofed name', 'start_date': '2026-10-05', 'end_date': '2026-10-09', **extra}, format='json')

    def act(self, user, request_id, action, **data):
        self.client.force_authenticate(user)
        return self.client.post(f'/api/v1/payroll/leave-requests/{request_id}/{action}/', data, format='json')

    def queue(self, user):
        self.client.force_authenticate(user)
        return self.client.get('/api/v1/payroll/leave-requests/pending-for-me/').data['results']

    def test_manager_then_hr_and_duplicate_decision(self):
        response = self.submit()
        self.assertEqual(response.status_code, 201, response.data)
        pk = response.data['id']
        self.assertEqual(response.data['employee_name'], 'Test Employee')
        self.assertEqual(len(self.queue(self.manager)), 1)
        self.assertEqual(self.queue(self.hr), [])
        self.assertEqual(self.queue(self.employee), [])
        self.assertEqual(self.act(self.hr, pk, 'approve').status_code, 400)
        self.assertEqual(self.act(self.employee, pk, 'rm-approve').status_code, 403)
        self.assertEqual(self.act(self.manager, pk, 'rm-approve').status_code, 200)
        self.assertEqual(self.queue(self.manager), [])
        self.assertEqual(len(self.queue(self.hr)), 1)
        self.assertEqual(self.act(self.manager, pk, 'rm-approve').status_code, 400)
        self.assertEqual(self.act(self.hr, pk, 'approve').status_code, 200)
        self.assertEqual(self.act(self.hr, pk, 'approve').status_code, 400)
        self.assertEqual(self.queue(self.hr), [])
        leave = LeaveRequest.objects.get(pk=pk)
        self.assertEqual(leave.workflow_instance.status, 'approved')
        self.assertEqual(leave.days_requested, 5)
        recipients = [call.kwargs['recipient'] for call in self.notifications.call_args_list]
        self.assertIn(self.manager, recipients)
        self.assertIn(self.hr, recipients)
        self.assertIn(self.employee, recipients)

    def test_leave_photo_uses_employee_profile_and_canonical_fallback(self):
        self.employee_record.photo_url = 'https://example.com/employee.jpg'
        self.employee_record.save()
        self.manager_record.photo_url = 'https://example.com/manager.jpg'
        self.manager_record.save()
        response = self.submit()
        self.assertEqual(response.data['employee_photo_url'], 'https://example.com/employee.jpg')
        profile = UserProfile.objects.create(user=self.employee, organization=Organization.objects.first())
        self.client.force_authenticate(self.manager)
        detail = self.client.get(f"/api/v1/payroll/leave-requests/{response.data['id']}/")
        self.assertEqual(detail.data['employee_photo_url'], 'https://example.com/employee.jpg')
        self.employee_record.photo_url = ''
        self.employee_record.save()
        profile.profile_photo = 'profile_photos/employee.jpg'
        profile.save()
        with patch.object(profile._meta.get_field('profile_photo').storage, 'url', return_value='https://example.com/uploaded.jpg'):
            detail = self.client.get(f"/api/v1/payroll/leave-requests/{response.data['id']}/")
        self.assertEqual(detail.data['employee_photo_url'], 'https://example.com/uploaded.jpg')

    def test_search_by_employee_name_and_code(self):
        self.submit()
        for term in ('E1', 'Test Employee'):
            response = self.client.get('/api/v1/payroll/leave-requests/', {'search': term, 'page_size': 10})
            self.assertEqual(response.data['count'], 1)
        response = self.client.get('/api/v1/payroll/leave-requests/', {'search': 'no match'})
        self.assertEqual(response.data['count'], 0)

    def test_missing_manager_and_missing_workflow_roll_back(self):
        self.employee_record.manager = None
        self.employee_record.save()
        response = self.submit()
        self.assertEqual(response.status_code, 201, response.data)
        pk = response.data['id']
        self.assertEqual(response.data['review_stage'], 'hr_review')
        self.assertEqual(len(self.queue(self.hr)), 1)
        self.assertEqual(self.act(self.hr, pk, 'approve').status_code, 200)
        LeaveRequest.objects.all().delete()
        self.employee_record.manager = self.manager_record
        self.employee_record.save()
        self.definition.is_active = False
        self.definition.save()
        self.assertEqual(self.submit().status_code, 400)
        self.assertEqual(LeaveRequest.objects.count(), 0)

    def test_rejection_requires_reason_and_stops_queue(self):
        pk = self.submit().data['id']
        self.assertEqual(self.act(self.manager, pk, 'rm-reject').status_code, 400)
        self.assertEqual(self.act(self.manager, pk, 'rm-reject', note='Coverage required').status_code, 200)
        self.assertEqual(self.queue(self.manager), [])
        self.assertEqual(self.queue(self.hr), [])
        self.assertEqual(self.act(self.hr, pk, 'approve').status_code, 400)

    def test_cancel_stops_tasks_and_blocks_later_approval(self):
        pk = self.submit().data['id']
        self.assertEqual(self.act(self.employee, pk, 'cancel').status_code, 200)
        self.assertFalse(HRWorkflowTask.objects.filter(status='pending').exists())
        self.assertEqual(self.queue(self.manager), [])
        self.assertEqual(self.act(self.manager, pk, 'rm-approve').status_code, 400)

    def test_spoofing_overlap_invalid_dates_and_edit_protection(self):
        self.assertEqual(self.submit(employee=str(self.other.pk)).status_code, 403)
        self.assertEqual(self.submit(start_date='2026-10-10', end_date='2026-10-09').status_code, 400)
        self.assertEqual(self.submit(start_date='2026-10-10', end_date='2026-10-11').status_code, 400)
        pk = self.submit().data['id']
        self.assertEqual(self.submit().status_code, 400)
        self.assertEqual(self.client.patch(f'/api/v1/payroll/leave-requests/{pk}/', {'reason': 'Changed'}).status_code, 400)
        self.assertEqual(self.client.delete(f'/api/v1/payroll/leave-requests/{pk}/').status_code, 400)
        self.assertEqual(self.act(self.other, pk, 'rm-approve').status_code, 404)

    def test_legacy_request_cannot_skip_manager(self):
        leave = LeaveRequest.objects.create(employee=self.employee, employee_name='Test Employee', leave_type=self.leave_type, start_date=date(2026, 10, 5), end_date=date(2026, 10, 9))
        self.assertEqual(self.act(self.hr, leave.pk, 'approve').status_code, 400)
        self.assertEqual(self.act(self.manager, leave.pk, 'rm-approve').status_code, 200)
        self.assertEqual(self.act(self.hr, leave.pk, 'approve').status_code, 200)

    def test_half_day_and_document_validation(self):
        self.leave_type.requires_document = True
        self.leave_type.save()
        self.assertEqual(self.submit().status_code, 400)
        self.leave_type.requires_document = False
        self.leave_type.save()
        self.assertEqual(self.submit(half_day=True).status_code, 400)
        response = self.submit(half_day=True, end_date='2026-10-05')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(float(response.data['days_requested']), 0.5)

    def test_annual_balance_reserved_then_deducted_only_by_hr(self):
        from apps.payroll.models import EmployeeLeaveRecord
        self.leave_type.category = 'annual'
        self.leave_type.save()
        self.assertEqual(self.submit().status_code, 400)
        ledger = EmployeeLeaveRecord.objects.create(employee_code='E1', employee_name='Test Employee', year=2026, total_earned=6, leave_balance=6)
        first = self.submit()
        self.assertEqual(first.status_code, 201, first.data)
        pk = first.data['id']
        ledger.refresh_from_db()
        self.assertEqual(ledger.leave_balance, 6)
        second = self.submit(start_date='2026-10-12', end_date='2026-10-13')
        self.assertEqual(second.status_code, 400)
        self.assertEqual(LeaveRequest.objects.count(), 1)
        self.assertEqual(self.act(self.manager, pk, 'rm-approve').status_code, 200)
        ledger.refresh_from_db()
        self.assertEqual(ledger.leave_balance, 6)
        self.assertEqual(self.act(self.hr, pk, 'approve').status_code, 200)
        ledger.refresh_from_db()
        self.assertEqual(ledger.leave_balance, 1)
        self.assertEqual(ledger.total_taken, 5)

    def test_hr_rejection_and_cancel_after_manager(self):
        pk = self.submit().data['id']
        self.act(self.manager, pk, 'rm-approve')
        self.assertEqual(self.act(self.hr, pk, 'reject').status_code, 400)
        self.assertEqual(self.act(self.hr, pk, 'reject', note='Not eligible').status_code, 200)
        self.assertEqual(self.queue(self.hr), [])
        pk = self.submit().data['id']
        self.act(self.manager, pk, 'rm-approve')
        self.assertEqual(self.act(self.employee, pk, 'cancel').status_code, 200)
        self.assertEqual(self.queue(self.hr), [])

    def test_hr_on_behalf_uses_employee_manager_and_identity(self):
        response = self.submit(user=self.hr, employee_code='E1')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(str(response.data['employee']), str(self.employee.pk))
        self.assertEqual(response.data['employee_name'], 'Test Employee')
        self.assertEqual(len(self.queue(self.manager)), 1)

    def test_self_manager_cannot_submit(self):
        self.employee_record.manager = self.employee_record
        self.employee_record.save()
        self.assertEqual(self.submit().status_code, 400)

    def test_no_hr_approver_rejects_submission(self):
        self.hr.is_active = False
        self.hr.save()
        self.assertEqual(self.submit().status_code, 400)
        self.assertFalse(LeaveRequest.objects.exists())

    def test_imported_balance_preserved_and_approval_is_idempotent(self):
        from apps.payroll.models import EmployeeLeaveRecord
        self.leave_type.category = 'annual'
        self.leave_type.save()
        ledger = EmployeeLeaveRecord.objects.create(employee_code='E1', employee_name='Test Employee', year=2026, total_earned=20, total_taken=7, leave_balance=13)
        pk = self.submit().data['id']
        self.act(self.manager, pk, 'rm-approve')
        self.assertEqual(self.act(self.hr, pk, 'approve').status_code, 200)
        leave = LeaveRequest.objects.get(pk=pk)
        leave.save()
        ledger.refresh_from_db()
        self.assertEqual(ledger.total_taken, 12)
        self.assertEqual(ledger.leave_balance, 8)
        leave.status = 'CANCELLED'
        leave.save()
        ledger.refresh_from_db()
        self.assertEqual(ledger.total_taken, 7)
        self.assertEqual(ledger.leave_balance, 13)

    def test_payroll_and_calendar_only_final_approval_split_months(self):
        from apps.payroll.services.leave_sync import monthly_payroll_leave
        self.leave_type.category = 'unpaid'
        self.leave_type.save()
        pk = self.submit(start_date='2026-09-24', end_date='2026-10-08').data['id']
        self.assertEqual(monthly_payroll_leave(2026, 9), {})
        self.act(self.manager, pk, 'rm-approve')
        self.assertEqual(monthly_payroll_leave(2026, 9), {})
        self.assertEqual(self.client.get('/api/v1/payroll/leave-calendar/?year=2026&month=9').data['calendar'], {})
        self.assertEqual(self.act(self.hr, pk, 'approve').status_code, 200)
        self.assertEqual(monthly_payroll_leave(2026, 9)['E1']['unpaid_leave_days'], 5)
        self.assertEqual(monthly_payroll_leave(2026, 10)['E1']['unpaid_leave_days'], 6)
        calendar = self.client.get('/api/v1/payroll/leave-calendar/?year=2026&month=9').data['calendar']['E1']
        self.assertEqual(len(calendar), 5)
        self.assertNotIn('2026-09-26', calendar)
        self.assertEqual(calendar['2026-09-24']['request_id'], str(pk))
        from apps.payroll_engine.models import PayrollEmployee
        from apps.payroll_engine.services.run_generator import generate_monthly_run
        PayrollEmployee.objects.create(employee_no='E1', full_name='Test Employee', basic=3000)
        with patch('apps.payroll_engine.services.run_generator.compute_monthly_hours', return_value={}):
            run = generate_monthly_run(2026, 9)
        slip = run.payslips.get()
        self.assertEqual(slip.unpaid_leave_days, 5)
        self.assertGreater(slip.total_deductions, 0)

    def test_half_day_shared_by_payroll_and_calendar(self):
        from apps.payroll.services.leave_sync import monthly_payroll_leave
        self.leave_type.category = 'unpaid'
        self.leave_type.save()
        pk = self.submit(half_day=True, end_date='2026-10-05').data['id']
        self.act(self.manager, pk, 'rm-approve')
        self.act(self.hr, pk, 'approve')
        self.assertEqual(float(monthly_payroll_leave(2026, 10)['E1']['unpaid_leave_days']), 0.5)
        day = self.client.get('/api/v1/payroll/leave-calendar/?year=2026&month=10').data['calendar']['E1']['2026-10-05']
        self.assertTrue(day['half_day'])
        self.assertEqual(day['days'], 0.5)

    def test_profile_manager_fallback_displays_routes_and_allows_review(self):
        self.employee_record.manager = None
        self.employee_record.save()
        organization = Organization.objects.first()
        manager_profile = UserProfile.objects.create(user=self.manager, organization=organization)
        UserProfile.objects.create(user=self.employee, organization=organization, manager=manager_profile)
        response = self.submit()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['line_manager_name'], self.manager.get_full_name() or self.manager.username)
        pk = response.data['id']
        task = LeaveRequest.objects.get(pk=pk).workflow_instance.tasks.get(status='pending')
        self.assertEqual(task.assigned_to, self.manager)
        self.assertEqual(len(self.queue(self.manager)), 1)
        self.assertEqual(self.act(self.manager, pk, 'rm-approve').status_code, 200)

    def test_legacy_request_without_master_uses_profile_manager(self):
        self.employee_record.delete()
        organization = Organization.objects.first()
        manager_profile = UserProfile.objects.create(user=self.manager, organization=organization)
        UserProfile.objects.create(user=self.employee, organization=organization, manager=manager_profile)
        leave = LeaveRequest.objects.create(employee=self.employee, employee_name='Test Employee', leave_type=self.leave_type, start_date=date(2026, 10, 5), end_date=date(2026, 10, 9))
        self.client.force_authenticate(self.employee)
        response = self.client.get(f'/api/v1/payroll/leave-requests/{leave.pk}/')
        self.assertEqual(response.data['line_manager_name'], 'manager')
        self.assertEqual(len(self.queue(self.manager)), 1)
        self.assertEqual(self.act(self.manager, leave.pk, 'rm-approve').status_code, 200)

    def test_organization_profile_manager_takes_precedence(self):
        organization = Organization.objects.first()
        wrong_manager = UserProfile.objects.create(user=self.other, organization=organization)
        UserProfile.objects.create(user=self.employee, organization=organization, manager=wrong_manager)
        response = self.submit()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['line_manager_name'], 'other')
        self.assertEqual(len(self.queue(self.other)), 1)
        self.assertEqual(self.queue(self.manager), [])

    def test_existing_request_loses_manager_routes_hr_with_audit(self):
        pk = self.submit().data['id']
        self.employee_record.manager = None
        self.employee_record.save()
        self.assertEqual(len(self.queue(self.hr)), 1)
        self.assertEqual(self.act(self.hr, pk, 'rm-approve').status_code, 403)
        self.assertEqual(self.act(self.hr, pk, 'approve').status_code, 200)
        leave = LeaveRequest.objects.get(pk=pk)
        self.assertEqual(leave.status, 'APPROVED')
        self.assertIsNone(leave.rm_reviewed_by)
        self.assertTrue(leave.workflow_instance.events.filter(event_type='manager_skipped').exists())

    def test_assigned_inactive_manager_does_not_allow_hr_bypass(self):
        pk = self.submit().data['id']
        self.manager.is_active = False
        self.manager.save()
        self.assertEqual(self.queue(self.hr), [])
        self.assertEqual(self.act(self.hr, pk, 'approve').status_code, 400)

    def test_changed_profile_manager_can_review_existing_task(self):
        pk = self.submit().data['id']
        organization = Organization.objects.first()
        manager = UserProfile.objects.create(user=self.other, organization=organization)
        UserProfile.objects.create(user=self.employee, organization=organization, manager=manager)
        self.assertEqual(self.queue(self.manager), [])
        self.assertEqual(len(self.queue(self.other)), 1)
        self.assertEqual(self.act(self.other, pk, 'rm-approve').status_code, 200)


from django.test import TransactionTestCase

class LeaveHalfDayMigrationTests(TransactionTestCase):
    def test_half_day_migration_applies_to_existing_schema(self):
        import importlib
        from django.apps import apps
        from django.db import connection
        from django.db.migrations.state import ProjectState
        state = ProjectState.from_apps(apps)
        # Recreate the pre-migration shape, then execute the real AddField migration.
        field = LeaveRequest._meta.get_field('half_day')
        with connection.schema_editor() as editor:
            editor.remove_field(LeaveRequest, field)
        state.remove_field('payroll', 'leaverequest', 'half_day')
        migration = importlib.import_module('apps.payroll.migrations.0023_leaverequest_half_day').Migration('0023_leaverequest_half_day', 'payroll')
        with connection.schema_editor() as editor:
            migration.apply(state, editor)
        with connection.cursor() as cursor:
            columns = connection.introspection.get_table_description(cursor, LeaveRequest._meta.db_table)
        self.assertIn('half_day', [column.name for column in columns])
