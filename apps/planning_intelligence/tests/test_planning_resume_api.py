"""Resume endpoints must preserve project isolation, idempotency and source fences."""
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, RolePermission, UserProfile, UserRole
from apps.rbac.module_actions import ensure_module_actions
from apps.users.models import User

from ..models import DocumentIntelligenceRun, PlanningFile, PlanningJob, PlanningProject
from ..services.document_intelligence import get_or_run_document_intelligence, run_document_intelligence
from ..tasks import run_planning_job


class PlanningResumeAPITests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='resume-owner', email='resume-owner@example.com')
        self.outsider = User.objects.create_user(username='resume-outsider', email='resume-outsider@example.com')
        organization = Organization.objects.create(name='Resume tests', code='resume-tests')
        self.role = Role.objects.create(name='Planning resume writer', code='resume-writer', level=4)
        module, _ = Module.objects.get_or_create(code='planning_package', defaults={'name': 'Planning'})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        RoleModule.objects.create(role=self.role, module=module)
        for permission in module.permissions.filter(action__in=['read', 'update', 'create'], is_active=True):
            RolePermission.objects.create(role=self.role, permission=permission)
        for user in (self.owner, self.outsider):
            profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': organization})
            UserRole.objects.create(user_profile=profile, role=self.role)
        self.project = PlanningProject.objects.create(name='Resume project', created_by=self.owner)
        self.source = PlanningFile.objects.create(project=self.project, category='sow', file='source.txt',
            original_filename='source.txt', parse_status='done', extracted_text='A' * 75)
        self.chunk_limit = patch('apps.planning_intelligence.services.intelligence.CLAUDE_MAX_INPUT_CHARS', 32)
        self.budget = patch.dict('os.environ', {'PLANNING_AI_MAX_CHUNKS': '1'})
        self.config = patch('apps.planning_intelligence.services.claude_client.get_claude_config', return_value={'configured': True})
        self.provider = patch('apps.planning_intelligence.services.claude_client.call_claude', return_value={'text': '{"facts": []}'})
        for item in (self.chunk_limit, self.budget, self.config, self.provider):
            self.addCleanup(item.stop)
        self.chunk_limit.start()
        self.budget.start()
        self.config.start()
        self.call_provider = self.provider.start()
        self.run, _ = run_document_intelligence(self.project, user=self.owner)
        self.call_provider.reset_mock()
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.url = f'/api/v1/planning-intelligence/intelligence-runs/{self.run.pk}/resume/'

    @patch('apps.planning_intelligence.intelligence_views.dispatch_job')
    def test_same_run_resume_reuses_one_durable_job_and_dispatch(self, dispatch):
        first = self.client.post(self.url, {}, format='json')
        second = self.client.post(self.url, {}, format='json')
        self.assertEqual(first.status_code, 202, first.data)
        self.assertEqual(second.status_code, 202, second.data)
        self.assertEqual(first.data['id'], second.data['id'])
        self.assertEqual(PlanningJob.objects.count(), 1)
        self.assertEqual(PlanningJob.objects.get().request_data, {'resume_run_id': self.run.pk})
        self.assertEqual(DocumentIntelligenceRun.objects.count(), 1)
        dispatch.assert_called_once()
        self.call_provider.assert_not_called()

    @patch('apps.planning_intelligence.intelligence_views.dispatch_job')
    def test_outsider_with_module_access_cannot_resume_another_project(self, dispatch):
        self.client.force_authenticate(self.outsider)
        response = self.client.post(self.url, {}, format='json')
        self.assertEqual(response.status_code, 404, response.data)
        self.assertFalse(PlanningJob.objects.exists())
        dispatch.assert_not_called()

    @patch('apps.planning_intelligence.intelligence_views.dispatch_job')
    def test_project_owner_without_module_update_cannot_resume(self, dispatch):
        RolePermission.objects.filter(role=self.role, permission__action='update').delete()
        response = self.client.post(self.url, {}, format='json')
        self.assertEqual(response.status_code, 403, response.data)
        self.assertFalse(PlanningJob.objects.exists())
        dispatch.assert_not_called()

    @patch('apps.planning_intelligence.intelligence_views.dispatch_job')
    def test_finished_or_unavailable_or_running_run_cannot_resume(self, dispatch):
        for run_status, summary in [
            ('succeeded', {'extraction_summary': {'resume_available': False}}),
            ('succeeded', {}),
            ('running', {'extraction_summary': {'resume_available': True}}),
        ]:
            with self.subTest(run_status=run_status, summary=summary):
                self.run.status, self.run.summary = run_status, summary
                self.run.save(update_fields=['status', 'summary'])
                response = self.client.post(self.url, {}, format='json')
                self.assertEqual(response.status_code, 409, response.data)
                self.assertEqual(response.data['code'], 'intelligence_resume_unavailable')
        self.assertFalse(PlanningJob.objects.exists())
        dispatch.assert_not_called()

    @patch('apps.planning_intelligence.intelligence_views.dispatch_job')
    def test_stale_content_hash_rejects_before_queueing_even_without_timestamp_change(self, dispatch):
        PlanningFile.objects.filter(pk=self.source.pk).update(extracted_text='Replacement source')
        response = self.client.post(self.url, {}, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertIn('source', response.data['error'].lower())
        self.assertFalse(PlanningJob.objects.exists())
        self.assertEqual(DocumentIntelligenceRun.objects.count(), 1)
        dispatch.assert_not_called()
        self.call_provider.assert_not_called()

    @patch('apps.planning_intelligence.intelligence_views.dispatch_job')
    def test_new_pending_upload_rejects_before_queueing(self, dispatch):
        PlanningFile.objects.create(project=self.project, file='pending.pdf', original_filename='pending.pdf', parse_status='pending')
        response = self.client.post(self.url, {}, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertIn('parsing', response.data['error'].lower())
        dispatch.assert_not_called()
        self.call_provider.assert_not_called()

    def test_reading_partial_run_does_not_start_work(self):
        response = self.client.get(f'/api/v1/planning-intelligence/intelligence-runs/{self.run.pk}/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['intelligence']['extraction_summary']['resume_available'])
        self.assertFalse(PlanningJob.objects.exists())
        self.call_provider.assert_not_called()

    @patch('apps.planning_intelligence.services.schedule_basis.build_schedule_basis',
           return_value=SimpleNamespace(id=9001, version=1, readiness={'ready': False}))
    def test_worker_unpacks_resumed_tuple_and_retains_partial_coverage(self, build_basis):
        job = PlanningJob.objects.create(project=self.project, requested_by=self.owner, job_type='analyze',
                                         request_data={'resume_run_id': self.run.pk}, idempotency_key='resume-worker')
        with patch('apps.planning_intelligence.services.document_intelligence.get_or_run_document_intelligence',
                   wraps=get_or_run_document_intelligence) as resume:
            result = run_planning_job.run(job.pk)
        job.refresh_from_db()
        self.assertEqual(result['status'], 'succeeded', job.error_message)
        self.assertEqual(resume.call_args.kwargs['resume_run'].pk, self.run.pk)
        intelligence = job.result_data['intelligence']
        self.assertNotEqual(intelligence['document_intelligence_run_id'], self.run.pk)
        self.assertEqual(intelligence['extraction_summary']['chunks_remaining'], 1)
        self.assertIn('partial', job.message)
        self.assertEqual(DocumentIntelligenceRun.objects.count(), 2)
        self.assertEqual(self.call_provider.call_count, 1)
        self.assertEqual(build_basis.call_args.args[0].pk, intelligence['document_intelligence_run_id'])
        # Task replay cannot create a third analysis or duplicate provider work.
        repeated = run_planning_job.run(job.pk)
        self.assertTrue(repeated['idempotent_replay'])
        self.assertEqual(DocumentIntelligenceRun.objects.count(), 2)
        self.assertEqual(self.call_provider.call_count, 1)

    def test_worker_rechecks_source_changed_after_enqueue_without_reprocessing(self):
        job = PlanningJob.objects.create(project=self.project, requested_by=self.owner, job_type='analyze',
                                         request_data={'resume_run_id': self.run.pk}, idempotency_key='resume-stale')
        PlanningFile.objects.filter(pk=self.source.pk).update(extracted_text='Changed after enqueue')
        result = run_planning_job.run(job.pk)
        job.refresh_from_db()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(job.status, 'failed')
        self.assertEqual(DocumentIntelligenceRun.objects.count(), 1)
        self.call_provider.assert_not_called()

    def test_worker_cannot_resume_run_from_another_project(self):
        other = PlanningProject.objects.create(name='Other project', created_by=self.outsider)
        job = PlanningJob.objects.create(project=other, requested_by=self.outsider, job_type='analyze',
                                         request_data={'resume_run_id': self.run.pk}, idempotency_key='resume-cross-project')
        result = run_planning_job.run(job.pk)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(DocumentIntelligenceRun.objects.count(), 1)
        self.call_provider.assert_not_called()
