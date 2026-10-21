"""Phase 2 trust boundaries using synthetic storage and engineering records."""
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile

from django.test import TestCase
from . import tests_matching_pipeline as pipeline
from .tests_matching_pipeline import xlsx
from .matching_models import ComponentMatchDecision
from .models import PaperSpecDocument, PaperSpecExtractionJob, PipingClass, PipingClassComponent
from .services.component_matcher import match_component, parse_match_xlsx
from .services.reference_intelligence import file_sha256


class ReferenceIntelligenceTests(TestCase):
    def setUp(self):
        pipeline.MatchingPipelineTests.setUp(self)
        document = PaperSpecDocument.objects.create(original_filename='synthetic.pdf', uploaded_by=self.owner,
                                                   project_id=str(self.project.pk), sha256_hash='a' * 64)
        self.job = PaperSpecExtractionJob.objects.create(document=document, created_by=self.owner, status='completed')
        pc = PipingClass.objects.create(job=self.job, class_code='A', pressure_rating='150', material_grade='A105')
        PipingClassComponent.objects.create(piping_class=pc, component_type='valve', sub_type='Gate', size_from='2')
        self.reference.cat_file.save('ambiguous.xlsx', ContentFile(xlsx('GateValve',
            ['IndustryCommodityCode', 'Npd[1]', 'PressureRating[1]', 'MaterialGrade'],
            [['VLV-A', 2, 150, 'A105'], ['VLV-B', 2, 150, 'A105']])))
        parse_match_xlsx(self.reference.match_file, self.reference)
        self.reference.is_parsed = self.reference.schema_valid = True
        for kind in ('match', 'spec', 'cat'):
            setattr(self.reference, f'{kind}_file_hash', file_sha256(getattr(self.reference, f'{kind}_file')))
        self.reference.save()

    # Inherited pipeline cases are independently covered by MatchingPipelineTests.
    def run_matching(self):
        return self.client.post('/api/v1/spec-customization/matching-run/', {
            'job_id': str(self.job.pk), 'workbook_set_id': str(self.reference.pk)}, format='json')

    def test_spec_ineligibility_never_reads_cat(self):
        with patch('apps.spec_customization.services.component_matcher.load_cat_sheet') as cat:
            result = match_component('GATE VALVE', '4', '150', 'A105', self.reference)
        self.assertEqual(result['state'], 'ineligible')
        cat.assert_not_called()

    def test_batch_pins_and_preserves_ambiguous_candidates_and_review_on_rerun(self):
        response = self.run_matching()
        self.assertEqual(response.status_code, 200, response.data)
        self.job.refresh_from_db()
        self.assertEqual(self.job.reference_workbook_set_id, self.reference.pk)
        decision = ComponentMatchDecision.objects.get(job=self.job)
        self.assertEqual(decision.state, 'ambiguous')
        self.assertEqual(len(decision.candidates), 2)
        endpoint = '/api/v1/spec-customization/matching-decision-review/'
        response = self.client.post(endpoint, {'decision_id': str(decision.pk), 'review_decision': 'approved'}, format='json')
        self.assertEqual(response.status_code, 400)
        response = self.client.post(endpoint, {'decision_id': str(decision.pk), 'review_decision': 'approved',
                                               'final_commodity_code': 'VLV-B'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.run_matching().status_code, 200)
        decision.refresh_from_db()
        self.assertEqual(decision.final_commodity_code, 'VLV-B')
        self.assertEqual(ComponentMatchDecision.objects.filter(job=self.job).count(), 1)

    def test_unauthorized_run_list_review_and_freeze(self):
        self.run_matching()
        decision = ComponentMatchDecision.objects.get(job=self.job)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.run_matching().status_code, 403)
        self.assertEqual(self.client.get('/api/v1/spec-customization/matching-decisions/', {'job': str(self.job.pk)}).status_code, 403)
        self.assertEqual(self.client.get('/api/v1/spec-customization/matching-decisions/').data['items'], [])
        self.assertEqual(self.client.post('/api/v1/spec-customization/matching-decision-review/', {
            'decision_id': str(decision.pk), 'review_decision': 'rejected'}).status_code, 403)
        self.assertEqual(self.client.post('/api/v1/spec-customization/matching-set-freeze/', {
            'workbook_set_id': str(self.reference.pk)}).status_code, 403)

    def test_freeze_rejects_parse_and_delete_but_allows_new_revision(self):
        response = self.client.post('/api/v1/spec-customization/matching-set-freeze/', {'workbook_set_id': str(self.reference.pk)})
        self.assertEqual(response.status_code, 200, response.data)
        base = f'/api/v1/spec-customization/matching/sets/{self.reference.pk}/'
        self.assertEqual(self.client.post(base + 'parse/').status_code, 409)
        self.assertEqual(self.client.delete(base).status_code, 409)
        self.assertEqual(self.client.delete(f'/api/v1/spec-customization/projects/{self.project.pk}/').status_code, 409)
        self.reference.refresh_from_db()
        self.assertTrue(parse_match_xlsx(self.reference.match_file, self.reference)[1])

    def test_schema_failure_preserves_active_set(self):
        response = self.client.post('/api/v1/spec-customization/matching/upload/', {
            'project_id': str(self.project.pk),
            **{f'{k}_file': SimpleUploadedFile(f'{k}.xlsx', xlsx('Bad', ['Bad'], [['bad']])) for k in ('match', 'spec', 'cat')},
        }, format='multipart')
        self.assertEqual(response.status_code, 422)
        self.assertTrue(response.data['schema_errors'])
        self.reference.refresh_from_db()
        self.assertTrue(self.reference.is_active)

    def test_tampered_reference_does_not_pin(self):
        self.reference.cat_file.save('tampered.xlsx', ContentFile(b'corrupt bytes'))
        self.assertEqual(self.run_matching().status_code, 409)
        self.job.refresh_from_db()
        self.assertIsNone(self.job.reference_workbook_set_id)

    def test_remap_requires_existing_catalog_code(self):
        self.run_matching()
        decision = ComponentMatchDecision.objects.get(job=self.job)
        payload = {'decision_id': str(decision.pk), 'review_decision': 'remapped'}
        endpoint = '/api/v1/spec-customization/matching-decision-review/'
        self.assertEqual(self.client.post(endpoint, payload).status_code, 400)
        self.assertEqual(self.client.post(endpoint, {**payload, 'final_commodity_code': 'INVENTED'}).status_code, 400)
        self.assertEqual(self.client.post(endpoint, {**payload, 'final_commodity_code': 'VLV-A'}).status_code, 200)

    def test_unit_and_rating_families_are_not_conflated(self):
        from .services.component_matcher import normalize_size, normalize_pressure
        self.assertNotEqual(normalize_size('DN50'), normalize_size('50'))
        self.assertNotEqual(normalize_pressure('PN150'), normalize_pressure('150#'))
        self.assertEqual(normalize_size('1/2"'), normalize_size('0.5'))
        self.assertEqual(normalize_pressure('150 lbs'), normalize_pressure('150#'))

    def test_size_intervals_require_explicit_expansion(self):
        with patch('apps.spec_customization.services.component_matcher.load_cat_sheet') as cat:
            result = match_component('GATE VALVE', '2..4', '150', 'A105', self.reference)
        self.assertEqual(result['state'], 'unmatched')
        cat.assert_not_called()

    def test_conflicting_subtype_cannot_match_through_sheet_fallback(self):
        result = match_component('GATE VALVE', '2', '150', 'A105', self.reference, sub_type='Ball')
        self.assertEqual(result['state'], 'unmatched')

    def test_frozen_reference_model_writes_are_guarded(self):
        from django.core.exceptions import ValidationError
        response = self.client.post('/api/v1/spec-customization/matching-set-freeze/', {'workbook_set_id': str(self.reference.pk)})
        self.assertEqual(response.status_code, 200)
        self.reference.refresh_from_db()
        self.reference.match_file_hash = 'b' * 64
        with self.assertRaises(ValidationError):
            self.reference.save()
        rule = self.reference.rules.get()
        rule.catalog_component_name = 'Ball Valve'
        with self.assertRaises(ValidationError):
            rule.save()
        with self.assertRaises(ValidationError):
            rule.delete()
        with self.assertRaises(ValidationError):
            self.reference.delete()
