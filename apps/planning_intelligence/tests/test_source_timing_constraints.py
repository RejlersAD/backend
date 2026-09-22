"""Source-first evidence must not silently turn a printed table into a CPM plan."""
from copy import deepcopy
import hashlib

from django.test import SimpleTestCase

from ..services.source_timing_constraints import source_timing_evidence


def printed_text():
    def double(value):
        return ''.join(character * 2 if character != ' ' else character for character in value)

    return '\n'.join([
        '2026', '# Activity ID Activity Name Original Duration Start Finish Total Float',
        f'1 {double("PROJECT")} 165 06-Jan-26 04-Sep-26 0',
        f'2 {double("MASTER DELIVERABLE REGISTER")} 40 23-Feb-26 23-Apr-26 89',
        '3 GEN_1850 MASTER DELIVERABLE REGISTER - IFR 5 23-Feb-26 02-Mar-26 89',
        '4 GEN_1860 MASTER DELIVERABLE REGISTER - COMPANY REVIEW 10 02-Mar-26 16-Mar-26 89',
        '5 GEN_1870 MASTER DELIVERABLE REGISTER - IFA 10 16-Mar-26 02-Apr-26 89',
        '6 GEN_1880 MASTER DELIVERABLE REGISTER - COMPANY APPROVAL 5 02-Apr-26 09-Apr-26 89',
        '7 GEN_1890 MASTER DELIVERABLE REGISTER - IFT/IFM 10 09-Apr-26 23-Apr-26 89',
        '8 A1130 PROJECT COMPLETE 0 04-Sep-26 0',
    ])


def source(identifier=1, **overrides):
    return {'id': identifier, 'filename': 'Approved schedule.pdf', 'category': 'reference_schedule',
            'parse_status': 'done', 'text': printed_text(), **overrides}


def scoped_task(identifier, title, activity_id=None, *, source_upload=None, line=None):
    document = source_upload or source()
    reference = {'file_id': document['id'],
                 'extracted_text_sha256': hashlib.sha256(document['text'].encode('utf-8')).hexdigest()}
    if line is not None:
        reference['locator'] = {'line': line}
    return {'id': identifier, 'title': title, 'source_activity_id': activity_id, 'source_references': [reference]}


