"""Isolated tests for usage/activity log retention (SQLite).

    python manage.py test apps.usage_tracking.tests_retention --settings=config.settings_usage_retention_test
"""
from .settings_console_test import *  # noqa: F403

INSTALLED_APPS = [*INSTALLED_APPS, *(a for a in ('apps.activity',) if a not in INSTALLED_APPS)]  # noqa: F405
# Day boundaries are calendar days in this zone (as in production when DJANGO_TIME_ZONE is set).
TIME_ZONE = 'Asia/Dubai'
