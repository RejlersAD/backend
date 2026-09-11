from datetime import date
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate
from .models import EmployeeMaster, HRWorkflowDefinition, HRWorkflowStage, OvertimeRequest
from .views import OvertimeRequestViewSet
from apps.rbac.models import Role, Organization, UserProfile


class OvertimeRoutingTests(TestCase):
    def setUp(self):
        self.users = {}
        self.employees = {}
        self.org = Organization.objects.create(code='OT', name='OT')
        for name in ['employee', 'manager', 'other', 'hr', 'finance']:
            user = get_user_model().objects.create_user(username=name, email=f'{name}@example.com')
            self.users[name] = user
            self.employees[name] = EmployeeMaster.objects.create(user=user, first_name=name, last_name='Test', email=user.email,
                join_date=date(2020, 1, 1), employee_number=name, employee_code=name, emp_code=name)
            profile = UserProfile.objects.create(user=user, organization=self.org)
            if name in {'hr', 'finance'}:
                role = Role.objects.create(code=f'{name}_manager', name=name)
                profile.roles.add(role)
        from apps.timesheet.models import DailyAttendanceSummary
        for employee in self.employees.values():
            for source in ['manual', 'biometric']:
                DailyAttendanceSummary.objects.create(employee_code=employee.employee_code, date=date(2026,9,10), source=source, effective_hours=8, overtime_hours=4)
        self.users['employee'].rbac_profile.manager = self.users['manager'].rbac_profile
        self.users['employee'].rbac_profile.save()
        # Conflicting canonical manager must not override Organization.
        self.employees['employee'].manager = self.employees['other']
        self.employees['employee'].save()
        definition = HRWorkflowDefinition.objects.create(code='overtime_request_v1', name='OT', subject_type='hr.overtime_request')
        HRWorkflowStage.objects.create(definition=definition, code='manager_review', name='Manager', sequence=1, approver_type='employee_manager', require_comment_on_reject=True)
        HRWorkflowStage.objects.create(definition=definition, code='hr_review', name='HR / Finance', sequence=2, approver_type='role', approver_value='hr_manager', require_comment_on_reject=True)
        for target in ['apps.hr_core.workflows.HRWorkflowService._notify_task', 'apps.hr_core.overtime.notify_result']:
            mock = patch(target); mock.start(); self.addCleanup(mock.stop)

    def call(self, actor, action, data=None, pk=None):
        method = 'get' if action in {'list', 'retrieve', 'employees'} else 'post'
        req = getattr(APIRequestFactory(), method)('/overtime/', data or {}, format='json')
        force_authenticate(req, self.users[actor])
        return OvertimeRequestViewSet.as_view({method: action})(req, **({'pk': pk} if pk else {}))

    def submit(self, actor='employee', employee='employee', **extra):
        return self.call(actor, 'create', {'employee': str(self.employees[employee].pk), 'work_date': '2026-09-10', 'requested_hours': '2.50', 'reason': 'Project delivery', **extra})

    def test_employee_manager_then_either_final_role(self):
        response = self.submit(); self.assertEqual(response.status_code, 201, response.data)
        pk = response.data['id']; self.assertEqual(response.data['stage_code'], 'manager_review')
        self.assertEqual(self.call('hr', 'approve', pk=pk).status_code, 403)
        self.assertEqual(self.call('other', 'approve', pk=pk).status_code, 404)
        approved = self.call('manager', 'approve', pk=pk)
        self.assertEqual(approved.data['stage_code'], 'hr_review')
        self.assertIsNone(approved.data['approved_hours'])
        final = self.call('finance', 'approve', pk=pk)
        self.assertEqual(final.data['status'], 'approved'); self.assertEqual(final.data['approved_hours'], '2.50')
        self.assertEqual(self.call('hr', 'approve', pk=pk).status_code, 400)

    def test_manager_submits_for_report_direct_final(self):
        response = self.submit(actor='manager'); self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['stage_code'], 'hr_review')
        self.assertEqual(self.call('manager', 'approve', pk=response.data['id']).status_code, 403)
        self.assertEqual(self.call('hr', 'approve', pk=response.data['id']).data['status'], 'approved')

    def test_manager_submits_for_self_direct_final(self):
        response = self.submit(actor='manager', employee='manager')
        self.assertEqual(response.data['stage_code'], 'hr_review')
        self.assertEqual(self.call('finance', 'approve', pk=response.data['id']).data['status'], 'approved')

    def test_unrelated_submission_and_forged_approval_are_blocked(self):
        self.assertEqual(self.submit(employee='other').status_code, 403)
        response = self.submit(status='approved', approved_hours='24')
        self.assertEqual(response.data['status'], 'pending'); self.assertIsNone(response.data['approved_hours'])

    def test_inactive_assigned_manager_not_bypassed(self):
        manager = self.users['manager']; manager.is_active = False; manager.save()
        self.assertEqual(self.submit().status_code, 400)

    def test_no_manager_goes_direct_final(self):
        response = self.submit(actor='other', employee='other')
        self.assertEqual(response.data['stage_code'], 'hr_review')

    def test_final_reviewer_cannot_self_approve(self):
        response = self.submit(actor='hr', employee='hr')
        self.assertEqual(self.call('hr', 'approve', pk=response.data['id']).status_code, 403)
        self.assertEqual(self.call('finance', 'approve', pk=response.data['id']).status_code, 200)

    def test_reject_requires_note_and_cancel_stops_approval(self):
        response = self.submit(); pk = response.data['id']
        self.assertEqual(self.call('manager', 'reject', pk=pk).status_code, 400)
        self.assertEqual(self.call('employee', 'cancel', pk=pk).data['status'], 'cancelled')
        self.assertEqual(self.call('manager', 'approve', pk=pk).status_code, 400)

    def test_hours_and_duplicate_validation(self):
        self.assertEqual(self.submit(requested_hours='-1').status_code, 400)
        self.assertEqual(self.submit(requested_hours='25').status_code, 400)
        self.assertEqual(self.submit().status_code, 201)
        self.assertEqual(self.submit().status_code, 400)

    def test_directory_and_visibility(self):
        own = self.call('employee', 'employees').data['employees']; self.assertEqual(len(own), 1)
        team = self.call('manager', 'employees').data['employees']; self.assertEqual(len(team), 2)
        response = self.submit(); pk = response.data['id']
        self.assertEqual(self.call('other', 'retrieve', pk=pk).status_code, 404)
        self.assertEqual(self.call('manager', 'retrieve', pk=pk).status_code, 200)


