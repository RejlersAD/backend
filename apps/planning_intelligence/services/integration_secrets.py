"""Dedicated fail-closed encryption for outbound integration credentials."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def is_configured():
    return bool(getattr(settings, 'PLANNING_INTEGRATION_ENCRYPTION_KEY', None))


def _fernet():
    key = getattr(settings, 'PLANNING_INTEGRATION_ENCRYPTION_KEY', None)
    if not key:
        raise ImproperlyConfigured('PLANNING_INTEGRATION_ENCRYPTION_KEY is not configured.')
    if isinstance(key, str):
        key = key.encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key).digest()))


def encrypt_secret(value):
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value):
    if not value:
        return ''
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError, TypeError, ImproperlyConfigured):
        return ''
