"""Task 4 disposable PostgreSQL migration and locking verification only."""
import os

from django.core.exceptions import ImproperlyConfigured

_port = os.environ.get('RADAI_PID_PG_PORT', '')
_password = os.environ.get('RADAI_PID_PG_PASSWORD', '')
if not _port.isdecimal() or int(_port) == 5432 or not _password:
    raise ImproperlyConfigured('Set an isolated non-5432 RADAI_PID_PG_PORT and RADAI_PID_PG_PASSWORD.')

os.environ.update({
    'DATABASE_URL': 'sqlite:///:memory:',
    'AIFLOW_ENVIRONMENT': 'testing',
    'ENVIRONMENT': 'testing',
    'USE_S3': 'false',
})

from .settings_pid_checker_security_test import *  # noqa: E402,F403

del MIGRATION_MODULES  # noqa: F821 - apply real historical migrations
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'HOST': '127.0.0.1', 'PORT': _port,
        'NAME': 'radai_pid_task4', 'USER': 'radai_pid_task4',
        'PASSWORD': _password, 'CONN_MAX_AGE': 0,
        'OPTIONS': {'connect_timeout': 5, 'application_name': 'radai-pid-task4-tests'},
        'TEST': {'NAME': 'test_radai_pid_task4'},
    },
}
