from datetime import date
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from .models import PayrollEmployee
from .services.attendance import get_attendance_summary
from .services.run_generator import (
    GenerationError, _attendance_snapshot_payload, generate_monthly_run,
)
from .services.workflow import _validate_attendance_release_basis


class AttendanceSnapshotPayloadTests(SimpleTestCase):
    def test_keeps_employee_facts_and_excludes_punch_detail(self):
        payload = _attendance_snapshot_payload({
            'year': 2026,
            'month': 11,
            'attendance_complete_pct': '100.00',
            'employee_count': 1,
            'complete_count': 1,
            'employees': [{
                'employee_code': '22001',
                'full_name': 'Test Employee',
                'present_days': '20.00',
                'absent_days': '1.00',
                'ot_hours': '2.50',
                'attendance_status': 'complete',
                'days_detail': [{'date': '2026-11-01', 'hours': '9.00'}],
                'punch_count_in': 20,
            }],
            'anomalies': [],
        }, 2026, 11)

        self.assertEqual(payload['employees'][0]['present_days'], '20.00')
        self.assertEqual(payload['employees'][0]['attendance_status'], 'complete')
        self.assertNotIn('days_detail', payload['employees'][0])
        self.assertNotIn('punch_count_in', payload['employees'][0])

    def test_rejects_a_snapshot_for_another_period(self):
        with self.assertRaises(GenerationError):
            _attendance_snapshot_payload({'year': 2026, 'month': 10}, 2026, 11)


class AttendanceSummaryTests(TestCase):
    def test_recorded_absence_is_complete_and_missing_employee_is_missing(self):
        from apps.timesheet.models import DailyAttendanceSummary

        PayrollEmployee.objects.create(employee_no='22001', full_name='Recorded Employee')
        PayrollEmployee.objects.create(employee_no='22002', full_name='Missing Employee')
        DailyAttendanceSummary.objects.create(
            employee_code='22001', date=date(2026, 11, 3),
            employee_name='Recorded Employee', attendance_status='absent',
        )

        summary = get_attendance_summary(2026, 11)
        by_code = {item['employee_code']: item for item in summary['employees']}

        self.assertEqual(summary['employee_count'], 2)
        self.assertEqual(summary['complete_count'], 1)
        self.assertEqual(summary['missing_count'], 1)
        self.assertEqual(by_code['22001']['attendance_status'], 'complete')
        self.assertEqual(by_code['22002']['attendance_status'], 'missing')

    def test_generation_copies_employee_facts_into_run_and_payslip(self):
        employee = PayrollEmployee.objects.create(
            employee_no='22003', full_name='Snapshot Employee', basic='5000.00',
        )
        run = generate_monthly_run(2099, 11, attendance_snapshot={
            'year': 2099,
            'month': 11,
            'employee_count': 1,
            'complete_count': 1,
            'partial_count': 0,
            'missing_count': 0,
            'attendance_complete_pct': '100.00',
            'employees': [{
                'employee_code': '22003',
                'full_name': 'Snapshot Employee',
                'present_days': '20.00',
                'absent_days': '1.00',
                'ot_hours': '2.50',
                'attendance_status': 'complete',
            }],
            'anomalies': [],
        })

        slip = run.payslips.get(employee=employee)
        self.assertEqual(str(slip.present_days), '20.00')
        self.assertEqual(str(slip.absent_days), '1.00')
        self.assertEqual(str(slip.ot_hours), '2.50')
        self.assertEqual(slip.attendance_status, 'complete')
        self.assertEqual(run.attendance_snapshot['employees'][0]['employee_code'], '22003')


class AttendanceReleaseBasisTests(SimpleTestCase):
    def make_run(self, snapshot, captured=True):
        return SimpleNamespace(
            cycle_code='2026-11',
            attendance_snapshot=snapshot,
            attendance_snapshot_at=timezone.now() if captured else None,
        )

    def test_rejects_missing_basis(self):
        with self.assertRaisesMessage(ValidationError, 'no captured attendance basis'):
            _validate_attendance_release_basis(self.make_run({}, captured=False))

    def test_rejects_critical_anomaly_from_stored_snapshot(self):
        run = self.make_run({
            'employees': [{'employee_code': '22001'}],
            'anomalies': [{'severity': 'critical', 'message': 'Missing attendance'}],
        })
        with self.assertRaisesMessage(ValidationError, 'Missing attendance'):
            _validate_attendance_release_basis(run)

    def test_accepts_complete_stored_basis(self):
        run = self.make_run({
            'employees': [{'employee_code': '22001', 'attendance_status': 'complete'}],
            'anomalies': [],
        })
        self.assertIsNone(_validate_attendance_release_basis(run))
