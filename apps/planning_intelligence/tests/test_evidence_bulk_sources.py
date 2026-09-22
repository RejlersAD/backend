"""Repair citations with real PDF bytes; never supply missing schedule facts."""
from copy import deepcopy
import hashlib
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from ..services import evidence_bulk_sources as sources
from ..services.parsers import _extract_pdf
from .test_reference_schedule_geometry import pdf_document


class GeometryCitationRepairTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.raw_bytes = pdf_document([
            [(1, 'summary', 'Project source', 4, 25, '06-Jan-26', '30-Jan-26', 0),
             (2, 'START_1', 'Start gate', 14, 0, '06-Jan-26', '', 0),
             (3, 'FINISH_1', 'Finish gate', 14, 0, '', '23-Jan-26', 0)],
            [(4, 'ACT_1', 'Prepare drawing - IFA', 14, 7.5, '07-Jan-26', '22-Jan-26', 0)],
        ])
        cls.text = _extract_pdf(BytesIO(cls.raw_bytes))
        cls.geometry = sources.parse_reference_schedule_pdf(BytesIO(cls.raw_bytes))
        cls.rows = {row['activity_id']: row for row in cls.geometry['activities']}

    def setUp(self):
        self.document = SimpleNamespace(
            pk='source-document-id', source_file_id=35, filename='schedule.pdf',
            integrity_status='verified', extracted_text=self.text,
            file_sha256=hashlib.sha256(self.raw_bytes).hexdigest(),
            text_sha256=hashlib.sha256(self.text.encode('utf-8')).hexdigest(),
        )
        self.verifier = sources.GeometryCitationVerifier()

    def candidate(self, prop='identity', activity='ACT_1'):
        row = self.rows[activity]
        field = {'identity': 'title', 'start_date': 'planned_start_date', 'finish_date': 'planned_finish_date'}[prop]
        node = SimpleNamespace(pk=f'{activity}:{prop}', property=prop,
                               entity_name=row['title'], value=row[field])
        source = {
            'file_id': 35, 'filename': 'schedule.pdf', 'document_version': str(self.document.pk),
            'sha256': self.document.file_sha256, 'text_sha256': self.document.text_sha256,
            'locator': deepcopy(row['source_locator']), 'excerpt': row['raw_text'],
            'verbatim': row['raw_text'], 'quote_verified': False, 'extraction_method': 'pdf',
        }
        return node, source

    def repair(self, node, source, raw_bytes=None):
        return self.verifier.repair(node, source, self.document,
                                    self.raw_bytes if raw_bytes is None else raw_bytes)

    def test_replaces_presentational_pipes_with_exact_original_text_quote(self):
        node, source = self.candidate()
        original = deepcopy(source)
        self.assertNotIn(source['verbatim'], self.document.extracted_text)
        repaired = self.repair(node, source)
        self.assertIsNotNone(repaired)
        self.assertEqual(repaired['value'], node.value)
        self.assertEqual(source, original)
        replacement, proof = repaired['source'], repaired['proof']
        self.assertTrue(replacement['quote_verified'])
        start, end = [replacement['locator'][key] for key in ('character_start', 'character_end')]
        self.assertEqual(self.document.extracted_text[start:end], replacement['verbatim'])
        self.assertIn('ACT_1', replacement['verbatim'])
        self.assertIn(node.value, replacement['verbatim'])
        self.assertNotIn(' | ', replacement['verbatim'])
        self.assertEqual(replacement['locator']['page'], 2)
        self.assertEqual(proof['schema'], sources.SCHEMA)
        self.assertEqual(proof['original_fact_id'], node.pk)
        self.assertEqual(proof['property'], 'identity')
        self.assertEqual(proof['value'], node.value)
        self.assertEqual(proof['file_sha256'], self.document.file_sha256)
        self.assertEqual(proof['text_sha256'], self.document.text_sha256)

    def test_exact_typed_dates_use_the_printed_columns(self):
        for prop, expected in (('start_date', '2026-01-07'), ('finish_date', '2026-01-22')):
            with self.subTest(prop=prop):
                node, source = self.candidate(prop)
                repaired = self.repair(node, source)
                self.assertIsNotNone(repaired)
                self.assertEqual(repaired['value'], expected)
                self.assertEqual(repaired['proof']['field_evidence']['column'], 'start' if prop == 'start_date' else 'finish')

    def test_single_date_milestone_endpoint_is_verified_from_original_geometry(self):
        for activity, prop, opposite in (('START_1', 'start_date', 'finish_date'),
                                         ('FINISH_1', 'finish_date', 'start_date')):
            with self.subTest(activity=activity):
                node, source = self.candidate(prop, activity)
                repaired = self.repair(node, source)
                self.assertIsNotNone(repaired)
                self.assertEqual(repaired['value'], node.value)
                missing, original_source = self.candidate(opposite, activity)
                self.assertIsNone(missing.value)
                self.assertIsNone(self.repair(missing, original_source))
                missing.value = node.value  # Never fill the blank opposite cell.
                self.assertIsNone(self.repair(missing, original_source))

    def test_unknown_or_assumed_duration_units_and_activity_types_are_not_repaired(self):
        node, source = self.candidate()
        for prop, value in (('duration', {'value': 7.5, 'unit': None}),
                             ('duration', {'value': 7.5, 'unit': 'working_days'}),
                             ('duration', {'value': 0, 'unit': 'calendar_days'}),
                             ('activity_type', 'task'), ('activity_type', 'start_milestone'),
                             ('dependencies', []), ('constraints', []), ('scope_complete', True)):
            with self.subTest(prop=prop, value=value):
                node.property, node.value = prop, value
                self.assertIsNone(self.repair(node, source))

    def test_wrong_value_or_title_never_becomes_a_correction(self):
        node, source = self.candidate('start_date')
        node.value = '2026-01-08'
        self.assertIsNone(self.repair(node, source))
        node, source = self.candidate()
        node.value = 'Different drawing - IFA'
        self.assertIsNone(self.repair(node, source))
        node, source = self.candidate('start_date')
        node.entity_name = 'Another activity with the same date'
        self.assertIsNone(self.repair(node, source))

    def test_changed_original_bytes_or_saved_text_are_rejected_even_after_cached_success(self):
        node, source = self.candidate()
        self.assertIsNotNone(self.repair(node, source))
        self.assertIsNone(self.repair(node, source, self.raw_bytes + b'changed'))
        self.document.extracted_text += '\nchanged'
        self.assertIsNone(self.repair(node, source))

    def test_wrong_document_scope_hash_or_integrity_is_rejected(self):
        node, source = self.candidate()
        variants = [('file_id', 99), ('document_version', 'other-document'),
                    ('sha256', 'wrong-original-hash'), ('text_sha256', 'wrong-text-hash')]
        for field, value in variants:
            with self.subTest(field=field):
                wrong = {**source, field: value}
                self.assertIsNone(self.repair(node, wrong))
        self.document.integrity_status = 'unavailable'
        self.assertIsNone(self.repair(node, source))

    def test_missing_wrong_or_nonfinite_geometry_does_not_match_by_title(self):
        node, source = self.candidate()
        for locator in ({}, {'page': 2, 'row': 4},
                        {**source['locator'], 'page': 1},
                        {**source['locator'], 'row': 3},
                        {**source['locator'], 'page': True},
                        {**source['locator'], 'bbox': [0, 0, 10, 10]},
                        {**source['locator'], 'bbox': [0, 0, float('nan'), 10]}):
            with self.subTest(locator=locator):
                self.assertIsNone(self.repair(node, {**source, 'locator': locator}))

    def test_exact_date_cell_bbox_is_supported_without_accepting_adjacent_cell(self):
        node, source = self.candidate('start_date')
        row = self.rows['ACT_1']
        source['locator'] = deepcopy(row['field_evidence']['planned_start_date']['source_locator'])
        self.assertIsNotNone(self.repair(node, source))
        source['locator'] = deepcopy(row['field_evidence']['planned_finish_date']['source_locator'])
        self.assertIsNone(self.repair(node, source))

    def test_geometry_and_text_are_parsed_once_for_many_facts(self):
        with patch.object(sources, 'parse_reference_schedule_pdf', wraps=sources.parse_reference_schedule_pdf) as geometry, patch.object(
            sources, 'parse_reference_schedule_text', wraps=sources.parse_reference_schedule_text,
        ) as saved:
            for activity in ('ACT_1', 'START_1', 'FINISH_1'):
                for prop in ('identity', 'start_date', 'finish_date'):
                    node, source = self.candidate(prop, activity)
                    self.repair(node, source)
        geometry.assert_called_once()
        saved.assert_called_once()

    def test_duplicate_saved_quote_or_row_identity_is_not_arbitrarily_selected(self):
        node, source = self.candidate()
        parsed = sources.parse_reference_schedule_text(self.document.extracted_text)
        quote = next(row['raw_text'] for row in parsed['rows'] if row['activity_id'] == 'ACT_1')
        self.document.extracted_text += '\n' + quote
        self.document.text_sha256 = hashlib.sha256(self.document.extracted_text.encode('utf-8')).hexdigest()
        source['text_sha256'] = self.document.text_sha256
        self.assertIsNone(self.repair(node, source))

    def test_duplicate_original_geometry_row_is_not_arbitrarily_selected(self):
        node, source = self.candidate()
        geometry = deepcopy(self.geometry)
        geometry['rows'].append(deepcopy(self.rows['ACT_1']))
        with patch.object(sources, 'parse_reference_schedule_pdf', return_value=geometry):
            self.assertIsNone(self.repair(node, source))

    def test_contradictory_saved_text_cannot_supply_quote_for_original_pdf_value(self):
        node, source = self.candidate('start_date')
        self.document.extracted_text = self.document.extracted_text.replace('07-Jan-26', '08-Jan-26')
        self.document.text_sha256 = hashlib.sha256(self.document.extracted_text.encode('utf-8')).hexdigest()
        source['text_sha256'] = self.document.text_sha256
        self.assertIsNone(self.repair(node, source))

    def test_provider_or_parser_failure_returns_no_repair(self):
        node, source = self.candidate()
        with patch.object(sources, 'parse_reference_schedule_pdf', side_effect=RuntimeError('invalid PDF')):
            self.assertIsNone(self.repair(node, source))
        self.assertIsNone(self.repair(node, source, b'not-a-pdf'))
        self.assertIsNone(self.repair(node, source, bytearray(self.raw_bytes)))

    def test_repaired_quote_and_proof_do_not_share_mutable_original_metadata(self):
        node, source = self.candidate('start_date')
        source['context_excerpt'] = 'old synthetic row'
        repaired = self.repair(node, source)
        self.assertIsNotNone(repaired)
        self.assertNotIn('context_excerpt', repaired['source'])
        repaired['source']['locator']['bbox'][0] = -100
        repaired['proof']['original_locator']['bbox'][0] = -200
        self.assertGreater(source['locator']['bbox'][0], 0)
        again = self.repair(node, source)
        self.assertGreater(again['source']['locator']['bbox'][0], 0)
