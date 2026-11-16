"""Test settings wrapper pointing the disposable Postgres at host.docker.internal."""
import os

os.environ.setdefault('RADAI_PID_PG_PORT', '15461')
os.environ.setdefault('RADAI_PID_PG_PASSWORD', 'synthetic-pid-task4-only')

from config.settings_radai_intelligence_postgresql_test import *  # noqa: F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'HOST': 'host.docker.internal', 'PORT': 15461,
        'NAME': 'radai_pid_task4', 'USER': 'radai_pid_task4',
        'PASSWORD': 'synthetic-pid-task4-only', 'CONN_MAX_AGE': 0,
        'OPTIONS': {'connect_timeout': 5, 'application_name': 'radai-pid-task4-tests'},
        'TEST': {'NAME': 'test_radai_pid_task4'},
    },
}