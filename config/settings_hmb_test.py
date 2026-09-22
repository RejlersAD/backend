"""Isolated settings for HMB API and storage unit tests."""
from .settings import *  # noqa: F403

DATABASES['default']['TEST'] = {'NAME': 'test_hmb'}  # noqa: F405
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
CACHES = {
	'default': {
		'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
		'LOCATION': 'hmb-test-cache',
	}
}


class DisableMigrations(dict):
	def __contains__(self, item):
		return True

	def __getitem__(self, item):
		return None


MIGRATION_MODULES = DisableMigrations()