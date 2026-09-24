"""Synthetic PFD artifact/API checks; no live providers, database or storage."""
from .settings_release_test import *  # noqa: F403

INSTALLED_APPS = [*INSTALLED_APPS, 'apps.pfd_converter']  # noqa: F405
ROOT_URLCONF = 'apps.pfd_converter.tests'
