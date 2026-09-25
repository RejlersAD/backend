"""Evidence drafts need source documents, not AI credentials or an approved plan."""
from copy import deepcopy
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.project_models import Project
from apps.rbac.models import Module, Permission
from apps.rbac.module_actions import ensure_module_actions
from apps.users.models import User

from ..models import (
    PlanningFile, PlanningJob, PlanningProject, ProjectScheduleConfiguration,
    Schedule, ScheduleBaseline, ScheduleCalculationRun, ScheduleVersion, WorkCalendar,
)
from ..services.pipeline import generate_schedule
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

    def missing_evidence_generation(self):
        self.file.extracted_text = 'Task|Duration (working days)\nQualify supplier|\nInspect excavation|\n'
        self.file.save(update_fields=['extracted_text'])
        with patch('apps.planning_intelligence.services.pipeline.analyze_documents', return_value={}):
            return generate_schedule(self.project, user=self.user)

    def test_materialize_returns_review_state_and_keeps_saved_evidence_on_retry(self):
        generation = self.missing_evidence_generation()
        saved_activities = deepcopy(generation.activities)
        saved_intelligence = deepcopy(generation.intelligence)
        saved_validation = deepcopy(generation.validation)
        saved_updated_at = generation.updated_at
        self.assertEqual(len(saved_activities), 2)
        self.assertTrue(saved_validation)
        self.assertTrue(all(row['duration_days'] is None for row in saved_activities))
        self.assertFalse(saved_intelligence['schedule_engine']['ready_for_calculation'])

        endpoint = f'/api/v1/planning-intelligence/generations/{generation.pk}/'
        for attempt in range(2):
            with self.subTest(attempt=attempt):
                response = self.client.post(f'{endpoint}materialize/', {}, format='json')
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data['generation_id'], generation.pk)
                self.assertEqual(response.data['state'], 'needs_evidence_review')
                self.assertIsNone(response.data.get('schedule_id'))
                self.assertIsNone(response.data['schedule_version_id'])
                self.assertIsNone(response.data['calculation_run_id'])
                self.assertEqual(response.data['materialization_issues'], saved_validation)

        generation.refresh_from_db()
        self.assertEqual(generation.activities, saved_activities)
        self.assertEqual(generation.intelligence, saved_intelligence)
        self.assertEqual(generation.validation, saved_validation)
        self.assertEqual(generation.updated_at, saved_updated_at)
        self.assertEqual(self.project.generations.count(), 1)
        self.assertFalse(Schedule.objects.filter(project=self.project).exists())
        self.assertFalse(ScheduleVersion.objects.filter(schedule__project=self.project).exists())
        self.assertFalse(ScheduleCalculationRun.objects.filter(version__schedule__project=self.project).exists())
        self.assertFalse(ScheduleBaseline.objects.filter(schedule__project=self.project).exists())
        self.assertFalse(WorkCalendar.objects.filter(project=self.project).exists())
        self.assertFalse(ProjectScheduleConfiguration.objects.filter(project=self.project).exists())

        # An evidence-only result remains a usable, saved generation to reopen.
        reopened = self.client.get(endpoint)
        self.assertEqual(reopened.status_code, 200, reopened.data)
        self.assertEqual(reopened.data['activities'], saved_activities)
        self.assertEqual(reopened.data['validation'], saved_validation)

    @patch('apps.planning_intelligence.services.schedule_materializer.materialize_generation')
    def test_foreign_project_generation_cannot_be_read_or_materialized(self, materialize):
        generation = self.missing_evidence_generation()
        other = User.objects.create_user(username='foreign-materializer', email='foreign-materializer@example.test')
        grant_test_approval((other,))
        self.client.force_authenticate(other)
        endpoint = f'/api/v1/planning-intelligence/generations/{generation.pk}/'

        read_response = self.client.get(endpoint)
        response = self.client.post(f'{endpoint}materialize/', {}, format='json')

        self.assertEqual(read_response.status_code, 404, read_response.data)
        self.assertEqual(response.status_code, 404, response.data)
        materialize.assert_not_called()
        self.assertEqual(self.project.generations.count(), 1)
        self.assertFalse(Schedule.objects.filter(project=self.project).exists())
        self.assertFalse(WorkCalendar.objects.filter(project=self.project).exists())

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
