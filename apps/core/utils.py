"""
Utility functions for the core app.
Smart helper functions for common operations.
"""
import hashlib
import secrets
from typing import Optional


def generate_random_string(length: int = 32) -> str:
    """Generate a cryptographically secure random string."""
    return secrets.token_urlsafe(length)


def hash_string(value: str, algorithm: str = 'sha256') -> str:
    """Hash a string using the specified algorithm."""
    hasher = hashlib.new(algorithm)
    hasher.update(value.encode('utf-8'))
    return hasher.hexdigest()


def truncate_string(text: str, max_length: int = 100, suffix: str = '...') -> str:
    """Truncate a string to a maximum length."""
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix
