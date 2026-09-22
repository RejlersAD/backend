"""Original PDF column positions, blank cells and hierarchy remain source facts."""
from copy import deepcopy
from io import BytesIO
import hashlib
from types import SimpleNamespace
from unittest import TestCase

import pymupdf

from ..services.reference_schedule_geometry import cached_schedule_geometry, parse_reference_schedule_pdf
from ..services.parsers import extract_text_with_coverage
from ..services.source_timing_constraints import source_timing_evidence
from ..services.document_plan import build_document_plan


def pdf_document(pages, *, year='2026', translated=True):
    document = pymupdf.open()
    for index, rows in enumerate(pages):
        page = document.new_page(width=900, height=500)
        offset = 24 + (17 * index if translated else 0)
        widths = [30, 105, 285, 58, 80, 80, 64]
        labels = ['#', 'Activity ID', 'Activity Name', 'Original', 'Start', 'Finish', 'Total Float']
        columns = []
        for width, label in zip(widths, labels):
            columns.append(offset)
            page.draw_rect(pymupdf.Rect(offset, 50, offset + width, 74))
            page.insert_text((offset + 2, 60), label, fontsize=8)
            if label == 'Original':
                page.insert_text((offset + 2, 70), 'Duration', fontsize=8)
            offset += width
        if year:
            page.insert_text((40, 28), f'Schedule {year}', fontsize=10)
        page.draw_line(pymupdf.Point(columns[0], 50), pymupdf.Point(columns[0], 100 + len(rows) * 20))
        for position, row in enumerate(rows):
            y = 90 + position * 20
            number, kind, title, indentation, duration, start, finish, total_float = row
            page.insert_text((columns[0] + 2, y), str(number), fontsize=8)
            page.insert_text((columns[1] + indentation, y), title if kind == 'summary' else kind, fontsize=8)
            if kind != 'summary':
                page.insert_text((columns[2] + 2, y), title, fontsize=8)
            for column, value in zip(columns[3:], [duration, start, finish, total_float]):
                if value != '':
                    page.insert_text((column + 2, y), str(value), fontsize=8)
    data = document.tobytes()
    document.close()
    return data


def source_pdf():
    return pdf_document([
        [(1, 'summary', 'Original project', 4, 25, '06-Jan-26', '30-Jan-26', 0),
         (2, 'summary', 'Design package', 14, 12, '06-Jan-26', '23-Jan-26', -2.5),
         (3, 'START_1', 'Start gate', 24, 0, '06-Jan-26', '', 0),
         (4, 'FINISH_1', 'Finish gate', 24, 0, '', '23-Jan-26', -2.5)],
        [(5, 'ACT_1', 'Prepare drawing', 24, 7.5, '07-Jan-26', '22-Jan-26', -2.5)],
    ])


