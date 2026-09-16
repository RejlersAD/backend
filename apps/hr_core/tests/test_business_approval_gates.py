"""Approval assignment, access and sequence regressions across HR/payroll."""
from datetime import date
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.procurement.tests.approval_fixtures import grant_approval, set_position
from apps.rbac.models import UserRole
from apps.hr_core.models import HRWorkflowDefinition, HRWorkflowStage, HRWorkflowInstance, HRWorkflowTask
from apps.hr_core.workflows import HRWorkflowService
from apps.hr_core.overtime import can_review as can_review_overtime
from apps.payroll.models import DailyWorkLog
from apps.payroll.views import DailyWorkLogViewSet
from apps.payroll_engine.models import PayrollRun
from apps.payroll_engine.catalog import Status
from apps.payroll_engine.services.workflow import hr_approve, finance_approve, release, can_transition
from apps.finance.models import Invoice, Approval
from apps.finance.approval_eligibility import can_approve_invoice
from apps.finance.services.workflow_service import FinanceWorkflowService
from apps.finance.views import get_approval_details, submit_approval_decision
from apps.finance.salary_approval_service import decide_salary_slip
from apps.finance.salary_models import EmployeeSalaryInfo, SalarySlip, SalarySlipApproval, PayrollRun as LegacyRun
from apps.core.project_models import Project, ProjectMember
from apps.onboarding.models import OffboardingRecord, ExitApproval
from apps.onboarding.rbac import can_decide_exit_project, can_manage_offboarding_stage
from apps.onboarding.views import OffboardingRecordViewSet


@override_settings(TEAMS_APPROVAL_WEBHOOK_URL='', WEB_PUSH_VAPID_PRIVATE_KEY='', EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class BusinessApprovalGateTests(TestCase):
    def setUp(self):
        self.users = {}
        self.employees = {}
        for name, title in [('employee', 'Engineer'), ('manager', 'Project Manager'),
                            ('hr', 'HR Manager'), ('finance', 'Finance Manager'), ('ceo', 'CEO')]:
            user = get_user_model().objects.create_user(username=name, email=f'{name}@example.test',
                                                        is_superuser=name == 'ceo', is_staff=name == 'ceo')
            grant_approval(user, 'payroll', 'hr_management', 'hr_onboarding', 'finance_incoming', 'finance_salary')
            self.users[name] = user
            self.employees[name] = set_position(user, title)
        self.employees['employee'].manager = self.employees['manager']
        self.employees['employee'].save(update_fields=['manager'])

    def workflow(self, subject='hr.overtime_request', requester='employee'):
        definition = HRWorkflowDefinition.objects.create(code='test-gates', name='Gates', subject_type=subject)
        manager = HRWorkflowStage.objects.create(definition=definition, code='manager_review', name='Manager',
            sequence=1, approver_type='employee_manager')
        hr = HRWorkflowStage.objects.create(definition=definition, code='hr_review', name='HR',
            sequence=2, approver_type='role', approver_value='hr_manager')
        instance = HRWorkflowInstance.objects.create(definition=definition, subject_type=subject, subject_id='1',
            employee=self.employees['employee'], requested_by=self.users[requester], current_stage=manager)
        task = HRWorkflowTask.objects.create(instance=instance, stage=manager, assigned_to=self.users['manager'])
        return instance, task, hr

    def test_ceo_admin_permission_does_not_supply_hr_assignment(self):
        instance, task, _ = self.workflow()
        for decision in ('approve', 'reject'):
            with self.assertRaises(PermissionDenied):
                HRWorkflowService.decide(instance, self.users['ceo'], decision)
        task.refresh_from_db()
        self.assertEqual(task.status, 'pending')
        self.assertIsNone(task.decided_by)

    def test_manager_submission_still_requires_explicit_manager_decision(self):
        instance, task, _ = self.workflow(requester='manager')
        self.assertTrue(can_review_overtime(task, self.users['manager']))
        self.assertFalse(can_review_overtime(task, self.users['hr']))
        with patch.object(HRWorkflowService, '_notify_task'):
            updated = HRWorkflowService.decide(instance, self.users['manager'], 'approve')
        task.refresh_from_db()
        self.assertEqual(task.decided_by, self.users['manager'])
        self.assertEqual(updated.current_stage.code, 'hr_review')

    def test_future_stage_cannot_open_without_prior_recorded_approval(self):
        instance, _, hr = self.workflow()
        instance.current_stage = hr
        instance.save(update_fields=['current_stage'])
        task = HRWorkflowTask.objects.create(instance=instance, stage=hr, assigned_role_code='hr_manager')
        self.assertFalse(HRWorkflowService.can_act(task, self.users['hr']))
        self.assertEqual(HRWorkflowService._task_recipients(task), [])

    def test_manager_change_and_permission_revocation_take_effect_immediately(self):
        instance, task, _ = self.workflow()
        self.assertTrue(HRWorkflowService.can_act(task, self.users['manager']))
        self.employees['employee'].manager = self.employees['finance']
        self.employees['employee'].save(update_fields=['manager'])
        task = HRWorkflowTask.objects.select_related('instance__employee', 'stage').get(pk=task.pk)
        self.assertFalse(HRWorkflowService.can_act(task, self.users['manager']))
        self.assertTrue(HRWorkflowService.can_act(task, self.users['finance']))
        UserRole.objects.filter(user_profile=self.users['finance'].rbac_profile).delete()
        self.assertFalse(HRWorkflowService.can_act(task, self.users['finance']))

    def test_missing_manager_does_not_skip_to_hr(self):
        instance, _, _ = self.workflow()
        self.employees['employee'].manager = None
        self.employees['employee'].save(update_fields=['manager'])
        with self.assertRaises(ValidationError):
            HRWorkflowService.start(instance.definition.code, instance.subject_type, '2',
                employee=self.employees['employee'], requested_by=self.users['employee'])

    def test_used_workflow_future_stages_cannot_be_deleted_to_skip_approval(self):
        from types import SimpleNamespace
        from apps.hr_core.views import HRWorkflowStageViewSet
        _, _, hr_stage = self.workflow()
        view = HRWorkflowStageViewSet()
        view.request = SimpleNamespace(user=self.users['ceo'])
        with self.assertRaises(ValidationError):
            view.perform_destroy(hr_stage)
        self.assertTrue(HRWorkflowStage.objects.filter(pk=hr_stage.pk).exists())

    def test_leave_api_completes_manager_then_canonical_hr_without_generic_admin_role(self):
        from apps.payroll.models import LeaveRequest, LeaveType
        from apps.payroll.views import LeaveRequestViewSet
        definition = HRWorkflowDefinition.objects.create(code='leave_request_v1', name='Leave', subject_type='payroll.leave_request')
        HRWorkflowStage.objects.create(definition=definition, code='manager_review', name='Manager', sequence=1, approver_type='employee_manager')
        HRWorkflowStage.objects.create(definition=definition, code='hr_review', name='HR', sequence=2, approver_type='role', approver_value='hr_manager')
        leave_type = LeaveType.objects.create(code='OTHER', name='Other', category='other')
        def call(actor, action, data=None, pk=None):
            request = APIRequestFactory().post('/leave/', data or {}, format='json')
            force_authenticate(request, self.users[actor])
            return LeaveRequestViewSet.as_view({'post': action})(request, **({'pk': pk} if pk else {}))
        with self.captureOnCommitCallbacks(execute=True), patch('apps.notifications.services.NotificationService.create_notification', return_value=None):
            response = call('employee', 'create', {'leave_type': leave_type.pk, 'employee_name': 'Employee', 'start_date': '2026-10-05', 'end_date': '2026-10-06'})
            self.assertEqual(response.status_code, 201, response.data)
            pk = response.data['id']
            self.assertNotEqual(call('ceo', 'approve', pk=pk).status_code, 200)
            self.assertEqual(call('manager', 'rm_approve', pk=pk).status_code, 200)
            result = call('hr', 'approve', pk=pk)
            self.assertEqual(result.status_code, 200, result.data)
            self.assertEqual(LeaveRequest.objects.get(pk=pk).status, 'APPROVED')

    def test_daily_log_requires_actual_selected_manager_and_pending(self):
        log = DailyWorkLog.objects.create(user=self.users['employee'], log_date=date(2026, 9, 1),
            task_title='Work', hours_spent=8, submitted_to_role='reporting_manager')
        self.assertTrue(DailyWorkLogViewSet._can_approve(self.users['manager'], log))
        self.assertFalse(DailyWorkLogViewSet._can_approve(self.users['ceo'], log))
        log.submitted_to_role = 'project_manager'
        self.assertFalse(DailyWorkLogViewSet._can_approve(self.users['manager'], log))
        log.submitted_to_role = 'reporting_manager'
        log.approval_status = 'approved'
        self.assertFalse(DailyWorkLogViewSet._can_approve(self.users['manager'], log))

    def test_payroll_positions_sequence_and_stale_instance(self):
        run = PayrollRun.objects.create(year=2026, month=9, cycle_code='2026-09')
        stale = PayrollRun.objects.get(pk=run.pk)
        self.assertFalse(can_transition(run, self.users['ceo'], Status.HR_APPROVED))
        with self.assertRaises(PermissionDenied):
            hr_approve(run, self.users['ceo'])
        hr_approve(run, self.users['hr'])
        self.assertFalse(can_transition(run, self.users['hr'], Status.FINANCE_APPROVED))
        from django.core.exceptions import ValidationError as DjangoValidationError
        with self.assertRaises(DjangoValidationError):
            hr_approve(stale, self.users['hr'])
        finance_approve(run, self.users['finance'])
        release(run, self.users['finance'])
        run.refresh_from_db()
        self.assertEqual(run.status, Status.RELEASED)
        self.assertEqual(run.hr_approved_by, self.users['hr'])
        self.assertEqual(run.finance_approved_by, self.users['finance'])

    def invoice(self):
        invoice = Invoice.objects.create(invoice_number='GATE-1', status='pending_approval', total_amount=100)
        rows = [Approval.objects.create(invoice=invoice, approver_name=name,
                approver_email=self.users[name].email, approval_level=level, level_name=name)
                for level, name in [(0, 'manager'), (0, 'hr'), (2, 'ceo')]]
        return invoice, rows

    def test_invoice_bound_actor_all_lower_peers_and_terminal_status(self):
        invoice, rows = self.invoice()
        self.assertFalse(can_approve_invoice(rows[0], self.users['ceo']))
        self.assertFalse(can_approve_invoice(rows[2], self.users['ceo']))
        rows[0].status = 'approved'; rows[0].save(update_fields=['status'])
        self.assertFalse(can_approve_invoice(rows[2], self.users['ceo']))
        rows[1].status = 'approved'; rows[1].save(update_fields=['status'])
        self.assertTrue(can_approve_invoice(rows[2], self.users['ceo']))
        invoice.status = 'rejected'; invoice.save(update_fields=['status'])
        rows[2].refresh_from_db()
        self.assertFalse(can_approve_invoice(rows[2], self.users['ceo']))

    def test_invoice_token_login_and_identity_required(self):
        _, rows = self.invoice()
        request = APIRequestFactory().get('/finance/approval/')
        response = get_approval_details(request, token=rows[0].approval_token)
        self.assertIn(response.status_code, (401, 403))
        request = APIRequestFactory().post('/finance/approval/', {'decision': 'approve'}, format='json')
        force_authenticate(request, self.users['ceo'])
        response = submit_approval_decision(request, token=rows[0].approval_token)
        self.assertEqual(response.status_code, 403)
        rows[0].refresh_from_db(); self.assertEqual(rows[0].status, 'pending')

    def test_invoice_service_rechecks_assigned_actor_and_never_accepts_token_alone(self):
        invoice, rows = self.invoice()
        service = FinanceWorkflowService.__new__(FinanceWorkflowService)
        service.email_service = Mock()
        with self.assertRaises(PermissionDenied):
            service.process_approval_decision(str(rows[0].approval_token), 'approve')
        with self.assertRaises(PermissionDenied):
            service.process_approval_decision(str(rows[2].approval_token), 'approve', actor=self.users['ceo'])
        self.assertTrue(service.process_approval_decision(str(rows[0].approval_token), 'approve', actor=self.users['manager']))
        invoice.refresh_from_db(); self.assertEqual(invoice.status, 'pending_approval')

    def test_invoice_route_configuration_requires_business_configurator_and_no_self_assignment(self):
        from types import SimpleNamespace
        from apps.finance.serializers import ApprovalRouteSerializer
        data = {'invoice_type': 'finance', 'approval_chain': [
            {'level': 0, 'name': 'Reviewer', 'email': self.users['manager'].email}]}
        valid = ApprovalRouteSerializer(data=data, context={'request': SimpleNamespace(user=self.users['finance'])})
        self.assertTrue(valid.is_valid(), valid.errors)
        ceo = ApprovalRouteSerializer(data=data, context={'request': SimpleNamespace(user=self.users['ceo'])})
        self.assertFalse(ceo.is_valid())
        data['approval_chain'][0]['email'] = self.users['finance'].email
        own = ApprovalRouteSerializer(data=data, context={'request': SimpleNamespace(user=self.users['finance'])})
        self.assertFalse(own.is_valid())

    def test_invoice_existing_route_is_not_rebuilt_and_missing_stage_is_not_skipped(self):
        from apps.finance.approval_eligibility import validated_invoice_route
        with self.assertRaises(ValidationError):
            validated_invoice_route([{'level': 0, 'name': 'Missing', 'email': ''},
                {'level': 1, 'name': 'CEO', 'email': self.users['ceo'].email}])
        invoice, _ = self.invoice()
        service = FinanceWorkflowService.__new__(FinanceWorkflowService)
        service._extract_invoice_data = Mock()
        self.assertFalse(service.process_invoice(invoice.pk))
        service._extract_invoice_data.assert_not_called()
        self.assertEqual(invoice.approvals.count(), 3)

    def test_salary_slip_requires_named_current_assignment(self):
        from apps.finance.salary_approval_service import submit_salary_slip
        from apps.finance.salary_serializers import SalarySlipApprovalSerializer
        from apps.finance.salary_views import SalarySlipApprovalViewSet
        from types import SimpleNamespace
        salary = EmployeeSalaryInfo.objects.create(user=self.users['employee'], employee_id='PAY-1', basic_salary=100)
        run = LegacyRun.objects.create(run_code='GATE-PAY', month=9, year=2026,
                                      period_start=date(2026, 9, 1), period_end=date(2026, 9, 30))
        slip = SalarySlip.objects.create(slip_number='GATE-SLIP', payroll_run=run, employee_salary_info=salary,
            month=9, year=2026, basic_salary=100, gross_salary=100, net_salary=100, status='generated')
        with self.assertRaises(ValidationError):
            submit_salary_slip(slip.pk, self.users['hr'])
        first = SalarySlipApproval.objects.create(salary_slip=slip, approver=self.users['hr'], approval_level=1)
        second = SalarySlipApproval.objects.create(salary_slip=slip, approver=self.users['finance'], approval_level=2)
        own = SalarySlipApprovalSerializer(instance=first, data={'approval_level': 3}, partial=True,
            context={'request': SimpleNamespace(user=self.users['hr'])})
        self.assertFalse(own.is_valid())
        stale = SalarySlipApprovalSerializer(instance=second, data={'approval_level': 3, 'salary_slip': slip.pk}, partial=True,
            context={'request': SimpleNamespace(user=self.users['hr'])})
        self.assertTrue(stale.is_valid(), stale.errors)
        with self.assertRaises(PermissionDenied):
            submit_salary_slip(slip.pk, self.users['ceo'])
        submit_salary_slip(slip.pk, self.users['hr'])
        with self.assertRaises(ValidationError):
            SalarySlipApprovalViewSet().perform_update(stale)
        second.refresh_from_db(); self.assertEqual(second.approval_level, 2)
        target = SalarySlip.objects.create(slip_number='OTHER-SLIP', payroll_run=run, employee_salary_info=salary,
            month=10, year=2026, basic_salary=100, gross_salary=100, net_salary=100, status='generated')
        moved = SalarySlipApprovalSerializer(instance=second, data={'salary_slip': target.pk}, partial=True,
            context={'request': SimpleNamespace(user=self.users['hr'])})
        self.assertFalse(moved.is_valid())
        for actor in (self.users['ceo'], self.users['finance']):
            with self.assertRaises(PermissionDenied):
                decide_salary_slip(slip.pk, actor, 'approve')
        decide_salary_slip(slip.pk, self.users['hr'], 'approve', approval_id=first.pk)
        slip.refresh_from_db(); self.assertEqual(slip.status, 'pending_approval')
        decide_salary_slip(slip.pk, self.users['finance'], 'approve')
        slip.refresh_from_db(); self.assertEqual(slip.status, 'approved')
        self.assertEqual(slip.approved_by, self.users['finance'])

    def offboarding(self):
        employee = self.users['employee']
        record = OffboardingRecord.objects.create(user=employee, employee_name='Employee', employee_email=employee.email,
            position='Engineer', department='Engineering', exit_reason='resignation',
            last_working_day=date(2026, 10, 1), target_completion_date=date(2026, 10, 1),
            project_manager_approval_status='pending')
        for index, manager in enumerate((self.users['manager'], self.users['finance'])):
            project = Project.objects.create(code=f'EXIT-{index}', name='Project', owner=manager, status='active')
            ProjectMember.objects.create(project=project, user=employee, role='engineer')
            ExitApproval.objects.create(offboarding_record=record, approver=manager, approval_step='project_manager')
        return record

    def test_offboarding_all_current_project_managers_must_decide(self):
        record = self.offboarding()
        self.assertFalse(can_decide_exit_project(record, self.users['ceo']))
        for index, name in enumerate(('manager', 'finance')):
            req = APIRequestFactory().post('/offboarding/', {'decision': 'approved'}, format='json')
            force_authenticate(req, self.users[name])
            with patch('apps.onboarding.views.NotificationService.create_notification', return_value=None):
                response = OffboardingRecordViewSet.as_view({'post': 'project_manager_decision'})(req, pk=record.pk)
            self.assertEqual(response.status_code, 200, response.data)
            record.refresh_from_db()
            self.assertEqual(record.project_manager_approval_status, 'pending' if index == 0 else 'approved')

    def test_offboarding_old_notification_or_admin_does_not_grant_assignment(self):
        record = self.offboarding()
        from apps.notifications.models import Notification
        Notification.objects.create(recipient=self.users['ceo'], title='Exit', message='Old request',
            metadata={'offboarding_id': record.pk, 'event': 'employee_exit_initiated'})
        self.assertFalse(can_decide_exit_project(record, self.users['ceo']))
        Project.objects.filter(owner=self.users['manager']).update(owner=self.users['hr'])
        self.assertFalse(can_decide_exit_project(record, self.users['manager']))

    def test_offboarding_later_checklist_requires_completed_prior_stages(self):
        record = self.offboarding()
        from apps.onboarding.models import CHECKLIST_STAGE_FINAL_SETTLEMENT
        self.assertFalse(can_manage_offboarding_stage(self.users['finance'], CHECKLIST_STAGE_FINAL_SETTLEMENT, record))
        self.assertFalse(can_manage_offboarding_stage(self.users['ceo'], CHECKLIST_STAGE_FINAL_SETTLEMENT, record))
