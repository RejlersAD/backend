"""
Planning Intelligence — BYOK encryption helper.

Self-contained (not imported from / into other apps) to respect RADAI's
feature-isolation rule. Mirrors the proven pattern already used in
apps.pid_verification_v2.views.manage_api_keys — Fernet symmetric encryption
with the key derived from settings.BYOK_ENCRYPTION_KEY (SHA-256 digest,
url-safe base64 encoded so it is always a valid 32-byte Fernet key regardless
of the raw secret's length), falling back to settings.SECRET_KEY with a
logged warning if BYOK_ENCRYPTION_KEY is not configured.
"""
import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

logger = logging.getLogger(__name__)


def _get_fernet() -> Fernet:
    encryption_key = getattr(settings, 'BYOK_ENCRYPTION_KEY', None)
    if not encryption_key:
        # In production, BYOK_ENCRYPTION_KEY MUST be set via environment variable.
        encryption_key = settings.SECRET_KEY
        logger.warning(
            '[Planning BYOK] No BYOK_ENCRYPTION_KEY found in settings — '
            'deriving key from SECRET_KEY as fallback!'
        )
    if isinstance(encryption_key, str):
        encryption_key = encryption_key.encode()
    fernet_key = base64.urlsafe_b64encode(hashlib.sha256(encryption_key).digest())
    return Fernet(fernet_key)


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
    except (InvalidToken, ValueError, TypeError) as exc:
        logger.warning('[Planning BYOK] Failed to decrypt stored API key: %s', exc)
        return None
