"""
Wrench Integration – Encryption helpers.
Uses Fernet symmetric encryption for API key storage.
The secret key is derived from Django's SECRET_KEY so it never needs
a separate environment variable.
"""
import base64
import hashlib
from cryptography.fernet import Fernet
from django.conf import settings


def _get_fernet() -> Fernet:
    """Derive a stable 32-byte Fernet key from Django SECRET_KEY."""
    raw = settings.SECRET_KEY.encode()
    digest = hashlib.sha256(raw).digest()          # 32 bytes
    key = base64.urlsafe_b64encode(digest)          # base64-encode for Fernet
    return Fernet(key)


def encrypt_value(plaintext: str) -> str:
    """Return the Fernet-encrypted, base64 string of *plaintext*."""
    if not plaintext:
        return ''
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt_value(token: str) -> str:
    """Decrypt a Fernet token back to plaintext."""
    if not token:
        return ''
    f = _get_fernet()
    return f.decrypt(token.encode()).decode()
