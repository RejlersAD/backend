"""Preview workers must see committed jobs; explicit recovery cannot duplicate work."""
from unittest.mock import patch

from django.db import connection, transaction
from django.test import TransactionTestCase, override_settings
from django.urls import include, path
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.project_models import Project
from apps.rbac.route_guard import secure_module_endpoints
from apps.users.models import User
from ..models import PlanningFile, PlanningJob, PlanningProject
from ..services.operational_jobs import dispatch_job, get_or_create_job
from ..tasks import run_planning_job
from .test_scheduling_engine import grant_planning_test_actions


urlpatterns = [path('api/v1/planning-intelligence/', include('apps.planning_intelligence.urls'))]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class PreviewJobDispatchTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='preview-dispatch', email='preview@example.test')
        grant_planning_test_actions((self.user,), ('read', 'create', 'update'))
        enterprise = Project.objects.create(code='PREVIEW-DISPATCH', name='Preview dispatch', owner=self.user)
        self.project = PlanningProject.objects.create(name='Preview dispatch', created_by=self.user, enterprise_project=enterprise)
        self.file = PlanningFile.objects.create(
            project=self.project, category='other', file='tests/activities.csv', original_filename='activities.csv',
            parse_status='done', extracted_text='Task|Duration\nInspect foundations|3\n', uploaded_by=self.user,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = f'/api/v1/planning-intelligence/projects/{self.project.pk}/generation-preview/'

    def queued_job(self, payload=None):
        job, _ = get_or_create_job(self.project, 'preview', payload or {}, self.user)
        job.task_id = f'planning-job-{job.pk}'
        job.save(update_fields=['task_id'])
        return job

    def retry(self, job, payload=None):
        return self.client.post(self.url, {**(payload or {}), 'retry_queued_job_id': job.pk}, format='json')

    @patch('apps.planning_intelligence.services.pipeline.preview_schedule', return_value={'activities': []})
    @patch('apps.planning_intelligence.tasks.run_planning_job.apply_async')
    def test_guarded_api_dispatches_fast_worker_after_outer_commit(self, publish, preview):
        def immediate_worker(*, args, task_id):
            self.assertFalse(connection.in_atomic_block)
            job = PlanningJob.objects.get(pk=args[0])
            self.assertEqual(job.task_id, task_id)
            run_planning_job.run(job.pk, dispatch_token=task_id)

        publish.side_effect = immediate_worker
        with transaction.atomic():
            with transaction.atomic():
                response = self.client.post(self.url, {}, format='json')
                self.assertEqual(response.status_code, 202, response.data)
                publish.assert_not_called()
            publish.assert_not_called()
        job = PlanningJob.objects.get(pk=response.data['id'])
        self.assertEqual(job.status, 'succeeded')
        self.assertEqual(job.attempt_count, 1)
        publish.assert_called_once()
        preview.assert_called_once()

    @patch('apps.planning_intelligence.tasks.run_planning_job.apply_async')
    def test_rolled_back_command_never_publishes(self, publish):
        with self.assertRaisesMessage(RuntimeError, 'rollback'):
            with transaction.atomic():
                response = self.client.post(self.url, {}, format='json')
                self.assertEqual(response.status_code, 202, response.data)
                raise RuntimeError('rollback')
        publish.assert_not_called()
        self.assertFalse(PlanningJob.objects.exists())

    @patch('apps.planning_intelligence.tasks.run_planning_job.apply_async')
    def test_autocommit_service_dispatch_remains_immediate(self, publish):
        job = self.queued_job()
        dispatch_job(job)
        publish.assert_called_once_with(args=[job.pk], task_id=job.task_id)

    @override_settings(PLANNING_JOB_LOCAL_FALLBACK=True)
    @patch('apps.planning_intelligence.services.operational_jobs._local_executor.submit')
    @patch('apps.planning_intelligence.tasks.run_planning_job.apply_async', side_effect=ConnectionError('broker unavailable'))
    def test_local_fallback_waits_for_commit_and_receives_delivery_token(self, publish, submit):
        with transaction.atomic():
            response = self.client.post(self.url, {}, format='json')
            publish.assert_not_called()
            submit.assert_not_called()
        job = PlanningJob.objects.get(pk=response.data['id'])
        self.assertEqual(job.status, 'queued')
        self.assertEqual(job.task_id, f'local-planning-job-{job.pk}')
        self.assertEqual(submit.call_args.args[2:], (job.pk, job.task_id))

    @override_settings(PLANNING_JOB_LOCAL_FALLBACK=False)
    @patch('apps.planning_intelligence.tasks.run_planning_job.apply_async', side_effect=ConnectionError('broker unavailable'))
    def test_post_commit_failure_is_durable_without_undoing_accepted_job(self, publish):
        response = self.client.post(self.url, {}, format='json')
        self.assertEqual(response.status_code, 202, response.data)
        job = PlanningJob.objects.get(pk=response.data['id'])
        self.assertEqual(job.status, 'failed')
        self.assertEqual(job.error_code, 'queue_unavailable')
        self.assertIsNotNone(job.finished_at)

    @patch('apps.planning_intelligence.services.pipeline.preview_schedule', return_value={'activities': []})
    @patch('apps.planning_intelligence.tasks.run_planning_job.apply_async')
    def test_explicit_retry_keeps_job_identity_and_rejects_old_delivery(self, publish, preview):
        job = self.queued_job()
        previous_token = job.task_id
        response = self.retry(job)
        self.assertEqual(response.status_code, 202, response.data)
        job.refresh_from_db()
        self.assertEqual(response.data['id'], job.pk)
        self.assertEqual(PlanningJob.objects.count(), 1)
        self.assertNotEqual(previous_token, job.task_id)
        publish.assert_called_once_with(args=[job.pk], task_id=job.task_id)
        with patch('apps.planning_intelligence.tasks._execute_planning_job') as execute:
            result = run_planning_job.run(job.pk, dispatch_token=previous_token)
            self.assertTrue(result['stale_dispatch'])
            execute.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.attempt_count, 0)
        run_planning_job.run(job.pk, dispatch_token=job.task_id)
        job.refresh_from_db()
        self.assertEqual(job.status, 'succeeded')
        self.assertEqual(job.attempt_count, 1)
        preview.assert_called_once()
        self.assertFalse(self.project.generations.exists())
        self.assertFalse(self.project.schedules.exists())

    @patch('apps.planning_intelligence.services.pipeline.preview_schedule', return_value={'activities': []})
    @patch('apps.planning_intelligence.tasks.run_planning_job.apply_async')
    def test_repeated_explicit_retries_only_latest_delivery_can_execute(self, publish, preview):
        job = self.queued_job()
        self.assertEqual(self.retry(job).status_code, 202)
        first_token = publish.call_args.kwargs['task_id']
        self.assertEqual(self.retry(job).status_code, 202)
        second_token = publish.call_args.kwargs['task_id']
        self.assertNotEqual(first_token, second_token)
        self.assertTrue(run_planning_job.run(job.pk, dispatch_token=first_token)['stale_dispatch'])
        run_planning_job.run(job.pk, dispatch_token=second_token)
        self.assertTrue(run_planning_job.run(job.pk, dispatch_token=second_token)['idempotent_replay'])
        preview.assert_called_once()
        job.refresh_from_db()
        self.assertEqual(job.attempt_count, 1)

    @patch('apps.planning_intelligence.tasks._execute_planning_job')
    def test_duplicate_running_delivery_does_not_start_work_or_telemetry(self, execute):
        job = self.queued_job()
        job.status, job.started_at, job.attempt_count = 'running', timezone.now(), 1
        job.save(update_fields=['status', 'started_at', 'attempt_count'])
        self.assertTrue(run_planning_job.run(job.pk, dispatch_token=job.task_id)['already_running'])
        execute.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.attempt_count, 1)

    @patch('apps.planning_intelligence.tasks._execute_planning_job', return_value={'status': 'succeeded'})
    def test_worker_lost_redelivery_retains_existing_recovery_semantics(self, execute):
        job = self.queued_job()
        job.status, job.started_at, job.attempt_count = 'running', timezone.now(), 1
        job.save(update_fields=['status', 'started_at', 'attempt_count'])
        run_planning_job.push_request(id=job.task_id, delivery_info={'redelivered': True})
        try:
            run_planning_job.run(job.pk)
        finally:
            run_planning_job.pop_request()
        execute.assert_called_once()
        job.refresh_from_db()
        self.assertEqual(job.attempt_count, 2)

    @patch('apps.planning_intelligence.tasks.run_planning_job.apply_async')
    def test_normal_reopen_reuses_queued_job_without_redispatch(self, publish):
        job = self.queued_job()
        response = self.client.post(self.url, {}, format='json')
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data['id'], job.pk)
        publish.assert_not_called()

    @patch('apps.planning_intelligence.tasks.run_planning_job.apply_async')
    def test_retry_preserves_running_and_terminal_jobs(self, publish):
        job = self.queued_job()
        for status in ('running', 'succeeded', 'failed', 'cancelled'):
            job.status, job.started_at, job.attempt_count = status, timezone.now(), 1
            job.save(update_fields=['status', 'started_at', 'attempt_count'])
            response = self.retry(job)
            self.assertEqual(response.status_code, 202, response.data)
            self.assertEqual(response.data['status'], status)
        publish.assert_not_called()

    @patch('apps.planning_intelligence.tasks.run_planning_job.apply_async')
    def test_retry_rejects_changed_payload_and_source_fingerprint(self, publish):
        job = self.queued_job({'intelligence_overrides': {'review': 'first'}})
        response = self.retry(job, {'intelligence_overrides': {'review': 'second'}})
        self.assertEqual(response.status_code, 409, response.data)
        self.file.save()
        response = self.retry(job, job.request_data)
        self.assertEqual(response.status_code, 409, response.data)
        publish.assert_not_called()
        self.assertEqual(PlanningJob.objects.count(), 1)

    @patch('apps.planning_intelligence.tasks.run_planning_job.apply_async')
    def test_retry_rejects_wrong_project_type_and_invalid_identity(self, publish):
        other = PlanningProject.objects.create(name='Other', created_by=self.user)
        foreign = PlanningJob.objects.create(project=other, job_type='preview')
        wrong_type = PlanningJob.objects.create(project=self.project, job_type='generate')
        for job in (foreign, wrong_type):
            response = self.retry(job)
            self.assertEqual(response.status_code, 404, response.data)
        for identity in (True, -1, 1.5, 'bad'):
            response = self.client.post(self.url, {'retry_queued_job_id': identity}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        publish.assert_not_called()

    @patch('apps.planning_intelligence.tasks.run_planning_job.apply_async')
    def test_retry_rollback_preserves_previous_delivery(self, publish):
        job = self.queued_job()
        previous_token = job.task_id
        with self.assertRaisesMessage(RuntimeError, 'rollback'):
            with transaction.atomic():
                self.assertEqual(self.retry(job).status_code, 202)
                raise RuntimeError('rollback')
        job.refresh_from_db()
        self.assertEqual(job.task_id, previous_token)
        publish.assert_not_called()

    @override_settings(PLANNING_JOB_LOCAL_FALLBACK=True)
    @patch('apps.planning_intelligence.services.operational_jobs._local_executor.submit')
    @patch('apps.planning_intelligence.tasks.run_planning_job.apply_async')
    def test_publish_error_cannot_reset_job_already_claimed_by_worker(self, publish, submit):
        def ambiguous_publish(*, args, task_id):
            PlanningJob.objects.filter(pk=args[0]).update(status='running', attempt_count=1, started_at=timezone.now())
            raise ConnectionError('publish acknowledgement lost')

        publish.side_effect = ambiguous_publish
        response = self.client.post(self.url, {}, format='json')
        self.assertEqual(response.status_code, 202)
        job = PlanningJob.objects.get(pk=response.data['id'])
        self.assertEqual(job.status, 'running')
        self.assertEqual(job.attempt_count, 1)
        submit.assert_not_called()
