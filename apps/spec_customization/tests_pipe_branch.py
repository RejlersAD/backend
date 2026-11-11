from types import SimpleNamespace
from django.test import SimpleTestCase, TestCase
from unittest.mock import patch
from openpyxl import load_workbook
from . import tests_controlled_release as release_tests
from .services.exporters.smartplant_config import _rows_pipe_branch, _rows_permissible_taps


class ApprovedPipeBranchTests(SimpleTestCase):
    def test_single_prefixed_tap_row_per_specification(self):
        for code in ('10A06.00', 'AA1B'):
            self.assertEqual(_rows_permissible_taps(SimpleNamespace(class_code=code)), [
                {'SpecName': code, 'PermissibleTapNumber': 'Tap-001', 'IsPreferredTap': '1'}])

    def test_restores_approved_pairs_and_primary_short_codes(self):
        rows = _rows_pipe_branch(SimpleNamespace(class_code='10A06.00'))
        self.assertEqual(len(rows), 148)
        self.assertEqual(len({(r['HeaderSize'], r['BranchSize']) for r in rows}), 148)
        self.assertTrue(all(r['SpecName'] == '10A06.00' for r in rows))
        self.assertEqual(rows[0]['HeaderSize'], '0.50')
        self.assertEqual(rows[0]['ShortCode'], 'Tee')
        self.assertEqual(next(r['ShortCode'] for r in rows if r['HeaderSize'] == '6' and r['BranchSize'] == '2'), 'Weldolet')
        self.assertTrue(all(r['AngleLow'] == r['AngleHigh'] == '90.5deg' for r in rows))
        self.assertTrue(all(r['SecondaryShortCode'] == r['TertiaryShortCode'] == '' for r in rows))

    def test_specifications_do_not_mutate_shared_matrix(self):
        first = _rows_pipe_branch(SimpleNamespace(class_code='A'))
        second = _rows_pipe_branch(SimpleNamespace(class_code='B'))
        first[0]['ShortCode'] = 'changed'
        self.assertEqual(second[0]['ShortCode'], 'Tee')
        self.assertEqual(second[0]['SpecName'], 'B')


class PipeBranchWorkbookTests(TestCase):
    def setUp(self):
        release_tests.ControlledReleaseTests.setUp(self)

    def test_preview_and_export_restore_matrix_and_clear_legacy_overrides(self):
        from .services.exporters.workbook_preview import build_preview
        preview = build_preview(self.job, 'spec')
        branch = next(sheet for sheet in preview['sheets'] if sheet['name'] == 'PipeBranch')
        taps = next(sheet for sheet in preview['sheets'] if sheet['name'] == 'PermissibleTaps')
        self.assertEqual(taps['row_count'], 1)
        self.assertEqual(taps['rows'][0]['cells']['PermissibleTapNumber'], 'Tap-001')
        self.assertEqual(branch['row_count'], 148)
        row_key = branch['rows'][0]['row_key']
        with patch('apps.spec_customization.services.exporters.workbook_preview._load_overrides', return_value={
            ('PipeBranch', row_key): {'AngleLow': '90', 'SecondaryShortCode': 'Weldolet', 'TertiaryShortCode': 'Sockolet'},
            ('PermissibleTaps', taps['rows'][0]['row_key']): {'PermissibleTapNumber': '1'},
        }):
            updated = build_preview(self.job, 'spec')
            branch = next(sheet for sheet in updated['sheets'] if sheet['name'] == 'PipeBranch')
            taps = next(sheet for sheet in updated['sheets'] if sheet['name'] == 'PermissibleTaps')
        self.assertEqual(taps['rows'][0]['cells']['PermissibleTapNumber'], 'Tap-001')
        self.assertTrue(all(row['cells']['AngleLow'] == row['cells']['AngleHigh'] == '90.5deg' for row in branch['rows']))
        self.assertTrue(all(row['cells']['SecondaryShortCode'] == row['cells']['TertiaryShortCode'] == '' for row in branch['rows']))
        workbook = load_workbook(self.real_spec_exporter(self.job, preview_data={'sheets': [branch, taps]}), read_only=True)
        values = list(workbook['PipeBranch'].values)
        header_index = next(i for i, row in enumerate(values) if 'HEADERSIZE' in [str(v).upper() for v in row])
        headers = [str(v).upper() for v in values[header_index]]
        columns = {name: headers.index(name) for name in ('HEADERSIZE', 'BRANCHSIZE', 'ANGLELOW', 'ANGLEHIGH', 'SECONDARYSHORTCODE', 'TERTIARYSHORTCODE')}
        data = [row for row in values[header_index + 1:] if row[columns['HEADERSIZE']] and row[columns['BRANCHSIZE']]]
        self.assertEqual(len(data), 148)
        self.assertTrue(all(row[columns['ANGLELOW']] == row[columns['ANGLEHIGH']] == '90.5deg' for row in data))
        self.assertTrue(all(not row[columns['SECONDARYSHORTCODE']] and not row[columns['TERTIARYSHORTCODE']] for row in data))
        tap_values = list(workbook['PermissibleTaps'].values)
        tap_header = next(i for i, row in enumerate(tap_values) if 'PERMISSIBLETAPNUMBER' in [str(v).upper() for v in row])
        tap_column = [str(v).upper() for v in tap_values[tap_header]].index('PERMISSIBLETAPNUMBER')
        codes = [row[tap_column] for row in tap_values[tap_header + 1:] if row[tap_column]]
        self.assertEqual(codes, ['Tap-001'])
