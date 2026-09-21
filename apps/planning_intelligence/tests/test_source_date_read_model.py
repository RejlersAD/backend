"""Source dates must stay useful without pretending that CPM was calculated."""
from copy import deepcopy
from unittest import TestCase

from ..services.source_date_read_model import enrich_source_dates, source_date_fields


def evidence(start='2026-02-23', finish='2026-03-02', **extra):
    return {'basis': 'printed_schedule', 'activity_specific': True,
            'values': {'planned_start_date': start, 'planned_finish_date': finish,
                       'date_columns_status': 'parsed', 'original_duration_days': 5},
            'source_references': [{'file_id': 24, 'filename': 'Reference.pdf', 'locator': {'page': 2, 'row': 118}}],
            **extra}


class SourceDateReadModelTests(TestCase):
    def test_legacy_duration_evidence_exposes_dates_without_calendar_or_cpm(self):
        task = {'duration_evidence': evidence(), 'duration_calendar_verified': False,
                'planned_start_date': None, 'planned_finish_date': None, 'calculated': False}
        before = deepcopy(task)
        fields = source_date_fields(task)
        self.assertEqual(fields['source_start_date'], '2026-02-23')
        self.assertEqual(fields['source_finish_date'], '2026-03-02')
        self.assertEqual(fields['source_date_status'], 'extracted')
        self.assertEqual(fields['source_date_references'], task['duration_evidence']['source_references'])
        self.assertEqual(task, before)
        self.assertNotIn('planned_start_date', fields)
        self.assertNotIn('total_float_days', fields)

    def test_new_structured_evidence_keeps_one_endpoint_without_filling_the_other(self):
        item = evidence('2031-09-13', None, basis='structured_schedule_table',
                        field_status={'planned_start_date': 'extracted', 'planned_finish_date': 'not_specified'})
        task = {'source_evidence': item, 'duration_days': 10, 'planned_finish_date': '2031-10-01'}
        result = source_date_fields(task)
        self.assertEqual(result['source_date_status'], 'partial')
        self.assertEqual(result['source_start_date'], '2031-09-13')
        self.assertIsNone(result['source_finish_date'])

    def test_ambiguous_single_pdf_date_is_not_assigned_to_start_or_finish(self):
        item = evidence(None, None)
        item['values'].update(printed_single_date='2026-09-04', date_columns_status='ambiguous')
        result = source_date_fields({'duration_evidence': item, 'is_milestone': True})
        self.assertEqual(result['source_date_status'], 'ambiguous')
        self.assertIsNone(result['source_start_date'])
        self.assertIsNone(result['source_finish_date'])
        self.assertEqual(result['source_date_evidence']['finish'][0]['printed_single_date'], '2026-09-04')

    def test_unsupported_field_format_preserves_the_other_valid_endpoint(self):
        item = evidence(None, '2026-03-02', field_status={
            'planned_start_date': 'unsupported_date_format', 'planned_finish_date': 'extracted'})
        item['values']['date_columns_status'] = 'unresolved'
        result = source_date_fields({'source_evidence': item})
        self.assertEqual(result['source_date_status'], 'ambiguous')
        self.assertEqual(result['source_start_status'], 'ambiguous')
        self.assertEqual(result['source_finish_date'], '2026-03-02')

    def test_printed_field_placeholders_do_not_hide_an_ambiguous_single_date(self):
        item = evidence(None, None, field_evidence={
            'planned_start_date': {'status': 'not_specified'},
            'planned_finish_date': {'status': 'not_specified'},
        })
        item['values'].update(printed_single_date='2038-07-12', date_columns_status='ambiguous')
        result = source_date_fields({'source_evidence': item})
        self.assertEqual(result['source_start_status'], 'ambiguous')
        self.assertEqual(result['source_finish_status'], 'ambiguous')
        self.assertIsNone(result['source_start_date'])
        self.assertIsNone(result['source_finish_date'])

    def test_malformed_or_reversed_dates_require_review(self):
        for start, finish in [('2026-02-30', '2026-03-02'), ('2026-04-01', '2026-03-02')]:
            with self.subTest(start=start):
                result = source_date_fields({'source_evidence': evidence(start, finish)})
                self.assertEqual(result['source_date_status'], 'invalid')
                self.assertIsNone(result['source_start_date'])

    def test_conflicting_stored_sources_do_not_select_one_date(self):
        task = {'source_evidence': evidence('2026-02-23'),
                'duration_evidence': evidence('2026-02-24')}
        result = source_date_fields(task)
        self.assertEqual(result['source_date_status'], 'conflicting')
        self.assertIsNone(result['source_start_date'])
        self.assertEqual(result['source_finish_date'], '2026-03-02')
        self.assertEqual(len(result['source_date_evidence']['start']), 2)

    def test_matching_evidence_copies_do_not_become_ambiguous(self):
        result = source_date_fields({'source_evidence': evidence(), 'duration_evidence': evidence()})
        self.assertEqual(result['source_date_status'], 'extracted')
        self.assertEqual(len(result['source_date_references']), 1)

    def test_untraceable_requirement_and_comparison_dates_cannot_supply_activity_dates(self):
        for task in ({'source_evidence': evidence(source_references=[])},
                     {'duration_evidence': evidence(activity_specific=False)},
                     {'duration_comparison_evidence': evidence()},
                     {'planned_start_date': '2026-02-23', 'planned_finish_date': '2026-03-02'},
                     {'source_start_date': '2026-02-23', 'source_finish_date': '2026-03-02'}):
            with self.subTest(task=task):
                result = source_date_fields(task)
                self.assertEqual(result['source_date_status'], 'not_specified')
                self.assertIsNone(result['source_start_date'])
                self.assertIsNone(result['source_finish_date'])

    def test_parent_summary_uses_its_own_source_and_never_child_span(self):
        state = {'tasks': [{'id': 'child', 'duration_evidence': evidence()}],
                 'deliverables': [
                     {'id': 'parent', 'duration_evidence': evidence('2026-02-23', '2026-04-23'),
                      'summary': {'planned_start_date': None, 'duration_days': None}},
                     {'id': 'missing', 'workflow_task_ids': ['child'], 'summary': {'duration_days': None}},
                 ], 'project_summary': {'planned_start_date': None}}
        enrich_source_dates(state)
        parent, missing = state['deliverables']
        self.assertEqual(parent['source_finish_date'], '2026-04-23')
        self.assertEqual(parent['summary']['source_finish_date'], '2026-04-23')
        self.assertIsNone(parent['summary']['duration_days'])
        self.assertIsNone(missing['summary']['source_finish_date'])
        self.assertNotIn('source_start_date', state['project_summary'])
