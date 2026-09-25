"""The planning canvas drafts, reviews and publishes through existing guards."""
from copy import deepcopy
from datetime import date
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import path
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.project_models import Project, ProjectMember, ProjectTask
from apps.hr_core.models import EmployeeMaster
from apps.rbac.models import Module, Permission, UserProfile
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints
from apps.users.models import User

from ..models import (
    CalendarException, PlanningProject, PlanningFile, Schedule, ScheduleActivity,
    ScheduleBaseline, ScheduleReview, ScheduleReviewDecision, ScheduleVersion, ScheduleWBSNode, WorkCalendar,
    WorkflowStage, WorkflowTemplate,
)
from ..simple_planning_views import SimplePlanningView
from .test_business_approval_gates import grant_test_approval


urlpatterns = [
    path('api/v1/planning-intelligence/projects/<int:project_id>/simple-plan/', SimplePlanningView.as_view()),
    *[path(f'api/v1/planning-intelligence/projects/<int:project_id>/simple-plan/{operation}/',
           SimplePlanningView.as_view(operation=operation))
      for operation in ('analyse', 'submit', 'approve-publish', 'reopen', 'propose-schedule', 'apply-schedule')],
]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class SimplePlanningTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='simple-owner', email='simple-owner@example.test')
        self.other = User.objects.create_user(username='simple-other', email='simple-other@example.test')
        self.reviewer = User.objects.create_user(username='simple-reviewer', email='simple-reviewer@example.test')
        module, _ = Module.objects.get_or_create(code='planning_package', defaults={'name': 'Planning'})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        grant_test_approval((self.owner, self.other, self.reviewer))
        self.enterprise = Project.objects.create(code='SIMPLE-001', name='Application launch', owner=self.owner)
        ProjectMember.objects.create(project=self.enterprise, user=self.reviewer, role='reviewer')
        self.project = PlanningProject.objects.create(
            name='Application launch', enterprise_project=self.enterprise, created_by=self.owner,
            planning_mode='manual', scope_summary='Test and launch the application.', phase='Phase 1',
            effective_date=date(2026, 11, 6), planned_end_date=date(2026, 12, 20),
        )
        self.url = f'/api/v1/planning-intelligence/projects/{self.project.pk}/simple-plan/'
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def task(self, key='task-a', **updates):
        return {'id': key, 'title': 'Test application', 'discipline': 'testing', 'owner': '',
                'effort_hours': 16, 'duration_days': 2, 'depends_on': [],
                'acceptance_criteria': 'All agreed scenarios pass', 'reviewer': '',
                'task_type': 'task', **updates}

    def read(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def save(self, tasks=None, revision=0, **extra):
        response = self.client.put(self.url, {
            'revision': revision, 'tasks': tasks if tasks is not None else [self.task()],
            'disciplines': [{'code': 'testing', 'name': 'Testing'}], **extra,
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def action(self, action, revision, **extra):
        response = self.client.post(self.url + action + '/', {'revision': revision, **extra}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_no_documents_or_ai_credentials_required_and_get_has_no_writes(self):
        state = self.read()
        self.assertEqual(state['state'], 'inputs')
        self.assertTrue(state['permissions']['can_generate_plan'])
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, {})
        with patch('apps.planning_intelligence.services.claude_client.call_claude') as ai:
            plan = self.action('analyse', 0)
        ai.assert_not_called()
        self.assertEqual(plan['state'], 'review')
        self.assertTrue(plan['permissions']['can_generate_plan'])
        self.assertEqual(plan['tasks'], [])
        self.assertFalse(self.project.intelligence_runs.exists())
        self.assertFalse(self.project.schedules.exists())
        self.assertFalse(ScheduleBaseline.objects.exists())

    def test_register_analysis_keeps_all_220_titles_and_never_auto_confirms_or_schedules(self):
        text = '--- Sheet: Sheet1 ---\nSL. NO.|DISCIPLINE|DOCUMENT TITLE\n'
        expected = []
        serial = 0
        for label, count in [('GENERAL', 34), ('HSE', 81), ('INSTRUMENTATION', 41), ('ELECTRICAL', 24), ('CIVIL', 38), ('HVAC', 2)]:
            for number in range(count):
                serial += 1
                title = f'{label} DOCUMENT {number + 1} - AREA {serial % 2}'
                text += f'{serial}|{label}|{title}\n'
                expected.append(title)
        PlanningFile.objects.create(project=self.project, category='mdr', file='test/mdr.xlsx', original_filename='MDR.xlsx',
                                    parse_status='done', extracted_text=text, uploaded_by=self.owner)
        with patch('apps.planning_intelligence.services.claude_client.call_claude') as ai:
            plan = self.action('analyse', 0)
        ai.assert_not_called()
        self.assertEqual([task['title'] for task in plan['tasks']], expected)
        self.assertTrue(all(task['duration_source'] == 'missing_source' and task['duration_days'] is None for task in plan['tasks']))
        self.assertTrue(all(task['depends_on'] == [] for task in plan['tasks']))
        self.assertEqual(len({task['id'] for task in plan['tasks']}), 220)
        intelligence = self.project.intelligence_runs.get()
        self.assertNotIn('preview_confirmation', intelligence.summary)
        self.assertFalse(intelligence.facts.filter(status='confirmed').exists())
        self.assertFalse(self.project.schedules.exists())
        self.assertFalse(self.project.schedule_bases.exists())
        self.assertFalse(self.project.generation_plans.exists())
        self.project.refresh_from_db()
        before = deepcopy(self.project.simple_planning_state)
        with CaptureQueriesContext(connection) as queries, patch('django.db.models.fields.files.FieldFile.open', side_effect=AssertionError('GET must not open source bytes')):
            reloaded = self.read()
        verification = reloaded['source_verification']
        self.assertEqual(verification['status'], 'unverified')
        self.assertEqual(verification['document_register']['status'], 'matched')
        self.assertEqual(verification['document_register']['expected_count'], 220)
        self.assertEqual(verification['document_register']['matched_count'], 220)
        self.assertEqual(verification['document_register']['missing'], [])
        self.assertEqual(verification['document_register']['extra'], [])
        self.assertEqual(verification['document_register']['changed'], [])
        self.assertFalse(verification['timing']['dates_verified'])
        self.assertEqual(verification['timing']['source_date_count'], 0)
        self.assertFalse(any(query['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for query in queries))
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, before)
        schedule_preview = self.action('propose-schedule', plan['revision'])
        self.assertEqual([task['title'] for task in schedule_preview['plan']['tasks']], expected)
        self.assertEqual([task['id'] for task in schedule_preview['plan']['tasks']], [task['id'] for task in plan['tasks']])

    def test_autosave_recalculates_weekdays_and_rejects_stale_or_cyclic_edits(self):
        plan = self.save([self.task(), self.task('task-b', title='Release review', duration_days=1, depends_on=['task-a'])])
        first, second = plan['tasks']
        self.assertEqual(first['planned_start_date'], '2026-11-06')
        self.assertEqual(first['planned_finish_date'], '2026-11-09')
        self.assertEqual(second['planned_start_date'], '2026-11-10')
        stale = self.client.put(self.url, {'revision': 0, 'tasks': [self.task()]}, format='json')
        self.assertEqual(stale.status_code, 409)
        cycle = self.client.put(self.url, {'revision': plan['revision'], 'tasks': [
            self.task(depends_on=['task-b']), self.task('task-b', depends_on=['task-a']),
        ]}, format='json')
        self.assertEqual(cycle.status_code, 400)
        self.assertEqual(self.read()['revision'], plan['revision'])

    def test_mdr_labeled_schedule_recovers_explicit_activities_and_survives_reload(self):
        from io import BytesIO
        from openpyxl import Workbook
        from ..services.parsers import extract_text_with_coverage

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = 'Delivery roadmap'
        sheet.append(['Pilot and rollout schedule'])
        sheet.append(['Phase Name', 'Module ID & Name', 'Target Completion Date', 'Duration (Days)', 'Owner / Lead'])
        sheet.append(['Pilot', 'Module 8: Sensor onboarding', '2038-06-14', 4, 'Controls lead'])
        sheet.append(['Expansion', 'Module 12: Telemetry archive', '2038-07-09', 6, 'Data team'])
        summary = workbook.create_sheet('Phase summary')
        summary.append(['Phase', 'Module Name', 'Target Window', 'Milestone Focus'])
        summary.append(['Pilot', 'Sensor onboarding', 'June 2038', 'Improve field-data capture'])
        stream = BytesIO()
        workbook.save(stream)
        text, _, _ = extract_text_with_coverage(stream, 'delivery-roadmap.xlsx')
        source = PlanningFile.objects.create(
            project=self.project, category='mdr', file='test/delivery-roadmap.xlsx',
            original_filename='delivery-roadmap.xlsx', parse_status='done',
            extracted_text=text, uploaded_by=self.owner,
        )
        with patch('apps.planning_intelligence.services.claude_client.call_claude') as ai:
            plan = self.action('analyse', 0)
        ai.assert_not_called()
        self.assertEqual([row['title'] for row in plan['tasks']],
                         ['Module 8: Sensor onboarding', 'Module 12: Telemetry archive'])
        self.assertEqual(plan['analysis_result']['status'], 'activities_created')
        self.assertEqual([row['duration_days'] for row in plan['tasks']], [4, 6])
        for task in plan['tasks']:
            self.assertEqual(task['source_references'][0]['file_id'], source.pk)
            self.assertEqual(task['source_references'][0]['locator']['sheet'], 'Delivery roadmap')
            self.assertEqual(task['depends_on'], [])
            self.assertIn('dependencies', task['source_missing_fields'])
            self.assertIn('start', task['source_missing_fields'])
            self.assertFalse(task['owner'])
        self.assertEqual(self.read()['tasks'], plan['tasks'])
        self.assertEqual(self.action('analyse', plan['revision'])['tasks'], plan['tasks'])
        self.assertEqual(self.project.intelligence_runs.count(), 1)
        self.project.refresh_from_db()
        self.assertEqual(self.project.planned_end_date, date(2026, 12, 20))
        self.assertFalse(self.project.schedules.exists())
        self.assertFalse(ScheduleBaseline.objects.exists())

    def test_mdr_label_with_only_narrative_does_not_invent_activities(self):
        PlanningFile.objects.create(
            project=self.project, category='mdr', file='test/requirements.txt',
            original_filename='requirements.txt', parse_status='done',
            extracted_text='Contractor shall keep operating records available.', uploaded_by=self.owner,
        )
        plan = self.action('analyse', 0)
        self.assertEqual(plan['tasks'], [])
        self.assertEqual(plan['analysis_result']['status'], 'no_activities')

    def test_submit_requires_one_approval_only_and_publishing_is_idempotent(self):
        plan = self.save()
        submitted = self.action('submit', plan['revision'])
        version = ScheduleVersion.objects.get(pk=submitted['version_id'])
        self.assertEqual(version.status, 'calculated')
        self.assertEqual(version.assurance_reviews.get().status, 'ready')
        self.assertEqual(ScheduleReview.objects.get().status, 'pending')
        self.assertFalse(ScheduleBaseline.objects.exists())
        repeated = self.action('submit', plan['revision'])
        self.assertEqual(repeated['version_id'], submitted['version_id'])
        published = self.action('approve-publish', submitted['revision'], name='Launch baseline')
        self.assertEqual(published['state'], 'baselined')
        self.assertEqual(ScheduleBaseline.objects.count(), 1)
        version.refresh_from_db()
        self.assertEqual(version.status, 'baselined')
        repeated = self.action('approve-publish', submitted['revision'], name='Another name')
        self.assertEqual(repeated['baseline']['id'], published['baseline']['id'])
        self.assertEqual(ScheduleBaseline.objects.get().name, 'Launch baseline')

    def test_edit_after_submission_cancels_review_and_invalidates_old_approval(self):
        saved = self.save()
        submitted = self.action('submit', saved['revision'])
        edited = self.save([self.task(duration_days=3)], revision=submitted['revision'])
        self.assertEqual(edited['state'], 'review')
        self.assertIsNone(edited['version_id'])
        self.assertEqual(ScheduleReview.objects.get(pk=submitted['review_id']).status, 'cancelled')
        response = self.client.post(self.url + 'approve-publish/', {'revision': submitted['revision']}, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertFalse(ScheduleBaseline.objects.exists())

    def test_pending_other_reviewers_cannot_be_bypassed_by_publish(self):
        saved = self.save()
        submitted = self.action('submit', saved['revision'])
        ScheduleReviewDecision.objects.create(review_id=submitted['review_id'], reviewer=self.reviewer)
        response = self.client.post(self.url + 'approve-publish/', {'revision': submitted['revision']}, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ScheduleBaseline.objects.exists())
        self.assertEqual(ScheduleReview.objects.get().status, 'pending')

    def test_external_user_cannot_read_edit_or_publish_this_project(self):
        saved = self.save()
        submitted = self.action('submit', saved['revision'])
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        response = self.client.put(self.url, {'revision': submitted['revision'], 'tasks': [self.task()]}, format='json')
        self.assertEqual(response.status_code, 404)
        response = self.client.post(self.url + 'approve-publish/', {'revision': submitted['revision']}, format='json')
        self.assertEqual(response.status_code, 404)

    def test_stale_source_inputs_block_approval_and_explicit_rebuild_is_required(self):
        saved = self.save()
        submitted = self.action('submit', saved['revision'])
        self.project.scope_summary = 'Changed scope'
        self.project.save(update_fields=['scope_summary'])
        self.assertTrue(self.read()['stale_inputs'])
        response = self.client.post(self.url + 'approve-publish/', {'revision': submitted['revision']}, format='json')
        self.assertEqual(response.status_code, 409)
        response = self.client.post(self.url + 'analyse/', {'revision': submitted['revision']}, format='json')
        self.assertEqual(response.status_code, 409)
        rebuilt = self.action('analyse', submitted['revision'], rebuild=True)
        self.assertFalse(rebuilt['stale_inputs'])
        self.assertEqual(rebuilt['tasks'][0]['id'], 'task-a')
        self.assertEqual(ScheduleReview.objects.get().status, 'cancelled')

    def test_publish_then_reopen_keeps_baseline_snapshot_immutable(self):
        saved = self.save()
        submitted = self.action('submit', saved['revision'])
        published = self.action('approve-publish', submitted['revision'])
        baseline = ScheduleBaseline.objects.get()
        snapshot = deepcopy(baseline.snapshot)
        response = self.client.put(self.url, {'revision': published['revision'], 'tasks': [self.task()]}, format='json')
        self.assertEqual(response.status_code, 409)
        reopened = self.action('reopen', published['revision'])
        self.save([self.task(title='Revised task', duration_days=4)], revision=reopened['revision'])
        baseline.refresh_from_db()
        self.assertEqual(baseline.snapshot, snapshot)
        self.assertEqual(ScheduleBaseline.objects.count(), 1)

    def test_contract_overrun_warns_and_can_be_submitted_without_extending_project_dates(self):
        saved = self.save([self.task(duration_days=100)])
        target = self.project.planned_end_date
        response = self.client.post(self.url + 'submit/', {'revision': saved['revision']}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['state'], 'submitted')
        self.assertIn('negative_float', {row['code'] for row in response.data['warnings']})
        self.assertTrue(ScheduleVersion.objects.exists())
        self.assertTrue(ScheduleReview.objects.exists())
        self.assertFalse(ScheduleBaseline.objects.exists())
        self.project.refresh_from_db()
        self.assertEqual(self.project.planned_end_date, target)

    def test_manual_draft_import_preserves_assignment_ids_and_unmanaged_work(self):
        employee = EmployeeMaster.objects.create(user=self.reviewer, employee_number='SIMPLE-EMP', employee_code='SIMPLE-EMP',
                                                emp_code='SIMPLE-EMP', email=self.reviewer.email, first_name='Reviewer',
                                                last_name='Engineer', employment_status='active', join_date=date(2026, 1, 1))
        self.project.manual_work_breakdown = {'revision': 4, 'tasks': [self.task(assignee_id=employee.user_id)],
                                              'disciplines': [{'code': 'testing', 'name': 'Testing'}]}
        self.project.save(update_fields=['manual_work_breakdown'])
        existing = ProjectTask.objects.create(project=self.enterprise, title='Existing assignment', assigned_to=self.reviewer,
                                               source_key=f'wbs:{self.project.pk}:task-a', status='in_progress', progress_percent=40,
                                               metadata={'preview_confirmed_at': f'manual:{self.project.pk}', 'source': 'work_breakdown'})
        unmanaged = ProjectTask.objects.create(project=self.enterprise, title='Other legacy assignment', assigned_to=self.reviewer,
                                                source_key=f'wbs:{self.project.pk}:other-legacy', metadata={'source': 'work_breakdown'})
        opened = self.read()
        self.assertEqual(opened['tasks'][0]['id'], 'task-a')
        self.assertEqual(opened['tasks'][0]['project_task_id'], existing.pk)
        self.assertEqual(opened['tasks'][0]['progress_percent'], 40)
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, {})
        saved = self.save([self.task(assignee_id=employee.user_id)], revision=0)
        existing.refresh_from_db()
        unmanaged.refresh_from_db()
        self.assertFalse(unmanaged.is_deleted)
        self.assertEqual(existing.status, 'in_progress')
        self.assertEqual(existing.progress_percent, 40)
        self.assertEqual(saved['tasks'][0]['project_task_id'], existing.pk)
        self.project.refresh_from_db()
        self.assertEqual(self.project.manual_work_breakdown['revision'], 4)

    def test_calculated_fields_and_read_only_history_use_real_version_data(self):
        saved = self.save()
        self.assertFalse(saved['tasks'][0]['is_critical'])
        self.assertTrue(saved['calculation_available'])
        self.assertEqual(saved['calculation_basis'], 'draft_cpm')
        submitted = self.action('submit', saved['revision'])
        version = ScheduleVersion.objects.get(pk=submitted['version_id'])
        activity = version.activities.get()
        self.assertTrue(submitted['calculation_available'])
        self.assertEqual(submitted['version_number'], version.version)
        self.assertEqual(submitted['tasks'][0]['is_critical'], activity.is_critical)
        self.assertEqual(submitted['tasks'][0]['total_float_days'], float(activity.total_float_days))
        self.assertEqual(submitted['tasks'][0]['planned_finish_date'], activity.planned_finish.isoformat())
        self.save([self.task(title='Changed draft', duration_days=4)], revision=submitted['revision'])
        response = self.client.get(self.url, {'version_id': version.pk})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['viewing_history'])
        self.assertFalse(any(response.data['permissions'][key] for key in
            ('can_edit', 'can_generate_plan', 'can_assign', 'can_submit', 'can_approve_publish', 'can_reopen')))
        # Explicit selection is permitted; merely reading this history did not
        # select it or grant permission to edit the displayed version.
        self.assertTrue(response.data['permissions']['can_select_version'])
        self.project.refresh_from_db()
        self.assertIsNone(self.project.master_schedule_version_id)
        self.assertEqual(response.data['tasks'][0]['title'], 'Test application')
        self.assertEqual(response.data['versions'][0]['version_number'], version.version)
        self.assertEqual(self.client.get(self.url, {'version_id': 99999999}).status_code, 404)

    def test_reanalysis_preserves_work_only_for_same_source_version_and_keeps_old_history(self):
        employee = EmployeeMaster.objects.create(user=self.reviewer, employee_number='REBUILD-EMP', employee_code='REBUILD-EMP',
                                                emp_code='REBUILD-EMP', email=self.reviewer.email, first_name='Reviewer',
                                                last_name='Engineer', employment_status='active', join_date=date(2026, 1, 1))
        source = PlanningFile.objects.create(project=self.project, category='mdr', file='test/rebuild.xlsx',
                                             original_filename='MDR.xlsx', parse_status='done', uploaded_by=self.owner,
                                             extracted_text='SL. NO.|DISCIPLINE|DOCUMENT TITLE\n1|ELECTRICAL|LAYOUT FAR-0\n2|ELECTRICAL|LAYOUT FAR-6')
        analysed = self.action('analyse', 0)
        tasks = deepcopy(analysed['tasks'])
        for task in tasks:
            task['assignee_id'] = employee.user_id
        saved = self.save(tasks, analysed['revision'])
        original_ids = [task['id'] for task in saved['tasks']]
        first = ProjectTask.objects.get(source_key=f'wbs:{self.project.pk}:{original_ids[0]}')
        first.status, first.progress_percent = 'in_progress', 35
        first.save(update_fields=['status', 'progress_percent'])
        second = ProjectTask.objects.get(source_key=f'wbs:{self.project.pk}:{original_ids[1]}')
        unchanged = self.action('analyse', saved['revision'], rebuild=True)
        self.assertEqual([task['id'] for task in unchanged['tasks']], original_ids)
        self.assertEqual(unchanged['tasks'][0]['project_task_id'], first.pk)
        self.assertEqual(unchanged['tasks'][0]['progress_percent'], 35)
        source.extracted_text = 'SL. NO.|DISCIPLINE|DOCUMENT TITLE\n1|ELECTRICAL|LAYOUT FAR-0'
        source.save(update_fields=['extracted_text', 'updated_at'])
        rebuilt = self.action('analyse', unchanged['revision'], rebuild=True)
        self.assertEqual(len(rebuilt['tasks']), 1)
        self.assertNotIn(rebuilt['tasks'][0]['id'], original_ids)
        self.assertIsNone(rebuilt['tasks'][0].get('project_task_id'))
        self.assertFalse(rebuilt['tasks'][0].get('assignee_id'))
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertTrue(first.is_deleted)
        self.assertEqual(first.progress_percent, 35)
        self.assertTrue(second.is_deleted)

    def test_duplicate_baseline_name_is_a_friendly_conflict_without_partial_approval(self):
        saved = self.save()
        submitted = self.action('submit', saved['revision'])
        published = self.action('approve-publish', submitted['revision'], name='Approved plan')
        reopened = self.action('reopen', published['revision'])
        submitted_again = self.action('submit', reopened['revision'])
        response = self.client.post(self.url + 'approve-publish/', {
            'revision': submitted_again['revision'], 'name': 'Approved plan',
        }, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'simple_plan_baseline_name_exists')
        self.assertEqual(ScheduleVersion.objects.get(pk=submitted_again['version_id']).status, 'calculated')
        self.assertEqual(ScheduleBaseline.objects.count(), 1)

    def test_legacy_baseline_keeps_zero_duration_milestones_and_cannot_lose_advanced_fields(self):
        saved = self.save()
        submitted = self.action('submit', saved['revision'])
        published = self.action('approve-publish', submitted['revision'])
        version = ScheduleVersion.objects.get(pk=published['version_id'])
        activity = version.activities.get()
        activity.activity_type, activity.duration_days = 'finish_milestone', 0
        activity.save(update_fields=['activity_type', 'duration_days'])
        self.project.refresh_from_db()
        self.project.simple_planning_state = {}
        self.project.save(update_fields=['simple_planning_state'])
        imported = self.read()
        self.assertTrue(imported['legacy_version_import'])
        self.assertTrue(imported['tasks'][0]['is_milestone'])
        self.assertEqual(imported['tasks'][0]['duration_days'], 0)
        self.assertFalse(imported['permissions']['can_reopen'])
        response = self.client.post(self.url + 'reopen/', {'revision': imported['revision']}, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'simple_plan_legacy_revision_required')

    def test_missing_duration_is_not_invented_from_effort_on_save(self):
        saved = self.save([self.task(duration_days=None, effort_hours=24)])
        self.assertIsNone(saved['tasks'][0]['duration_days'])
        self.assertIsNone(saved['tasks'][0]['planned_finish_date'])
        self.assertFalse(saved['calculation_available'])

    def test_analysis_source_race_rolls_back_without_claiming_new_inputs_were_analysed(self):
        from ..services.document_intelligence import run_document_intelligence

        source = PlanningFile.objects.create(project=self.project, category='mdr', file='test/race.xlsx',
                                             original_filename='MDR.xlsx', parse_status='done', uploaded_by=self.owner,
                                             extracted_text='SL. NO.|DISCIPLINE|DOCUMENT TITLE\n1|CIVIL|FOUNDATION DRAWING')

        def analyse_then_change(*args, **kwargs):
            result = run_document_intelligence(*args, **kwargs)
            source.extracted_text += '\n2|CIVIL|ADDED DRAWING'
            source.save(update_fields=['extracted_text', 'updated_at'])
            return result

        with patch('apps.planning_intelligence.services.simple_planning.run_document_intelligence', side_effect=analyse_then_change):
            response = self.client.post(self.url + 'analyse/', {'revision': 0}, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'simple_plan_inputs_changed_during_analysis')
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, {})
        self.assertFalse(self.project.intelligence_runs.exists())

    def test_schedule_managed_assignment_due_dates_move_but_explicit_legacy_dates_stay(self):
        employee = EmployeeMaster.objects.create(user=self.reviewer, employee_number='DUE-EMP', employee_code='DUE-EMP',
                                                emp_code='DUE-EMP', email=self.reviewer.email, first_name='Reviewer',
                                                last_name='Engineer', employment_status='active', join_date=date(2026, 1, 1))
        saved = self.save([
            self.task(assignee_id=employee.user_id),
            self.task('explicit-task', assignee_id=employee.user_id, due_date='2026-12-10'),
        ])
        self.assertEqual(saved['tasks'][0]['due_date_source'], 'schedule')
        managed = ProjectTask.objects.get(source_key=f'wbs:{self.project.pk}:task-a')
        explicit = ProjectTask.objects.get(source_key=f'wbs:{self.project.pk}:explicit-task')
        self.assertEqual(managed.due_date.isoformat(), '2026-11-09')
        self.assertEqual(explicit.due_date.isoformat(), '2026-12-10')
        changed = deepcopy(saved['tasks'])
        for task in changed:
            task['duration_days'] = 4
        updated = self.save(changed, saved['revision'])
        managed.refresh_from_db()
        explicit.refresh_from_db()
        self.assertEqual(managed.due_date.isoformat(), '2026-11-11')
        self.assertEqual(explicit.due_date.isoformat(), '2026-12-10')
        self.assertEqual(updated['tasks'][0]['due_date_source'], 'schedule')
        self.assertEqual(updated['tasks'][1]['due_date_source'], 'explicit')

    def test_activity_canvas_reads_real_nested_wbs_calendar_spans_and_milestones_without_writes(self):
        calendar = WorkCalendar.objects.create(project=self.project, name='Site calendar',
                                               working_weekdays=[0, 1, 2, 3, 4], hours_per_day=8)
        CalendarException.objects.create(calendar=calendar, date=date(2026, 11, 7), is_working=True, name='Site working Saturday')
        CalendarException.objects.create(calendar=calendar, date=date(2026, 11, 9), is_working=False, name='Site holiday')
        schedule = Schedule.objects.create(project=self.project, name='Site delivery', code='SITE',
                                            planned_start=date(2026, 11, 6), default_calendar=calendar)
        version = ScheduleVersion.objects.create(schedule=schedule, version=7, status='calculated', calculated_at=timezone.now())
        root = ScheduleWBSNode.objects.create(version=version, code='SITE', name='Site delivery', sort_order=0)
        phase = ScheduleWBSNode.objects.create(version=version, parent=root, code='SITE.P1', name='Phase 1', level=1, sort_order=1)
        area_a = ScheduleWBSNode.objects.create(version=version, parent=phase, code='SITE.P1.A', name='Electrical area A',
                                                discipline='electrical', level=2, sort_order=2)
        area_b = ScheduleWBSNode.objects.create(version=version, parent=phase, code='SITE.P1.B', name='Electrical area B',
                                                discipline='electrical', level=2, sort_order=3)
        source = [{'file_id': 42, 'filename': 'Register.xlsx', 'locator': {'sheet': 'Sheet1', 'row': 14, 'register_item': '9'}}]
        ScheduleActivity.objects.create(version=version, wbs_node=area_a, external_id='A1000', name='Area A design',
                                         discipline='electrical', duration_days=3, planned_start=date(2026, 11, 6),
                                         planned_finish=date(2026, 11, 10), total_float_days=0, is_critical=True, sort_order=0,
                                         metadata={'source_references': source, 'document_number': 'DOC-009', 'document_revision': 'B'})
        ScheduleActivity.objects.create(version=version, wbs_node=area_b, external_id='EL-020', name='Area B design',
                                         discipline='electrical', duration_days=2, planned_start=date(2026, 11, 10),
                                         planned_finish=date(2026, 11, 11), total_float_days=None, sort_order=1)
        ScheduleActivity.objects.create(version=version, wbs_node=area_b, external_id='MS-010', name='Issue milestone',
                                         discipline='electrical', activity_type='finish_milestone', duration_days=0,
                                         planned_start=date(2026, 11, 11), planned_finish=date(2026, 11, 11),
                                         total_float_days=0, is_critical=True, sort_order=2)
        before = list(version.activities.order_by('pk').values())
        response = self.client.get(self.url, {'version_id': version.pk})
        self.assertEqual(response.status_code, 200, response.data)
        plan = response.data
        self.assertEqual(plan['project']['code'], 'SIMPLE-001')
        self.assertEqual(plan['project']['phase'], 'Phase 1')
        self.assertEqual(plan['calendar']['id'], calendar.pk)
        self.assertEqual(plan['calendar']['working_weekdays'], [0, 1, 2, 3, 4])
        self.assertEqual(plan['calendar']['exceptions'][0]['date'], '2026-11-07')
        nodes = {node['id']: node for node in plan['wbs_nodes']}
        self.assertEqual(nodes[area_a.pk]['parent_id'], phase.pk)
        self.assertEqual(nodes[area_b.pk]['parent_id'], phase.pk)
        self.assertEqual(nodes[phase.pk]['parent_id'], root.pk)
        self.assertEqual(nodes[root.pk]['summary']['duration_days'], 4)
        self.assertEqual(nodes[area_a.pk]['summary']['duration_days'], 3)
        self.assertEqual(nodes[root.pk]['summary']['task_count'], 3)
        self.assertIsNone(nodes[root.pk]['summary']['total_float_days'])
        self.assertEqual(nodes[area_a.pk]['summary']['total_float_days'], 0)
        self.assertEqual(plan['project_summary']['duration_days'], 4)
        self.assertEqual(len(plan['disciplines']), 1)
        first, second, milestone = plan['tasks']
        self.assertEqual(first['activity_code'], 'A1000')
        self.assertEqual(first['external_id'], 'A1000')
        self.assertEqual(first['wbs_node_id'], area_a.pk)
        self.assertEqual(first['document_number'], 'DOC-009')
        self.assertEqual(first['document_revision'], 'B')
        self.assertEqual(first['source_references'], source)
        self.assertEqual(first['total_float_days'], 0)
        self.assertIsNone(second['total_float_days'])
        self.assertTrue(milestone['is_milestone'])
        self.assertEqual(milestone['duration_days'], 0)
        self.assertEqual(milestone['activity_code'], 'MS-010')
        self.assertEqual(list(version.activities.order_by('pk').values()), before)
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, {})

    def test_activity_canvas_draft_uses_source_document_ids_and_parallel_calendar_span(self):
        source = [{'file_id': 17, 'locator': {'register_item': '11', 'sheet': 'Register'}}]
        self.project.manual_work_breakdown = {
            'revision': 0, 'disciplines': [{'code': 'testing', 'name': 'Application testing'}],
            'tasks': [self.task(document_number='QA-011', document_revision='C', source_references=source),
                      self.task('task-b', title='Parallel test', document_number='')],
        }
        self.project.save(update_fields=['manual_work_breakdown'])
        before = deepcopy(self.project.manual_work_breakdown)
        plan = self.read()
        first, second = plan['tasks']
        self.assertEqual(first['activity_code'], 'QA-011')
        self.assertEqual(first['activity_code_source'], 'document_number')
        self.assertEqual(first['source_references'], source)
        self.assertEqual(second['activity_code'], '1.2')
        self.assertIsNone(first['external_id'])
        self.assertIsNotNone(first['total_float_days'])
        self.assertFalse(first['is_critical'])
        self.assertEqual(first['wbs_node_id'], 'draft:testing')
        self.assertEqual(plan['wbs_nodes'][0]['name'], 'Application testing')
        self.assertTrue(plan['wbs_nodes'][0]['is_derived'])
        self.assertEqual(plan['project_summary']['duration_days'], 2)
        self.assertEqual(plan['project_summary']['total_float_days'], first['total_float_days'])
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, {})
        self.assertEqual(self.project.manual_work_breakdown, before)
        self.assertFalse(self.project.schedules.exists())

    def test_activity_canvas_hides_internal_uuid_display_and_keeps_uncalculated_float_unknown(self):
        schedule = Schedule.objects.create(project=self.project, name='Existing plan', code='EXISTING', planned_start=date(2026, 11, 6))
        version = ScheduleVersion.objects.create(schedule=schedule, version=1)
        node = ScheduleWBSNode.objects.create(version=version, code='2.0', name='Testing', discipline='testing')
        internal_id = 'task-123456781234123412341234567890ab'
        activity = ScheduleActivity.objects.create(version=version, wbs_node=node, external_id=internal_id,
                                                   name='Release', discipline='testing', activity_type='finish_milestone',
                                                   duration_days=0, total_float_days=0, planned_start=date(2026, 11, 6),
                                                   planned_finish=date(2026, 11, 6))
        plan = self.client.get(self.url, {'version_id': version.pk}).data
        self.assertEqual(plan['tasks'][0]['external_id'], internal_id)
        self.assertEqual(plan['tasks'][0]['activity_code'], '2.1')
        self.assertEqual(plan['tasks'][0]['activity_code_source'], 'schedule_wbs')
        self.assertEqual(plan['tasks'][0]['duration_days'], 0)
        self.assertIsNone(plan['tasks'][0]['total_float_days'])
        self.assertIsNone(plan['tasks'][0]['is_critical'])
        self.assertEqual(plan['wbs_nodes'][0]['summary']['duration_days'], 0)
        activity.metadata = {'document_number': 'REL-001'}
        activity.save(update_fields=['metadata'])
        plan = self.client.get(self.url, {'version_id': version.pk}).data
        self.assertEqual(plan['tasks'][0]['activity_code'], 'REL-001')
        self.assertEqual(plan['tasks'][0]['activity_code_source'], 'document_number')

    def proposal_draft(self, extra_tasks=None):
        self.project.effective_date = date(2026, 1, 6)
        self.project.planned_end_date = date(2026, 9, 4)
        tasks = [
            self.task('basis', title='Electrical Design Basis', discipline='electrical', effort_hours=None, duration_days=5, duration_source='proposed'),
            self.task('drawing-a', title='ELECTRICAL LAYOUT - FAR-0', discipline='electrical', effort_hours=None, duration_days=5, duration_source='proposed'),
            self.task('drawing-b', title='ELECTRICAL LAYOUT - FAR-6', discipline='electrical', effort_hours=None, duration_days=5, duration_source='proposed'),
            self.task('closeout', title='PROJECT CLOSE-OUT REPORT', discipline='general', effort_hours=None, duration_days=5, duration_source='proposed'),
            self.task('weekly', title='WEEKLY PROGRESS REPORT', discipline='general', effort_hours=None, duration_days=5, duration_source='proposed'),
        ]
        tasks.extend(extra_tasks or [])
        tasks[1]['source_references'] = [{'file_id': 88, 'locator': {'sheet': 'MDR', 'row': 6, 'register_item': '3'}}]
        self.project.manual_work_breakdown = {'revision': 0, 'tasks': tasks,
                                             'disciplines': [{'code': 'electrical', 'name': 'ELECTRICAL'}, {'code': 'general', 'name': 'GENERAL'}]}
        self.project.save(update_fields=['effective_date', 'planned_end_date', 'manual_work_breakdown'])
        return tasks

    def test_schedule_proposal_is_read_only_preserves_rows_and_keeps_parallel_tasks(self):
        original = self.proposal_draft()
        self.assertEqual(self.read()['scheduling_status']['state'], 'unsequenced')
        before = deepcopy(self.project.manual_work_breakdown)
        with CaptureQueriesContext(connection) as queries:
            proposal = self.action('propose-schedule', 0)
        writes = [query['sql'] for query in queries.captured_queries if query['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))]
        self.assertEqual(writes, [])
        plan = proposal['plan']
        self.assertTrue(plan['is_schedule_preview'])
        self.assertFalse(plan['permissions']['can_edit'])
        self.assertEqual([task['id'] for task in plan['tasks']], [task['id'] for task in original])
        self.assertEqual([task['title'] for task in plan['tasks']], [task['title'] for task in original])
        rows = {task['id']: task for task in plan['tasks']}
        self.assertEqual(rows['drawing-a']['source_references'], original[1]['source_references'])
        self.assertEqual(rows['drawing-a']['depends_on'], [])
        self.assertEqual(rows['drawing-b']['depends_on'], [])
        self.assertEqual(rows['drawing-a']['planned_start_date'], rows['drawing-b']['planned_start_date'])
        self.assertIsNone(rows['closeout']['planned_finish_date'])
        self.assertIsNone(rows['weekly']['planned_finish_date'])
        self.assertIsNone(proposal['proposal']['finish_date'])
        self.assertGreater(proposal['proposal']['changed_count'], 0)
        self.assertTrue(all(task['is_critical'] is None and task['total_float_days'] is None for task in plan['tasks']))
        self.assertIsNone(plan['calculation_basis'])
        self.assertTrue(all(task['duration_days'] is None for task in plan['tasks']))
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, {})
        self.assertEqual(self.project.manual_work_breakdown, before)
        self.assertFalse(self.project.schedules.exists())

    def test_schedule_proposal_apply_is_explicit_idempotent_and_preserves_manual_fields(self):
        manual = self.task('manual', title='Planner retained task', discipline='electrical', duration_days=5,
                           duration_source='planner', planned_start_date='2026-08-20', depends_on=['drawing-a'],
                           due_date='2026-12-10', effort_hours=40)
        effort_based = self.task('effort', title='Effort estimate', duration_days=3, duration_source='proposed', effort_hours=24)
        self.proposal_draft([manual, effort_based])
        preview = self.action('propose-schedule', 0)
        applied = self.action('apply-schedule', 0, proposal_token=preview['proposal']['token'])
        rows = {task['id']: task for task in applied['tasks']}
        self.assertEqual(rows['manual']['duration_days'], 5)
        self.assertEqual(rows['manual']['duration_source'], 'planner')
        self.assertIsNone(rows['manual']['planned_start_date'])  # Unknown predecessor prevents a computed date.
        self.assertEqual(rows['manual']['depends_on'], ['drawing-a'])
        self.assertEqual(rows['manual']['due_date'], '2026-12-10')
        self.assertIsNone(rows['effort']['duration_days'])
        self.assertEqual(applied['revision'], 1)
        self.assertIsNone(applied['version_id'])
        self.assertFalse(ScheduleBaseline.objects.exists())
        repeated = self.action('apply-schedule', 0, proposal_token=preview['proposal']['token'])
        self.assertEqual(repeated['revision'], 1)
        self.assertEqual(repeated['tasks'], applied['tasks'])
        # Ordinary task payloads omit server-owned proposal rationale.
        edited = deepcopy(applied['tasks'])
        for task in edited:
            for field in ('schedule_rationale', 'dependency_rationales', 'schedule_phase', 'schedule_generated_fields'):
                task.pop(field, None)
        saved = self.save(edited, revision=1)
        self.assertEqual(saved['tasks'][1]['duration_review_status'], 'missing_source')
        self.assertEqual(saved['tasks'][1]['duration_evidence'], None)

    def test_schedule_proposal_template_and_calendar_changes_invalidate_token(self):
        self.proposal_draft()
        template = WorkflowTemplate.objects.create(project=self.project, code='DRAWING', name='Drawing', status='active', version=1)
        stage = WorkflowStage.objects.create(template=template, sequence=1, code='DESIGN', name='Design', duration_days=7)
        calendar = WorkCalendar.objects.create(project=self.project, name='Default', working_weekdays=[0, 1, 2, 3, 4], is_default=True)
        preview = self.action('propose-schedule', 0)
        self.assertIsNone(next(task for task in preview['plan']['tasks'] if task['id'] == 'drawing-a')['duration_days'])
        stage.duration_days = 8
        stage.save(update_fields=['duration_days'])
        response = self.client.post(self.url + 'apply-schedule/', {'revision': 0, 'proposal_token': preview['proposal']['token']}, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'simple_plan_proposal_stale')
        fresh = self.action('propose-schedule', 0)
        CalendarException.objects.create(calendar=calendar, date=date(2026, 1, 7), is_working=False, name='Holiday')
        response = self.client.post(self.url + 'apply-schedule/', {'revision': 0, 'proposal_token': fresh['proposal']['token']}, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'simple_plan_proposal_stale')
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, {})

    def test_schedule_proposal_source_constraints_remain_relative_with_unknown_award(self):
        self.proposal_draft()
        PlanningFile.objects.create(project=self.project, category='sow', original_filename='Scope.pdf', file='test/scope.pdf',
                                    parse_status='done', uploaded_by=self.owner, extracted_text=(
                                        'Allow two (02) weeks (10 working days) time duration required\n'
                                        'by COMPANY for the review of submitted technical documents.\n'
                                        'Complete all FEED SERVICES within 07 months (28 weeks) from\n'
                                        'the effective date of award.\nWeeks from\nSr.No Description\nEffective Date\n'
                                        'Submit draft PDR and EPC package\n1. 24\nalong with cost estimate.\n'))
        preview = self.action('propose-schedule', 0)
        constraints = preview['proposal']['source_constraints']
        self.assertIn(10, [row['value'] for row in constraints if row['kind'] == 'review_days'])
        self.assertEqual({row['value'] for row in constraints if row['kind'] == 'relative_weeks'}, {28})
        self.assertTrue(all(row['anchor_status'] == 'unconfirmed' for row in constraints if row['kind'] == 'relative_weeks'))
        self.assertTrue(all(row['source_references'][0]['locator']['line'] > 0 for row in constraints))
        self.assertTrue(all(row['anchor_status'] == 'unconfirmed' for row in constraints if row['kind'] == 'relative_weeks'))
        self.assertEqual(preview['plan']['project']['end_date'], '2026-09-04')
        self.assertFalse(any('2026-07-21' in row['message'] for row in constraints))

    def test_schedule_proposal_rejects_cycles_history_bad_signature_and_revision_changes(self):
        self.proposal_draft()
        preview = self.action('propose-schedule', 0)
        invalid = self.client.post(self.url + 'apply-schedule/', {'revision': 0, 'proposal_token': preview['proposal']['token'] + 'x'}, format='json')
        self.assertEqual(invalid.status_code, 409)
        history = self.client.post(self.url + 'propose-schedule/?version_id=7', {'revision': 0}, format='json')
        self.assertEqual(history.status_code, 409)
        self.save([self.task()], revision=0)
        stale = self.client.post(self.url + 'apply-schedule/', {'revision': 0, 'proposal_token': preview['proposal']['token']}, format='json')
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.data['code'], 'simple_plan_revision_conflict')
        self.project.refresh_from_db()
        state = self.project.simple_planning_state
        state['tasks'] = [self.task('a', depends_on=['b']), self.task('b', depends_on=['a'])]
        self.project.save(update_fields=['simple_planning_state'])
        cycle = self.client.post(self.url + 'propose-schedule/', {'revision': 1}, format='json')
        self.assertEqual(cycle.status_code, 409)
        self.assertIn('cycle', cycle.data['error'])

    def test_schedule_proposal_reports_actual_finish_and_preserves_overrunning_manual_start(self):
        self.proposal_draft([self.task('late', title='Late manual work', duration_days=5,
                                       duration_source='planner', planned_start_date='2026-09-07')])
        preview = self.action('propose-schedule', 0)
        self.assertEqual(preview['proposal']['finish_date'], '2026-09-11')
        self.assertEqual(preview['proposal']['target_finish_date'], '2026-09-04')
        self.assertTrue(any('after the registered target' in warning for warning in preview['proposal']['warnings']))
        self.assertEqual(next(task for task in preview['plan']['tasks'] if task['id'] == 'late')['planned_start_date'], '2026-09-07')

    def test_schedule_proposal_does_not_guess_audit_windows_from_percentages_or_row_order(self):
        self.proposal_draft([self.task(f'audit-{value}', title=f'HSE AUDIT ({value}%)', discipline='hse', effort_hours=None,
                                       duration_days=5, duration_source='proposed') for value in (30, 60, 90)])
        first = self.action('propose-schedule', 0)
        dates = [task['planned_finish_date'] for task in first['plan']['tasks'] if task['id'].startswith('audit-')]
        self.assertTrue(all(value is None for value in dates))
        self.project.manual_work_breakdown['tasks'].reverse()
        self.project.save(update_fields=['manual_work_breakdown'])
        second = self.action('propose-schedule', 0)
        by_id = lambda response: {task['id']: (task['planned_start_date'], task['planned_finish_date'], task['depends_on']) for task in response['plan']['tasks']}
        self.assertEqual(by_id(first), by_id(second))

    def test_schedule_proposal_does_not_invent_setup_sequence_from_titles(self):
        self.proposal_draft([self.task(f'setup-{index}', title=title, discipline='general', effort_hours=None,
                                       duration_days=5, duration_source='proposed') for index, title in enumerate([
                                           'MASTER DELIVERABLE REGISTER', 'Engineering Deliverable Register',
                                           'EDDR', 'PLANNING PACKAGE', 'DOCUMENT NUMBERING PROCEDURE'])])
        preview = self.action('propose-schedule', 0)
        rows = {task['id']: task for task in preview['plan']['tasks']}
        for index in range(5):
            self.assertIsNone(rows[f'setup-{index}']['planned_start_date'])
            self.assertIsNone(rows[f'setup-{index}']['duration_days'])
            self.assertEqual(rows[f'setup-{index}']['depends_on'], [])

    def test_schedule_proposal_preserves_started_employee_task_dates_duration_and_progress(self):
        self.proposal_draft()
        employee = EmployeeMaster.objects.create(user=self.reviewer, employee_number='PROP-EMP', employee_code='PROP-EMP',
                                                emp_code='PROP-EMP', email=self.reviewer.email, first_name='Reviewer',
                                                last_name='Engineer', employment_status='active', join_date=date(2026, 1, 1))
        tasks = self.read()['tasks']
        tasks[1]['assignee_id'] = employee.user_id
        saved = self.save(tasks)
        assigned = ProjectTask.objects.get(source_key=f'wbs:{self.project.pk}:drawing-a')
        assigned.status, assigned.progress_percent = 'in_progress', 30
        assigned.save(update_fields=['status', 'progress_percent'])
        previous_due = assigned.due_date
        preview = self.action('propose-schedule', saved['revision'])
        applied = self.action('apply-schedule', saved['revision'], proposal_token=preview['proposal']['token'])
        assigned.refresh_from_db()
        task = next(row for row in applied['tasks'] if row['id'] == 'drawing-a')
        self.assertEqual(task['project_task_id'], assigned.pk)
        self.assertEqual(task['progress_percent'], 30)
        self.assertEqual(assigned.status, 'in_progress')
        self.assertEqual(assigned.due_date, previous_due)
        self.assertEqual(task['duration_days'], tasks[1]['duration_days'])
        self.assertEqual(task['planned_start_date'], tasks[1]['planned_start_date'])
        self.assertEqual(assigned.due_date.isoformat(), task['planned_finish_date'])

    def test_schedule_proposal_apply_rolls_back_if_sources_change_during_assignment_sync(self):
        from ..services.work_assignments import sync_workspace_assignments
        self.proposal_draft()
        source = PlanningFile.objects.create(project=self.project, category='other', original_filename='Notes.txt',
                                             file='test/notes.txt', parse_status='done', extracted_text='First notes', uploaded_by=self.owner)
        preview = self.action('propose-schedule', 0)

        def sync_then_change(*args, **kwargs):
            result = sync_workspace_assignments(*args, **kwargs)
            source.extracted_text = 'Changed notes'
            source.save(update_fields=['extracted_text'])
            return result

        with patch('apps.planning_intelligence.services.simple_planning.sync_workspace_assignments', side_effect=sync_then_change):
            response = self.client.post(self.url + 'apply-schedule/', {'revision': 0, 'proposal_token': preview['proposal']['token']}, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'simple_plan_proposal_stale')
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, {})
        source.refresh_from_db()
        self.assertEqual(source.extracted_text, 'First notes')

    def test_applied_proposal_review_notes_reload_and_follow_current_dates_without_history_bleed(self):
        self.proposal_draft([
            self.task('late-a', title='First late task', duration_days=5, duration_source='planner', planned_start_date='2026-09-07'),
            self.task('late-b', title='Second late task', duration_days=5, duration_source='planner', planned_start_date='2026-09-14'),
        ])
        PlanningFile.objects.create(project=self.project, category='sow', original_filename='Scope.pdf', file='test/scope.pdf',
                                    parse_status='done', uploaded_by=self.owner,
                                    extracted_text='FEED completion within 28 weeks from the effective date of award.')
        preview = self.action('propose-schedule', 0)
        applied = self.action('apply-schedule', 0, proposal_token=preview['proposal']['token'])

        def warnings(plan):
            return {item['code']: item for item in plan['warnings'] if isinstance(item, dict)}

        self.assertEqual(warnings(applied)['contract_finish_overrun']['task_count'], 2)
        self.assertIn('schedule_award_anchor_unconfirmed', warnings(applied))
        self.assertTrue(any(item.get('code', '').startswith('schedule_proposal_assumption_') for item in applied['assumptions']))
        self.project.refresh_from_db()
        self.project.simple_planning_state['warnings'] = [{'code': 'existing_assurance', 'message': 'Existing assurance warning'}]
        self.project.save(update_fields=['simple_planning_state'])
        reloaded = self.read()
        self.assertEqual(warnings(reloaded)['contract_finish_overrun']['task_count'], 2)
        self.assertIn('existing_assurance', warnings(reloaded))
        edited = deepcopy(reloaded['tasks'])
        next(task for task in edited if task['id'] == 'late-a')['planned_start_date'] = '2026-08-03'
        changed = self.save(edited, revision=reloaded['revision'])
        self.assertEqual(warnings(changed)['contract_finish_overrun']['task_count'], 1)
        self.assertEqual(warnings(changed)['contract_finish_overrun']['task_ids'], ['late-b'])
        fixed = deepcopy(changed['tasks'])
        next(task for task in fixed if task['id'] == 'late-b')['planned_start_date'] = '2026-08-10'
        final = self.save(fixed, revision=changed['revision'])
        self.assertNotIn('contract_finish_overrun', warnings(final))
        self.assertNotIn('contract_finish_overrun', warnings(self.read()))
        self.assertIn('schedule_award_anchor_unconfirmed', warnings(final))
        schedule = Schedule.objects.create(project=self.project, name='Historical plan', code='HISTORY', planned_start=date(2026, 1, 6))
        version = ScheduleVersion.objects.create(schedule=schedule, version=1)
        ScheduleActivity.objects.create(version=version, external_id='HIST-01', name='Earlier activity', duration_days=1,
                                         planned_start=date(2026, 1, 6), planned_finish=date(2026, 1, 6))
        historical = self.client.get(self.url, {'version_id': version.pk})
        self.assertEqual(historical.status_code, 200)
        self.assertNotIn('schedule_proposal', historical.data)
        self.assertNotIn('schedule_award_anchor_unconfirmed', warnings(historical.data))
        self.assertFalse(any(item.get('code', '').startswith('schedule_proposal_assumption_') for item in historical.data['assumptions']))

    def _assert_reference_upload_allows_review_but_blocks_publication(self, category, filename, text):
        source = PlanningFile.objects.create(
            project=self.project, category=category, original_filename=filename,
            file='test/reference-without-storage', parse_status='done', extracted_text=text,
        )
        saved = self.save()
        self.assertEqual(saved['source_verification']['schedule_reference']['status'], 'not_imported')
        self.assertEqual(saved['source_verification']['schedule_reference']['files'][0]['id'], source.pk)
        self.project.refresh_from_db()
        before = deepcopy(self.project.simple_planning_state)
        audit_count = self.project.audit_events.count()
        for operation in ('analyse', 'propose-schedule', 'submit'):
            with self.subTest(operation=operation), CaptureQueriesContext(connection) as queries:
                response = self.client.post(self.url + operation + '/', {
                    'revision': saved['revision'],
                }, format='json')
            if operation == 'submit':
                self.assertEqual(response.status_code, 409, response.data)
                self.assertEqual(response.data['code'], 'simple_plan_incomplete')
                self.assertIn('reference_schedule_not_imported', [row['code'] for row in response.data['blockers']])
            else:
                self.assertEqual(response.status_code, 200, response.data)
                review = response.data['plan'] if operation == 'propose-schedule' else response.data
                self.assertEqual({row['id'] for row in review['tasks']}, {row['id'] for row in saved['tasks']})
                self.assertEqual(review['source_verification']['schedule_reference']['status'], 'not_imported')
                self.assertFalse(review['permissions']['can_submit'])
            self.assertFalse(any(query['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for query in queries))
            self.project.refresh_from_db()
            self.assertEqual(self.project.simple_planning_state, before)
            self.assertEqual(self.project.audit_events.count(), audit_count)
            self.assertFalse(self.project.intelligence_runs.exists())
            self.assertFalse(self.project.schedules.exists())
            self.assertFalse(self.project.schedule_bases.exists())

        # A requested rebuild of an unsupported source is not permission to
        # delete existing work merely because extraction recovered no rows.
        # Analysis may record evidence diagnostics, or decline the replacement;
        # either outcome must retain the actual saved task values and identity.
        with patch('apps.planning_intelligence.services.claude_client.call_claude', return_value=None):
            rebuilt = self.client.post(self.url + 'analyse/', {
                'revision': saved['revision'], 'rebuild': True,
            }, format='json')
        self.assertIn(rebuilt.status_code, (200, 409), rebuilt.data)
        self.project.refresh_from_db()
        retained = self.project.simple_planning_state['tasks']
        self.assertEqual({row['id'] for row in retained}, {row['id'] for row in before['tasks']})
        self.assertEqual([(row['title'], row['duration_days'], row['duration_source']) for row in retained],
                         [(row['title'], row['duration_days'], row['duration_source']) for row in before['tasks']])
        self.assertFalse(self.project.schedules.exists())
        self.assertFalse(ScheduleBaseline.objects.filter(schedule__project=self.project).exists())

    def test_reference_schedule_category_allows_review_but_blocks_unverified_submission(self):
        self._assert_reference_upload_allows_review_but_blocks_publication('reference_schedule', 'Approved schedule.pdf', 'Original schedule report.')

    def test_native_reference_schedule_allows_review_but_blocks_unverified_submission(self):
        self._assert_reference_upload_allows_review_but_blocks_publication('other', 'Original schedule.XER', 'ERMHDR\t8.4\n%T\tTASK\n')

    def test_embedded_schedule_columns_allow_review_but_block_unverified_submission(self):
        self._assert_reference_upload_allows_review_but_blocks_publication(
            'sow', 'Scope with schedule appendix.pdf',
            'Appendix\nActivity ID\nActivity Name\nOriginal Duration\nStart\nFinish\nA010\nMobilization\n',
        )

    def test_rebuild_replaces_previous_source_values_without_mixing_old_values_and_new_evidence(self):
        from ..services.simple_planning import _retain_saved_work
        reference = {'file_id': 12, 'locator': {'line': 3}}
        previous = self.task('source-task', title='Test equipment', duration_days=5,
                             duration_source='source_document', source_activity_id='ACT-2',
                             source_references=[reference], source_title='Test equipment',
                             depends_on=['before'],
                             dependency_details=[{'task_id': 'before', 'type': 'FS', 'lag_days': 0, 'source': 'source_document'}],
                             duration_evidence={'values': {'original_duration_days': 5}})
        current = self.task('source-task', title='Test equipment', duration_days=3,
                            duration_source='source_document', source_activity_id='ACT-2',
                            source_references=[reference], depends_on=['after'],
                            dependency_details=[{'task_id': 'after', 'type': 'SS', 'lag_days': 2, 'source': 'source_document'}],
                            duration_evidence={'values': {'original_duration_days': 3}})
        proposed = [self.task('before'), self.task('after'), current]
        _retain_saved_work([previous], proposed)
        self.assertEqual(current['duration_days'], 3)
        self.assertEqual(current['duration_evidence']['values']['original_duration_days'], 3)
        self.assertEqual(current['depends_on'], ['after'])
        self.assertEqual(current['dependency_details'][0]['task_id'], 'after')

    def test_document_milestone_missing_duration_remains_unspecified_when_seeded(self):
        from ..services.simple_planning import _seed_task
        task = _seed_task({'id': 'source-milestone', 'title': 'Acceptance', 'is_milestone': True,
                           'evidence_policy': 'document_driven', 'duration_days': None,
                           'duration_source': 'missing_source'})
        self.assertIsNone(task['duration_days'])
        self.assertEqual(task['duration_source'], 'missing_source')

    def test_started_work_retains_its_value_and_old_evidence_with_new_source_conflict(self):
        from ..services.simple_planning import _retain_saved_work
        old = self.task('source-task', title='Inspect equipment', duration_days=5,
                        duration_source='source_document', status='in_progress',
                        duration_evidence={'values': {'original_duration_days': 5}})
        current = self.task('source-task', title='Inspect equipment', duration_days=3,
                            duration_source='source_document', evidence_policy='document_driven',
                            duration_evidence={'values': {'original_duration_days': 3}})
        _retain_saved_work([old], [current])
        self.assertEqual(current['duration_days'], 5)
        self.assertEqual(current['duration_evidence']['values']['original_duration_days'], 5)
        self.assertEqual(current['duration_comparison_evidence']['values']['original_duration_days'], 3)
        self.assertEqual(current['duration_review_status'], 'conflict')

    def test_rebuild_does_not_retain_known_template_guesses_over_current_source_facts(self):
        from ..services.simple_planning import _retain_saved_work
        for old_source in ('proposed', 'template', 'default'):
            with self.subTest(old_source=old_source):
                old = self.task('source-task', title='Inspect equipment', duration_days=9,
                                duration_source=old_source, depends_on=['before'],
                                schedule_generated_fields=['duration_days', 'depends_on'],
                                dependency_details=[{'task_id': 'before', 'type': 'FS', 'lag_days': 0,
                                                     'source': 'deliverable_sequence', 'status': 'proposed'}])
                current = self.task('source-task', title='Inspect equipment', duration_days=3,
                                    duration_source='source_document', evidence_policy='document_driven',
                                    depends_on=['after'],
                                    dependency_details=[{'task_id': 'after', 'type': 'SS', 'lag_days': 2,
                                                         'source': 'source_document'}],
                                    duration_evidence={'values': {'original_duration_days': 3}})
                proposed = [self.task('before'), self.task('after'), current]
                _retain_saved_work([old], proposed)
                self.assertEqual(current['duration_days'], 3)
                self.assertEqual(current['duration_source'], 'source_document')
                self.assertEqual(current['depends_on'], ['after'])
                self.assertEqual(current['dependency_details'][0]['task_id'], 'after')

    def test_reference_schedule_blocks_current_simple_direct_approval_but_not_independent_manual_version(self):
        from ..services.cpm import calculate_schedule_version
        from ..services.schedule_approval import (
            ScheduleApprovalError, approve_schedule_version, can_approve_schedule, can_baseline_schedule,
        )
        from ..services.trustworthy_scheduling import approve_schedule_assurance, run_schedule_assurance

        saved = self.save()
        submitted = self.action('submit', saved['revision'], approver_id=self.owner.pk)
        current = ScheduleVersion.objects.get(pk=submitted['version_id'])
        self.assertTrue(current.activities.exists())
        self.assertTrue(all(activity.metadata.get('planning_workflow') == 'simple_planning'
                            for activity in current.activities.all()))
        PlanningFile.objects.create(project=self.project, category='reference_schedule', file='test/approved.xer',
                                    original_filename='Approved schedule.xer', parse_status='done', extracted_text='ERMHDR')
        self.project.refresh_from_db()
        before = deepcopy(self.project.simple_planning_state)
        audit_count = self.project.audit_events.count()
        with self.assertRaises(ScheduleApprovalError) as raised:
            approve_schedule_version(current, self.owner)
        self.assertEqual(raised.exception.payload['code'], 'reference_schedule_not_imported')
        current.refresh_from_db()
        self.assertEqual(current.status, 'calculated')
        self.assertFalse(can_approve_schedule(current, self.owner))
        published = self.client.post(self.url + 'approve-publish/', {'revision': submitted['revision']}, format='json')
        self.assertEqual(published.status_code, 409, published.data)
        self.assertIn('reference_schedule_not_imported', [row['code'] for row in published.data['blockers']])
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, before)
        self.assertEqual(self.project.audit_events.count(), audit_count)
        self.assertFalse(ScheduleBaseline.objects.filter(schedule__project=self.project).exists())

        # Editing can clear the state's active version; the explicit marker must
        # retain the import guard on direct approval of its existing activities.
        self.project.simple_planning_state = {**before, 'version_id': None}
        self.project.save(update_fields=['simple_planning_state'])
        with self.assertRaises(ScheduleApprovalError) as marked:
            approve_schedule_version(current, self.owner)
        self.assertEqual(marked.exception.payload['code'], 'reference_schedule_not_imported')
        current.refresh_from_db()
        self.assertEqual(current.status, 'calculated')
        self.project.simple_planning_state = before
        self.project.save(update_fields=['simple_planning_state'])

        # This separate schedule was authored directly; it is not a simple-plan import.
        schedule = Schedule.objects.create(
            project=self.project, code='INDEPENDENT', name='Manually developed schedule',
            planned_start=self.project.effective_date, default_calendar=current.schedule.default_calendar,
        )
        independent = ScheduleVersion.objects.create(schedule=schedule, version=1, created_by=self.owner)
        ScheduleActivity.objects.create(version=independent, external_id='MAN-01', name='Agreed manual activity',
                                         duration_days=2, calendar=schedule.default_calendar)
        calculate_schedule_version(independent, requested_by=self.owner)
        run_schedule_assurance(independent, requested_by=self.owner)
        approve_schedule_assurance(independent, self.owner)
        independent.refresh_from_db()
        self.assertTrue(can_approve_schedule(independent, self.owner))
        approved = approve_schedule_version(independent, self.owner)
        self.assertEqual(approved.status, 'approved')
        self.assertTrue(can_baseline_schedule(approved, self.owner))

    @override_settings(ROOT_URLCONF='config.urls_test')
    def test_ordinary_baseline_endpoint_preserves_simple_source_guard_and_manual_baselining(self):
        from ..services.cpm import calculate_schedule_version
        from ..services.schedule_approval import approve_schedule_version, decide_schedule_review
        from ..services.trustworthy_scheduling import approve_schedule_assurance, run_schedule_assurance

        saved = self.save()
        submitted = self.action('submit', saved['revision'], approver_id=self.owner.pk)
        version = ScheduleVersion.objects.get(pk=submitted['version_id'])
        approve_schedule_assurance(version, self.owner)
        decide_schedule_review(version, submitted['review']['id'], self.owner, decision='approved')
        version.refresh_from_db()
        self.assertEqual(version.status, 'approved')
        self.assertFalse(ScheduleBaseline.objects.filter(schedule__project=self.project).exists())
        self.assertTrue(version.activities.filter(metadata__planning_workflow='simple_planning').exists())
        PlanningFile.objects.create(project=self.project, category='reference_schedule', file='test/original.xml',
                                    original_filename='Original schedule.xml', parse_status='done', extracted_text='<Project/>')
        audit_count = self.project.audit_events.count()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.post(
                f'/api/v1/planning-intelligence/schedule-versions/{version.pk}/baseline/',
                {'name': 'Must not publish an unverified import'}, format='json',
            )
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'reference_schedule_not_imported')
        self.assertFalse(any(query['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for query in queries))
        self.assertFalse(ScheduleBaseline.objects.filter(schedule__project=self.project).exists())
        self.assertEqual(self.project.audit_events.count(), audit_count)
        version.refresh_from_db()
        self.assertEqual(version.status, 'approved')

        schedule = Schedule.objects.create(project=self.project, code='MANUAL-BASELINE', name='Manual control plan',
                                           planned_start=self.project.effective_date, default_calendar=version.schedule.default_calendar)
        manual = ScheduleVersion.objects.create(schedule=schedule, version=1, created_by=self.owner)
        ScheduleActivity.objects.create(version=manual, external_id='MANUAL-01', name='Manually agreed task',
                                         duration_days=2, calendar=schedule.default_calendar)
        calculate_schedule_version(manual, requested_by=self.owner)
        run_schedule_assurance(manual, requested_by=self.owner)
        approve_schedule_assurance(manual, self.owner)
        approve_schedule_version(manual, self.owner)
        response = self.client.post(
            f'/api/v1/planning-intelligence/schedule-versions/{manual.pk}/baseline/',
            {'name': 'Independent manual baseline'}, format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(ScheduleBaseline.objects.filter(source_version=manual).exists())
        self.assertFalse(ScheduleBaseline.objects.filter(source_version=version).exists())
