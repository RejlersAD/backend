from datetime import date, datetime, time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from . import config, get_service, mirror_services
from .manual_import import _hours, _parse, _time
from .models import DailyAttendanceSummary, TimesheetEvent
from .services import _backfill_email_from_matrix_name


class ServiceSelectionTests(SimpleTestCase):
    def test_biometric_mirror_selects_mirror_service(self):
        with patch.object(config, 'INPUT_MODE', 'biometric'), patch.object(config, 'DATA_SOURCE', 'mirror'):
            self.assertIs(get_service(), mirror_services)

    def test_manual_always_selects_summary_service(self):
        with patch.object(config, 'INPUT_MODE', 'manual'), patch.object(config, 'DATA_SOURCE', 'sqlserver'):
            self.assertIs(get_service(), mirror_services)

    def test_hybrid_selects_mirror_service(self):
        with patch.object(config, 'INPUT_MODE', 'hybrid'), patch.object(config, 'DATA_SOURCE', 'sqlserver'):
            self.assertIs(get_service(), mirror_services)

    def test_hybrid_is_configured_without_direct_sql_credentials(self):
        empty_sql = {'host': '192.168.99.52', 'user': '', 'password': '', 'database': ''}
        with (
            patch.object(config, 'FEATURE_ENABLED', True),
            patch.object(config, 'INPUT_MODE', 'hybrid'),
            patch.object(config, 'DATA_SOURCE', 'sqlserver'),
            patch.object(config, 'SQLSERVER', empty_sql),
        ):
            self.assertTrue(config.is_configured())


class BiometricHoursTests(SimpleTestCase):
    def test_paired_hours_obey_daily_cap(self):
        punches = [
            {'event_time': datetime(2026, 8, 1, 7, 0), 'event_type': 'IN'},
            {'event_time': datetime(2026, 8, 1, 20, 0), 'event_type': 'OUT'},
        ]
        result = mirror_services._compute_paired_hours(punches)
        self.assertEqual(result['effective_hours'], 9.0)
        self.assertEqual(result['paired_hours'], 9.0)


class ManualAttendanceParsingTests(SimpleTestCase):
    def test_cosec_duration_is_converted_to_decimal_hours(self):
        self.assertAlmostEqual(_hours('10:55'), 10 + 55 / 60)

    def test_cosec_missing_clock_out_is_empty(self):
        self.assertIsNone(_time('-'))

    def test_cosec_export_row_is_accepted(self):
        rows = [
            ['Date', 'Emp ID', 'Employee Name', 'Department', 'Time In', 'Time Out', 'Total Hours', 'Status'],
            ['01/08/2026', '05192601', 'Test Employee', 'Rejlers Abu Dhabi', '20:03:18', '06:58:26', '10:55', 'Present'],
        ]

        parsed, errors = _parse(rows, year=2026, month=8)

        self.assertEqual(errors, [])
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].employee_code, '05192601')
        self.assertAlmostEqual(parsed[0].hours, 10 + 55 / 60)
        self.assertEqual(parsed[0].time_in, time(20, 3, 18))
        self.assertEqual(parsed[0].time_out, time(6, 58, 26))

    def test_native_cosec_organization_report_is_accepted(self):
        rows = [
            [None, None, None, 'REJLERS ABU DHABI'],
            [None, None, None, 'Organization-Wise Attendance From 01/07/2026 To 31/07/2026'],
            [None, 'User', ' Name', ' Shift', ' IN-', None, ' OUT-', None, ' IN-', ' OUT-', None, None, None, None, None, None, None, None, None, 'Work'],
            [None, 'ID', None, None, ' SPFID', None, ' SPFID', None, ' SPFID', ' SPFID', None, None, None, None, None, None, None, None, None, 'Hrs'],
            [None] * 20,
            [None, datetime(2026, 7, 4), None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None],
            [None, '05192603', 'Test Employee', None, datetime(2026, 7, 4, 8, 47, 16), None,
             datetime(2026, 7, 4, 19, 32, 5), None, None, None, None, None, None, None, None, None, None, None, None, '10:45'],
        ]

        parsed, errors = _parse(rows, year=2026, month=7)

        self.assertEqual(errors, [])
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].employee_code, '05192603')
        self.assertEqual(parsed[0].date, date(2026, 7, 4))
        self.assertEqual(parsed[0].time_in, time(8, 47, 16))
        self.assertEqual(parsed[0].time_out, time(19, 32, 5))
        self.assertEqual(parsed[0].hours, 10.75)


class AttendanceNameBackfillTests(SimpleTestCase):
    def test_multiple_employee_names_use_one_bulk_profile_query(self):
        from apps.rbac.models import UserProfile

        profiles = [
            SimpleNamespace(
                user=SimpleNamespace(
                    id=1, first_name='Alice', last_name='Smith',
                    email='alice.smith@example.com', username='alice.smith',
                ),
                user_id=1, department='Engineering', job_title='Engineer',
            ),
            SimpleNamespace(
                user=SimpleNamespace(
                    id=2, first_name='Bob', last_name='Jones',
                    email='bob.jones@example.com', username='bob.jones',
                ),
                user_id=2, department='Operations', job_title='Manager',
            ),
        ]
        manager = Mock()
        manager.select_related.return_value.filter.return_value = profiles

        rows = [
            {'employee_name': 'Alice Smith', 'radai_email': None},
            {'employee_name': 'Bob Jones', 'radai_email': None},
        ]
        name_backfill = {'enabled': True, 'min_token_hits': 2, 'max_candidates': 8}

        with (
            patch.object(config, 'NAME_BACKFILL', name_backfill),
            patch.object(UserProfile, 'objects', manager),
        ):
            result = _backfill_email_from_matrix_name(rows)

        manager.select_related.assert_called_once_with('user')
        manager.select_related.return_value.filter.assert_called_once_with(is_deleted=False)
        self.assertEqual(result[0]['radai_email'], 'alice.smith@example.com')
        self.assertEqual(result[1]['radai_email'], 'bob.jones@example.com')


class BiometricMirrorIntegrationTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_ingest_is_idempotent_and_builds_daily_summary(self):
        payload = {
            'events': [
                {
                    'source_event_id': 'event-in',
                    'employee_code': ' E001 ',
                    'employee_name': 'Test Employee',
                    'event_time': '2026-08-20T08:00:00',
                    'event_type': 'IN',
                },
                {
                    'source_event_id': 'event-out',
                    'employee_code': 'E001',
                    'employee_name': 'Test Employee',
                    'event_time': '2026-08-20T17:00:00',
                    'event_type': 'OUT',
                },
            ]
        }
        headers = {'HTTP_X_TIMESHEET_MIRROR_KEY': 'test-key'}
        with patch.object(config, 'MIRROR_API_KEY', 'test-key'), patch.object(config, 'INGEST_TZ_OFFSET_HOURS', 0):
            first = self.client.post(reverse('timesheet:mirror-ingest'), payload, format='json', **headers)
            second = self.client.post(reverse('timesheet:mirror-ingest'), payload, format='json', **headers)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.data['inserted'], 2)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data['updated'], 2)
        self.assertEqual(TimesheetEvent.objects.count(), 2)
        summary = DailyAttendanceSummary.objects.get(
            employee_code='E001', date=date(2026, 8, 20), source='biometric'
        )
        self.assertEqual(summary.effective_hours, 9.0)

    def test_biometric_monthly_report_excludes_manual_row(self):
        DailyAttendanceSummary.objects.create(
            employee_code='E002', date=date(2026, 8, 1), source='biometric', effective_hours=3,
        )
        DailyAttendanceSummary.objects.create(
            employee_code='E002', date=date(2026, 8, 1), source='manual', effective_hours=8,
        )
        with patch.object(config, 'INPUT_MODE', 'biometric'):
            report = mirror_services.monthly_report(2026, 8)
        row = next(item for item in report['rows'] if item['employee_code'] == 'E002')
        self.assertEqual(row['total_hours'], 3.0)

    def test_uae_after_midnight_punch_stays_on_local_attendance_date(self):
        payload = {
            'events': [
                {
                    'source_event_id': 'midnight-in', 'employee_code': 'E003',
                    'event_time': '2026-08-20T00:30:00', 'event_type': 'IN',
                },
                {
                    'source_event_id': 'midnight-out', 'employee_code': 'E003',
                    'event_time': '2026-08-20T02:30:00', 'event_type': 'OUT',
                },
            ]
        }
        with patch.object(config, 'MIRROR_API_KEY', 'test-key'), patch.object(config, 'INGEST_TZ_OFFSET_HOURS', 4):
            response = self.client.post(
                reverse('timesheet:mirror-ingest'), payload, format='json',
                HTTP_X_TIMESHEET_MIRROR_KEY='test-key',
            )

        self.assertEqual(response.status_code, 200)
        summary = DailyAttendanceSummary.objects.get(employee_code='E003', source='biometric')
        self.assertEqual(summary.date, date(2026, 8, 20))
        self.assertEqual(summary.effective_hours, 2.0)

    def test_hybrid_prefers_biometric_and_uses_manual_as_fallback(self):
        DailyAttendanceSummary.objects.create(
            employee_code='E010', date=date(2026, 8, 1), source='manual',
            employee_name='Hybrid Employee', effective_hours=8,
        )
        DailyAttendanceSummary.objects.create(
            employee_code='E010', date=date(2026, 8, 1), source='biometric',
            employee_name='Hybrid Employee', effective_hours=6,
        )
        DailyAttendanceSummary.objects.create(
            employee_code='E010', date=date(2026, 8, 2), source='manual',
            employee_name='Hybrid Employee', effective_hours=7,
        )
        with patch.object(config, 'INPUT_MODE', 'hybrid'):
            report = mirror_services.monthly_report(2026, 8)
        row = next(item for item in report['rows'] if item['employee_code'] == 'E010')
        self.assertEqual(row['total_hours'], 13.0)
        self.assertEqual(report['attendance_source'], 'hybrid')

    def test_hybrid_live_roster_without_punch_is_unknown_not_out(self):
        today = mirror_services._attendance_today()
        DailyAttendanceSummary.objects.create(
            employee_code='E011', date=today, source='manual',
            employee_name='No Punch Employee', effective_hours=8,
        )
        with patch.object(config, 'INPUT_MODE', 'hybrid'):
            report = mirror_services.live_status()
        row = next(item for item in report['rows'] if item['employee_code'] == 'E011')
        self.assertIsNone(row['is_in'])
        self.assertEqual(report['summary']['currently_out'], 0)
        self.assertEqual(report['summary']['no_punch'], 1)


class PayrollDispatcherTests(SimpleTestCase):
    @patch('apps.timesheet.get_service')
    def test_payroll_uses_configured_timesheet_service(self, selector):
        service = Mock()
        service.monthly_report.return_value = {'rows': []}
        selector.return_value = service
        from apps.payroll_engine.services.attendance import _safe_monthly_report

        self.assertEqual(_safe_monthly_report(2026, 8), {'rows': []})
        service.monthly_report.assert_called_once_with(2026, 8)
