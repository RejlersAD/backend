"""Workbook snapshots preserve full source scope without granting source access."""
import json
from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZipFile

from django.core.management import call_command
from django.test import SimpleTestCase
from openpyxl import Workbook

from apps.finance.services.workbook_summary import (
    SNAPSHOT_PATH, build_workbook_summary, validate_snapshot,
)
from apps.finance.services.workbook_summary_snapshot import HEADERS, generate_workbook_snapshot


CAPTURED = datetime(2026, 9, 21, 13, 50, tzinfo=timezone.utc)


class WorkbookSummaryTests(SimpleTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix='invoice-workbook-summary-tests-')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'source.xlsx'

    def workbook(self, rows):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = 'External Invoice '
        for column, label in HEADERS.items():
            sheet[f'{column}5'] = label
        for number, values in enumerate(rows, 6):
            for column, value in values.items():
                sheet[f'{column}{number}'] = value
        workbook.save(self.path)
        workbook.close()

    def snapshot(self, last_row):
        return generate_workbook_snapshot(self.path, last_row=last_row, snapshot_at=CAPTURED)

    def test_packaged_snapshot_matches_independently_audited_workbook(self):
        data = validate_snapshot(json.loads(SNAPSHOT_PATH.read_text(encoding='utf-8')))
        self.assertEqual(data['totals'], {
            'invoice_amount': '315481678.41', 'invoice_amount_aed': '466151390.16',
            'actual_payment_received': '285759742.00', 'project_count': 496,
        })
        self.assertEqual(data['invoice_count'], 4404)
        self.assertEqual([row['count'] for row in data['payment_status']], [3895, 368, 58, 34, 49])
        self.assertEqual(data['source']['sha256'], '308a453a0bf71490174f6699760cfb670186e1731adf8f3843eff55b12840e43')
        self.assertEqual(data['source']['first_row'], 6)
        self.assertEqual(data['source']['last_row'], 4409)
        self.assertEqual(data['currency_basis'], 'mixed_original')
        self.assertEqual(data['project_excluded_rows'], 37)
        self.assertEqual(data['coverage'], {
            'invoice_amount': {'numeric_count': 4397, 'blank_count': 5, 'text_count': 2, 'error_count': 0},
            'invoice_amount_aed': {'numeric_count': 4397, 'blank_count': 5, 'text_count': 0, 'error_count': 2},
            'actual_payment_received': {'numeric_count': 3865, 'blank_count': 527, 'text_count': 12, 'error_count': 0},
        })
        self.assertNotIn('C:\\', data['source']['file_name'])

    def test_generation_preserves_duplicate_rows_and_source_statuses(self):
        self.workbook([
            {'A': 'DUP', 'G': 123, 'L': 10.005, 'M': 30.005, 'AA': 2, 'R': ' Paid '},
            {'A': 'DUP', 'G': '123', 'L': 10.005, 'M': 30.005, 'AA': 'AED 2', 'R': 'paid'},
            {'A': 'C', 'G': 'N/A', 'L': 'USD 10', 'M': '#VALUE!', 'AA': None, 'R': 'CANCELLED'},
            {'A': 'D', 'G': None, 'L': None, 'M': None, 'AA': 0, 'R': 'Pending'},
            {'A': 'E', 'G': 'P2', 'L': -3, 'M': -9, 'AA': True, 'R': 'New'},
            {'A': 'F', 'G': 'P2', 'L': 0, 'M': 0, 'AA': '#VALUE!', 'R': 'Offset'},
            {'A': 'G', 'G': 'P2', 'L': 0, 'M': 0, 'AA': 0, 'R': None},
            {'A': 'H', 'G': 'P2', 'L': 0, 'M': 0, 'AA': 0, 'R': '#VALUE!'},
            {'L': 999999, 'M': 999999, 'AA': 999999},
        ])
        data = self.snapshot(13)
        self.assertEqual(data['invoice_count'], 8)
        self.assertEqual(data['totals'], {
            'invoice_amount': '17.01', 'invoice_amount_aed': '51.01',
            'actual_payment_received': '2.00', 'project_count': 2,
        })
        self.assertEqual([row['count'] for row in data['payment_status']], [2, 1, 1, 1, 3])
        self.assertEqual(data['other_statuses'], [
            {'label': '#VALUE!', 'count': 1}, {'label': 'Not recorded', 'count': 1},
            {'label': 'Offset', 'count': 1},
        ])
        self.assertEqual(data['project_excluded_rows'], 2)
        self.assertEqual(data['coverage']['actual_payment_received'], {
            'numeric_count': 4, 'blank_count': 1, 'text_count': 2, 'error_count': 1,
        })
        self.assertEqual(data['source']['scope'], 'full_workbook')
        self.assertEqual(self.snapshot(13), data)

    def test_generator_uses_cached_formula_values_without_recalculation(self):
        self.workbook([{'A': 'INV', 'G': 'P1', 'L': 3, 'M': '=L6*3', 'AA': 1, 'R': 'Paid'}])
        original = self.path.read_bytes()
        replacement = BytesIO()
        with ZipFile(BytesIO(original)) as source, ZipFile(replacement, 'w') as target:
            for info in source.infolist():
                content = source.read(info.filename)
                if info.filename == 'xl/worksheets/sheet1.xml':
                    root = ElementTree.fromstring(content)
                    ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                    # The deliberately stale cached value must be preserved.
                    root.find('.//s:c[@r="M6"]/s:v', ns).text = '8.123'
                    content = ElementTree.tostring(root)
                target.writestr(info, content)
        self.path.write_bytes(replacement.getvalue())
        self.assertEqual(self.snapshot(6)['totals']['invoice_amount_aed'], '8.12')

    def test_generator_rejects_footer_inside_range_and_truncated_invoice_ranges(self):
        for footer in ({'L': 999}, {'A': 'Grand total', 'L': 999}):
            with self.subTest(footer=footer):
                self.workbook([{'A': 'INV', 'G': 'P1', 'L': 10, 'R': 'Paid'}, footer])
                with self.assertRaisesRegex(ValueError, 'not an invoice row'):
                    self.snapshot(7)
        self.workbook([{'A': 'INV-1', 'L': 10}, {'A': 'INV-2', 'L': 20}])
        with self.assertRaisesRegex(ValueError, 'after the specified last row'):
            self.snapshot(6)

    def test_generator_rejects_changed_headers_and_invalid_bounds(self):
        self.workbook([{'A': 'INV', 'L': 10}])
        for last_row in (5, 99):
            with self.assertRaises(ValueError):
                self.snapshot(last_row)
        with self.assertRaisesRegex(ValueError, 'header'):
            generate_workbook_snapshot(self.path, last_row=6, header_row=4)
        with self.assertRaisesRegex(ValueError, 'timezone'):
            generate_workbook_snapshot(self.path, last_row=6, snapshot_at=datetime(2026, 9, 21))

    def test_permission_is_checked_before_opening_any_snapshot_data(self):
        with patch('apps.finance.services.workbook_summary.module_action_allowed', return_value=False) as permission, \
                patch.object(Path, 'open') as opened:
            result = build_workbook_summary('reader')
        permission.assert_called_once_with('reader', 'finance_outgoing', 'read')
        opened.assert_not_called()
        self.assertEqual(result['status'], 'restricted')
        self.assertEqual(set(result), {'schema_version', 'status', 'reason'})

    def test_unavailable_or_inconsistent_snapshot_does_not_return_partial_data(self):
        data = json.loads(SNAPSHOT_PATH.read_text(encoding='utf-8'))
        inconsistent = deepcopy(data)
        inconsistent['payment_status'][0]['count'] += 1
        for content in ('not json', '{}', json.dumps(inconsistent)):
            with self.subTest(content=content[:20]):
                path = Path(self.directory.name) / 'invalid.json'
                path.write_text(content, encoding='utf-8')
                with patch('apps.finance.services.workbook_summary.module_action_allowed', return_value=True), \
                        patch('apps.finance.services.workbook_summary.SNAPSHOT_PATH', path), \
                        self.assertLogs('apps.finance.services.workbook_summary', level='ERROR'):
                    result = build_workbook_summary('reader')
                self.assertEqual(result['status'], 'unavailable')
                self.assertEqual(set(result), {'schema_version', 'status', 'reason'})
        with patch('apps.finance.services.workbook_summary.module_action_allowed', return_value=True), \
                patch('apps.finance.services.workbook_summary.SNAPSHOT_PATH', Path(self.directory.name) / 'missing.json'), \
                self.assertLogs('apps.finance.services.workbook_summary', level='ERROR'):
            self.assertEqual(build_workbook_summary('reader')['status'], 'unavailable')

    def test_management_command_generates_only_an_aggregate_artifact(self):
        self.workbook([{'A': 'PRIVATE-INVOICE', 'G': 'PRIVATE-PROJECT', 'L': 10, 'M': 30, 'AA': 2, 'R': 'Paid'}])
        output = Path(self.directory.name) / 'summary.json'
        call_command('generate_invoice_workbook_summary', str(self.path), last_row=6,
                     snapshot_at=CAPTURED.isoformat(), output=output, stdout=StringIO())
        content = output.read_text(encoding='utf-8')
        self.assertNotIn('PRIVATE-', content)
        self.assertEqual(json.loads(content), self.snapshot(6))