class OvertimeBenefitTests(OvertimeRoutingTests):
    def approved_request(self, benefit='cash'):
        response = self.submit(actor='manager', compensation_type=benefit)
        pk = response.data['id']
        self.assertEqual(self.call('finance', 'approve', pk=pk).status_code, 200)
        return pk

    def test_cash_formula_and_retry(self):
        from apps.payroll_engine.models import PayrollEmployee, PayrollAdjustment
        from .models import OvertimeConversion
        PayrollEmployee.objects.create(employee=self.employees['employee'], employee_no='employee', full_name='Employee', basic='2400')
        pk = self.approved_request()
        data = {'year': 2026, 'month': 9, 'multiplier': '1.50'}
        response = self.call('hr', 'apply_benefit', data, pk)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['application']['amount'], '37.50')
        self.assertEqual(self.call('finance', 'apply_benefit', data, pk).status_code, 200)
        self.assertEqual(PayrollAdjustment.objects.count(), 1)
        self.assertEqual(OvertimeConversion.objects.count(), 1)

    def test_off_day_credit_and_type_protection(self):
        from apps.payroll.models import EmployeeLeaveRecord
        from decimal import Decimal
        record = EmployeeLeaveRecord.objects.create(employee_code='employee', employee_name='Employee', year=2026, leave_balance=3)
        pk = self.approved_request('day_off')
        self.assertEqual(self.call('hr', 'apply_benefit', {'year':2026,'month':9,'type':'cash'}, pk).status_code, 400)
        response = self.call('hr', 'apply_benefit', {'year':2026,'month':9}, pk)
        self.assertEqual(response.status_code, 200, response.data)
        record.refresh_from_db()
        self.assertEqual(record.leave_balance, Decimal('3.3125'))
        self.assertEqual(record.carryforward, Decimal('.3125'))
        self.call('hr', 'apply_benefit', {'year':2026,'month':9}, pk)
        record.refresh_from_db()
        self.assertEqual(record.leave_balance, Decimal('3.3125'))

    def test_benefit_requires_final_approval_and_authority(self):
        pk = self.submit().data['id']
        self.assertEqual(self.call('hr', 'apply_benefit', {'year':2026,'month':9}, pk).status_code, 400)
        self.assertEqual(self.call('employee', 'apply_benefit', {'year':2026,'month':9}, pk).status_code, 403)

    def test_cash_updates_draft_run_and_blocks_approved_run(self):
        from apps.payroll_engine.models import PayrollEmployee, PayrollRun, Payslip
        from apps.payroll_engine import catalog
        employee = PayrollEmployee.objects.create(employee=self.employees['employee'], employee_no='employee', full_name='Employee', basic='2400')
        run = PayrollRun.objects.create(year=2026, month=9)
        slip = Payslip.objects.create(run=run, employee=employee, basic='2400')
        pk = self.approved_request()
        response = self.call('hr','apply_benefit',{'year':2026,'month':9,'multiplier':'1.25'},pk)
        self.assertEqual(response.status_code,200,response.data)
        slip.refresh_from_db()
        self.assertEqual(str(slip.other_earnings),'31.25')
        self.assertEqual(slip.line_items.count(),1)
        self.call('hr','apply_benefit',{'year':2026,'month':9,'multiplier':'1.25'},pk)
        self.assertEqual(slip.line_items.count(),1)


