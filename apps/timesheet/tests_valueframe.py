"""ValueFrame reconciliation: parse the Wage Type Report, compare with attendance."""
import io
from datetime import date, datetime, time
from decimal import Decimal

import openpyxl
from unittest import mock
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.timesheet.models import BiometricUserMaster, DailyAttendanceSummary, ValueFrameEntry, ValueFrameImport
from apps.timesheet.valueframe import compare_import, parse_wage_type_report
from apps.timesheet import valueframe_views

HEADER = ['Employee Number', 'Employee Number 2', 'Employee Name', 'Normal', 'Working time flexibility free',
          'Sick leave, medical certificate', 'Annual vacation', 'Paternity leave, paid', 'Bank holiday',
          'Other paid leave', 'Other unpaid leave', 'Billability %', 'Invoicing Hours', 'Divisor', 'Total Hours']


def workbook(rows, title='Wage Type Report From 1.8.2026 - 31.8.2026', header=HEADER, name='VF.xlsx'):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append([title])
    sheet.append(['Company', 'Rejlers International Engineering Solutions AB'])
    for label in ('Location', 'Team', 'Employee', 'Wage Type', 'Employment Type'):
        sheet.append([label, 'Select'])
    sheet.append([]); sheet.append([]); sheet.append([])
    sheet.append(['Wage Type Report'])
    sheet.append(header)
    for row in rows:
        sheet.append(row)
    sheet.append(['Total', None, None, sum(r[3] or 0 for r in rows)])
    buffer = io.BytesIO()
    book.save(buffer)
    return SimpleUploadedFile(name, buffer.getvalue())


def vf_row(code, name, normal, annual=None, unpaid=None):
    return [code, None, name, normal, None, None, annual, None, None, None, unpaid, 100, normal, normal, (normal or 0) + (annual or 0)]


class ValueFrameParseTests(TestCase):
    def test_reads_period_company_rows_and_skips_total(self):
        parsed = parse_wage_type_report(workbook([vf_row(22393, 'Aarekh', 153), vf_row(22722.0, 'Abdul', 45, annual=99)]))
        self.assertEqual((parsed['period_start'], parsed['period_end']), (date(2026, 8, 1), date(2026, 8, 31)))
        self.assertEqual(parsed['company'], 'Rejlers International Engineering Solutions AB')
        self.assertEqual([r['employee_code'] for r in parsed['rows']], ['22393', '22722'])
        self.assertEqual(parsed['rows'][1]['annual_hours'], 99.0)

    def test_rejects_bad_files_with_actionable_messages(self):
        with self.assertRaisesMessage(ValueError, 'Employee Number'):
            parse_wage_type_report(workbook([vf_row(1, 'A', 9)], header=['Code', 'Name', 'Normal']))
        with self.assertRaisesMessage(ValueError, 'period'):
            parse_wage_type_report(workbook([vf_row(1, 'A', 9)], title='Wage Type Report'))
        with self.assertRaisesMessage(ValueError, '.xlsx'):
            parse_wage_type_report(SimpleUploadedFile('vf.csv', b'a,b'))

    def test_duplicate_employee_rows_warn(self):
        parsed = parse_wage_type_report(workbook([vf_row(5, 'A', 9), vf_row(5, 'A again', 18)]))
        self.assertEqual(len(parsed['rows']), 1)
        self.assertTrue(parsed['warnings'])


