import datetime as dt
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from ..models import PlanningJob, PlanningProject
from ..services.operational_jobs import dispatch_job


class PlanningQueueRecoveryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='queue-recovery-planner')
        self.project = PlanningProject.objects.create(
            name='Queue Recovery', effective_date=dt.date(2026, 1, 1), created_by=self.user,
        )

    @override_settings(PLANNING_JOB_LOCAL_FALLBACK=True)
    @patch('apps.planning_intelligence.services.operational_jobs._local_executor.submit')
    @patch('apps.planning_intelligence.tasks.run_planning_job.apply_async')
    def test_broker_failure_uses_non_blocking_local_recovery(self, apply_async, submit):
        apply_async.side_effect = ConnectionError('redis unavailable')
        job = PlanningJob.objects.create(
            project=self.project, job_type='analyze', requested_by=self.user,
        )

        returned = dispatch_job(job)

        job.refresh_from_db()
        self.assertEqual(returned.id, job.id)
        self.assertEqual(job.status, 'queued')
        self.assertEqual(job.task_id, f'local-planning-job-{job.id}')
        self.assertEqual(job.progress_log[-1]['phase'], 'local_fallback')
        submit.assert_called_once()

    @override_settings(PLANNING_JOB_LOCAL_FALLBACK=False)
    @patch('apps.planning_intelligence.tasks.run_planning_job.apply_async')
    def test_broker_failure_fails_closed_when_recovery_is_disabled(self, apply_async):
        apply_async.side_effect = ConnectionError('redis unavailable')
        job = PlanningJob.objects.create(
            project=self.project, job_type='analyze', requested_by=self.user,
        )

        with self.assertRaisesRegex(RuntimeError, 'background worker queue is unavailable'):
            dispatch_job(job)

        job.refresh_from_db()
        self.assertEqual(job.status, 'failed')
        self.assertEqual(job.error_code, 'queue_unavailable')
