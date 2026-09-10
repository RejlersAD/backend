"""Isolated RBAC integration tests; never use the running application's DB."""
from .settings_leave_test import *  # noqa: F403

INSTALLED_APPS = [*INSTALLED_APPS, 'apps.invoice_tracker']  # noqa: F405
ROOT_URLCONF = 'config.urls_rbac_test'
