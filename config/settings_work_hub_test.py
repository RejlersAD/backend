"""Work Hub checks use isolated SQLite and the optional activity source."""
from .settings_executive_test import *  # noqa: F403

INSTALLED_APPS = [*INSTALLED_APPS, 'apps.activity']  # noqa: F405
ROOT_URLCONF = 'config.urls_work_hub_test'
