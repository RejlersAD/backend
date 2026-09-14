"""Executive reporting tests use an isolated in-memory database."""
from .settings_rbac_test import *  # noqa: F403

INSTALLED_APPS = [*INSTALLED_APPS, 'apps.dashboard', 'apps.qhse']  # noqa: F405
ROOT_URLCONF = 'config.urls_executive_test'
# The reduced app registry intentionally omits optional AI-conversion signal senders.
SILENCED_SYSTEM_CHECKS = ['signals.E001']
