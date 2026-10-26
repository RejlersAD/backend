"""Release alignment regressions on isolated SQLite, never application data.

Include the engineering apps pulled from Development so their model-based
tests can run alongside the existing release registry. Migrations remain
disabled here; PostgreSQL history/schema verification is a separate check.
"""
from .settings_release_test import *  # noqa: F403

INSTALLED_APPS = [  # noqa: F405
    *INSTALLED_APPS,
    'apps.valve_mto',
    'apps.electrical_comparison',
]
