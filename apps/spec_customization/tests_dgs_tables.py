"""Synthetic DGS layout regressions; not independent engineering labels."""
from pathlib import Path
import tempfile

from django.test import SimpleTestCase

from .services.config import SPEC_EXTRACTION_CONFIG
from .services.local_tables import LocalTableParser, positioned_rows
from .services.local_extraction import run_local_engine
from .services.extraction_service import PaperSpecExtractionService
from .services.data_quality import process_extracted_classes


def fixture_words(code='11176', secondary_size=False, missing_header=False):
    words = []

    def line(y, values):
        for x, text in values:
            words.append((x, y, x + len(text) * 4, y + 8, text))

    line(30, [(260, 'DGS-1300-010')])
    line(60, [(100, code), (210, 'APPENDIX II – DETAILED PIPING CLASSES')])
    line(100, [(45, 'Short'), (95, 'Size'), (136, 'Rating'), (169, 'End'),
               (198, 'Schedule'), (323, 'Description'), (467, 'Stock'),
               (489, 'code'), (528, 'Notes'), (558, 'Rev')])
    line(110, [(45, 'Code'), (78, 'From'), (112, 'To')] + ([] if missing_header else [(197, 'Thickness')]))
    line(130, [(44, 'PIP'), (83, '0.5'), (113, '1.5'), (171, 'PE'),
               (200, '160'), (243, 'SEAMLESS PIPE, A333 GR.6'), (533, '08')])
    line(140, [(243, 'IMPACT TESTED')])
    if secondary_size:
        line(150, [(83, '2'), (113, '3')])
    line(170, [(83, '2'), (113, '6'), (141, '150'), (171, 'RF'),
               (243, 'FLOATING TYPE BALL VALVE, A351 CF8M')])
    line(180, [(243, 'FULL PORT, FIRE SAFE'), (533, '22')])
    line(210, [(280, 'Sheet 1 of 2')])
    return words


class DgsTableTests(SimpleTestCase):
    def parse(self, **kwargs):
        parser = LocalTableParser(SPEC_EXTRACTION_CONFIG['piping_class_header_regex'], 'pymupdf_text')
        parser.read_rows(positioned_rows(fixture_words(**kwargs)), 7)
        return parser

    def test_split_headers_multiline_rows_and_blank_short_codes(self):
        parser = self.parse()
        cls = parser.result()['piping_classes'][0]
        self.assertEqual(cls['class_code'], '11176')
        pipe, valve = cls['components']
        self.assertEqual(pipe['description'], 'SEAMLESS PIPE, A333 GR.6 IMPACT TESTED')
        self.assertEqual((pipe['size_from'], pipe['size_to']), ('0.5', '1.5'))
        self.assertEqual(pipe['schedule_or_rating'], '160')
        self.assertEqual(pipe['end_connection'], 'PE')
        self.assertEqual(valve['component_type'], 'valve')
        self.assertEqual(valve['schedule_or_rating'], '150')
        self.assertIn('FULL PORT, FIRE SAFE', valve['description'])
        self.assertIn('notes=22', valve['notes'])
        self.assertEqual(pipe['material_standard'], '')
        self.assertIn('Source page 7, row', pipe['notes'])
        self.assertEqual(parser.issues, [])
        cleaned, _ = process_extracted_classes([cls])
        self.assertEqual(cleaned[0]['components'], [pipe, valve])

    def test_multiple_size_lines_are_rejected_with_review_issue(self):
        parser = self.parse(secondary_size=True)
        self.assertEqual(parser.component_count, 1)
        self.assertEqual(parser.issues[0]['reason'], 'dgs_ambiguous_component_or_size')

    def test_missing_or_ambiguous_class_never_uses_previous_page(self):
        for code in ('', '11176 11376'):
            parser = self.parse(code=code)
            self.assertEqual(parser.component_count, 0)
            self.assertEqual(parser.issues[0]['reason'], 'ambiguous_dgs_class')

    def test_incomplete_header_is_reported(self):
        parser = self.parse(missing_header=True)
        self.assertEqual(parser.component_count, 0)
        self.assertEqual(parser.issues[0]['reason'], 'dgs_size_header_unreadable')

    def test_source_rows_survive_union_but_overlapping_chunk_copies_do_not_duplicate(self):
        import copy
        cls = self.parse().result()['piping_classes'][0]
        different_row = copy.deepcopy(cls)
        different_row['components'][0]['_source_reference']['row'] += 100
        merged = PaperSpecExtractionService.merge_classes([[copy.deepcopy(cls)], [copy.deepcopy(cls)], [different_row]])
        self.assertEqual(len(merged[0]['components']), 3)

    def test_native_pdf_dispatch_reaches_dgs_parser_in_bounded_child(self):
        import fitz
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'dgs-fixture.pdf'
            with fitz.open() as doc:
                page = doc.new_page()
                for x, y, _, _, value in fixture_words():
                    page.insert_text((x, y + 8), value.replace('–', '-'), fontsize=7)
                doc.save(path)
            result = run_local_engine(path, 0, 0, 'pymupdf_text', SPEC_EXTRACTION_CONFIG, 30)
        self.assertEqual(result['local_report']['components'], 2)
        self.assertEqual(result['local_report']['issues'], [])

    def test_image_only_page_is_flagged_but_blank_page_is_not(self):
        import fitz
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'image-fixture.pdf'
            with fitz.open() as doc:
                page = doc.new_page()
                image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 4, 4), False)
                image.clear_with(255)
                page.insert_image(fitz.Rect(20, 20, 200, 200), pixmap=image)
                doc.new_page()
                doc.save(path)
            result = run_local_engine(path, 0, 1, 'pymupdf_text', SPEC_EXTRACTION_CONFIG, 30)
        self.assertEqual(result['local_report']['issues'], [{'page': 1, 'reason': 'image_page_requires_ocr'}])
