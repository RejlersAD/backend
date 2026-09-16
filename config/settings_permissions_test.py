"""Isolated action-authorization integration tests; no external database."""
from .settings_console_test import *  # noqa: F403

INSTALLED_APPS = [*INSTALLED_APPS, 'apps.pid_analysis', 'apps.pfd_quality', 'apps.designiq', 'apps.qhse']  # noqa: F405
ROOT_URLCONF = 'config.urls_permissions_test'
# Optional AI result handlers are outside this isolated authorization suite.
SILENCED_SYSTEM_CHECKS = ['signals.E001']
