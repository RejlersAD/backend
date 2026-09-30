"""Central credential resolution without changing project source authority."""
from django.views.decorators.debug import sensitive_variables

from apps.core.ai_credentials import (
    AICredentialUnavailable, get_provider_configuration, resolve_provider_credential,
)


def central_status(provider):
    try:
        return get_provider_configuration(provider)
    except AICredentialUnavailable:
        return {'provider': provider, 'managed': True, 'enabled': False,
                'ready': False, 'key_configured': False, 'model': '', 'revision': 0}


@sensitive_variables()
def central_project_config(project, provider, default_model):
    """Return (managed, configuration); an unavailable managed key never falls back."""
    try:
        key, status = resolve_provider_credential(provider)
    except AICredentialUnavailable:
        return True, None
    if not status['managed']:
        return False, None
    saved = getattr(project, 'ai_settings', None) or {}
    if not status['ready'] or saved.get('enabled') is False:
        return True, None
    if not key:
        return True, None
    return True, {'provider': provider, 'api_key': key, 'managed': True,
                  'model': saved.get('model') or status.get('model') or default_model}
