"""Requirements can become reviewable dated activities without provider calls."""
from copy import deepcopy
from datetime import date
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import path

from apps.rbac.route_guard import secure_module_endpoints

from ..models import (
    CalendarException, IntelligenceFact, PlanningFile, Schedule, ScheduleActivity,
    ScheduleBaseline, ScheduleVersion, WorkCalendar,
)
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

    def source(self, statements=None):
        statements = statements or [
            'The CONTRACTOR shall prepare the Fire and Gas Mapping Report.',
            'Contract precedence shall follow the order listed in the agreement.',
            'The CONTRACTOR must maintain document records.',
        ]
        return PlanningFile.objects.create(
            project=self.project, category='sow', file='tests/programmatic-scope.pdf',
            original_filename='Scope.pdf', parse_status='done', uploaded_by=self.owner,
            extracted_text='\n'.join(statements),
        )

    def analyse(self, statements=None):
        source = self.source(statements)
        run, _ = run_document_intelligence(self.project, user=self.owner, allow_ai=False)
        facts = list(run.facts.filter(fact_type='requirement').order_by('id'))
        return source, run, facts

    def create_draft(self, revision=0, **extra):
        return self.client.post(self.url + 'programmatic-draft/', {
            'revision': revision, 'requirement_scope': 'all', **extra,
        }, format='json')

    def assert_unchanged(self, before):
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, before)
        self.assertFalse(ScheduleBaseline.objects.exists())

    def test_all_221_requirements_become_dated_unapproved_activities_without_ai(self):
        statements = [f'The CONTRACTOR shall record requirement number {index + 1}.' for index in range(220)]
        statements.insert(1, 'The order of precedence shall follow the contract, including all amendments.')
        source, run, facts = self.analyse(statements)
        self.assertEqual(len(facts), 221)
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
        self.assertEqual([task['id'] for task in plan['tasks']], [f'requirement-{fact.pk}' for fact in facts])
        self.assertEqual([task['source_title'] for task in plan['tasks']], [fact.value for fact in facts])
        codes = [task['activity_code'] for task in plan['tasks']]
        self.assertEqual(len(set(codes)), 221)
        self.assertTrue(all(code.endswith(f'-{(index + 1) * 10:04d}') for index, code in enumerate(codes)))
        for task, fact in zip(plan['tasks'], facts):
            self.assertNotIn('shall', task['title'].lower())
            self.assertEqual(task['activity_name_original'], task['title'])
            self.assertEqual(task['requirement_value'], fact.value)
            self.assertEqual(task['duration_source'], 'proposed')
            self.assertTrue(task['needs_review'])
            self.assertEqual(task['depends_on'], [])
            self.assertGreaterEqual(task['duration_days'], 1)
            self.assertLessEqual(self.project.effective_date.isoformat(), task['planned_start_date'])
            self.assertLessEqual(task['planned_start_date'], task['planned_finish_date'])
            self.assertLessEqual(task['planned_finish_date'], self.project.planned_end_date.isoformat())
            self.assertLess(date.fromisoformat(task['planned_start_date']).weekday(), 5)
            self.assertLess(date.fromisoformat(task['planned_finish_date']).weekday(), 5)
            self.assertEqual(task['source_references'][0]['file_id'], source.pk)
            self.assertEqual(task['source_references'][0]['locator'], fact.source_locator)
            self.assertEqual(task['source_references'][0]['excerpt'], fact.source_excerpt)
        self.assertEqual(plan['programmatic_summary']['requirement_count'], 221)
        self.assertTrue(plan['warnings'])
        self.assertTrue(plan['assumptions'])
        self.assertLess(len({task['planned_start_date'] for task in plan['tasks']}), 221)
        self.assertEqual(self.project.intelligence_runs.count(), 1)
        self.assertEqual(run.facts.filter(fact_type='requirement', status='detected').count(), 221)
        self.assertFalse(Schedule.objects.exists())
        self.assertFalse(ScheduleVersion.objects.exists())
        self.assertFalse(ScheduleBaseline.objects.exists())
        reloaded = self.read()
        self.assertEqual([(task['id'], task['planned_start_date'], task['planned_finish_date']) for task in reloaded['tasks']],
                         [(task['id'], task['planned_start_date'], task['planned_finish_date']) for task in plan['tasks']])

    def test_duplicate_statements_keep_distinct_fact_identity(self):
        source, run, facts = self.analyse()
        duplicate = IntelligenceFact.objects.create(
            run=run, source_file=source, fact_type='requirement', key='duplicate-location',
            value=facts[0].value, normalized_value=facts[0].normalized_value,
            confidence=.76, source_excerpt=facts[0].source_excerpt,
            source_locator={'page': 7, 'line': 12},
        )
        response = self.create_draft()
        self.assertEqual(response.status_code, 200, response.data)
        matching = [task for task in response.data['tasks'] if task['source_title'] == facts[0].value]
        self.assertEqual({task['id'] for task in matching}, {f'requirement-{facts[0].pk}', f'requirement-{duplicate.pk}'})
        self.assertEqual(len(response.data['tasks']), 4)

    def test_all_includes_reviewed_statements_without_changing_fact_status(self):
        _, run, facts = self.analyse()
        for fact, status in zip(facts, ('confirmed', 'rejected', 'conflicted')):
            fact.status = status
            fact.save(update_fields=['status'])
        response = self.create_draft()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([task['requirement_status'] for task in response.data['tasks']],
                         ['confirmed', 'rejected', 'conflicted'])
        self.assertTrue(all(task['needs_review'] for task in response.data['tasks']))
        self.assertEqual(list(run.facts.filter(fact_type='requirement').order_by('id').values_list('status', flat=True)),
                         ['confirmed', 'rejected', 'conflicted'])

    def test_long_requirement_keeps_full_statement_behind_bounded_activity_title(self):
        statement = 'The CONTRACTOR shall review ' + 'all listed project requirements and deliverable details ' * 15 + '.'
        _, _, facts = self.analyse([statement])
        response = self.create_draft()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertGreater(len(facts[0].value), 500)
        task = response.data['tasks'][0]
        self.assertTrue(task['title'].startswith('Review '))
        self.assertLessEqual(len(task['title']), 160)
        self.assertEqual(task['source_title'], facts[0].value)
        self.assertEqual(task['requirement_value'], facts[0].value)
        self.assertEqual(task['acceptance_criteria'], facts[0].value)

    def test_names_follow_source_actions_while_codes_remain_separate_from_wbs(self):
        self.project.phase = 'FEED'
        self.project.save(update_fields=['phase'])
        self.analyse(['The CONTRACTOR shall prepare the cable routing layouts.',
                      'The order of precedence shall follow the contract.',
                      'Demolished components shall not be reused.'])
        plan = self.create_draft().data
        self.assertEqual(plan['tasks'][0]['title'], 'Prepare cable routing layouts')
        self.assertEqual(plan['tasks'][1]['title'], 'Review document order of precedence')
        self.assertIn('no reuse', plan['tasks'][2]['title'].lower())
        self.assertEqual([task['activity_code'] for task in plan['tasks']],
                         ['FEED-REQ-0010', 'FEED-REQ-0020', 'FEED-REQ-0030'])
        self.assertNotEqual(plan['tasks'][0]['activity_code'], plan['tasks'][0]['wbs_code'])
        self.assertEqual(plan['tasks'][0]['field_provenance']['title']['type'], 'proposal')
        self.assertEqual([task['activity_code'] for task in self.read()['tasks']],
                         [task['activity_code'] for task in plan['tasks']])

    def test_deliverable_register_is_separate_from_all_requirement_activities(self):
        self.analyse([
            'The contractor shall prepare each identified project document.',
            'Contract precedence shall follow the order listed in the agreement.',
            'APPENDIX 3 - FEED DELIVERABLES', 'S. No. Description', 'General',
            '1 Site Survey Report', '2 Project Execution Plan.',
            'Conceptual Design Study', '1 Site Survey Report',
        ])
        response = self.create_draft()
        self.assertEqual(response.status_code, 200, response.data)
        plan = response.data
        self.assertEqual(len(plan['tasks']), 2)
        self.assertEqual([row['title'] for row in plan['document_deliverables']],
                         ['Site Survey Report', 'Project Execution Plan.', 'Site Survey Report'])
        self.assertEqual([row['source_heading'] for row in plan['document_deliverables']],
                         ['General', 'General', 'Conceptual Design Study'])
        self.assertEqual([row['source_item_number'] for row in plan['document_deliverables']], ['1', '2', '1'])
        self.assertEqual(plan['programmatic_summary']['deliverable_count'], 3)
        self.assertEqual(plan['programmatic_summary']['activity_count'], 2)
        self.assertFalse(plan['programmatic_summary']['ai_used'])
        self.assertFalse(set(row['id'] for row in plan['tasks']).intersection(
            row['id'] for row in plan['document_deliverables']))
        for row in plan['document_deliverables']:
            self.assertEqual(row['duration_source'], 'proposed')
            self.assertTrue(row['needs_review'])
            self.assertLessEqual(self.project.effective_date.isoformat(), row['planned_start_date'])
            self.assertLessEqual(row['planned_finish_date'], self.project.planned_end_date.isoformat())
        reopened = self.read()
        self.assertEqual(reopened['document_deliverables'], plan['document_deliverables'])

    def test_editing_generated_draft_keeps_statement_provenance_and_review_flags(self):
        _, _, facts = self.analyse()
        response = self.create_draft()
        self.assertEqual(response.status_code, 200, response.data)
        plan = response.data
        edited = [self.task(task['id'], title=task['title'], discipline=task['discipline'],
                           duration_days=task['duration_days'], effort_hours=None,
                           acceptance_criteria=task['acceptance_criteria']) for task in plan['tasks']]
        edited[0]['title'] = 'Planner clarified wording'
        saved = self.save(tasks=edited, revision=plan['revision'])
        original = next(task for task in saved['tasks'] if task['id'] == f'requirement-{facts[0].pk}')
        self.assertEqual(original['title'], 'Planner clarified wording')
        self.assertEqual(original['source_title'], facts[0].value)
        self.assertEqual(original['requirement_value'], facts[0].value)
        self.assertEqual(original['requirement_id'], facts[0].pk)
        self.assertEqual(original['source_references'], plan['tasks'][0]['source_references'])
        self.assertTrue(original['needs_review'])
        self.assertEqual(original['duration_source'], 'proposed')
        self.assertEqual([(task['planned_start_date'], task['planned_finish_date']) for task in saved['tasks']],
                         [(task['planned_start_date'], task['planned_finish_date']) for task in plan['tasks']])

    def test_current_project_dates_can_be_changed_without_reextracting_unchanged_statements(self):
        self.analyse()
        self.project.effective_date = date(2027, 2, 1)
        self.project.planned_end_date = date(2027, 2, 12)
        self.project.save(update_fields=['effective_date', 'planned_end_date', 'updated_at'])
        response = self.create_draft()
        self.assertEqual(response.status_code, 200, response.data)
        for task in response.data['tasks']:
            self.assertLessEqual('2027-02-01', task['planned_start_date'])
            self.assertLessEqual(task['planned_finish_date'], '2027-02-12')
        self.assertEqual(self.project.intelligence_runs.count(), 1)

    def test_saved_empty_analysis_can_be_filled_and_stale_derived_data_is_cleared(self):
        self.analyse()
        saved = self.save(tasks=[])
        self.project.refresh_from_db()
        self.project.simple_planning_state.update({
            'schedule_proposal': {'mode': 'source_only'},
            'duration_review': {'summary': 'Old review'},
            'calculation_run_id': 999,
        })
        self.project.save(update_fields=['simple_planning_state'])
        response = self.create_draft(revision=saved['revision'])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data['tasks']), 3)
        self.assertEqual(response.data['revision'], saved['revision'] + 1)
        self.project.refresh_from_db()
        for field in ('schedule_proposal', 'duration_review', 'calculation_run_id'):
            self.assertNotIn(field, self.project.simple_planning_state)

    def test_registered_calendar_and_exceptions_control_proposed_dates(self):
        self.project.effective_date = date(2026, 11, 6)
        self.project.planned_end_date = date(2026, 11, 13)
        self.project.save(update_fields=['effective_date', 'planned_end_date'])
        calendar = WorkCalendar.objects.create(
            project=self.project, name='Sunday to Thursday', is_default=True,
            working_weekdays=[6, 0, 1, 2, 3],
        )
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
                self.assertLessEqual(actual, self.project.planned_end_date)

    def test_missing_dates_and_inverted_horizon_do_not_create_a_draft(self):
        self.analyse()
        for start, finish in ((None, date(2026, 12, 20)), (date(2026, 11, 6), None),
                              (date(2026, 12, 20), date(2026, 11, 6))):
            with self.subTest(start=start, finish=finish):
                self.project.effective_date = start
                self.project.planned_end_date = finish
                self.project.save(update_fields=['effective_date', 'planned_end_date'])
                response = self.create_draft()
                self.assertIn(response.status_code, (400, 409), response.data)
                self.assertEqual(response.data['code'], 'dates_required' if not start or not finish else 'dates_invalid')
                self.assert_unchanged({})

    def test_horizon_with_no_working_days_does_not_create_dates_outside_project(self):
        self.project.effective_date = date(2026, 11, 7)
        self.project.planned_end_date = date(2026, 11, 8)
        self.project.save(update_fields=['effective_date', 'planned_end_date'])
        self.analyse()
        response = self.create_draft()
        self.assertIn(response.status_code, (400, 409), response.data)
        self.assertEqual(response.data['code'], 'programmatic_no_working_days')
        self.assert_unchanged({})

    def test_missing_successful_analysis_is_actionable_and_does_not_call_ai(self):
        self.source()
        with patch('apps.planning_intelligence.services.project_ai.call_project_ai') as ai:
            response = self.create_draft()
        self.assertIn(response.status_code, (400, 409), response.data)
        ai.assert_not_called()
        self.assert_unchanged({})

    def test_changed_or_added_source_documents_reject_stale_requirements(self):
        source, _, _ = self.analyse()
        source.extracted_text += '\nThe CONTRACTOR shall prepare a changed report.'
        source.save(update_fields=['extracted_text'])
        response = self.create_draft()
        self.assertEqual(response.status_code, 409, response.data)
        self.assert_unchanged({})

    def test_added_source_requires_current_complete_analysis(self):
        self.analyse()
        self.source(['The CONTRACTOR shall record an additional obligation.'])
        response = self.create_draft()
        self.assertEqual(response.status_code, 409, response.data)
        self.assert_unchanged({})

    def test_latest_successful_run_is_used_without_accumulating_old_requirements(self):
        _, original, _ = self.analyse()
        latest, _ = run_document_intelligence(self.project, user=self.owner, allow_ai=False)
        response = self.create_draft()
        self.assertEqual(response.status_code, 200, response.data)
        expected = list(latest.facts.filter(fact_type='requirement').order_by('id').values_list('id', flat=True))
        old = set(original.facts.filter(fact_type='requirement').values_list('id', flat=True))
        self.assertEqual([task['id'] for task in response.data['tasks']], [f'requirement-{pk}' for pk in expected])
        self.assertFalse(old.intersection(expected))

    def test_populated_draft_and_stale_revision_are_not_overwritten(self):
        self.analyse()
        saved = self.save()
        self.project.refresh_from_db()
        before = deepcopy(self.project.simple_planning_state)
        for revision in (0, saved['revision']):
            with self.subTest(revision=revision):
                response = self.create_draft(revision=revision)
                self.assertEqual(response.status_code, 409, response.data)
                self.assert_unchanged(before)

    def test_history_requests_are_read_only(self):
        self.analyse()
        for url, extra in ((self.url + 'programmatic-draft/?version_id=1', {}),
                           (self.url + 'programmatic-draft/', {'viewing_history': True}),
                           (self.url + 'programmatic-draft/', {'version_id': 1})):
            with self.subTest(url=url, extra=extra):
                response = self.client.post(url, {'revision': 0, 'requirement_scope': 'all', **extra}, format='json')
                self.assertEqual(response.status_code, 409, response.data)
                self.assert_unchanged({})

    def test_historical_view_does_not_show_current_programmatic_register_or_assumptions(self):
        self.analyse([
            'The CONTRACTOR shall maintain all project records.',
            'Deliverables', '1 Site Survey Report',
        ])
        created = self.create_draft()
        self.assertEqual(created.status_code, 200, created.data)
        self.assertTrue(created.data['document_deliverables'])
        schedule = Schedule.objects.create(project=self.project, name='Prior schedule', code='EARLIER',
                                           planned_start=self.project.effective_date)
        version = ScheduleVersion.objects.create(schedule=schedule, version=1)
        ScheduleActivity.objects.create(version=version, external_id='EARLIER-01', name='Earlier work', duration_days=2)
        self.project.refresh_from_db()
        before = deepcopy(self.project.simple_planning_state)
        response = self.client.get(self.url, {'version_id': version.pk})
        self.assertEqual(response.status_code, 200, response.data)
        historical = response.data
        self.assertEqual([task['id'] for task in historical['tasks']], ['EARLIER-01'])
        for field in ('programmatic_summary', 'programmatic_assumptions', 'document_deliverables'):
            self.assertNotIn(field, historical)
        self.assertNotEqual(historical['method'], 'programmatic_requirements')
        self.assertFalse(any(row.get('code', '').startswith('programmatic_') for row in historical['warnings']))
        self.assertFalse(any(row.get('code', '').startswith('programmatic_') or row.get('code') == 'all_requirements_included'
                             for row in historical['assumptions']))
        self.assert_unchanged(before)

    def test_materialized_version_preserves_requirement_values_and_provisional_timing_metadata(self):
        self.analyse()
        created = self.create_draft()
        self.assertEqual(created.status_code, 200, created.data)
        self.project.refresh_from_db()
        draft = deepcopy(self.project.simple_planning_state)
        version = materialize_work_breakdown(
            self.project, draft, actor=self.owner, start=self.project.effective_date,
            token=f'simple:{self.project.pk}',
        )
        stored = {row.external_id: row for row in version.activities.all()}
        restored = {row['id']: row for row in _version_tasks(version)}
        self.assertEqual(len(restored), 3)
        for original in draft['tasks']:
            task = restored[original['id']]
            activity = stored[original['id']]
            for field in ('requirement_id', 'requirement_value', 'requirement_status',
                          'selection_basis', 'needs_review', 'review_flags', 'proposal_timing',
                          'duration_source', 'source_references'):
                self.assertEqual(activity.metadata[field], original[field], field)
                self.assertEqual(task[field], original[field], field)
            self.assertEqual(activity.metadata['source_title'], original['source_title'])
            self.assertEqual(task['acceptance_criteria'], original['requirement_value'])
            self.assertEqual(task['constraint_type'], 'start_no_earlier')
            self.assertEqual(task['constraint_date'], original['planned_start_date'])
            self.assertEqual(task['depends_on'], [])
        self.assertEqual(version.status, 'draft')
        self.assertFalse(ScheduleBaseline.objects.exists())

    def test_selected_canonical_version_is_not_replaced(self):
        self.analyse()
        schedule = Schedule.objects.create(project=self.project, name='Accepted', code='ACCEPTED',
                                           planned_start=self.project.effective_date)
        version = ScheduleVersion.objects.create(schedule=schedule, version=1)
        self.project.master_schedule_version = version
        self.project.save(update_fields=['master_schedule_version'])
        response = self.create_draft()
        self.assertEqual(response.status_code, 409, response.data)
        self.assert_unchanged({})
        self.assertEqual(self.project.master_schedule_version_id, version.pk)
        self.assertEqual(ScheduleVersion.objects.count(), 1)

    def test_baselined_draft_is_not_changed(self):
        self.analyse()
        self.read()
        self.project.simple_planning_state = {
            'state': 'baselined', 'revision': 2, 'tasks': [], 'disciplines': [],
            'baseline_id': None, 'version_id': None, 'review_id': None,
        }
        self.project.save(update_fields=['simple_planning_state'])
        before = deepcopy(self.project.simple_planning_state)
        response = self.create_draft(revision=2)
        self.assertEqual(response.status_code, 409, response.data)
        self.assert_unchanged(before)

    def test_non_project_user_cannot_create_draft(self):
        self.analyse()
        self.client.force_authenticate(self.other)
        response = self.create_draft()
        self.assertEqual(response.status_code, 404, response.data)
        self.assert_unchanged({})
