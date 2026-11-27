"""ValueFrame → Payroll handoff: flagged gaps become pending 'absent' deductions.

    python manage.py test apps.payroll_engine.tests_valueframe_handoff --settings=config.settings_payroll_engine_test
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.payroll_engine import catalog
from apps.payroll_engine.models import PayrollAdjustment, PayrollEmployee
from apps.payroll_engine.test_support import active_profile, grant_payroll
from apps.timesheet.models import BiometricUserMaster, DailyAttendanceSummary, ValueFrameEntry, ValueFrameImport

BASE = '/api/v1/payroll-engine/valueframe/'


class ValueFrameHandoffTests(TestCase):
    def setUp(self):
        from unittest import mock
        matrix = mock.patch('apps.timesheet.valueframe._matrix_office_days', return_value=None)  # no live attendance source
        matrix.start()
        self.addCleanup(matrix.stop)
        users = get_user_model().objects
        self.payroll = users.create_user(username='vf-payroll', email='vf-payroll@example.test')
        grant_payroll(self.payroll, 'hr_manager', actions=('read', 'create', 'update'))  # an adjustment-writer role
        self.staff = users.create_user(username='vf-staff2', email='vf-staff2@example.test')
        active_profile(self.staff)
        self.item = ValueFrameImport.objects.create(period_start=date(2026, 8, 1), period_end=date(2026, 8, 31),
                                                    file_name='VF.xlsx', file_sha256='x', employee_count=3)
        # A: expat, gross 13,000 (9 h/day). Booked 5 days, present 1 -> 4 days x 500 = 2,000
        # B: basic only 8,000 (8 h/day). Booked 45 h = 5.625 d, present 0 -> 5.5 d (half-day steps)
        # C: flagged but no payroll record
        for code, name in (('A1', 'Alpha'), ('B1', 'Bravo'), ('C1', 'Charlie')):
            ValueFrameEntry.objects.create(vf_import=self.item, employee_code=code, employee_name=name, normal_hours=45)
            BiometricUserMaster.objects.create(employee_code=code, full_name=name)
        DailyAttendanceSummary.objects.create(employee_code='A1', date=date(2026, 8, 3), effective_hours=9)
        PayrollEmployee.objects.create(employee_no='A1', full_name='Alpha', basic=Decimal('9000'),
                                       housing=Decimal('3000'), transport=Decimal('1000'))
        PayrollEmployee.objects.create(employee_no='B1', full_name='Bravo', basic=Decimal('8000'))
        today = timezone.localdate()
        self.target = {'target_year': today.year, 'target_month': today.month}

    def api(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_proposal_prices_gaps_like_unpaid_days(self):
        response = self.api(self.payroll).get(f'{BASE}{self.item.pk}/proposal/')
        self.assertEqual(response.status_code, 200, getattr(response, 'data', None))
        lines = {l['employee_code']: l for l in response.data['lines']}
        self.assertEqual((lines['A1']['gap_days'], lines['A1']['daily_rate'], lines['A1']['amount']), ('4.0', '500.00', '2000.00'))
        self.assertEqual((lines['B1']['hours_per_day'], lines['B1']['gap_days'], lines['B1']['amount']), ('8.0', '5.5', '1692.31'))
        self.assertEqual(lines['C1']['status'], 'no_payroll_record')
        self.assertEqual(response.data['summary']['ready'], 2)
        self.assertEqual(response.data['component_code'], 'absent')

    def test_send_creates_pending_deductions_and_resend_refreshes_them(self):
        client = self.api(self.payroll)
        sent = client.post(f'{BASE}{self.item.pk}/send/', self.target, format='json')
        self.assertEqual(sent.status_code, 201, getattr(sent, 'data', None))
        self.assertEqual((sent.data['created'], sent.data['skipped']), (2, 1))
        adjustments = PayrollAdjustment.objects.filter(description__startswith=f'[ValueFrame #{self.item.pk}]')
        self.assertEqual(adjustments.count(), 2)
        self.assertTrue(all(a.status == catalog.AdjustmentStatus.PENDING and a.kind == catalog.LineItemKind.DEDUCTION
                            and a.component_code == 'absent' for a in adjustments))
        again = client.post(f'{BASE}{self.item.pk}/send/', self.target, format='json')
        self.assertEqual((again.status_code, again.data['created'], again.data['updated']), (200, 0, 2))
        self.assertEqual(adjustments.count(), 2)

    def test_send_only_selected_employees(self):
        response = self.api(self.payroll).post(f'{BASE}{self.item.pk}/send/', {**self.target, 'employee_codes': ['A1']}, format='json')
        self.assertEqual(response.data['created'], 1)
        self.assertEqual(PayrollAdjustment.objects.get().employee.employee_no, 'A1')

    def test_past_month_is_refused(self):
        response = self.api(self.payroll).post(f'{BASE}{self.item.pk}/send/', {'target_year': 2020, 'target_month': 1}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(PayrollAdjustment.objects.exists())

    def make_run(self, status):
        from apps.payroll_engine.models import PayrollRun
        return PayrollRun.objects.create(year=self.target['target_year'], month=self.target['target_month'],
                                         cycle_code=f"{self.target['target_year']}-{self.target['target_month']:02d}",
                                         status=status, employee_count=0, total_gross=0, total_net=0)

    def test_approved_run_month_is_refused(self):
        self.make_run(catalog.Status.HR_APPROVED)
        response = self.api(self.payroll).post(f'{BASE}{self.item.pk}/send/', self.target, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Choose the next payroll month', str(response.data))
        self.assertFalse(PayrollAdjustment.objects.exists())

    def test_existing_draft_run_is_reported_for_regeneration(self):
        run = self.make_run(catalog.Status.DRAFT)
        response = self.api(self.payroll).post(f'{BASE}{self.item.pk}/send/', self.target, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['draft_run_id'], run.pk)

    def test_send_is_refused_when_attendance_is_unavailable(self):
        DailyAttendanceSummary.objects.all().delete()  # no matrix (mocked None) and no stored rows
        client = self.api(self.payroll)
        self.assertFalse(client.get(f'{BASE}{self.item.pk}/proposal/').data['attendance_available'])
        response = client.post(f'{BASE}{self.item.pk}/send/', self.target, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('not available', str(response.data))
        self.assertFalse(PayrollAdjustment.objects.exists())

    def test_access(self):
        self.assertEqual(self.api(self.staff).get(f'{BASE}{self.item.pk}/proposal/').status_code, 403)
        self.assertEqual(self.api(self.staff).post(f'{BASE}{self.item.pk}/send/', self.target, format='json').status_code, 403)
        self.assertEqual(self.api(self.payroll).get(f'{BASE}999/proposal/').status_code, 404)
