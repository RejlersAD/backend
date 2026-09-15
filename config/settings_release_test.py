"""Functional regressions on isolated SQLite with the affected API routes.

Never use this model-sync test harness to certify applied migrations; migration
state and PostgreSQL-specific behavior must be checked separately.
Module/action guards use settings_permissions_test and its guarded URL table;
functional fixtures here exercise the views' existing object/workflow checks.
"""
from tempfile import TemporaryDirectory

from .settings_database_maintenance_test import *  # noqa: F403

ROOT_URLCONF = 'config.urls_release_test'
INSTALLED_APPS = [  # noqa: F405
    *INSTALLED_APPS,
    'apps.project_organizer',
    'apps.spec_customization',
    'apps.valve_standards',
]
USE_S3 = False
_release_media_directory = TemporaryDirectory(prefix='radai-release-tests-')
MEDIA_ROOT = _release_media_directory.name
DEFAULT_FILE_STORAGE = 'django.core.files.storage.FileSystemStorage'
