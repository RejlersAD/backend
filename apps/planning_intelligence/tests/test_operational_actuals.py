"""Operational actuals read approved work without guessing attribution or rates."""
from datetime import date
from decimal import Decimal
import json
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.core.project_models import Project
from apps.project_control.models import ApprovedHourEntry, ControlAccount, CostLedgerEntry, ReportingPeriod, WBSNode
from apps.project_control.epc_models import WBSActivityLink
from apps.timesheet.models import DailyAttendanceSummary
from apps.users.models import User
from ..models import PlanningProject, Schedule, ScheduleActivity, ScheduleBaseline, ScheduleVersion
from ..services.operational_actuals import read_operational_actuals, can_view_commercial_actuals


class OperationalActualsTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='actuals-owner', email='actuals-owner@example.test')
        self.reviewer = User.objects.create_user(username='actuals-reviewer', email='actuals-reviewer@example.test')
        self.outsider = User.objects.create_user(username='actuals-outsider', email='actuals-outsider@example.test')
        self.enterprise, self.wbs, self.account, self.period = self.scope('OPS-1')
        self.project = PlanningProject.objects.create(name='Operational evidence', enterprise_project=self.enterprise, created_by=self.owner)
        self.schedule = Schedule.objects.create(project=self.project, code='S-1', name='Control baseline', planned_start=date(2026, 11, 2))
        self.version = ScheduleVersion.objects.create(schedule=self.schedule, version=1, status='baselined')
        self.activities = [ScheduleActivity.objects.create(version=self.version, external_id=f'ACT-{index}', name=f'Package {index}', duration_days=3)
                           for index in (1, 2)]
        self.baseline = ScheduleBaseline.objects.create(schedule=self.schedule, source_version=self.version,
            name='Approved reference', snapshot={'activities': [{'id': row.pk, 'external_id': row.external_id} for row in self.activities]},
            approved_by=self.reviewer, approved_at=timezone.now())
        self.cutoff = date(2026, 11, 15)

    def scope(self, code):
        project = Project.objects.create(code=code, name=code, owner=self.owner, currency='AED')
        node = WBSNode.objects.create(project=project, code='1', name='Explicit control scope')
        account = ControlAccount.objects.create(project=project, wbs_node=node, code='CA-1', name='Delivery', manager=self.owner,
            baseline_start=date(2026, 11, 2), baseline_finish=date(2026, 11, 30), status='active',
            approved_by=self.reviewer, approved_at=timezone.now())
        period = ReportingPeriod.objects.create(project=project, sequence=1, name='November', start_date=date(2026, 11, 1),
            end_date=date(2026, 11, 30), data_date=date(2026, 11, 15))
        return project, node, account, period

    def hour(self, reference='H-1', **changes):
        values = dict(project=self.enterprise, control_account=self.account, reporting_period=self.period,
            employee_code='EMP-1', work_date=date(2026, 11, 10), hours=Decimal('6'), hourly_cost_rate=Decimal('10'),
            labor_actual_cost=Decimal('60'), currency='AED', source_type='manual', source_reference=reference,
            status='approved', submitted_by=self.owner, approved_by=self.reviewer, approved_at=timezone.now())
        return ApprovedHourEntry.objects.create(**(values | changes))

    def cost(self, key='C-1', **changes):
        values = dict(project=self.enterprise, control_account=self.account, reporting_period=self.period, wbs_node=self.wbs,
            entry_key=key, entry_type='actual', entry_date=date(2026, 11, 10), amount=Decimal('100'), currency='AED',
            source_type='invoice_allocation', source_id='INVOICE-ALLOCATION-1', status='posted', created_by=self.reviewer)
        return CostLedgerEntry.objects.create(**(values | changes))

    def read(self, **changes):
        return read_operational_actuals(changes.get('project', self.project), changes.get('baseline', self.baseline),
            changes.get('data_date', self.cutoff), actor=changes.get('actor', self.owner))

    def test_approved_hours_and_posted_costs_are_read_without_writes_or_double_counting_labour(self):
        hour = self.hour()
        self.cost('LABOUR', amount=Decimal('60'), source_type='approved_hour', source_id=str(hour.pk))
        self.cost('FINANCE', amount=Decimal('40'))
        DailyAttendanceSummary.objects.create(employee_code='EMP-1', date=date(2026, 11, 10), effective_hours=99)
        with patch('apps.project_control.services.actuals.reconcile_reporting_period', side_effect=AssertionError('Read must not reconcile')):
            with CaptureQueriesContext(connection) as queries:
                result = self.read()
        self.assertFalse([row['sql'] for row in queries if row['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))])
        self.assertEqual(Decimal(result['total_hours']), Decimal('6'))
        self.assertEqual(result['costs_by_currency'], {'AED': '100.00'})
        self.assertEqual(len(result['hours']), 1)
        self.assertEqual(len(result['costs']), 2)
        self.assertEqual(result['hours'][0]['approved_by_id'], self.reviewer.pk)
        self.assertEqual(result['manifest']['cutoff_basis'], 'effective_work_or_entry_date')
        json.dumps(result)  # No Decimal, datetime or UUID leaks across JSON boundaries.

    def test_cutoff_approval_reversal_and_foreign_project_filters_are_visible(self):
        accepted = self.hour()
        self.hour('PENDING', status='submitted', approved_by=None, approved_at=None)
        self.hour('NO-EVIDENCE', approved_at=None)
        self.hour('FUTURE', work_date=date(2026, 11, 17))
        self.hour('REVERSED', status='reversed')
        self.hour('DELETED', is_deleted=True)
        foreign, foreign_wbs, foreign_account, foreign_period = self.scope('OPS-2')
        self.hour('FOREIGN', project=foreign, control_account=foreign_account, reporting_period=foreign_period)
        self.hour('BAD-LINK', control_account=foreign_account)
        self.cost('FOREIGN', project=foreign, wbs_node=foreign_wbs, control_account=foreign_account, reporting_period=foreign_period)
        self.cost('FUTURE', entry_date=date(2026, 11, 17))
        self.cost('REVERSED', status='reversed')
        self.cost('COMMITMENT', entry_type='commitment')
        result = self.read()
        self.assertEqual([row['id'] for row in result['hours']], [accepted.pk])
        self.assertEqual(result['costs'], [])
        self.assertEqual(result['coverage']['hours']['submitted'], 1)
        self.assertEqual(result['coverage']['hours']['future'], 1)
        self.assertEqual(result['coverage']['hours']['missing_approval'], 1)
        self.assertEqual(result['coverage']['hours']['foreign_control_account'], 1)
        self.assertNotIn('FOREIGN', {row['source_reference'] for row in result['manifest']['hours']})
        self.assertEqual(result['costs_by_currency'], {})

    def test_exact_baseline_bridges_never_allocate_shared_account_actuals(self):
        for row in self.activities:
            WBSActivityLink.objects.create(project=self.enterprise, wbs_node=self.wbs, activity=row, link_type='engineering')
        outside = ScheduleActivity.objects.create(version=self.version, external_id='OUTSIDE', name='Not frozen', duration_days=2)
        WBSActivityLink.objects.create(project=self.enterprise, wbs_node=self.wbs, activity=outside, link_type='engineering')
        self.hour()
        self.cost()
        result = self.read()
        self.assertEqual(result['costs'][0]['account_activity_ids'], [row.pk for row in self.activities])
        self.assertEqual(result['costs'][0]['activity_allocation_status'], 'not_allocated')
        self.assertNotIn('activity_amounts', result['costs'][0])
        self.assertEqual(result['costs_by_currency'], {'AED': '100.00'})
        self.assertEqual(len(result['manifest']['activity_bridges']), 2)

    def test_missing_actuals_rates_and_mixed_currencies_remain_explicit(self):
        empty = self.read()
        self.assertIsNone(empty['total_hours'])
        self.assertEqual(empty['costs_by_currency'], {})
        self.hour(hourly_cost_rate=Decimal('0'), labor_actual_cost=Decimal('0'))
        self.cost('AED', amount=Decimal('80'))
        self.cost('USD', amount=Decimal('20'), currency='USD')
        self.cost('NO-CURRENCY', amount=Decimal('8'), currency='')
        result = self.read()
        self.assertEqual(result['costs_by_currency'], {'AED': '80.00', 'USD': '20.00'})
        self.assertEqual(result['hours'][0]['rate_status'], 'zero_rate_requires_confirmation')
        self.assertTrue({'labor_rate_unconfirmed', 'multiple_cost_currencies', 'cost_currency_missing', 'approved_hours_not_posted'}
                        <= {row['code'] for row in result['issues']})
        self.assertNotIn('total_cost', result)

    def test_fingerprint_tracks_source_values_status_and_explicit_links(self):
        entry = self.cost()
        first = self.read()
        self.assertEqual(first['fingerprint'], self.read()['fingerprint'])
        CostLedgerEntry.objects.filter(pk=entry.pk).update(amount=Decimal('101'))
        changed = self.read()
        self.assertNotEqual(first['fingerprint'], changed['fingerprint'])
        WBSActivityLink.objects.create(project=self.enterprise, wbs_node=self.wbs, activity=self.activities[0], link_type='engineering')
        linked = self.read()
        self.assertNotEqual(changed['fingerprint'], linked['fingerprint'])
        CostLedgerEntry.objects.filter(pk=entry.pk).update(status='reversed')
        reversed_result = self.read()
        self.assertNotEqual(linked['fingerprint'], reversed_result['fingerprint'])
        self.assertEqual(reversed_result['costs'], [])

    def test_stale_posted_labour_does_not_override_reversed_hour_approval(self):
        hour = self.hour(status='reversed')
        self.cost(source_type='approved_hour', source_id=str(hour.pk), amount=Decimal('60'))
        result = self.read()
        self.assertEqual(result['costs'], [])
        self.assertIsNone(result['total_hours'])
        self.assertIn('cost_labor_source_not_approved', {row['code'] for row in result['issues']})

    def test_split_ledger_rows_share_source_without_being_dropped_or_counted_twice(self):
        self.cost('SPLIT-1', amount=Decimal('80'))
        self.cost('SPLIT-2', amount=Decimal('20'))
        result = self.read()
        self.assertEqual(len(result['costs']), 2)
        self.assertEqual(len({row['source_id'] for row in result['costs']}), 1)
        self.assertEqual(result['costs_by_currency'], {'AED': '100.00'})
        self.assertEqual(result['fingerprint'], self.read()['fingerprint'])

    def test_access_and_baseline_identity_are_checked_without_commercial_permission_escalation(self):
        with self.assertRaises(NotFound):
            self.read(actor=self.outsider)
        self.assertFalse(can_view_commercial_actuals(self.owner, self.project))
        self.owner.is_staff = True
        self.owner.save(update_fields=['is_staff'])
        self.assertTrue(can_view_commercial_actuals(self.owner, self.project))
        foreign = PlanningProject.objects.create(name='Foreign', created_by=self.outsider)
        with self.assertRaises(ValidationError):
            self.read(project=foreign, actor=None)
        self.assertIsNone(self.read(actor=None)['total_hours'])
