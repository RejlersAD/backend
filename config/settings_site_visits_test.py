"""Site visit authorization checks in the disposable release-test database."""
from .settings_release_test import *  # noqa: F403

INSTALLED_APPS = [*INSTALLED_APPS, 'apps.site_visits']  # noqa: F405
