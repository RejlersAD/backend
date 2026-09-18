"""Read-model evidence checks using in-memory files; no database or file IO."""
from copy import deepcopy
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from ..services.source_schedule_verification import _printed_schedules, verify_plan_sources
from .test_reference_schedule_text import document, stage_rows, summary


SERVICE = 'apps.planning_intelligence.services.source_schedule_verification'


def source(file_id=23, category='reference_schedule', text='', **extra):
    return SimpleNamespace(
        pk=file_id, id=file_id, original_filename=f'Source-{file_id}.pdf',
        category=category, extracted_text=text,
        parse_status=extra.pop('parse_status', 'done'), is_deleted=False, **extra,
    )


class PrintedScheduleVerificationTests(TestCase):
    def setUp(self):
        self.enterContext(patch(
            'django.db.backends.base.base.BaseDatabaseWrapper.ensure_connection',
            side_effect=AssertionError('These evidence tests must not access any database.'),
        ))
        self.enterContext(patch(
            'django.db.models.fields.files.FieldFile.open',
            side_effect=AssertionError('The verification read model must not open original files.'),
        ))

    def verify(self, files, state):
        file_manager = MagicMock()
        file_manager.filter.return_value.only.return_value.order_by.return_value = files
        project = SimpleNamespace(files=file_manager)
        with patch(f'{SERVICE}._calendar_status', return_value={
            'status': 'default_unverified', 'name': 'Monday-Friday', 'exception_count': 0,
        }):
            return verify_plan_sources(project, state)

    def test_printed_165_is_separate_from_current_193_and_register_parents_are_counted_once(self):
        register = source(21, 'mdr', '--- Sheet: MDR ---\nSL. NO.|DISCIPLINE|DOCUMENT TITLE\n'
                          '1|HSE|Audit 30%\n2|HSE|Audit 60%\n')
        reference = source(text=document([
            summary(1, 'SOURCE PROJECT', 165, '06-Jan-26', '04-Sep-26', 0),
            summary(2, 'MASTER DELIVERABLE REGISTER'), *stage_rows(),
        ]))
        state = {
            'project_summary': {'duration_days': 193, 'planned_start_date': '2026-01-06',
                                'planned_finish_date': '2026-10-01'},
            'deliverables': [{'id': 'source-deliverable-1', 'title': 'Audit 30%', 'discipline': 'hse'}],
            'tasks': [
                {'id': 'stage-1', 'title': 'Audit 30% - IFR', 'discipline': 'hse',
                 'parent_deliverable_id': 'source-deliverable-1', 'duration_source': 'proposed',
                 'planned_start_date': None, 'planned_finish_date': None,
                 'depends_on': ['stage-0'], 'dependency_rationales': {
                     'stage-0': {'status': 'proposed', 'evidence_type': 'planning_inference'},
                 }},
                {'id': 'unparented', 'title': 'Audit 60%', 'discipline': 'hse'},
            ],
        }
        original = deepcopy(state)
        result = self.verify([register, reference], state)
        evidence = result['schedule_reference']['printed_schedules'][0]
        self.assertEqual(evidence['id'], 23)
        self.assertEqual(evidence['status'], 'parsed')
        self.assertEqual(evidence['project_summary'], {
            'title': 'SOURCE PROJECT', 'original_duration_days': 165,
            'planned_start_date': '2026-01-06', 'planned_finish_date': '2026-09-04',
            'total_float_days': 0, 'source_locator': {'page': 1, 'row': 1, 'line': 3},
            'date_columns_status': 'parsed',
        })
        self.assertEqual((evidence['row_count'], evidence['activity_count'], evidence['deliverable_count']), (7, 5, 1))
        self.assertNotIn('activities', evidence)
        self.assertNotIn('rows', evidence)
        self.assertNotIn('raw_text', evidence['project_summary'])
        self.assertEqual(result['document_register']['status'], 'matched')
        self.assertEqual(result['document_register']['expected_count'], 2)
        self.assertEqual(result['document_register']['matched_count'], 2)
        self.assertEqual(result['status'], 'unverified')
        self.assertEqual(result['schedule_reference']['status'], 'not_imported')
        self.assertEqual(result['schedule_reference']['blocker']['code'], 'reference_schedule_not_imported')
        self.assertEqual(result['timing']['proposed_duration_count'], 1)
        self.assertEqual(result['timing']['inferred_relationship_count'], 1)
        self.assertEqual(result['timing']['source_date_count'], 0)
        self.assertFalse(result['timing']['dates_verified'])
        self.assertFalse(result['timing']['dependencies_verified'])
        self.assertFalse(evidence['logic_verified'])
        self.assertFalse(evidence['calendar_verified'])
        self.assertEqual(result['calendar']['status'], 'default_unverified')
        self.assertEqual(state, original)

    def test_unlocated_source_date_remains_unknown_instead_of_using_current_plan_dates(self):
        reference = source(text=document(['1 SSOOUURRCCEE 165 04-Sep-26 0 SOURCE']))
        state = {'tasks': [], 'project_summary': {
            'planned_start_date': '2026-01-06', 'planned_finish_date': '2026-09-04',
        }}
        evidence = self.verify([reference], state)['schedule_reference']['printed_schedules'][0]
        self.assertEqual(evidence['status'], 'partial')
        self.assertIsNone(evidence['project_summary']['planned_start_date'])
        self.assertIsNone(evidence['project_summary']['planned_finish_date'])
        self.assertEqual(evidence['project_summary']['printed_single_date'], '2026-09-04')
        self.assertEqual(evidence['project_summary']['date_columns_status'], 'ambiguous')
        self.assertEqual(evidence['issues'][0]['code'], 'single_date_column_unknown')
        self.assertEqual(evidence['issues'][0]['count'], 1)
        self.assertEqual(evidence['issues'][0]['source_locators'][0]['row'], 1)

    def test_regular_response_has_bounded_issue_examples_and_no_full_table_or_private_fields(self):
        issues = [{'code': 'ambiguous', 'message': 'A date column is not known.',
                   'source_locator': {'page': 1, 'row': index + 1}} for index in range(28)]
        issues.extend({'code': f'other-{index}', 'message': 'Other extraction issue.'} for index in range(20))
        parsed = {'status': 'partial', 'project_summary': None,
                  'row_count': 1270, 'activity_count': 1048, 'deliverable_count': 196,
                  'page_count': 21, 'rows': ['must not return'], 'activities': ['must not return'],
                  'deliverables': ['must not return'], 'issues': issues}
        with patch(f'{SERVICE}.parse_reference_schedule_text', return_value=parsed):
            evidence = _printed_schedules([source(api_key='never-return', file='private/path')])[0]
        self.assertEqual(evidence['issue_count'], 48)
        self.assertEqual(len(evidence['issues']), 12)
        self.assertTrue(evidence['issues_truncated'])
        self.assertEqual(evidence['issues'][0]['count'], 28)
        self.assertEqual(len(evidence['issues'][0]['source_locators']), 3)
        self.assertEqual(evidence['activity_count'], 1048)
        self.assertEqual(evidence['deliverable_count'], 196)
        for field in ('activities', 'rows', 'deliverables', 'api_key', 'file', 'extracted_text'):
            self.assertNotIn(field, evidence)

    def test_unparsed_or_unsupported_candidates_are_not_reported_as_printed_schedules(self):
        pending = source(parse_status='pending', text=document([summary(1, 'OLD TEXT')]))
        with patch(f'{SERVICE}.parse_reference_schedule_text', side_effect=AssertionError('Do not use stale text.')):
            result = _printed_schedules([pending, source(2, 'sow', 'Ordinary prose.')])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['status'], 'not_parsed')
        self.assertIsNone(result[0]['project_summary'])
        self.assertEqual(result[0]['issues'][0]['code'], 'file_not_parsed')
        unsupported = _printed_schedules([source(text='Native schedule upload not decoded.')])[0]
        self.assertEqual(unsupported['status'], 'not_detected')
        self.assertIsNone(unsupported['project_summary'])
        self.assertEqual(unsupported['activity_count'], 0)
