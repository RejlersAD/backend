import copy
import unittest
from .services.exporters.commodity_codes import assign_generated_codes


class GeneratedCodeTests(unittest.TestCase):
    def row(self, **changes):
        return {'_generation_base': 'RAD_CAP_10A0600_N0050', 'CommodityCode': '',
                'SpecName': '10A06.00', 'FirstSizeFrom': '0.5', 'FirstSizeTo': '0.5', **changes}

    def test_reference_workbook_pattern_without_separators(self):
        row = assign_generated_codes([self.row()])[0]
        self.assertEqual(row['CommodityCode'], 'RADCAP10A0600N0050')
        self.assertEqual(len(row['CommodityCode']), 18)

    def test_long_and_short_codes_are_stable_twenty_characters(self):
        for base in ('RAD_CAP_LONGSPECIFICATION_N0050_to_N0200', 'RAD_PIP_A_N0001'):
            source = self.row(_generation_base=base)
            first = assign_generated_codes([copy.deepcopy(source)])[0]['CommodityCode']
            second = assign_generated_codes([copy.deepcopy(source)])[0]['CommodityCode']
            self.assertEqual(first, second)
            self.assertEqual(len(first), 20)
            self.assertTrue(first.isalnum())

    def test_different_schedules_do_not_share_identifier(self):
        rows = [self.row(FirstSizeSchedule='40'), self.row(FirstSizeSchedule='80')]
        first = assign_generated_codes(copy.deepcopy(rows))
        second = assign_generated_codes(list(reversed(copy.deepcopy(rows))))
        self.assertEqual({r['FirstSizeSchedule']: r['CommodityCode'] for r in first}, {r['FirstSizeSchedule']: r['CommodityCode'] for r in second})
        self.assertEqual(len({r['CommodityCode'] for r in first}), 2)
        self.assertTrue(all(len(r['CommodityCode']) == 20 for r in first))

    def test_literal_pms_code_is_not_changed(self):
        row = assign_generated_codes([self.row(CommodityCode='SOURCE 123')])[0]
        self.assertEqual(row['CommodityCode'], 'SOURCE 123')
        self.assertNotIn('GeneratedCommodityCode', row)

    def test_generated_code_does_not_collide_with_literal_source_identifier(self):
        source = self.row(CommodityCode='RADCAP10A0600N0050')
        generated = self.row(FirstSizeSchedule='80')
        rows = assign_generated_codes([source, generated])
        self.assertEqual(rows[0]['CommodityCode'], 'RADCAP10A0600N0050')
        self.assertNotEqual(rows[1]['CommodityCode'], rows[0]['CommodityCode'])
        self.assertEqual(len(rows[1]['CommodityCode']), 20)
