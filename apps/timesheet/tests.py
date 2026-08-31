from datetime import date, datetime, time, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from . import config, get_service, mirror_services, services
from .manual_import import _hours, _parse, _time
from .models import DailyAttendanceSummary, TimesheetEvent
from .services import _backfill_email_from_matrix_name
from scripts import timesheet_mirror_sync


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
        self.assertEqual(result['regular_hours'], 9.0)
        self.assertEqual(result['paired_hours'], 13.0)
        self.assertEqual(result['actual_hours'], 13.0)
        self.assertEqual(result['recorded_overtime'], 4.0)

    def test_first_in_and_last_out_use_event_types(self):
        punches = [
            {'event_time': datetime(2026, 8, 1, 7, 30), 'event_type': 'OUT'},
            {'event_time': datetime(2026, 8, 1, 8, 0), 'event_type': 'IN'},
            {'event_time': datetime(2026, 8, 1, 17, 0), 'event_type': 'OUT'},
            {'event_time': datetime(2026, 8, 1, 18, 0), 'event_type': 'IN'},
        ]

        result = mirror_services._compute_paired_hours(punches)

        self.assertEqual(result['first_in'], datetime(2026, 8, 1, 8, 0))
        self.assertEqual(result['last_out'], datetime(2026, 8, 1, 17, 0))
        self.assertEqual(result['elapsed_hours'], 9.0)


class DirectSqlAttendanceQueryTests(SimpleTestCase):
    schema = {
        'table': 'dbo.Mx_VEW_UserAttendanceEvents',
        'columns': {
            'employee_code': 'UserID', 'employee_email': '',
            'employee_name': 'FullName', 'department': 'DptName',
            'punch_time': 'EventDateTime', 'punch_type': 'EntryExitType',
            'in_value': '0', 'out_value': '1',
            'login_time': '', 'logout_time': '', 'date': '',
        },
    }

    def _connection(self):
        connection = MagicMock()
        cursor = connection.__enter__.return_value
        cursor.fetchall.return_value = []
        return connection, cursor

    def test_daily_uses_first_entry_and_last_exit(self):
        connection, cursor = self._connection()
        with patch.object(config, 'SCHEMA', self.schema), patch.object(services, 'connect', return_value=connection):
            services.daily_report.__wrapped__('2026-08-27')

        sql, params = cursor.execute.call_args.args
        self.assertIn('MIN(CASE WHEN [EntryExitType] = %s THEN [EventDateTime] END)', sql)
        self.assertIn('MAX(CASE WHEN [EntryExitType] = %s THEN [EventDateTime] END)', sql)
        self.assertEqual(params, ('0', '1', date(2026, 8, 27)))

    def test_monthly_uses_entry_exit_values_before_date_range(self):
        connection, cursor = self._connection()
        with patch.object(config, 'SCHEMA', self.schema), patch.object(services, 'connect', return_value=connection):
            services.monthly_report.__wrapped__(2026, 8)

        _, params = cursor.execute.call_args.args
        self.assertEqual(params, ('0', '1', date(2026, 8, 1), date(2026, 8, 31)))

    def test_user_history_uses_first_entry_and_last_exit(self):
        connection, cursor = self._connection()
        raw = [
            {'punch_time': datetime(2026, 8, 27, 7, 30), 'punch_type': '1', 'employee_code': 'E001'},
            {'punch_time': datetime(2026, 8, 27, 8, 0), 'punch_type': '0', 'employee_code': 'E001'},
            {'punch_time': datetime(2026, 8, 27, 17, 0), 'punch_type': '1', 'employee_code': 'E001'},
            {'punch_time': datetime(2026, 8, 27, 18, 0), 'punch_type': '0', 'employee_code': 'E001'},
        ]
        with (
            patch.object(config, 'SCHEMA', self.schema),
            patch.object(services, 'connect', return_value=connection),
            patch.object(services, 'rows_to_dicts', return_value=raw),
        ):
            report = services.user_history(
                employee_code='E001',
                from_date='2026-08-27',
                to_date='2026-08-27',
            )

        row = report['rows'][0]
        self.assertEqual(row['first_in'], '2026-08-27T08:00:00')
        self.assertEqual(row['last_out'], '2026-08-27T17:00:00')
        self.assertEqual(row['hours'], 9.0)


