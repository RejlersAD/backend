"""Check replica migration state without a live database.

Apply migrations on PostgreSQL: core's existing dependency migrations use
PostgreSQL-specific SQL and cannot be executed against SQLite.
"""
from .settings_file_replica_test import *  # noqa: F403

INSTALLED_APPS = [
    'django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions',
    'rest_framework', 'apps.users', 'apps.core', 'apps.file_replica',
]
MIGRATION_MODULES = {}
