"""No database: explicit scenarios against reproducible immutable reports."""
from copy import deepcopy
import json
import unittest

from ..services.delay_analysis import DelayAnalysisError, analyze_delay_case
from ..services.operational_calculations import forecast_operational_schedule
from . import test_operational_calculations as operational


def reference(count=2, *, chain=False, milestone=False):
    baseline, policy, observations = operational.inputs(count)
    for row in observations:
        row.update(actual_start=None, physical_progress_pct='0')
    if milestone:
        baseline['activities'][-1].update(activity_type='finish_milestone', duration_days='0')
        observations[-1]['remaining_duration_days'] = '0'
    if chain:
        baseline['relationships'] = [{'predecessor': key - 1, 'successor': key, 'relationship_type': 'FS', 'lag_days': '0'}
                                     for key in range(2, count + 1)]
    forecast = forecast_operational_schedule(baseline, observations, '2026-10-07')['forecast']
    return {'baseline': baseline, 'observations': observations, 'data_date': '2026-10-07',
            'forecast': forecast, 'rule_version': 'operational-controls/1.1', 'report_id': 22}


def event(key='E1', activities=None, **values):
    return {'id': key, 'revision': 1, 'title': 'Explicit instruction', 'start_date': '2026-10-08',
            'end_date': None, 'activity_ids': activities or [1],
            'evidence': [{'reference': 'Recorded instruction, page 2', 'document_version_id': 42, 'locator': {'page': 2}}], **values}


def change(activity_id=1, value='4', *, before='2', event_id='E1', field='remaining_duration_days'):
    return {'event_id': event_id, 'activity_id': activity_id, 'field': field, 'expected_before': before,
            'value': value, 'evidence': 'Instruction page 2', 'reason': 'Explicit remaining-work assessment'}


def link(pred, succ, before, value, event_id='E1'):
    return {'event_id': event_id, 'field': 'relationship', 'predecessor_id': pred, 'successor_id': succ,
            'expected_before': before, 'value': value, 'evidence': 'Approved interface note', 'reason': 'Explicit network review'}


