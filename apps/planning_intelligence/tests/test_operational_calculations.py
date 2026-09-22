"""Pure regression tests for approved-policy controls and frozen-network forecasts."""
from copy import deepcopy
from decimal import Decimal
import json
import unittest

from ..services.operational_calculations import calculate_operational_report


def inputs(count=2):
    baseline = {'activities': [{'id': index, 'external_id': f'A-{index}', 'name': f'Activity {index}',
        'activity_type': 'task', 'duration_days': '5', 'planned_start': '2026-10-05', 'planned_finish': '2026-10-09',
        'calendar': 7, 'constraint_type': 'none'} for index in range(1, count + 1)], 'relationships': [],
        'accepted_inputs': {'default_calendar_id': 7, 'project_start': '2026-10-05', 'project_finish': '2026-10-23',
            'calendars': [{'id': 7, 'working_weekdays': [0, 1, 2, 3, 4], 'hours_per_day': '8', 'exceptions': []}]}}
    policy = {'currency': 'AED', 'activities': [{'activity_id': index, 'method': 'manual_percent', 'weight': '1',
        'budget': '100', 'planned_quantity': None, 'quantity_unit': '', 'pv_method': 'working_day_linear',
        'planned_value': []} for index in range(1, count + 1)]}
    observations = [{'activity_id': index, 'actual_start': '2026-10-05', 'actual_finish': None,
        'physical_progress_pct': '50', 'installed_quantity': None, 'remaining_duration_days': '2', 'evidence': 'Accepted record',
        'notes': ''} for index in range(1, count + 1)]
    return baseline, policy, observations


def run_report(baseline, policy, observations, *, day='2026-10-07', actuals=None, confirmed=True):
    return calculate_operational_report(baseline, policy, observations, day,
        actuals if actuals is not None else {'costs_by_currency': {'AED': '80'}}, cost_coverage_confirmed=confirmed)


