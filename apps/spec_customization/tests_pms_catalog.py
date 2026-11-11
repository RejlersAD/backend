"""PMS identifiers remain source observations across catalog resolution."""
import unittest
import pandas as pd
from .services.pcmc_matching import match_pcmc


class CatalogResolutionTests(unittest.TestCase):
    def source(self, **values):
        return {'description': 'pipe', 'component_type': 'pipe', 'size_from': '1', 'size_to': '1',
                'pms_commodity_code': '', **values}

    def catalog(self, **values):
        return {'CONTRACTORCOMMODITYCODE': 'PIPE001', 'SHORTMATERIALDESCRIPTION': 'pipe',
                'FIRSTSIZEFROM': '1', 'FIRSTSIZETO': '1', **values}

    def test_exact_code_checks_engineering_before_confirmation(self):
        result = match_pcmc(pd.DataFrame([self.catalog()]), self.source(pms_commodity_code='PIPE001'))
        self.assertEqual(result['discriminators_matched']['pms_code_lookup'], 'exact')
        self.assertTrue(result['candidates'][0]['confirmable'])
        self.assertFalse(result['matched'])

    def test_exact_code_conflicting_size_cannot_be_confirmed(self):
        result = match_pcmc(pd.DataFrame([self.catalog()]), self.source(pms_commodity_code='PIPE001', size_from='2', size_to='2'))
        self.assertFalse(result['candidates'][0]['confirmable'])
        self.assertTrue(result['candidates'][0]['differences'])

    def test_multiple_candidates_remain_review_work(self):
        result = match_pcmc(pd.DataFrame([self.catalog(), self.catalog(CONTRACTORCOMMODITYCODE='PIPE002')]), self.source())
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
        self.assertEqual(result['candidates'][0]['commodity_code'], 'PIPE001')

    def test_reference_document_cannot_supply_commodity_identifier(self):
        result = match_pcmc(pd.DataFrame([self.catalog()]), self.source(description='pipe (REF DOC:ITN14200)'))
        self.assertEqual(result['discriminators_matched']['pms_code_lookup'], 'blank')

    def test_conflicting_rating_codelist_id_blocks_exact_code(self):
        result = match_pcmc(pd.DataFrame([self.catalog(PressureRating='40')]), self.source(pms_commodity_code='PIPE001', rating='150'))
        self.assertFalse(result['candidates'][0]['confirmable'])
        self.assertEqual(result['candidates'][0]['differences'][0]['attribute'], 'rating')

    def test_reviewed_codelist_selection_is_reused(self):
        from .services.buhasa_codelists import dictionary, normalize
        male = next(entry for entry in dictionary()[0]['EndPreparation'] if entry['shortDesc'] == 'MTE')
        evidence = normalize({'EndPreparation': 'THD'}, {'EndPreparation': male})
        result = match_pcmc(pd.DataFrame([self.catalog(EndPreparation='331')]), self.source(end_connection='THD'), evidence)
        self.assertTrue(result['candidates'][0]['confirmable'])
        self.assertIn('end_connection', result['candidates'][0]['matched_attributes'])
