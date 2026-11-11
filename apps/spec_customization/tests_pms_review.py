from django.core.files.base import ContentFile
from django.test import TestCase
from . import tests_reference_intelligence as reference_tests
from .tests_matching_pipeline import xlsx
from .matching_models import ComponentMatchDecision
from .services.reference_intelligence import file_sha256


class PMSReviewTests(TestCase):
    def setUp(self):
        reference_tests.ReferenceIntelligenceTests.setUp(self)
        self.component = self.job.piping_classes.get().components.get()
        self.component.source_read_status = 'blank'
        self.component.source_reference = {'page': 1, 'row': 7, 'reference_documents': ['ITN14200']}
        self.component.save()
        self.reference.cat_file.save('pcmc.xlsx', ContentFile(xlsx('PipingCommodityMatlControlData',
            ['CONTRACTORCOMMODITYCODE', 'SHORTMATERIALDESCRIPTION', 'FIRSTSIZEFROM', 'FIRSTSIZETO'],
            [['GATE001', 'Gate valve', '2', '2']])))
        self.reference.cat_file_hash = file_sha256(self.reference.cat_file)
        self.reference.save()

    def run_matching(self):
        return self.client.post('/api/v1/spec-customization/matching-run/', {
            'job_id': str(self.job.pk), 'workbook_set_id': str(self.reference.pk)}, format='json')

    def test_blank_pms_confirmed_mapping_survives_reload_and_rerun(self):
        result = self.run_matching()
        self.assertEqual(result.status_code, 200, result.data)
        decision = ComponentMatchDecision.objects.get(job=self.job)
        candidate = decision.candidates[0]
        result = self.client.post('/api/v1/spec-customization/matching-decision-review/', {
            'decision_id': str(decision.pk), 'review_decision': 'approved',
            'candidate_id': candidate['candidate_id'], 'final_commodity_code': candidate['commodity_code']}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(self.run_matching().status_code, 200)
        decision.refresh_from_db()
        self.component.refresh_from_db()
        self.assertEqual(decision.final_commodity_code, 'GATE001')
        self.assertEqual(decision.review_decision, 'approved')
        self.assertIsNotNone(decision.reviewed_at)
        self.assertEqual(decision.reviewed_by, self.owner)
        self.assertEqual(self.component.pms_commodity_code, '')
        from .services.exporters.workbook_preview import build_preview
        catalog = build_preview(self.job, 'cat')
        rows = [row for sheet in catalog['sheets'] for row in sheet['rows']
                if row.get('source', {}).get('component_id') == str(self.component.pk)]
        self.assertTrue(rows)
        self.assertTrue(all(not row['cells'].get('IndustryCommodityCode') for row in rows))
        result = self.client.get('/api/v1/spec-customization/matching-decisions/', {'job': str(self.job.pk)})
        self.assertEqual(result.data['items'][0]['final_commodity_code'], 'GATE001')

    def test_conflicting_candidate_cannot_be_confirmed_or_remapped(self):
        self.component.pms_commodity_code = 'GATE001'
        self.component.save()
        self.reference.cat_file.save('wrong-size.xlsx', ContentFile(xlsx('PipingCommodityMatlControlData',
            ['CONTRACTORCOMMODITYCODE', 'SHORTMATERIALDESCRIPTION', 'FIRSTSIZEFROM', 'FIRSTSIZETO'],
            [['GATE001', 'Gate valve', '4', '4']])))
        self.reference.cat_file_hash = file_sha256(self.reference.cat_file)
        self.reference.save()
        self.assertEqual(self.run_matching().status_code, 200)
        decision = ComponentMatchDecision.objects.get(job=self.job)
        for action in ('approved', 'remapped'):
            result = self.client.post('/api/v1/spec-customization/matching-decision-review/', {
                'decision_id': str(decision.pk), 'review_decision': action,
                'candidate_id': decision.candidates[0]['candidate_id'], 'final_commodity_code': 'GATE001'}, format='json')
            self.assertEqual(result.status_code, 409)
