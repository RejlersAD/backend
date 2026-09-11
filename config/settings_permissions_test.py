"""Isolated action-authorization integration tests; no external database."""
from .settings_console_test import *  # noqa: F403

INSTALLED_APPS = [*INSTALLED_APPS, 'apps.pid_analysis', 'apps.pfd_quality', 'apps.designiq', 'apps.qhse']  # noqa: F405
ROOT_URLCONF = 'config.urls_permissions_test'
