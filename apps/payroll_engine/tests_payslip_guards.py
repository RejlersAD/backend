"""Approved and released payroll runs cannot gain payslips.

    python manage.py test apps.payroll_engine.tests_payslip_guards --settings=config.settings_payroll_engine_test
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.payroll_engine import catalog
from apps.payroll_engine.models import PayrollEmployee, PayrollRun, Payslip
from apps.payroll_engine.test_support import grant_payroll

URL = '/api/v1/payroll-engine/payslips/'


class PayslipCreateGuardTests(TestCase):
    def setUp(self):
        # A payroll officer who may add payslips (module create) but approves nothing.
        officer = get_user_model().objects.create_user(username='payroll-officer', email='payroll-officer@example.test')
        grant_payroll(officer, 'payroll_officer', actions=('read', 'create'))
        self.client = APIClient()
        self.client.force_authenticate(user=officer)
        self.employee = PayrollEmployee.objects.create(employee_no='G1', full_name='Guard One')

    def make_run(self, month, status):
        return PayrollRun.objects.create(year=2026, month=month, cycle_code=f'2026-{month:02d}', status=status,
                                         employee_count=0, total_gross=Decimal('0'), total_net=Decimal('0'))

    def post(self, run):
        return self.client.post(URL, {'run': run.pk, 'employee': self.employee.pk, 'basic': '5000'}, format='json')

    def test_draft_run_accepts_payslip_and_updates_run_totals(self):
        run = self.make_run(9, catalog.Status.DRAFT)
        response = self.post(run)
        self.assertEqual(response.status_code, 201, response.data)
        run.refresh_from_db()
        self.assertEqual(run.employee_count, 1)
        self.assertEqual(Payslip.objects.filter(run=run).count(), 1)

    def test_approved_or_released_runs_refuse_new_payslips(self):
        for month, status in enumerate([catalog.Status.HR_APPROVED, catalog.Status.FINANCE_APPROVED,
                                        catalog.Status.RELEASED], start=10):
            run = self.make_run(month, status)
            response = self.post(run)
            self.assertEqual(response.status_code, 409, status)
            self.assertIn('only Draft runs allow adding payslips', response.data['error'])
            self.assertFalse(Payslip.objects.filter(run=run).exists())
