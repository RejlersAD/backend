"""SLD BYOK key encryption — mirrors apps.planning_intelligence.services.
byok_crypto's contract exactly (Fernet; BYOK_ENCRYPTION_KEY preferred, with
the legacy SECRET_KEY compatibility fallback). Kept local per this
codebase's per-app isolation convention for BYOK features.
"""
import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

logger = logging.getLogger(__name__)


def _raw_key():
    key = getattr(settings, 'BYOK_ENCRYPTION_KEY', None) or getattr(settings, 'SECRET_KEY', None)
    if not key:
        return None
    if not getattr(settings, 'BYOK_ENCRYPTION_KEY', None):
        logger.warning('[SLD BYOK] Using SECRET_KEY compatibility encryption — configure BYOK_ENCRYPTION_KEY.')
    return key.encode() if isinstance(key, str) else key


def encryption_ready():
    return _raw_key() is not None


def _fernet():
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(_raw_key()).digest()))


def encrypt_api_key(raw: str) -> str:
    return _fernet().encrypt(raw.encode()).decode()


def decrypt_api_key(value: str):
    """None (never raises) when the token is unreadable — callers treat
    that as 'no usable key'."""
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        logger.warning('[SLD BYOK] stored key undecryptable')
        return None