class SyncAgentEventTypeTests(SimpleTestCase):
    def test_numeric_zero_entry_value_is_not_discarded(self):
        schema = {
            'table': 'dbo.Mx_VEW_UserAttendanceEvents',
            'employee_code': 'UserID', 'employee_name': 'FullName',
            'employee_email': '', 'department': 'DptName',
            'punch_time': 'EventDateTime', 'punch_type': 'EntryExitType',
            'in_value': '0', 'out_value': '1',
            'login_time': '', 'logout_time': '', 'work_date': '',
        }
        source_rows = [
            {
                'employee_code': 'E001', 'employee_name': 'Test Employee',
                'punch_time': datetime(2026, 8, 27, 8, 0), 'punch_type': 0,
            },
            {
                'employee_code': 'E001', 'employee_name': 'Test Employee',
                'punch_time': datetime(2026, 8, 27, 17, 0), 'punch_type': 1,
            },
        ]

        with (
            patch.object(timesheet_mirror_sync, 'attendance_schema', return_value=schema),
            patch.object(timesheet_mirror_sync, '_query_rows', return_value=source_rows),
        ):
            events = timesheet_mirror_sync.fetch_events(hours=24)

        self.assertEqual([event['event_type'] for event in events], ['IN', 'OUT'])

    def test_watch_checkpoint_only_selects_unseen_events(self):
        events = [
            {'source_event_id': 'known'},
            {'source_event_id': 'new'},
        ]
        self.assertEqual(
            timesheet_mirror_sync.unseen_events(events, {'known'}),
            [{'source_event_id': 'new'}],
        )

    def test_watch_checkpoint_round_trip(self):
        with TemporaryDirectory() as directory:
            state_path = Path(directory) / 'state.json'
            events = [
                {'source_event_id': 'event-2'},
                {'source_event_id': 'event-1'},
                {'source_event_id': 'event-1'},
            ]
            timesheet_mirror_sync.save_seen_event_ids(state_path, events)

            self.assertEqual(
                timesheet_mirror_sync.load_seen_event_ids(state_path),
                {'event-1', 'event-2'},
            )

    def test_sync_refuses_oversized_replay_without_explicit_override(self):
        args = SimpleNamespace(
            users=False, hours=2, full=False, batch_size=100, dry_run=False,
            max_events_per_run=1, allow_large_replay=False,
        )
        events = [{'source_event_id': 'one'}, {'source_event_id': 'two'}]
        with (
            patch.object(timesheet_mirror_sync, 'fetch_events', return_value=events),
            patch.object(timesheet_mirror_sync, 'post_batches') as post_batches,
            self.assertRaises(RuntimeError),
        ):
            timesheet_mirror_sync.sync_once(args)

        post_batches.assert_not_called()


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
        self.assertEqual(second.data['updated'], 0)
        self.assertEqual(second.data['unchanged'], 2)
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

    def test_daily_and_employee_profile_share_uae_day_and_hours(self):
        TimesheetEvent.objects.create(
            source_event_id='profile-midnight-in', employee_code='E004',
            employee_name='Profile Employee',
            event_time=datetime(2026, 8, 19, 20, 30, tzinfo=timezone.utc),
            event_type='IN',
        )
        TimesheetEvent.objects.create(
            source_event_id='profile-midnight-out', employee_code='E004',
            employee_name='Profile Employee',
            event_time=datetime(2026, 8, 19, 22, 30, tzinfo=timezone.utc),
            event_type='OUT',
        )
        identity = lambda rows: rows
        with (
            patch.object(config, 'INGEST_TZ_OFFSET_HOURS', 4),
            patch.object(mirror_services, '_enrich_from_user_master_mirror', side_effect=identity),
            patch.object(mirror_services, '_enrich_with_rad_users', side_effect=identity),
            patch.object(mirror_services, '_backfill_email_from_matrix_name', side_effect=identity),
        ):
            daily = mirror_services.daily_report('2026-08-20')
            profile = mirror_services.user_history(
                employee_code='E004',
                from_date='2026-08-20',
                to_date='2026-08-20',
            )

        daily_row = daily['rows'][0]
        profile_row = profile['rows'][0]
        self.assertEqual(profile_row['date'], '2026-08-20')
        self.assertEqual(profile_row['first_in'], daily_row['first_in'].isoformat())
        self.assertEqual(profile_row['last_out'], daily_row['last_out'].isoformat())
        self.assertEqual(profile_row['hours_worked'], daily_row['hours_worked'])

    def test_employee_profile_uses_manual_daily_fallback(self):
        DailyAttendanceSummary.objects.create(
            employee_code='E005', date=date(2026, 8, 20), source='manual',
            employee_name='Manual Profile Employee', department='Engineering',
            effective_hours=8.0, time_in=time(8, 30), time_out=time(16, 30),
        )

        with patch.object(config, 'INPUT_MODE', 'manual'):
            profile = mirror_services.user_history(
                employee_code='E005',
                from_date='2026-08-20',
                to_date='2026-08-20',
            )

        self.assertEqual(len(profile['rows']), 1)
        row = profile['rows'][0]
        self.assertEqual(row['first_in'], '2026-08-20T08:30:00')
        self.assertEqual(row['last_out'], '2026-08-20T16:30:00')
        self.assertEqual(row['hours_worked'], 8.0)
        self.assertEqual(row['attendance_source'], 'manual_upload')

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

    def test_hybrid_daily_sorts_biometric_datetimes_with_manual_strings(self):
        TimesheetEvent.objects.create(
            source_event_id='hybrid-sort-in', employee_code='E020',
            employee_name='Biometric Employee',
            event_time=datetime(2026, 8, 20, 4, 0, tzinfo=timezone.utc),
            event_type='IN',
        )
        TimesheetEvent.objects.create(
            source_event_id='hybrid-sort-out', employee_code='E020',
            employee_name='Biometric Employee',
            event_time=datetime(2026, 8, 20, 13, 0, tzinfo=timezone.utc),
            event_type='OUT',
        )
        DailyAttendanceSummary.objects.create(
            employee_code='E021', date=date(2026, 8, 20), source='manual',
            employee_name='Manual Employee', effective_hours=8,
            time_in=time(9, 0), time_out=time(17, 0),
        )
        identity = lambda rows: rows
        with (
            patch.object(config, 'INPUT_MODE', 'hybrid'),
            patch.object(config, 'INGEST_TZ_OFFSET_HOURS', 4),
            patch.object(mirror_services, '_enrich_from_user_master_mirror', side_effect=identity),
            patch.object(mirror_services, '_enrich_with_rad_users', side_effect=identity),
            patch.object(mirror_services, '_backfill_email_from_matrix_name', side_effect=identity),
        ):
            report = mirror_services.daily_report('2026-08-20')

        self.assertEqual({row['employee_code'] for row in report['rows']}, {'E020', 'E021'})

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

    def test_recorded_overtime_is_not_added_to_payroll_hours(self):
        from apps.payroll_engine.services import attendance

        report = {
            'rows': [{
                'employee_code': 'E001',
                'days_detail': [{
                    'date': '2026-08-20',
                    'hours': 9.0,
                    'overtime_hours': 4.0,
                    'total_presence_hours': 13.0,
                }],
            }],
        }
        with (
            patch.object(attendance, 'HOURS_FROM_TIMESHEET', True),
            patch.object(attendance, '_safe_monthly_report', return_value=report),
            patch.object(attendance, '_overrides_for_month', return_value={}),
        ):
            result = attendance.compute_monthly_hours(2026, 8)

        self.assertEqual(result['E001'], 9)
