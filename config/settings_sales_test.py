"""Focused in-memory settings for governed Sales lifecycle tests."""

from .settings_test import *  # noqa: F403

INSTALLED_APPS = [  # noqa: F405
    *INSTALLED_APPS,  # noqa: F405
    'apps.rbac',
    'config.test_app_configs.HRCoreWithoutSignalsConfig',
    'apps.finance',
    'apps.procurement',
    'apps.sales',
    'apps.payroll',
    'apps.payroll_engine',
    'apps.timesheet',
]
ROOT_URLCONF = 'config.urls_sales_test'
# Optional AI senders are deliberately absent from this isolated registry.
SILENCED_SYSTEM_CHECKS = ['signals.E001']
