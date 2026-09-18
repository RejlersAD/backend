"""Source-backed deliverables must not be inferred from ambiguous fragments."""
from django.test import SimpleTestCase

from ..models import DocumentIntelligenceRun, PlanningFile
from ..services.deliverable_matching import find_deliverable_match
from ..services.document_intelligence import _extract_file_facts
from ..services.intelligence import _detect_disciplines_and_deliverables


PIPING_SPECIFICATION = 'Piping Material Specification'


class DeliverableMatchingTests(SimpleTestCase):
    def extract(self, text):
        # Unsaved model instances exercise the real extraction/provenance path
        # without changing projects, analysis history or uploaded documents.
        source = PlanningFile(id=41, category='sow', extracted_text=text)
        rows = {'run': DocumentIntelligenceRun(id=23), 'facts': [], '_seen': set()}
        _extract_file_facts(rows, source)
        return [fact for fact in rows['facts'] if fact.fact_type == 'deliverable']

    def assert_no_piping_specification(self, text):
        self.assertIsNone(find_deliverable_match(text, PIPING_SPECIFICATION))
        detected = _detect_disciplines_and_deliverables(text)['piping']
        self.assertNotIn(PIPING_SPECIFICATION, detected['mentioned_in_source'])
        self.assertNotIn(PIPING_SPECIFICATION, [fact.value['name'] for fact in self.extract(text)])
        # Catalogue suggestions remain available; they are not source evidence.
        self.assertIn(PIPING_SPECIFICATION, detected['deliverables'])

    def test_electrical_bulk_standard_is_not_piping_scope_evidence(self):
        self.assert_no_piping_specification(
            'Page 52 of 75\nElectrical standards\nAGES-SP-02-019 Bulk Material Specification',
        )

    def test_generic_material_specifications_do_not_infer_piping_deliverable(self):
        for text in (
            'Electrical material specification is required.',
            'Civil material specifications shall be reviewed.',
            'Piping interfaces are reviewed separately.\nBulk material specification.',
            'Material specification for structural steel.',
        ):
            with self.subTest(text=text):
                self.assert_no_piping_specification(text)

    def test_pms_inside_other_identifiers_is_not_a_deliverable(self):
        for text in ('Configure APMS monitoring.', 'PMSController', 'X_PMS_FLAG', 'PMS2'):
            with self.subTest(text=text):
                self.assert_no_piping_specification(text)

    def test_explicit_piping_titles_and_pms_retain_exact_source_provenance(self):
        for phrase in (
            'Piping Material Specification', 'Piping Material Specifications',
            'PIPING MATERIAL SPEC', 'Piping material specs', 'PMS',
            'Piping\nMaterial\tSpecification',
        ):
            with self.subTest(phrase=phrase):
                text = f'Page 52 of 75\nPrepare {phrase} for review.'
                detected = _detect_disciplines_and_deliverables(text)['piping']
                self.assertIn(PIPING_SPECIFICATION, detected['mentioned_in_source'])
                facts = [fact for fact in self.extract(text) if fact.value['name'] == PIPING_SPECIFICATION]
                self.assertEqual(len(facts), 1)
                fact = facts[0]
                self.assertEqual(fact.source_file_id, 41)
                self.assertEqual(fact.source_locator['matched_term'], phrase)
                self.assertEqual(fact.source_locator['line'], 2)
                self.assertEqual(fact.source_locator['page'], 52)
                self.assertEqual(fact.source_locator['character_start'], text.index(phrase))
                self.assertIn(' '.join(phrase.split()), fact.source_excerpt)

    def test_valid_title_after_unrelated_material_standard_is_used_as_evidence(self):
        text = ('AGES-SP-02-019 Bulk Material Specification\n'
                'Prepare Piping Material Specification for review.')
        facts = [fact for fact in self.extract(text) if fact.value['name'] == PIPING_SPECIFICATION]
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].source_locator['line'], 2)
        self.assertEqual(facts[0].source_locator['matched_term'], PIPING_SPECIFICATION)

    def test_plural_engineering_acronyms_and_titles_still_match(self):
        expected = {
            'Piping & Instrumentation Diagram (P&ID) - Process': 'P&IDs',
            'Process Flow Diagram (PFD)': 'PFDs',
            'Single Line Diagram': 'SLDs',
            'MTO / Bill of Materials': 'MTOs',
            'Equipment List': 'equipment lists',
        }
        for canonical, phrase in expected.items():
            with self.subTest(phrase=phrase):
                text = f'Prepare {phrase} for review.'
                match = find_deliverable_match(text, canonical)
                self.assertIsNotNone(match)
                self.assertEqual(match.group(0), phrase)
                self.assertIn(canonical, [fact.value['name'] for fact in self.extract(text)])

    def test_other_short_aliases_do_not_match_inside_unrelated_words(self):
        for canonical, text in (
            ('Piping & Instrumentation Diagram (P&ID) - Process', 'rapid review'),
            ('Single Line Diagram', 'XSLDController'),
            ('MTO / Bill of Materials', 'bombproof enclosure'),
        ):
            with self.subTest(canonical=canonical):
                self.assertIsNone(find_deliverable_match(text, canonical))
                self.assertNotIn(canonical, [fact.value['name'] for fact in self.extract(text)])
