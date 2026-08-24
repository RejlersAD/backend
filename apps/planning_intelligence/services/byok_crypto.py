"""
Planning Intelligence — BYOK encryption helper.

Fernet symmetric encryption prefers the dedicated BYOK_ENCRYPTION_KEY. Existing
RADAI installations historically used SECRET_KEY when the dedicated setting was
absent, so that stable, non-default key remains a compatibility fallback until
stored credentials can be rotated.
"""
import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)


def _raw_encryption_key():
    encryption_key = getattr(settings, 'BYOK_ENCRYPTION_KEY', None)
    if encryption_key:
        return encryption_key
    legacy_key = getattr(settings, 'SECRET_KEY', None)
    if legacy_key and legacy_key != 'django-insecure-change-this-in-production':
        logger.warning(
            '[Planning BYOK] Using legacy SECRET_KEY compatibility encryption. '
            'Configure BYOK_ENCRYPTION_KEY and rotate stored project credentials.'
        )
        return legacy_key
    raise ImproperlyConfigured(
        'BYOK_ENCRYPTION_KEY must be configured before storing or using planning API keys.'
    )


def _get_fernet() -> Fernet:
    encryption_key = _raw_encryption_key()
    if isinstance(encryption_key, str):
        encryption_key = encryption_key.encode()
    fernet_key = base64.urlsafe_b64encode(hashlib.sha256(encryption_key).digest())
    return Fernet(fernet_key)


def is_encryption_configured() -> bool:
    try:
        _raw_encryption_key()
        return True
    except ImproperlyConfigured:
        return False


def encrypt_api_key(raw_key: str) -> str:
    """Encrypt a plaintext API key for storage in PlanningProject.ai_settings."""
    return _get_fernet().encrypt(raw_key.encode()).decode()


def decrypt_api_key(encrypted_key: str) -> str | None:
    """
    Decrypt a stored API key. Returns None (never raises) if the token is
    invalid/corrupt/encrypted with a different key — callers must treat that
    as "no usable key configured" and fall back to deterministic behaviour.
    """
    if not encrypted_key:
        return None
    try:
        return _get_fernet().decrypt(encrypted_key.encode()).decode()
    except (InvalidToken, ValueError, TypeError, ImproperlyConfigured) as exc:
        logger.warning('[Planning BYOK] Failed to decrypt stored API key: %s', exc)
        return None
