"""Isolated synthetic Wrench API checks; no live database, Wrench or storage."""
from .settings_release_test import *  # noqa: F403

INSTALLED_APPS = [*INSTALLED_APPS, 'apps.wrench_integration']  # noqa: F405
ROOT_URLCONF = 'apps.wrench_integration.tests'
