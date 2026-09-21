"""A register must not hide independently recovered source schedule evidence."""
from copy import deepcopy
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import path

from apps.core.project_models import ProjectTask
from apps.rbac.route_guard import secure_module_endpoints

from ..models import PlanningFile
from ..simple_planning_views import SimplePlanningView
from . import test_simple_planning as fixture


urlpatterns = [
    path('api/v1/planning-intelligence/projects/<int:project_id>/simple-plan/', SimplePlanningView.as_view()),
    *[path(f'api/v1/planning-intelligence/projects/<int:project_id>/simple-plan/{operation}/',
           SimplePlanningView.as_view(operation=operation))
      for operation in ('analyse', 'source-preview')],
]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class SourceSchedulePreviewTests(TestCase):
    def setUp(self):
        fixture.SimplePlanningTests.setUp(self)
        self.register = PlanningFile.objects.create(
            project=self.project, category='mdr', file='tests/register.xlsx', original_filename='Register.xlsx',
            parse_status='done', uploaded_by=self.owner,
            extracted_text='Document Number|Document Title|Department\nD1|Inspection|Quality\nD2|Training|Operations\n')
        self.schedule = PlanningFile.objects.create(
            project=self.project, category='reference_schedule', file='tests/schedule.csv', original_filename='Schedule.csv',
            parse_status='done', uploaded_by=self.owner,
            extracted_text='ID|Task|Duration|Duration Unit|Start|Finish|Predecessors|Activity Type\n'
                           'D1|Inspection|4|working days|2026-11-09|2026-11-12|None|Task\n'
                           'A2|Witness qualification|2|working days|2026-11-13|2026-11-16|D1:FS+0d|Task\n')
        self.preview_url = self.url + 'source-preview/'

    def preview(self, **params):
        response = self.client.get(self.preview_url, params)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_mdr_does_not_hide_schedule_dates_durations_or_explicit_relationships(self):
        data = self.preview()
        self.assertEqual(data['summary']['activity_count'], 2)
        self.assertEqual(data['summary']['duration_count'], 2)
        self.assertEqual(data['summary']['start_date_count'], 2)
        self.assertEqual(data['summary']['finish_date_count'], 2)
        self.assertEqual(data['summary']['relationship_count'], 1)
        self.assertEqual(data['summary']['register_count'], 2)
        # A matching printed identifier/title is not proof of cross-document identity.
        self.assertEqual(data['summary']['matched_register_count'], 0)
        self.assertEqual(data['summary']['unmapped_register_count'], 2)
        self.assertEqual(data['summary']['unmapped_schedule_count'], 2)
        first, second = data['rows']
        self.assertEqual((first['source_activity_id'], first['duration_days']), ('D1', 4))
        self.assertIs(first['is_milestone'], False)
        self.assertEqual(first['source_start_date'], '2026-11-09')
        self.assertEqual(first['source_finish_date'], '2026-11-12')
        self.assertEqual(first['source_start_status'], 'extracted')
        self.assertEqual(first['source_references'][0]['file_id'], self.schedule.pk)
        self.assertEqual(first['source_references'][0]['locator']['line'], 2)
        self.assertEqual(second['predecessors'][0]['id'], first['id'])
        self.assertEqual(second['predecessors'][0]['type'], 'FS')
        self.assertFalse(data['applied'])
        self.assertFalse(data['calculation_available'])
        self.assertFalse(first['calendar_verified'])
        self.assertIsNone(first['total_float_days'])
        self.assertEqual(data['project_window'], {'start_date': '2026-11-06', 'finish_date': '2026-12-20'})

    def test_preview_never_writes_draft_assignments_dates_or_analysis(self):
        original = {'revision': 8, 'tasks': [{'id': 'existing', 'title': 'Assigned register row',
            'assignee_id': self.reviewer.pk, 'duration_days': None, 'depends_on': []}]}
        self.project.simple_planning_state = deepcopy(original)
        self.project.save(update_fields=['simple_planning_state'])
        assignment = ProjectTask.objects.create(project=self.enterprise, title='Assigned register row',
            assigned_to=self.reviewer, source_key=f'simple:{self.project.pk}:existing',
            status='in_progress', progress_percent=40)
        with CaptureQueriesContext(connection) as queries, patch(
                'apps.planning_intelligence.services.simple_planning.run_document_intelligence') as analyse:
            self.preview()
        self.assertFalse([row for row in queries if row['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))])
        analyse.assert_not_called()
        self.project.refresh_from_db()
        assignment.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, original)
        self.assertEqual(assignment.assigned_to_id, self.reviewer.pk)
        self.assertEqual(assignment.progress_percent, 40)
        self.assertEqual(assignment.status, 'in_progress')
        self.assertEqual(self.project.planned_end_date.isoformat(), '2026-12-20')
        self.assertFalse(self.project.intelligence_runs.exists())
        self.assertFalse(self.project.schedules.exists())

    def test_pagination_and_search_keep_global_counts_separate(self):
        first = self.preview(limit=1)
        self.assertTrue(first['pagination']['has_next'])
        second = self.preview(limit=1, offset=1)
        self.assertFalse(second['pagination']['has_next'])
        self.assertNotEqual(first['rows'][0]['id'], second['rows'][0]['id'])
        found = self.preview(search='wItNeSs')
        self.assertEqual(found['pagination']['total'], 1)
        self.assertEqual(found['summary']['activity_count'], 2)
        self.assertEqual(found['rows'][0]['source_activity_id'], 'A2')
        self.assertEqual(self.preview(search='D1')['rows'][0]['source_activity_id'], 'D1')
        self.assertEqual(self.preview(offset=999)['rows'], [])
        for params in ({'limit': 201}, {'limit': 0}, {'offset': -1}, {'offset': 'bad'}, {'search': 'x' * 251}):
            with self.subTest(params=params):
                self.assertEqual(self.client.get(self.preview_url, params).status_code, 400)

    def test_preview_requires_project_access_and_planning_read_permission(self):
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.preview_url).status_code, 404)
        self.client.force_authenticate(self.owner)
        with patch('apps.planning_intelligence.simple_planning_views.module_action_allowed', return_value=False):
            self.assertEqual(self.client.get(self.preview_url).status_code, 403)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.preview_url).status_code, (401, 403))

    def test_preview_does_not_accept_mutation_methods(self):
        for method in ('post', 'put', 'patch', 'delete'):
            with self.subTest(method=method):
                response = getattr(self.client, method)(self.preview_url, {'revision': 0}, format='json')
                self.assertEqual(response.status_code, 405)

    def test_unparsed_deleted_example_files_and_register_fallback_are_not_schedule_rows(self):
        self.schedule.parse_status = 'pending'
        self.schedule.save(update_fields=['parse_status'])
        data = self.preview()
        self.assertEqual(data['rows'], [])
        self.assertEqual(data['summary']['activity_count'], 0)
        self.assertEqual(data['summary']['register_count'], 2)
        self.assertEqual(data['summary']['matched_register_count'], 0)
        self.assertEqual(data['summary']['unmapped_register_count'], 2)
        for fields in ({'parse_status': 'done', 'is_deleted': True},
                       {'parse_status': 'done', 'is_deleted': False, 'category': 'output_schedule_sample'}):
            PlanningFile.objects.filter(pk=self.schedule.pk).update(**fields)
            self.assertEqual(self.preview()['rows'], [])

    def test_analyse_retains_extracted_summary_without_replacing_register_scope(self):
        with patch('apps.planning_intelligence.services.claude_client.call_claude') as ai:
            response = self.client.post(self.url + 'analyse/', {'revision': 0}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        ai.assert_not_called()
        self.assertEqual([row['title'] for row in response.data['tasks']], ['Inspection', 'Training'])
        self.assertTrue(all(row['duration_days'] is None for row in response.data['tasks']))
        self.assertEqual(response.data['document_schedule_summary']['activity_count'], 2)
        self.assertTrue(response.data['source_preview_available'])
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state['document_schedule_summary']['duration_count'], 2)
        self.assertIn('register_schedule_association_not_specified',
                      self.project.simple_planning_state['document_schedule_summary']['validation_counts'])

    def test_legacy_draft_exposes_preview_without_reanalysis(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['source_preview_available'])
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, {})
