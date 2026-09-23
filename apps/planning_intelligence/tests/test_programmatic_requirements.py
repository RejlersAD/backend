"""Source scope produces reviewed execution workflows, never date-slot demo rows."""
from copy import deepcopy
from datetime import date
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import path

from apps.rbac.route_guard import secure_module_endpoints
from ..models import CalendarException, PlanningFile, Schedule, ScheduleActivity, ScheduleBaseline, ScheduleVersion, WorkCalendar
from ..services.document_intelligence import run_document_intelligence
from ..services.simple_planning import _version_tasks
from ..services.work_breakdown import materialize_work_breakdown
from ..simple_planning_views import SimplePlanningView
from . import test_simple_planning as fixture

urlpatterns = [
    *fixture.urlpatterns,
    path('api/v1/planning-intelligence/projects/<int:project_id>/simple-plan/programmatic-draft/',
         SimplePlanningView.as_view(operation='programmatic-draft')),
]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class ProgrammaticRequirementsTests(TestCase):
    setUp = fixture.SimplePlanningTests.setUp
    read = fixture.SimplePlanningTests.read
    save = fixture.SimplePlanningTests.save
    task = fixture.SimplePlanningTests.task

    def source(self, statements=None, category='sow'):
        statements = statements if statements is not None else [
            'The CONTRACTOR shall prepare the Fire and Gas Mapping Report.',
            'Contract precedence shall follow the order listed in the agreement.',
            'The CONTRACTOR must maintain document records.',
        ]
        return PlanningFile.objects.create(
            project=self.project, category=category, file='tests/programmatic-scope.pdf',
            original_filename='Scope.pdf', parse_status='done', uploaded_by=self.owner,
            extracted_text='\n'.join(statements),
        )

    def analyse(self, statements=None):
        source = self.source(statements)
        run, _ = run_document_intelligence(self.project, user=self.owner, allow_ai=False)
        return source, run, list(run.facts.filter(fact_type='requirement').order_by('id'))

    def create_draft(self, revision=0, **extra):
        return self.client.post(self.url + 'programmatic-draft/', {
            'revision': revision, 'requirement_scope': 'all', **extra,
        }, format='json')

    def assert_unchanged(self, before):
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, before)
        self.assertFalse(ScheduleBaseline.objects.exists())

    def assert_network(self, plan):
        tasks = plan['tasks']
        work = [task for task in tasks if task.get('parent_deliverable_id')]
        self.assertTrue(work)
        self.assertEqual(len({task['id'] for task in tasks}), len(tasks))
        self.assertEqual(len({task['activity_code'] for task in tasks}), len(tasks))
        self.assertGreater(len({task['duration_days'] for task in work}), 1)
        self.assertTrue(all(task['duration_days'] > 1 for task in work))
        self.assertEqual(len([task for task in tasks if not task['depends_on']]), 1)
        self.assertEqual(len([task for task in tasks if not task['successors']]), 1)
        for task in work:
            self.assertTrue(task['discipline'])
            self.assertTrue(task['deliverable'])
            self.assertTrue(task['task_deliverable'])
            self.assertTrue(task['progress_measurement'])
            self.assertTrue(task['depends_on'])
            self.assertTrue(task['successors'])
            self.assertTrue(task['calculated'])
            self.assertTrue(all(link['type'] == 'FS' for link in task['dependency_details']))
        for parent in plan['deliverables']:
            chain = sorted([task for task in work if task['parent_deliverable_id'] == parent['id']],
                           key=lambda task: task['workflow_stage_sequence'])
            self.assertGreaterEqual(len(chain), 5)
            for first, second in zip(chain, chain[1:]):
                self.assertIn(first['id'], second['depends_on'])
                self.assertLess(first['planned_finish_date'], second['planned_start_date'])
        self.assertTrue(plan['enterprise_generation_summary']['quality']['valid'])

    def test_source_work_becomes_connected_workflow_without_ai_and_clauses_stay_in_review(self):
        source, run, facts = self.analyse()
        with patch('apps.planning_intelligence.services.project_ai.call_project_ai') as ai, \
                patch('apps.planning_intelligence.services.claude_client.call_claude') as claude:
            response = self.create_draft()
        self.assertEqual(response.status_code, 200, response.data)
        ai.assert_not_called()
        claude.assert_not_called()
        plan = response.data
        self.assertEqual(plan['method'], 'programmatic_requirements')
        self.assertEqual(plan['state'], 'review')
        self.assertEqual(plan['revision'], 1)
        self.assert_network(plan)
        self.assertEqual(len(plan['deliverables']), 1)
        self.assertEqual(len(plan['scope_selection']['excluded_requirements']), 2)
        self.assertNotIn('programmatic_summary', plan)  # Old UI text describes slot allocation.
        for task in plan['tasks']:
            if not task.get('parent_deliverable_id'):
                continue
            self.assertEqual(task['requirement_value'], facts[0].value)
            self.assertEqual(task['source_references'][0]['file_id'], source.pk)
            self.assertEqual(task['duration_source'], 'proposed')
            self.assertTrue(task['needs_review'])
        self.assertEqual(run.facts.filter(fact_type='requirement', status='detected').count(), 3)
        self.assertFalse(Schedule.objects.exists())
        self.assertFalse(ScheduleBaseline.objects.exists())
        self.assertEqual(self.read()['tasks'], plan['tasks'])

    def test_221_generic_obligations_do_not_become_one_day_orphan_activities(self):
        statements = [f'The CONTRACTOR shall record requirement number {index + 1}.' for index in range(220)]
        statements.append('The order of precedence shall follow the contract, including all amendments.')
        self.analyse(statements)
        response = self.create_draft()
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'programmatic_executable_scope_required')
        self.assertEqual(len(response.data['excluded_requirements']), 221)
        self.assert_unchanged({})

    def test_duplicate_work_statements_share_one_workflow_and_keep_both_quotes(self):
        statement = 'The CONTRACTOR shall prepare the Fire and Gas Mapping Report.'
        self.analyse([statement, statement])
        response = self.create_draft()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data['deliverables']), 1)
        self.assertEqual(len(response.data['document_deliverables'][0]['source_references']), 2)
        self.assert_network(response.data)

    def test_rejected_and_conflicted_requirements_do_not_create_work(self):
        _, run, facts = self.analyse([
            'The CONTRACTOR shall prepare the Fire and Gas Mapping Report.',
            'The CONTRACTOR shall prepare cable routing layouts.',
            'The CONTRACTOR shall prepare foundation design calculations.',
        ])
        for fact, status in zip(facts, ('confirmed', 'rejected', 'conflicted')):
            fact.status = status
            fact.save(update_fields=['status'])
        response = self.create_draft()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data['deliverables']), 1)
        self.assertEqual({row['reason'] for row in response.data['scope_selection']['excluded_requirements']},
                         {'source_review_not_accepted'})
        self.assertEqual(list(run.facts.filter(fact_type='requirement').order_by('id').values_list('status', flat=True)),
                         ['confirmed', 'rejected', 'conflicted'])

    def test_deliverable_register_is_hierarchy_and_repeated_narrative_is_not_duplicate_work(self):
        self.analyse([
            'The CONTRACTOR shall prepare the Site Survey Report.',
            'Contract precedence shall follow the order listed in the agreement.',
            'APPENDIX 3 - FEED DELIVERABLES', 'S. No. Description', 'General',
            '1 Site Survey Report', '2 Project Execution Plan.',
        ])
        response = self.create_draft()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data['deliverables']), 2)
        self.assertEqual(len(response.data['document_deliverables']), 2)
        self.assert_network(response.data)
        for task in response.data['tasks']:
            self.assertNotIn(task['title'], ['Site Survey Report', 'Project Execution Plan.'])

    def test_reference_schedule_never_adds_project_scope(self):
        self.source(['Deliverables', '1 Offshore Platform Construction'], category='output_schedule_sample')
        self.analyse()
        response = self.create_draft()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data['deliverables']), 1)
        self.assertFalse(any('Offshore' in task['title'] for task in response.data['tasks']))

    def test_explicit_field_work_remains_in_scope_alongside_design_register(self):
        self.analyse([
            'The CONTRACTOR shall procure process pumps.',
            'The CONTRACTOR shall install water mains.',
            'The CONTRACTOR shall test installed water mains.',
            'Deliverables', 'Civil', '1 Foundation Design Calculations',
        ])
        response = self.create_draft()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data['deliverables']), 4)
        phases = {task['schedule_phase'] for task in response.data['tasks'] if task.get('parent_deliverable_id')}
        self.assertIn('procurement', phases)
        self.assertIn('construction', phases)
        self.assertIn('testing', phases)
        self.assert_network(response.data)

    def test_deliverable_only_source_does_not_require_narrative_shall_facts(self):
        _, _, facts = self.analyse(['Deliverables', 'Civil', '1 Foundation Design Calculations'])
        self.assertEqual(facts, [])
        response = self.create_draft()
        self.assertEqual(response.status_code, 200, response.data)
        self.assert_network(response.data)

    def test_tight_target_retains_realistic_durations_and_reports_negative_float(self):
        self.analyse()
        self.project.effective_date = date(2027, 2, 1)
        self.project.planned_end_date = date(2027, 2, 2)
        self.project.save(update_fields=['effective_date', 'planned_end_date', 'updated_at'])
        response = self.create_draft()
        self.assertEqual(response.status_code, 200, response.data)
        self.assert_network(response.data)
        self.assertGreater(response.data['enterprise_generation_summary']['calculated_finish_date'], '2027-02-02')
        self.assertTrue(any(task['total_float_days'] < 0 for task in response.data['tasks']))
        self.assertTrue(any(row['code'] == 'enterprise_target_overrun' for row in response.data['warnings']))
        self.assertEqual(self.project.intelligence_runs.count(), 1)

    def test_registered_calendar_and_exceptions_control_calculated_dates(self):
        calendar = WorkCalendar.objects.create(project=self.project, name='Sunday to Thursday',
                                               is_default=True, working_weekdays=[6, 0, 1, 2, 3])
        CalendarException.objects.create(calendar=calendar, date=date(2026, 11, 9), is_working=False)
        self.analyse()
        response = self.create_draft()
        self.assertEqual(response.status_code, 200, response.data)
        for task in response.data['tasks']:
            for field in ('planned_start_date', 'planned_finish_date'):
                actual = date.fromisoformat(task[field])
                self.assertIn(actual.weekday(), {6, 0, 1, 2, 3})
                self.assertNotEqual(actual, date(2026, 11, 9))
                self.assertGreaterEqual(actual, self.project.effective_date)

    def test_editing_generated_draft_preserves_network_and_scope_provenance(self):
        self.analyse()
        created = self.create_draft()
        self.assertEqual(created.status_code, 200, created.data)
        plan = created.data
        tasks = deepcopy(plan['tasks'])
        task = next(row for row in tasks if row.get('parent_deliverable_id'))
        task['title'] = 'Review Fire and Gas Mapping design inputs'
        saved = self.save(tasks=tasks, revision=plan['revision'], disciplines=plan['disciplines'])
        original = next(row for row in saved['tasks'] if row['id'] == task['id'])
        self.assertEqual(original['title'], task['title'])
        self.assertEqual(original['source_references'], task['source_references'])
        self.assertEqual(original['requirement_value'], task['requirement_value'])
        self.assert_network(saved)

    def test_saved_empty_analysis_can_be_filled_and_stale_derived_data_is_cleared(self):
        self.analyse()
        saved = self.save(tasks=[])
        self.project.refresh_from_db()
        self.project.simple_planning_state.update(schedule_proposal={'mode': 'source_only'},
                                                 duration_review={'summary': 'Old'}, calculation_run_id=999)
        self.project.save(update_fields=['simple_planning_state'])
        response = self.create_draft(revision=saved['revision'])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['revision'], saved['revision'] + 1)
        self.project.refresh_from_db()
        for field in ('schedule_proposal', 'duration_review', 'calculation_run_id'):
            self.assertNotIn(field, self.project.simple_planning_state)

    def test_materialized_version_retains_execution_logic_and_estimate_provenance(self):
        self.analyse()
        created = self.create_draft()
        self.assertEqual(created.status_code, 200, created.data)
        self.project.refresh_from_db()
        draft = deepcopy(self.project.simple_planning_state)
        version = materialize_work_breakdown(self.project, draft, actor=self.owner,
                                            start=self.project.effective_date, token=f'simple:{self.project.pk}')
        restored = {row['id']: row for row in _version_tasks(version)}
        self.assertEqual(len(restored), len(draft['tasks']))
        for original in draft['tasks']:
            task = restored[original['id']]
            self.assertEqual(task['depends_on'], original['depends_on'])
            for field in ('duration_source', 'source_references', 'generation_method', 'task_deliverable'):
                if field in original:
                    self.assertEqual(task[field], original[field], field)
            if original.get('parent_deliverable_id'):
                self.assertEqual(task['requirement_value'], original['requirement_value'])
        self.assertEqual(version.status, 'draft')
        self.assertFalse(ScheduleBaseline.objects.exists())

    def test_missing_dates_and_inverted_horizon_do_not_create_a_draft(self):
        self.analyse()
        for start, finish in ((None, date(2026, 12, 20)), (date(2026, 11, 6), None),
                              (date(2026, 12, 20), date(2026, 11, 6))):
            self.project.effective_date, self.project.planned_end_date = start, finish
            self.project.save(update_fields=['effective_date', 'planned_end_date'])
            response = self.create_draft()
            self.assertIn(response.status_code, (400, 409), response.data)
            self.assertEqual(response.data['code'], 'dates_required' if not start or not finish else 'dates_invalid')
            self.assert_unchanged({})

    def test_horizon_with_no_working_days_rejects_generation(self):
        self.project.effective_date, self.project.planned_end_date = date(2026, 11, 7), date(2026, 11, 8)
        self.project.save(update_fields=['effective_date', 'planned_end_date'])
        self.analyse()
        response = self.create_draft()
        self.assertEqual(response.data['code'], 'programmatic_no_working_days')
        self.assert_unchanged({})

    def test_missing_successful_analysis_does_not_call_ai(self):
        self.source()
        with patch('apps.planning_intelligence.services.project_ai.call_project_ai') as ai:
            response = self.create_draft()
        self.assertEqual(response.status_code, 409, response.data)
        ai.assert_not_called()
        self.assert_unchanged({})

    def test_changed_source_rejects_stale_requirements(self):
        source, _, _ = self.analyse()
        source.extracted_text += '\nThe CONTRACTOR shall prepare a changed report.'
        source.save(update_fields=['extracted_text'])
        response = self.create_draft()
        self.assertEqual(response.status_code, 409, response.data)
        self.assert_unchanged({})

    def test_added_source_requires_current_complete_analysis(self):
        self.analyse()
        self.source(['The CONTRACTOR shall prepare cable routing layouts.'])
        self.assertEqual(self.create_draft().status_code, 409)
        self.assert_unchanged({})

    def test_populated_draft_and_stale_revision_are_not_overwritten(self):
        self.analyse()
        saved = self.save()
        self.project.refresh_from_db()
        before = deepcopy(self.project.simple_planning_state)
        for revision in (0, saved['revision']):
            self.assertEqual(self.create_draft(revision=revision).status_code, 409)
            self.assert_unchanged(before)

    def test_history_requests_are_read_only(self):
        self.analyse()
        for url, extra in ((self.url + 'programmatic-draft/?version_id=1', {}),
                           (self.url + 'programmatic-draft/', {'viewing_history': True}),
                           (self.url + 'programmatic-draft/', {'version_id': 1})):
            response = self.client.post(url, {'revision': 0, 'requirement_scope': 'all', **extra}, format='json')
            self.assertEqual(response.status_code, 409, response.data)
            self.assert_unchanged({})

    def test_historical_view_does_not_show_current_generation_scope_or_assumptions(self):
        self.analyse()
        self.assertEqual(self.create_draft().status_code, 200)
        schedule = Schedule.objects.create(project=self.project, name='Prior schedule', code='EARLIER',
                                           planned_start=self.project.effective_date)
        version = ScheduleVersion.objects.create(schedule=schedule, version=1)
        ScheduleActivity.objects.create(version=version, external_id='EARLIER-01', name='Earlier work', duration_days=2)
        self.project.refresh_from_db()
        before = deepcopy(self.project.simple_planning_state)
        response = self.client.get(self.url, {'version_id': version.pk})
        self.assertEqual(response.status_code, 200, response.data)
        for field in ('enterprise_generation_summary', 'scope_selection', 'programmatic_assumptions', 'document_deliverables'):
            self.assertNotIn(field, response.data)
        self.assert_unchanged(before)

    def test_selected_canonical_version_is_not_replaced(self):
        self.analyse()
        schedule = Schedule.objects.create(project=self.project, name='Accepted', code='ACCEPTED',
                                           planned_start=self.project.effective_date)
        version = ScheduleVersion.objects.create(schedule=schedule, version=1)
        self.project.master_schedule_version = version
        self.project.save(update_fields=['master_schedule_version'])
        self.assertEqual(self.create_draft().status_code, 409)
        self.assert_unchanged({})
        self.assertEqual(self.project.master_schedule_version_id, version.pk)

    def test_baselined_draft_is_not_changed(self):
        self.analyse()
        self.project.simple_planning_state = {'state': 'baselined', 'revision': 2, 'tasks': [],
                                             'disciplines': [], 'baseline_id': None, 'version_id': None, 'review_id': None}
        self.project.save(update_fields=['simple_planning_state'])
        before = deepcopy(self.project.simple_planning_state)
        self.assertEqual(self.create_draft(revision=2).status_code, 409)
        self.assert_unchanged(before)

    def test_non_project_user_cannot_create_draft(self):
        self.analyse()
        self.client.force_authenticate(self.other)
        self.assertEqual(self.create_draft().status_code, 404)
        self.assert_unchanged({})
