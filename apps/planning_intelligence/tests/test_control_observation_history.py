import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from importlib import import_module
from threading import Barrier
from unittest.mock import patch

from django.apps import apps
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.db.migrations.state import ProjectState
from django.db.models.deletion import CASCADE, ProtectedError
from django.test import TransactionTestCase, skipUnlessDBFeature
from rest_framework.test import APIClient

from apps.users.models import User

from ..models import (
    ActivityAssignment, ActivityProgressUpdate, PlanningProject, Schedule,
    ScheduleControlSnapshot, ScheduleResource, ScheduleVersion,
)
from ..schedule_serializers import ScheduleControlSnapshotSerializer
from ..services.project_controls import capture_control_snapshot
from .test_scheduling_engine import ScheduleFixture


class ControlObservationHistoryTests(ScheduleFixture):
    def setUp(self):
        super().setUp()
        self.data_date = dt.date(2026, 8, 25)
        self.task = self.activity(
            'A', 4, planned_start=dt.date(2026, 8, 24), planned_finish=dt.date(2026, 8, 27),
        )
        resource = ScheduleResource.objects.create(
            project=self.project, code='ENG', name='Engineer', resource_type='labor',
        )
        ActivityAssignment.objects.create(
            activity=self.task, resource=resource, budgeted_hours=10, budgeted_cost=100,
        )
        self.progress = ActivityProgressUpdate.objects.create(
            version=self.version, activity=self.task, data_date=self.data_date,
            physical_progress_pct=25, actual_cost=20, actual_hours=2,
            actual_start=dt.date(2026, 8, 24), forecast_finish=dt.date(2026, 8, 28),
            reported_by=self.owner, notes='First accepted measurement.',
        )

    def capture(self, data_date=None):
        return capture_control_snapshot(self.version, data_date or self.data_date, self.owner)

    def test_corrected_capture_keeps_original_values_ids_and_evidence(self):
        first = self.capture()
        original = dict(ScheduleControlSnapshotSerializer(first).data)
        self.progress.physical_progress_pct = 50
        self.progress.actual_cost = 30
        self.progress.notes = 'Corrected measurement.'
        self.progress.save()

        second = self.capture()
        first.refresh_from_db()

        self.assertEqual((first.revision, second.revision), (1, 2))
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(ScheduleControlSnapshotSerializer(first).data, original)
        self.assertEqual(first.earned_value, Decimal('25.00'))
        self.assertEqual(second.earned_value, Decimal('50.00'))
        self.assertEqual(first.actual_cost, Decimal('20.00'))
        self.assertEqual(second.actual_cost, Decimal('30.00'))
        first_evidence = first.payload['activities'][0]
        self.assertEqual(first_evidence['progress_update_id'], self.progress.pk)
        self.assertEqual(first_evidence['physical_progress_pct'], '25.00')
        self.assertEqual(first_evidence['notes'], 'First accepted measurement.')
        self.assertEqual(first_evidence['last_reported_date'], '2026-08-25')
        self.assertEqual(first.payload['source_manifest']['revision'], 1)
        self.assertEqual(second.payload['source_manifest']['revision'], 2)
        self.assertEqual(first.payload['source_manifest']['captured_by_id'], self.owner.pk)

    def test_identical_capture_is_a_new_observation_not_an_overwrite(self):
        first, second = self.capture(), self.capture()
        self.assertEqual((first.revision, second.revision), (1, 2))
        self.assertEqual(first.progress_pct, second.progress_pct)
        self.assertEqual(self.version.control_snapshots.count(), 2)

    def test_latest_revision_is_deterministic_when_timestamps_match(self):
        fixed = dt.datetime(2026, 8, 25, 12, tzinfo=dt.timezone.utc)
        with patch('django.utils.timezone.now', return_value=fixed):
            first, second = self.capture(), self.capture()
        self.assertEqual(first.created_at, second.created_at)
        self.assertEqual(self.version.control_snapshots.first().pk, second.pk)
        self.assertEqual(list(self.version.control_snapshots.values_list('revision', flat=True)), [2, 1])

    def test_revisions_restart_per_date_and_older_correction_does_not_become_latest_date(self):
        first = self.capture()
        later = self.capture(dt.date(2026, 8, 26))
        correction = self.capture()
        self.assertEqual((first.revision, later.revision, correction.revision), (1, 1, 2))
        self.assertEqual(self.version.control_snapshots.first().pk, later.pk)

    def test_model_save_soft_delete_and_instance_delete_cannot_rewrite_observation(self):
        captured = self.capture()
        for operation in ('save', 'soft_delete', 'delete'):
            with self.subTest(operation=operation):
                captured.refresh_from_db()
                captured.actual_cost = Decimal('999')
                with self.assertRaises(ValueError):
                    getattr(captured, operation)()
        captured.refresh_from_db()
        self.assertEqual(captured.actual_cost, Decimal('20.00'))
        self.assertFalse(captured.is_deleted)

    def test_public_queryset_mutations_and_upserts_cannot_rewrite_observation(self):
        captured = self.capture()
        captured.actual_cost = Decimal('999')
        operations = [
            lambda: ScheduleControlSnapshot.objects.filter(pk=captured.pk).update(actual_cost=999),
            lambda: ScheduleControlSnapshot.objects.bulk_update([captured], ['actual_cost']),
            lambda: ScheduleControlSnapshot.objects.filter(pk=captured.pk).delete(),
            lambda: ScheduleControlSnapshot.objects.update_or_create(
                version=self.version, data_date=self.data_date, revision=1,
                defaults={'actual_cost': 999},
            ),
            lambda: ScheduleControlSnapshot.objects.bulk_create([
                ScheduleControlSnapshot(version=self.version, data_date=self.data_date, revision=1, actual_cost=999),
            ], update_conflicts=True, update_fields=['actual_cost'], unique_fields=['version', 'data_date', 'revision']),
        ]
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(ValueError):
                    operation()
        captured.refresh_from_db()
        self.assertEqual(captured.actual_cost, Decimal('20.00'))

    def test_database_rejects_duplicate_or_nonpositive_revision(self):
        self.capture()
        for revision in (0, 1):
            with self.subTest(revision=revision), self.assertRaises(IntegrityError), transaction.atomic():
                ScheduleControlSnapshot.objects.create(
                    version=self.version, data_date=self.data_date, revision=revision,
                )

    def test_version_deletion_cannot_cascade_into_published_observations(self):
        captured = self.capture()
        with self.assertRaises(ProtectedError):
            self.version.delete()
        self.assertTrue(ScheduleControlSnapshot.objects.filter(pk=captured.pk).exists())

    def test_failed_capture_does_not_reserve_or_consume_a_revision(self):
        self.capture()
        with patch('apps.planning_intelligence.services.project_controls.build_control_dashboard', side_effect=ValueError('failed calculation')):
            with self.assertRaisesMessage(ValueError, 'failed calculation'):
                self.capture()
        self.assertEqual(self.capture().revision, 2)

    def test_capture_api_returns_distinct_read_only_revision_ids(self):
        client = APIClient()
        client.force_authenticate(self.owner)
        path = f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/capture-controls/'
        first = client.post(path, {'data_date': self.data_date.isoformat(), 'revision': 99}, format='json')
        second = client.post(path, {'data_date': self.data_date.isoformat()}, format='json')
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual((first.data['revision'], second.data['revision']), (1, 2))
        self.assertNotEqual(first.data['id'], second.data['id'])
        self.assertTrue(ScheduleControlSnapshotSerializer().fields['revision'].read_only)

    def test_controls_api_selects_latest_revision_at_or_before_requested_date(self):
        self.capture()
        corrected = self.capture()
        later = self.capture(dt.date(2026, 8, 26))
        client = APIClient()
        client.force_authenticate(self.owner)
        path = f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/controls/'
        historical = client.get(path, {'data_date': '2026-08-25'})
        current = client.get(path, {'data_date': '2026-08-26'})
        before_history = client.get(path, {'data_date': '2026-08-24'})
        self.assertEqual(historical.status_code, 200)
        self.assertEqual(historical.data['latest_snapshot']['id'], corrected.pk)
        self.assertEqual(historical.data['latest_snapshot']['revision'], 2)
        self.assertEqual(current.data['latest_snapshot']['id'], later.pk)
        self.assertIsNone(before_history.data['latest_snapshot'])


class ConcurrentControlObservationTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_concurrent_first_captures_allocate_distinct_revisions(self):
        owner = User.objects.create_user(username='concurrent-observer', email='observer@example.com')
        project = PlanningProject.objects.create(name='Concurrent capture', created_by=owner)
        schedule = Schedule.objects.create(
            project=project, name='Master', code='MASTER', planned_start=dt.date(2026, 8, 24), created_by=owner,
        )
        version = ScheduleVersion.objects.create(schedule=schedule, version=1, created_by=owner)
        barrier = Barrier(2)

        def capture():
            close_old_connections()
            try:
                selected = ScheduleVersion.objects.get(pk=version.pk)
                actor = User.objects.get(pk=owner.pk)
                barrier.wait(timeout=10)
                result = capture_control_snapshot(selected, dt.date(2026, 8, 25), actor)
                return result.pk, result.revision
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: capture(), range(2)))
        self.assertEqual(sorted(revision for _, revision in results), [1, 2])
        self.assertEqual(len({pk for pk, _ in results}), 2)
        self.assertEqual(ScheduleControlSnapshot.objects.filter(version=version).count(), 2)


class ControlObservationMigrationTests(TransactionTestCase):
    def test_existing_observation_keeps_identity_values_and_dates_as_revision_one(self):
        owner = User.objects.create_user(username='legacy-observer', email='legacy-observer@example.com')
        project = PlanningProject.objects.create(name='Legacy observations', created_by=owner)
        schedule = Schedule.objects.create(
            project=project, name='Master', code='MASTER', planned_start=dt.date(2026, 8, 24), created_by=owner,
        )
        version = ScheduleVersion.objects.create(schedule=schedule, version=1, created_by=owner)
        before = ProjectState.from_apps(apps)
        legacy_state = before.models['planning_intelligence', 'schedulecontrolsnapshot']
        legacy_state.fields.pop('revision')
        legacy_state.fields['version'].remote_field.on_delete = CASCADE
        legacy_state.options['unique_together'] = {('version', 'data_date')}
        legacy_state.options['ordering'] = ['-data_date', '-created_at']
        legacy_state.options['constraints'] = []
        legacy_model = before.apps.get_model('planning_intelligence', 'ScheduleControlSnapshot')
        migration = import_module(
            'apps.planning_intelligence.migrations.0028_schedule_control_observation_revisions',
        ).Migration('0028_schedule_control_observation_revisions', 'planning_intelligence')
        try:
            with connection.schema_editor() as editor:
                editor.delete_model(ScheduleControlSnapshot)
                editor.create_model(legacy_model)
            legacy = legacy_model.objects.create(
                version_id=version.pk, data_date=dt.date(2026, 8, 25),
                earned_value=Decimal('321.00'), captured_by_id=owner.pk,
                payload={'legacy_evidence': 'Original publication'},
            )
            with connection.schema_editor() as editor:
                migration.apply(before, editor)
            preserved = ScheduleControlSnapshot.objects.get(pk=legacy.pk)
            self.assertEqual(preserved.revision, 1)
            self.assertEqual(preserved.earned_value, Decimal('321.00'))
            self.assertEqual(preserved.data_date, legacy.data_date)
            self.assertEqual(preserved.created_at, legacy.created_at)
            self.assertEqual(preserved.updated_at, legacy.updated_at)
            self.assertEqual(preserved.payload, legacy.payload)
            self.assertEqual(capture_control_snapshot(version, legacy.data_date, owner).revision, 2)
        finally:
            # Restore the current model schema even if an assertion or migration
            # fails, so this isolated runner can safely execute other tests.
            with connection.schema_editor() as editor:
                editor.delete_model(ScheduleControlSnapshot)
                editor.create_model(ScheduleControlSnapshot)
