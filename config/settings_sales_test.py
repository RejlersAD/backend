"""Focused in-memory settings for governed Sales lifecycle tests."""

from .settings_test import *  # noqa: F403

INSTALLED_APPS = [  # noqa: F405
    *INSTALLED_APPS,  # noqa: F405
    'apps.rbac',
    'config.test_app_configs.HRCoreWithoutSignalsConfig',
    'apps.finance',
    'apps.procurement',
    'apps.sales',
]
ROOT_URLCONF = 'config.urls_sales_test'
