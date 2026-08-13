from django.test import SimpleTestCase

from apps.instrument_io_workflow.services.comment_table_extractor import _consume_rows
from apps.instrument_io_workflow.services.io_table_extractor import _rows_from_ocr_text


class CommentTableContinuationTests(SimpleTestCase):
    def test_multiline_and_next_page_fragments_merge_into_numbered_row(self):
        state = {'records': [], 'current': None, 'status_code': '2', 'page_number': 1}
        _consume_rows([
            ['S.No.', '', 'COMPANY Comments', '', 'CONTRACTOR / VENDOR Reply', '', 'COMPANY Decision'],
            ['1.', '', '', '', 'Noted and updated.', '', ''],
            ['', '', 'First line of a long review comment', '', '', '', ''],
            ['', '', 'second line', '', '', '', 'Accepted'],
        ], state)
        state['page_number'] = 2
        _consume_rows([
            ['', 'continued on the next page', '', 'Additional reply'],
            ['2.', 'Second review row', 'Second reply', 'Noted'],
        ], state)
        state['records'].append(state['current'])

        self.assertEqual(len(state['records']), 2)
        first = state['records'][0]
        self.assertEqual(first['s_no'], '1')
        self.assertIn('First line of a long review comment', first['company_comment'])
        self.assertIn('second line', first['company_comment'])
        self.assertIn('continued on the next page', first['company_comment'])
        self.assertIn('Noted and updated.', first['contractor_reply'])
        self.assertIn('Additional reply', first['company_decision'])

    def test_content_next_to_serial_column_is_not_discarded(self):
        state = {'records': [], 'current': None, 'status_code': '3', 'page_number': 3}
        _consume_rows([
            ['S.No.', '', 'COMPANY Comments', '', '', 'CONTRACTOR / VENDOR Reply', '', '', 'COMPANY Decision', ''],
            ['1.', 'Page 1 of 11 Code-2 Approved with comments.', '', '', 'Noted and updated.', '', '', 'OK', '', ''],
        ], state)
        state['records'].append(state['current'])

        self.assertIn('Page 1 of 11', state['records'][0]['company_comment'])


class DrawingOcrRowTests(SimpleTestCase):
    def test_ocr_transcript_produces_canonical_partial_rows(self):
        rows = _rows_from_ocr_text(
            'FIELD DCS SYSTEM CABINET UNIT: 113 13-PT-3191 113-TT-5101 113 A 16 101',
            page_number=18,
        )

        self.assertEqual([row['tag_number'] for row in rows], ['113-PT-3191', '113-TT-5101'])
        self.assertEqual(rows[0]['instrument_type'], 'PT')
        self.assertEqual(rows[0]['system'], 'DCS')
        self.assertEqual(rows[0]['unit'], '113')
        self.assertEqual(rows[0]['pri_cable_no'], '113 A 16 101')
        self.assertEqual(rows[0]['page_number'], 18)
