"""A blocked plan explains its repair using stable activity IDs before submit."""
from copy import deepcopy
from datetime import date

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from ..models import CalendarException, ScheduleReview, ScheduleVersion, WorkCalendar
from ..services.cpm import calculate_schedule_version
from ..services.trustworthy_scheduling import run_schedule_assurance
from ..services.work_breakdown import materialize_work_breakdown
from . import test_simple_planning as fixture


@override_settings(ROOT_URLCONF='apps.planning_intelligence.tests.test_simple_planning')
class SimpleScheduleChecksTests(TestCase):
    task = fixture.SimplePlanningTests.task
    read = fixture.SimplePlanningTests.read
    save = fixture.SimplePlanningTests.save
    action = fixture.SimplePlanningTests.action

    def setUp(self):
        fixture.SimplePlanningTests.setUp(self)

    def set_finish(self, finish):
        self.project.planned_end_date = finish
        self.project.save(update_fields=['planned_end_date'])

    def submit_error(self, revision):
        response = self.client.post(self.url + 'submit/', {'revision': revision}, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'simple_plan_schedule_blocked')
        return {row['code']: row for row in response.data['blockers']}

    def authoritative_checks(self):
        self.project.refresh_from_db()
        draft = deepcopy(self.project.simple_planning_state)
        version = materialize_work_breakdown(
            self.project, draft, actor=self.owner, start=self.project.effective_date,
            token=draft['assignment_token'],
        )
        calculate_schedule_version(version, requested_by=self.owner)
        version.refresh_from_db()
        return run_schedule_assurance(version, requested_by=self.owner)

    def test_readonly_checks_match_submit_and_authoritative_calendar_calculation(self):
        self.set_finish(date(2026, 11, 12))
        calendar = WorkCalendar.objects.create(
            project=self.project, name='Project calendar', is_default=True, working_weekdays=[0, 1, 2, 3, 4],
        )
        CalendarException.objects.create(calendar=calendar, date=date(2026, 11, 9), is_working=False)
        saved = self.save([self.task('a', duration_days=2), self.task('b', duration_days=3, depends_on=['a'])])
        self.project.refresh_from_db()
        before = deepcopy(self.project.simple_planning_state)
        with CaptureQueriesContext(connection) as queries:
            plan = self.read()
        self.assertFalse([row['sql'] for row in queries
                          if row['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))])
        self.assertFalse(plan['permissions']['can_submit'])
        self.assertEqual(plan['revision'], saved['revision'])
        expected = {row['code']: row for row in plan['blockers']}
        self.assertEqual(expected['negative_float']['task_ids'], ['a', 'b'])
        self.assertEqual(expected['negative_float']['minimum_float_days'], -1)
        overrun = expected['contract_finish_overrun']
        self.assertEqual(overrun['task_ids'], ['b'])
        self.assertEqual(overrun['target_finish_date'], '2026-11-12')
        self.assertEqual(overrun['forecast_finish_date'], '2026-11-13')
        self.assertEqual(overrun['variance_working_days'], 1)
        self.assertEqual(self.submit_error(saved['revision']), expected)
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, before)
        self.assertFalse(ScheduleVersion.objects.filter(schedule__project=self.project).exists())
        self.assertFalse(ScheduleReview.objects.exists())
        assurance = self.authoritative_checks()
        actual = {row['code']: row for row in assurance.blockers}
        for code in expected:
            for key in ('task_ids', 'field', 'resolution', 'target_finish_date', 'forecast_finish_date',
                        'minimum_float_days', 'variance_working_days'):
                self.assertEqual(actual[code].get(key), expected[code].get(key), (code, key))
            self.assertEqual([row['task_id'] for row in actual[code]['affected_activities']], expected[code]['task_ids'])

    def test_real_duration_correction_clears_checks_and_allows_submission(self):
        self.set_finish(date(2026, 11, 10))
        saved = self.save([self.task('a', duration_days=2), self.task('b', duration_days=3, depends_on=['a'])])
        self.submit_error(saved['revision'])
        corrected = self.save([self.task('a', duration_days=2), self.task('b', duration_days=1, depends_on=['a'])],
                              revision=saved['revision'])
        self.assertEqual(corrected['blockers'], [])
        self.assertTrue(corrected['permissions']['can_submit'])
        submitted = self.action('submit', corrected['revision'])
        self.assertEqual(submitted['state'], 'submitted')
        self.assertEqual(ScheduleReview.objects.get().status, 'pending')
        self.assertEqual(self.project.planned_end_date, date(2026, 11, 10))

    def test_every_affected_activity_is_returned_when_more_than_25_are_blocked(self):
        self.set_finish(date(2026, 11, 9))
        ids = [f'late-{index}' for index in range(31)]
        saved = self.save([self.task(key, duration_days=3) for key in ids])
        for plan in (saved, self.read()):
            for finding in plan['blockers']:
                self.assertEqual(finding['task_ids'], ids)
                self.assertEqual(finding['task_count'], 31)
                self.assertEqual(len(finding['affected_activities']), 31)
        for finding in self.submit_error(saved['revision']).values():
            self.assertEqual(finding['task_ids'], ids)
        for finding in self.authoritative_checks().blockers:
            self.assertEqual(finding['task_ids'], ids)
            self.assertEqual(len(finding['affected_activities']), 31)

    def test_start_to_finish_is_shown_before_submission_without_changing_the_link(self):
        saved = self.save([self.task('a'), self.task('b', depends_on=['a'])])
        self.project.refresh_from_db()
        self.project.simple_planning_state['tasks'][1]['dependency_details'] = [
            {'task_id': 'a', 'type': 'SF', 'lag_days': 1},
        ]
        self.project.save(update_fields=['simple_planning_state'])
        plan = self.read()
        finding = next(row for row in plan['blockers'] if row['code'] == 'start_to_finish')
        self.assertEqual(finding['task_ids'], ['b'])
        self.assertEqual(finding['field'], 'depends_on')
        self.assertEqual(finding['relationships'], [{
            'predecessor_task_id': 'a', 'successor_task_id': 'b', 'type': 'SF', 'lag_days': 1,
        }])
        self.assertEqual(self.submit_error(saved['revision'])['start_to_finish'], finding)
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state['tasks'][1]['dependency_details'][0]['type'], 'SF')
        self.assertFalse(ScheduleVersion.objects.filter(schedule__project=self.project).exists())

    def test_resource_rejection_retains_full_activity_and_resource_details_after_rollback(self):
        saved = self.save([self.task('a', owner='Engineer', duration_days=1, effort_hours=16),
                           self.task('b', owner='Engineer', duration_days=1, effort_hours=8),
                           self.task('later', owner='Engineer', duration_days=1, effort_hours=8,
                                     planned_start_date='2026-11-12')])
        self.assertEqual(saved['blockers'], [])
        finding = self.submit_error(saved['revision'])['resource_overallocation']
        self.assertEqual(finding['task_ids'], ['a', 'b'])
        self.assertEqual(finding['field'], 'assignee_id')
        self.assertEqual(len(finding['resource_ids']), 1)
        self.assertEqual(finding['resources'][0]['resource_name'], 'Engineer')
        self.assertEqual(finding['resources'][0]['peak_demand'], 24)
        self.assertEqual(finding['resources'][0]['task_ids'], ['a', 'b'])
        self.assertEqual({row['task_id'] for row in finding['affected_activities']}, {'a', 'b'})
        self.assertTrue(finding['resolution'])
        self.assertFalse(ScheduleVersion.objects.filter(schedule__project=self.project).exists())
        self.assertEqual(self.read()['revision'], saved['revision'])

    def test_stale_revision_and_outside_project_access_still_block_check_submission(self):
        self.set_finish(date(2026, 11, 9))
        saved = self.save([self.task(duration_days=3)])
        stale = self.client.post(self.url + 'submit/', {'revision': 0}, format='json')
        self.assertEqual(stale.status_code, 409)
        self.assertNotEqual(stale.data['code'], 'simple_plan_schedule_blocked')
        self.assertEqual(self.read()['revision'], saved['revision'])
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url + 'submit/', {'revision': saved['revision']}, format='json').status_code, 404)
