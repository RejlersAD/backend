"""Real, synthetic PDF fixtures for register cell boundaries and scope marks."""
import io
import unittest
from unittest.mock import patch

import pdfplumber
from reportlab.pdfgen.canvas import Canvas

from apps.planning_intelligence.services.pdf_register_geometry import (
    PdfRegisterGeometryExtractor,
    extract_pdf_register_rows,
)


def make_register_pdf(rows, *, caption=True, negative_legend=False, continuation=False, next_annex=False, shared_remarks=False):
    stream = io.BytesIO()
    canvas = Canvas(stream, pagesize=(640, 800))
    boundaries = [40, 85, 150, 350, 380, 410, 440, 600]

    def text(x, y, value):
        canvas.setFont('Helvetica', 8)
        canvas.drawString(x, 800 - y, value)

    def line(x0, top, x1, bottom):
        canvas.line(x0, 800 - top, x1, 800 - bottom)

    def page(items, header, second=False):
        if second and next_annex:
            text(40, 35, 'ANNEXURE 2: Reference documents')
        if header:
            if caption:
                text(40, 35, 'Table 2: Applicable Deliverables for Work Packages')
            if negative_legend:
                text(40, 52, negative_legend if isinstance(negative_legend, str) else 'X = not required')
            for x in (40, 85, 150, 350, 440, 600):
                line(x, 70, x, 110)
            for y in (70, 110):
                line(40, y, 600, y)
            line(350, 90, 440, 90)
            for x in (380, 410):
                line(x, 90, x, 110)
            text(45, 94, 'S. NO.')
            text(91, 94, 'Discipline')
            text(159, 94, 'Deliverable')
            text(351, 84, 'Work Packages')
            for index, x in enumerate((362, 392, 422), 1):
                text(x, 104, str(index))
            text(450, 94, 'Remarks')
            top = 110
        else:
            top = 70
        for index, row in enumerate(items):
            bottom = top + 48
            merged = row.get('merged', False)
            for x in boundaries:
                if not (merged and x in (380, 410)) and not (row.get('section_heading') and x == 150):
                    line(x, top, x, bottom)
            # Continuation has an open top, matching real PDFs which cause
            # generic find_tables() to omit their first continuation row.
            if not (second and index == 0):
                line(40, top, 440 if shared_remarks and index > 0 else 600, top)
            line(40, bottom, 440 if shared_remarks and index < len(items) - 1 else 600, bottom)
            text(44, top + 26, row['code'])
            text(90, top + 26, row.get('discipline', 'Process'))
            for n, value in enumerate(row['title'].split('\n')):
                text(154, top + (26 if row.get('title_at_center') else 16) + n * 11, value)
            for mark, x in row.get('marks', [('X', 360)]):
                text(x, top + 26, mark)
            for n, value in enumerate(row.get('remarks', '').split('\n')):
                text(445, top + 16 + n * 11, value)
            top = bottom
        text(40, 780, 'Annexure-1 List of Engineering Deliverables.docx 1 / 2')
        canvas.showPage()

    page(rows[:1] if continuation else rows, True)
    if continuation:
        page(rows[1:], False, True)
    canvas.save()
    stream.seek(0)
    return stream


def make_landscape_cooling_register(*, caption='Table 9: Applicable Deliverables for Cooling Network', title_header='Document title'):
    """Independent landscape contract fixture with two named work packages."""
    stream = io.BytesIO()
    canvas = Canvas(stream, pagesize=(900, 600))

    def text(x, top, value):
        canvas.setFont('Helvetica', 10)
        canvas.drawString(x, 600 - top, value)

    def line(x0, top, x1, bottom):
        canvas.line(x0, 600 - top, x1, 600 - bottom)

    text(80, 40, caption)
    for x in (80, 140, 250, 530, 710, 830):
        line(x, 90, x, 200)
    line(620, 115, 620, 200)
    for top in (90, 140, 200):
        line(80, top, 830, top)
    line(530, 115, 710, 115)
    text(87, 124, 'Item No.')
    text(150, 124, 'Discipline')
    text(260, 124, title_header)
    text(549, 107, 'Work Packages')
    text(552, 133, 'WP 10')
    text(646, 133, 'WP 20')
    text(730, 124, 'Comments')
    text(87, 175, 'ME-014')
    text(150, 175, 'Mechanical')
    text(260, 164, 'Cooling Water Equipment Specification')
    text(260, 181, 'and Operating Envelope')
    text(650, 175, 'X')
    text(720, 175, 'New issue')
    canvas.showPage()
    canvas.save()
    stream.seek(0)
    return stream


