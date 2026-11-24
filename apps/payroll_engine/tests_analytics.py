"""Payroll analytics for the HR dashboard "Payroll management" panel."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.payroll_engine import catalog
from apps.payroll_engine.models import PayrollEmployee, PayrollRun, Payslip
from apps.payroll_engine.test_support import active_profile, grant_payroll

User = get_user_model()
URL = '/api/v1/payroll-engine/analytics/'


class PayrollAnalyticsTests(TestCase):
    def setUp(self):
        self.hr = User.objects.create_user(username='hr-analyst', email='hr-analyst@example.test')
        self.staff = User.objects.create_user(username='engineer', email='engineer@example.test')
        # Payroll module read access, but not an HR / payroll / finance role.
        self.module_reader = User.objects.create_user(username='project-controls', email='controls@example.test')
        active_profile(self.staff)
        grant_payroll(self.hr, 'hr_manager', actions=('read',))
        grant_payroll(self.module_reader, 'project_controls', actions=('read',))

        self.employees = [
            PayrollEmployee.objects.create(employee_no=f'E{i}', full_name=f'Employee {i}', department=dept)
            for i, dept in enumerate(['Process', 'Process', 'Piping', 'Electrical', 'Instrument', 'HSE', 'Civil', 'Admin', ''])
        ]

    def make_run(self, year, month, gross, net, employees=None, status=catalog.Status.DRAFT):
        employees = self.employees if employees is None else employees
        run = PayrollRun.objects.create(
            year=year, month=month, cycle_code=f'{year:04d}-{month:02d}', status=status,
            employee_count=len(employees), total_gross=Decimal(gross), total_net=Decimal(net),
            total_deductions=Decimal(gross) - Decimal(net))
        share_gross = Decimal(gross) / len(employees)
        share_net = Decimal(net) / len(employees)
        for emp in employees:
            Payslip.objects.create(run=run, employee=emp, snapshot_department=emp.department,
                                   gross_earnings=share_gross, net_payable=share_net)
        return run

    def get(self, user, **params):
        client = APIClient()
        client.force_authenticate(user=user)
        return client.get(URL, params)

    def test_only_payroll_roles_can_read_company_totals(self):
        self.assertEqual(self.get(self.staff).status_code, 403)
        self.assertEqual(self.get(self.module_reader).status_code, 403)
        self.assertIn(APIClient().get(URL).status_code, (401, 403))
        self.assertEqual(self.get(self.hr).status_code, 200)

    def test_empty_when_no_runs(self):
        data = self.get(self.hr).data
        self.assertEqual(data['trend'], [])
        self.assertIsNone(data['latest'])
        self.assertEqual(data['departments'], [])

    def test_trend_latest_change_and_departments(self):
        self.make_run(2026, 6, '900', '810', status=catalog.Status.RELEASED)
        self.make_run(2026, 7, '1000', '900', status=catalog.Status.HR_APPROVED)
        latest = self.make_run(2026, 8, '1100', '990')
        data = self.get(self.hr, months=3).data

        self.assertEqual([row['cycle_code'] for row in data['trend']], ['2026-06', '2026-07', '2026-08'])
        self.assertEqual(data['trend'][1]['status_label'], 'HR Approved')
        self.assertEqual(data['latest']['cycle_code'], '2026-08')
        self.assertEqual(data['latest']['gross_change_pct'], 10.0)
        self.assertEqual(data['latest']['headcount_change'], 0)
        self.assertEqual(data['latest']['average_net'], '110.00')
        self.assertEqual(data['currency'], 'AED')

        departments = data['departments']
        self.assertEqual(len(departments), 6)  # 5 largest + "Other"
        self.assertEqual(departments[0]['department'], 'Process')
        self.assertEqual(departments[0]['employees'], 2)
        self.assertTrue(departments[-1]['department'].startswith('Other ('))
        self.assertEqual(sum(row['employees'] for row in departments), latest.employee_count)
        self.assertAlmostEqual(sum(row['share_pct'] for row in departments), 100, delta=0.5)
        self.assertEqual(data['pending_payslips'], 18)  # July (HR approved) + August (draft); released June excluded

    def test_months_limits_trend_and_rejects_bad_values(self):
        for month in range(1, 9):
            self.make_run(2026, month, '100', '90', employees=self.employees[:1])
        self.assertEqual(len(self.get(self.hr, months=3).data['trend']), 3)
        self.assertEqual(len(self.get(self.hr, months=12).data['trend']), 8)
        self.assertEqual(self.get(self.hr, months='abc').data['months'], 6)
        self.assertEqual(self.get(self.hr, months=999).data['months'], 6)
