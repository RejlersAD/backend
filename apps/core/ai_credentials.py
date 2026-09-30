"""Shared, fail-closed resolution of administrator-managed provider credentials."""
import base64
import hashlib
from dataclasses import dataclass, field

from cryptography.fernet import Fernet, InvalidToken
from django.apps import apps
from django.conf import settings
from django.db import DatabaseError
from django.views.decorators.debug import sensitive_variables


PROVIDERS = ('openai', 'anthropic', 'gemini')


class AICredentialUnavailable(RuntimeError):
    def __init__(self, reason='registry_unavailable'):
        self.reason = reason if reason in {
            'registry_unavailable', 'encryption_unavailable', 'credential_unreadable',
            'unsupported_provider', 'registry_not_ready',
        } else 'registry_unavailable'
        super().__init__('The configured AI credential is unavailable.')


@sensitive_variables()
def _encryption_material():
    key = getattr(settings, 'AI_CREDENTIAL_ENCRYPTION_KEY', None) or getattr(settings, 'BYOK_ENCRYPTION_KEY', None)
    if isinstance(key, str):
        key = key.strip().encode()
    if not isinstance(key, bytes) or not key.strip():
        raise AICredentialUnavailable('encryption_unavailable')
    return key


def encryption_ready():
    try:
        _encryption_material()
        return True
    except AICredentialUnavailable:
        return False


@sensitive_variables()
def encrypt_api_key(value):
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(_encryption_material()).digest())).encrypt(value.encode()).decode()


@sensitive_variables()
def decrypt_api_key(value):
    material = _encryption_material()
    try:
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material).digest())).decrypt(value.encode()).decode()
    except (InvalidToken, ValueError, TypeError, AttributeError, UnicodeError):
        raise AICredentialUnavailable('credential_unreadable') from None


def _provider_record(provider):
    if provider not in PROVIDERS:
        raise AICredentialUnavailable('unsupported_provider')
    if not apps.ready:
        raise AICredentialUnavailable('registry_not_ready')
    try:
        model = apps.get_model('core', 'AIProviderConfiguration')
        return model.objects.select_related('selected_credential').filter(provider=provider).first()
    except DatabaseError:
        raise AICredentialUnavailable('registry_unavailable') from None


def configuration_metadata(provider, record):
    selected = record.selected_credential if record else None
    valid_key = bool(selected and selected.provider == provider and selected.encrypted_key)
    storage = encryption_ready()
    enabled = bool(record and record.enabled)
    return {
        'provider': provider, 'managed': record is not None, 'enabled': enabled,
        'model': record.model if record else '', 'key_configured': valid_key,
        'revision': record.revision if record else 0,
        'selected_credential_id': str(selected.pk) if selected else None,
        'encryption_ready': storage,
        'ready': bool(enabled and valid_key and selected.enabled and storage),
    }


def get_provider_configuration(provider):
    """Safe metadata only; readiness is not a provider authentication claim."""
    return configuration_metadata(provider, _provider_record(provider))


@sensitive_variables()
def resolve_provider_credential(provider, fallback=''):
    """Resolve on each use; a managed provider never revives a legacy key.

    Call after Django startup, not while importing modules or constructing a
    process-global client. Database/encryption errors fail closed. Legacy values
    are compatible only when this provider has never been centrally managed.
    A callable fallback is evaluated only in that unmanaged case, so missing
    legacy configuration cannot block a valid central credential.
    """
    record = _provider_record(provider)
    metadata = configuration_metadata(provider, record)
    if record is None:
        return (fallback() if callable(fallback) else fallback), metadata
    selected = record.selected_credential
    if not record.enabled or not selected or not selected.enabled:
        return '', metadata
    if selected.provider != provider:
        raise AICredentialUnavailable('credential_unreadable')
    return decrypt_api_key(selected.encrypted_key), metadata


@sensitive_variables()
def get_provider_api_key(provider, fallback=''):
    return resolve_provider_credential(provider, fallback)[0]


@dataclass(frozen=True)
class TestCredential:
    provider: str
    model: str
    api_key: str = field(repr=False)
