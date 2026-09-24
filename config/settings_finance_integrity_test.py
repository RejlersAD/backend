"""F06 guarded invoice checks on isolated SQLite and temporary local storage.

Model synchronization does not certify deployed migrations or PostgreSQL locks.
"""
from .settings_release_test import *  # noqa: F403

ROOT_URLCONF = 'apps.finance.tests_invoice_field_integrity'