class SourceTimingEvidenceTests(SimpleTestCase):
    def test_inherited_parent_locator_cannot_supply_duration_for_a_workflow_child(self):
        parent = scoped_task('parent', 'MASTER DELIVERABLE REGISTER', line=4)
        child = {**parent, 'id': 'child', 'title': 'MASTER DELIVERABLE REGISTER - IFR',
                 'parent_deliverable_id': 'parent', 'workflow_stage_code': 'IFR'}
        result = source_timing_evidence([parent, child], {'files': [source()]})
        self.assertEqual(result['matched_tasks']['parent']['values']['original_duration_days'], 40)
        self.assertNotIn('child', result['matched_tasks'])
        child.update(source_activity_id='GEN_1850')
        result = source_timing_evidence([child], {'files': [source()]})
        self.assertEqual(result['matched_tasks']['child']['values']['original_duration_days'], 5)

    def test_generic_evidence_supports_different_project_tables_without_project_summary(self):
        text = ('Task ID,Task Name,Duration (working days),Predecessors,Start\n'
                'QA2,Instrument calibration,4,QA1:FS+0d,2032-07-10\n')
        evidence = source_timing_evidence([scoped_task('calibrate', 'Instrument calibration', 'QA2', source_upload=source(text=text))],
                                         {'files': [source(filename='arbitrary.csv', text=text)]})
        self.assertEqual(len(evidence['evidence_records']), 1)
        self.assertEqual(evidence['matched_tasks']['calibrate']['values']['original_duration_days'], 4)
        row = evidence['evidence_records'][0]
        self.assertEqual(row['relationships'][0]['predecessor_id'], 'QA1')
        self.assertEqual(row['relationships'][0]['lag']['value'], 0)
        self.assertEqual(row['field_evidence']['duration']['header'], 'Duration (working days)')
        self.assertEqual(row['field_evidence']['duration']['raw_text'], '4')
        self.assertEqual(row['source_references'][0]['excerpt'], text.splitlines(keepends=True)[1])
        self.assertFalse(any('No supported activity schedule' in item for item in evidence['warnings']))
        self.assertFalse(row['can_apply_to_cpm'])

    def test_generic_filename_does_not_supply_facts_or_infer_dependency(self):
        text = 'Task|Duration (days)\nCommissioning|6\nCloseout|3\n'
        one = source_timing_evidence([], {'files': [source(filename='Closeout_after_commissioning.xlsx', text=text)]})
        two = source_timing_evidence([], {'files': [source(filename='unrelated.txt', text=text)]})
        self.assertEqual([item['values'] for item in one['evidence_records']],
                         [item['values'] for item in two['evidence_records']])
        self.assertTrue(all(item['relationships'] is None for item in one['evidence_records']))

    def test_generic_and_printed_adapters_remain_independent_and_do_not_duplicate(self):
        text = printed_text() + '\n--- Table: 2 ---\nTask ID|Task|Duration (days)\nX9|Independent audit|2\n'
        result = source_timing_evidence([], {'files': [source(text=text)]})
        self.assertEqual(len(result['evidence_records']), 9)
        self.assertEqual(len([item for item in result['evidence_records'] if item['basis'] == 'structured_schedule_table']), 1)
        self.assertEqual(len(result['extraction_reports']), 1)
        self.assertEqual(len(result['extraction_reports'][0]['adapters']), 2)

    def test_generic_uploaded_revisions_and_duplicate_task_titles_are_not_arbitrarily_selected(self):
        text = 'Task|Duration (days)\nDesign package|7\n'
        task = {'id': 'design', 'title': 'Design package'}
        result = source_timing_evidence([task], {'files': [source(text=text), source(2, text=text)]})
        self.assertEqual(result['matched_tasks'], {})
        self.assertEqual(len(result['evidence_records']), 2)
        self.assertTrue(any('ambiguous' in item for item in result['warnings']))

    def test_generic_unsupported_document_has_visible_extraction_report(self):
        text = 'Requirements narrative without an explicit activity table.'
        result = source_timing_evidence([], {'files': [source(text=text)]})
        report = result['extraction_reports'][0]
        self.assertEqual(report['adapters'][0]['status'], 'not_detected')
        self.assertEqual(report['adapters'][0]['coverage']['uninterpreted_line_count'], 1)
        self.assertFalse(report['complete_document_understanding'])

    def test_same_source_repeated_in_context_deduplicates_evidence_not_distinct_revisions(self):
        text = 'Task|Duration (days)\nSite inspection|2\n'
        result = source_timing_evidence([scoped_task('site', 'Site inspection', source_upload=source(text=text), line=2)],
                                         {'files': [source(text=text), source(text=text)]})
        self.assertEqual(len(result['evidence_records']), 1)
        self.assertEqual(result['matched_tasks']['site']['values']['original_duration_days'], 2)

    def test_explicit_source_identity_matches_same_named_activities_uniquely(self):
        text = 'ID|Task|Duration (days)\nA|Review|2\nB|Review|5\n'
        tasks = [scoped_task('first', 'Review', 'A', source_upload=source(text=text)),
                 scoped_task('second', 'Review', 'B', source_upload=source(text=text))]
        result = source_timing_evidence(tasks, {'files': [source(text=text)]})
        self.assertEqual(result['matched_tasks']['first']['values']['original_duration_days'], 2)
        self.assertEqual(result['matched_tasks']['second']['values']['original_duration_days'], 5)

    def test_source_identity_without_scope_or_version_or_with_duplicate_id_is_not_selected(self):
        task = {'id': 'test', 'title': 'Review', 'source_activity_id': 'A'}
        text = 'ID|Task|Duration (days)\nA|Review|2\n'
        self.assertEqual(source_timing_evidence([task], {'files': [source(text=text)]})['matched_tasks'], {})
        task['source_references'] = [{'file_id': 1}]
        duplicate = text + 'A|Other review|9\n'
        self.assertEqual(source_timing_evidence([task], {'files': [source(text=duplicate)]})['matched_tasks'], {})
        task['title'] = 'Unrelated activity'
        self.assertEqual(source_timing_evidence([task], {'files': [source(text=text)]})['matched_tasks'], {})

    def test_preserves_original_values_and_stage_dates_without_mutating_or_importing(self):
        tasks = [{**scoped_task('mdr', 'Master Deliverable Register', line=4), 'duration_days': 31,
                  'planned_start_date': '2026-01-06'}]
        context = {'files': [source()]}
        before = deepcopy((tasks, context))
        evidence = source_timing_evidence(tasks, context)
        match = evidence['matched_tasks']['mdr']
        self.assertEqual(match['values']['original_duration_days'], 40)
        self.assertEqual(match['values']['planned_start_date'], '2026-02-23')
        self.assertEqual(match['values']['planned_finish_date'], '2026-04-23')
        self.assertEqual(match['values']['total_float_days'], 89)
        self.assertEqual([item['values']['original_duration_days'] for item in match['workflow_activities']],
                         [5, 10, 10, 5, 10])
        self.assertEqual(match['workflow_activities'][0]['values']['planned_finish_date'],
                         match['workflow_activities'][1]['values']['planned_start_date'])
        self.assertEqual(match['source_references'][0]['locator']['row'], 2)
        self.assertEqual(match['source_references'][0]['file_id'], 1)
        self.assertFalse(match['can_apply_to_cpm'])
        self.assertFalse(evidence['exact_import_verified'])
        self.assertEqual(evidence['project_summaries'][0]['values']['original_duration_days'], 165)
        self.assertEqual((tasks, context), before)

    def test_duplicate_titles_and_multiple_uploaded_revisions_are_not_selected(self):
        task = {'id': 'mdr', 'title': 'MASTER DELIVERABLE REGISTER'}
        duplicate_tasks = [task, {**task, 'id': 'mdr-copy'}]
        self.assertEqual(source_timing_evidence(duplicate_tasks, {'files': [source()]})['matched_tasks'], {})
        self.assertEqual(source_timing_evidence([task], {'files': [source(), source(2)]})['matched_tasks'], {})

    def test_never_fuzzy_matches_or_substitutes_final_issue_for_source_ift_ifm(self):
        tasks = [{'id': 'approximate', 'title': 'MASTER DOCUMENT REGISTER'},
                 {'id': 'stage', 'title': 'MASTER DELIVERABLE REGISTER - FINAL ISSUE'},
                 scoped_task('exact', ' master   deliverable register - IFR ', 'GEN_1850')]
        result = source_timing_evidence(tasks, {'files': [source()]})
        self.assertEqual(set(result['matched_tasks']), {'exact'})

    def test_single_date_milestone_preserves_ambiguity_instead_of_assigning_date(self):
        evidence = source_timing_evidence([scoped_task('end', 'PROJECT COMPLETE', 'A1130')], {'files': [source()]})
        values = evidence['matched_tasks']['end']['values']
        self.assertEqual(values['printed_single_date'], '2026-09-04')
        self.assertIsNone(values['planned_start_date'])
        self.assertIsNone(values['planned_finish_date'])
        self.assertEqual(values['date_columns_status'], 'ambiguous')

    def test_review_requirement_uses_processed_source_evidence_and_keeps_award_unanchored(self):
        sow = source(2, filename='SOW.pdf', category='sow',
                     text='Company review requires 10 working days.\nCompletion is 28 weeks after award.')
        context = {'files': [sow]}
        result = source_timing_evidence([{'id': 'a', 'title': 'Design'}], context)
        self.assertEqual(result['review_requirement']['duration_days'], 10)
        self.assertEqual(result['review_requirement']['source_references'][0]['file_id'], 2)
        relative = next(item for item in result['source_constraints'] if item['kind'] == 'relative_weeks')
        self.assertEqual(relative['anchor_status'], 'unconfirmed')
        self.assertEqual(result['matched_tasks'], {})
        conflicting = source(3, category='sow', text='Company review requires 15 working days.')
        self.assertIsNone(source_timing_evidence([], {'files': [sow, conflicting]})['review_requirement'])

    def test_unparsed_deleted_output_sample_and_unidentified_sources_are_ignored(self):
        files = [source(parse_status='pending'), source(2, is_deleted=True),
                 source(3, category='output_schedule_sample'), source(None)]
        result = source_timing_evidence([{'id': 'a', 'title': 'MASTER DELIVERABLE REGISTER'}], {'files': files})
        self.assertEqual(result['matched_tasks'], {})
        self.assertEqual(result['project_summaries'], [])

    def test_duplicate_printed_identity_prevents_matching(self):
        text = printed_text() + '\n8 A1130 PROJECT COMPLETE 0 04-Sep-26 0'
        result = source_timing_evidence([{'id': 'a', 'title': 'MASTER DELIVERABLE REGISTER'}],
                                        {'files': [source(text=text)]})
        self.assertEqual(result['matched_tasks'], {})
        self.assertTrue(any('duplicate' in message for message in result['warnings']))

    def test_register_without_timing_does_not_invent_activity_dates_or_durations(self):
        mdr = source(category='mdr', filename='MDR.xlsx',
                     text='SL. NO.|DISCIPLINE|DOCUMENT TITLE\n1|GENERAL|MASTER DELIVERABLE REGISTER')
        result = source_timing_evidence([{'id': 'a', 'title': 'MASTER DELIVERABLE REGISTER'}], {'files': [mdr]})
        self.assertEqual(result['matched_tasks'], {})
        self.assertIsNone(result['review_requirement'])
        self.assertFalse(result['exact_import_verified'])
