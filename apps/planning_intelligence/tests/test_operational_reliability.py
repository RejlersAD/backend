import datetime as dt
import os
import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import SimpleTestCase, TestCase, tag
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from ..models import ActivityRelationship, PlanningProject, Schedule, ScheduleActivity, ScheduleVersion, WorkCalendar
from ..services.cpm import calculate_schedule_version
from ..services.deployment_compatibility import check_planning_compatibility
from ..services.operational_jobs import generation_plan_build_fingerprint, get_or_create_job
from ..services.regression_library import run_regression_library


class OperationalJobTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='reliable-planner', password='test')
        self.project = PlanningProject.objects.create(
            name='Reliable Planning', effective_date=dt.date(2026, 1, 1), created_by=self.user,
        )

    def test_same_request_reuses_durable_job(self):
        payload = {'schedule_version_id': 99, 'calculated_state_at': '2026-08-26T12:00:00Z'}
        first, first_created = get_or_create_job(self.project, 'assurance', payload, self.user)
        second, second_created = get_or_create_job(self.project, 'assurance', payload, self.user)

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first.id, second.id)
        self.assertEqual(first.idempotency_key, second.idempotency_key)

    def test_job_progress_is_persistent_and_auditable(self):
        from ..services.operational_jobs import update_job_progress
        job, _ = get_or_create_job(self.project, 'calculate', {'version': 1}, self.user)
        update_job_progress(job, 35, 'Calculating network', phase='cpm')
        job.refresh_from_db()
        self.assertEqual(job.progress, 35)
        self.assertEqual(job.progress_log[-1]['phase'], 'cpm')
        self.assertIsNotNone(job.heartbeat_at)

    def test_new_extractor_version_does_not_replay_old_analysis_job(self):
        with patch('apps.planning_intelligence.services.document_intelligence.ENGINE_VERSION', '3.1'):
            previous, _ = get_or_create_job(self.project, 'analyze', {}, self.user)
        previous.status = 'succeeded'
        previous.result_data = {'intelligence_run_id': 99}
        previous.save(update_fields=['status', 'result_data'])

        current, created = get_or_create_job(self.project, 'analyze', {}, self.user)
        replay, replay_created = get_or_create_job(self.project, 'analyze', {}, self.user)

        self.assertTrue(created)
        self.assertNotEqual(current.pk, previous.pk)
        self.assertFalse(replay_created)
        self.assertEqual(replay.pk, current.pk)
        previous.refresh_from_db()
        self.assertEqual(previous.status, 'succeeded')
        self.assertEqual(previous.result_data, {'intelligence_run_id': 99})

    def test_generation_plan_job_is_idempotent_for_unchanged_basis(self):
        from ..models import DocumentIntelligenceRun, ScheduleBasis
        finished_at = timezone.now()
        run = DocumentIntelligenceRun.objects.create(
            project=self.project, status='succeeded', started_at=finished_at,
            finished_at=finished_at, requested_by=self.user,
        )
        basis = ScheduleBasis.objects.create(project=self.project, source_run=run, version=1, status='approved')
        fingerprint = generation_plan_build_fingerprint(basis)
        first, created = get_or_create_job(
            self.project, 'build_plan', {'basis_id': basis.id}, self.user,
            idempotency_key=fingerprint,
        )
        second, replay_created = get_or_create_job(
            self.project, 'build_plan', {'basis_id': basis.id}, self.user,
            idempotency_key=generation_plan_build_fingerprint(basis),
        )
        self.assertTrue(created)
        self.assertFalse(replay_created)
        self.assertEqual(first.id, second.id)
        basis.project_name = 'Reviewed basis name'
        basis.save(update_fields=['project_name', 'updated_at'])
        changed_fingerprint = generation_plan_build_fingerprint(basis)
        self.assertNotEqual(changed_fingerprint, fingerprint)
        changed, changed_created = get_or_create_job(
            self.project, 'build_plan', {'basis_id': basis.id}, self.user,
            idempotency_key=changed_fingerprint,
        )
        self.assertTrue(changed_created)
        self.assertNotEqual(changed.id, first.id)

    def test_corrected_extractor_invalidates_cached_preview_and_generation_jobs(self):
        for job_type in ('preview', 'generate'):
            with self.subTest(job_type=job_type):
                with patch('apps.planning_intelligence.services.document_intelligence.ENGINE_VERSION', 'previous-extractor'):
                    previous, _ = get_or_create_job(self.project, job_type, {}, self.user)
                previous.status = 'succeeded'
                previous.result_data = {'preview': {'activities': []}}
                previous.save(update_fields=['status', 'result_data'])
                with patch('apps.planning_intelligence.services.document_intelligence.ENGINE_VERSION', 'corrected-extractor'):
                    current, created = get_or_create_job(self.project, job_type, {}, self.user)
                    replay, replay_created = get_or_create_job(self.project, job_type, {}, self.user)
                self.assertTrue(created)
                self.assertNotEqual(current.pk, previous.pk)
                self.assertFalse(replay_created)
                self.assertEqual(replay.pk, current.pk)
                previous.refresh_from_db()
                self.assertEqual(previous.status, 'succeeded')
                self.assertEqual(previous.result_data, {'preview': {'activities': []}})

