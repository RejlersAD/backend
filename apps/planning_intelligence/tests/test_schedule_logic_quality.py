"""Schedule review diagnostics operate without ORM access or input mutation."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from unittest import TestCase

from ..services.schedule_logic_quality import analyze_schedule_logic


STAGES = [('IFR', 10, '2026-04-15', '2026-04-28'),
          ('COMPANY_REVIEW', 10, '2026-04-29', '2026-05-12'),
          ('IFA', 5, '2026-05-13', '2026-05-19'),
          ('COMPANY_APPROVAL', 5, '2026-05-20', '2026-05-26'),
          ('FINAL_ISSUE', 1, '2026-05-27', '2026-05-27')]


def chain(parent, discipline='hse', gates=('gate',), **changes):
    rows = []
    for sequence, (code, duration, start, finish) in enumerate(STAGES, 1):
        predecessors = list(gates) if sequence == 1 else [rows[-1]['id']]
        rows.append({'id': f'{parent}:{sequence}', 'title': f'Deliverable {parent} - {code}',
                     'parent_deliverable_id': parent, 'deliverable': f'Deliverable {parent}',
                     'discipline': discipline, 'workflow_stage_code': code, 'workflow_stage_sequence': sequence,
                     'duration_days': duration, 'duration_source': 'proposed',
                     'planned_start_date': start, 'planned_finish_date': finish,
                     'depends_on': predecessors,
                     'dependency_details': [{'task_id': key, 'type': 'FS', 'lag_days': 0} for key in predecessors],
                     **changes})
    return rows


def parallel(count=5, **changes):
    return [{'id': 'gate', 'title': 'Philosophy final issue', 'duration_days': 1,
             'planned_start_date': '2026-04-14', 'planned_finish_date': '2026-04-14'}] + [
        task for index in range(count) for task in chain(str(index), **changes)]


def review_groups(result):
    return [group for group in result['groups'] if group['requires_review']]


class ScheduleLogicQualityTests(TestCase):
    def test_pdf_like_173_deliverables_form_four_discipline_clusters(self):
        tasks = [{'id': 'gate', 'title': 'Basis final'}, {'id': 'philosophy', 'title': 'Philosophy final'}]
        for discipline, count in [('hse', 40), ('process', 50), ('piping', 50), ('electrical', 33)]:
            for index in range(count):
                tasks.extend(chain(f'{discipline}:{index}', discipline, gates=('gate', 'philosophy')))
        result = analyze_schedule_logic(tasks)
        self.assertEqual(result['summary']['parallel_group_count'], 4)
        self.assertEqual(result['summary']['parallel_deliverable_count'], 173)
        self.assertEqual(result['summary']['relationship_count'], 173 * 6)
        self.assertEqual(result['summary']['cross_deliverable_link_count'], 173 * 2)
        self.assertEqual(result['summary']['unconnected_workflow_start_count'], 0)
        for group in review_groups(result):
            self.assertEqual(group['start_date'], '2026-04-15')
            self.assertEqual(group['finish_date'], '2026-05-27')
            self.assertEqual(group['date_basis'], 'planned')
            self.assertEqual(sum(row['duration_days'] for row in group['stage_durations']), 31)
            self.assertEqual(len(group['task_ids']), group['deliverable_count'] * 5)
            self.assertEqual(len(group['first_task_ids']), group['deliverable_count'])
            self.assertEqual(group['terminal_task_count'], group['deliverable_count'])
            self.assertEqual({row['task_id'] for row in group['common_predecessors']}, {'gate', 'philosophy'})

    def test_intentional_parallelism_is_review_not_a_claim_of_invalid_logic(self):
        result = analyze_schedule_logic(parallel())
        group = review_groups(result)[0]
        self.assertEqual(group['status'], 'requires_review')
        self.assertIn('Confirm that parallel execution is intended', group['message'])
        self.assertNotIn('invalid', group['message'])
        self.assertNotIn('blockers', result)
        self.assertEqual(result['summary']['terminal_branch_count'], 5)
        self.assertFalse(review_groups(analyze_schedule_logic(parallel(4))))

    def test_different_dates_durations_discipline_or_internal_logic_do_not_match(self):
        for change in ({'planned_start_date': '2026-04-16'}, {'duration_days': 11}, {'discipline': 'process'}):
            with self.subTest(change=change):
                tasks = parallel()
                tasks[1].update(change)
                self.assertFalse(review_groups(analyze_schedule_logic(tasks)))
        tasks = parallel()
        tasks[2]['dependency_details'][0]['type'] = 'SS'
        self.assertFalse(review_groups(analyze_schedule_logic(tasks)))

    def test_actual_typed_links_lags_and_stage_targets_define_common_gates(self):
        for field, value in [('type', 'SS'), ('lag_days', 2), ('lag_unit', 'calendar_days')]:
            with self.subTest(field=field):
                tasks = parallel()
                tasks[1]['dependency_details'][0][field] = value
                self.assertFalse(review_groups(analyze_schedule_logic(tasks)))
        tasks = parallel()
        tasks[1]['depends_on'], tasks[1]['dependency_details'] = [], []
        tasks[2]['depends_on'].append('gate')
        tasks[2]['dependency_details'].append({'task_id': 'gate', 'type': 'FS', 'lag_days': 0})
        self.assertFalse(review_groups(analyze_schedule_logic(tasks)))

    def test_missing_dates_are_unknown_and_never_fabricated(self):
        tasks = parallel(planned_start_date=None, planned_finish_date=None)
        group = review_groups(analyze_schedule_logic(tasks))[0]
        self.assertIsNone(group['start_date'])
        self.assertIsNone(group['finish_date'])
        self.assertEqual(group['date_basis'], 'unknown')
        for task in tasks[1:]:
            task['duration_days'] = None
        self.assertFalse(review_groups(analyze_schedule_logic(tasks)))

    def test_stale_or_uncalculated_dates_are_not_reported_as_calculated(self):
        for flags in ({'calculated': False}, {'calculated': True, 'calculation_stale': True},
                      {'calculated': True, 'calculation_status': 'stale'},
                      {'source_evidence_review': {'status': 'requires_review'}}):
            with self.subTest(flags=flags):
                group = review_groups(analyze_schedule_logic(parallel(**flags)))[0]
                self.assertIsNone(group['start_date'])
                self.assertIsNone(group['finish_date'])
                self.assertEqual(group['date_basis'], 'unknown')

    def test_source_dates_stay_source_evidence_with_no_calculated_substitution(self):
        tasks = parallel(calculated=False)
        for task in tasks[1:]:
            task['source_evidence'] = {'values': {'planned_start_date': task['planned_start_date'],
                                                  'planned_finish_date': task['planned_finish_date']}}
            task['planned_start_date'] = '2028-01-01'
        group = review_groups(analyze_schedule_logic(tasks))[0]
        self.assertEqual(group['date_basis'], 'source')
        self.assertEqual(group['start_date'], '2026-04-15')
        self.assertEqual(group['finish_date'], '2026-05-27')

    def test_missing_one_stage_date_does_not_claim_complete_window(self):
        tasks = parallel()
        for task in tasks[1:]:
            if task['workflow_stage_code'] == 'IFA':
                task['planned_finish_date'] = None
        group = review_groups(analyze_schedule_logic(tasks))[0]
        self.assertEqual(group['start_date'], '2026-04-15')
        self.assertIsNone(group['finish_date'])

    def test_mixed_id_types_metadata_and_parent_title_fallback(self):
        tasks = parallel(gates=(42,))
        tasks[0]['id'] = '42'
        for task in tasks[1:]:
            task['parent_deliverable_id'] = int(task['parent_deliverable_id'])
            task['metadata'] = {key: task.pop(key) for key in
                                ('parent_deliverable_id', 'workflow_stage_code', 'workflow_stage_sequence')}
        parents = [{'id': index, 'title': f'Confirmed title {index}'} for index in range(5)]
        group = review_groups(analyze_schedule_logic(tasks, parents))[0]
        self.assertEqual(group['deliverable_ids'], ['0', '1', '2', '3', '4'])
        self.assertEqual(group['deliverable_titles'][0], 'Confirmed title 0')
        self.assertEqual(group['common_predecessors'][0]['task_id'], '42')

    def test_explicit_deliverable_membership_fallback_excludes_ambiguous_mapping(self):
        tasks = parallel()
        parents = [{'id': index, 'title': f'Drawing {index}',
                    'workflow_task_ids': [f'{index}:{stage}' for stage in range(1, 6)]} for index in range(5)]
        for task in tasks[1:]:
            task.pop('parent_deliverable_id')
        group = review_groups(analyze_schedule_logic(tasks, parents))[0]
        self.assertEqual(group['deliverable_ids'], ['0', '1', '2', '3', '4'])
        self.assertEqual(group['deliverable_titles'][0], 'Drawing 0')
        parents.append({'id': 'ambiguous', 'workflow_task_ids': ['0:1']})
        self.assertFalse(review_groups(analyze_schedule_logic(tasks, parents)))

    def test_unknown_source_units_and_bare_source_references_are_not_invented(self):
        tasks = parallel(duration_source='source_document', duration_unit=None)
        self.assertFalse(review_groups(analyze_schedule_logic(tasks)))
        tasks = parallel(evidence_policy='document_driven', duration_unit='working_days', dependency_status='not_specified')
        for task in tasks[1:]:
            if task['workflow_stage_code'] == 'IFR':
                task['dependency_details'] = []
        self.assertFalse(review_groups(analyze_schedule_logic(tasks)))
        tasks = parallel(date_authority='source_document')
        group = review_groups(analyze_schedule_logic(tasks))[0]
        self.assertEqual(group['date_basis'], 'unknown')
        self.assertIsNone(group['start_date'])

    def test_dangling_unknown_or_cyclic_links_do_not_become_common_real_gates(self):
        tasks = parallel()
        self.assertFalse(review_groups(analyze_schedule_logic(tasks[1:])))
        for detail in ({'task_id': 'gate', 'type': None, 'lag_days': 0},
                       {'task_id': 'gate', 'type': 'FS', 'lag_days': None},
                       {'task_id': 'gate', 'source': 'source_document'}):
            unknown = deepcopy(tasks)
            for task in unknown[1:]:
                if task['workflow_stage_code'] == 'IFR':
                    task['dependency_details'] = [detail]
            self.assertFalse(review_groups(analyze_schedule_logic(unknown)))
        tasks[0]['depends_on'] = ['0:5']
        result = analyze_schedule_logic(tasks)
        self.assertFalse(review_groups(result))
        self.assertEqual(result['summary']['cycle_or_blocked_task_count'], 26)

    def test_unconnected_and_terminal_workflows_are_warnings_and_connected_finish_is_not_terminal(self):
        tasks = chain('a', gates=()) + chain('b', gates=())
        result = analyze_schedule_logic(tasks)
        self.assertFalse(review_groups(result))
        self.assertEqual(result['summary']['unconnected_workflow_start_count'], 2)
        self.assertEqual(result['summary']['terminal_branch_count'], 2)
        self.assertTrue(all(row['status'] == 'warning' for row in result['groups']))
        tasks.append({'id': 'handover', 'depends_on': ['a:5', 'b:5']})
        self.assertEqual(analyze_schedule_logic(tasks)['summary']['terminal_branch_count'], 0)

    def test_nonworkflow_tasks_are_not_inferred_from_titles_or_wbs(self):
        tasks = [{'id': index, 'title': 'Drawing - IFR', 'wbs_node_id': 1, 'duration_days': 10,
                  'depends_on': ['gate']} for index in range(20)] + [{'id': 'gate'}]
        result = analyze_schedule_logic(tasks)
        self.assertFalse(result['groups'])
        self.assertEqual(result['summary']['workflow_deliverable_count'], 0)
        self.assertFalse(analyze_schedule_logic([])['groups'])

    def test_fingerprint_is_order_stable_ignores_float_and_does_not_mutate_inputs(self):
        tasks = parallel()
        original = deepcopy(tasks)
        first = analyze_schedule_logic(tasks)
        self.assertEqual(tasks, original)
        shuffled = list(reversed(deepcopy(tasks)))
        for task in shuffled:
            task.update(total_float_days=900, free_float_days=90, is_critical=True)
            task['depends_on'] = list(reversed(task.get('depends_on', [])))
            task['dependency_details'] = list(reversed(task.get('dependency_details', [])))
            if task['dependency_details']:
                task['dependency_details'].append(deepcopy(task['dependency_details'][0]))
        self.assertEqual(first, analyze_schedule_logic(shuffled))
        self.assertRegex(first['fingerprint'], r'^[0-9a-f]{64}$')

    def test_fingerprint_changes_for_review_relevant_inputs_even_if_group_identity_stays(self):
        tasks = parallel()
        before = analyze_schedule_logic(tasks)
        for field, value in [('duration_days', 12), ('planned_start_date', '2026-04-16'),
                             ('constraint_date', '2026-04-16'), ('calendar_id', 2),
                             ('source_start_date', '2025-01-01'), ('title', 'Renamed'),
                             ('parent_deliverable_id', 'other')]:
            with self.subTest(field=field):
                updated = deepcopy(tasks)
                updated[1][field] = value
                self.assertNotEqual(before['fingerprint'], analyze_schedule_logic(updated)['fingerprint'])
        updated = deepcopy(tasks)
        updated[1]['dependency_details'][0]['lag_days'] = 1
        self.assertNotEqual(before['fingerprint'], analyze_schedule_logic(updated)['fingerprint'])
        for task in tasks[1:]:
            task['duration_days'] *= 2
        after = analyze_schedule_logic(tasks)
        self.assertEqual(review_groups(before)[0]['id'], review_groups(after)[0]['id'])
        self.assertNotEqual(before['fingerprint'], after['fingerprint'])

    def test_numeric_and_date_objects_have_json_safe_stable_values(self):
        tasks = parallel()
        before = analyze_schedule_logic(tasks)
        for task in tasks[1:]:
            task['duration_days'] = Decimal(str(task['duration_days']))
            task['planned_start_date'] = date.fromisoformat(task['planned_start_date'])
        self.assertEqual(before, analyze_schedule_logic(tasks))

    def test_duplicate_activity_ids_cannot_establish_parallel_group(self):
        tasks = parallel()
        tasks.append(deepcopy(tasks[1]))
        result = analyze_schedule_logic(tasks)
        self.assertFalse(review_groups(result))
        self.assertEqual(result['summary']['duplicate_task_id_count'], 1)
