"""Source-preservation and reference-coverage regressions from release review."""
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from .services.asme_validation import _interpolate, validate_piping_class
from .services.config import ASME_VALIDATION_CONFIG, HEADER_FOOTER_STRIP_CONFIG
from .services.extraction_service import PaperSpecExtractionService


class RunningFurnitureTests(SimpleTestCase):
    def setUp(self):
        settings_patch = patch.dict(HEADER_FOOTER_STRIP_CONFIG, {
            'enabled': True, 'max_top_lines': 2, 'max_bottom_lines': 1,
            'min_repeat_ratio': 0.6, 'min_line_length': 8,
            'extra_line_patterns': [r'^page\s+\d+\s+of\s+\d+$'],
        })
        settings_patch.start()
        self.addCleanup(settings_patch.stop)

    def test_single_page_keeps_source_text_and_removes_only_explicit_page_counter(self):
        source = 'CLASS A1\nPIPE ASTM A106 GR B\nDesign pressure 19 bar\nSource note retained.'
        self.assertEqual(PaperSpecExtractionService.strip_running_furniture(
            [source + '\nPage 1 of 1']), [source])

    def test_distinct_pages_keep_unique_numeric_content(self):
        pages = ['CLASS A100\nPressure 19 bar\nFirst source note',
                 'CLASS A200\nPressure 20 bar\nSecond source note']
        self.assertEqual(PaperSpecExtractionService.strip_running_furniture(pages), pages)

    def test_genuinely_repeated_headers_are_removed_from_both_pages(self):
        pages = ['Company specification\nCLASS A100\nFirst source note',
                 'Company specification\nCLASS A200\nSecond source note']
        self.assertEqual(PaperSpecExtractionService.strip_running_furniture(pages),
                         ['CLASS A100\nFirst source note', 'CLASS A200\nSecond source note'])

    def test_repeated_header_text_inside_body_is_preserved(self):
        pages = ['Company specification\nCLASS A100\nFirst property\nCompany specification\nFirst note',
                 'Company specification\nCLASS A200\nSecond property\nCompany specification\nSecond note']
        cleaned = PaperSpecExtractionService.strip_running_furniture(pages)
        self.assertEqual(cleaned[0], 'CLASS A100\nFirst property\nCompany specification\nFirst note')
        self.assertEqual(cleaned[1], 'CLASS A200\nSecond property\nCompany specification\nSecond note')


class ASMEReferenceCoverageTests(SimpleTestCase):
    """Synthetic reference numbers verify control flow, not engineering ratings."""
    def setUp(self):
        settings_patch = patch.dict(ASME_VALIDATION_CONFIG, {
            'enabled': True, 'interpolation_enabled': True, 'tolerance_pct': 0,
        })
        settings_patch.start()
        self.addCleanup(settings_patch.stop)
        manager_patch = patch('apps.valve_standards.models.PressureTemperatureRating.objects')
        self.manager = manager_patch.start()
        self.addCleanup(manager_patch.stop)
        self.set_reference_rows([('100', 10), ('200', 5)])
        material_patch = patch(
            'apps.spec_customization.services.asme_validation.resolve_material_group',
            return_value={'group_no': 'synthetic', 'matched_spec': 'TEST',
                          'matched_grade': 'TEST', 'product_form': 'casting', 'score': 2},
        )
        material_patch.start()
        self.addCleanup(material_patch.stop)

    def set_reference_rows(self, rows):
        self.manager.filter.return_value.exclude.return_value = [
            SimpleNamespace(temp_label=label, pressure=pressure) for label, pressure in rows
        ]

    def validate(self, points):
        cls = SimpleNamespace(pressure_rating='CLASS 150', material_grade='TEST',
                              components=SimpleNamespace(all=lambda: []),
                              pt_rating_table=[{'temperature_c': temperature,
                                                'pressure_bar_g': pressure}
                                               for temperature, pressure in points])
        return validate_piping_class(cls)

    def test_temperatures_outside_reference_bounds_have_no_rating(self):
        for temperature in (99, 99.9, 200.1, 1000):
            with self.subTest(temperature=temperature):
                self.assertIsNone(_interpolate('synthetic', 150, temperature))

    def test_exact_and_bracketed_reference_points_still_work(self):
        exact = _interpolate('synthetic', 150, 100)
        interpolated = _interpolate('synthetic', 150, 150)
        self.assertEqual((exact['method'], exact['allowed_bar']), ('exact', 10))
        self.assertEqual((interpolated['method'], interpolated['allowed_bar']), ('interpolated', 7.5))
        self.assertEqual(self.validate([(100, 9), (150, 7)])['status'], 'pass')

    def test_explicit_reference_interval_retains_its_actual_coverage(self):
        self.set_reference_rows([('-29 to 38', 10), ('100', 8)])
        for temperature in (-29, 0, 38):
            with self.subTest(temperature=temperature):
                hit = _interpolate('synthetic', 150, temperature)
                self.assertEqual(hit['allowed_bar'], 10)
                self.assertEqual(hit['bracket'], [-29, 38])
        self.assertIsNone(_interpolate('synthetic', 150, -30))

    def test_out_of_range_point_is_skipped_without_a_pass_claim(self):
        result = self.validate([(1000, 1)])
        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['points_checked'], 0)
        self.assertIsNone(result['points'][0]['ok'])
        self.assertIsNone(result['points'][0]['allowed_bar_g'])

    def test_partial_reference_coverage_cannot_pass_the_whole_table(self):
        result = self.validate([(100, 9), (1000, 1)])
        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['reason'], 'pt_table_incomplete_rating_data')
        self.assertEqual(result['points_checked'], 1)
        self.assertIs(result['points'][0]['ok'], True)
        self.assertIsNone(result['points'][1]['ok'])

    def test_confirmed_exceedance_remains_visible_with_an_uncovered_point(self):
        result = self.validate([(100, 11), (1000, 1)])
        self.assertEqual(result['status'], 'fail')
        self.assertEqual(result['points_failed'], 1)

    def test_missing_reference_data_is_skipped(self):
        self.set_reference_rows([])
        result = self.validate([(100, 1)])
        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['points_checked'], 0)
