"""Isolated P&ID Checker authorization tests."""
from .settings_release_test import *  # noqa: F403

# apps.crs may already come from settings_test; add each app only once.
INSTALLED_APPS = [*INSTALLED_APPS, *(app for app in ('apps.pid_verification', 'apps.pid_checker_v2', 'apps.crs')  # noqa: F405
                                     if app not in INSTALLED_APPS)]  # noqa: F405
ROOT_URLCONF = 'config.urls_pid_checker_security_test'
