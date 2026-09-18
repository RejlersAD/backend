"""Source registers retain their own titles and rows, independent of catalogues."""
import io
from unittest import TestCase

from openpyxl import Workbook

from ..services.register_rows import (
    extract_legacy_register_rows, extract_register_rows, extract_workbook_register_rows,
    normalize_register_discipline,
)


class RegisterRowExtractionTests(TestCase):
    def workbook(self, rows, sheet_name='MDR'):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = sheet_name
        for row in rows:
            sheet.append(row)
        stream = io.BytesIO()
        workbook.save(stream)
        stream.seek(0)
        self.addCleanup(stream.close)
        return stream

    def test_workbook_preserves_original_titles_discipline_and_true_row(self):
        stream = self.workbook([
            [], [], [None, 'SL. NO.', 'DISCIPLINE', 'DRAWING / DOCUMENT (TITLE)'],
            [None, None, 'GENERAL', None],
            [None, 1, 'GENERAL', 'MASTER DELIVERABLE REGISTER'],
            [None, None, 'HVAC', None],
            [None, 219, 'HVAC', 'HVAC ADEQUEACY REPORT -FAR-0'],
            [None, 220, 'HVAC', 'HVAC ADEQUEACY REPORT -FAR-6'],
        ], 'Sheet1')

        rows = extract_workbook_register_rows(stream)

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1]['name'], 'HVAC ADEQUEACY REPORT -FAR-0')
        self.assertEqual(rows[1]['original_title'], rows[1]['name'])
        self.assertEqual(rows[1]['discipline'], 'hvac')
        self.assertEqual(rows[1]['discipline_label'], 'HVAC')
        self.assertEqual(rows[1]['register_item'], 219)
        self.assertEqual(rows[1]['source_locator'], {'sheet': 'Sheet1', 'row': 7})
        self.assertEqual(stream.tell(), 0)

    def test_legacy_flattened_excel_is_not_labelled_as_worksheet_rows(self):
        text = (
            '--- Sheet: Sheet1 ---\nSL. NO. | DISCIPLINE | DRAWING / DOCUMENT (TITLE)\n'
            'GENERAL\n1 | GENERAL | MASTER DELIVERABLE REGISTER\n'
            'HVAC\n219 | HVAC | HVAC ADEQUEACY REPORT -FAR-0\n'
        )
        rows = extract_register_rows(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]['source_locator'], {'sheet': 'Sheet1', 'line': 6})
        self.assertIsNone(rows[1]['row_number'])
        self.assertEqual(text[rows[1]['start']:rows[1]['end']].strip(), rows[1]['source_excerpt'])

    def test_220_row_register_roundtrips_all_six_groups_without_catalogue_names(self):
        # Match the observed workbook layout and size using generated test data;
        # no project documents or their confidential contents enter the fixture.
        from ..services.parsers import _extract_xlsx

        contents = [[], [], [None, 'SL. NO.', 'DISCIPLINE', 'DRAWING / DOCUMENT (TITLE)']]
        expected = []
        for label, count in (
            ('GENERAL', 34), ('HSE', 81), ('INSTRUMENTATION', 41),
            ('ELECTRICAL', 24), ('CIVIL', 38), ('HVAC', 2),
        ):
            contents.append([None, None, label, None])
            for index in range(count):
                serial = len(expected) + 1
                title = f'{label} Deliverable {index + 1} (+/- 15%) -FAR-{serial % 2}'
                expected.append((serial, label, title))
                contents.append([None, serial, label, title])
        stream = self.workbook(contents, 'Sheet1')

        workbook_rows = extract_workbook_register_rows(stream)
        text_rows = extract_register_rows(_extract_xlsx(stream))

        for rows in (workbook_rows, text_rows):
            self.assertEqual(len(rows), 220)
            self.assertEqual(
                [(row['register_item'], row['discipline_label'], row['name']) for row in rows],
                expected,
            )
        self.assertEqual(workbook_rows[-1]['row_number'], 229)

    def test_quoted_csv_titles_keep_commas_quotes_and_newlines(self):
        rows = extract_register_rows(
            'Serial No,Discipline,Document Title,Document No,Revision\n'
            '1,CIVIL,"FOUNDATION, \"\"A\"\"\nREPORT",CV-001,02\n'
            '2,CIVIL,"FOUNDATION, B",CV-002,01\n'
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['name'], 'FOUNDATION, "A"\nREPORT')
        self.assertEqual(rows[0]['document_number'], 'CV-001')
        self.assertEqual(rows[0]['document_revision'], '02')
        self.assertEqual(rows[1]['source_line'], 4)

    def test_tsv_with_empty_columns_does_not_shift_titles(self):
        rows = extract_register_rows(
            '\tDocument No\tDepartment\tDocument Title\tRev\n'
            '\t\tElectrical\tLOAD LIST - FAR-0\t\n'
        )
        self.assertEqual(rows[0]['name'], 'LOAD LIST - FAR-0')
        self.assertEqual(rows[0]['document_number'], '')
        self.assertEqual(rows[0]['discipline'], 'electrical')

    def test_excel_parse_preserves_blank_optional_columns_and_quoted_title_content(self):
        from ..services.parsers import _extract_xlsx

        title = 'FOUNDATION | "SPECIAL"\nDRAWING'
        stream = self.workbook([
            [None, 'SL. NO.', 'DISCIPLINE', 'DOCUMENT NO', 'DOCUMENT TITLE', 'REV'],
            [None, 1, 'CIVIL', None, title, None],
            [None, 2, 'CIVIL', 'CV-002', 'DETAIL DRAWING', 'A'],
        ])
        text = _extract_xlsx(stream)
        rows = extract_register_rows(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['name'], title)
        self.assertEqual(rows[0]['document_number'], '')
        self.assertEqual(rows[0]['document_revision'], '')
        self.assertEqual(rows[1]['name'], 'DETAIL DRAWING')
        self.assertEqual(rows[1]['document_number'], 'CV-002')
        self.assertEqual(rows[1]['document_revision'], 'A')

    def test_excel_rows_and_legacy_pdf_rows_can_form_one_exact_register(self):
        spreadsheet = extract_register_rows(
            'SL. NO. | DISCIPLINE | DOCUMENT TITLE\n1 | GENERAL | PROJECT PLAN\n'
        )
        pdf = extract_legacy_register_rows(
            '1 CIVIL ABC-CV-001 Foundation drawing North Island NEW 1 B\n'
            '2 HVAC ABC-HV-002 Ventilation layout North Island EXISTING 2 C\n'
        )
        self.assertEqual([row['name'] for row in spreadsheet + pdf], [
            'PROJECT PLAN', 'Foundation drawing', 'Ventilation layout',
        ])
        self.assertEqual(pdf[1]['discipline'], 'hvac')
        self.assertEqual(pdf[1]['document_number'], 'ABC-HV-002')
        self.assertEqual(pdf[1]['document_revision'], 'C')
        self.assertEqual(pdf[1]['source_line'], 2)
        self.assertIn('North Island', pdf[1]['source_excerpt'])

    def test_legacy_pdf_shared_document_title_endings_are_retained(self):
        rows = extract_legacy_register_rows(
            '1 CIVIL ABC-CV-001 Foundation Inspection Report NEW 1 B\n'
            '2 CIVIL ABC-CV-002 Concrete Inspection Report NEW 1 B\n'
        )
        self.assertEqual([row['name'] for row in rows], [
            'Foundation Inspection Report', 'Concrete Inspection Report',
        ])

    def test_missing_merged_discipline_is_carried_forward(self):
        stream = self.workbook([
            ['Serial Number', 'Discipline', 'Document Title'],
            [1, 'INSTRUMENTATION', 'SYSTEM ARCHITECTURE DIAGRAM FOR FAR 0'],
            [2, None, 'HSSD ADEQUACY REPORT-FAR0'],
        ])
        rows = extract_workbook_register_rows(stream)
        self.assertEqual([row['discipline'] for row in rows], ['instrumentation'] * 2)
        legacy = extract_register_rows(
            'Serial Number | Discipline | Document Title\n'
            '1 | INSTRUMENTATION | SYSTEM ARCHITECTURE DIAGRAM FOR FAR 0\n'
            '2 | HSSD ADEQUACY REPORT-FAR0\n'
        )
        self.assertEqual(legacy[1]['discipline_label'], 'INSTRUMENTATION')
        self.assertEqual(legacy[1]['name'], 'HSSD ADEQUACY REPORT-FAR0')

    def test_repeated_headers_blanks_and_sections_are_not_deliverables(self):
        rows = extract_register_rows(
            'SL. NO. | DISCIPLINE | DRAWING / DOCUMENT (TITLE)\n'
            'HSE\n1 | HSE | HAZOP REPORT\n\n'
            'SL. NO. | DISCIPLINE | DRAWING / DOCUMENT (TITLE)\n'
            '2 | HSE | HAZOP REPORT\nTotal | HSE | 2\n'
        )
        self.assertEqual([row['register_item'] for row in rows], [1, 2])
        # Two source entries with the same title are still two deliverables.
        self.assertEqual([row['name'] for row in rows], ['HAZOP REPORT', 'HAZOP REPORT'])

    def test_each_sheet_uses_its_own_columns_and_discipline(self):
        stream = self.workbook([
            ['Serial No', 'Discipline', 'Document Title'],
            [1, 'HSE', 'HAZOP REPORT'],
        ], 'Studies')
        import openpyxl
        workbook = openpyxl.load_workbook(stream)
        other = workbook.create_sheet('Other')
        other.append(['Document No', 'Document Title'])
        other.append(['PKG-1', 'EPC PACKAGE'])
        stream.seek(0)
        workbook.save(stream)
        stream.seek(0)
        rows = extract_workbook_register_rows(stream)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]['discipline'], 'general')
        self.assertEqual(rows[1]['source_locator'], {'sheet': 'Other', 'row': 2})
        self.assertIsNone(rows[1]['register_item'])

    def test_unknown_department_name_is_preserved_without_guessing(self):
        rows = extract_register_rows(
            'Serial No;Department;Deliverable Name\n1;People & Culture;Recruitment plan\n'
        )
        self.assertEqual(rows[0]['discipline_label'], 'People & Culture')
        self.assertEqual(rows[0]['discipline'], 'people_culture')
        self.assertEqual(rows[0]['name'], 'Recruitment plan')

    def test_unstructured_prose_does_not_become_register_rows(self):
        self.assertEqual(extract_register_rows('The contractor shall prepare a material specification.'), [])
        self.assertEqual(extract_register_rows('Title | Note\nElectrical Design | Proposed'), [])

    def test_known_disciplines_are_matched_as_labels_not_substrings(self):
        self.assertEqual(normalize_register_discipline('Civil / Structural'), 'civil')
        self.assertEqual(normalize_register_discipline('Instrumentation & Control'), 'instrumentation')
        self.assertEqual(normalize_register_discipline('HVAC'), 'hvac')
        self.assertEqual(normalize_register_discipline('GENERAL'), 'general')
        self.assertEqual(normalize_register_discipline('Civilian Engagement'), 'civilian_engagement')

    def test_corrupt_workbook_restores_stream_position(self):
        stream = io.BytesIO(b'not a workbook')
        stream.seek(2)
        with self.assertRaises(Exception):
            extract_workbook_register_rows(stream)
        self.assertEqual(stream.tell(), 2)
