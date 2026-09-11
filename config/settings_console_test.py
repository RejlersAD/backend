"""Isolated console pipeline tests using SQLite and local cache."""
from .settings_rbac_test import *  # noqa: F403

INSTALLED_APPS = [
    'apps.hr_core.apps.HrCoreConfig' if app == 'config.test_app_configs.HRCoreWithoutSignalsConfig' else app
    for app in INSTALLED_APPS  # noqa: F405
] + ['apps.usage_tracking', 'apps.onboarding']
