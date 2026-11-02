"""Synthetic native grids verify mechanics, not engineering-labelled accuracy."""
import copy
from pathlib import Path
import tempfile

from django.test import SimpleTestCase, TestCase

from .services.config import SPEC_EXTRACTION_CONFIG
from .services.extraction_service import PaperSpecExtractionService
from .services.local_extraction import run_local_engine
from .services.local_tables import parse_size
from .services.lng_tables import size_bounds
from .services.data_quality import process_extracted_classes


def add_lng_page(document, code='A', logical=10, total=20, table=True,
                 invalid_size=False, blank_name=False, commodity=True, last_name='GATE VALVE', broken_header=False):
    import pymupdf
    page = document.new_page()
    page.insert_text((30, 40), 'ADNOC LNG PIPING SPECIFICATIONS', fontsize=9)
    page.insert_text((30, 60), 'A-100-A4Z-0-25020', fontsize=9)
    if code:
        page.insert_text((30, 90), f'PIPING SPEC: {code}', fontsize=9)
    page.insert_text((30, 800), f'Page {logical} of {total}', fontsize=9)
    if not table:
        page.insert_text((30, 130), 'GENERAL NOTES', fontsize=9)
        return
    xs = [30, 55, 130, 180, 225, 270, 310, 445, 520, 570]
    ys = [200, 220, 240, 270, 300, 330]

    def cell(r0, r1, c0, c1, text):
        box = pymupdf.Rect(xs[c0], ys[r0], xs[c1], ys[r1])
        page.draw_rect(box, width=.5)
        if text:
            result = page.insert_textbox(box + (2, 2, -2, -2), text, fontsize=7)
            assert result >= 0, text

    for column, text in [(0, 'REV\nNO'), (1, 'PART NAME'), (2, 'PART\nSYMBOL'),
                         (5, 'W.T.'), (6, 'DESCRIPTION'),
                         (7, 'COMMODITY\nCODE' if commodity else 'ME NO.'), (8, 'NOTES')]:
        cell(0, 2, column, column + 1, text)
    cell(0, 1, 3, 5, 'SIZE')
    cell(1, 2, 3, 4, 'START' if broken_header else 'FROM')
    cell(1, 2, 4, 5, 'TO')
    cell(2, 4, 1, 2, 'PIPE')
    cell(2, 4, 2, 3, 'PIP')
    cell(2, 4, 6, 7, 'SEAMLESS PIPE\nA106-B')
    for r, lower, upper, wt, identifier in [(2, '1/2"', '2"', '40', 'SOURCE-1'),
                                           (3, 'bad' if invalid_size else '3"', '4"', '', 'SOURCE-2')]:
        for c, text in [(0, ''), (3, lower), (4, upper), (5, wt), (7, identifier), (8, '')]:
            cell(r, r + 1, c, c + 1, text)
    for c, text in enumerate(['', '' if blank_name else last_name, 'GTV', '2"', '6"', '',
                              'A105 BALL TRIM', 'SOURCE-3', 'NOTE 1']):
        cell(4, 5, c, c + 1, text)


def parse_pages(pages, start=0, end=None, config=None):
    import pymupdf
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'synthetic-lng.pdf'
        with pymupdf.open() as document:
            for options in pages:
                add_lng_page(document, **options)
            document.save(path)
        return run_local_engine(path, start, len(pages) - 1 if end is None else end,
                                'pymupdf_text', config or SPEC_EXTRACTION_CONFIG, 30)


