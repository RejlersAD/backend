"""PMS identifiers remain source observations across catalog resolution."""
import unittest
import pandas as pd
from .services.pcmc_matching import match_pcmc


class CatalogResolutionTests(unittest.TestCase):
    def test_noncompliant_catalog_identifiers_are_flagged_without_rewriting(self):
        for code in ('SHORT1', 'CODE0000000000000000001', 'PIPE 0000000000001', ' PIPE00000000000001'):
            with self.subTest(code=code):
                result = match_pcmc(pd.DataFrame([self.catalog(CONTRACTORCOMMODITYCODE=code)]), self.source())
                candidate = result['candidates'][0]
                self.assertEqual(candidate['commodity_code'], code)
                self.assertFalse(candidate['confirmable'])
                self.assertIn('18–20', candidate['code_format_issue'])

    def test_twenty_character_approved_code_is_not_changed(self):
        code = 'PIPE0000000000000001'
        self.assertEqual(len(code), 20)
        result = match_pcmc(pd.DataFrame([self.catalog(CONTRACTORCOMMODITYCODE=code)]), self.source())
        self.assertTrue(result['candidates'][0]['confirmable'])
        self.assertEqual(result['candidates'][0]['commodity_code'], code)
    def test_wrapped_header_preserves_blank_cell_without_using_reference_document(self):
        from .services.local_tables import LocalTableParser
        parser = LocalTableParser(r'PIPING SPEC:\s*(?P<code>\S+)', 'test')
        parser.read_text('PIPING SPEC: 10A06.00\ncomponent|description|size|COMMODITY\n|||CODE\npipe|pipe (REF DOC:ITN14200)|1|', 779)
        component = parser.classes['10A06.00']['components'][0]
        self.assertEqual(component['pms_commodity_code'], '')
        self.assertEqual(component['_source_reference']['pms_commodity_code_status'], 'blank')
        self.assertIn('ITN14200', component['description'])

    def test_commodity_without_code_header_is_not_a_code_column(self):
        from .services.local_tables import LocalTableParser
        parser = LocalTableParser(r'PIPING SPEC:\s*(?P<code>\S+)', 'test')
        parser.read_text('PIPING SPEC: 10A06.00\ncomponent|description|size|Commodity\npipe|pipe|1|PART-SYMBOL', 1)
        component = parser.classes['10A06.00']['components'][0]
        self.assertEqual(component['pms_commodity_code'], '')
        self.assertEqual(component['_source_reference']['pms_commodity_code_status'], 'unreadable')

    def source(self, **values):
        return {'description': 'pipe', 'component_type': 'pipe', 'size_from': '1', 'size_to': '1',
                'pms_commodity_code': '', **values}

    def catalog(self, **values):
        return {'CONTRACTORCOMMODITYCODE': 'PIPE00000000000001', 'SHORTMATERIALDESCRIPTION': 'pipe',
                'FIRSTSIZEFROM': '1', 'FIRSTSIZETO': '1', **values}

    def test_exact_code_checks_engineering_before_confirmation(self):
        result = match_pcmc(pd.DataFrame([self.catalog()]), self.source(pms_commodity_code='PIPE00000000000001'))
        self.assertEqual(result['discriminators_matched']['pms_code_lookup'], 'exact')
        self.assertTrue(result['candidates'][0]['confirmable'])
        self.assertFalse(result['matched'])

    def test_exact_code_conflicting_size_cannot_be_confirmed(self):
        result = match_pcmc(pd.DataFrame([self.catalog()]), self.source(pms_commodity_code='PIPE00000000000001', size_from='2', size_to='2'))
        self.assertFalse(result['candidates'][0]['confirmable'])
        self.assertTrue(result['candidates'][0]['differences'])

    def test_multiple_candidates_remain_review_work(self):
        result = match_pcmc(pd.DataFrame([self.catalog(), self.catalog(CONTRACTORCOMMODITYCODE='PIPE00000000000002')]), self.source())
        self.assertEqual(result['candidate_count'], 2)
        self.assertEqual(result['state'], 'ambiguous')

    def test_range_gap_cannot_be_assigned_a_single_code(self):
        result = match_pcmc(pd.DataFrame([self.catalog()]), self.source(size_to='4'))
        candidate = result['candidates'][0]
        self.assertEqual(candidate['coverage']['gaps'], [[1.0, 4.0]])
        self.assertFalse(candidate['confirmable'])

    def test_missing_catalog_size_is_unknown(self):
        result = match_pcmc(pd.DataFrame([self.catalog(FIRSTSIZEFROM='', FIRSTSIZETO='')]), self.source(size_to='4'))
        self.assertIn('size coverage', result['candidates'][0]['unknown_attributes'])
        self.assertFalse(result['candidates'][0]['confirmable'])

    def test_blank_source_does_not_become_suggested_code(self):
        source = self.source()
        result = match_pcmc(pd.DataFrame([self.catalog()]), source)
        self.assertEqual(source['pms_commodity_code'], '')
        self.assertEqual(result['commodity_code'], '')
        self.assertEqual(result['candidates'][0]['commodity_code'], 'PIPE00000000000001')

    def test_reference_document_cannot_supply_commodity_identifier(self):
        result = match_pcmc(pd.DataFrame([self.catalog()]), self.source(description='pipe (REF DOC:ITN14200)'))
        self.assertEqual(result['discriminators_matched']['pms_code_lookup'], 'blank')

    def test_conflicting_rating_codelist_id_blocks_exact_code(self):
        result = match_pcmc(pd.DataFrame([self.catalog(PressureRating='40')]), self.source(pms_commodity_code='PIPE00000000000001', rating='150'))
        self.assertFalse(result['candidates'][0]['confirmable'])
        self.assertEqual(result['candidates'][0]['differences'][0]['attribute'], 'rating')

    def test_reviewed_codelist_selection_is_reused(self):
        from .services.buhasa_codelists import dictionary, normalize
        male = next(entry for entry in dictionary()[0]['EndPreparation'] if entry['shortDesc'] == 'MTE')
        evidence = normalize({'EndPreparation': 'THD'}, {'EndPreparation': male})
        result = match_pcmc(pd.DataFrame([self.catalog(EndPreparation='331')]), self.source(end_connection='THD'), evidence)
        self.assertTrue(result['candidates'][0]['confirmable'])
        self.assertIn('end_connection', result['candidates'][0]['matched_attributes'])
