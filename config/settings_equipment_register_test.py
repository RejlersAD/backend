"""Isolated SQLite/model-sync settings for Equipment Register API tests."""
from .settings_release_test import *  # noqa: F403

INSTALLED_APPS = [  # noqa: F405
    *INSTALLED_APPS,
    'apps.pid_analysis',
]
ROOT_URLCONF = 'apps.pid_analysis.test_equipment_register_api'
SILENCED_SYSTEM_CHECKS = ['signals.E001']