class RegressionLibraryTests(SimpleTestCase):
    def test_all_reference_projects_match_expected_topology(self):
        results = run_regression_library()
        self.assertGreaterEqual(len(results), 3)
        self.assertTrue(all(row['passed'] for row in results), results)

    @tag('performance')
    def test_ten_thousand_activity_reference_meets_cpu_budget(self):
        started = time.perf_counter()
        result = next(row for row in run_regression_library() if row['code'] == 'enterprise_large_10000')
        elapsed = time.perf_counter() - started
        budget = float(os.getenv('PLANNING_REGRESSION_CPU_BUDGET_SECONDS', '2.0'))
        self.assertTrue(result['passed'])
        self.assertLess(elapsed, budget, f'10k activity topology validation took {elapsed:.3f}s')


class DeploymentCompatibilityTests(TestCase):
    def test_required_routes_models_and_migrations_are_compatible(self):
        result = check_planning_compatibility()
        self.assertTrue(result['compatible'], result['checks'])


class LargeSchedulePerformanceTests(TestCase):
    @tag('performance')
    def test_relational_cpm_scales_to_one_thousand_activity_chain(self):
        user = get_user_model().objects.create_user(username='performance-planner', password='test')
        project = PlanningProject.objects.create(
            name='Large CPM Reference', effective_date=dt.date(2026, 1, 1), created_by=user,
        )
        calendar = WorkCalendar.objects.create(
            project=project, name='Performance Calendar', working_weekdays=[0, 1, 2, 3, 4], is_default=True,
        )
        schedule = Schedule.objects.create(
            project=project, name='Large Schedule', code='PERF-1000', planned_start=dt.date(2026, 1, 1),
            default_calendar=calendar, created_by=user,
        )
        version = ScheduleVersion.objects.create(schedule=schedule, version=1, created_by=user)
        ScheduleActivity.objects.bulk_create([
            ScheduleActivity(
                version=version, calendar=calendar, external_id=f'A{index:05d}', name=f'Activity {index}',
                duration_days=1, sort_order=index,
            ) for index in range(1000)
        ], batch_size=500)
        activities = list(version.activities.order_by('sort_order'))
        ActivityRelationship.objects.bulk_create([
            ActivityRelationship(version=version, predecessor=activities[index - 1], successor=activities[index])
            for index in range(1, len(activities))
        ], batch_size=500)

        started = time.perf_counter()
        with CaptureQueriesContext(connection) as queries:
            run = calculate_schedule_version(version, requested_by=user)
        elapsed = time.perf_counter() - started

        budget = float(os.getenv('PLANNING_CPM_1000_BUDGET_SECONDS', '30'))
        self.assertEqual(run.activity_count, 1000)
        self.assertEqual(run.status, 'succeeded')
        self.assertLess(len(queries), 50, f'CPM used {len(queries)} database queries')
        self.assertLess(elapsed, budget, f'1k activity CPM took {elapsed:.3f}s')
