from datetime import date

from django.db import connection
from django.test import TestCase

from apps.hr_core.models import EmployeeMaster

from .models import PayrollEmployee
from .services.run_generator import generate_monthly_run


class PayrollEmployeeMasterConsolidationTests(TestCase):
    def employee(self, number, *, payroll_enabled=True):
        return EmployeeMaster.objects.create(
            employee_number=number, employee_code=number, emp_code=number,
            first_name='Test', last_name=number, email=f'{number}@example.test',
            employment_status='active', join_date=date(2026, 1, 1),
            payroll_full_name=f'Test {number}', payroll_enabled=payroll_enabled,
            payroll_basic='5000.00',
        )

    def test_payroll_projection_uses_employee_master_table(self):
        employee = self.employee('22001')
        projected = PayrollEmployee.objects.get(pk=employee.pk)
        self.assertEqual(PayrollEmployee._meta.db_table, EmployeeMaster._meta.db_table)
        self.assertEqual(projected.employee_no, employee.employee_number)
        self.assertNotIn('payroll_engine_employee', connection.introspection.table_names())

    def test_generation_uses_only_payroll_enabled_employee_master_rows(self):
        included = self.employee('22001')
        self.employee('22002', payroll_enabled=False)
        run = generate_monthly_run(2099, 2, attendance_snapshot={
            'year': 2099, 'month': 2, 'employee_count': 1, 'complete_count': 0,
            'partial_count': 0, 'missing_count': 1,
            'attendance_complete_pct': '0.00', 'employees': [], 'anomalies': [],
        })
        self.assertEqual(list(run.payslips.values_list('employee_id', flat=True)), [included.pk])
