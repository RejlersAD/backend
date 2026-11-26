"""Employees see their own released payslips in self-service, and nothing else.

    python manage.py test apps.payroll_engine.tests_payslip_self_service --settings=config.settings_payroll_engine_test
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.hr_core.models import EmployeeMaster
from apps.payroll_engine import catalog
from apps.payroll_engine.models import PayrollRun, Payslip

BASE = '/api/v1/payroll-engine/payslips/'


class PayslipSelfServiceTests(TestCase):
    def setUp(self):
        users = get_user_model().objects
        self.me = users.create_user(username='employee-me', email='employee-me@example.test')
        self.colleague = users.create_user(username='employee-other', email='employee-other@example.test')
        self.mine = self.employee('S1', 'Self', 'Service', self.me)
        self.theirs = self.employee('S2', 'Other', 'Colleague', self.colleague)
        self.released = self.make_run(8, catalog.Status.RELEASED)
        self.draft = self.make_run(9, catalog.Status.DRAFT)
        self.finance_approved = self.make_run(7, catalog.Status.FINANCE_APPROVED)
        self.my_released = self.slip(self.released, self.mine)
        self.slip(self.draft, self.mine)
        self.slip(self.finance_approved, self.mine)
        self.their_released = self.slip(self.released, self.theirs)

    def employee(self, number, first_name, last_name, user):
        return EmployeeMaster.objects.create(
            employee_number=number, employee_code=number, emp_code=number,
            first_name=first_name, last_name=last_name, user=user, email=user.email,
            join_date='2025-01-01', payroll_enabled=True,
            payroll_full_name=f'{first_name} {last_name}',
        )

    def make_run(self, month, status):
        return PayrollRun.objects.create(year=2026, month=month, cycle_code=f'2026-{month:02d}', status=status,
                                         employee_count=0, total_gross=Decimal('0'), total_net=Decimal('0'))

    def slip(self, run, employee):
        return Payslip.objects.create(run=run, employee=employee, snapshot_full_name=employee.full_name,
                                      basic=Decimal('1000'), gross_earnings=Decimal('1000'), net_payable=Decimal('1000'))

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_employee_without_payroll_access_lists_only_own_released_payslips(self):
        response = self.client_for(self.me).get(f'{BASE}mine/')
        self.assertEqual(response.status_code, 200, getattr(response, 'data', None))
        self.assertEqual([row['id'] for row in response.data['results']], [self.my_released.pk])

    def test_full_payslip_list_stays_closed_to_employees(self):
        self.assertEqual(self.client_for(self.me).get(BASE).status_code, 403)

    def test_download_own_released_payslip_only(self):
        client = self.client_for(self.me)
        own = client.get(f'{BASE}{self.my_released.pk}/mine-download/')
        self.assertEqual(own.status_code, 200)
        self.assertIn('spreadsheetml', own['Content-Type'])
        self.assertEqual(client.get(f'{BASE}{self.their_released.pk}/mine-download/').status_code, 404)
        draft_slip = Payslip.objects.get(run=self.draft, employee=self.mine)
        self.assertEqual(client.get(f'{BASE}{draft_slip.pk}/mine-download/').status_code, 404)

    def test_link_through_hr_employee_record_counts(self):
        linked_user = get_user_model().objects.create_user(username='linked', email='linked@example.test')
        master = self.employee('HRL1', 'Linked', 'Person', linked_user)
        slip = self.slip(self.released, master)
        response = self.client_for(linked_user).get(f'{BASE}mine/')
        self.assertEqual([row['id'] for row in response.data['results']], [slip.pk])

    def test_anonymous_is_refused(self):
        self.assertIn(APIClient().get(f'{BASE}mine/').status_code, (401, 403))