class ValueFrameComparisonTests(TestCase):
    def setUp(self):
        # Never reach the live Monthly matrix source (SQL Server / mirror) from tests:
        # None = source unavailable, so office presence falls back to stored daily rows.
        self.matrix = mock.patch('apps.timesheet.valueframe._matrix_office_days', return_value=None)
        self.matrix_days = self.matrix.start()
        self.addCleanup(self.matrix.stop)
        self.item = ValueFrameImport.objects.create(period_start=date(2026, 8, 1), period_end=date(2026, 8, 31),
                                                    file_name='VF.xlsx', file_sha256='x', employee_count=4)
        for code, normal in (('100', 45), ('200', 45), ('300', 18), ('400', 0)):
            ValueFrameEntry.objects.create(vf_import=self.item, employee_code=code, employee_name=f'E{code}', normal_hours=normal)
        for code in ('100', '200', '400'):
            BiometricUserMaster.objects.create(employee_code=code, full_name=f'E{code}')

    def attend(self, code, day, source=DailyAttendanceSummary.SOURCE_BIOMETRIC, hours=8.5):
        DailyAttendanceSummary.objects.create(employee_code=code, date=date(2026, 8, day), source=source,
                                              effective_hours=hours, first_in=timezone.make_aware(datetime.combine(date(2026, 8, day), time(8))))

    def test_presence_sources_and_flags(self):
        # 100: 5 booked days covered by office x2, HR manual x1, approved WFH x1, site visit x1 -> ok
        self.attend('100', 3); self.attend('100', 4)
        self.attend('100', 5, source=DailyAttendanceSummary.SOURCE_MANUAL)
        from apps.payroll.models import LeaveRequest, LeaveType
        wfh = LeaveType.objects.create(code='WFH', name='Work From Home', category='work_from_home')
        LeaveRequest.objects.create(employee_code='100', employee_name='E100', leave_type=wfh, status='APPROVED',
                                    start_date=date(2026, 8, 6), end_date=date(2026, 8, 6))
        from apps.site_visits.models import ClientSite, SiteVisitCheckIn
        site = ClientSite.objects.create(name='Plant', client_name='Client', address='Ruwais', latitude=Decimal('24.1'), longitude=Decimal('52.7'))
        SiteVisitCheckIn.objects.create(employee_code='100', employee_name='E100', site=site,
                                        check_in_time=timezone.make_aware(datetime(2026, 8, 7, 9)))
        # 200: 5 booked days, badged in once -> flagged with a 4-day gap
        self.attend('200', 3)
        result = compare_import(self.item)
        rows = {r['employee_code']: r for r in result['rows']}
        self.assertEqual(rows['100']['status'], 'ok')
        self.assertEqual((rows['100']['office_days'], rows['100']['manual_days'], rows['100']['wfh_days'], rows['100']['site_days']), (2, 1, 1, 1))
        self.assertEqual((rows['100']['office_hours'], rows['100']['manual_hours'], rows['100']['actual_hours']), (17.0, 8.5, 25.5))
        self.assertEqual(rows['200']['status'], 'flagged')
        self.assertEqual(rows['200']['gap_days'], 4.0)
        self.assertEqual(rows['200']['pattern'], 'partial')
        self.assertEqual(rows['300']['pattern'], '')  # not enrolled is its own status
        self.assertEqual(rows['300']['status'], 'not_enrolled')   # booked, never badged, not in biometric system
        self.assertEqual(rows['400']['status'], 'no_booking')
        self.assertEqual(result['summary']['flagged'], 1)
        self.assertEqual(result['rows'][0]['employee_code'], '200')  # flagged first

    def test_office_presence_comes_from_the_monthly_matrix_when_available(self):
        # The matrix says 200 badged in on 5 days; a stored biometric row is ignored in favour of the matrix.
        self.matrix_days.return_value = {'200': {date(2026, 8, d): h for d, h in ((3, 9.0), (4, 8.5), (5, 9.0), (6, 7.25), (7, 0.0))}}
        self.attend('100', 3)
        result = compare_import(self.item)
        rows = {r['employee_code']: r for r in result['rows']}
        self.assertEqual(rows['200']['office_days'], 5)
        self.assertEqual(rows['200']['status'], 'ok')
        self.assertEqual(rows['200']['actual_hours'], 33.75)   # sum of the matrix's daily hours
        self.assertEqual(rows['200']['hours_difference'], -11.25)  # 33.75 actual vs 45 booked
        self.assertEqual(rows['100']['office_days'], 0)   # only the matrix decides office presence
        self.assertEqual(rows['100']['pattern'], 'whole_period')
        self.assertEqual(result['summary']['attendance_source'], 'Monthly matrix (real-time attendance)')

    def test_attendance_outside_the_period_is_ignored(self):
        DailyAttendanceSummary.objects.create(employee_code='200', date=date(2026, 9, 1), effective_hours=9)
        rows = {r['employee_code']: r for r in compare_import(self.item)['rows']}
        self.assertEqual(rows['200']['present_days'], 0)


class ValueFrameApiTests(TestCase):
    def setUp(self):
        users = get_user_model().objects
        self.hr = users.create_superuser(username='vf-hr', email='vf-hr@example.test', password=None)
        self.staff = users.create_user(username='vf-staff', email='vf-staff@example.test')

    def call(self, view, user, method='get', data=None, **kwargs):
        factory = APIRequestFactory()
        request = factory.post('/', data or {}, format='multipart') if method == 'post' else getattr(factory, method)('/')
        force_authenticate(request, user)
        return view(request, **kwargs)

    def test_upload_compare_conflict_replace_and_access(self):
        upload = lambda: workbook([vf_row(22393, 'Aarekh', 45)])
        self.assertEqual(self.call(valueframe_views.valueframe_imports, self.staff, 'post', {'file': upload()}).status_code, 403)
        created = self.call(valueframe_views.valueframe_imports, self.hr, 'post', {'file': upload()})
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data['summary']['employees'], 1)
        again = self.call(valueframe_views.valueframe_imports, self.hr, 'post', {'file': upload()})
        self.assertEqual(again.status_code, 409)
        replaced = self.call(valueframe_views.valueframe_imports, self.hr, 'post', {'file': upload(), 'replace': 'true'})
        self.assertEqual(replaced.status_code, 201)
        self.assertEqual(ValueFrameImport.objects.count(), 1)
        pk = replaced.data['import']['id']
        self.assertEqual(self.call(valueframe_views.valueframe_comparison, self.hr, pk=pk).status_code, 200)
        self.assertEqual(self.call(valueframe_views.valueframe_comparison, self.staff, pk=pk).status_code, 403)
        self.assertEqual(self.call(valueframe_views.valueframe_import_detail, self.hr, 'delete', pk=pk).status_code, 204)
        self.assertFalse(ValueFrameEntry.objects.exists())

    def test_bad_file_is_a_clear_400(self):
        response = self.call(valueframe_views.valueframe_imports, self.hr, 'post', {'file': SimpleUploadedFile('x.xlsx', b'not excel')})
        self.assertEqual(response.status_code, 400)
        self.assertIn('Excel', response.data['detail'])
