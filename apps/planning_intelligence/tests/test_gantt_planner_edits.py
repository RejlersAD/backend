"""Gantt edits retain source observations and respect calendar/approval gates."""
from copy import deepcopy
from datetime import date

from django.test import TestCase, override_settings

from ..models import PlanningAuditEvent, WorkCalendar
from ..services.simple_planning import _fingerprint, save_plan
from ..services.work_breakdown import materialize_work_breakdown
from ..services.cpm import calculate_schedule_version, SchedulingError
from . import test_simple_planning as fixture


@override_settings(ROOT_URLCONF='apps.planning_intelligence.tests.test_simple_planning')
class GanttPlannerEditTests(TestCase):
    task = fixture.SimplePlanningTests.task
    read = fixture.SimplePlanningTests.read
    save = fixture.SimplePlanningTests.save

    def setUp(self):
        fixture.SimplePlanningTests.setUp(self)

    def source_draft(self, *, milestone=False, calendar_verified=False):
        evidence = {'activity_specific': True,
            'values': {'planned_start_date': '2026-11-06', 'planned_finish_date': '2026-11-09',
                       'original_duration_days': 2},
            'source_references': [{'file_id': 91, 'filename': 'schedule.pdf', 'locator': {'page': 1, 'row': 2}}]}
        task = self.task(duration_days=0 if milestone else 2, duration_source='source_document',
            source_evidence=deepcopy(evidence), duration_evidence=deepcopy(evidence),
            duration_calendar_verified=calendar_verified, evidence_policy='document_driven',
            duration_policy='source_only', is_milestone=milestone,
            activity_type='finish_milestone' if milestone else 'task',
            planned_start_date=None, planned_finish_date=None, constraint_type='none', constraint_date=None)
        state = {'state': 'review', 'revision': 3, 'tasks': [task],
            'disciplines': [{'code': 'testing', 'name': 'Testing'}],
            'assignment_token': f'simple:{self.project.pk}', 'managed_task_ids': [task['id']],
            'input_fingerprint': _fingerprint(self.project), 'warnings': [],
            'version_id': None, 'review_id': None, 'baseline_id': None}
        self.project.simple_planning_state = state
        self.project.save(update_fields=['simple_planning_state'])
        return state

    def test_start_then_finish_selects_exact_anchor_and_preserves_source_on_reload(self):
        initial = self.source_draft()
        source = deepcopy(initial['tasks'][0]['source_evidence'])
        task = self.read()['tasks'][0]
        task['timing_edit'] = {'field': 'start', 'value': '2026-11-10'}
        saved = self.save([task], revision=3)
        row = saved['tasks'][0]
        self.assertEqual(row['planned_start_date'], '2026-11-10')
        self.assertIsNone(row['planned_finish_date'])
        self.assertEqual(row['constraint_type'], 'must_start')
        self.assertFalse(row['calculated'])
        self.assertIsNone(row['total_float_days'])
        self.assertEqual(row['source_start_date'], '2026-11-06')
        self.assertEqual(row['source_finish_date'], '2026-11-09')
        row['timing_edit'] = {'field': 'finish', 'value': '2026-11-13'}
        self.save([row], revision=saved['revision'])
        reloaded = self.read()['tasks'][0]
        self.assertIsNone(reloaded['planned_start_date'])
        self.assertEqual(reloaded['planned_finish_date'], '2026-11-13')
        self.assertEqual(reloaded['constraint_type'], 'must_finish')
        self.assertEqual(reloaded['planner_timing']['anchor'], 'finish')
        self.assertEqual(reloaded['planner_timing']['edited_by'], self.owner.pk)
        self.assertEqual(reloaded['source_evidence'], source)
        self.assertEqual(reloaded['duration_evidence'], source)
        self.assertEqual(reloaded['duration_days'], 2)
        self.assertFalse(reloaded['duration_calendar_verified'])
        self.project.refresh_from_db()
        self.assertNotIn('timing_edit', self.project.simple_planning_state['tasks'][0])
        self.assertTrue(PlanningAuditEvent.objects.filter(project=self.project, action='simple_plan.saved').exists())

    def test_duration_edit_does_not_verify_import_calendar_or_erase_original_duration(self):
        initial = self.source_draft()
        task = self.read()['tasks'][0]
        task['duration_days'] = 7
        saved = self.save([task], revision=3)
        row = saved['tasks'][0]
        self.assertEqual(row['duration_days'], 7)
        self.assertEqual(row['duration_source'], 'planner')
        self.assertEqual(row['duration_evidence'], initial['tasks'][0]['duration_evidence'])
        self.assertEqual(row['duration_review_status'], 'manual_unverified')
        self.assertFalse(row['duration_calendar_verified'])
        self.assertFalse(row['calculated'])
        self.assertIn('source_calendar_unverified', {issue['code'] for issue in saved['blockers']})
        self.assertEqual(self.read()['tasks'][0]['duration_days'], 7)

    def test_finish_anchor_recalculates_draft_and_materializes_without_bypassing_evidence(self):
        self.source_draft(calendar_verified=True)
        WorkCalendar.objects.create(project=self.project, name='Declared project calendar', is_default=True,
                                   working_weekdays=[0, 1, 2, 3, 4])
        task = self.read()['tasks'][0]
        task['timing_edit'] = {'field': 'finish', 'value': '2026-11-13'}
        saved = self.save([task], revision=3)
        self.assertEqual(saved['tasks'][0]['planned_start_date'], '2026-11-12')
        self.assertEqual(saved['tasks'][0]['planned_finish_date'], '2026-11-13')
        self.assertTrue(saved['tasks'][0]['calculated'])
        task = saved['tasks'][0]
        task['duration_days'] = 3
        saved = self.save([task], revision=saved['revision'])
        self.assertEqual(saved['tasks'][0]['planned_start_date'], '2026-11-11')
        self.assertEqual(saved['tasks'][0]['planned_finish_date'], '2026-11-13')
        self.project.refresh_from_db()
        draft = deepcopy(self.project.simple_planning_state)
        version = materialize_work_breakdown(self.project, draft, actor=self.owner,
                    start=self.project.effective_date, token=draft['assignment_token'])
        with self.assertRaises(SchedulingError) as rejected:
            calculate_schedule_version(version, requested_by=self.owner)
        self.assertEqual(rejected.exception.code, 'planning_inputs_not_accepted')
        activity = version.activities.get()
        self.assertEqual(activity.constraint_type, 'must_finish')
        self.assertEqual(activity.constraint_date, date(2026, 11, 13))
        self.assertIsNone(activity.planned_start)
        self.assertIsNone(activity.planned_finish)
        self.assertEqual(activity.metadata['planner_timing']['anchor'], 'finish')
        self.assertEqual(activity.metadata['source_evidence'], task['source_evidence'])
        self.assertEqual(activity.metadata['duration_evidence'], task['duration_evidence'])

    def test_zero_duration_milestone_moves_as_one_point_and_rejects_positive_duration(self):
        self.source_draft(milestone=True)
        task = self.read()['tasks'][0]
        task['timing_edit'] = {'field': 'finish', 'value': '2026-11-12'}
        saved = self.save([task], revision=3)
        row = saved['tasks'][0]
        self.assertEqual(row['planned_start_date'], '2026-11-12')
        self.assertEqual(row['planned_finish_date'], '2026-11-12')
        self.assertEqual(row['duration_days'], 0)
        row['duration_days'] = 2
        response = self.client.put(self.url, {'revision': saved['revision'], 'tasks': [row]}, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'simple_plan_duration_invalid')
        self.assertEqual(self.read()['tasks'][0]['duration_days'], 0)

    def test_clear_anchor_releases_constraint_and_does_not_promote_source_dates(self):
        self.source_draft()
        task = self.read()['tasks'][0]
        task['timing_edit'] = {'field': 'start', 'value': '2026-11-10'}
        saved = self.save([task], revision=3)
        task = saved['tasks'][0]
        task['timing_edit'] = {'field': 'start', 'value': None}
        cleared = self.save([task], revision=saved['revision'])['tasks'][0]
        self.assertEqual(cleared['constraint_type'], 'none')
        self.assertIsNone(cleared['constraint_date'])
        self.assertNotIn('planner_timing', cleared)
        self.assertIsNone(cleared['planned_start_date'])
        self.assertEqual(cleared['source_start_date'], '2026-11-06')

    def test_individual_source_milestone_keeps_type_when_materialized(self):
        self.source_draft(milestone=True, calendar_verified=True)
        task = self.read()['tasks'][0]
        task['timing_edit'] = {'field': 'finish', 'value': '2026-11-12'}
        self.save([task], revision=3)
        self.project.refresh_from_db()
        draft = deepcopy(self.project.simple_planning_state)
        version = materialize_work_breakdown(self.project, draft, actor=self.owner,
                    start=self.project.effective_date, token=draft['assignment_token'])
        activity = version.activities.get()
        self.assertTrue(activity.is_milestone)
        self.assertEqual(activity.activity_type, 'finish_milestone')
        self.assertEqual(activity.duration_days, 0)
        self.assertEqual(activity.constraint_date, date(2026, 11, 12))
        self.assertEqual(activity.metadata['source_evidence'], task['source_evidence'])

    def test_invalid_date_stale_update_and_unverified_source_cycle_have_no_side_effects(self):
        initial = self.source_draft()
        row = self.read()['tasks'][0]
        row['timing_edit'] = {'field': 'start', 'value': '2026-02-31'}
        response = self.client.put(self.url, {'revision': 3, 'tasks': [row]}, format='json')
        self.assertEqual(response.status_code, 400)
        row['timing_edit']['value'] = '2026-11-10'
        response = self.client.put(self.url, {'revision': 2, 'tasks': [row]}, format='json')
        self.assertEqual(response.status_code, 409)
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, initial)
        first, second = deepcopy(initial['tasks'][0]), deepcopy(initial['tasks'][0])
        first['depends_on'] = ['b']
        second.update(id='b', depends_on=[first['id']])
        with self.assertRaisesRegex(ValueError, 'cycle'):
            save_plan(self.project, self.owner, {'revision': 3, 'tasks': [first, second]})
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, initial)

    def test_typed_lagged_links_retype_and_remove_without_mutating_original_source(self):
        state = self.source_draft()
        first = state['tasks'][0]
        second = deepcopy(first)
        second.update(id='b', title='Second source activity', depends_on=[first['id']],
            dependency_details=[{'task_id': first['id'], 'type': 'FS', 'lag_days': 0,
                                 'source': 'source_document', 'status': 'confirmed'}])
        state['tasks'].append(second)
        self.project.simple_planning_state = state
        self.project.save(update_fields=['simple_planning_state'])
        for kind, lag in [('SS', 2), ('FF', -1), ('SF', 0)]:
            read = self.read()
            rows = read['tasks']
            rows[1]['dependency_details'] = [{'task_id': first['id'], 'type': kind, 'lag_days': lag}]
            saved = self.save(rows, revision=read['revision'])
            link = saved['tasks'][1]['dependency_details'][0]
            self.assertEqual((link['type'], link['lag_days'], link['source']), (kind, lag, 'planner'))
            self.assertEqual(saved['tasks'][1]['source_evidence'], second['source_evidence'])
            self.assertEqual(saved['tasks'][1]['dependency_status'], 'planner')
        rows = self.read()['tasks']
        rows[1].update(depends_on=[], dependency_details=[])
        saved = self.save(rows, revision=saved['revision'])
        self.assertEqual(saved['tasks'][1]['depends_on'], [])
        self.assertEqual(self.read()['tasks'][1]['dependency_details'], [])

    def test_workflow_link_can_be_removed_and_retains_five_stages_with_review_warning(self):
        state = self.source_draft()
        rows = [self.task(f'stage-{number}', parent_deliverable_id='parent',
                 workflow_stage_sequence=number, workflow_stage_code=f'S{number}',
                 depends_on=[f'stage-{number-1}'] if number > 1 else [],
                 dependency_details=[{'task_id': f'stage-{number-1}', 'type': 'FS', 'lag_days': 0}] if number > 1 else [])
                for number in range(1, 6)]
        state.update(tasks=rows, deliverables=[{'id': 'parent', 'title': 'Deliverable', 'discipline': 'testing',
                     'workflow_task_ids': [row['id'] for row in rows]}])
        self.project.simple_planning_state = state
        self.project.save(update_fields=['simple_planning_state'])
        read = self.read()
        read['tasks'][2].update(depends_on=[], dependency_details=[])
        saved = self.save(read['tasks'], revision=read['revision'])
        self.assertEqual(len(saved['tasks']), 5)
        self.assertEqual(saved['tasks'][2]['depends_on'], [])
        self.assertIn('workflow_sequence_edited', {row['code'] for row in saved['warnings']})
