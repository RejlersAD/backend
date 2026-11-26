from datetime import date
from importlib import import_module
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.hr_core.models import EmployeeMaster

from .models import PayrollEmployee, PayrollRun, Payslip
from .services.run_generator import GenerationError, generate_monthly_run


class PayrollIdentityIntegrityTests(TestCase):
    def setUp(self):
        self.master = EmployeeMaster.objects.create(
            employee_number='22001', employee_code='22001', emp_code='22001',
            first_name='Test', last_name='Employee',
            email='test.employee@example.test', employment_status='active',
            join_date=date(2026, 1, 1),
        )

    def test_database_rejects_two_active_records_for_canonical_employee(self):
        PayrollEmployee.objects.create(
            employee_no='22001', full_name='Test Employee', employee=self.master,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            PayrollEmployee.objects.create(
                employee_no='WRONG-22001', full_name='Test Employee', employee=self.master,
            )

    def test_generation_rejects_duplicate_active_user_identity(self):
        user = get_user_model().objects.create_user(
            username='payroll-identity-test', email='identity@example.test', password='unused',
        )
        second_master = EmployeeMaster.objects.create(
            employee_number='22002', employee_code='22002', emp_code='22002',
            first_name='Second', last_name='Employee',
            email='second.employee@example.test', employment_status='active',
            join_date=date(2026, 1, 1),
        )
        PayrollEmployee.objects.create(
            employee_no='22001', full_name='One', user=user, employee=self.master,
        )
        PayrollEmployee.objects.create(
            employee_no='22002', full_name='Two', user=user, employee=second_master,
        )

        with self.assertRaisesMessage(GenerationError, 'multiple active payroll records'):
            generate_monthly_run(2099, 1)

    def test_generation_uses_only_employees_linked_to_directory(self):
        linked = PayrollEmployee.objects.create(
            employee_no='22001', full_name='Directory Employee', employee=self.master,
        )
        PayrollEmployee.objects.create(
            employee_no='ORPHAN-1', full_name='Payroll Only Employee',
        )

        run = generate_monthly_run(2099, 2, attendance_snapshot={
            'year': 2099, 'month': 2, 'employee_count': 1, 'complete_count': 0,
            'partial_count': 0, 'missing_count': 1,
            'attendance_complete_pct': '0.00', 'employees': [], 'anomalies': [],
        })

        self.assertEqual(list(run.payslips.values_list('employee_id', flat=True)), [linked.pk])

    def test_reconciliation_removes_orphans_only_from_draft_history(self):
        linked = PayrollEmployee.objects.create(
            employee_no='22001', full_name='Directory Employee', employee=self.master,
        )
        orphan = PayrollEmployee.objects.create(
            employee_no='NOT-IN-DIRECTORY', full_name='Payroll Only Employee',
        )
        draft = PayrollRun.objects.create(year=2099, month=3, cycle_code='2099-03', status='draft')
        released = PayrollRun.objects.create(year=2099, month=4, cycle_code='2099-04', status='released')
        Payslip.objects.create(run=draft, employee=linked, gross_earnings='100.00', net_payable='100.00')
        Payslip.objects.create(run=draft, employee=orphan, gross_earnings='200.00', net_payable='200.00')
        historical = Payslip.objects.create(
            run=released, employee=orphan, gross_earnings='200.00', net_payable='200.00',
        )

        migration = import_module(
            'apps.payroll_engine.migrations.0021_reconcile_directory_payroll_roster'
        )
        migration.reconcile_directory_payroll_roster(
            import_module('django.apps').apps,
            SimpleNamespace(connection=SimpleNamespace(alias='default')),
        )

        orphan.refresh_from_db()
        draft.refresh_from_db()
        self.assertFalse(orphan.is_active)
        self.assertEqual(list(draft.payslips.values_list('employee_id', flat=True)), [linked.pk])
        self.assertEqual(draft.employee_count, 1)
        self.assertEqual(draft.total_gross, 100)
        self.assertTrue(Payslip.objects.filter(pk=historical.pk).exists())
