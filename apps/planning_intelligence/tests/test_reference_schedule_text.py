"""Pure text evidence tests: no Django setup, database, or original-file IO."""
from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch

from ..services.reference_schedule_text import parse_reference_schedule_text


HEADER = '# Activity ID Activity Name Original Start Finish Total Float March 2026 April 2026\nDuration\n'
FOOTER = '\nDate Revision Checked Approved\nActual Level of Effort summary Page 1 of 1\n'
STAGES = ['IFR', 'COMPANY REVIEW', 'IFA', 'COMPANY APPROVAL', 'IFT/IFM']


def doubled(value):
    return ''.join(character if character.isspace() else character * 2 for character in value)


def summary(number, title, duration=40, start='23-Feb-26', finish='23-Apr-26', total_float=89):
    return f'{number} {doubled(title)} {duration} {start} {finish} {total_float} {title}'


def stage_rows(first=3, title='MASTER DELIVERABLE REGISTER', prefix='GEN'):
    durations = [5, 10, 10, 5, 10]
    dates = [('23-Feb-26', '02-Mar-26'), ('02-Mar-26', '16-Mar-26'),
             ('16-Mar-26', '02-Apr-26'), ('02-Apr-26', '09-Apr-26'), ('09-Apr-26', '23-Apr-26')]
    return [f'{first + index} {prefix}_{1850 + index * 10} {title} - {stage} {durations[index]} '
            f'{dates[index][0]} {dates[index][1]} 89 {title} - {stage}' for index, stage in enumerate(STAGES)]


def document(rows, header=HEADER):
    return header + '\n'.join(rows) + FOOTER


