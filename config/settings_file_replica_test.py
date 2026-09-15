"""Replica integration checks use a private SQLite database, never live data."""
from .settings_rbac_test import *  # noqa: F403

INSTALLED_APPS = [  # noqa: F405
    'config.file_replica_test_apps.ReplicaRBACConfig' if app == 'apps.rbac' else app
    for app in INSTALLED_APPS
] + ['apps.file_replica']
ROOT_URLCONF = 'config.urls_file_replica_test'
USE_S3 = False
FILE_REPLICA_ROOT = BASE_DIR / 'private' / 'replica-tests'  # noqa: F405
REST_FRAMEWORK = {**REST_FRAMEWORK, 'DEFAULT_THROTTLE_CLASSES': []}  # noqa: F405
