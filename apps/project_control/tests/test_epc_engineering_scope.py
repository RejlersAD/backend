"""Detailed Engineering owns Engineering metrics while retaining EPC dependencies."""
from datetime import date
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from queue import Queue
from time import monotonic, sleep

from django.db import close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.core.project_models import Project
from apps.core.project_serializers import ProjectSerializer
from apps.planning_intelligence.models import ActivityAssignment, ActivityProgressUpdate, ScheduleActivity, ScheduleControlSnapshot, ScheduleResource, ScheduleVersion
from apps.planning_intelligence.services.project_controls import build_control_dashboard, capture_control_snapshot
from ..epc_models import IntegratedBaseline, WBSActivityLink
from ..models import CostLedgerEntry, IntegratedReportingSnapshot, ReconciliationRun, ReportingPeriod
from ..services.actuals import create_integrated_snapshot, reconcile_reporting_period
from ..services.epc import capture_integrated_baseline, save_activity_link, setup_epc_project
from . import test_epc_foundation as foundation


class EngineeringScopeTests(TestCase):
    setup_foundation = foundation.EpcFoundationTests.setup_foundation
    approved_sources = foundation.EpcFoundationTests.approved_sources
    api = foundation.EpcFoundationTests.api

    def setUp(self):
        foundation.EpcFoundationTests.setUp(self)
        self.values['scope_type'] = 'detailed_engineering'
        self.approved_sources()
        self.baseline_values['budget_ids'] = [self.budgets[0].pk]
        self.data_date = date(2026, 1, 5)
        resource = ScheduleResource.objects.create(project=self.version.schedule.project,
            code='CREW', name='Recorded resources', resource_type='labor')
        for index, activity in enumerate(self.activities):
            activity.duration_days = 10 if index == 0 else 100
            activity.planned_finish = date(2026, 1, 10) if index == 0 else date(2026, 12, 31)
            activity.save(update_fields=['duration_days', 'planned_finish', 'updated_at'])
            ActivityAssignment.objects.create(activity=activity, resource=resource,
                budgeted_cost=100000 if index == 0 else 900000, budgeted_hours=100 if index == 0 else 900)
            ActivityProgressUpdate.objects.create(version=self.version, activity=activity,
                data_date=self.data_date, physical_progress_pct=50 if index == 0 else 100,
                actual_cost=2000 if index == 0 else 10000, actual_hours=10 if index == 0 else 200,
                reported_by=self.owner)

    def seal(self):
        return capture_integrated_baseline(self.project, self.baseline_values, user=self.authority)

    def test_setup_api_exposes_owned_scope_and_keeps_all_four_roots(self):
        response = self.api('setup', 'post', self.values)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['ready'])
        self.assertEqual(response.data['control_scope']['owned_phases'], ['engineering'])
        self.assertEqual(response.data['control_scope']['dependency_phases'], ['procurement', 'construction', 'commissioning'])
        self.assertEqual({row['value'] for row in response.data['scope_options']}, {'epc', 'detailed_engineering'})
        self.assertEqual(len(response.data['wbs']), 4)
        links = self.api('links').data['results']
        self.assertEqual([row['control_role'] for row in links], ['owned', 'dependency', 'dependency', 'dependency'])
        choices = self.api('baseline').data
        self.assertEqual([row['id'] for row in choices['budgets']], [self.budgets[0].pk])
        self.assertEqual(choices['excluded_dependency_budget_count'], 3)

    def test_baseline_freezes_dependencies_without_earning_their_budget(self):
        baseline = self.seal()
        self.assertEqual(baseline.budget_total, Decimal('25000'))
        self.assertEqual(baseline.manifest['owned_activity_ids'], [self.activities[0].pk])
        self.assertEqual(baseline.manifest['dependency_activity_ids'], [row.pk for row in self.activities[1:]])
        self.assertEqual(len(baseline.manifest['activity_links']), 4)
        self.assertEqual(len(baseline.manifest['control_accounts']), 1)
        self.assertEqual([row['control_role'] for row in baseline.manifest['activity_links']], ['owned', 'dependency', 'dependency', 'dependency'])

    def test_dependency_budget_cannot_be_added_to_owned_baseline(self):
        with self.assertRaisesMessage(ValidationError, 'only the owned Engineering WBS'):
            capture_integrated_baseline(self.project,
                {**self.baseline_values, 'budget_ids': [row.pk for row in self.budgets]}, user=self.authority)
        self.assertFalse(IntegratedBaseline.objects.exists())

    def test_dependency_cannot_be_disguised_with_engineering_link_label(self):
        link = WBSActivityLink.objects.get(activity=self.activities[1])
        with self.assertRaisesMessage(ValidationError, 'phase must match'):
            save_activity_link(self.project, {'id': link.pk, 'activity': link.activity_id,
                'wbs_node': link.wbs_node_id, 'link_type': 'engineering'}, user=self.owner)
        # Legacy invalid labels are also rejected by capture, not merely forms.
        WBSActivityLink.objects.filter(pk=link.pk).update(link_type='engineering')
        with self.assertRaisesMessage(ValidationError, 'phase must match'):
            self.seal()

    def test_every_external_activity_still_requires_explicit_mapping(self):
        WBSActivityLink.objects.filter(activity=self.activities[-1]).update(is_deleted=True)
        with self.assertRaisesMessage(ValidationError, 'Map every saved schedule baseline activity'):
            self.seal()

    def test_owned_progress_plan_cost_and_hours_exclude_all_dependencies(self):
        dashboard = build_control_dashboard(self.version, self.data_date)
        self.assertTrue(dashboard['control_scope']['ready'])
        self.assertEqual(dashboard['control_scope']['source'], 'activity_links')
        self.assertEqual(dashboard['progress_pct'], Decimal('50'))
        self.assertEqual(dashboard['planned_progress_pct'], Decimal('50'))
        self.assertEqual(dashboard['bac'], Decimal('100000'))
        self.assertEqual(dashboard['earned_value'], Decimal('50000'))
        self.assertEqual(dashboard['planned_value'], Decimal('50000'))
        self.assertEqual(dashboard['actual_cost'], Decimal('2000'))
        self.assertEqual(dashboard['actual_hours'], Decimal('10'))
        self.assertEqual(dashboard['forecast_finish'], date(2026, 1, 10))
        self.assertEqual(len(dashboard['activities']), 4)
        self.assertEqual(sum(row['activity_count'] for row in dashboard['wbs_breakdown']), 1)
        self.assertEqual({row['id']: row['physical_progress_pct'] for row in dashboard['activities']},
                         {row.pk: Decimal('50') if index == 0 else Decimal('100')
                          for index, row in enumerate(self.activities)})
        point = next(row for row in dashboard['curve'] if row['date'] == self.data_date)
        self.assertEqual(point['progress_pct'], Decimal('50'))
        self.assertEqual(point['planned_progress_pct'], Decimal('50'))

    def test_completed_dependencies_cannot_earn_unreported_engineering_progress(self):
        ActivityProgressUpdate.objects.filter(activity=self.activities[0]).delete()
        dashboard = build_control_dashboard(self.version, self.data_date)
        self.assertEqual(dashboard['progress_pct'], Decimal('0'))
        self.assertEqual(dashboard['earned_value'], Decimal('0'))
        self.assertEqual(dashboard['actual_cost'], Decimal('0'))

    def test_external_progress_api_remains_trackable_without_earning_owned_progress(self):
        client = APIClient()
        client.force_authenticate(self.authority)
        response = client.post(f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/progress/', {
            'data_date': self.data_date.isoformat(),
            'updates': [{'activity': self.activities[1].pk, 'physical_progress_pct': '40'}],
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Decimal(response.data['updates'][0]['physical_progress_pct']), Decimal('40'))
        self.assertEqual(response.data['controls']['progress_pct'], Decimal('50'))
        dependency = next(row for row in response.data['controls']['activities'] if row['id'] == self.activities[1].pk)
        self.assertEqual(dependency['control_role'], 'dependency')
        self.assertEqual(dependency['physical_progress_pct'], Decimal('40'))

    def test_missing_mapping_makes_aggregate_unavailable_and_cannot_publish(self):
        WBSActivityLink.objects.filter(activity=self.activities[-1]).update(is_deleted=True)
        dashboard = build_control_dashboard(self.version, self.data_date)
        self.assertFalse(dashboard['control_scope']['ready'])
        self.assertIsNone(dashboard['progress_pct'])
        self.assertIsNone(dashboard['planned_value'])
        self.assertEqual(dashboard['curve'], [])
        self.assertEqual(len(dashboard['activities']), 4)
        with self.assertRaisesMessage(ValidationError, 'Map every activity'):
            capture_control_snapshot(self.version, self.data_date, self.owner)
        self.assertFalse(ScheduleControlSnapshot.objects.exists())

    def test_frozen_membership_survives_live_mapping_changes_and_preserves_history(self):
        baseline = self.seal()
        first = capture_control_snapshot(self.version, self.data_date, self.owner)
        link = WBSActivityLink.objects.get(activity=self.activities[1])
        save_activity_link(self.project, {'id': link.pk, 'activity': link.activity_id,
            'wbs_node': self.budgets[0].wbs_node_id, 'link_type': 'engineering'}, user=self.owner)
        second = capture_control_snapshot(self.version, self.data_date, self.owner)
        for observation in (first, second):
            self.assertEqual(observation.progress_pct, Decimal('50'))
            self.assertEqual(observation.payload['control_scope']['source'], 'integrated_baseline')
            self.assertEqual(observation.payload['control_scope']['integrated_baseline_id'], baseline.pk)
            self.assertEqual(observation.payload['control_scope']['owned_activity_ids'], [self.activities[0].pk])
        self.assertEqual((first.revision, second.revision), (1, 2))
        first.refresh_from_db()
        self.assertEqual(first.progress_pct, Decimal('50'))

    def test_changed_activity_set_requires_new_reviewed_scope_before_publish(self):
        self.seal()
        ScheduleActivity.objects.create(version=self.version, external_id='NEW-UNMAPPED', name='New dependency')
        dashboard = build_control_dashboard(self.version, self.data_date)
        self.assertFalse(dashboard['control_scope']['ready'])
        with self.assertRaisesMessage(ValidationError, 'complete Engineering ownership evidence'):
            capture_control_snapshot(self.version, self.data_date, self.owner)

    def test_full_epc_still_counts_all_four_phases(self):
        self.project = setup_epc_project(self.project, {**self.values, 'scope_type': 'epc'}, user=self.authority)
        dashboard = build_control_dashboard(self.version, self.data_date)
        self.assertEqual(dashboard['progress_pct'], Decimal('98.39'))
        self.assertEqual(dashboard['bac'], Decimal('2800000'))
        self.assertEqual(len(dashboard['control_scope']['owned_activity_ids']), 4)
        self.assertEqual(dashboard['control_scope']['dependency_activity_ids'], [])

    def test_setup_cannot_reinterpret_an_existing_approved_scope(self):
        self.seal()
        with self.assertRaisesMessage(ValidationError, 'Delivery scope cannot change'):
            setup_epc_project(self.project, {**self.values, 'scope_type': 'epc'}, user=self.authority)
        self.project.refresh_from_db()
        self.assertEqual(self.project.scope_type, 'detailed_engineering')

    def assert_scope_frozen_metadata_editable(self):
        self.assertEqual(self.api('setup', 'post', {**self.values, 'name': 'Updated descriptive name'}).status_code, 200)
        with self.assertRaisesMessage(ValidationError, 'Delivery scope cannot change'):
            setup_epc_project(self.project, {**self.values, 'scope_type': 'epc'}, user=self.authority)
        metadata = ProjectSerializer(self.project, data={'description': 'Updated project description'}, partial=True)
        self.assertTrue(metadata.is_valid(), metadata.errors)
        metadata.save()
        reclassification = ProjectSerializer(self.project, data={'scope_type': 'epc'}, partial=True)
        self.assertFalse(reclassification.is_valid())
        self.assertIn('scope_type', reclassification.errors)
        self.project.refresh_from_db()
        self.assertEqual(self.project.scope_type, 'detailed_engineering')
        self.assertEqual(self.project.description, 'Updated project description')

    def test_schedule_observation_freezes_scope_before_any_integrated_baseline(self):
        capture_control_snapshot(self.version, self.data_date, self.owner)
        self.assertFalse(IntegratedBaseline.objects.exists())
        self.assert_scope_frozen_metadata_editable()

    def test_legacy_commercial_observation_freezes_scope_before_any_epc_work(self):
        period = ReportingPeriod.objects.create(project=self.project, sequence=1, name='Legacy published period',
            start_date=date(2026, 1, 1), end_date=self.data_date, data_date=self.data_date, status='locked')
        reconciliation = ReconciliationRun.objects.create(project=self.project, reporting_period=period,
            run_number=1, status='completed', checksum='0' * 64)
        IntegratedReportingSnapshot.objects.create(project=self.project, reporting_period=period,
            reconciliation_run=reconciliation, version=1, data_date=self.data_date, checksum='1' * 64)
        self.assertFalse(IntegratedBaseline.objects.exists())
        self.assertFalse(ScheduleControlSnapshot.objects.exists())
        self.assert_scope_frozen_metadata_editable()

    def owned_reporting_period(self):
        self.seal()
        capture_control_snapshot(self.version, self.data_date, self.authority)
        period = ReportingPeriod.objects.create(project=self.project, sequence=1, name='Engineering control period',
            start_date=date(2026, 1, 1), end_date=self.data_date, data_date=self.data_date)
        reconciliation = reconcile_reporting_period(period, user=self.authority)
        self.assertEqual(reconciliation.status, 'completed', reconciliation.exceptions)
        period.status = 'submitted'
        period.save(update_fields=['status', 'updated_at'])
        owned = CostLedgerEntry.objects.create(project=self.project, wbs_node_id=self.budgets[0].wbs_node_id,
            entry_key='owned-commitment', entry_type='commitment', amount=250, currency='AED',
            source_type='purchase_order', source_id='isolated-owned-order', entry_date=self.data_date)
        return period, owned

    def test_owned_engineering_commitment_can_be_sealed(self):
        period, owned = self.owned_reporting_period()
        snapshot = create_integrated_snapshot(period, user=self.authority)
        self.assertEqual(snapshot.commitments, Decimal('250'))
        self.assertEqual(snapshot.progress_pct, Decimal('50'))
        owned.refresh_from_db()
        self.assertEqual(owned.status, 'posted')

    def test_external_or_unmapped_commitment_blocks_sealing_without_rewriting_source(self):
        period, owned = self.owned_reporting_period()
        external = CostLedgerEntry.objects.create(project=self.project, wbs_node_id=self.budgets[1].wbs_node_id,
            entry_key='external-commitment', entry_type='commitment', amount=700, currency='AED',
            source_type='purchase_order', source_id='isolated-external-order', entry_date=self.data_date)
        for node_id in (self.budgets[1].wbs_node_id, None):
            with self.subTest(wbs_node=node_id):
                external.wbs_node_id = node_id
                external.save(update_fields=['wbs_node', 'updated_at'])
                with self.assertRaisesMessage(ValueError, 'Map posted commitments to owned Engineering WBS'):
                    create_integrated_snapshot(period, user=self.authority)
                self.assertFalse(IntegratedReportingSnapshot.objects.exists())
                self.assertEqual(list(CostLedgerEntry.objects.filter(pk__in=[owned.pk, external.pk])
                    .order_by('amount').values_list('amount', 'status')), [(Decimal('250'), 'posted'), (Decimal('700'), 'posted')])


