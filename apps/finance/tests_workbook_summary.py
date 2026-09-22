"""Workbook snapshots preserve full source scope without granting source access."""
import json
from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
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

    def workbook(self, rows, formats=None):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = 'External Invoice '
        for column, label in HEADERS.items():
            sheet[f'{column}5'] = label
        for number, values in enumerate(rows, 6):
            for column, value in values.items():
                sheet[f'{column}{number}'] = value
        for coordinate, number_format in (formats or {}).items():
            sheet[coordinate].number_format = number_format
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
        self.assertEqual([row['amount_aed'] for row in data['payment_status']], [
            '412405138.81', '39497085.61', '6401792.89', '2257559.79', '5589813.06',
        ])
        self.assertEqual(data['payment_status'][0]['amount_coverage']['error_count'], 2)
        self.assertEqual(data['payment_status'][1]['amount_coverage']['blank_count'], 5)
        self.assertEqual(data['payment_status_rounding_adjustment'], '0.00')
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
        groups = {(row['currency'], row['currency_status']): row for row in data['currency_breakdown']}
        expected = {
            ('AED', 'recorded'): ('256727607.14', '236869176.92', 2020, 1829),
            ('EUR', 'recorded'): ('13008426.71', '9592153.57', 1707, 1462),
            ('SEK', 'recorded'): ('219000.00', '219000.00', 1, 1),
            ('USD', 'recorded'): ('41706207.76', '36175812.14', 639, 549),
            (None, 'conflict'): ('674340.40', '475467.22', 11, 10),
            (None, 'not_recorded'): ('3146096.40', '2428132.15', 19, 14),
        }
        self.assertEqual(set(groups), set(expected))
        for identity, (invoice, receipt, invoice_count, receipt_count) in expected.items():
            group = groups[identity]
            self.assertEqual(group['invoice_amount'], invoice)
            self.assertEqual(group['actual_payment_received'], receipt)
            self.assertEqual(group['coverage']['invoice_amount']['numeric_count'], invoice_count)
            self.assertEqual(group['coverage']['actual_payment_received']['numeric_count'], receipt_count)
        self.assertEqual(data['currency_rounding_adjustment'], {
            'invoice_amount': '0.00', 'actual_payment_received': '0.00',
        })

    def test_currency_classification_uses_each_amounts_own_explicit_sources(self):
        self.workbook([
            {'A': '1', 'L': 1, 'AA': 2, 'AE': ' usd '},
            {'A': '2', 'L': 3, 'AA': 4},
            {'A': '3', 'L': 5, 'AA': 6, 'AE': 'AED'},
            {'A': '4', 'L': 0, 'AA': None, 'AE': 'Euro'},
            {'A': '5', 'L': 7, 'AA': 8},
            {'A': '6', 'L': 9, 'AA': 10, 'AE': '#VALUE!'},
            {'A': '7', 'L': 11, 'AA': 12, 'AE': 'dollars'},
            {'A': '8', 'L': 'GBP 12', 'AA': None, 'AE': 'GBP'},
            {'A': '9', 'L': 0, 'AA': None},
        ], formats={
            'L6': '[$EUR] #,##0.00', 'AA6': '[$USD] #,##0.00',
            'L7': '#,##0.00 [$\u20ac-1]', 'AA7': '[$AED] #,##0.00',
            'AA8': '[$EUR] #,##0.00',
            'L11': '[$USD] #,##0.00', 'AA11': '[$USD] #,##0.00',
            'L12': '[$USD] #,##0.00', 'AA12': '[$USD] #,##0.00',
            'L14': '[$SEK] #,##0.00', 'AA14': '[$USD] #,##0.00',
        })
        data = self.snapshot(14)
        groups = {(row['currency'], row['currency_status']): row for row in data['currency_breakdown']}
        self.assertEqual(groups[('EUR', 'recorded')]['invoice_amount'], '3.00')
        self.assertIsNone(groups[('EUR', 'recorded')]['actual_payment_received'])
        self.assertEqual(groups[('AED', 'recorded')]['invoice_amount'], '5.00')
        self.assertEqual(groups[('AED', 'recorded')]['actual_payment_received'], '4.00')
        self.assertEqual(groups[('USD', 'recorded')]['actual_payment_received'], '2.00')
        self.assertIsNone(groups[('USD', 'recorded')]['invoice_amount'])
        self.assertEqual(groups[(None, 'conflict')]['invoice_amount'], '1.00')
        self.assertEqual(groups[(None, 'conflict')]['actual_payment_received'], '6.00')
        self.assertEqual(groups[(None, 'not_recorded')]['invoice_amount'], '7.00')
        self.assertEqual(groups[(None, 'not_recorded')]['actual_payment_received'], '8.00')
        self.assertEqual(groups[(None, 'error')]['actual_payment_received'], '10.00')
        self.assertEqual(groups[(None, 'unrecognized')]['invoice_amount'], '11.00')
        self.assertEqual(groups[('SEK', 'recorded')]['invoice_amount'], '0.00')
        self.assertIsNone(groups[('GBP', 'recorded')]['invoice_amount'])
        self.assertEqual(groups[('GBP', 'recorded')]['coverage']['invoice_amount']['text_count'], 1)
        for key in ('invoice_amount', 'actual_payment_received'):
            self.assertEqual(sum(row['row_counts'][key] for row in groups.values()), 9)

    def test_ambiguous_or_disagreeing_format_markers_remain_unassigned(self):
        self.workbook([
            {'A': '1', 'L': 3, 'AA': 4},
            {'A': '2', 'L': 5, 'AA': 6},
        ], formats={
            'L6': '[$$-409] #,##0.00', 'AA6': '#,##0.00',
            'L7': '[$EUR] #,##0.00;[$USD] -#,##0.00',
            'AA7': '"US DOLLAR" #,##0.00',
        })
        groups = {(row['currency'], row['currency_status']): row for row in self.snapshot(7)['currency_breakdown']}
        self.assertEqual(groups[(None, 'not_recorded')]['invoice_amount'], '3.00')
        self.assertEqual(groups[(None, 'conflict')]['invoice_amount'], '5.00')
        self.assertEqual(groups[('USD', 'recorded')]['actual_payment_received'], '6.00')

    def test_currency_subtotals_keep_exact_precision_and_report_rounding_difference(self):
        self.workbook([
            {'A': '1', 'L': .005, 'M': .005, 'AA': -.005, 'AE': 'EUR', 'R': 'Paid'},
            {'A': '2', 'L': .005, 'M': .005, 'AA': -.005, 'AE': 'USD', 'R': 'New'},
        ])
        data = self.snapshot(7)
        self.assertEqual(data['totals']['invoice_amount'], '0.01')
        self.assertEqual([row['invoice_amount'] for row in data['currency_breakdown']], ['0.01', '0.01'])
        self.assertEqual(data['currency_rounding_adjustment'], {
            'invoice_amount': '-0.01', 'actual_payment_received': '0.01',
        })
        for key in ('invoice_amount', 'actual_payment_received'):
            broken = deepcopy(data)
            broken['currency_breakdown'][0]['exact_amounts'][key] = '100'
            with self.assertRaises(ValueError):
                validate_snapshot(broken)
        broken = deepcopy(data)
        broken['currency_breakdown'][0]['coverage']['invoice_amount']['numeric_count'] = 0
        with self.assertRaises(ValueError):
            validate_snapshot(broken)
        self.assertEqual(data['payment_status_rounding_adjustment'], '-0.01')
        self.assertEqual(data['totals']['invoice_amount_aed'], '0.01')
        for field, value in [('amount_aed', '999'), ('exact_amount_aed', '999')]:
            broken = deepcopy(data)
            broken['payment_status'][0][field] = value
            with self.assertRaises(ValueError):
                validate_snapshot(broken)
        broken = deepcopy(data)
        broken['payment_status'][0]['amount_coverage']['numeric_count'] = 0
        with self.assertRaises(ValueError):
            validate_snapshot(broken)
        broken = deepcopy(data)
        broken['payment_status_rounding_adjustment'] = '0.00'
        with self.assertRaises(ValueError):
            validate_snapshot(broken)
        broken = deepcopy(data)
        broken['currency_rounding_adjustment']['invoice_amount'] = '0.00'
        with self.assertRaises(ValueError):
            validate_snapshot(broken)

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
        self.assertEqual([row['amount_aed'] for row in data['payment_status']], [
            '60.01', None, None, '-9.00', '0.00',
        ])
        self.assertEqual(data['payment_status'][1]['amount_coverage']['error_count'], 1)
        self.assertEqual(data['payment_status'][2]['amount_coverage']['blank_count'], 1)
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
                    # The deliberately stale cached value must be preserved.
                    uncached = b'<f>L6*3</f><v></v>'
                    self.assertEqual(content.count(uncached), 1)
                    content = content.replace(uncached, b'<f>L6*3</f><v>8.123</v>', 1)
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
        snapshot = SimpleNamespace(
            sha256=data['source']['sha256'], sheet_name=data['source']['sheet'],
            first_row=data['source']['first_row'], last_row=data['source']['last_row'],
            header_row=data['source']['first_row'] - 1, row_count=data['invoice_count'],
            file_name=data['source']['file_name'], reconciliation={})
        inconsistent = deepcopy(data)
        inconsistent['payment_status'][0]['count'] += 1
        for content in ('not json', '{}', json.dumps(inconsistent)):
            with self.subTest(content=content[:20]):
                path = Path(self.directory.name) / 'invalid.json'
                path.write_text(content, encoding='utf-8')
                with patch('apps.finance.services.workbook_summary.module_action_allowed', return_value=True), \
                        patch('apps.finance.services.workbook_summary.SNAPSHOT_PATH', path), \
                        self.assertLogs('apps.finance.services.workbook_summary', level='ERROR'):
                    result = build_workbook_summary('reader', source_snapshot=snapshot)
                self.assertEqual(result['status'], 'unavailable')
                self.assertEqual(set(result), {'schema_version', 'status', 'reason'})
        with patch('apps.finance.services.workbook_summary.module_action_allowed', return_value=True), \
                patch('apps.finance.services.workbook_summary.SNAPSHOT_PATH', Path(self.directory.name) / 'missing.json'), \
                self.assertLogs('apps.finance.services.workbook_summary', level='ERROR'):
            self.assertEqual(build_workbook_summary('reader', source_snapshot=snapshot)['status'], 'unavailable')

    def test_legacy_packaged_summary_requires_matching_active_source_provenance(self):
        data = json.loads(SNAPSHOT_PATH.read_text(encoding='utf-8'))
        snapshot = SimpleNamespace(
            sha256=data['source']['sha256'], sheet_name=data['source']['sheet'],
            first_row=data['source']['first_row'], last_row=data['source']['last_row'],
            header_row=data['source']['first_row'] - 1, row_count=data['invoice_count'],
            file_name='Original uploaded workbook.xlsx', reconciliation={})
        with patch('apps.finance.services.workbook_summary.module_action_allowed', return_value=True):
            result = build_workbook_summary('reader', source_snapshot=snapshot)
            self.assertEqual(result['totals'], data['totals'])
            self.assertEqual(result['source']['file_name'], snapshot.file_name)
            for field, value in (('sha256', 'b' * 64), ('sheet_name', 'Other sheet'),
                                 ('first_row', 7), ('last_row', 4408), ('row_count', 4403), ('header_row', 4)):
                with self.subTest(field=field):
                    changed = deepcopy(snapshot)
                    setattr(changed, field, value)
                    with self.assertLogs('apps.finance.services.workbook_summary', level='ERROR'):
                        self.assertEqual(build_workbook_summary('reader', source_snapshot=changed)['status'],
                                         'unavailable')

    def test_invalid_stored_summary_never_falls_back_to_packaged_totals(self):
        data = json.loads(SNAPSHOT_PATH.read_text(encoding='utf-8'))
        snapshot = SimpleNamespace(
            sha256=data['source']['sha256'], sheet_name=data['source']['sheet'],
            first_row=data['source']['first_row'], last_row=data['source']['last_row'],
            header_row=data['source']['first_row'] - 1, row_count=data['invoice_count'],
            file_name=data['source']['file_name'], reconciliation={'workbook_summary': {}})
        with patch('apps.finance.services.workbook_summary.module_action_allowed', return_value=True), \
                patch.object(Path, 'open') as opened, \
                self.assertLogs('apps.finance.services.workbook_summary', level='ERROR'):
            result = build_workbook_summary('reader', source_snapshot=snapshot)
        opened.assert_not_called()
        self.assertEqual(result['status'], 'unavailable')

    def test_no_active_snapshot_requires_upload_and_never_opens_packaged_totals(self):
        with patch('apps.finance.services.workbook_summary.module_action_allowed', return_value=True), \
                patch.object(Path, 'open') as opened:
            result = build_workbook_summary('reader', source_snapshot=None)
        opened.assert_not_called()
        self.assertEqual(result, {'schema_version': '1.0', 'status': 'unavailable',
                                 'reason': 'Upload a receivables workbook to view its totals.'})

    def test_management_command_generates_only_an_aggregate_artifact(self):
        self.workbook([{'A': 'PRIVATE-INVOICE', 'G': 'PRIVATE-PROJECT', 'L': 10, 'M': 30, 'AA': 2, 'R': 'Paid'}])
        output = Path(self.directory.name) / 'summary.json'
        call_command('generate_invoice_workbook_summary', str(self.path), last_row=6,
                     snapshot_at=CAPTURED.isoformat(), output=output, stdout=StringIO())
        content = output.read_text(encoding='utf-8')
        self.assertNotIn('PRIVATE-', content)
        self.assertEqual(json.loads(content), self.snapshot(6))
