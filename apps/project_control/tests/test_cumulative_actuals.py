from datetime import date
from decimal import Decimal

from django.test import TestCase

from apps.planning_intelligence.models import PlanningProject
from apps.planning_intelligence.schedule_models import Schedule, ScheduleControlSnapshot, ScheduleVersion

from ..models import ApprovedHourEntry, CostLedgerEntry, IntegratedReportingSnapshot, ReportingPeriod
from ..serializers import IntegratedReportingSnapshotSerializer
from ..services.actuals import create_integrated_snapshot, reconcile_reporting_period
from .test_actuals_and_snapshots import ActualsAndSnapshotTests


class CumulativeActualsTests(TestCase):
    def setUp(self):
        ActualsAndSnapshotTests.setUp(self)
        self.budget.amount = Decimal('100000')
        self.budget.save(update_fields=['amount', 'updated_at'])
        workspace = PlanningProject.objects.create(
            enterprise_project=self.project, name='Cost basis regression', created_by=self.owner,
        )
        schedule = Schedule.objects.create(
            project=workspace, name='Master', code='MASTER', status='active',
            planned_start=date(2026, 1, 1), data_date=date(2026, 2, 28), created_by=self.owner,
        )
        self.version = ScheduleVersion.objects.create(
            schedule=schedule, version=1, status='approved', created_by=self.owner,
        )

    def hour(self, period, amount, reference, *, work_date=None):
        return ApprovedHourEntry.objects.create(
            project=self.project, control_account=self.account, reporting_period=period,
            employee_code='COST-01', work_date=work_date or period.data_date,
            hours=Decimal('100'), hourly_cost_rate=Decimal(amount) / 100,
            labor_actual_cost=Decimal(amount), currency='AED', source_reference=reference,
            status='approved', approved_by=self.approver,
        )

    def adjustment(self, period, amount, key, *, entry_date=None, currency='AED', status='posted'):
        return CostLedgerEntry.objects.create(
            project=self.project, control_account=self.account, reporting_period=period,
            entry_key=key, entry_type='adjustment', amount=Decimal(amount), currency=currency,
            source_type='manual_adjustment', source_reference=key,
            entry_date=entry_date or period.data_date, status=status,
        )

    def seal(self, period, progress):
        previous = ScheduleControlSnapshot.objects.filter(version=self.version, data_date=period.data_date).count()
        ScheduleControlSnapshot.objects.create(
            version=self.version, data_date=period.data_date, revision=previous + 1,
            progress_pct=Decimal(progress), planned_progress_pct=Decimal(progress),
            captured_by=self.approver,
        )
        run = reconcile_reporting_period(period, user=self.approver)
        self.assertEqual(run.status, 'completed', run.exceptions)
        period.status = 'submitted'
        period.save(update_fields=['status', 'updated_at'])
        result = create_integrated_snapshot(period, user=self.approver)
        period.status = 'locked'
        period.save(update_fields=['status', 'updated_at'])
        return result

    def february(self):
        return ReportingPeriod.objects.create(
            project=self.project, sequence=2, name='February', start_date=date(2026, 2, 1),
            end_date=date(2026, 2, 28), data_date=date(2026, 2, 28), status='open', created_by=self.owner,
        )

    def test_two_period_cpi_uses_cumulative_cost_and_eac_uses_unrounded_ratio(self):
        self.hour(self.period, '20000', 'JAN')
        january = self.seal(self.period, '20')
        february = self.february()
        self.hour(february, '10000', 'FEB')
        snapshot = self.seal(february, '50')

        self.assertEqual(snapshot.earned_value, Decimal('50000'))
        self.assertEqual(snapshot.actual_cost, Decimal('30000'))
        self.assertEqual(snapshot.cpi, Decimal('1.6667'))
        self.assertEqual(snapshot.estimate_at_completion, Decimal('60000.00'))
        self.assertEqual(snapshot.estimate_to_complete, Decimal('30000.00'))
        self.assertEqual(snapshot.variance_at_completion, Decimal('40000.00'))
        self.assertEqual(snapshot.cost_variance, Decimal('20000.00'))
        data = IntegratedReportingSnapshotSerializer(snapshot).data
        self.assertEqual(data['actual_cost_basis'], 'cumulative_to_data_date')
        self.assertEqual(data['period_totals']['ledger_actual_cost'], '10000.00')
        self.assertEqual(data['cumulative_totals']['ledger_actual_cost'], '30000.00')
        self.assertEqual(data['cumulative_totals']['approved_hours'], '200.00')
        self.assertEqual(len(snapshot.source_manifest['actual_cost_observation']['entries']), 2)
        self.assertEqual(snapshot.calculation_payload['values']['AC'], '30000.00')
        january.refresh_from_db()
        self.assertEqual(january.actual_cost, Decimal('20000'))
        self.assertEqual(january.cpi, Decimal('1'))

    def test_reopened_prior_period_reversal_and_backdated_correction_preserve_sealed_history(self):
        original_hour = self.hour(self.period, '20000', 'JAN')
        january = self.seal(self.period, '20')
        february = self.february()
        self.hour(february, '10000', 'FEB')
        old_february = self.seal(february, '50')
        old_checksum = old_february.checksum
        old_sources = old_february.source_manifest

        self.period.status = 'reopened'
        self.period.save(update_fields=['status', 'updated_at'])
        original_hour.status = 'reversed'
        original_hour.save(update_fields=['status', 'updated_at'])
        self.hour(self.period, '15000', 'JAN-CORRECTION', work_date=date(2026, 1, 25))
        corrected_january = self.seal(self.period, '20')
        february.status = 'reopened'
        february.save(update_fields=['status', 'updated_at'])
        corrected_february = self.seal(february, '50')

        self.assertEqual(corrected_january.version, 2)
        self.assertEqual(corrected_february.version, 2)
        self.assertEqual(corrected_february.actual_cost, Decimal('25000'))
        self.assertEqual(corrected_february.cpi, Decimal('2'))
        old_february.refresh_from_db()
        january.refresh_from_db()
        self.assertEqual(old_february.checksum, old_checksum)
        self.assertEqual(old_february.source_manifest, old_sources)
        self.assertEqual(old_february.actual_cost, Decimal('30000'))
        self.assertEqual(january.actual_cost, Decimal('20000'))

    def test_data_date_excludes_future_and_reversed_cost_and_includes_adjustments(self):
        self.period.data_date = date(2026, 1, 15)
        self.period.save(update_fields=['data_date', 'updated_at'])
        self.hour(self.period, '20000', 'CURRENT', work_date=date(2026, 1, 10))
        self.hour(self.period, '40000', 'FUTURE-HOURS', work_date=date(2026, 1, 20))
        self.adjustment(self.period, '1000', 'ADJUSTMENT', entry_date=date(2026, 1, 12))
        self.adjustment(self.period, '3000', 'FUTURE', entry_date=date(2026, 1, 20))
        self.adjustment(self.period, '5000', 'REVERSED', status='reversed')
        result = self.seal(self.period, '30')
        self.assertEqual(result.actual_cost, Decimal('21000'))
        self.assertEqual(result.labor_actual_cost, Decimal('20000'))
        self.assertEqual(result.finance_actual_cost, Decimal('1000'))
        self.assertEqual(result.calculation_payload['period_totals']['ledger_actual_cost'], '21000.00')
        self.assertEqual(CostLedgerEntry.objects.filter(source_reference='FUTURE-HOURS').count(), 0)

    def test_backdated_change_after_submission_requires_reconciliation_again(self):
        self.hour(self.period, '20000', 'JAN')
        reconcile_reporting_period(self.period, user=self.approver)
        self.period.status = 'submitted'
        self.period.save(update_fields=['status', 'updated_at'])
        self.adjustment(self.period, '1000', 'LATE-POST', entry_date=date(2026, 1, 15))
        with self.assertRaisesMessage(ValueError, 'Reopen the submitted period'):
            create_integrated_snapshot(self.period, user=self.approver)
        self.assertEqual(IntegratedReportingSnapshot.objects.count(), 0)
        self.period.status = 'reopened'
        self.period.save(update_fields=['status', 'updated_at'])
        self.assertEqual(self.seal(self.period, '30').actual_cost, Decimal('21000'))

    def test_prior_period_currency_exception_blocks_current_reconciliation(self):
        self.hour(self.period, '20000', 'JAN')
        self.seal(self.period, '20')
        self.adjustment(self.period, '1000', 'WRONG-CURRENCY', currency='USD')
        february = self.february()
        run = reconcile_reporting_period(february, user=self.approver)
        self.assertEqual(run.status, 'exceptions')
        self.assertIn('currency_mismatch', {row['type'] for row in run.exceptions})

    def test_missing_cumulative_account_mapping_is_not_silently_ignored(self):
        self.hour(self.period, '20000', 'JAN')
        self.seal(self.period, '20')
        row = self.adjustment(self.period, '1000', 'UNMAPPED-PAST')
        row.control_account = None
        row.save(update_fields=['control_account', 'updated_at'])
        run = reconcile_reporting_period(self.february(), user=self.approver)
        self.assertEqual(run.status, 'exceptions')
        self.assertIn('unmapped_control_account', {row['type'] for row in run.exceptions})

    def test_zero_actual_does_not_create_a_cost_index_or_forecast(self):
        snapshot = self.seal(self.period, '0')
        self.assertEqual(snapshot.actual_cost, Decimal('0'))
        self.assertIsNone(snapshot.cpi)
        self.assertIsNone(snapshot.estimate_at_completion)
        self.assertIsNone(snapshot.estimate_to_complete)

    def test_legacy_snapshot_is_identified_without_rewriting_history(self):
        run = reconcile_reporting_period(self.period, user=self.approver)
        snapshot = IntegratedReportingSnapshot.objects.create(
            project=self.project, reporting_period=self.period, reconciliation_run=run,
            version=1, data_date=self.period.data_date, actual_cost=Decimal('10000'),
            cpi=Decimal('5'), checksum='unchanged-legacy-checksum', calculation_payload={},
        )
        data = IntegratedReportingSnapshotSerializer(snapshot).data
        self.assertEqual(data['actual_cost_basis'], 'legacy_period')
        self.assertEqual(Decimal(data['period_totals']['ledger_actual_cost']), Decimal('10000'))
        self.assertIsNone(data['cumulative_totals'])
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.cpi, Decimal('5'))
        self.assertEqual(snapshot.checksum, 'unchanged-legacy-checksum')