class OvertimeAttendanceTests(OvertimeRoutingTests):
    def test_missing_day_and_excess_hours_rejected(self):
        self.assertEqual(self.submit(work_date='2026-09-09').status_code, 400)
        self.assertEqual(self.submit(requested_hours='4.01').status_code, 400)
        self.assertEqual(self.submit(requested_hours='4.00').status_code, 201)

    def test_available_days_scope_and_claims(self):
        def days(actor, employee):
            req = APIRequestFactory().get('/available-days/', {'employee': str(employee.pk)})
            force_authenticate(req, self.users[actor])
            return OvertimeRequestViewSet.as_view({'get':'available_days'})(req)
        response = days('employee',self.employees['employee'])
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data['days'][0]['overtime_hours'],'4.00')
        self.assertEqual(days('other',self.employees['employee']).status_code,403)
        self.assertEqual(days('manager',self.employees['employee']).status_code,200)
        self.submit()
        self.assertEqual(days('employee',self.employees['employee']).data['days'],[])

    def test_open_shift_is_unavailable(self):
        from apps.timesheet.models import DailyAttendanceSummary
        DailyAttendanceSummary.objects.filter(employee_code='employee').update(open_shift=True)
        self.assertEqual(self.submit().status_code,400)


class OvertimePersonalScopeTests(OvertimeRoutingTests):
    def test_hr_profile_only_returns_own_requests(self):
        other = self.submit(actor='manager').data['id']
        own = self.submit(actor='hr',employee='hr').data['id']
        request = APIRequestFactory().get('/overtime/', {'scope':'mine'})
        force_authenticate(request,self.users['hr'])
        response = OvertimeRequestViewSet.as_view({'get':'list'})(request)
        self.assertEqual(response.data['count'],1)
        self.assertEqual(response.data['results'][0]['id'],own)
        response = OvertimeRequestViewSet.as_view({'get':'retrieve'})(request,pk=other)
        self.assertEqual(response.status_code,404)


class SuperAdminHRAccessTests(OvertimeRoutingTests):
    def test_super_admin_role_aliases_have_hr_access_without_staff_flag(self):
        from apps.payroll.views import _is_hr_manager
        from apps.hr_core.views import _is_hr
        from apps.hr_core.overtime import is_final_reviewer
        user = self.users['other']
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.is_staff)
        self.assertFalse(_is_hr_manager(user))
        for code in ['super_admin', 'superadmin']:
            role = Role.objects.create(code=code,name=code)
            user.rbac_profile.roles.set([role])
            self.assertTrue(_is_hr_manager(user))
            self.assertTrue(_is_hr(user))
            self.assertTrue(is_final_reviewer(user))
        pk = self.submit(actor='manager').data['id']
        self.assertEqual(self.call('other','approve',pk=pk).status_code,200)


class OvertimeMultiDayTests(OvertimeRoutingTests):
    def test_multiple_days_and_atomic_failure(self):
        from apps.timesheet.models import DailyAttendanceSummary
        for source in ['manual','biometric']:
            DailyAttendanceSummary.objects.create(employee_code='employee', date=date(2026,9,9), source=source, effective_hours=8, overtime_hours=3)
        payload = {'employee':str(self.employees['employee'].pk),'reason':'Delivery','compensation_type':'cash',
                   'days':[{'work_date':'2026-09-09','requested_hours':'2'},{'work_date':'2026-09-10','requested_hours':'5'}]}
        response = self.call('employee','submit_days',payload)
        self.assertEqual(response.status_code,400,response.data)
        self.assertEqual(OvertimeRequest.objects.count(),0)
        payload['days'][1]['requested_hours']='3'
        response = self.call('employee','submit_days',payload)
        self.assertEqual(response.status_code,201,response.data)
        self.assertEqual(len(response.data['results']),1)
        self.assertEqual(len(response.data['results'][0]['day_entries']),2)
        self.assertEqual(response.data['results'][0]['requested_hours'],'5.00')
        self.assertEqual(OvertimeRequest.objects.count(),1)
        self.assertEqual(self.call('employee','submit_days',payload).status_code,400)
        self.assertEqual(OvertimeRequest.objects.count(),1)
