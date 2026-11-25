from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.hr_core.models import EmployeeMaster

from .models import PayrollEmployee
from .services.run_generator import GenerationError, generate_monthly_run


class PayrollIdentityIntegrityTests(TestCase):
    def setUp(self):
        self.master = EmployeeMaster.objects.create(
            employee_number='22001', first_name='Test', last_name='Employee',
            email='test.employee@example.test', employment_status='active',
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
        PayrollEmployee.objects.create(employee_no='22001', full_name='One', user=user)
        PayrollEmployee.objects.create(employee_no='22002', full_name='Two', user=user)

        with self.assertRaisesMessage(GenerationError, 'multiple active payroll records'):
            generate_monthly_run(2099, 1)
