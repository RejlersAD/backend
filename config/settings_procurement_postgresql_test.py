"""PR concurrency tests against an explicitly provisioned disposable PostgreSQL.

Never inherits database credentials from the running application. Like the
functional release harness, this synchronizes current models, not migrations.
See docs/PROCUREMENT_CONCURRENCY_POSTGRESQL.md for the isolated Docker command.
"""
import os

from django.core.exceptions import ImproperlyConfigured


_port = os.environ.get('RADAI_CONCURRENCY_PG_PORT', '')
_password = os.environ.get('RADAI_CONCURRENCY_PG_PASSWORD', '')
if not _port.isdecimal() or not 1024 <= int(_port) <= 65535 or int(_port) == 5432 or not _password:
    raise ImproperlyConfigured(
        'Provision the disposable PR PostgreSQL container and explicitly set '
        'RADAI_CONCURRENCY_PG_PORT (non-5432) and RADAI_CONCURRENCY_PG_PASSWORD.'
    )

# Base settings must not discover the ordinary application's database/storage.
# These settings are only for tests, with synthetic data and mocked delivery.
os.environ.update({
    'DATABASE_URL': 'sqlite:///:memory:',
    'AIFLOW_ENVIRONMENT': 'testing',
    'ENVIRONMENT': 'testing',
    'SECRET_KEY': 'synthetic-postgresql-concurrency-tests-only',
    'USE_S3': 'false',
    'SPEC_SKIP_CORS_ON_READY': '1',
    'S3_AUTO_APPLY_CORS': '0',
    'TEAMS_APPROVAL_WEBHOOK_URL': '',
    'WEB_PUSH_VAPID_PRIVATE_KEY': '',
    'WEB_PUSH_VAPID_PUBLIC_KEY': '',
})

from .settings_release_test import *  # noqa: E402,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'HOST': '127.0.0.1',
        'PORT': _port,
        'NAME': 'radai_pr_concurrency',
        'USER': 'radai_pr_concurrency',
        'PASSWORD': _password,
        'CONN_MAX_AGE': 0,
        'OPTIONS': {
            'connect_timeout': 5,
            'application_name': 'radai-pr-concurrency-tests',
            'options': '-c statement_timeout=20000 -c lock_timeout=15000',
        },
        'TEST': {'NAME': 'test_radai_pr_concurrency'},
    },
}
