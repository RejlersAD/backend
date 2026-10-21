"""Offline BUHASA matching, canonical review and server-side release guards."""
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase
from . import tests_reference_intelligence as reference_tests
from . import tests_controlled_release as release_tests
from unittest.mock import patch
from .tests_matching_pipeline import xlsx
from .matching_models import ComponentMatchDecision
from .services.buhasa_codelists import dictionary, resolve, normalize, dictionary_fingerprint
from .services.reference_intelligence import file_sha256
from .services.controlled_release import codelist_release_blockers


class ResolutionTests(SimpleTestCase):
    def test_dictionary_fingerprint_preserves_original_crlf_across_lf_checkouts(self):
        import hashlib
        raw = b'{"FlangeFacing":[]}\r\n'
        aliases = b'{"RF":[]}\r\n'
        original = hashlib.sha256(raw + aliases).hexdigest()
        self.assertEqual(dictionary_fingerprint(raw, aliases), original)
        self.assertEqual(dictionary_fingerprint(raw.replace(b'\r\n', b'\n'), aliases.replace(b'\r\n', b'\n')), original)

    def test_exact_alias_unmapped_and_thread_gender(self):
        self.assertEqual(resolve('FlangeFacing', 'RF')['code'], 5)
        self.assertEqual(resolve('FlangeFacing', ' raised FACE ')['code'], 5)
        self.assertEqual(resolve('EndPreparation', 'BW')['code'], 301)
        result = resolve('EndPreparation', 'THD')
        self.assertEqual(result['status'], 'AMBIGUOUS')
        self.assertEqual({entry['code'] for entry in result['candidates']}, {331, 441})
        self.assertEqual(resolve('BoltType', 'invented')['status'], 'UNMAPPED_CODELIST')
        self.assertEqual(resolve('BoltType', 'invented')['confidence'], 0)
        self.assertEqual(resolve('BoltType', '')['status'], 'NOT_SUPPLIED')

    def test_invalid_selection_rejected_and_catalog_numbers_are_ids(self):
        with self.assertRaises(ValueError):
            normalize({}, {'FlangeFacing': {'shortDesc': 'Forged', 'code': 5}})
        self.assertEqual(resolve('FlangeFacing', '5.0', catalog=True)['code'], 5)
        self.assertEqual(resolve('PressureRating', '150')['code'], 35)


