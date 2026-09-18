"""Draft float uses the same working-day network as persisted schedule CPM."""
from copy import deepcopy
from datetime import date

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from ..models import CalendarException, Schedule, ScheduleActivity, ScheduleVersion, WorkCalendar
from ..services.cpm import calculate_schedule_version
from ..services.simple_planning import _dated_tasks
from ..services.work_breakdown import materialize_work_breakdown
from . import test_simple_planning as simple_fixture


@override_settings(ROOT_URLCONF='apps.planning_intelligence.tests.test_simple_planning')
class SimpleDraftFloatTests(TestCase):
    task = simple_fixture.SimplePlanningTests.task
    read = simple_fixture.SimplePlanningTests.read
    save = simple_fixture.SimplePlanningTests.save
    action = simple_fixture.SimplePlanningTests.action

    def setUp(self):
        simple_fixture.SimplePlanningTests.setUp(self)

    def set_horizon(self, start, finish):
        self.project.effective_date = start
        self.project.planned_end_date = finish
        self.project.save(update_fields=['effective_date', 'planned_end_date'])

    def assert_no_writes(self, queries):
        self.assertFalse([
            query['sql'] for query in queries
            if query['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))
        ])

    def assert_matches_persisted_cpm(self, tasks):
        """Materialize typed workflow leaves without using draft output as constraints."""
        source = deepcopy(tasks)
        for task in source:
            task.setdefault('source_references', [])
            task.setdefault('document_number', '')
            task.setdefault('document_revision', '')
            task['parent_deliverable_id'] = 'source-parent'
        draft = {
            'revision': 1,
            'tasks': source,
            'deliverables': [{'id': 'source-parent', 'title': 'Source deliverable', 'discipline': 'testing'}],
            'disciplines': [{'code': 'testing', 'name': 'Testing'}],
        }
        unchanged = deepcopy(source)
        calculated = {task['id']: task for task in _dated_tasks(self.project, source)}
        self.assertEqual(source, unchanged)
        version = materialize_work_breakdown(
            self.project, draft, actor=self.owner, start=self.project.effective_date, token='draft-float-fixture',
        )
        run = calculate_schedule_version(version, requested_by=self.owner)
        self.assertEqual(run.status, 'succeeded')
        for activity in version.activities.all():
            task = calculated[activity.external_id]
            with self.subTest(activity=activity.external_id):
                self.assertEqual(task['planned_start_date'], activity.planned_start.isoformat())
                self.assertEqual(task['planned_finish_date'], activity.planned_finish.isoformat())
                self.assertEqual(task['early_start'], activity.early_start.isoformat())
                self.assertEqual(task['early_finish'], activity.early_finish.isoformat())
                self.assertEqual(task['late_start'], activity.late_start.isoformat())
                self.assertEqual(task['late_finish'], activity.late_finish.isoformat())
                self.assertEqual(task['total_float_days'], float(activity.total_float_days))
                self.assertEqual(task['free_float_days'], float(activity.free_float_days))
                self.assertEqual(task['is_critical'], activity.is_critical)
        return calculated

    def test_registered_target_exposes_positive_zero_and_negative_float_before_submission(self):
        revision = 0
        for target, expected in [(date(2026, 1, 9), 1), (date(2026, 1, 8), 0), (date(2026, 1, 7), -1)]:
            with self.subTest(target=target):
                self.set_horizon(date(2026, 1, 6), target)
                task = self.task(duration_days=3)
                saved = self.save([task], revision=revision)
                revision = saved['revision']
                self.assertTrue(saved['calculation_available'])
                self.assertEqual(saved['calculation_basis'], 'draft_cpm')
                self.assertTrue(saved['tasks'][0]['calculated'])
                self.assertEqual(saved['tasks'][0]['total_float_days'], expected)
                self.assertEqual(saved['tasks'][0]['is_critical'], expected <= 0)
                self.assertEqual(saved['project_summary']['total_float_days'], expected)
                self.assertIsNone(saved['version_id'])
                calculated = self.assert_matches_persisted_cpm([task])
                self.assertEqual(calculated['task-a']['total_float_days'], expected)

    def test_draft_get_recalculates_calendar_float_without_persisting_a_version_or_state(self):
        self.set_horizon(date(2026, 11, 6), date(2026, 11, 12))
        saved = self.save([self.task(duration_days=4)])
        self.assertEqual(saved['tasks'][0]['total_float_days'], 1)
        calendar = WorkCalendar.objects.create(
            project=self.project, name='Draft calendar', is_default=True, working_weekdays=[0, 1, 2, 3, 4],
        )
        CalendarException.objects.create(calendar=calendar, date=date(2026, 11, 9), is_working=False, name='Holiday')
        self.project.refresh_from_db()
        before = deepcopy(self.project.simple_planning_state)
        with CaptureQueriesContext(connection) as queries:
            refreshed = self.read()
        self.assert_no_writes(queries)
        self.assertEqual(refreshed['tasks'][0]['total_float_days'], 0)
        self.assertTrue(refreshed['tasks'][0]['is_critical'])
        self.assertEqual(refreshed['tasks'][0]['planned_finish_date'], '2026-11-12')
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, before)
        self.assertFalse(ScheduleVersion.objects.filter(schedule__project=self.project).exists())

    def test_all_relationship_types_fractional_lags_and_multiple_constraints_match_saved_cpm(self):
        self.set_horizon(date(2026, 1, 6), date(2026, 1, 16))
        tasks = [
            self.task('a', duration_days=4),
            self.task('b', duration_days=2, depends_on=['a'],
                      dependency_details=[{'task_id': 'a', 'type': 'SS', 'lag_days': 1.5},
                                          {'task_id': 'a', 'type': 'FF', 'lag_days': 0}]),
            self.task('c', duration_days=2.5, depends_on=['a'],
                      dependency_details=[{'task_id': 'a', 'type': 'FF', 'lag_days': -1}]),
            self.task('d', duration_days=1, depends_on=['a'],
                      dependency_details=[{'task_id': 'a', 'type': 'SF', 'lag_days': 2}]),
            self.task('e', duration_days=2, depends_on=['a'],
                      dependency_details=[{'task_id': 'a', 'type': 'FS', 'lag_days': .25}]),
            self.task('f', duration_days=0, activity_type='finish_milestone', is_milestone=True,
                      depends_on=['b', 'c', 'd', 'e']),
        ]
        calculated = self.assert_matches_persisted_cpm(tasks)
        self.assertEqual(calculated['b']['planned_start_date'], '2026-01-08')
        self.assertEqual(calculated['c']['planned_start_date'], '2026-01-06')
        self.assertEqual(calculated['d']['planned_start_date'], '2026-01-07')
        self.assertEqual(calculated['e']['planned_start_date'], '2026-01-13')
        self.assertEqual(calculated['f']['planned_start_date'], '2026-01-15')
        self.assertEqual(calculated['f']['planned_start_date'], calculated['f']['planned_finish_date'])
        self.assertEqual(calculated['a']['total_float_days'], 1)
        self.assertEqual(calculated['c']['total_float_days'], 5)

    def test_parallel_branch_and_terminal_milestone_respect_working_and_nonworking_exceptions(self):
        self.set_horizon(date(2026, 11, 6), date(2026, 11, 12))
        calendar = WorkCalendar.objects.create(
            project=self.project, name='Site calendar', is_default=True, working_weekdays=[0, 1, 2, 3, 4],
        )
        CalendarException.objects.create(calendar=calendar, date=date(2026, 11, 7), is_working=True, name='Working Saturday')
        CalendarException.objects.create(calendar=calendar, date=date(2026, 11, 9), is_working=False, name='Holiday')
        tasks = [self.task('a', duration_days=2),
                 self.task('b', duration_days=2, depends_on=['a']),
                 self.task('c', duration_days=1, depends_on=['a']),
                 self.task('m', duration_days=0, activity_type='finish_milestone', is_milestone=True,
                           depends_on=['b', 'c'])]
        calculated = self.assert_matches_persisted_cpm(tasks)
        self.assertEqual(calculated['a']['planned_finish_date'], '2026-11-07')
        self.assertEqual(calculated['b']['planned_start_date'], '2026-11-10')
        self.assertEqual(calculated['m']['planned_finish_date'], '2026-11-12')
        self.assertEqual({key: task['total_float_days'] for key, task in calculated.items()},
                         {'a': 0, 'b': 0, 'c': 1, 'm': 0})

    def test_missing_target_uses_network_finish_with_float_on_the_shorter_branch(self):
        self.set_horizon(date(2026, 11, 6), None)
        tasks = [self.task('a', duration_days=2), self.task('b', duration_days=4, depends_on=['a']),
                 self.task('c', duration_days=1, depends_on=['a']),
                 self.task('d', duration_days=1, depends_on=['b', 'c'])]
        saved = self.save(tasks)
        self.assertTrue(saved['calculation_available'])
        self.assertEqual({task['id']: task['total_float_days'] for task in saved['tasks']},
                         {'a': 0, 'b': 0, 'c': 3, 'd': 0})
        self.assert_matches_persisted_cpm(tasks)

    def test_only_zero_duration_milestones_match_inclusive_deadline_and_next_workday(self):
        milestone = self.task(duration_days=0, activity_type='finish_milestone', is_milestone=True)
        for target, requested_start, expected in [
            (date(2026, 11, 6), None, 0),
            (date(2026, 11, 6), '2026-11-09', -1),
            (None, None, 0),
        ]:
            with self.subTest(target=target, requested_start=requested_start):
                self.set_horizon(date(2026, 11, 6), target)
                task = {**milestone, 'planned_start_date': requested_start}
                calculated = self.assert_matches_persisted_cpm([task])
                self.assertEqual(calculated['task-a']['total_float_days'], expected)
                self.assertEqual(calculated['task-a']['planned_start_date'], requested_start or '2026-11-06')
                self.assertEqual(calculated['task-a']['planned_finish_date'], requested_start or '2026-11-06')

    def test_empty_or_undated_draft_keeps_float_unknown(self):
        empty = self.read()
        self.assertEqual(empty['tasks'], [])
        self.assertFalse(empty['calculation_available'])
        self.assertIsNone(empty['project_summary']['total_float_days'])
        self.set_horizon(None, date(2026, 12, 20))
        saved = self.save()
        self.assertFalse(saved['calculation_available'])
        self.assertIsNone(saved['tasks'][0]['total_float_days'])
        self.assertIsNone(saved['tasks'][0]['is_critical'])
        self.assertFalse(saved['tasks'][0]['calculated'])

    def test_schedule_proposal_exposes_draft_float_without_writes(self):
        saved = self.save()
        self.project.refresh_from_db()
        before = deepcopy(self.project.simple_planning_state)
        with CaptureQueriesContext(connection) as queries:
            preview = self.action('propose-schedule', saved['revision'])
        self.assert_no_writes(queries)
        self.assertTrue(preview['plan']['calculation_available'])
        self.assertEqual(preview['plan']['calculation_basis'], 'draft_cpm')
        self.assertTrue(all(task['total_float_days'] is not None for task in preview['plan']['tasks']))
        self.assertTrue(all(task['calculated'] for task in preview['plan']['tasks']))
        self.assertIsNone(preview['plan']['version_id'])
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, before)
        self.assertFalse(ScheduleVersion.objects.filter(schedule__project=self.project).exists())

    def test_historical_version_retains_stored_zero_negative_and_unknown_float(self):
        self.save()
        schedule = Schedule.objects.create(project=self.project, name='Stored source schedule', code='SOURCE',
                                           planned_start=date(2026, 1, 6))
        version = ScheduleVersion.objects.create(schedule=schedule, version=7, status='calculated',
                                                 calculated_at=timezone.now())
        for index, value in enumerate([0, -3, None]):
            ScheduleActivity.objects.create(
                version=version, external_id=f'SOURCE-{index}', name=f'Stored activity {index}',
                discipline='testing', duration_days=2, sort_order=index,
                planned_start=date(2026, 1, 6), planned_finish=date(2026, 1, 7),
                total_float_days=value, is_critical=value is not None and value <= 0,
            )
        before = list(version.activities.order_by('pk').values())
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url, {'version_id': version.pk})
        self.assertEqual(response.status_code, 200, response.data)
        self.assert_no_writes(queries)
        self.assertTrue(response.data['viewing_history'])
        self.assertEqual(response.data['calculation_basis'], 'saved_version_cpm')
        self.assertEqual([task['total_float_days'] for task in response.data['tasks']], [0, -3, None])
        self.assertEqual([task['is_critical'] for task in response.data['tasks']], [True, True, False])
        self.assertTrue(all(task['planned_start_date'] == '2026-01-06' for task in response.data['tasks']))
        self.assertTrue(all(task['planned_finish_date'] == '2026-01-07' for task in response.data['tasks']))
        self.assertEqual(list(version.activities.order_by('pk').values()), before)
