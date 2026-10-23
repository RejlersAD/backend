from datetime import date
from unittest.mock import patch
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import path, include
from apps.rbac.route_guard import secure_module_endpoints
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from apps.rbac.models import Role
from apps.hr_core.models import HRWorkflowDefinition, HRWorkflowStage
from apps.procurement.tests.approval_fixtures import grant_approval, set_position
from apps.hr_core.models import EmployeeMaster
from apps.hr_core.organization import payroll_eligible
from apps.payroll.models import LeaveRequest, LeaveType
from apps.payroll_engine.models import PayrollEmployee


urlpatterns = [path('api/v1/payroll/', include('apps.payroll.urls'))]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class DepartmentEnhancementTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        for name in ('employee', 'manager', 'hr', 'other'):
            setattr(self, name, get_user_model().objects.create_user(username=name, email=f'{name}@example.test'))
        for user in (self.employee, self.manager, self.hr, self.other):
            grant_approval(user, 'payroll')
        set_position(self.hr, 'HR Manager')
        role = Role.objects.create(code='hr_manager', name='HR manager')
        self.hr.rbac_profile.roles.add(role)
        self.manager_record = EmployeeMaster.objects.create(user=self.manager, join_date=date(2020, 1, 1),
            employee_number='M1', employee_code='M1', emp_code='M1', email=self.manager.email, first_name='Line', last_name='Manager')
        self.employee_record = EmployeeMaster.objects.create(user=self.employee, join_date=date(2020, 1, 1),
            employee_number='E1', employee_code='E1', emp_code='E1', email=self.employee.email,
            first_name='Test', last_name='Employee', manager=self.manager_record)
        self.leave_type = LeaveType.objects.create(code='OTHER', name='Other', category='other')
        self.definition = HRWorkflowDefinition.objects.create(code='leave_request_v1', version=1, name='Leave', subject_type='payroll.leave_request')
        HRWorkflowStage.objects.create(definition=self.definition, code='manager_review', name='Manager review', sequence=1, approver_type='employee_manager', require_comment_on_reject=True)
        HRWorkflowStage.objects.create(definition=self.definition, code='hr_review', name='HR review', sequence=2, approver_type='role', approver_value='hr_manager', require_comment_on_reject=True)
        self.notifications = patch('apps.notifications.services.NotificationService.create_notification').start()
        self.addCleanup(patch.stopall)

    def submit(self, **extra):
        self.client.force_authenticate(self.employee)
        return self.client.post('/api/v1/payroll/leave-requests/', {'leave_type': self.leave_type.pk,
            'employee_name': 'Client supplied name', 'start_date': '2026-10-05', 'end_date': '2026-10-09', **extra}, format='json')

    def act(self, user, request_id, action, **data):
        self.client.force_authenticate(user)
        return self.client.post(f'/api/v1/payroll/leave-requests/{request_id}/{action}/', data, format='json')

    def queue(self, user):
        self.client.force_authenticate(user)
        return self.client.get('/api/v1/payroll/leave-requests/pending-for-me/').data['results']

    def test_normal_leave_keeps_manager_hr_sequence(self):
        response = self.submit()
        self.assertEqual(response.status_code, 201, response.data)
        pk = response.data['id']
        self.assertEqual(self.act(self.hr, pk, 'approve').status_code, 400)
        self.assertEqual(self.act(self.manager, pk, 'rm-approve').status_code, 200)
        final = self.act(self.hr, pk, 'approve')
        self.assertEqual(final.status_code, 200, final.data)
        self.assertTrue(all(step['complete'] for step in final.data['approval_steps']))

    def test_manual_selection_is_request_specific_and_sales_finance_only(self):
        self.employee_record.manager = None
        self.employee_record.department = 'Sales'
        self.employee_record.save()
        response = self.submit(selected_manager=str(self.manager_record.pk))
        self.assertEqual(response.status_code, 201, response.data)
        self.employee_record.refresh_from_db()
        self.assertIsNone(self.employee_record.manager_id)
        self.assertEqual(self.act(self.other, response.data['id'], 'rm-approve').status_code, 404)
        self.assertEqual(self.act(self.manager, response.data['id'], 'rm-approve').status_code, 200)

    def test_other_department_cannot_select_manager(self):
        self.employee_record.manager = None
        self.employee_record.department = 'Engineering'
        self.employee_record.save()
        response = self.submit(selected_manager=str(self.manager_record.pk))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(LeaveRequest.objects.exists())

    def test_manual_manager_endpoint_is_self_scoped_under_http_guard(self):
        self.client.force_authenticate(self.employee)
        self.assertFalse(self.client.get('/api/v1/payroll/leave-requests/manager-options/').data['manual_selection_required'])
        self.employee_record.department = 'Finance'
        self.employee_record.manager = None
        self.employee_record.save()
        result = self.client.get('/api/v1/payroll/leave-requests/manager-options/')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertTrue(result.data['manual_selection_required'])
        self.assertIn(str(self.manager_record.pk), [item['id'] for item in result.data['results']])

    def test_nsl_uses_nationality_instead_of_residence(self):
        self.leave_type = LeaveType.objects.create(code='NSL', name='National Service Leave', category='national_service')
        self.employee_record.country = 'United Arab Emirates'
        self.employee_record.save()
        self.assertEqual(self.submit().status_code, 400)
        self.employee_record.nationality = 'United Arab Emirates'
        self.employee_record.save()
        self.assertEqual(self.submit().status_code, 201)

    def test_flex_requires_three_sequential_distinct_approvers_then_hr(self):
        self.leave_type = LeaveType.objects.create(code='FU', name='Flex Utilization', category='flex_utilization')
        self.assertEqual(self.submit().status_code, 400)
        call_command('configure_flex_leave', jamal=self.manager.email, rafat=self.other.email, mohammed=self.hr.email)
        response = self.submit()
        self.assertEqual(response.status_code, 201, response.data)
        pk = response.data['id']
        self.assertEqual(response.data['review_stage'], 'jemal_review')
        self.assertFalse(self.queue(self.other))
        self.assertEqual(self.act(self.manager, pk, 'rm-approve').data['review_stage'], 'rafat_review')
        self.assertEqual(self.act(self.other, pk, 'rm-approve').data['review_stage'], 'mohammed_review')
        result = self.act(self.hr, pk, 'rm-approve')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['review_stage'], 'hr_review')
        final = self.act(self.hr, pk, 'approve')
        self.assertEqual(final.status_code, 200, final.data)
        self.assertEqual(final.data['status'], 'APPROVED')
        self.assertEqual(len(final.data['approval_steps']), 4)
        self.assertEqual(self.act(self.hr, pk, 'approve').status_code, 400)

    def test_edc_excluded_with_and_without_canonical_payroll_link(self):
        self.employee_record.business_unit = 'EDC'
        self.employee_record.save()
        linked = PayrollEmployee.objects.create(employee_no='LINKED', full_name='Linked', employee=self.employee_record)
        legacy = PayrollEmployee.objects.create(employee_no='E1', full_name='Legacy')
        eligible = PayrollEmployee.objects.create(employee_no='NORMAL', full_name='Normal')
        ids = set(payroll_eligible(PayrollEmployee.objects.all()).values_list('pk', flat=True))
        self.assertNotIn(linked.pk, ids)
        self.assertNotIn(legacy.pk, ids)
        self.assertIn(eligible.pk, ids)

    def test_department_head_notified_only_after_final_approval(self):
        self.definition.configuration = {'department_heads': {'Engineering': self.other.email}}
        self.definition.save()
        self.employee_record.department = 'Engineering'
        self.employee_record.save()
        pk = self.submit().data['id']
        with self.captureOnCommitCallbacks(execute=True):
            self.act(self.manager, pk, 'rm-approve')
        self.assertFalse(any(call.kwargs.get('title') == 'Leave approved — for information'
                             for call in self.notifications.call_args_list))
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.act(self.hr, pk, 'approve').status_code, 200)
        notices = [call for call in self.notifications.call_args_list
                   if call.kwargs.get('title') == 'Leave approved — for information']
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0].kwargs['recipient'], self.other)
        self.assertFalse(notices[0].kwargs['metadata']['requires_action'])

    def test_department_head_resolves_current_business_position_without_a_payroll_grant(self):
        from apps.rbac.models import UserRole
        set_position(self.other, 'Manager of Engineering')
        UserRole.objects.filter(user_profile=self.other.rbac_profile).delete()
        self.employee_record.department = 'Engineering'
        self.employee_record.save()
        pk = self.submit().data['id']
        self.act(self.manager, pk, 'rm-approve')
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.act(self.hr, pk, 'approve').status_code, 200)
        notices = [call for call in self.notifications.call_args_list if call.kwargs.get('title') == 'Leave approved — for information']
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0].kwargs['recipient'], self.other)
        self.client.force_authenticate(self.hr)
        result = self.client.get(f'/api/v1/payroll/leave-requests/{pk}/')
        self.assertEqual(result.data['department_head_notification_status'], 'created')

    def test_edc_does_not_generate_a_payslip_and_blocks_old_run_processing(self):
        from apps.hr_core.organization import require_payroll_eligible_run
        from apps.payroll_engine.services.run_generator import generate_monthly_run
        from apps.payroll_engine.models import Payslip
        from rest_framework.exceptions import ValidationError
        self.employee_record.business_unit = 'EDC'
        self.employee_record.save()
        excluded = PayrollEmployee.objects.create(employee_no='E1', full_name='EDC profile')
        regular = PayrollEmployee.objects.create(employee_no='NORMAL', full_name='Regular')
        with patch('apps.payroll_engine.services.run_generator.HOURS_FROM_TIMESHEET', False):
            run = generate_monthly_run(2027, 1)
        self.assertEqual(list(run.payslips.values_list('employee_id', flat=True)), [regular.pk])
        Payslip.objects.create(run=run, employee=excluded)
        with self.assertRaises(ValidationError):
            require_payroll_eligible_run(run)

    def test_hr_can_extend_five_days_to_eight_with_negative_three_balance_once(self):
        from apps.payroll.models import EmployeeLeaveRecord
        from apps.hr_core.models import HRWorkflowEvent
        self.leave_type = LeaveType.objects.create(code='AL', name='Annual', category='annual')
        ledger = EmployeeLeaveRecord.objects.create(employee_code='E1', employee_name='Test Employee', year=2026,
            total_earned=5, leave_balance=5)
        pk = self.submit().data['id']
        self.assertEqual(self.act(self.manager, pk, 'rm-approve', allow_negative_balance=True).status_code, 403)
        self.assertEqual(self.act(self.manager, pk, 'rm-approve').status_code, 200)
        payload = {'approved_end_date': '2026-10-14', 'allow_negative_balance': True}
        self.assertEqual(self.act(self.hr, pk, 'approve', **payload).status_code, 400)
        ledger.refresh_from_db()
        self.assertEqual(ledger.leave_balance, 5)
        result = self.act(self.hr, pk, 'approve', **payload, override_reason='HR-approved additional three days')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['days_requested'], '8.00')
        self.assertEqual(result.data['requested_days'], '5.00')
        self.assertEqual(result.data['requested_end_date'], '2026-10-09')
        ledger.refresh_from_db()
        self.assertEqual(ledger.leave_balance, -3)
        self.assertEqual(ledger.total_taken, 8)
        self.assertTrue(HRWorkflowEvent.objects.filter(event_type='hr_leave_override', actor=self.hr).exists())
        self.assertEqual(self.act(self.hr, pk, 'approve', **payload, override_reason='Retry').status_code, 400)
        ledger.refresh_from_db()
        self.assertEqual(ledger.leave_balance, -3)

    def test_employee_cannot_submit_more_than_available_balance(self):
        from apps.payroll.models import EmployeeLeaveRecord
        self.leave_type = LeaveType.objects.create(code='AL', name='Annual', category='annual')
        EmployeeLeaveRecord.objects.create(employee_code='E1', employee_name='Test Employee', year=2026, leave_balance=4)
        self.assertEqual(self.submit(allow_negative_balance=True).status_code, 400)
        self.assertFalse(LeaveRequest.objects.exists())

    def test_probation_uses_calendar_months_and_accepts_custom_dates(self):
        from apps.hr_core.services import probation_dates
        from django.core.exceptions import ValidationError
        self.assertEqual(probation_dates(date(2026, 8, 31)), (date(2026, 8, 31), date(2027, 2, 28)))
        self.assertEqual(probation_dates(date(2026, 1, 1), '2026-02-01', '2026-05-15'), (date(2026, 2, 1), date(2026, 5, 15)))
        with self.assertRaises(ValidationError):
            probation_dates(date(2026, 1, 1), '2026-02-01', '2026-01-01')