class LNGGridTests(SimpleTestCase):
    def test_real_grid_resolves_explicit_row_spans_and_keeps_blank_cells(self):
        result = parse_pages([{}])
        self.assertEqual(result['local_report']['components'], 3)
        self.assertEqual(result['local_report']['issues'], [])
        cls = result['piping_classes'][0]
        self.assertEqual(cls['class_code'], 'A')
        first, second, valve = cls['components']
        self.assertEqual(first['description'], second['description'])
        self.assertEqual(second['sub_type'], 'PIPE')
        self.assertEqual(second['schedule_or_rating'], '')
        self.assertEqual(second['_source_reference']['cells']['description']['row'], 3)
        self.assertIn('description source row 3', second['notes'])
        self.assertIn('commodity_code=SOURCE-2', second['notes'])
        self.assertNotIn('me_number=', second['notes'])
        self.assertEqual(valve['component_type'], 'valve')
        self.assertEqual(valve['material_standard'], '')
        self.assertEqual(valve['end_connection'], '')
        cleaned, _ = process_extracted_classes(result['piping_classes'])
        self.assertEqual(cleaned[0]['components'], cls['components'])

    def test_explicit_blank_name_does_not_inherit_previous_component(self):
        result = parse_pages([{'blank_name': True}])
        self.assertEqual(result['local_report']['components'], 2)
        self.assertEqual(result['local_report']['issues'][0]['reason'], 'lng_component_or_size_requires_review')

    def test_me_numbers_remain_distinct_from_commodity_codes(self):
        row = parse_pages([{'commodity': False}])['piping_classes'][0]['components'][0]
        self.assertIn('me_number=SOURCE-1', row['notes'])
        self.assertNotIn('commodity_code=', row['notes'])

    def test_unknown_category_keeps_literal_row_for_review_without_guessing(self):
        result = parse_pages([{'last_name': 'SPECIAL ITEM'}])
        self.assertEqual(result['local_report']['components'], 3)
        self.assertEqual(result['piping_classes'][0]['components'][-1]['component_type'], 'other')
        self.assertIn('lng_component_classification_requires_review',
                      [issue['reason'] for issue in result['local_report']['issues']])

    def test_continuation_uses_verified_class_even_when_chunk_starts_mid_class(self):
        result = parse_pages([{}, {'code': None, 'logical': 11}], start=1)
        self.assertEqual(result['local_report']['components'], 3)
        cls = result['piping_classes'][0]
        self.assertEqual(cls['class_code'], 'A')
        self.assertEqual(cls['_source_pages'], [1, 2])
        self.assertEqual(cls['components'][0]['_source_reference']['class_heading_page'], 1)
        self.assertIn('Source page 2', cls['components'][0]['notes'])

    def test_context_cannot_cross_notes_page_or_logical_sequence_break(self):
        cases = [([{}, {'table': False, 'code': None, 'logical': 11},
                    {'code': None, 'logical': 12}], 2),
                 ([{}, {'code': None, 'logical': 12}], 1),
                 ([{}, {'code': None, 'logical': 11, 'total': 21}], 1)]
        for pages, start in cases:
            with self.subTest(pages=pages):
                result = parse_pages(pages, start=start)
                self.assertEqual(result['local_report']['components'], 0)
                self.assertEqual(result['local_report']['issues'][0]['reason'], 'lng_class_context_unverified')

    def test_new_explicit_class_replaces_previous_context(self):
        result = parse_pages([{}, {'code': 'B', 'logical': 11}])
        self.assertEqual([cls['class_code'] for cls in result['piping_classes']], ['A', 'B'])
        self.assertEqual([len(cls['components']) for cls in result['piping_classes']], [3, 3])

    def test_malformed_size_is_retained_as_review_issue(self):
        result = parse_pages([{'invalid_size': True}])
        self.assertEqual(result['local_report']['components'], 2)
        self.assertTrue(result['local_report']['issues'])

    def test_missing_required_header_never_shifts_source_columns(self):
        result = parse_pages([{'broken_header': True}])
        self.assertEqual(result['local_report']['components'], 0)
        self.assertEqual(result['local_report']['issues'][0]['reason'], 'lng_grid_header_unreadable')

    def test_ambiguous_class_headings_cannot_select_a_class(self):
        result = parse_pages([{'code': 'A\nPIPING SPEC: B'}])
        self.assertEqual(result['local_report']['components'], 0)
        self.assertEqual(result['local_report']['issues'][0]['reason'], 'lng_class_context_unverified')

    def test_invalid_explicit_heading_blocks_previous_class_and_following_pages(self):
        for code in ('29 PS0A', 'A' * 33, 'B (UNSUPPORTED)', 'A\nPIPING SPEC: 29 PS0A'):
            with self.subTest(code=code):
                result = parse_pages([{}, {'code': code, 'logical': 11}, {'code': None, 'logical': 12}])
                self.assertEqual(result['local_report']['components'], 3)
                self.assertEqual([cls['class_code'] for cls in result['piping_classes']], ['A'])
                self.assertEqual([issue['reason'] for issue in result['local_report']['issues']],
                                 ['lng_class_context_unverified', 'lng_class_context_unverified'])

    def test_note_qualified_heading_keeps_explicit_code_and_flags_note(self):
        result = parse_pages([{'code': 'PC5 (NOTE-1)'}])
        self.assertEqual(result['piping_classes'][0]['class_code'], 'PC5')
        self.assertIn('(NOTE-1)', result['piping_classes'][0]['class_full_code'])
        self.assertIn('lng_class_heading_note_requires_review', [issue['reason'] for issue in result['local_report']['issues']])

    def test_continuation_lookup_respects_its_configured_bound(self):
        result = parse_pages([{}, {'code': None, 'logical': 11}, {'code': None, 'logical': 12}], start=2,
                             config={**SPEC_EXTRACTION_CONFIG, 'lng_context_lookback_pages': 1})
        self.assertEqual(result['local_report']['components'], 0)
        self.assertEqual(result['local_report']['issues'][0]['reason'], 'lng_class_context_unverified')

    def test_literal_inch_notations_and_open_bounds_do_not_expand_sizes(self):
        for value in ('1/4”', '2“', '1 ½”', '1½”', '1 / 2″', '1 1/2"'):
            self.assertEqual(parse_size(value), (value, value))
        self.assertEqual(size_bounds('1½” & BELOW', ''), ('', '1½”'))
        self.assertEqual(size_bounds('2” AND ABOVE', ''), ('2”', ''))
        self.assertEqual(size_bounds('1½” & BELOW', '1½” & BELOW'), ('', '1½”'))
        self.assertEqual(size_bounds('3”', ''), ('3”', ''))
        self.assertIsNone(size_bounds('unclear', '2”'))

    def test_union_keeps_separate_source_rows_and_removes_overlap_copies(self):
        cls = parse_pages([{}])['piping_classes'][0]
        second_page = copy.deepcopy(cls)
        for row in second_page['components']:
            row['_source_reference']['page'] = 2
        result = PaperSpecExtractionService.merge_classes([[cls], [copy.deepcopy(cls)], [second_page]])
        self.assertEqual(len(result[0]['components']), 6)


class LNGPersistenceTests(TestCase):
    def test_source_rows_survive_actual_database_persistence(self):
        from .models import PaperSpecDocument, PaperSpecExtractionJob
        from .tasks import _persist_classes
        result = parse_pages([{}])
        document = PaperSpecDocument.objects.create(original_filename='synthetic-lng.pdf', sha256_hash='e' * 64)
        job = PaperSpecExtractionJob.objects.create(document=document)
        self.assertEqual(_persist_classes(job, result['piping_classes']), 1)
        cls = job.piping_classes.get()
        rows = list(cls.components.order_by('display_order'))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1].schedule_or_rating, '')
        self.assertIn('description source row 3', rows[1].notes)
        self.assertIn('class heading page 1', rows[1].notes)
        self.assertEqual(rows[0].material_standard, '')