class ReferenceScheduleGeometryTests(TestCase):
    def test_original_cells_blank_milestone_dates_and_translated_page_hierarchy(self):
        data = source_pdf()
        result = parse_reference_schedule_pdf(BytesIO(data))
        self.assertEqual(result['status'], 'parsed', result['issues'])
        self.assertEqual((result['row_count'], result['activity_count'], result['page_count']), (5, 3, 2))
        self.assertEqual(result['checksum_sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(result['project_summary']['original_duration_days'], 25)
        first, second, task = result['activities']
        self.assertEqual((first['planned_start_date'], first['planned_finish_date']), ('2026-01-06', None))
        self.assertEqual(first['record_type'], 'start milestone')
        self.assertEqual(first['field_evidence']['planned_finish_date']['status'], 'explicit_none')
        self.assertEqual((second['planned_start_date'], second['planned_finish_date']), (None, '2026-01-23'))
        self.assertEqual(second['record_type'], 'finish milestone')
        self.assertEqual(second['total_float_days'], -2.5)
        self.assertEqual(task['original_duration_days'], 7.5)
        self.assertEqual(task['source_hierarchy'], {'row_number': 5, 'parent_row_number': 2, 'level': 2, 'basis': 'printed_pdf_indentation'})
        self.assertEqual(task['source_locator']['page'], 2)
        self.assertEqual(len(task['field_evidence']['planned_start_date']['source_locator']['bbox']), 4)
        self.assertIsNone(result['relationships'])
        self.assertIsNone(result['calendar'])
        self.assertFalse(result['logic_verified'])
        self.assertFalse(result['calendar_verified'])

    def test_parser_cache_and_evidence_document_pipeline_preserve_exact_fields(self):
        file = BytesIO(source_pdf())
        file.name = 'source/schedule.pdf'
        text, confidence, coverage = extract_text_with_coverage(file, 'schedule.pdf')
        self.assertIn('Original', text)
        self.assertGreater(confidence, 0)
        geometry = coverage['structured_evidence']['reference_schedule_geometry']
        uploaded = SimpleNamespace(file=file, extracted_text=text,
            document_profile=SimpleNamespace(extraction_coverage=coverage))
        self.assertEqual(cached_schedule_geometry(uploaded)['checksum_sha256'], geometry['checksum_sha256'])
        source = {'id': 17, 'project_id': 4, 'filename': 'schedule.pdf', 'category': 'schedule', 'parse_status': 'done',
                  'text': text, 'structured_evidence': {'reference_schedule_geometry': geometry}}
        evidence = source_timing_evidence([], {'files': [source]})
        self.assertEqual(len(evidence['evidence_records']), 5)
        first = next(row for row in evidence['evidence_records'] if row['activity_id'] == 'START_1')
        self.assertTrue(first['values']['is_milestone'])
        self.assertEqual(first['record_type'], 'start milestone')
        self.assertEqual(first['field_status']['planned_finish_date'], 'explicit_none')
        self.assertEqual(first['source_hierarchy']['parent_row_number'], 2)
        self.assertEqual(first['source_references'][0]['checksum_sha256'], geometry['checksum_sha256'])
        self.assertEqual(len(first['source_references'][0]['locator']['bbox']), 4)
        plan = build_document_plan([source])
        activity = next(row for row in plan['activities'] if row['source_activity_id'] == 'START_1')
        self.assertEqual(activity['start_date'], '2026-01-06')
        self.assertIsNone(activity['finish_date'])
        self.assertTrue(activity['is_milestone'])
        self.assertEqual(activity['source_evidence']['field_status']['planned_finish_date'], 'explicit_none')
        self.assertEqual(activity['source_evidence']['source_hierarchy']['parent_row_number'], 2)
        uploaded.extracted_text += ' changed'
        self.assertIsNone(cached_schedule_geometry(uploaded))
        uploaded.extracted_text = text
        uploaded.file.name = 'source/replaced.pdf'
        self.assertIsNone(cached_schedule_geometry(uploaded))

    def test_unanchored_year_invalid_cells_and_missing_rows_are_explicit(self):
        data = pdf_document([[(1, 'summary', 'Project', 4, 9, '06-Jan-26', '22-Jan-26', 0),
                              (3, 'ACT_1', 'Task', 14, -1, '99-Jan-26', '', 0)]], year='')
        result = parse_reference_schedule_pdf(BytesIO(data))
        self.assertEqual(result['status'], 'partial')
        self.assertIn('missing_rows', {row['code'] for row in result['issues']})
        self.assertEqual(result['hierarchy_status'], 'unverified')
        self.assertNotIn('source_hierarchy', result['activities'][0])
        self.assertIsNone(result['activities'][0]['original_duration_days'])
        self.assertIsNone(result['activities'][0]['planned_start_date'])

    def test_unruled_pdf_and_stale_cache_do_not_claim_column_geometry(self):
        document = pymupdf.open()
        page = document.new_page()
        page.insert_text((30, 40), '# Activity ID Activity Name Original Start Finish Total Float 2026')
        result = parse_reference_schedule_pdf(BytesIO(document.tobytes()))
        document.close()
        self.assertEqual(result['status'], 'not_detected')
        self.assertEqual(result['rows'], [])
        geometry = parse_reference_schedule_pdf(BytesIO(source_pdf()))
        geometry['text_sha256'] = 'stale'
        source = {'id': 17, 'filename': 'schedule.pdf', 'parse_status': 'done', 'text': 'Unrelated text',
                  'structured_evidence': {'reference_schedule_geometry': deepcopy(geometry)}}
        self.assertEqual(source_timing_evidence([], {'files': [source]})['evidence_records'], [])

    def test_partial_or_failed_geometry_does_not_suppress_complete_text_fallback(self):
        text = ('# Activity ID Activity Name Original Start Finish Total Float March 2026\nDuration\n'
                '1 TEXT_1 Existing printed activity 2 02-Mar-26 03-Mar-26 0\n')
        geometry = parse_reference_schedule_pdf(BytesIO(source_pdf()))
        geometry['text_sha256'] = hashlib.sha256(text.encode('utf-8')).hexdigest()
        for status in ('partial', 'not_detected'):
            with self.subTest(status=status):
                geometry['status'] = status
                source = {'id': 17, 'filename': 'schedule.pdf', 'parse_status': 'done', 'text': text,
                          'structured_evidence': {'reference_schedule_geometry': geometry}}
                rows = source_timing_evidence([], {'files': [source]})['evidence_records']
                self.assertEqual([row['activity_id'] for row in rows], ['TEXT_1'])
