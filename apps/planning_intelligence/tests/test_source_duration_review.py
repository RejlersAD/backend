"""Source reconciliation never replaces a missing source fact with a guess."""
from copy import deepcopy

from django.test import SimpleTestCase

from ..services.source_duration_review import reconcile_source_durations
from .test_source_timing_constraints import printed_text, source, scoped_task


def activity(identifier='a', title='MASTER DELIVERABLE REGISTER - IFR', source_upload=None, **overrides):
    # Explicit fixture pointers replace the removed production title lookup.
    ids = {'MASTER DELIVERABLE REGISTER - IFR': 'GEN_1850',
           'MASTER DELIVERABLE REGISTER - COMPANY REVIEW': 'GEN_1860',
           'MASTER DELIVERABLE REGISTER - IFA': 'GEN_1870',
           'MASTER DELIVERABLE REGISTER - COMPANY APPROVAL': 'GEN_1880',
           'MASTER DELIVERABLE REGISTER - IFT/IFM': 'GEN_1890', 'PROJECT COMPLETE': 'A1130'}
    return {**scoped_task(identifier, title, ids.get(title), source_upload=source_upload),
            'duration_days': 10, 'duration_source': 'proposed', 'depends_on': [], **overrides}


class SourceDurationReviewTests(SimpleTestCase):
    def test_copies_exact_printed_duration_without_importing_dates_float_or_links(self):
        task = activity(planned_start_date='2026-01-06', planned_finish_date='2026-01-19',
                        total_float_days=143, schedule_generated_fields=['duration_days', 'planned_start_date'])
        tasks, audit, summary, warnings = reconcile_source_durations([task], {'files': [source()]})
        actual = tasks[0]
        self.assertEqual(actual['duration_days'], 5)
        self.assertEqual(actual['duration_source'], 'source_document')
        self.assertIsNone(actual['planned_start_date'])
        self.assertEqual(actual['planned_finish_date'], '2026-01-19')
        self.assertEqual(actual['total_float_days'], 143)
        self.assertEqual(actual['depends_on'], [])
        self.assertEqual(actual['duration_evidence']['values']['planned_start_date'], '2026-02-23')
        self.assertFalse(actual['duration_calendar_verified'])
        self.assertFalse(actual['duration_evidence']['relationships_verified'])
        self.assertFalse(actual['duration_evidence']['can_apply_to_cpm'])
        self.assertEqual(audit[0]['source_references'][0]['locator']['row'], 3)
        self.assertEqual(summary['source_document_count'], 1)
        self.assertTrue(any('not an exact schedule import' in warning for warning in warnings))

    def test_missing_fact_clears_template_and_generated_dates_even_when_effort_exists(self):
        task = activity(title='Design unknown', effort_hours=80, planned_start_date='2026-01-06',
                        planned_finish_date='2026-01-19', schedule_generated_fields=[
                            'duration_days', 'planned_start_date', 'planned_finish_date', 'depends_on'])
        tasks, _, summary, _ = reconcile_source_durations([task], {'files': [source()]})
        self.assertIsNone(tasks[0]['duration_days'])
        self.assertEqual(tasks[0]['duration_source'], 'missing_source')
        self.assertIsNone(tasks[0]['planned_start_date'])
        self.assertIsNone(tasks[0]['planned_finish_date'])
        self.assertEqual(tasks[0]['effort_hours'], 80)
        self.assertEqual(tasks[0]['schedule_generated_fields'], ['depends_on'])
        self.assertEqual(summary['cleared_generated_date_count'], 2)

    def test_general_review_requirement_is_explicitly_distinct_from_real_planned_duration(self):
        sow = source(category='sow', filename='SOW.pdf', text='Company review requires 10 working days.')
        task = activity(workflow_stage_code='COMPANY_REVIEW', title='Unknown - COMPANY REVIEW')
        tasks, _, summary, _ = reconcile_source_durations([task], {'files': [sow]})
        self.assertIsNone(tasks[0]['duration_days'])
        self.assertEqual(tasks[0]['duration_source'], 'source_requirement')
        self.assertEqual(tasks[0]['duration_review_status'], 'requirement_needs_review')
        self.assertFalse(tasks[0]['duration_evidence']['activity_specific'])
        self.assertNotIn('original_duration_days', tasks[0]['duration_evidence']['values'])
        self.assertEqual(summary['source_document_count'], 0)
        self.assertEqual(summary['source_requirement_count'], 1)

    def test_review_allowance_does_not_spread_to_other_stages_or_conflicting_sources(self):
        sow = source(category='sow', text='Company review requires 10 working days.')
        competing = source(2, category='sow', text='Company review requires 15 working days.')
        task = activity(workflow_stage_code='IFR')
        result, *_ = reconcile_source_durations([task], {'files': [sow]})
        self.assertIsNone(result[0]['duration_days'])
        result, *_ = reconcile_source_durations([{**task, 'workflow_stage_code': 'COMPANY_REVIEW'}],
                                               {'files': [sow, competing]})
        self.assertIsNone(result[0]['duration_days'])

    def test_unique_activity_fact_wins_over_general_review_requirement(self):
        schedule = source(text=printed_text().replace(
            'COMPANY REVIEW 10 02-Mar-26', 'COMPANY REVIEW 12 02-Mar-26'))
        sow = source(2, category='sow', text='Company review requires 10 working days.')
        task = activity(title='MASTER DELIVERABLE REGISTER - COMPANY REVIEW', workflow_stage_code='COMPANY_REVIEW', source_upload=schedule)
        tasks, *_ = reconcile_source_durations([task], {'files': [schedule, sow]})
        self.assertEqual(tasks[0]['duration_days'], 12)
        self.assertEqual(tasks[0]['duration_source'], 'source_document')

    def test_final_issue_is_not_aliased_to_ift_ifm_and_package_remains_unknown(self):
        names = ['IFR', 'COMPANY REVIEW', 'IFA', 'COMPANY APPROVAL', 'FINAL ISSUE']
        tasks = [activity(str(index), f'MASTER DELIVERABLE REGISTER - {name}',
                          parent_deliverable_id='0', deliverable='MASTER DELIVERABLE REGISTER')
                 for index, name in enumerate(names)]
        tasks, _, summary, _ = reconcile_source_durations(tasks, {'files': [source()]})
        self.assertEqual([task['duration_days'] for task in tasks], [5, 10, 10, 5, None])
        package = summary['package_reviews'][0]
        self.assertIsNone(package['source_original_duration_days'])  # A parent title is not an approved source link.
        self.assertIsNone(package['duration_days'])
        self.assertFalse(package['duration_complete'])
        self.assertEqual(package['missing_duration_count'], 1)

    def test_package_activity_sum_is_not_claimed_to_be_elapsed_source_duration(self):
        names = ['IFR', 'COMPANY REVIEW', 'IFA', 'COMPANY APPROVAL', 'IFT/IFM']
        tasks = [activity(str(index), f'MASTER DELIVERABLE REGISTER - {name}',
                          parent_deliverable_id='0', deliverable='MASTER DELIVERABLE REGISTER')
                 for index, name in enumerate(names)]
        _, _, summary, _ = reconcile_source_durations(tasks, {'files': [source()]})
        package = summary['package_reviews'][0]
        self.assertEqual(package['duration_days'], 40)
        self.assertEqual(package['duration_basis'], 'sum_of_activity_durations_not_elapsed_package_duration')
        self.assertFalse(package['relationships_verified'])

    def test_duplicate_uploaded_revisions_and_duplicate_task_titles_remain_unknown(self):
        task = activity()
        for tasks, files in [([{**task, 'source_references': []}], [source(), source(2)]),
                             ([task, {**task, 'id': 'b'}], [source()])]:
            with self.subTest(tasks=len(tasks), files=len(files)):
                output, *_ = reconcile_source_durations(tasks, {'files': files})
                self.assertTrue(all(row['duration_days'] is None for row in output))

    def test_manual_duration_is_preserved_but_source_mismatch_is_visible(self):
        task = activity(duration_source='planner', planned_start_date='2026-01-06',
                        schedule_generated_fields=['duration_days'])
        tasks, audit, summary, _ = reconcile_source_durations([task], {'files': [source()]})
        self.assertEqual(tasks[0]['duration_days'], 10)
        self.assertEqual(tasks[0]['planned_start_date'], '2026-01-06')
        self.assertEqual(tasks[0]['duration_review_status'], 'manual_unverified')
        self.assertEqual(audit[0]['source_original_duration_days'], 5)
        self.assertIn('states 5 days', audit[0]['reason'])
        self.assertEqual(summary['manual_unverified_count'], 1)

    def test_started_work_and_upstream_chain_are_preserved(self):
        upstream = activity()
        started = activity('b', 'MASTER DELIVERABLE REGISTER - IFA', status='in_progress', depends_on=['a'])
        tasks, _, summary, _ = reconcile_source_durations([upstream, started], {'files': [source()]})
        self.assertEqual([row['duration_days'] for row in tasks], [10, 10])
        self.assertEqual(summary['started_unverified_count'], 2)
        self.assertEqual(tasks[1]['depends_on'], ['a'])

    def test_protected_verified_source_calendar_and_evidence_are_not_downgraded(self):
        original_evidence = {'basis': 'native_import', 'calendar_verified': True,
                             'source_references': [{'file_id': 99, 'locator': {'activity': 'A1'}}]}
        for changes in ({'status': 'in_progress'}, {'duration_confirmed': True}):
            task = activity(duration_source='source_document', duration_days=10,
                duration_calendar_verified=True, duration_evidence=original_evidence,
                planned_start_date='2026-01-06', schedule_generated_fields=['planned_start_date'], **changes)
            reviewed, audit, *_ = reconcile_source_durations([task], {'files': [source()]})
            self.assertTrue(reviewed[0]['duration_calendar_verified'])
            self.assertEqual(reviewed[0]['duration_evidence'], original_evidence)
            self.assertEqual(reviewed[0]['duration_comparison_evidence']['values']['original_duration_days'], 5)
            self.assertEqual(reviewed[0]['planned_start_date'], '2026-01-06')
            self.assertEqual(reviewed[0]['duration_days'], 10)
            self.assertEqual(audit[0]['source_original_duration_days'], 5)

    def test_progress_actuals_and_historical_context_are_not_overwritten(self):
        for changes, context in [({'progress_percent': 1}, {}), ({'actual_start_date': '2026-01-06'}, {}),
                                 ({}, {'historical': True}), ({}, {'read_only': True}),
                                 ({'duration_confirmed_at': '2026-01-05'}, {})]:
            with self.subTest(changes=changes, context=context):
                tasks, *_ = reconcile_source_durations([activity(**changes)], {'files': [source()], **context})
                self.assertEqual(tasks[0]['duration_days'], 10)

    def test_deleted_unparsed_samples_and_title_only_mdr_do_not_set_duration(self):
        context = {'files': [source(is_deleted=True), source(2, parse_status='pending'),
                             source(3, category='output_schedule_sample'),
                             source(4, category='mdr', text='ID|DOCUMENT TITLE\n1|MASTER DELIVERABLE REGISTER')]}
        tasks, *_ = reconcile_source_durations([activity()], context)
        self.assertIsNone(tasks[0]['duration_days'])

    def test_stale_source_duration_is_cleared_if_uploaded_fact_is_removed(self):
        first, *_ = reconcile_source_durations([activity()], {'files': [source()]})
        second, *_ = reconcile_source_durations(first, {'files': []})
        self.assertIsNone(second[0]['duration_days'])
        self.assertIsNone(second[0]['duration_evidence'])

    def test_explicit_milestone_zero_is_retained_without_claiming_an_uploaded_fact(self):
        actual = activity('end', 'PROJECT COMPLETE', is_milestone=True, duration_days=0)
        unknown = activity('unknown', 'Unknown end', is_milestone=True, duration_days=0)
        tasks, *_ = reconcile_source_durations([actual, unknown], {'files': [source()]})
        self.assertEqual(tasks[0]['duration_days'], 0)
        self.assertEqual(tasks[1]['duration_days'], 0)
        self.assertEqual(tasks[1]['duration_source'], 'milestone_definition')
        self.assertEqual(tasks[1]['duration_review_status'], 'milestone_definition')
        repeated, *_ = reconcile_source_durations(tasks, {'files': [source()]})
        self.assertEqual(tasks, repeated)
        self.assertNotIn('planned_start_date', tasks[0])
        self.assertIsNone(tasks[0]['duration_evidence']['values']['planned_start_date'])

    def test_nonmilestone_zero_fact_cannot_silently_change_activity_type(self):
        tasks, *_ = reconcile_source_durations([activity('end', 'PROJECT COMPLETE')], {'files': [source()]})
        self.assertIsNone(tasks[0]['duration_days'])
        self.assertNotIn('is_milestone', tasks[0])

    def test_relationships_and_manual_dates_are_never_changed_by_duration_review(self):
        tasks = [activity('a', 'Other'), activity('b', depends_on=['a'], planned_start_date='2026-01-06',
            dependency_details=[{'task_id': 'a', 'type': 'FF', 'lag_days': -2}],
            dependency_rationales={'a': {'status': 'confirmed', 'evidence_type': 'source'}})]
        reviewed, *_ = reconcile_source_durations(tasks, {'files': [source()]})
        for key in ['depends_on', 'dependency_details', 'dependency_rationales', 'planned_start_date']:
            self.assertEqual(reviewed[1][key], tasks[1][key])

    def test_inputs_are_unchanged_and_repeated_reconciliation_is_stable(self):
        tasks, context = [activity()], {'files': [source()]}
        before = deepcopy((tasks, context))
        first, *_ = reconcile_source_durations(tasks, context)
        second, audit, summary, _ = reconcile_source_durations(first, context)
        self.assertEqual((tasks, context), before)
        self.assertEqual(first, second)
        self.assertFalse(audit[0]['changed'])
        self.assertEqual(summary['changed_count'], 0)
