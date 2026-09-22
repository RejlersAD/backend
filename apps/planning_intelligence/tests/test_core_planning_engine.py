"""Manual WBS and network inputs survive editing, reload and materialization."""
from copy import deepcopy
from datetime import date

from django.test import TestCase, override_settings

from ..models import CalendarException, ScheduleVersion, WorkCalendar
from ..services.work_breakdown import materialize_work_breakdown
from . import test_simple_planning as fixture


@override_settings(ROOT_URLCONF='apps.planning_intelligence.tests.test_simple_planning')
class CorePlanningEngineTests(TestCase):
    setUp = fixture.SimplePlanningTests.setUp
    task = fixture.SimplePlanningTests.task
    read = fixture.SimplePlanningTests.read
    save = fixture.SimplePlanningTests.save
    action = fixture.SimplePlanningTests.action

    def test_all_relationship_types_control_draft_dates_and_survive_materialization(self):
        revision = 0
        for kind, lag, start in [('FS', 1, '2026-11-11'), ('SS', 1, '2026-11-09'),
                                 ('FF', 1, '2026-11-10'), ('SF', 2, '2026-11-09')]:
            with self.subTest(kind=kind):
                state = self.save([self.task('a', duration_days=2), self.task(
                    'b', duration_days=1, depends_on=['a'],
                    dependency_details=[{'task_id': 'a', 'type': kind, 'lag_days': lag}],
                )], revision=revision)
                revision = state['revision']
                # Friday + a two-day predecessor finishes Monday; CPM respects weekends.
                self.assertEqual(state['tasks'][1]['planned_start_date'], start)
                self.assertEqual(self.read()['tasks'][1]['dependency_details'][0]['type'], kind)
                self.project.refresh_from_db()
                version = materialize_work_breakdown(self.project, self.project.simple_planning_state,
                    actor=self.owner, start=self.project.effective_date, token=f'simple:{self.project.pk}')
                link = version.relationships.get()
                self.assertEqual((link.relationship_type, float(link.lag_days)), (kind, lag))

    def test_editing_relationship_type_and_lag_replaces_previous_values(self):
        state = self.save([self.task('a'), self.task('b', depends_on=['a'], dependency_details=[
            {'task_id': 'a', 'type': 'SS', 'lag_days': 1}])])
        tasks = deepcopy(state['tasks'])
        tasks[1]['dependency_details'] = [{'task_id': 'a', 'type': 'FF', 'lag_days': -1}]
        changed = self.save(tasks, revision=state['revision'])
        self.assertEqual(changed['tasks'][1]['dependency_details'][0]['type'], 'FF')
        self.assertEqual(changed['tasks'][1]['dependency_details'][0]['lag_days'], -1)
        self.assertEqual(changed['tasks'][1]['dependency_status'], 'planner')

    def test_manual_hierarchy_groups_deliverables_by_phase_and_survives_reload(self):
        state = self.save([self.task('a', wbs_phase='Design', wbs_deliverable='Approved package'),
                           self.task('b', wbs_phase='Design', wbs_deliverable='Approved package'),
                           self.task('c', wbs_phase='Delivery', wbs_deliverable='Approved package')])
        nodes = state['wbs_nodes']
        self.assertEqual(len(nodes), 4)
        phases = {node['name']: node for node in nodes if node['kind'] == 'phase'}
        deliverables = [node for node in nodes if node['kind'] == 'deliverable']
        self.assertEqual({node['parent_id'] for node in deliverables}, {node['id'] for node in phases.values()})
        self.assertEqual(state['tasks'][0]['wbs_node_id'], state['tasks'][1]['wbs_node_id'])
        self.assertNotEqual(state['tasks'][0]['wbs_node_id'], state['tasks'][2]['wbs_node_id'])
        self.assertEqual(self.read()['wbs_nodes'], nodes)
        self.project.refresh_from_db()
        version = materialize_work_breakdown(self.project, self.project.simple_planning_state,
            actor=self.owner, start=self.project.effective_date, token=f'simple:{self.project.pk}')
        self.assertEqual(version.wbs_nodes.count(), 4)
        activity = version.activities.get(external_id='a')
        self.assertEqual(activity.wbs_node.name, 'Approved package')
        self.assertEqual(activity.wbs_node.parent.name, 'Design')
        self.assertEqual(activity.metadata['wbs_phase'], 'Design')
        historical = self.client.get(self.url, {'version_id': version.pk})
        self.assertEqual(historical.status_code, 200, historical.data)
        self.assertEqual(historical.data['hierarchy_source'], 'manual_wbs')
        self.assertEqual(len([node for node in historical.data['wbs_nodes'] if node.get('kind') == 'deliverable']), 2)

    def test_exact_finish_constraint_matches_saved_schedule(self):
        state = self.save([self.task(constraint_type='must_finish', constraint_date='2026-11-12')])
        task = state['tasks'][0]
        self.assertEqual((task['planned_start_date'], task['planned_finish_date']), ('2026-11-11', '2026-11-12'))
        self.assertEqual(task['total_float_days'], 0)
        self.project.refresh_from_db()
        version = materialize_work_breakdown(self.project, self.project.simple_planning_state,
            actor=self.owner, start=self.project.effective_date, token=f'simple:{self.project.pk}')
        activity = version.activities.get()
        self.assertEqual((activity.constraint_type, activity.constraint_date), ('must_finish', date(2026, 11, 12)))

    def test_impossible_constraint_is_visible_and_blocks_submission(self):
        state = self.save([self.task('a', duration_days=3), self.task(
            'b', depends_on=['a'], constraint_type='must_start', constraint_date='2026-11-09')])
        self.assertIn('constraint_violation', {row['code'] for row in state['blockers']})
        self.assertFalse(state['permissions']['can_submit'])
        response = self.client.post(self.url + 'submit/', {'revision': state['revision']}, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertFalse(ScheduleVersion.objects.exists())

    def test_invalid_network_details_or_constraint_dates_do_not_save(self):
        cases = [
            self.task('b', depends_on=['a'], dependency_details=[]),
            self.task('b', depends_on=['a'], dependency_details=[{'task_id': 'a', 'type': 'unknown'}]),
            self.task('b', depends_on=['a'], dependency_details=[{'task_id': 'a', 'type': 'SS', 'lag_days': 366}]),
            self.task('b', constraint_type='must_start'),
            self.task('b', wbs_deliverable='Package'),
        ]
        for task in cases:
            with self.subTest(task=task):
                response = self.client.put(self.url, {'revision': 0, 'tasks': [self.task('a'), task]}, format='json')
                self.assertEqual(response.status_code, 400, response.data)
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, {})

    def test_exact_constraint_on_weekend_is_rejected_without_saving(self):
        response = self.client.put(self.url, {'revision': 0, 'tasks': [self.task(
            constraint_type='must_start', constraint_date='2026-11-08')]}, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, {})

    def test_calendar_change_keeps_invalid_exact_constraint_readable_and_repairable(self):
        calendar = WorkCalendar.objects.create(project=self.project, name='Project calendar', is_default=True,
                                               working_weekdays=[0, 1, 2, 3, 4])
        state = self.save([self.task('a', constraint_type='must_start', constraint_date='2026-11-09'),
                           self.task('b', depends_on=['a'])])
        CalendarException.objects.create(calendar=calendar, date=date(2026, 11, 9), is_working=False, name='Holiday')
        reloaded = self.read()
        self.assertEqual(reloaded['revision'], state['revision'])
        self.assertIn('constraint_nonworking_date', {row['code'] for row in reloaded['blockers']})
        self.assertFalse(reloaded['permissions']['can_submit'])
        self.assertTrue(all(row['planned_start_date'] is None for row in reloaded['tasks']))
        tasks = deepcopy(reloaded['tasks'])
        tasks[0]['constraint_date'] = '2026-11-10'
        repaired = self.save(tasks, revision=state['revision'])
        self.assertNotIn('constraint_nonworking_date', {row['code'] for row in repaired['blockers']})
        self.assertEqual(repaired['tasks'][0]['planned_start_date'], '2026-11-10')

    def test_partial_task_fields_cannot_leave_orphan_constraints_or_deliverables(self):
        state = self.save([self.task(constraint_type='must_start', constraint_date='2026-11-09',
                                    wbs_phase='Design', wbs_deliverable='Approved package')])
        for updates, code in [({'constraint_date': None}, 'simple_plan_constraint_date_required'),
                              ({'wbs_phase': ''}, 'simple_plan_wbs_phase_required')]:
            with self.subTest(updates=updates):
                response = self.client.put(self.url, {'revision': state['revision'], 'tasks': [self.task(**updates)]}, format='json')
                self.assertEqual(response.status_code, 409, response.data)
                self.assertEqual(response.data['code'], code)
        reloaded = self.read()
        self.assertEqual(reloaded['revision'], state['revision'])
        self.assertEqual(reloaded['tasks'][0]['constraint_date'], '2026-11-09')
        self.assertEqual(reloaded['tasks'][0]['wbs_phase'], 'Design')

    def test_fractional_lag_is_retained_with_explicit_calculation_precision_notice(self):
        state = self.save([self.task('a'), self.task('b', depends_on=['a'],
                           dependency_details=[{'task_id': 'a', 'type': 'SS', 'lag_days': 1.1}])])
        self.assertEqual(state['tasks'][1]['dependency_details'][0]['lag_days'], 1.1)
        self.assertEqual(state['tasks'][1]['planned_start_date'], '2026-11-10')
        self.assertIn('working_day_precision', {row['code'] for row in state['warnings']})

    def test_explicit_none_releases_legacy_start_constraint_in_draft_and_saved_version(self):
        state = self.save([self.task(planned_start_date='2026-11-12')])
        self.assertEqual(state['tasks'][0]['constraint_type'], 'start_no_earlier')
        task = {**state['tasks'][0], 'constraint_type': 'none', 'constraint_date': None}
        updated = self.save([task], revision=state['revision'])
        self.assertEqual(updated['tasks'][0]['planned_start_date'], '2026-11-06')
        self.project.refresh_from_db()
        version = materialize_work_breakdown(self.project, self.project.simple_planning_state,
            actor=self.owner, start=self.project.effective_date, token=f'simple:{self.project.pk}')
        activity = version.activities.get()
        self.assertEqual(activity.constraint_type, 'none')
        self.assertIsNone(activity.constraint_date)
