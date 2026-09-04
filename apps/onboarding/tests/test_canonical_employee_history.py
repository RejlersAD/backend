from datetime import date, timedelta

from django.test import TestCase

from apps.hr_core.models import EmployeeMaster
from apps.onboarding.models import OnboardingRecord


class CanonicalEmployeeOnboardingHistoryTests(TestCase):
    def test_employee_can_have_multiple_historical_onboarding_records(self):
        employee = EmployeeMaster.objects.create(
            employee_number='HISTORY-001',
            employee_code='HISTORY-001',
            emp_code='HISTORY-001',
            first_name='History',
            last_name='Employee',
            join_date=date(2020, 1, 1),
        )
        common = {
            'canonical_employee': employee,
            'employee_name': 'History Employee',
            'employee_id': 'HISTORY-001',
            'position': 'Engineer',
            'department': 'Engineering',
            'joining_date': date(2020, 1, 1),
            'target_completion_date': date(2020, 1, 1) + timedelta(days=30),
        }

        OnboardingRecord.objects.create(
            **common,
            employee_email='history.first@example.com',
        )
        OnboardingRecord.objects.create(
            **{
                **common,
                'joining_date': date(2024, 1, 1),
                'target_completion_date': date(2024, 1, 31),
            },
            employee_email='history.rehire@example.com',
        )

        self.assertEqual(employee.onboarding_records.count(), 2)
