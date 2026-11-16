"""Isolated P&ID Checker authorization tests."""
from .settings_release_test import *  # noqa: F403

INSTALLED_APPS = [*INSTALLED_APPS, 'apps.pid_verification', 'apps.pid_checker_v2', 'apps.crs']  # noqa: F405
ROOT_URLCONF = 'config.urls_pid_checker_security_test'