class EngineeringScopeConcurrencyTests(TransactionTestCase):
    setUp = foundation.EpcFoundationTests.setUp
    setup_foundation = foundation.EpcFoundationTests.setup_foundation
    approved_sources = foundation.EpcFoundationTests.approved_sources

    def race_publication_and_scope(self, publish):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL lock-observation regression.')
        created, release, changing = Event(), Event(), Event()
        changing_backend = Queue()

        def capture():
            close_old_connections()
            try:
                with transaction.atomic():
                    snapshot = publish()
                    created.set()
                    if not release.wait(timeout=5):
                        raise RuntimeError('The isolated concurrency test did not release its capture.')
                    return snapshot.pk
            finally:
                close_old_connections()

        def change_scope():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    changing_backend.put(cursor.fetchone()[0])
                changing.set()
                try:
                    setup_epc_project(Project.objects.get(pk=self.project.pk),
                        {**self.values, 'scope_type': 'detailed_engineering'}, user=self.authority)
                except ValidationError as exc:
                    return str(exc)
                return 'incorrectly changed scope'
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            capture_result = pool.submit(capture)
            try:
                self.assertTrue(created.wait(timeout=5))
                change_result = pool.submit(change_scope)
                self.assertTrue(changing.wait(timeout=5))
                backend_pid = changing_backend.get(timeout=5)
                blocked = False
                deadline = monotonic() + 3
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s', [backend_pid])
                        waiting = cursor.fetchone()
                    if waiting and waiting[0] == 'Lock':
                        blocked = True
                        break
                    if change_result.done():
                        break
                    sleep(0.01)
            finally:
                release.set()
            captured_id = capture_result.result(timeout=10)
            result = change_result.result(timeout=10)
        self.assertTrue(blocked, 'Scope change must await the in-flight first publication lock.')
        self.assertIn('Delivery scope cannot change', result)
        self.project.refresh_from_db()
        self.assertEqual(self.project.scope_type, 'epc')
        return captured_id

    @skipUnlessDBFeature('has_select_for_update')
    def test_first_observation_and_scope_change_cannot_reclassify_published_history(self):
        self.approved_sources()
        captured_id = self.race_publication_and_scope(lambda: capture_control_snapshot(
            ScheduleVersion.objects.get(pk=self.version.pk), date(2026, 1, 5), self.authority))
        self.assertEqual(ScheduleControlSnapshot.objects.get(pk=captured_id).payload['control_scope']['scope_type'], 'epc')

    @skipUnlessDBFeature('has_select_for_update')
    def test_first_commercial_seal_and_scope_change_finish_without_fk_deadlock(self):
        self.approved_sources()
        period = ReportingPeriod.objects.create(project=self.project, sequence=1, name='First publication',
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 5), data_date=date(2026, 1, 5))
        reconciliation = reconcile_reporting_period(period, user=self.authority)
        self.assertEqual(reconciliation.status, 'completed', reconciliation.exceptions)
        period.status = 'submitted'
        period.save(update_fields=['status', 'updated_at'])
        captured_id = self.race_publication_and_scope(lambda: create_integrated_snapshot(period, user=self.authority))
        self.assertEqual(IntegratedReportingSnapshot.objects.get(pk=captured_id).budget_at_completion, Decimal('100000'))
