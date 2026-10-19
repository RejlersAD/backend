"""Sales verification against an explicitly provisioned disposable PostgreSQL.

Never inherits the application database. Model-sync checks do not certify
deployed migration history; validate migrations separately.
"""
import os
from django.core.exceptions import ImproperlyConfigured

_host = os.environ.get('RADAI_SALES_TEST_PG_HOST', '')
_port = os.environ.get('RADAI_SALES_TEST_PG_PORT', '')
_password = os.environ.get('RADAI_SALES_TEST_PG_PASSWORD', '')
if (_host not in {'127.0.0.1', 'radai_sales_verify_20261019'} or not _port.isdecimal()
        or not _password or (_host == '127.0.0.1' and _port == '5432')):
    raise ImproperlyConfigured('Explicit disposable Sales PostgreSQL host, port and password are required.')

os.environ.update({
    'DATABASE_URL': 'sqlite:///:memory:', 'AIFLOW_ENVIRONMENT': 'testing',
    'ENVIRONMENT': 'testing', 'USE_S3': 'false', 'SPEC_SKIP_CORS_ON_READY': '1',
    'S3_AUTO_APPLY_CORS': '0', 'TEAMS_APPROVAL_WEBHOOK_URL': '',
    'WEB_PUSH_VAPID_PRIVATE_KEY': '', 'WEB_PUSH_VAPID_PUBLIC_KEY': '',
})

from .settings_sales_test import *  # noqa: E402,F403

DATABASES = {'default': {
    'ENGINE': 'django.db.backends.postgresql', 'HOST': _host, 'PORT': _port,
    'NAME': 'radai_sales_verification', 'USER': 'sales_verify', 'PASSWORD': _password,
    'CONN_MAX_AGE': 0,
    # Expanded payroll/procurement model cleanup can exceed 30s on Windows Docker.
    'OPTIONS': {'connect_timeout': 5, 'options': '-c statement_timeout=120000 -c lock_timeout=15000'},
    'TEST': {'NAME': 'test_radai_sales_verification'},
}}