class BuhasaWorkflowTests(TestCase):
    def setUp(self):
        reference_tests.ReferenceIntelligenceTests.setUp(self)
        pc = self.job.piping_classes.get()
        pc.material_grade = 'Alloy 20 (UNS N08020)'
        pc.flange_facing = 'RF'
        pc.save()
        self.component = pc.components.get()
        self.component.end_connection = 'THD'
        self.component.save()
        import io
        import pandas as pd
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            pd.DataFrame({'Npd': ['2']}).to_excel(writer, sheet_name='PipeNominalDiameters', index=False)
            pd.DataFrame({'MaterialsGrade': [40001]}).to_excel(writer, sheet_name='MaterialsData', index=False)
        self.reference.spec_file.save('spec-buhasa.xlsx', ContentFile(buffer.getvalue()))
        self.reference.cat_file.save('cat-buhasa.xlsx', ContentFile(xlsx('GateValve',
            ['IndustryCommodityCode', 'Npd[1]', 'PressureRating[1]', 'MaterialGrade', 'EndPreparation[1]', 'FlangeFacing[1]', 'ScheduleThickness'],
            [['MALE', 2, 35, 40001, 331, 5, 5], ['FEMALE', 2, 35, 40001, 441, 5, 10],
             ['WRONG-SIZE', 4, 35, 40001, 331, 5, 5], ['WRONG-RATING', 2, 40, 40001, 331, 5, 5]])))
        for kind in ('spec', 'cat'):
            setattr(self.reference, f'{kind}_file_hash', file_sha256(getattr(self.reference, f'{kind}_file')))
        self.reference.save()
        self.pressure = next(entry for entry in dictionary()[0]['PressureRating'] if entry['longDesc'] == 'CL150')
        self.male = next(entry for entry in dictionary()[0]['EndPreparation'] if entry['shortDesc'] == 'MTE')

    def run_matching(self):
        return self.client.post('/api/v1/spec-customization/matching-run/', {
            'job_id': str(self.job.pk), 'workbook_set_id': str(self.reference.pk), 'codelist_profile': 'BUHASA'}, format='json')

    def review(self, **values):
        return self.client.post('/api/v1/spec-customization/matching-decision-review/',
            {'decision_id': str(ComponentMatchDecision.objects.get(job=self.job).pk), **values}, format='json')

    def test_candidate_precedence_normalization_persistence_and_release_guard(self):
        response = self.run_matching()
        self.assertEqual(response.status_code, 200, response.data)
        decision = ComponentMatchDecision.objects.get(job=self.job)
        self.assertEqual(decision.state, 'ambiguous')
        self.assertEqual({candidate['commodity_code'] for candidate in decision.candidates}, {'MALE', 'FEMALE'})
        self.assertTrue(codelist_release_blockers(self.job))
        self.assertEqual(self.review(review_decision='approved', final_commodity_code='MALE').status_code, 409)
        response = self.review(review_decision='normalized', codelist_selections={'PressureRating': self.pressure})
        self.assertEqual(response.status_code, 200, response.data)
        evidence = response.data['discriminators_matched']['codelists']
        self.assertTrue(any('EndPreparation' in value for value in evidence['differences']))
        self.assertEqual([stage['attribute'] for stage in evidence['stages']][:5], ['Size', 'PressureRating', 'EndPreparation', 'FlangeFacing', 'MaterialsGrade'])
        response = self.review(review_decision='normalized', codelist_selections={'EndPreparation': self.male})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['state'], 'unique_match')
        candidate = response.data['candidates'][0]
        response = self.review(review_decision='approved', final_commodity_code='MALE', candidate_id=candidate['candidate_id'])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(codelist_release_blockers(self.job), [])
        self.assertEqual(self.run_matching().status_code, 200)
        decision.refresh_from_db()
        self.assertEqual(decision.review_decision, 'approved')
        self.assertEqual(codelist_release_blockers(self.job), [])

    def test_unmapped_cannot_be_remapped_around_and_invalid_values_are_rejected(self):
        self.component.end_connection = 'unknown connection'
        self.component.save()
        self.assertEqual(self.run_matching().status_code, 200)
        decision = ComponentMatchDecision.objects.get(job=self.job)
        self.assertEqual(decision.discriminators_matched['codelists']['attributes']['EndPreparation']['status'], 'UNMAPPED_CODELIST')
        self.assertEqual(decision.state, 'ambiguous')
        self.assertEqual(self.review(review_decision='remapped', final_commodity_code='MALE').status_code, 409)
        before = decision.discriminators_matched
        response = self.review(review_decision='normalized', codelist_selections={'EndPreparation': {'shortDesc': 'fake', 'code': 331}})
        self.assertEqual(response.status_code, 409)
        decision.refresh_from_db()
        self.assertEqual(decision.discriminators_matched, before)
        self.assertTrue(codelist_release_blockers(self.job))

    def test_access_guards_apply_to_canonical_review(self):
        self.assertEqual(self.run_matching().status_code, 200)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.review(review_decision='normalized', codelist_selections={'EndPreparation': self.male}).status_code, 403)

    def test_confirmation_atomically_links_source_and_unchanged_rerun_keeps_it(self):
        self.job.document.total_pages = 2
        self.job.document.save(update_fields=['total_pages'])
        self.assertEqual(self.run_matching().status_code, 200)
        response = self.review(review_decision='normalized', codelist_selections={'EndPreparation': self.male})
        candidate = response.data['candidates'][0]
        reference = {'page': 1, 'table_id': 'T6', 'bbox': [0.1, 0.2, 0.9, 0.4], 'coordinate_space': 'normalized'}
        result = self.review(review_decision='approved', final_commodity_code='MALE', candidate_id=candidate['candidate_id'], source_reference=reference)
        self.assertEqual(result.status_code, 200, result.data)
        linked = result.data['discriminators_matched']['source_reference']
        self.assertEqual({key: linked[key] for key in reference}, reference)
        self.assertEqual(linked['document_id'], str(self.job.document_id))
        self.assertEqual(linked['document_sha256'], self.job.document.sha256_hash)
        self.assertEqual(linked['linked_by'], str(self.owner.pk))
        self.assertEqual(self.run_matching().status_code, 200)
        decision = ComponentMatchDecision.objects.get(job=self.job)
        self.assertEqual(decision.discriminators_matched['source_reference'], linked)
        self.assertEqual(decision.review_decision, 'approved')
        refreshed = self.client.get('/api/v1/spec-customization/matching-decisions/', {'job': str(self.job.pk)})
        self.assertEqual(refreshed.data['items'][0]['discriminators_matched']['source_reference'], linked)

    def test_invalid_or_out_of_bounds_source_does_not_approve_or_link(self):
        self.job.document.total_pages = 2
        self.job.document.save(update_fields=['total_pages'])
        self.run_matching()
        response = self.review(review_decision='normalized', codelist_selections={'EndPreparation': self.male})
        candidate = response.data['candidates'][0]
        for locator in ({'page': 3}, {'page': 1, 'bbox': [0, 0, 2, 1], 'coordinate_space': 'normalized'},
                        {'page': 1, 'bbox': [1, 1, 0, 0], 'coordinate_space': 'pdf_points'},
                        {'page': 1, 'bbox': [0, 0, 1, 1]}):
            with self.subTest(locator=locator):
                result = self.review(review_decision='approved', final_commodity_code='MALE', candidate_id=candidate['candidate_id'], source_reference=locator)
                self.assertEqual(result.status_code, 400, result.data)
                decision = ComponentMatchDecision.objects.get(job=self.job)
                self.assertEqual(decision.review_decision, '')
                self.assertNotIn('source_reference', decision.discriminators_matched)

    def test_canonical_edit_can_upgrade_a_legacy_decision(self):
        result = self.client.post('/api/v1/spec-customization/matching-run/', {'job_id': str(self.job.pk), 'workbook_set_id': str(self.reference.pk)}, format='json')
        self.assertEqual(result.status_code, 200)
        result = self.review(review_decision='normalized', codelist_selections={'EndPreparation': self.male})
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['discriminators_matched']['codelists']['profile'], 'BUHASA')
        self.assertEqual(result.data['discriminators_matched']['codelists']['attributes']['EndPreparation']['code'], 331)