class OperationalCalculationTests(unittest.TestCase):
    def test_working_day_pv_approved_weights_and_unrounded_eac(self):
        baseline, policy, observations = inputs()
        baseline['accepted_inputs']['calendars'][0]['exceptions'] = [{'date': '2026-10-06', 'is_working': False}]
        policy['activities'][0]['weight'] = '3'
        observations[0]['physical_progress_pct'] = '100'
        observations[0]['actual_finish'] = '2026-10-07'
        observations[0]['remaining_duration_days'] = '0'
        observations[1]['physical_progress_pct'] = '0'
        report = run_report(baseline, policy, observations)
        metrics = report['metrics']
        self.assertEqual(metrics['planned_value'], '100.00')  # Two of four working days, not three of five calendar days.
        self.assertEqual(metrics['earned_value'], '100.00')
        self.assertEqual(metrics['progress_pct'], '75.00')
        self.assertEqual(metrics['spi'], '1.0000')
        self.assertEqual(metrics['cpi'], '1.2500')
        self.assertEqual(metrics['eac'], '160.00')
        self.assertEqual(metrics['planned_progress_pct'], '50.00')
        self.assertEqual(metrics['remaining_progress_pct'], '25.00')
        self.assertEqual([row['remaining_progress_pct'] for row in report['activity_comparisons']], ['0.00', '100.00'])
        self.assertEqual(metrics['bac'], '200.00')
        self.assertEqual(metrics['etc'], '80.00')

    def test_remaining_progress_uses_approved_weights_and_does_not_require_money(self):
        baseline, policy, observations = inputs()
        policy['currency'] = None
        for row in policy['activities']:
            row['budget'] = None
        policy['activities'][0]['weight'] = '3'
        observations[0]['physical_progress_pct'] = '20'
        observations[1]['physical_progress_pct'] = '80'
        report = run_report(baseline, policy, observations, confirmed=False)
        self.assertEqual(report['metrics']['progress_pct'], '35.00')
        self.assertEqual(report['metrics']['remaining_progress_pct'], '65.00')
        self.assertEqual(report['metrics']['planned_progress_pct'], '60.00')
        self.assertEqual([row['remaining_progress_pct'] for row in report['activity_comparisons']], ['80.00', '20.00'])
        self.assertIsNone(report['metrics']['bac'])
        self.assertIsNone(report['metrics']['etc'])

    def test_remaining_progress_is_unknown_for_missing_or_invalid_measurements(self):
        baseline, policy, observations = inputs()
        missing = run_report(baseline, policy, observations[:1])
        self.assertIsNone(missing['metrics']['remaining_progress_pct'])
        self.assertIsNone(missing['activity_comparisons'][1]['remaining_progress_pct'])
        self.assertIn('progress_observations_incomplete', missing['metrics']['null_reasons']['remaining_progress_pct'])
        self.assertNotIn('currency_not_specified', missing['metrics']['null_reasons']['remaining_progress_pct'])
        observations[0]['physical_progress_pct'] = '101'
        invalid = run_report(baseline, policy, observations)
        self.assertIsNone(invalid['metrics']['remaining_progress_pct'])
        self.assertIsNone(invalid['activity_comparisons'][0]['remaining_progress_pct'])

    def test_missing_weights_do_not_invent_project_remaining_progress(self):
        baseline, policy, observations = inputs()
        policy['activities'][0]['weight'] = None
        report = run_report(baseline, policy, observations)
        self.assertIsNone(report['metrics']['remaining_progress_pct'])
        self.assertEqual(report['metrics']['null_reasons']['remaining_progress_pct'], ['approved_weights_incomplete'])
        self.assertEqual(report['activity_comparisons'][0]['remaining_progress_pct'], '50.00')

    def test_eac_never_divides_by_display_rounded_cpi(self):
        baseline, policy, observations = inputs(1)
        policy['activities'][0]['budget'] = '10000'
        observations[0]['physical_progress_pct'] = '33.33'
        result = run_report(baseline, policy, observations, actuals={'costs_by_currency': {'AED': '9999'}})
        self.assertEqual(result['metrics']['cpi'], '0.3333')
        self.assertEqual(result['metrics']['eac'], '30000.00')

    def test_no_observations_or_weights_are_not_zero_or_duration_weighted(self):
        baseline, policy, observations = inputs()
        policy['activities'][0]['weight'] = None
        baseline['activities'][0]['duration_days'] = '300'
        result = run_report(baseline, policy, observations)
        self.assertIsNone(result['metrics']['progress_pct'])
        self.assertIn('approved_weights_incomplete', result['metrics']['null_reasons']['progress_pct'])
        missing = run_report(baseline, policy, observations[:1])
        self.assertIsNone(missing['metrics']['earned_value'])
        self.assertIsNone(missing['activity_comparisons'][1]['physical_progress_pct'])
        self.assertIsNone(missing['forecast']['forecast_finish'])
        self.assertEqual(missing['forecast']['status'], 'partial')

    def test_quantity_and_declared_milestone_methods_do_not_infer_from_dates(self):
        baseline, policy, observations = inputs(3)
        policy['activities'][0].update(method='quantity', planned_quantity='200', quantity_unit='m')
        observations[0]['installed_quantity'] = '50'
        policy['activities'][1]['method'] = 'zero_hundred'
        policy['activities'][2]['method'] = 'fifty_fifty'
        result = run_report(baseline, policy, observations)
        self.assertEqual([row['physical_progress_pct'] for row in result['activity_comparisons']], ['25.00', '0.00', '50.00'])
        observations[0]['installed_quantity'] = None
        result = run_report(baseline, policy, observations)
        self.assertIsNone(result['activity_comparisons'][0]['physical_progress_pct'])

    def test_explicit_points_are_step_values_without_interpolation_or_initial_zero(self):
        baseline, policy, observations = inputs(1)
        policy['activities'][0].update(pv_method='explicit_points', planned_value=[
            {'date': '2026-10-06', 'value': '25'}, {'date': '2026-10-09', 'value': '100'}])
        report = run_report(baseline, policy, observations)
        self.assertEqual(report['metrics']['planned_value'], '25.00')
        self.assertIsNone(report['metrics']['planned_curve'][0]['planned_value'])
        policy['activities'][0]['planned_value'].append({'date': '2026-10-12', 'value': '50'})
        invalid = run_report(baseline, policy, observations)
        self.assertIsNone(invalid['metrics']['planned_value'])

    def test_missing_budget_currency_and_cost_coverage_never_fabricate_metrics(self):
        baseline, policy, observations = inputs(1)
        no_confirmation = run_report(baseline, policy, observations, confirmed=False)
        self.assertIsNone(no_confirmation['metrics']['actual_cost'])
        self.assertIsNone(no_confirmation['metrics']['cpi'])
        no_currency = run_report(baseline, {**policy, 'currency': None}, observations)
        self.assertIsNone(no_currency['metrics']['earned_value'])
        self.assertEqual(no_currency['metrics']['progress_pct'], '50.00')
        policy['activities'][0]['budget'] = None
        self.assertIsNone(run_report(baseline, policy, observations)['metrics']['earned_value'])

    def test_cost_adapter_pending_or_invalid_coverage_blocks_even_confirmed_ac(self):
        baseline, policy, observations = inputs(1)
        for actuals in (
            {'costs_by_currency': {'AED': '80'}, 'coverage': {'costs': {'included': 2, 'draft': 1}}},
            {'costs_by_currency': {'AED': '80'}, 'coverage': {'hours': {'submitted': 1}}},
            {'costs_by_currency': {'AED': '80'}, 'issues': [{'code': 'approved_hours_not_posted', 'message': 'Missing labour cost.', 'severity': 'warning'}]},
        ):
            with self.subTest(actuals=actuals):
                result = run_report(baseline, policy, observations, actuals=actuals)
                self.assertIsNone(result['metrics']['actual_cost'])
                self.assertIn('actual_cost_source_coverage_incomplete', result['metrics']['null_reasons']['actual_cost'])
        clean = run_report(baseline, policy, observations, actuals={'costs_by_currency': {'AED': '80'},
            'coverage': {'costs': {'included': 2, 'future': 1, 'reversed': 1}, 'hours': {'future': 3}},
            'issues': [{'code': 'activity_bridge_missing', 'message': 'No activity allocation.'}]})
        self.assertEqual(clean['metrics']['actual_cost'], '80.00')
        self.assertIn('activity_bridge_missing', {row['code'] for row in clean['issues']})

    def test_mixed_currency_and_absent_cost_are_unknown_while_explicit_zero_is_known(self):
        baseline, policy, observations = inputs(1)
        for costs in ({'AED': '80', 'USD': '5'}, {}, {'AED': 'NaN'}):
            with self.subTest(costs=costs):
                self.assertIsNone(run_report(baseline, policy, observations, actuals={'costs_by_currency': costs})['metrics']['actual_cost'])
        zero = run_report(baseline, policy, observations, actuals={'costs_by_currency': {'AED': '0'}})
        self.assertEqual(zero['metrics']['actual_cost'], '0.00')
        self.assertIsNone(zero['metrics']['cpi'])
        self.assertIsNone(zero['metrics']['eac'])

    def test_remaining_fs_sequence_skips_frozen_holiday_and_keeps_contract_finish(self):
        baseline, policy, observations = inputs()
        baseline['relationships'] = [{'predecessor': 1, 'successor': 2, 'relationship_type': 'FS', 'lag_days': '0'}]
        baseline['accepted_inputs']['calendars'][0]['exceptions'] = [{'date': '2026-10-09', 'is_working': False}]
        baseline['accepted_inputs']['project_finish'] = '2026-10-13'
        observations[1].update(actual_start=None, physical_progress_pct='0')
        result = run_report(baseline, policy, observations)
        first, second = result['forecast']['activities']
        self.assertEqual(first['actual_start'], '2026-10-05')
        self.assertEqual(first['remaining_start'], '2026-10-08')
        self.assertEqual(first['forecast_finish'], '2026-10-12')
        self.assertEqual(second['forecast_start'], '2026-10-13')
        self.assertEqual(second['forecast_finish'], '2026-10-14')
        self.assertEqual(result['forecast']['contractual_finish'], '2026-10-13')
        self.assertEqual(result['forecast']['finish_variance_calendar_days'], 1)
        self.assertEqual(first['total_float_days'], -1)
        self.assertEqual(second['total_float_days'], -1)
        self.assertIn('forecast_contractual_finish_overrun', {row['code'] for row in result['issues']})

    def test_all_four_relationship_types_and_signed_lags_preserve_boundaries(self):
        expected = {('FS', 0): '2026-10-13', ('FS', -1): '2026-10-12',
                    ('SS', 1): '2026-10-09', ('FF', 1): '2026-10-12', ('SF', 3): '2026-10-09'}
        for (kind, lag), start in expected.items():
            with self.subTest(kind=kind, lag=lag):
                baseline, policy, observations = inputs()
                for row in observations:
                    row.update(actual_start=None, physical_progress_pct='0')
                observations[0]['remaining_duration_days'] = '3'
                observations[1]['remaining_duration_days'] = '2'
                baseline['relationships'] = [{'predecessor': 1, 'successor': 2, 'relationship_type': kind, 'lag_days': lag}]
                report = run_report(baseline, policy, observations)
                self.assertEqual(report['forecast']['activities'][1]['forecast_start'], start)

    def test_out_of_sequence_actual_is_preserved_and_remaining_work_retains_logic(self):
        baseline, policy, observations = inputs()
        baseline['relationships'] = [{'predecessor': 1, 'successor': 2, 'relationship_type': 'FS', 'lag_days': 0}]
        result = run_report(baseline, policy, observations)
        second = result['forecast']['activities'][1]
        self.assertEqual(second['actual_start'], '2026-10-05')
        self.assertEqual(second['forecast_start'], '2026-10-05')
        self.assertEqual(second['remaining_start'], '2026-10-12')
        self.assertIn('out_of_sequence_actual', {row['code'] for row in result['issues']})

    def test_missing_predecessor_remaining_propagates_unknown_no_baseline_finish_fallback(self):
        baseline, policy, observations = inputs()
        baseline['relationships'] = [{'predecessor': 1, 'successor': 2, 'relationship_type': 'FS', 'lag_days': 0}]
        observations[0]['remaining_duration_days'] = None
        observations[1].update(actual_start=None, physical_progress_pct='0')
        result = run_report(baseline, policy, observations)
        self.assertIsNone(result['forecast']['forecast_finish'])
        self.assertTrue(all(row['forecast_finish'] is None for row in result['forecast']['activities']))
        self.assertIn('predecessor_boundary_unavailable', result['forecast']['activities'][1]['unavailable_reasons'])

    def test_completed_boundary_can_release_successor_without_invented_remaining_duration(self):
        baseline, policy, observations = inputs()
        observations[0].update(actual_finish='2026-10-07', physical_progress_pct='100', remaining_duration_days=None)
        observations[1].update(actual_start=None, physical_progress_pct='0')
        baseline['relationships'] = [{'predecessor': 1, 'successor': 2, 'relationship_type': 'FS', 'lag_days': 0}]
        result = run_report(baseline, policy, observations)
        self.assertEqual(result['forecast']['activities'][0]['forecast_finish'], '2026-10-07')
        self.assertEqual(result['forecast']['activities'][1]['forecast_start'], '2026-10-08')
        self.assertIsNone(result['forecast']['activities'][0]['total_float_days'])

    def test_fractional_remaining_or_lag_mixed_calendars_and_cycles_are_not_rounded(self):
        baseline, policy, observations = inputs()
        cases = []
        fractional = deepcopy(observations)
        fractional[0]['remaining_duration_days'] = '1.5'
        cases.append((deepcopy(baseline), fractional, 'remaining_duration_not_supported'))
        lag = deepcopy(baseline)
        lag['relationships'] = [{'predecessor': 1, 'successor': 2, 'relationship_type': 'FS', 'lag_days': '.5'}]
        cases.append((lag, deepcopy(observations), 'forecast_relationship_unsupported'))
        mixed = deepcopy(baseline)
        mixed['activities'][1]['calendar'] = 8
        cases.append((mixed, deepcopy(observations), 'forecast_calendar_unsupported'))
        cycle = deepcopy(baseline)
        cycle['relationships'] = [{'predecessor': x, 'successor': y, 'relationship_type': 'FS', 'lag_days': 0} for x, y in [(1, 2), (2, 1)]]
        cases.append((cycle, deepcopy(observations), 'forecast_dependency_cycle'))
        for source, observed, code in cases:
            with self.subTest(code=code):
                result = run_report(source, policy, observed)
                self.assertIsNone(result['forecast']['forecast_finish'])
                self.assertIn(code, {row['code'] for row in result['issues']})

    def test_constraints_warn_on_impossible_bound_and_nonworking_exact_date_not_rounded(self):
        baseline, policy, observations = inputs(1)
        baseline['activities'][0].update(constraint_type='finish_no_later', constraint_date='2026-10-11')
        observations[0]['remaining_duration_days'] = '3'
        result = run_report(baseline, policy, observations)
        self.assertEqual(result['forecast']['forecast_finish'], '2026-10-12')
        self.assertIn('forecast_constraint_overrun', {row['code'] for row in result['issues']})
        baseline['activities'][0].update(constraint_type='must_finish')
        result = run_report(baseline, policy, observations)
        self.assertIsNone(result['forecast']['forecast_finish'])
        self.assertIn('exact_constraint_nonworking', {row['code'] for row in result['issues']})

    def test_exact_ids_not_titles_and_no_baseline_or_input_mutation(self):
        baseline, policy, observations = inputs()
        baseline['activities'][1]['name'] = baseline['activities'][0]['name']
        originals = deepcopy((baseline, policy, observations))
        result = run_report(baseline, policy, observations)
        self.assertEqual([row['activity_id'] for row in result['activity_comparisons']], [1, 2])
        self.assertEqual(result['activity_comparisons'][0]['finish_variance_calendar_days'], 0)
        self.assertEqual((baseline, policy, observations), originals)
        json.dumps(result, allow_nan=False)
        self.assertTrue(all(set(point) == {'date', 'planned_value'} for point in result['metrics']['planned_curve']))

    def test_future_actual_dates_cannot_earn_or_forecast_and_nonworking_actual_is_kept(self):
        baseline, policy, observations = inputs(1)
        observations[0].update(actual_finish='2026-10-08', physical_progress_pct='100')
        future = run_report(baseline, policy, observations)
        self.assertIsNone(future['metrics']['earned_value'])
        self.assertIsNone(future['forecast']['forecast_finish'])
        observations[0].update(actual_start='2026-10-04', actual_finish='2026-10-06')
        nonworking = run_report(baseline, policy, observations)
        self.assertEqual(nonworking['forecast']['activities'][0]['actual_start'], '2026-10-04')
        self.assertIsNone(nonworking['forecast']['forecast_finish'])

    def test_duplicate_and_foreign_observation_identity_cannot_silently_change_scope(self):
        baseline, policy, observations = inputs(1)
        for extra in ({**observations[0]}, {**observations[0], 'activity_id': 99}):
            with self.subTest(extra=extra['activity_id']):
                result = run_report(baseline, policy, [*observations, extra])
                self.assertIsNone(result['metrics']['earned_value'])
                self.assertIsNone(result['forecast']['forecast_finish'])

    def test_zero_day_milestone_uses_explicit_weight_and_point_dependency(self):
        baseline, policy, observations = inputs()
        baseline['activities'][0].update(activity_type='start_milestone', duration_days='0')
        policy['activities'][0].update(method='zero_hundred', weight='20')
        policy['activities'][1]['weight'] = '80'
        for row in observations:
            row.update(actual_start=None, physical_progress_pct='0')
        observations[0]['remaining_duration_days'] = '0'
        baseline['relationships'] = [{'predecessor': 1, 'successor': 2, 'relationship_type': 'FS', 'lag_days': 0}]
        report = run_report(baseline, policy, observations)
        milestone, successor = report['forecast']['activities']
        self.assertEqual(milestone['forecast_start'], milestone['forecast_finish'])
        self.assertEqual(successor['forecast_start'], milestone['forecast_finish'])
        self.assertEqual(report['metrics']['progress_pct'], '0.00')

    def test_missing_predecessor_finish_does_not_hide_a_reported_start_used_by_ss(self):
        baseline, policy, observations = inputs()
        observations[0]['remaining_duration_days'] = None
        observations[1].update(actual_start=None, physical_progress_pct='0')
        baseline['relationships'] = [{'predecessor': 1, 'successor': 2, 'relationship_type': 'SS', 'lag_days': 5}]
        report = run_report(baseline, policy, observations)
        self.assertIsNone(report['forecast']['forecast_finish'])
        self.assertEqual(report['forecast']['activities'][1]['forecast_start'], '2026-10-12')

    def test_unsupported_calendar_never_borrows_a_default_and_actual_dates_remain_available(self):
        baseline, policy, observations = inputs(1)
        baseline['accepted_inputs']['calendars'] = []
        result = run_report(baseline, policy, observations)
        self.assertIsNone(result['metrics']['planned_value'])
        self.assertIsNone(result['forecast']['forecast_finish'])
        self.assertEqual(result['activity_comparisons'][0]['actual_start'], '2026-10-05')
        self.assertIn('forecast_calendar_unsupported', {row['code'] for row in result['issues']})

    def test_partial_day_exception_and_missing_lag_require_review(self):
        baseline, policy, observations = inputs()
        baseline['accepted_inputs']['calendars'][0]['exceptions'] = [
            {'date': '2026-10-08', 'is_working': True, 'working_hours': '4'}]
        result = run_report(baseline, policy, observations)
        self.assertIsNone(result['forecast']['forecast_finish'])
        self.assertIn('frozen_calendar_unavailable', {row['code'] for row in result['issues']})
        baseline['accepted_inputs']['calendars'][0]['exceptions'] = []
        baseline['relationships'] = [{'predecessor': 1, 'successor': 2, 'relationship_type': 'FS'}]
        result = run_report(baseline, policy, observations)
        self.assertIsNone(result['forecast']['forecast_finish'])
        self.assertIn('forecast_relationship_unsupported', {row['code'] for row in result['issues']})

    def test_net_cost_credit_retained_but_nonpositive_denominator_has_no_cpi(self):
        baseline, policy, observations = inputs(1)
        result = run_report(baseline, policy, observations, actuals={'costs_by_currency': {'AED': '-5'}})
        self.assertEqual(result['metrics']['actual_cost'], '-5.00')
        self.assertEqual(result['metrics']['cost_variance'], '55.00')
        self.assertIsNone(result['metrics']['cpi'])
        self.assertIn('actual_cost_nonpositive', result['metrics']['null_reasons']['cpi'])

    def test_invalid_data_date_is_actionable_and_empty_baseline_is_not_zero(self):
        baseline, policy, observations = inputs(0)
        with self.assertRaisesRegex(ValueError, 'ISO calendar data date'):
            run_report(baseline, policy, observations, day='tomorrow')
        result = run_report(baseline, policy, observations)
        self.assertIsNone(result['metrics']['earned_value'])
        self.assertIsNone(result['metrics']['progress_pct'])
        self.assertEqual(result['forecast']['status'], 'unavailable')

    def test_empty_status_row_is_unknown_for_binary_earning_policies(self):
        baseline, policy, observations = inputs(1)
        observations[0].update(actual_start=None, physical_progress_pct=None, installed_quantity=None,
                               remaining_duration_days=None, evidence='')
        for method in ('zero_hundred', 'fifty_fifty'):
            with self.subTest(method=method):
                policy['activities'][0]['method'] = method
                result = run_report(baseline, policy, observations)
                self.assertIsNone(result['metrics']['earned_value'])
                self.assertIsNone(result['activity_comparisons'][0]['physical_progress_pct'])
                observations[0]['physical_progress_pct'] = '0'
                stated = run_report(baseline, policy, observations)
                self.assertEqual(stated['activity_comparisons'][0]['physical_progress_pct'], '0.00')
                observations[0]['physical_progress_pct'] = None

    def test_partial_baseline_dates_stay_missing_without_blocking_reported_remaining_work(self):
        baseline, policy, observations = inputs(1)
        baseline['activities'][0]['planned_finish'] = None
        result = run_report(baseline, policy, observations)
        comparison = result['activity_comparisons'][0]
        self.assertIsNone(comparison['baseline_finish'])
        self.assertIsNone(comparison['finish_variance_calendar_days'])
        self.assertIsNone(result['metrics']['planned_value'])
        self.assertEqual(comparison['forecast_finish'], '2026-10-09')
        self.assertEqual(result['forecast']['status'], 'complete')

    def test_completed_status_with_remaining_work_is_review_issue_not_earned_completion(self):
        baseline, policy, observations = inputs(1)
        observations[0].update(actual_finish='2026-10-07', physical_progress_pct='100', remaining_duration_days='2')
        report = run_report(baseline, policy, observations)
        self.assertIsNone(report['metrics']['earned_value'])
        self.assertIsNone(report['forecast']['forecast_finish'])
        self.assertEqual(report['activity_comparisons'][0]['actual_finish'], '2026-10-07')
        self.assertIn('completed_remaining_inconsistent', {item['code'] for item in report['issues']})

    def test_positive_earned_work_without_actual_start_is_unknown(self):
        baseline, policy, observations = inputs(1)
        observations[0]['actual_start'] = None
        report = run_report(baseline, policy, observations)
        self.assertIsNone(report['metrics']['earned_value'])
        self.assertIn('actual_start_not_reported', {item['code'] for item in report['issues']})

    def test_long_chain_and_parallel_roots_use_iterative_forecast_without_recursion(self):
        baseline, policy, observations = inputs(1100)
        baseline['relationships'] = [{'predecessor': key - 1, 'successor': key, 'relationship_type': 'FS', 'lag_days': 0}
                                     for key in range(5, 1101)]
        for row in observations:
            row.update(actual_start=None, physical_progress_pct='0')
        report = run_report(baseline, policy, observations)
        self.assertEqual(report['forecast']['status'], 'complete')
        self.assertEqual(report['forecast']['calculated_activity_count'], 1100)
        self.assertEqual(len(report['activity_comparisons']), 1100)
        self.assertGreater(report['forecast']['forecast_finish'], '2030-01-01')
        self.assertTrue(all(row['total_float_days'] is not None for row in report['forecast']['activities']))


if __name__ == '__main__':
    unittest.main()
