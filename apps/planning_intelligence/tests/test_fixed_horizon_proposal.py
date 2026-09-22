"""Fixed-date proposals preserve evidence and never conceal an impossible network."""
from copy import deepcopy
from datetime import date
from unittest import TestCase
from unittest.mock import patch

from ..services.cpm import WorkdayCalendar
from ..services.fixed_horizon_proposal import fit_proposed_schedule
from ..services.simple_schedule_proposal import build_proposed_tasks, retain_supported_dependencies


class FixedHorizonProposalTests(TestCase):
    def setUp(self):
        self.enterContext(patch('django.db.backends.base.base.BaseDatabaseWrapper.ensure_connection',
                                side_effect=AssertionError('Proposal fitting must not access the database.')))
        self.context = {'start_date': date(2026, 1, 6), 'finish_date': date(2026, 9, 4)}
        self.calendar = WorkdayCalendar(None, self.context['start_date'])

    def task(self, key, duration, start=None, predecessor=None, **extra):
        return {'id': key, 'title': key, 'duration_days': duration, 'duration_source': 'proposed',
                'planned_start_date': start, 'depends_on': [predecessor] if predecessor else [],
                'schedule_generated_fields': ['duration_days', 'planned_start_date', 'depends_on'], **extra}

    def fit(self, tasks, **kwargs):
        return fit_proposed_schedule(tasks, self.context, self.calendar, **kwargs)

    def test_late_generated_package_and_closeout_move_inside_existing_dates_without_shortening(self):
        tasks = [self.task('package', 31, '2026-07-08'), self.task('closeout', 31, '2026-08-20', 'package')]
        before = deepcopy(tasks)
        result, summary, warnings = self.fit(tasks)
        self.assertEqual(summary['original_forecast_finish'], '2026-10-01')
        self.assertEqual(summary['forecast_finish'], '2026-09-04')
        self.assertTrue(summary['fits'])
        self.assertEqual([row['planned_start_date'] for row in result], ['2026-06-11', '2026-07-24'])
        self.assertEqual([row['duration_days'] for row in result], [31, 31])
        self.assertEqual(result[1]['depends_on'], ['package'])
        self.assertEqual(summary['resized_activity_ids'], [])
        self.assertEqual(tasks, before)
        self.assertEqual(warnings, [])

    def test_generated_estimates_can_shrink_but_project_end_never_moves(self):
        self.context = {'start_date': date(2026, 1, 5), 'finish_date': date(2026, 1, 30)}
        self.calendar = WorkdayCalendar(None, self.context['start_date'])
        rows = [self.task('a', 20, '2026-01-05'), self.task('b', 20, None, 'a')]
        fitted, summary, warnings = self.fit(rows)
        self.assertEqual([row['duration_days'] for row in fitted], [10, 10])
        self.assertEqual(summary['forecast_finish'], '2026-01-30')
        self.assertEqual(summary['finish_date'], '2026-01-30')
        self.assertTrue(warnings)
        self.assertTrue(all(row['duration_source'] == 'proposed' for row in fitted))

    def test_explicit_company_review_requirement_is_never_compressed(self):
        self.context['finish_date'] = date(2026, 1, 26)
        rows = [self.task('ifr', 10, '2026-01-06'),
                self.task('review', 10, None, 'ifr', workflow_stage_code='COMPANY_REVIEW'),
                self.task('issue', 10, None, 'review')]
        fitted, summary, _ = self.fit(rows, review_requirement={'duration_days': 10})
        self.assertTrue(summary['fits'])
        self.assertEqual(fitted[1]['duration_days'], 10)
        self.assertLessEqual(summary['forecast_finish'], '2026-01-26')

    def test_manual_start_and_source_duration_remain_visible_when_they_cannot_fit(self):
        row = self.task('source', 31, '2026-08-20', duration_source='source', schedule_generated_fields=[])
        fitted, summary, warnings = self.fit([row])
        self.assertFalse(summary['fits'])
        self.assertEqual(summary['forecast_finish'], '2026-10-01')
        self.assertEqual(summary['finish_date'], '2026-09-04')
        self.assertEqual(fitted, [row])
        self.assertIn('does not prevent submission', warnings[0])

    def test_started_work_and_its_upstream_chain_are_not_shifted(self):
        rows = [self.task('a', 31, '2026-07-08'), self.task('b', 31, '2026-08-20', 'a', status='in_progress')]
        fitted, summary, _ = self.fit(rows)
        self.assertFalse(summary['fits'])
        self.assertEqual(fitted, rows)

    def test_typed_links_lags_and_zero_duration_milestones_are_preserved(self):
        rows = [self.task('a', 4, '2026-09-01'), self.task('b', 4, None, 'a',
                dependency_details=[{'task_id': 'a', 'type': 'SS', 'lag_days': 2}]),
                self.task('end', 0, None, 'b', is_milestone=True)]
        fitted, summary, _ = self.fit(rows)
        self.assertTrue(summary['fits'])
        self.assertEqual(fitted[1]['dependency_details'], rows[1]['dependency_details'])
        self.assertEqual(fitted[2]['duration_days'], 0)
        self.assertEqual(summary['forecast_finish'], '2026-09-04')

    def test_actual_working_calendar_exceptions_are_used_without_inventing_holidays(self):
        self.calendar.exceptions[date(2026, 9, 3)] = False
        fitted, summary, _ = self.fit([self.task('a', 5, '2026-09-01')])
        self.assertTrue(summary['fits'])
        self.assertEqual(fitted[0]['planned_start_date'], '2026-08-28')
        self.assertEqual(summary['forecast_finish'], '2026-09-04')
        self.assertEqual(self.calendar.exceptions, {date(2026, 9, 3): False})

    def test_repeated_fit_does_not_continue_shortening_or_moving_the_plan(self):
        rows = [self.task('a', 31, '2026-07-08'), self.task('b', 31, '2026-08-20', 'a')]
        first, _, _ = self.fit(rows)
        second, _, _ = self.fit(first)
        self.assertEqual(first, second)

    def test_only_unconfirmed_inferred_cross_deliverable_links_are_removed(self):
        inferred = {'task_id': 'a', 'source': 'deliverable_sequence', 'status': 'proposed', 'evidence_type': 'planning_inference'}
        rows = [self.task('a', 5), self.task('b', 5, predecessor='a', dependency_details=[inferred]),
                self.task('c', 5, predecessor='a', dependency_details=[{**inferred, 'source': 'planner', 'status': 'confirmed'}]),
                self.task('d', 5, predecessor='a', dependency_details=[{**inferred, 'source': 'workflow_template'}])]
        fitted, count = retain_supported_dependencies(rows)
        self.assertEqual(count, 1)
        self.assertEqual(fitted[1]['depends_on'], [])
        self.assertEqual(fitted[2]['depends_on'], ['a'])
        self.assertEqual(fitted[3]['depends_on'], ['a'])
        self.assertEqual(rows[1]['depends_on'], ['a'])

    def test_source_start_is_protected_even_with_an_old_generated_marker(self):
        row = self.task('source', 31, '2026-08-20', source_timing={'file_id': 1}, duration_source='source')
        fitted, summary, _ = self.fit([row])
        self.assertFalse(summary['fits'])
        self.assertEqual(fitted, [row])

    def test_negative_lag_with_source_values_is_not_clipped_to_fake_a_fit(self):
        rows = [self.task('a', 1, '2026-01-06', duration_source='source'),
                self.task('b', 1, None, 'a', duration_source='source', schedule_generated_fields=[],
                          dependency_details=[{'task_id': 'a', 'type': 'SS', 'lag_days': -2}])]
        fitted, summary, _ = self.fit(rows)
        self.assertFalse(summary['fits'])
        self.assertEqual(fitted, rows)

    def test_negative_lag_in_an_editable_proposal_gets_a_real_start_constraint(self):
        rows = [self.task('a', 1, '2026-01-06'), self.task('b', 1, None, 'a',
                dependency_details=[{'task_id': 'a', 'type': 'SS', 'lag_days': -2}])]
        fitted, summary, _ = self.fit(rows)
        self.assertTrue(summary['fits'])
        self.assertEqual(fitted[1]['planned_start_date'], '2026-01-06')
        self.assertIn('planned_start_date', fitted[1]['schedule_generated_fields'])

    def test_infeasible_source_review_period_is_not_described_as_successfully_fitted(self):
        self.context['finish_date'] = date(2026, 1, 8)
        rows = [self.task('review', 5, '2026-01-06', workflow_stage_code='COMPANY_REVIEW')]
        fitted, summary, _ = self.fit(rows, review_requirement={'duration_days': 10})
        self.assertFalse(summary['fits'])
        self.assertEqual(fitted[0]['duration_days'], 10)
        self.assertNotIn('timing fitted within', fitted[0]['schedule_rationale'])

    def test_source_only_rebuild_preserves_active_work_and_its_upstream_links(self):
        inferred = {'task_id': 'a', 'source': 'deliverable_sequence', 'status': 'proposed', 'evidence_type': 'planning_inference'}
        rows = [self.task('a', 7, '2026-04-01', discipline='general'),
                self.task('b', 8, '2026-06-01', 'a', discipline='general', status='in_progress', dependency_details=[inferred])]
        context = {**self.context, 'files': [], 'templates': [], 'overrides': [],
                   'default_template_id': None, 'project_id': 1, 'calendar': {'hours_per_day': 8},
                   'dependency_policy': 'source_or_planner'}
        proposed, _, _, _ = build_proposed_tasks(rows, context, self.calendar)
        for original, new in zip(rows, proposed):
            for field in ('duration_days', 'planned_start_date', 'depends_on', 'dependency_details'):
                self.assertEqual(new.get(field), original.get(field))
