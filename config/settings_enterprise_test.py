"""Isolated settings for Team & Enterprise tests. Does not require config/settings.py wiring.

    python manage.py test apps.enterprise --settings=config.settings_enterprise_test
"""
from .settings_radai_intelligence_test import *  # noqa: F403,F401

if 'apps.enterprise' not in INSTALLED_APPS:  # noqa: F405
    INSTALLED_APPS = [*INSTALLED_APPS, 'apps.enterprise']  # noqa: F405

ENABLE_ENTERPRISE_TEAMS = True
