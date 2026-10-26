"""Synthetic layout/dispatch/budget checks; not engineering acceptance data."""
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from .services.config import SPEC_EXTRACTION_CONFIG
from .services.local_tables import LocalTableParser, positioned_rows
from .services.local_extraction import local_capabilities, run_local_engine, _ocr_words
from .services.extraction_service import PaperSpecExtractionService
from .services.data_quality import process_extracted_classes


TABLE = ('PIPING CLASS: CS150\n'
         'Component | Size | Material | Schedule | Ends\n'
         'Pipe | 2 | ASTM A106 GR B | 40 | BW\n'
         'Gate Valve | 1/2 - 2 | ASTM A105 | 150 | RF\n')


def native_pdf(path):
    import fitz
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((36, 40), 'PIPING CLASS: CS150', fontsize=11)
        for y, values in [(70, ['Component', 'Size', 'Material', 'Schedule', 'Ends']),
                          (90, ['Pipe', '2', 'ASTM A106 GR B', '40', 'BW']),
                          (110, ['Gate Valve', '1/2 - 2', 'ASTM A105', '150', 'RF'])]:
            for x, value in zip([36, 160, 260, 410, 500], values):
                page.insert_text((x, y), value, fontsize=9)
        document.save(path)


class LocalTableTests(SimpleTestCase):
    def parse(self, text=TABLE):
        parser = LocalTableParser(SPEC_EXTRACTION_CONFIG['piping_class_header_regex'], 'synthetic')
        parser.read_text(text, 3)
        return parser

    def test_explicit_table_preserves_fields_intervals_and_source(self):
        result = self.parse().result()
        piping_class = result['piping_classes'][0]
        self.assertEqual(piping_class['class_code'], 'CS150')
        self.assertEqual(piping_class['_source_pages'], [3, 3])
        pipe, valve = piping_class['components']
        self.assertEqual(pipe['material_standard'], 'ASTM A106 GR B')
        self.assertEqual(pipe['end_connection'], 'BW')
        self.assertEqual((valve['size_from'], valve['size_to']), ('1/2', '2'))
        self.assertIn('Source page 3, row 3', pipe['notes'])
        self.assertEqual(result['local_report']['issues'], [])

    def test_no_components_in_prose_or_header_only(self):
        self.assertEqual(self.parse('PIPING CLASS: CS150\nPipe ASTM A106 GR B 2 inch').component_count, 0)

    def test_missing_class_or_invalid_size_remains_unresolved(self):
        result = self.parse(TABLE.split('\n', 1)[1]).result()
        self.assertEqual(result['local_report']['components'], 0)
        self.assertEqual(result['local_report']['issues'][0]['reason'], 'missing_class_heading')
        result = self.parse(TABLE.replace('Pipe | 2 |', 'Pipe | unsure |')).result()
        self.assertEqual(result['local_report']['components'], 1)
        self.assertTrue(result['local_report']['issues'])

    def test_class_must_be_repeated_on_next_page(self):
        parser = self.parse()
        parser.read_text(TABLE.split('\n', 1)[1], 4)
        self.assertEqual(parser.component_count, 2)
        self.assertTrue(parser.issues)

    def test_missing_values_and_size_ranges_survive_quality_pipeline(self):
        classes, _ = process_extracted_classes(self.parse().result()['piping_classes'])
        self.assertEqual(len(classes[0]['components']), 2)
        for key in ('material_grade', 'flange_facing', 'corrosion_allowance'):
            self.assertEqual(classes[0][key], '')

    def test_duplicate_headers_are_not_accepted(self):
        self.assertEqual(self.parse(TABLE.replace('Schedule', 'Material')).component_count, 0)

    def test_oversize_field_is_reported_instead_of_truncated(self):
        parser = self.parse(TABLE.replace('ASTM A106 GR B', 'X' * 257))
        self.assertEqual(parser.component_count, 1)
        self.assertEqual(parser.issues[0]['reason'], 'field_exceeds_storage_boundary')

    def test_native_pdf_runs_in_real_bounded_child_without_cloud(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic.pdf'
            native_pdf(path)
            result = run_local_engine(path, 0, 0, 'pymupdf_text', SPEC_EXTRACTION_CONFIG, 30)
        self.assertEqual(result['local_report']['components'], 2)
        self.assertEqual(result['local_report']['issues'], [])

    def test_real_child_is_killed_when_budget_expires(self):
        with self.assertRaises(TimeoutError):
            run_local_engine('not-opened.pdf', 0, 0, 'pymupdf_text', SPEC_EXTRACTION_CONFIG, .00001)

    def test_paddle_requires_preinstalled_models(self):
        config = {**SPEC_EXTRACTION_CONFIG, 'paddle_detection_model_dir': '', 'paddle_recognition_model_dir': ''}
        item = next(item for item in local_capabilities(config)['engines'] if item['id'] == 'paddleocr')
        self.assertFalse(item['available'])

    def test_matrix_uses_populated_cells_and_never_builds_commodity_codes(self):
        def row(y, *words):
            return positioned_rows([(x, y, x + len(text) * 4, y + 8, text) for x, text in words])[0]
        rows = [row(10, (10, 'Class 011412-Z Page 4 of 12')),
                row(30, (10, 'PIPING COMPONENTS')),
                row(50, (200, 'Nominal pipe size')),
                row(70, (200, '15'), (240, '20'), (280, '25')),
                row(90, (10, 'Pipe'), (150, '743005'), (203, '0181'), (283, '0381')),
                row(110, (10, 'Metric units'))]
        parser = self.parse('')
        parser.read_rows(rows, 66)
        components = parser.result()['piping_classes'][0]['components']
        self.assertEqual([item['size_from'] for item in components], ['DN 15', 'DN 25'])
        self.assertEqual(components[0]['material_standard'], '')
        self.assertEqual(components[0]['_source_reference']['cell_code'], '0181')
        self.assertNotIn('commodity_code', components[0])

    def test_rapidocr_positions_are_preserved(self):
        from PIL import Image
        reader = lambda image: ([([[10, 20], [30, 20], [30, 28], [10, 28]], 'Pipe', .9)], None)
        words = _ocr_words('rapidocr', Image.new('RGB', (40, 40)), reader)
        self.assertEqual(words, [(10, 20, 30, 28, 'Pipe')])

    def test_native_render_respects_dimension_limit(self):
        import base64
        import io
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic.pdf'
            native_pdf(path)
            service = PaperSpecExtractionService({'max_image_size': 500})
            self.assertIn('PIPING CLASS', service.extract_text_for_pages(str(path), 0, 0)[0])
            image = Image.open(io.BytesIO(base64.b64decode(service.render_pages_to_jpeg_b64(str(path), 0, 0)[0])))
            self.assertLessEqual(max(image.size), 500)


class LocalDispatchTests(SimpleTestCase):
    def setUp(self):
        self.service = PaperSpecExtractionService({'retry_max': 0})
        self.text = self.enterContext(patch.object(self.service, 'extract_via_text_layer', return_value={'piping_classes': []}))
        self.enterContext(patch.object(self.service, '_configured_engines', return_value=['pymupdf_text', 'rapidocr', 'paddleocr', 'tesseract', 'openai_vision']))
        self.rapid = self.enterContext(patch.object(self.service, 'extract_via_rapidocr', return_value=None))
        self.paddle = self.enterContext(patch.object(self.service, 'extract_via_paddleocr', return_value=None))
        self.tesseract = self.enterContext(patch.object(self.service, 'extract_via_tesseract', return_value=None))
        self.cloud = self.enterContext(patch.object(self.service, 'extract_via_openai'))
        self.enterContext(patch.object(self.service, 'render_pages_to_jpeg_b64', return_value=['synthetic']))

    def test_local_dispatch_before_cloud_even_with_zero_paid_budget(self):
        self.service.config['max_ai_pages_per_job'] = 0
        self.paddle.return_value = {'piping_classes': [{'class_code': 'CS150', 'components': [{'component_type': 'pipe'}]}]}
        self.assertEqual(self.service.extract_chunk('synthetic.pdf', 0, 0)['engine_used'], 'paddleocr')
        self.rapid.assert_called_once()
        self.paddle.assert_called_once()
        self.tesseract.assert_not_called()
        self.cloud.assert_not_called()

    def test_header_only_ocr_does_not_mask_later_component_result(self):
        self.rapid.return_value = {'piping_classes': [{'class_code': 'CS150', 'components': []}]}
        self.tesseract.return_value = {'piping_classes': [{'class_code': 'CS150', 'components': [{'component_type': 'pipe'}]}]}
        self.assertEqual(self.service.extract_chunk('synthetic.pdf', 0, 0)['engine_used'], 'tesseract')

    def test_partial_local_rows_allow_cloud_fallback_without_losing_evidence(self):
        local = {'piping_classes': [{'class_code': 'CS150', 'components': [{'component_type': 'pipe'}]}],
                 'engine_used': 'pymupdf_text', 'local_report': {'issues': [{'page': 1, 'reason': 'unsupported_row'}]}}
        self.text.return_value = local
        self.cloud.return_value = {'piping_classes': [{'class_code': 'CS150', 'components': []}]}
        result = self.service.extract_chunk('synthetic.pdf', 0, 0)
        self.cloud.assert_called_once()
        self.assertEqual(result, local)

    def test_partial_local_rows_survive_structured_cloud_fallback(self):
        self.text.return_value = {'piping_classes': [{'class_code': 'CS150', 'components': [{'component_type': 'pipe'}]}],
                                 'local_report': {'issues': [{'page': 1}]}}
        self.cloud.return_value = {'piping_classes': [{'class_code': 'CS150', 'components': [{'component_type': 'pipe'}, {'component_type': 'valve'}]}]}
        result = self.service.extract_chunk('synthetic.pdf', 0, 0)
        self.assertEqual(result['engine_used'], 'openai_vision')
        self.assertTrue(result['local_report']['issues'])
        self.assertEqual(len(result['piping_classes'][0]['components']), 2)

    def test_failed_paid_call_consumes_budget_once(self):
        self.service.config['max_ai_pages_per_job'] = 1
        self.cloud.return_value = None
        self.service.extract_chunk('synthetic.pdf', 0, 0)
        self.service.extract_chunk('synthetic.pdf', 1, 1)
        self.cloud.assert_called_once()
        self.assertEqual(self.service._ai_pages_used, 1)

    def test_transient_local_error_retried_with_shared_budget(self):
        self.service.config.update(retry_max=2, retry_backoff_base=1)
        with patch.object(self.service, '_extract_chunk_once', side_effect=[OSError(), {'piping_classes': []}]) as attempt, patch('apps.spec_customization.services.extraction_service.time.sleep') as sleep:
            self.service.extract_chunk('synthetic.pdf', 0, 0)
        self.assertEqual(attempt.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_no_retry_after_a_paid_attempt(self):
        self.service.config['retry_max'] = 3
        def failed(*args):
            self.service._cloud_attempted = True
            raise OSError('synthetic')
        with patch.object(self.service, '_extract_chunk_once', side_effect=failed) as attempt:
            with self.assertRaises(OSError):
                self.service.extract_chunk('synthetic.pdf', 0, 0)
        attempt.assert_called_once()

    def test_deadline_prevents_retry_and_sdk_retry_is_disabled(self):
        with patch.object(self.service, 'remaining_seconds', side_effect=TimeoutError()):
            with self.assertRaises(TimeoutError):
                self.service.extract_chunk('synthetic.pdf', 0, 0)
        from unittest.mock import Mock
        client = Mock()
        self.service._chunk_deadline = None
        self.service._bounded_client(client)
        self.assertEqual(client.with_options.call_args.kwargs['max_retries'], 0)


class LocalAcceptanceTests(SimpleTestCase):
    def test_label_comparison_rejects_wrong_source_or_unreviewed_labels(self):
        from scripts.benchmark_s3d_local import compare_labels, FIELDS
        labels = {'source_sha256': 'a' * 64, 'label_status': 'unreviewed'}
        with self.assertRaises(ValueError):
            compare_labels(labels, 'b' * 64, [])
        with self.assertRaises(ValueError):
            compare_labels(labels, 'a' * 64, [])
        expected = {field: '' for field in FIELDS}
        expected.update(class_code='SYNTHETIC', component_type='pipe', size_from='2')
        labels.update(label_status='engineering_reviewed', reviewer='synthetic fixture reviewer',
                      reviewed_at='2026-10-26', components=[expected])
        report = compare_labels(labels, 'a' * 64, [expected, {**expected, 'size_from': '3'}])
        self.assertEqual((report['exact_matches'], report['missing'], report['unexpected']), (1, 0, 1))

    def test_pilot_package_rejects_drafts_and_tampered_workbooks(self):
        import hashlib
        from scripts.check_s3d_pilot_package import verify_package
        with tempfile.TemporaryDirectory() as directory:
            spec, cat = Path(directory) / 'SPEC.xlsx', Path(directory) / 'CAT.xlsx'
            spec.write_bytes(b'synthetic spec')
            cat.write_bytes(b'synthetic cat')
            release = {'id': 'synthetic', 'state': 'draft', 'manifest': {
                'input_sha256': 'a' * 64, 'reference_set_hash': 'b' * 64,
                'spec_sha256': hashlib.sha256(spec.read_bytes()).hexdigest(),
                'cat_sha256': hashlib.sha256(cat.read_bytes()).hexdigest()}}
            with self.assertRaises(ValueError):
                verify_package(release, spec, cat)
            release['state'] = 'frozen'
            self.assertEqual(verify_package(release, spec, cat)['target_import'], 'not_executed')
            cat.write_bytes(b'tampered')
            with self.assertRaises(ValueError):
                verify_package(release, spec, cat)

    def test_config_requires_authentication_and_reports_local_capabilities(self):
        from types import SimpleNamespace
        from rest_framework.test import APIRequestFactory, force_authenticate
        from .views import config_view
        factory = APIRequestFactory()
        self.assertIn(config_view(factory.get('/config/')).status_code, (401, 403))
        request = factory.get('/config/')
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        response = config_view(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['processing_limits']['max_chunks_parallel'], 1)
        self.assertFalse(response.data['processing_limits']['automatic_paid_retries'])
        self.assertFalse(response.data['local_extraction']['engineering_acceptance_verified'])


class LocalOutcomeTests(TestCase):
    def setUp(self):
        from . import tests_extraction_recovery as recovery
        recovery.ExtractionOutcomeTests.setUp(self)

    def test_unhandled_local_rows_are_partial_and_diagnostics_persist(self):
        from . import tests_extraction_recovery as recovery
        from .services.completeness import extraction_completeness
        self.service.extract_chunk.return_value = {'piping_classes': [], 'engine_used': 'pymupdf_text',
            'local_report': {'issues': [{'page': 1, 'reason': 'unsupported_component_or_size'}]}}
        recovery.ExtractionOutcomeTests.run_with_classes(self, [{'class_code': 'SYN150', 'components': [{'component_type': 'pipe'}]}])
        self.assertEqual(extraction_completeness(self.job)['state'], 'partial')
        self.assertTrue(self.job.config_snapshot['extraction_outcome']['local_reports'])