class ReferenceScheduleTextTests(TestCase):
    def test_source_summary_and_five_stage_values_are_preserved_without_calendar_or_logic_claims(self):
        text = document([summary(1, 'SOURCE PROJECT', 165, '06-Jan-26', '04-Sep-26', 0),
                         summary(2, 'MASTER DELIVERABLE REGISTER'), *stage_rows()])
        with patch('builtins.open', side_effect=AssertionError('must not open original files')):
            result = parse_reference_schedule_text(text)
        self.assertEqual(result['status'], 'parsed')
        self.assertEqual(result['project_summary']['title'], 'SOURCE PROJECT')
        self.assertEqual(result['project_summary']['original_duration_days'], 165)
        self.assertEqual(result['project_summary']['planned_start_date'], '2026-01-06')
        self.assertEqual(result['project_summary']['planned_finish_date'], '2026-09-04')
        self.assertEqual(result['project_summary']['total_float_days'], 0)
        group = result['deliverables'][0]
        self.assertEqual(group['stage_names'], STAGES)
        self.assertEqual(group['workflow_task_ids'], ['GEN_1850', 'GEN_1860', 'GEN_1870', 'GEN_1880', 'GEN_1890'])
        self.assertEqual([row['original_duration_days'] for row in group['activities']], [5, 10, 10, 5, 10])
        self.assertEqual(group['activities'][0]['planned_finish_date'], '2026-03-02')
        self.assertEqual(group['activities'][1]['planned_start_date'], '2026-03-02')
        self.assertEqual(group['title_match_status'], 'matched')
        self.assertFalse(result['logic_verified'])
        self.assertFalse(result['calendar_verified'])
        self.assertIsNone(result['relationships'])
        self.assertIsNone(result['calendar'])
        self.assertTrue(all('depends_on' not in row for row in result['activities']))

    def test_single_milestone_date_is_kept_without_guessing_which_column_it_occupied(self):
        result = parse_reference_schedule_text(document([
            summary(1, 'SOURCE PROJECT'), '2 A.03 FINISH GATE 0 23-Apr-26 0 FINISH GATE',
        ]))
        task = result['activities'][0]
        self.assertEqual(task['original_duration_days'], 0)
        self.assertTrue(task['is_milestone'])
        self.assertEqual(task['printed_single_date'], '2026-04-23')
        self.assertIsNone(task['planned_start_date'])
        self.assertIsNone(task['planned_finish_date'])
        self.assertEqual(task['date_columns_status'], 'ambiguous')
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['issues'][0]['code'], 'single_date_column_unknown')

    def test_exact_doubled_letters_preserve_real_repeated_characters_and_gantt_tail_is_not_title(self):
        result = parse_reference_schedule_text(document([
            summary(1, 'BOOKKEEPER / FEED'),
            '2 ID_20 FEED & COOLING LOOP 2 02-Mar-26 03-Mar-26 -2 DIFFERENT TIMELINE LABEL',
        ]))
        self.assertEqual(result['project_summary']['title'], 'BOOKKEEPER / FEED')
        self.assertEqual(result['activities'][0]['title'], 'FEED & COOLING LOOP')
        self.assertEqual(result['activities'][0]['total_float_days'], -2)
        malformed = parse_reference_schedule_text(document(['1 BBOOOKK 5 02-Mar-26 06-Mar-26 0 BOOK']))
        self.assertEqual(malformed['rows'], [])
        self.assertIn('row_identity_unknown', [issue['code'] for issue in malformed['issues']])

    def test_groups_cross_physical_pages_and_duplicate_titles_remain_distinct(self):
        first = [summary(1, 'SOURCE PROJECT'), summary(2, 'REPEATED DRAWING'), *stage_rows(title='REPEATED DRAWING')[:2]]
        second = [*stage_rows(title='REPEATED DRAWING')[2:], summary(8, 'REPEATED DRAWING'),
                  *stage_rows(first=9, title='REPEATED DRAWING', prefix='ELE')]
        result = parse_reference_schedule_text(document(first) + '\f' + document(second))
        self.assertEqual(result['deliverable_count'], 2)
        self.assertEqual([group['id'] for group in result['deliverables']], ['pdf-deliverable-2', 'pdf-deliverable-8'])
        self.assertEqual(result['deliverables'][0]['activities'][2]['source_locator']['page'], 2)
        self.assertNotEqual(result['deliverables'][0]['workflow_task_ids'], result['deliverables'][1]['workflow_task_ids'])

    def test_order_or_title_mismatch_is_not_silently_normalized_into_a_workflow(self):
        rows = stage_rows(title='OTHER DRAWING')
        result = parse_reference_schedule_text(document([summary(1, 'SOURCE PROJECT'), summary(2, 'DRAWING'), *rows]))
        self.assertEqual(result['deliverables'][0]['title_match_status'], 'mismatch')
        self.assertIn('deliverable_title_mismatch', [issue['code'] for issue in result['issues']])
        rows[1], rows[2] = rows[2], rows[1]
        result = parse_reference_schedule_text(document([summary(1, 'SOURCE PROJECT'), summary(2, 'DRAWING'), *rows]))
        self.assertEqual(result['deliverables'], [])

    def test_incomplete_rows_duplicates_and_truncation_are_explicit(self):
        result = parse_reference_schedule_text(document([
            summary(1, 'SOURCE PROJECT'), '3 A1 Task 2 02-Mar-26 03-Mar-26 0',
            '4 A1 Duplicate ID 2 02-Mar-26 03-Mar-26 0',
            '4 B2 Duplicate row 2 02-Mar-26 03-Mar-26 0',
            '5 C3 Missing columns',
        ]) + '\n...[truncated]')
        codes = [issue['code'] for issue in result['issues']]
        for code in ['text_truncated', 'duplicate_activity_ids', 'duplicate_row_numbers', 'missing_rows', 'row_cells_ambiguous']:
            self.assertIn(code, codes)
        self.assertEqual(result['status'], 'partial')

    def test_invalid_date_or_ambiguous_century_stays_unknown_and_inverted_dates_are_preserved(self):
        result = parse_reference_schedule_text(document([
            summary(1, 'SOURCE PROJECT'), '2 A1 Invalid 2 31-Feb-26 03-Mar-26 0',
            '3 B1 Inverted 2 05-Mar-26 03-Mar-26 0',
        ]))
        self.assertIsNone(result['activities'][0]['planned_start_date'])
        self.assertIsNone(result['activities'][0]['planned_finish_date'])
        self.assertEqual(result['activities'][1]['planned_start_date'], '2026-03-05')
        self.assertIn('date_range_invalid', [issue['code'] for issue in result['issues']])
        no_year = parse_reference_schedule_text(document([summary(1, 'SOURCE PROJECT')], header=HEADER.replace('2026', '')))
        self.assertIsNone(no_year['project_summary']['planned_start_date'])

    def test_unsupported_headers_or_nontext_inputs_do_not_become_schedule_evidence(self):
        for text in [None, {}, '', 'Activity Name Duration Start Finish\n1 SAMPLE 5 02-Mar-26 06-Mar-26 0', 'Ordinary scope narrative']:
            self.assertEqual(parse_reference_schedule_text(text)['status'], 'not_detected')

    def test_extreme_row_number_is_bounded_and_negative_duration_is_unknown(self):
        result = parse_reference_schedule_text(document([
            summary(1, 'SOURCE PROJECT'), '999999999999 A1 Attack 2 02-Mar-26 03-Mar-26 0',
            '2 A2 Invalid duration -2 02-Mar-26 03-Mar-26 0',
        ]))
        self.assertIn('row_number_limit', [issue['code'] for issue in result['issues']])
        self.assertIsNone(result['activities'][0]['original_duration_days'])

    def test_missing_page_separators_and_multiple_project_roots_do_not_receive_false_identity(self):
        first = document([summary(1, 'PROJECT ONE')])
        second = document([summary(1, 'PROJECT TWO')])
        result = parse_reference_schedule_text(first + '\n' + second)
        self.assertIsNone(result['page_count'])
        self.assertTrue(all(row['source_locator']['page'] is None for row in result['rows']))
        self.assertIsNone(result['project_summary'])

    def test_calls_do_not_share_mutable_results(self):
        text = document([summary(1, 'SOURCE PROJECT'), summary(2, 'DRAWING'), *stage_rows(title='DRAWING')])
        first = parse_reference_schedule_text(text)
        expected = deepcopy(first)
        first['deliverables'][0]['stage_names'][0] = 'Changed'
        self.assertEqual(parse_reference_schedule_text(text), expected)
