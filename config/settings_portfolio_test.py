"""Isolated portfolio import, synchronization and executive API regressions."""
from .settings_executive_test import *  # noqa: F403

INSTALLED_APPS = [*INSTALLED_APPS, 'apps.portfolio']  # noqa: F405
ROOT_URLCONF = 'config.urls_portfolio_test'
PORTFOLIO_SYNC_ENABLED = False
