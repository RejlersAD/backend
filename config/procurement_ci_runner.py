"""Strict, opt-in runner for the two purchase-recommendation CI modules."""

import logging

import sys
import unittest

from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from config.test_runner import NonInteractiveKeepDbRunner
logger = logging.getLogger(__name__)


REQUIRED_TEST_MODULES = (
    'apps.procurement.tests.test_pr_save_submit_api',
    'apps.procurement.tests.test_requisition_concurrency_postgresql',
)


def _test_ids(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from _test_ids(test)
        else:
            yield test.id()


class PurchaseRecommendationCIRunner(NonInteractiveKeepDbRunner):
    """Do not accept SQLite, absent suites, skipped tests or an empty run as green.

    Selected only through --testrunner in the focused CI workflow. The normal
    application's settings and local test runner remain unchanged.
    """

    def build_suite(self, test_labels=None, **kwargs):
        suite = super().build_suite(test_labels, **kwargs)
        test_ids = list(_test_ids(suite))
        missing = [
            module for module in REQUIRED_TEST_MODULES
            if not any(test_id.startswith(module + '.') for test_id in test_ids)
        ]
        if missing:
            raise ImproperlyConfigured(
                'Required purchase-recommendation test modules were not discovered: '
                + ', '.join(missing)
            )
        return suite

    def setup_databases(self, **kwargs):
        if connection.vendor != 'postgresql':
            raise ImproperlyConfigured('Purchase recommendation CI requires PostgreSQL.')
        # Verify the actual service before database creation or test execution.
        # Connection errors propagate and fail the job; no backend fallback.
        connection.ensure_connection()
        with connection.cursor() as cursor:
            cursor.execute('SELECT version()')
            logger.debug('CI PostgreSQL service: %s', cursor.fetchone()[0])
        return super().setup_databases(**kwargs)

    def suite_result(self, suite, result, **kwargs):
        failures = super().suite_result(suite, result, **kwargs)
        if result.skipped:
            logger.debug(f'CI failure: {len(result.skipped)} test(s) unexpectedly skipped; '
                'all focused PostgreSQL tests must execute.')
            failures += len(result.skipped)
        if result.testsRun == 0:
            logger.debug('CI failure: no purchase-recommendation tests executed.')
            failures += 1
        return failures