class BuhasaReleaseTests(TestCase):
    def setUp(self):
        release_tests.ControlledReleaseTests.setUp(self)

    def test_legacy_frozen_snapshot_and_bytes_do_not_depend_on_dictionary(self):
        with patch('apps.spec_customization.services.buhasa_codelists.dictionary', side_effect=AssertionError('Legacy snapshot must not read BUHASA')):
            release = release_tests.ControlledReleaseTests.frozen(self)
            manifest = release.manifest.copy()
            first = self.client.get(self.root + 'export-spec/')
            self.assertEqual(first.status_code, 200)
            release.refresh_from_db()
            self.assertEqual(release.manifest, manifest)

    def test_unmapped_or_ambiguous_blocks_freeze_even_with_approved_commodity(self):
        for raw in ('unknown connection', 'THD'):
            self.decision.discriminators_matched = {'codelists': normalize({'EndPreparation': raw})}
            self.decision.save()
            validation = self.client.post(self.root + 'releases/validate/')
            self.assertEqual(validation.status_code, 201)
            draft = self.client.post(self.root + 'releases/')
            result = self.client.post(self.root + f"releases/{draft.data['id']}/freeze/")
            self.assertEqual(result.status_code, 409)
            self.assertTrue(any('EndPreparation:' in item for item in result.data['blockers']))
