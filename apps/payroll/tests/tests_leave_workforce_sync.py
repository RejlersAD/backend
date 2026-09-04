from datetime import date
from decimal import Decimal

from django.test import TestCase

from apps.hr_core.models import EmployeeMaster
from apps.payroll.models import EmployeeLeaveRecord
from apps.payroll.services.leave_workforce_sync import (
    CANONICAL_SOURCE,
    ensure_canonical_leave_records,
)


class LeaveWorkforceSyncTests(TestCase):
    def create_employee(self, code='23022', branch='RAD'):
        return EmployeeMaster.objects.create(
            employee_number=f'EMP-{code}',
            employee_code=code,
            emp_code=code,
            email=f'{code}@example.com',
            first_name='Firaol',
            last_name='Akawak',
            department='radai',
            job_title_uae='Full Stack Developer',
            join_date=date(2026, 8, 1),
            employment_status='probation',
            branch=branch,
        )

    def test_creates_missing_leave_ledger_from_employee_master(self):
        self.create_employee()

        created = ensure_canonical_leave_records(2026)

        self.assertEqual(created, 1)
        record = EmployeeLeaveRecord.objects.get(employee_code='23022', year=2026)
        self.assertEqual(record.employee_name, 'Firaol Akawak')
        self.assertEqual(record.branch, 'RAD')
        self.assertEqual(record.source_file, CANONICAL_SOURCE)
        self.assertEqual(record.monthly_breakdown.count(), 12)
        self.assertGreater(record.total_earned, Decimal('0'))

    def test_does_not_overwrite_an_imported_leave_ledger(self):
        self.create_employee()
        EmployeeLeaveRecord.objects.create(
            employee_code='23022',
            employee_name='Workbook Name',
            year=2026,
            branch='RAD',
            total_taken=Decimal('4'),
            source_file='approved-leave-workbook.xlsx',
        )

        created = ensure_canonical_leave_records(2026)

        self.assertEqual(created, 0)
        record = EmployeeLeaveRecord.objects.get(employee_code='23022', year=2026)
        self.assertEqual(record.employee_name, 'Workbook Name')
        self.assertEqual(record.total_taken, Decimal('4'))
        self.assertEqual(record.source_file, 'approved-leave-workbook.xlsx')

    def test_skips_employee_without_a_classified_legal_branch(self):
        self.create_employee(branch='')

        created = ensure_canonical_leave_records(2026)

        self.assertEqual(created, 0)
        self.assertFalse(EmployeeLeaveRecord.objects.filter(employee_code='23022').exists())
