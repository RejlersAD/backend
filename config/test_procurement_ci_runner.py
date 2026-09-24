"""Database-free controls for the opt-in purchase recommendation CI gates.

Run with ``python -m unittest config.test_procurement_ci_runner``. These checks
exercise the runner's exit decisions; they do not verify PostgreSQL row locks.
"""

import unittest
from contextlib import redirect_stderr
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.exceptions import ImproperlyConfigured
from django.db import OperationalError
from django.test.runner import DiscoverRunner

from config.procurement_ci_runner import (
    REQUIRED_TEST_MODULES,
    PurchaseRecommendationCIRunner,
)


def synthetic_case(module, outcome='pass'):
    def test_gate(self):
        if outcome == 'skip':
            self.skipTest('Synthetic PostgreSQL test was unexpectedly skipped')
        if outcome == 'failure':
            self.fail('Synthetic assertion failure')
        if outcome == 'error':
            raise RuntimeError('Synthetic test error')

    if outcome == 'unexpected_success':
        test_gate = unittest.expectedFailure(test_gate)
    case_class = type('SyntheticGateCase', (unittest.TestCase,), {
        '__module__': module,
        'test_gate': test_gate,
    })
    return case_class('test_gate')


class PurchaseRecommendationCIRunnerTests(unittest.TestCase):
    def setUp(self):
        self.runner = PurchaseRecommendationCIRunner(verbosity=0)

    def result_status(self, *cases):
        result = unittest.TestSuite(cases).run(unittest.TestResult())
        with redirect_stderr(StringIO()):
            status = self.runner.suite_result(None, result)
        return status

    def test_successful_tests_return_success(self):
        cases = [synthetic_case(module) for module in REQUIRED_TEST_MODULES]
        self.assertEqual(self.result_status(*cases), 0)

    def test_skip_in_either_required_module_fails(self):
        for module in REQUIRED_TEST_MODULES:
            with self.subTest(module=module):
                cases = [synthetic_case(module, 'skip'), synthetic_case(module)]
                self.assertGreater(self.result_status(*cases), 0)

    def test_empty_result_fails(self):
        self.assertGreater(self.result_status(), 0)

    def test_failures_errors_and_unexpected_successes_still_fail(self):
        for outcome in ('failure', 'error', 'unexpected_success'):
            with self.subTest(outcome=outcome):
                self.assertGreater(
                    self.result_status(synthetic_case(REQUIRED_TEST_MODULES[0], outcome)),
                    0,
                )

    def test_nested_suite_with_both_required_modules_is_accepted(self):
        suite = unittest.TestSuite([
            unittest.TestSuite([synthetic_case(module)])
            for module in REQUIRED_TEST_MODULES
        ])
        with patch.object(DiscoverRunner, 'build_suite', return_value=suite):
            self.assertIs(self.runner.build_suite(REQUIRED_TEST_MODULES), suite)

    def test_missing_either_required_module_is_rejected(self):
        for omitted in REQUIRED_TEST_MODULES:
            with self.subTest(omitted=omitted):
                suite = unittest.TestSuite([
                    synthetic_case(module)
                    for module in REQUIRED_TEST_MODULES if module != omitted
                ])
                with patch.object(DiscoverRunner, 'build_suite', return_value=suite):
                    with self.assertRaises(ImproperlyConfigured):
                        self.runner.build_suite(REQUIRED_TEST_MODULES)

    def test_empty_discovery_is_rejected(self):
        with patch.object(DiscoverRunner, 'build_suite', return_value=unittest.TestSuite()):
            with self.assertRaises(ImproperlyConfigured):
                self.runner.build_suite(REQUIRED_TEST_MODULES)

    def test_non_postgresql_backend_is_rejected_before_database_setup(self):
        # Supply the replacement explicitly so mock does not inspect Django's
        # lazy connection proxy and accidentally load application settings.
        with patch('config.procurement_ci_runner.connection', new=MagicMock()) as database, \
                patch.object(DiscoverRunner, 'setup_databases') as setup:
            database.vendor = 'sqlite'
            with self.assertRaisesRegex(ImproperlyConfigured, 'PostgreSQL'):
                self.runner.setup_databases()
            setup.assert_not_called()

    def test_unavailable_postgresql_is_rejected_before_database_setup(self):
        with patch('config.procurement_ci_runner.connection', new=MagicMock()) as database, \
                patch.object(DiscoverRunner, 'setup_databases') as setup:
            database.vendor = 'postgresql'
            database.ensure_connection.side_effect = OperationalError('Synthetic connection refusal')
            with self.assertRaises(OperationalError):
                self.runner.setup_databases()
            setup.assert_not_called()

    def test_postgresql_query_failure_is_not_ignored(self):
        with patch('config.procurement_ci_runner.connection', new=MagicMock()) as database, \
                patch.object(DiscoverRunner, 'setup_databases') as setup:
            database.vendor = 'postgresql'
            database.cursor.return_value.__enter__.return_value.execute.side_effect = (
                OperationalError('Synthetic disconnected database')
            )
            with self.assertRaises(OperationalError):
                self.runner.setup_databases()
            setup.assert_not_called()


if __name__ == '__main__':
    unittest.main()
