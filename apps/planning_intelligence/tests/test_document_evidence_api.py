"""Evidence drafts need source documents, not AI credentials or an approved plan."""
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.project_models import Project
from apps.rbac.models import Module, Permission
from apps.rbac.module_actions import ensure_module_actions
from apps.users.models import User

from ..models import PlanningFile, PlanningJob, PlanningProject, ProjectScheduleConfiguration, ScheduleVersion, WorkCalendar
from ..tasks import run_planning_job
from .test_business_approval_gates import grant_test_approval


class DocumentEvidenceAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='evidence-api', email='evidence-api@example.test')
        module, _ = Module.objects.get_or_create(code='planning_package', defaults={'name': 'Planning'})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        grant_test_approval((self.user,))
        enterprise = Project.objects.create(code='GENERIC-API', name='Supplier mobilisation', owner=self.user)
        self.project = PlanningProject.objects.create(name='Supplier mobilisation', created_by=self.user, enterprise_project=enterprise)
        self.file = PlanningFile.objects.create(
            project=self.project, category='other', file='tests/requirements.csv', original_filename='requirements.csv',
            parse_status='done', extracted_text='Task|Duration (working days)\nQualify supplier|7\nInspect excavation|3\n',
            uploaded_by=self.user,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def endpoint(self, action):
        return f'/api/v1/planning-intelligence/projects/{self.project.pk}/{action}/'

    @patch('apps.planning_intelligence.services.claude_client.get_claude_config', return_value=None)
    @patch('apps.planning_intelligence.services.claude_client.call_claude')
    @patch('apps.planning_intelligence.views.dispatch_job')
    def test_analyze_preview_generate_generic_csv_without_ai_or_approved_basis(self, dispatch, call_ai, _config):
        dispatch.side_effect = lambda job: run_planning_job.run(job.pk)
        for action in ('analyze', 'generation-preview', 'generate'):
            with self.subTest(action=action):
                response = self.client.post(self.endpoint(action), {}, format='json')
                self.assertEqual(response.status_code, 202, response.data)
                job = PlanningJob.objects.get(pk=response.data['id'])
                self.assertEqual(job.status, 'succeeded', job.error_message)
        call_ai.assert_not_called()
        generation = self.project.generations.get()
        self.assertEqual([activity['name'] for activity in generation.activities], ['Qualify supplier', 'Inspect excavation'])
        self.assertEqual([activity['original_duration_days'] for activity in generation.activities], [7, 3])
        self.assertTrue(all(activity['source_references'][0]['file_id'] == self.file.pk for activity in generation.activities))
        self.assertEqual(generation.logic_matrix, [])
        self.assertFalse(self.project.schedule_bases.filter(status='approved').exists())
        self.assertFalse(self.project.generation_plans.exists())
        self.assertFalse(ScheduleVersion.objects.filter(schedule__project=self.project).exists())
        self.assertFalse(WorkCalendar.objects.filter(project=self.project).exists())
        self.assertFalse(ProjectScheduleConfiguration.objects.filter(project=self.project).exists())
        self.assertEqual(job.result_data['state'], 'needs_evidence_review')

    @patch('apps.planning_intelligence.views.dispatch_job')
    def test_missing_parsed_sources_still_return_actionable_error(self, dispatch):
        self.file.parse_status = 'pending'
        self.file.save(update_fields=['parse_status'])
        for action in ('analyze', 'generation-preview', 'generate'):
            response = self.client.post(self.endpoint(action), {}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
            self.assertIn('parsed', response.data['error'])
        dispatch.assert_not_called()

    @patch('apps.planning_intelligence.views.dispatch_job')
    def test_unrelated_user_cannot_generate_evidence(self, dispatch):
        other = User.objects.create_user(username='evidence-other', email='other@example.test')
        grant_test_approval((other,))
        self.client.force_authenticate(other)
        response = self.client.post(self.endpoint('generate'), {}, format='json')
        self.assertEqual(response.status_code, 404, response.data)
        dispatch.assert_not_called()