class DelayAnalysisTests(unittest.TestCase):
    def test_unchanged_case_reproduces_reference_without_mutating_inputs(self):
        source = reference(3, chain=True, milestone=True)
        events = [event()]
        original = deepcopy((source, events))
        result = analyze_delay_case(source, events, [], [])
        self.assertEqual(result['impact']['status'], 'complete')
        self.assertEqual(result['impact']['net_finish_shift_calendar_days'], 0)
        self.assertEqual(result['impact']['affected_activities'], [])
        self.assertEqual((source, events), original)
        self.assertEqual(result['reference']['report_id'], 22)
        json.dumps(result, allow_nan=False)

    def test_default_public_forecast_wrapper_matches_release_three_report(self):
        baseline, policy, observations = operational.inputs(3)
        result = operational.run_report(baseline, policy, observations)
        wrapped = forecast_operational_schedule(baseline, observations, '2026-10-07')
        self.assertEqual(result['forecast'], wrapped['forecast'])

    def test_parallel_event_effects_are_combined_once_not_summed(self):
        source = reference()
        events = [event(), event('E2', [2])]
        changes = [change(), change(2, event_id='E2')]
        result = analyze_delay_case(source, events, changes, [])
        self.assertEqual(result['reference']['forecast_finish'], '2026-10-09')
        self.assertEqual(result['impact']['forecast_finish'], '2026-10-13')
        self.assertEqual(result['impact']['net_finish_shift_calendar_days'], 4)
        self.assertNotIn('entitlement_days', result)

    def test_serial_changes_recalculate_calendar_network_instead_of_adding_days(self):
        result = analyze_delay_case(reference(chain=True), [event(activities=[1, 2])], [change(), change(2)], [])
        self.assertEqual(result['reference']['forecast_finish'], '2026-10-13')
        self.assertEqual(result['impact']['forecast_finish'], '2026-10-19')
        self.assertEqual(result['impact']['net_finish_shift_calendar_days'], 6)

    def test_float_absorption_is_zero_project_impact_even_when_one_activity_changes(self):
        source = reference()
        source['observations'][1]['remaining_duration_days'] = '10'
        source['forecast'] = forecast_operational_schedule(source['baseline'], source['observations'], source['data_date'])['forecast']
        result = analyze_delay_case(source, [event()], [change()], [])
        self.assertEqual(result['impact']['net_finish_shift_calendar_days'], 0)
        self.assertEqual([row['activity_id'] for row in result['impact']['affected_activities']], [1])
        self.assertEqual(result['impact']['affected_activities'][0]['finish_shift_calendar_days'], 4)

    def test_hold_started_work_preserves_actual_start_and_moves_remaining_work_only(self):
        source = reference(1)
        source['observations'][0].update(actual_start='2026-10-05', physical_progress_pct='50')
        source['forecast'] = forecast_operational_schedule(source['baseline'], source['observations'], source['data_date'])['forecast']
        proposed = change(value='2026-10-12', before=None, field='remaining_not_before')
        result = analyze_delay_case(source, [event()], [proposed], [])
        row = result['impact']['affected_activities'][0]
        self.assertEqual(row['reference_start'], '2026-10-05')
        self.assertEqual(row['scenario_start'], '2026-10-05')
        self.assertEqual(row['scenario_remaining_start'], '2026-10-12')
        self.assertEqual(row['scenario_finish'], '2026-10-13')
        self.assertTrue(row['timing_changed'])

    def test_explicit_holiday_and_weekend_hold_use_frozen_calendar(self):
        source = reference(1)
        source['baseline']['accepted_inputs']['calendars'][0]['exceptions'] = [{'date': '2026-10-12', 'is_working': False}]
        source['forecast'] = forecast_operational_schedule(source['baseline'], source['observations'], source['data_date'])['forecast']
        result = analyze_delay_case(source, [event()], [change(value='2026-10-11', before=None, field='remaining_not_before')], [])
        row = result['impact']['affected_activities'][0]
        self.assertEqual(row['scenario_remaining_start'], '2026-10-13')
        self.assertEqual(row['scenario_finish'], '2026-10-14')

    def test_recovery_options_are_independent_and_use_impacted_expected_values(self):
        source = reference(1)
        options = [{'id': 'R1', 'name': 'Explicit reduction', 'changes': [change(value='3', before='5')]},
                   {'id': 'R2', 'name': 'Second independent reduction', 'changes': [change(value='2', before='5')]}]
        original = deepcopy((source, options))
        result = analyze_delay_case(source, [event()], [change(value='5')], options)
        self.assertEqual(result['impact']['forecast_finish'], '2026-10-14')
        self.assertEqual([row['forecast_finish'] for row in result['scenarios']], ['2026-10-12', '2026-10-09'])
        self.assertEqual([row['recovered_calendar_days'] for row in result['scenarios']], [2, 5])
        self.assertEqual((source, options), original)
        options[0]['changes'][0]['expected_before'] = '2'
        with self.assertRaises(DelayAnalysisError) as error:
            analyze_delay_case(source, [event()], [change(value='5')], options)
        self.assertEqual(error.exception.code, 'expected_value_mismatch')

    def test_recovery_can_explicitly_remove_proposed_hold_without_removing_baseline_constraint(self):
        source = reference(1)
        source['baseline']['activities'][0].update(constraint_type='start_no_earlier', constraint_date='2026-10-09')
        source['forecast'] = forecast_operational_schedule(source['baseline'], source['observations'], source['data_date'])['forecast']
        hold = change(value='2026-10-14', before=None, field='remaining_not_before')
        option = {'id': 'R', 'name': 'Remove hold', 'changes': [change(value=None, before='2026-10-14', field='remaining_not_before')]}
        result = analyze_delay_case(source, [event()], [hold], [option])
        self.assertEqual(result['scenarios'][0]['forecast']['activities'][0]['forecast_start'], '2026-10-09')
        self.assertEqual(result['scenarios'][0]['net_finish_shift_calendar_days'], 0)

    def test_affected_milestone_and_driving_witness_use_exact_typed_network(self):
        source = reference(3, chain=True, milestone=True)
        result = analyze_delay_case(source, [event()], [change()], [])
        self.assertEqual([row['activity_id'] for row in result['impact']['affected_milestones']], [3])
        paths = result['impact']['paths']
        self.assertEqual(paths['potentially_affected_activity_ids'], [1, 2, 3])
        self.assertEqual(paths['witnesses'][0]['activity_ids'], [1, 2, 3])
        self.assertEqual(paths['witnesses'][0]['basis'], 'scenario_driving_edges')
        self.assertFalse(paths['witnesses'][0]['causal_entitlement'])
        self.assertTrue(all(edge['driving_in_scenario'] for edge in paths['edges']))

    def test_reachable_descendant_is_not_claimed_timing_changed_when_other_path_drives(self):
        source = reference(3)
        source['observations'][1]['remaining_duration_days'] = '10'
        source['baseline']['relationships'] = [{'predecessor': key, 'successor': 3, 'relationship_type': 'FS', 'lag_days': 0} for key in (1, 2)]
        source['forecast'] = forecast_operational_schedule(source['baseline'], source['observations'], source['data_date'])['forecast']
        result = analyze_delay_case(source, [event()], [change()], [])
        self.assertEqual(result['impact']['paths']['potentially_affected_activity_ids'], [1, 3])
        node = next(row for row in result['impact']['paths']['nodes'] if row['activity_id'] == 3)
        self.assertFalse(node['timing_changed'])
        self.assertEqual(result['impact']['net_finish_shift_calendar_days'], 0)

    def test_explicit_relationship_replacement_uses_type_and_negative_lag(self):
        source = reference(chain=True)
        result = analyze_delay_case(source, [event(activities=[2])], [link(1, 2,
            {'type': 'FS', 'lag_days': 0}, {'type': 'FS', 'lag_days': -1})], [])
        self.assertEqual(result['impact']['forecast_finish'], '2026-10-12')
        edges = result['impact']['paths']['edges']
        self.assertEqual(len(edges), 2)
        self.assertTrue(any(row['in_scenario'] and not row['in_reference'] and row['lag_days'] == '-1' for row in edges))
        self.assertTrue(any(row['in_reference'] and not row['in_scenario'] for row in edges))

    def test_all_relationship_types_are_passed_to_forecast_without_type_inference(self):
        expected = {'FS': '2026-10-14', 'SS': '2026-10-12', 'FF': '2026-10-12', 'SF': '2026-10-09'}
        for kind, finish in expected.items():
            with self.subTest(kind=kind):
                source = reference()
                result = analyze_delay_case(source, [event(activities=[2])], [link(1, 2, None, {'type': kind, 'lag_days': 1})], [])
                self.assertEqual(result['impact']['forecast_finish'], finish)

    def test_relationship_removal_is_explicit_and_graph_discloses_removed_edge(self):
        source = reference(chain=True)
        result = analyze_delay_case(source, [event(activities=[2])], [link(1, 2, {'type': 'FS', 'lag_days': 0}, None)], [])
        self.assertEqual(result['impact']['forecast_finish'], '2026-10-09')
        edge = result['impact']['paths']['edges'][0]
        self.assertTrue(edge['in_reference'])
        self.assertFalse(edge['in_scenario'])

    def test_duplicate_field_or_link_writes_are_rejected_not_last_value_wins(self):
        for changes in ([change(), change(value='6')], [link(1, 2, None, {'type': 'FS', 'lag_days': 0}),
                         link(1, 2, {'type': 'FS', 'lag_days': 0}, {'type': 'SS', 'lag_days': 0})]):
            with self.subTest(changes=changes):
                with self.assertRaises(DelayAnalysisError) as error:
                    analyze_delay_case(reference(), [event(activities=[1, 2])], changes, [])
                self.assertEqual(error.exception.code, 'duplicate_change')

    def test_scope_evidence_actual_edits_and_completed_changes_are_rejected(self):
        source = reference()
        cases = [(change(activity_id=99), 'change_scope_invalid'),
                 (change(activity_id=2), 'change_event_scope_mismatch'),
                 (change(field='actual_finish', value='2026-10-09', before=None), 'change_field_forbidden'),
                 ({**change(), 'evidence': ''}, 'change_evidence_required'),
                 ({**change(), 'reason': ''}, 'change_evidence_required'),
                 ({**change(), 'event_id': 'foreign'}, 'change_event_not_selected'),
                 (change(value='1.5'), 'unsupported_day_value')]
        for proposed, code in cases:
            with self.subTest(code=code), self.assertRaises(DelayAnalysisError) as error:
                analyze_delay_case(source, [event()], [proposed], [])
            self.assertEqual(error.exception.code, code)
        source['observations'][0].update(actual_start='2026-10-05', actual_finish='2026-10-07',
                                         remaining_duration_days='0', physical_progress_pct='100')
        source['forecast'] = forecast_operational_schedule(source['baseline'], source['observations'], source['data_date'])['forecast']
        with self.assertRaises(DelayAnalysisError) as error:
            analyze_delay_case(source, [event()], [change(before='0')], [])
        self.assertEqual(error.exception.code, 'completed_activity_immutable')

    def test_cycles_cross_baseline_links_and_ambiguous_expected_links_are_rejected(self):
        source = reference(chain=True)
        cases = [(link(2, 1, None, {'type': 'FS', 'lag_days': 0}), 'scenario_dependency_cycle'),
                 (link(99, 1, None, {'type': 'FS', 'lag_days': 0}), 'relationship_scope_invalid'),
                 (link(1, 2, {'type': 'FS', 'lag_days': 1}, None), 'expected_value_mismatch')]
        for proposed, code in cases:
            with self.subTest(code=code), self.assertRaises(DelayAnalysisError) as error:
                analyze_delay_case(source, [event(activities=[1, 2])], [proposed], [])
            self.assertEqual(error.exception.code, code)

    def test_reference_rule_and_published_forecast_mismatch_stop_comparison(self):
        for version in ('operational-controls/1.0', 'operational-controls/future', 'old-engine'):
            with self.subTest(version=version):
                source = reference()
                source['rule_version'] = version
                result = analyze_delay_case(source, [event()], [change()], [])
                self.assertEqual(result['impact']['status'], 'unavailable')
                self.assertEqual(result['issues'][0]['code'], 'reference_rule_version_unsupported')
        source = reference()
        source['forecast']['activities'][0]['total_float_days'] = 999
        result = analyze_delay_case(source, [event()], [change()], [])
        self.assertEqual(result['issues'][0]['code'], 'published_forecast_not_reproduced')
        self.assertIsNone(result['impact']['forecast'])

    def test_stored_project_finish_dates_remaining_and_status_all_participate_in_anchor(self):
        for path in ('forecast_finish', 'remaining_start', 'remaining_duration_days', 'status'):
            with self.subTest(path=path):
                source = reference()
                if path == 'forecast_finish':
                    source['forecast'][path] = '2026-11-01'
                else:
                    source['forecast']['activities'][0][path] = {'remaining_start': '2026-10-09', 'remaining_duration_days': '8', 'status': 'actual'}[path]
                result = analyze_delay_case(source, [event()], [change()], [])
                self.assertEqual(result['issues'][0]['code'], 'published_forecast_not_reproduced')

    def test_missing_reference_inputs_do_not_borrow_baseline_finish_or_prove_net_impact(self):
        source = reference(1)
        source['observations'][0]['remaining_duration_days'] = None
        source['forecast'] = forecast_operational_schedule(source['baseline'], source['observations'], source['data_date'])['forecast']
        result = analyze_delay_case(source, [event()], [change(before=None)], [])
        self.assertEqual(result['impact']['status'], 'partial')
        self.assertIsNone(result['impact']['net_finish_shift_calendar_days'])
        self.assertIn('reference_forecast_incomplete', {row['code'] for row in result['issues']})

    def test_event_elapsed_days_are_not_added_and_past_events_require_evidence_review(self):
        source = reference(1)
        past = event(start_date='2026-09-01', end_date='2026-10-06')
        result = analyze_delay_case(source, [past], [change(value='2026-10-06', before=None, field='remaining_not_before')], [])
        self.assertEqual(result['impact']['net_finish_shift_calendar_days'], 0)
        self.assertIn('event_may_already_be_observed', {row['code'] for row in result['issues']})
        self.assertIn('release_not_after_data_date', {row['code'] for row in result['issues']})

    def test_contract_finish_is_not_extended_and_overrun_is_not_an_entitlement(self):
        source = reference(1)
        source['baseline']['accepted_inputs']['project_finish'] = '2026-10-09'
        source['forecast'] = forecast_operational_schedule(source['baseline'], source['observations'], source['data_date'])['forecast']
        result = analyze_delay_case(source, [event()], [change()], [])
        self.assertEqual(result['reference']['contractual_finish'], '2026-10-09')
        self.assertEqual(result['impact']['forecast']['contractual_finish'], '2026-10-09')
        self.assertEqual(result['impact']['contract_overrun_calendar_days'], 4)
        self.assertFalse(any('entitlement' in key for key in result['impact']))

    def test_witnesses_are_bounded_for_many_parallel_impacted_milestones(self):
        source = reference(45)
        for row in source['baseline']['activities'][1:]:
            row.update(activity_type='finish_milestone', duration_days='0')
        for row in source['observations'][1:]:
            row['remaining_duration_days'] = '0'
        source['baseline']['relationships'] = [{'predecessor': 1, 'successor': key, 'relationship_type': 'FS', 'lag_days': 0} for key in range(2, 46)]
        source['forecast'] = forecast_operational_schedule(source['baseline'], source['observations'], source['data_date'])['forecast']
        result = analyze_delay_case(source, [event()], [change()], [])
        paths = result['impact']['paths']
        self.assertEqual(len(paths['witnesses']), 30)
        self.assertEqual(paths['witness_terminal_count'], 44)
        self.assertEqual(paths['truncated']['witnesses'], 14)

    def test_deep_1100_activity_network_has_bounded_iterative_path_witness(self):
        source = reference(1100, chain=True, milestone=True)
        result = analyze_delay_case(source, [event()], [change()], [])
        self.assertEqual(result['impact']['status'], 'complete')
        self.assertEqual(result['impact']['paths']['reachable_activity_count'], 1100)
        self.assertEqual(len(result['impact']['paths']['edges']), 1099)
        self.assertEqual(result['impact']['paths']['witnesses'][0]['activity_ids'], list(range(1, 1101)))


if __name__ == '__main__':
    unittest.main()
