"""Isolated Data Mining functional/API checks; no live database or storage."""
from .settings_release_test import *  # noqa: F403

INSTALLED_APPS = [*INSTALLED_APPS, 'apps.data_mining', 'apps.wrench_integration']  # noqa: F405
ROOT_URLCONF = 'apps.data_mining.tests'
