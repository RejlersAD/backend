"""Helpers for keeping optional startup work out of test database setup."""

import os
import sys


def is_test_runtime():
    """Return whether Django is running a test command or pytest."""
    if os.environ.get('PYTEST_CURRENT_TEST'):
        return True
    if any(arg.lower() in {'test', 'pytest'} for arg in sys.argv):
        return True
    settings_module = os.environ.get('DJANGO_SETTINGS_MODULE', '').lower()
    return 'test' in settings_module or 'testing' in settings_module
