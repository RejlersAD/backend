import tempfile
from pathlib import Path
from unittest import TestCase

from openpyxl import Workbook

from .hmb_master_template_parser import (
    _canonical_case_name,
    analyze_hmb_master_template,
    parse_hmb_case_workbook,
    parse_hmb_csv_file,
)


class HMBWorkbookParserTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.master_path = self.root / 'Master.xlsx'
        self._write_master(self.master_path, populated=False)
        self.template = analyze_hmb_master_template(str(self.master_path))

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def _write_master(path, populated):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = 'Master'
        sheet['A1'] = 'CASE A (1a) - baseline'
        sheet['C1'] = 'Stream'
        sheet['D1'] = '1048'
        sheet['C2'] = 'Description'
        sheet['D2'] = 'Test stream'
        sheet['B6'] = 'Temperature'
        sheet['C6'] = 'F'
        sheet['B43'] = 'H2S'
        sheet['C43'] = 'mol frac.'
        if populated:
            sheet['D6'] = 100.5
            sheet['D43'] = 0.01
        workbook.save(path)
        workbook.close()

    def test_canonical_case_names(self):
        self.assertEqual(_canonical_case_name('Case_1B_Stream_Summary.xlsx'), 'CASE A (1b)')
        self.assertEqual(_canonical_case_name('CASE C (2a)'), 'CASE C (2a)')
        self.assertEqual(_canonical_case_name('CASE-A-1a-SUM-MAX OIL-GAS-REV D'), 'CASE A (1a)')
        self.assertEqual(_canonical_case_name('Relief scenario.xlsx'), 'Relief scenario')

    def test_csv_long_layout_maps_to_template(self):
        path = self.root / 'Case_A_1b.csv'
        path.write_text(
            'Case,Stream ID,Phase,Parameter,UOM,Result\n'
            'CASE A (1b),1048,Overall,Temperature,F,64.61\n'
            'CASE A (1b),1048,Composition,H2S,mol frac.,0.0012\n',
            encoding='utf-8',
        )

        parsed = parse_hmb_csv_file(str(path), path.name, self.template)

        self.assertEqual(parsed['detected_format'], 'csv_long')
        self.assertEqual(parsed['case_name'], 'CASE A (1b)')
        self.assertEqual(parsed['record_count'], 2)
        self.assertEqual({row['property_name'] for row in parsed['records']}, {'Temperature', 'H2S'})

    def test_csv_wide_layout_maps_stream_columns(self):
        path = self.root / 'CASE B (1a).csv'
        path.write_text(
            'Section,Property,Unit,1048\n'
            'General,Temperature,F,71.2\n'
            'Composition,H2S,mol frac.,0.002\n',
            encoding='utf-8',
        )

        parsed = parse_hmb_csv_file(str(path), path.name, self.template)

        self.assertEqual(parsed['detected_format'], 'csv_wide')
        self.assertEqual(parsed['case_name'], 'CASE B (1a)')
        self.assertEqual(parsed['stream_count'], 1)
        self.assertEqual(parsed['record_count'], 2)

    def test_normalized_summary_maps_to_master(self):
        path = self.root / 'Case_1B_Stream_Summary.xlsx'
        workbook = Workbook()
        conditions = workbook.active
        conditions.title = 'Conditions'
        conditions.append(['Stream', 'Temperature: (F) | Overall'])
        conditions.append(['1048', 64.61])
        properties = workbook.create_sheet('Properties')
        properties.append(['Stream', 'Molecular Weight | Overall'])
        properties.append(['1048', 18.06])
        composition = workbook.create_sheet('Mole Fraction')
        composition.append(['Stream', 'H2S'])
        composition.append(['1048', 0.0012])
        workbook.save(path)
        workbook.close()

        parsed = parse_hmb_case_workbook(str(path), path.name, self.template)

        self.assertEqual(parsed['case_name'], 'CASE A (1b)')
        self.assertEqual(parsed['detected_format'], 'normalized_summary')
        self.assertEqual(parsed['stream_count'], 1)
        self.assertEqual(parsed['stream_mapping']['mapped_source_stream_count'], 1)
        values = {(row['section_key'], row['property_name']): row['value_text'] for row in parsed['records']}
        self.assertEqual(values[('general', 'Temperature')], '64.61')
        self.assertEqual(values[('composition', 'H2S')], '0.0012')

    def test_populated_master_is_case_data(self):
        path = self.root / 'Case 1A Template.xlsx'
        self._write_master(path, populated=True)

        parsed = parse_hmb_case_workbook(str(path), path.name, self.template)

        self.assertEqual(parsed['case_name'], 'CASE A (1a)')
        self.assertEqual(parsed['detected_format'], 'master_case')
        self.assertEqual(parsed['stream_count'], 1)
        self.assertEqual(parsed['record_count'], 2)
        self.assertEqual({row['value_text'] for row in parsed['records']}, {'100.5', '0.01'})
