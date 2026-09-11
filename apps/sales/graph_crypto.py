"""Fail-closed encryption for delegated Microsoft Graph refresh tokens."""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def is_configured():
    return bool(getattr(settings, 'SALES_GRAPH_TOKEN_ENCRYPTION_KEY', None))


def _fernet():
    raw_key = getattr(settings, 'SALES_GRAPH_TOKEN_ENCRYPTION_KEY', None)
    if not raw_key:
        raise ImproperlyConfigured('SALES_GRAPH_TOKEN_ENCRYPTION_KEY is not configured.')
    if isinstance(raw_key, str):
        raw_key = raw_key.encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw_key).digest()))


def encrypt_token(value):
    if not value:
        return ''
    return _fernet().encrypt(value.encode()).decode()


def decrypt_token(value):
    if not value:
        return ''
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError, TypeError, ImproperlyConfigured):
        return ''
