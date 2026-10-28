"""Runtime chain resolution for the AI Model hub.

Modules resolve their task's ordered provider:model chain here; credentials
resolve personal → module → org → server-config. Modules may register extra
tasks via register_task(); seeded RADAI tasks live in SEEDED_TASKS (spec §6.4)
and are written to ai_route by a data migration.
"""
import logging

from django.conf import settings
from django.views.decorators.debug import sensitive_variables

logger = logging.getLogger(__name__)

# task_id → {label, module, path, needs[], data_policy}
SEEDED_TASKS = {
    'pid-extraction': {
        'label': 'P&ID extraction',
        'module': 'pid_analysis',
        'path': '/pid-analysis',
        'needs': ['text', 'vision'],
        'data_policy': 'cloud',
    },
    'service-text-alarm-reasoning': {
        'label': 'Service text and alarm reasoning',
        'module': 'maintenance',
        'path': '/maintenance/service-text',
        'needs': ['text', 'reasoning'],
        'data_policy': 'cloud',
    },
    'instrument-index-checks': {
        'label': 'Instrument Index checks',
        'module': 'instrumentation',
        'path': '/instrument-index',
        'needs': ['text', 'json'],
        'data_policy': 'cloud',
    },
    'confidential-client-drawings': {
        'label': 'Confidential client drawings',
        'module': 'drawings',
        'path': '/drawings/confidential',
        'needs': ['text', 'vision'],
        'data_policy': 'onprem',
    },
    'document-search-index': {
        'label': 'Document search index',
        'module': 'documents',
        'path': '/documents/search',
        'needs': ['embedding'],
        'data_policy': 'cloud',
    },
    'enquiry-triage': {
        'label': 'Enquiry triage',
        'module': 'enquiries',
        'path': '/enquiries',
        'needs': ['text'],
        'data_policy': 'cloud',
    },
}

# Runtime registry: seeded defaults + anything modules register later.
TASKS = dict(SEEDED_TASKS)


def register_task(task_id, *, label, module, path='', needs, data_policy='cloud'):
    TASKS[task_id] = {'label': label, 'module': module, 'path': path,
                      'needs': list(needs), 'data_policy': data_policy}


@sensitive_variables()
def _credential_for(provider_id, user=None, module_id=None):
    """Resolve (secret, base_url, extra, scope) personal → module → org → server-config."""
    from .ai_credentials import decrypt_api_key
    from .ai_hub_models import AICredential

    def unwrap(cred, scope):
        try:
            payload = decrypt_api_key(cred.encrypted_secret)
        except Exception:
            return None
        import json
        secret, extra = payload, {}
        if payload.startswith('{'):
            try:
                blob = json.loads(payload)
                secret, extra = blob.get('secret', ''), {k: v for k, v in blob.items() if k != 'secret'}
            except ValueError:
                pass
        return secret, cred.base_url or '', extra, scope

    # personal scope (BYOK per-user keys projected from SldUserAIKey)
    if user is not None and getattr(user, 'is_authenticated', False):
        try:
            from apps.electrical_comparison.models import SldUserAIKey
            alias = {'claude': 'anthropic'}.get(provider_id, provider_id)
            for key_provider in {provider_id, alias}:
                row = SldUserAIKey.objects.filter(user=user, provider=key_provider).first()
                if row:
                    return decrypt_api_key(row.encrypted_key), '', {}, 'personal'
        except Exception:
            pass

    qs = AICredential.objects.filter(provider_id=provider_id, enabled=True, revoked_at__isnull=True)
    if module_id:
        for cred in qs.filter(scope='module'):
            if module_id in (cred.module_ids or []):
                resolved = unwrap(cred, 'module')
                if resolved:
                    return resolved
    cred = qs.filter(scope='org').order_by('created_at').first()
    if cred:
        resolved = unwrap(cred, 'org')
        if resolved:
            return resolved

    server_keys = getattr(settings, 'AI_HUB_SERVER_KEYS', {}) or {}
    entry = server_keys.get(provider_id)
    if isinstance(entry, str) and entry:
        return entry, '', {}, 'server_config'
    if isinstance(entry, dict) and entry.get('secret'):
        extra = {k: v for k, v in entry.items() if k not in ('secret', 'base_url')}
        return entry['secret'], entry.get('base_url', ''), extra, 'server_config'
    return None


def resolve_chain(task_id, user=None, module_id=None):
    """Return ordered [(provider, model, credential_scope), ...] for a task.

    Slots without a usable credential or pointing at disabled/shut_down models
    are skipped, preserving fallback order.
    """
    from .ai_hub_models import AIModel, AIRoute

    route = AIRoute.objects.filter(task_id=task_id).first()
    if route is None:
        return []
    module_id = module_id or route.module
    resolved = []
    for slot in (route.chain or [])[:3]:
        if not slot or ':' not in slot:
            continue
        provider_id, model_id = slot.split(':', 1)
        model = AIModel.objects.filter(provider_id=provider_id, model_id=model_id).first()
        if model is None or not model.enabled or model.status == 'shut_down':
            continue
        if route.data_policy == 'onprem' and model.provider.group != 'self':
            continue
        cred = _credential_for(provider_id, user=user, module_id=module_id)
        if cred is None:
            continue
        resolved.append((provider_id, model_id, cred[3]))
    return resolved


def record_model_used(result, model_used, provider, credential_scope, fallback_index):
    """Attach model-usage metadata to a module's result payload."""
    if isinstance(result, dict):
        result['model_used'] = model_used
        result['model_provider'] = provider
        result['credential_scope'] = credential_scope
        result['fallback_index'] = fallback_index
    return result