from django.test import TransactionTestCase


class DepartmentMigrationTests(TransactionTestCase):
    def test_additive_migrations_preserve_custom_leave_types_and_do_not_infer_nationality(self):
        import importlib
        from django.apps import apps
        from django.db import connection
        from django.db.migrations.state import ProjectState
        employee = EmployeeMaster.objects.create(employee_number='PROFILE', employee_code='PROFILE',
            emp_code='PROFILE', first_name='Test', email='profile@example.test', join_date=date(2026, 1, 1),
            country='United Arab Emirates')
        leave_type = LeaveType.objects.create(code='CL', name='Existing custom CL', category='other')
        leave = LeaveRequest.objects.create(employee_name='Test', leave_type=leave_type, start_date=date(2026, 10, 5), end_date=date(2026, 10, 9))
        state = ProjectState.from_apps(apps)
        with connection.schema_editor() as editor:
            editor.remove_field(LeaveRequest, LeaveRequest._meta.get_field('selected_manager'))
            editor.remove_field(EmployeeMaster, EmployeeMaster._meta.get_field('nationality'))
            editor.remove_field(EmployeeMaster, EmployeeMaster._meta.get_field('probation_start_date'))
            for name in ('requested_start_date', 'requested_end_date', 'requested_days', 'hr_balance_override', 'hr_override_reason'):
                editor.remove_field(LeaveRequest, LeaveRequest._meta.get_field(name))
        state.remove_field('payroll', 'leaverequest', 'selected_manager')
        state.remove_field('hr_core', 'employeemaster', 'nationality')
        state.remove_field('hr_core', 'employeemaster', 'probation_start_date')
        for name in ('requested_start_date', 'requested_end_date', 'requested_days', 'hr_balance_override', 'hr_override_reason'):
            state.remove_field('payroll', 'leaverequest', name)
        for label, name in [('hr_core', '0015_employee_nationality'), ('hr_core', '0016_employee_probation_start'), ('payroll', '0024_hr_department_leave'), ('payroll', '0025_leave_hr_override')]:
            migration = importlib.import_module(f'apps.{label}.migrations.{name}').Migration(name, label)
            with connection.schema_editor() as editor:
                state = migration.apply(state, editor)
        employee.refresh_from_db()
        self.assertEqual(employee.nationality, '')
        leave.refresh_from_db()
        self.assertEqual(leave.requested_days, 5)
        self.assertEqual(leave.requested_end_date, date(2026, 10, 9))
        self.assertEqual(LeaveType.objects.get(code='CL').name, 'Existing custom CL')
        self.assertEqual(set(LeaveType.objects.values_list('code', flat=True)), {'CL', 'NSL', 'WFH', 'FU'})