class PdfRegisterGeometryTests(unittest.TestCase):
    def test_wrapped_title_stays_in_title_column_and_shared_mark_stays_shared(self):
        stream = make_register_pdf([{
            'code': '3.1.8', 'title': 'Reliability Availability and\nMaintenance (RAM) Study Report',
            'remarks': 'Common report for all packages', 'merged': True,
        }])
        rows = extract_pdf_register_rows(stream)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['name'], 'Reliability Availability and Maintenance (RAM) Study Report')
        self.assertEqual(rows[0]['discipline_label'], 'Process')
        self.assertEqual(rows[0]['source_remarks'], 'Common report for all packages')
        self.assertEqual(rows[0]['applicability_status'], 'marked')
        self.assertEqual(len(rows[0]['applicability_cells']), 1)
        self.assertEqual(rows[0]['applicability_cells'][0]['package_labels'], ['1', '2', '3'])
        self.assertTrue(rows[0]['applicability_cells'][0]['merged'])
        self.assertEqual(rows[0]['source_locator']['page'], 1)
        self.assertEqual(len(rows[0]['source_locator']['columns']['title']), 4)
        self.assertIn('\n', rows[0]['literal_cells']['title'])

    def test_individual_marks_are_mapped_to_their_actual_columns(self):
        stream = make_register_pdf([{'code': '3.2.1', 'title': 'Design Report', 'marks': [('X', 360), ('x', 420)]}])
        row = extract_pdf_register_rows(stream)[0]
        self.assertEqual([cell['package_labels'] for cell in row['applicability_cells']], [['1'], ['3']])
        self.assertFalse(any(cell['merged'] for cell in row['applicability_cells']))

    def test_unmarked_inventory_is_not_promoted_to_required_scope(self):
        row = extract_pdf_register_rows(make_register_pdf([{'code': '3.2.2', 'title': 'Optional Report', 'marks': []}]))[0]
        self.assertEqual(row['applicability_status'], 'not_marked')
        self.assertFalse(row['applicability_marked'])

    def test_conditions_bundling_and_explicit_exclusions_remain_reviewable(self):
        rows = extract_pdf_register_rows(make_register_pdf([
            {'code': '3.1.1', 'title': 'Conditional Report', 'remarks': 'Only if equipment changes'},
            {'code': '3.1.2', 'title': 'Emergency Response Plan', 'remarks': 'No separate deliverable'},
            {'code': '3.1.3', 'title': 'Obsolete Report', 'remarks': 'Not required'},
        ]))
        self.assertEqual([row['applicability_status'] for row in rows], ['conditional', 'bundled', 'not_required'])
        self.assertTrue(all(row['applicability_marked'] for row in rows))

    def test_scope_inclusion_is_not_mistaken_for_bundling_with_another_document(self):
        for remarks in ('Included in FEED scope', 'Covered by contract', 'Included within engineering services'):
            with self.subTest(remarks=remarks):
                row = extract_pdf_register_rows(make_register_pdf([{
                    'code': '1', 'title': 'Valve Specification', 'remarks': remarks,
                }]))[0]
                self.assertEqual(row['applicability_status'], 'marked')
        for remarks in ('Included in safety report', 'Covered by the Design Basis Report', 'No separate document'):
            with self.subTest(remarks=remarks):
                row = extract_pdf_register_rows(make_register_pdf([{
                    'code': '1', 'title': 'Valve Specification', 'remarks': remarks,
                }]))[0]
                self.assertEqual(row['applicability_status'], 'bundled')

    def test_host_report_and_negative_inclusion_are_not_excluded_as_bundled(self):
        for remarks in ('3.1.1 and 3.1.2 shall be part of this report', 'Not included in safety report'):
            with self.subTest(remarks=remarks):
                row = extract_pdf_register_rows(make_register_pdf([{
                    'code': '7.3', 'title': 'Materials Study Report', 'remarks': remarks,
                }]))[0]
                self.assertEqual(row['applicability_status'], 'marked')

    def test_explicit_numbered_register_reference_remains_bundled(self):
        for remarks in ('covered in 8.2.14', 'Covered in Sr. No. 8.2.14'):
            with self.subTest(remarks=remarks):
                row = extract_pdf_register_rows(make_register_pdf([{
                    'code': '9.2', 'title': 'Noise Hazard Area Layout', 'remarks': remarks,
                }]))[0]
                self.assertEqual(row['applicability_status'], 'bundled')

    def test_negative_x_legend_is_not_interpreted_as_positive_applicability(self):
        rows = extract_pdf_register_rows(make_register_pdf([{'code': '3.1.1', 'title': 'Report'}], negative_legend=True))
        self.assertEqual(rows[0]['applicability_status'], 'not_required')
        self.assertEqual(rows[0]['source_locator']['mark_meaning'], 'not_required')

    def test_negative_legend_dash_variants_do_not_turn_exclusions_into_applicable_scope(self):
        for legend in ('X - Not required', 'X \u2013 Not applicable', 'X \u2014 Excluded', 'X: Not required', 'X denotes not applicable'):
            with self.subTest(legend=legend):
                rows = extract_pdf_register_rows(make_register_pdf([
                    {'code': '8.1', 'title': 'Cooling Water Load Study'},
                ], negative_legend=legend))
                self.assertEqual(rows[0]['applicability_status'], 'not_required')

    def test_vertically_merged_remarks_apply_to_the_explicit_shared_row_group(self):
        rows = extract_pdf_register_rows(make_register_pdf([
            {'code': '3.1.1', 'title': 'Report One'},
            {'code': '3.1.2', 'title': 'Report Two', 'remarks': 'Requirement to be assessed'},
        ], shared_remarks=True))
        self.assertEqual([row['applicability_status'] for row in rows], ['conditional', 'conditional'])
        self.assertTrue(all(row['source_remarks'] == 'Requirement to be assessed' for row in rows))
        self.assertEqual(rows[0]['source_locator']['shared_cells']['remarks']['register_items'], ['3.1.1', '3.1.2'])

    def test_geometry_without_an_explicit_register_caption_is_not_a_register(self):
        self.assertEqual(extract_pdf_register_rows(make_register_pdf([{'code': '3.1.1', 'title': 'Report'}], caption=False)), [])

    def test_continuation_recovers_first_row_even_when_top_rule_is_open(self):
        rows = extract_pdf_register_rows(make_register_pdf([
            {'code': '3.1.1', 'title': 'First Report'}, {'code': '3.1.2', 'title': 'Second\nReport'},
        ], continuation=True))
        self.assertEqual([row['name'] for row in rows], ['First Report', 'Second Report'])
        self.assertEqual([row['source_locator']['page'] for row in rows], [1, 2])

    def test_new_annexure_cannot_inherit_previous_table_semantics(self):
        rows = extract_pdf_register_rows(make_register_pdf([
            {'code': '3.1.1', 'title': 'First Report'}, {'code': '3.1.2', 'title': 'Reference Only'},
        ], continuation=True, next_annex=True))
        self.assertEqual([row['name'] for row in rows], ['First Report'])

    def test_page_number_above_new_annexure_cannot_hide_the_physical_section_boundary(self):
        class NumberedCanvas(Canvas):
            def drawString(self, x, y, text, *args, **kwargs):
                if text.startswith('ANNEXURE 2:'):
                    super().drawString(40, 780, '2')
                return super().drawString(x, y, text, *args, **kwargs)

        with patch(__name__ + '.Canvas', NumberedCanvas):
            stream = make_register_pdf([
                {'code': '7.4.1', 'title': 'Cooling Water Design Criteria'},
                {'code': '7.4.2', 'title': 'Reference Standard Only'},
            ], continuation=True, next_annex=True)
        rows = extract_pdf_register_rows(stream)
        self.assertEqual([row['register_item'] for row in rows], ['7.4.1'])

    def test_stream_position_and_original_flattened_source_are_preserved(self):
        stream = make_register_pdf([{'code': '3.1.1', 'title': 'Wrapped\nReport'}])
        with pdfplumber.open(stream) as pdf:
            source_text = '\f'.join(page.extract_text() for page in pdf.pages)
        stream.seek(9)
        rows = extract_pdf_register_rows(stream, extracted_text=source_text)
        self.assertEqual(stream.tell(), 9)
        row = rows[0]
        self.assertIn('3.1.1', source_text[row['start']:row['end']])
        self.assertEqual(row['source_locator']['raw_text_start'], row['start'])
        self.assertEqual(row['source_line'], source_text[:row['start']].count('\n') + 1)
        self.assertEqual(row['source_text_excerpt'], source_text[row['start']:row['end']])
        self.assertEqual(row['source_locator']['quote'], row['source_text_excerpt'])
        for reference in row['source_locator']['text_ranges']:
            self.assertEqual(reference['quote'], source_text[reference['character_start']:reference['character_end']])

    def test_skipping_a_page_clears_continuation_context(self):
        stream = make_register_pdf([
            {'code': '3.1.1', 'title': 'First Report'}, {'code': '3.1.2', 'title': 'Unrelated Report'},
        ], continuation=True)
        with pdfplumber.open(stream) as pdf:
            extractor = PdfRegisterGeometryExtractor()
            self.assertEqual(len(extractor.extract_page(pdf.pages[0], 1)), 1)
            self.assertEqual(extractor.extract_page(pdf.pages[1], 3), [])

    def test_page_limit_fails_explicitly_without_returning_truncated_inventory(self):
        stream = make_register_pdf([
            {'code': '3.1.1', 'title': 'First Report'}, {'code': '3.1.2', 'title': 'Second Report'},
        ], continuation=True)
        with patch('apps.planning_intelligence.services.pdf_register_geometry.MAX_PAGES', 1):
            with self.assertRaisesRegex(ValueError, 'page_limit'):
                extract_pdf_register_rows(stream)
        self.assertEqual(stream.tell(), 0)

    def test_row_limit_fails_explicitly_without_returning_truncated_inventory(self):
        stream = make_register_pdf([
            {'code': '3.1.1', 'title': 'First Report'}, {'code': '3.1.2', 'title': 'Second Report'},
        ])
        with patch('apps.planning_intelligence.services.pdf_register_geometry.MAX_ROWS', 1):
            with self.assertRaisesRegex(ValueError, 'row_limit'):
                extract_pdf_register_rows(stream)

    def test_mismatched_text_page_count_cannot_produce_false_source_offsets(self):
        stream = make_register_pdf([
            {'code': '3.1.1', 'title': 'First Report'}, {'code': '3.1.2', 'title': 'Second Report'},
        ], continuation=True)
        with self.assertRaisesRegex(ValueError, 'text_page_mismatch'):
            extract_pdf_register_rows(stream, extracted_text='Only one unpaginated page')

    def test_simple_dotted_and_alphanumeric_ids_work_only_in_verified_serial_cells(self):
        for code in ('1', '27', '2.1', '7.4.2', 'A-014', 'ME-DR-032', 'HVAC-12A'):
            with self.subTest(code=code):
                rows = extract_pdf_register_rows(make_register_pdf([{
                    'code': code, 'title': 'Cooling Water Hydraulic Study', 'discipline': 'Mechanical',
                }]))
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['register_item'], code)
                self.assertEqual(rows[0]['name'], 'Cooling Water Hydraulic Study')

    def test_merged_discipline_heading_is_not_mistaken_for_a_two_level_deliverable(self):
        rows = extract_pdf_register_rows(make_register_pdf([
            {'code': '2.1', 'title': '', 'discipline': 'Mechanical (Rotating Equipment)', 'marks': [], 'section_heading': True},
            {'code': '2.1.1', 'title': 'Cooling Pump Datasheet'},
        ]))
        self.assertEqual([row['register_item'] for row in rows], ['2.1.1'])

    def test_independent_landscape_document_uses_detected_columns_and_package_labels(self):
        rows = extract_pdf_register_rows(make_landscape_cooling_register())
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['register_item'], 'ME-014')
        self.assertEqual(row['name'], 'Cooling Water Equipment Specification and Operating Envelope')
        self.assertEqual(row['discipline_label'], 'Mechanical')
        self.assertEqual(row['applicability_cells'][0]['package_labels'], ['WP 20'])
        self.assertEqual(row['source_remarks'], 'New issue')

    def test_reference_caption_does_not_turn_an_unrelated_landscape_table_into_scope(self):
        self.assertEqual(extract_pdf_register_rows(make_landscape_cooling_register(caption='Table 9: Reference standards')), [])

    def test_applicability_caption_without_a_deliverable_header_is_rejected(self):
        self.assertEqual(extract_pdf_register_rows(make_landscape_cooling_register(title_header='Reference standard')), [])

    def test_simple_ids_do_not_bypass_caption_verification(self):
        self.assertEqual(extract_pdf_register_rows(make_register_pdf([
            {'code': '1', 'title': 'Ordinary Numbered Requirement'},
        ], caption=False)), [])

    def test_repeated_ids_use_unique_literal_title_evidence_instead_of_first_item_line(self):
        stream = make_register_pdf([
            {'code': '1', 'title': 'Cooling Water Design Criteria'},
            {'code': '1', 'title': 'Circulation Pump Specification'},
        ])
        with pdfplumber.open(stream) as pdf:
            source_text = '\f'.join(page.extract_text() for page in pdf.pages)
        rows = extract_pdf_register_rows(stream, extracted_text=source_text)
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]['source_locator']['bbox'], rows[1]['source_locator']['bbox'])
        self.assertNotEqual(rows[0]['start'], rows[1]['start'])
        for row in rows:
            self.assertEqual(source_text[row['start']:row['end']], row['name'])
            self.assertEqual(row['source_text_excerpt'], row['name'])
            self.assertEqual(row['source_locator']['text_row_status'], 'unique_title_cell')
            title_ranges = [part for part in row['source_locator']['text_ranges'] if part['field'] == 'title']
            self.assertEqual(len(title_ranges), 1)
            self.assertEqual(title_ranges[0]['quote'], row['name'])
            self.assertEqual(source_text[title_ranges[0]['character_start']:title_ranges[0]['character_end']], row['name'])

    def test_repeated_ids_and_titles_preserve_ambiguity_without_false_offsets(self):
        stream = make_register_pdf([
            {'code': '1', 'title': 'Cooling Water Design Criteria'},
            {'code': '1', 'title': 'Cooling Water Design Criteria'},
        ])
        with pdfplumber.open(stream) as pdf:
            source_text = '\f'.join(page.extract_text() for page in pdf.pages)
        rows = extract_pdf_register_rows(stream, extracted_text=source_text)
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertIsNone(row['start'])
            self.assertIsNone(row['end'])
            self.assertNotIn('source_text_excerpt', row)
            self.assertNotIn('quote', row['source_locator'])
            self.assertEqual(row['source_locator']['text_row_status'], 'ambiguous_repeated_item')

    def test_repeated_ids_can_use_the_matching_item_line_when_it_contains_unique_title(self):
        stream = make_register_pdf([
            {'code': '12', 'title': 'Cooling Water Design Criteria', 'title_at_center': True},
            {'code': '12', 'title': 'Circulation Pump Specification', 'title_at_center': True},
        ])
        with pdfplumber.open(stream) as pdf:
            source_text = '\f'.join(page.extract_text() for page in pdf.pages)
        rows = extract_pdf_register_rows(stream, extracted_text=source_text)
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]['start'], rows[1]['start'])
        for row in rows:
            self.assertIn(row['name'], source_text[row['start']:row['end']])
            self.assertEqual(row['source_locator']['text_row_status'], 'unique_title_in_item_line')


if __name__ == '__main__':
    unittest.main()
