"""Protocol adapters for the AI Model hub catalogue sync.

Each adapter returns normalised rows:
    {model_id, status?, status_note?, capabilities?, context_tokens?,
     price_in_per_m?, price_out_per_m?, maker?}
and raises NotConfigured when credentials/base URL are absent (sync marks the
source 'skipped'). Secrets are never logged; the audit helper redacts
Authorization/x-api-key/api-key values.
"""
import ipaddress
import logging
import socket

import requests
from django.conf import settings
from django.views.decorators.debug import sensitive_variables

logger = logging.getLogger(__name__)

TIMEOUT = 20
_METADATA_HOSTS = {'metadata.google.internal', 'metadata', 'instance-data'}
_BLOCKED_NETWORKS = [ipaddress.ip_network('169.254.0.0/16')]
SENSITIVE_HEADERS = ('authorization', 'x-api-key', 'api-key')


class NotConfigured(RuntimeError):
    """Credential or endpoint configuration missing for this provider."""


class AdapterError(RuntimeError):
    pass


def redact(mapping):
    """Return a copy of a header/detail mapping with secrets redacted."""
    safe = {}
    for key, value in (mapping or {}).items():
        safe[key] = '***' if str(key).lower() in SENSITIVE_HEADERS else value
    return safe


def _host_allowed(url):
    from urllib.parse import urlparse
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or '').lower()
    if not host:
        raise AdapterError('invalid_base_url')
    allow_http = set(getattr(settings, 'AI_HUB_HTTP_ALLOW_HOSTS',
                             ('localhost', '127.0.0.1', 'host.docker.internal')))
    if scheme == 'http':
        if host not in allow_http:
            raise AdapterError('http_not_allowed')
    elif scheme != 'https':
        raise AdapterError('scheme_not_allowed')
    if host in _METADATA_HOSTS:
        raise AdapterError('metadata_host_blocked')
    try:
        addr = ipaddress.ip_address(host)
        if any(addr in net for net in _BLOCKED_NETWORKS):
            raise AdapterError('link_local_blocked')
    except ValueError:
        pass  # hostname, not a literal IP
    else:
        return
    # Resolve hostnames once and block link-local targets (SSRF guard).
    try:
        for info in socket.getaddrinfo(host, None):
            addr = ipaddress.ip_address(info[4][0])
            if any(addr in net for net in _BLOCKED_NETWORKS):
                raise AdapterError('link_local_blocked')
    except socket.gaierror:
        raise AdapterError('unresolvable_host')


@sensitive_variables('secret')
def _get(url, headers=None, params=None):
    _host_allowed(url)
    try:
        response = requests.get(url, headers=headers or {}, params=params or {},
                                timeout=TIMEOUT, allow_redirects=False)
    except AdapterError:
        raise
    except requests.RequestException as exc:
        raise AdapterError(f'request_failed:{type(exc).__name__}') from None
    if response.is_redirect or response.is_permanent_redirect:
        raise AdapterError('redirect_refused')
    if response.status_code == 401 or response.status_code == 403:
        raise AdapterError('authentication_failed')
    if response.status_code >= 400:
        raise AdapterError(f'http_{response.status_code}')
    try:
        return response.json()
    except ValueError:
        raise AdapterError('invalid_response') from None


def _join(base_url, endpoint):
    return base_url.rstrip('/') + '/' + endpoint.lstrip('/')


def _rows_from_ids(ids, extra=None):
    return [{'model_id': mid, **(extra or {})} for mid in sorted(set(ids))]


# ── protocol adapters ────────────────────────────────────────────────────────

@sensitive_variables('secret')
def list_openai(provider, secret=None, base_url=None):
    if not secret:
        raise NotConfigured()
    base = base_url or provider.default_base_url
    data = _get(_join(base, provider.list_endpoint or '/models'),
                headers={'Authorization': f'Bearer {secret}'})
    items = data.get('data') if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise AdapterError('invalid_response')
    return _rows_from_ids(i.get('id') for i in items if isinstance(i, dict) and i.get('id'))


# OpenAI-compatible gateways and self-hosted servers share the /models shape.
list_openai_compatible = list_openai


@sensitive_variables('secret')
def list_openai_compatible_optional_key(provider, secret=None, base_url=None):
    """Self-hosted openai_compatible (vLLM, custom): key optional."""
    base = base_url or provider.default_base_url
    if not base:
        raise NotConfigured()
    headers = {'Authorization': f'Bearer {secret}'} if secret else {}
    data = _get(_join(base, provider.list_endpoint or '/models'), headers=headers)
    items = data.get('data') if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise AdapterError('invalid_response')
    return _rows_from_ids(i.get('id') for i in items if isinstance(i, dict) and i.get('id'))


@sensitive_variables('secret')
def list_anthropic(provider, secret=None, base_url=None):
    if not secret:
        raise NotConfigured()
    base = base_url or provider.default_base_url
    data = _get(_join(base, provider.list_endpoint or '/v1/models'),
                headers={'x-api-key': secret, 'anthropic-version': '2023-06-01'})
    items = data.get('data') if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise AdapterError('invalid_response')
    return _rows_from_ids(i.get('id') for i in items if isinstance(i, dict) and i.get('id'))


@sensitive_variables('secret')
def list_gemini(provider, secret=None, base_url=None):
    if not secret:
        raise NotConfigured()
    base = base_url or provider.default_base_url
    data = _get(_join(base, provider.list_endpoint or '/v1beta/models'),
                headers={'x-goog-api-key': secret})
    items = data.get('models') if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise AdapterError('invalid_response')
    rows = []
    for item in items:
        name = (item.get('name') or '').replace('models/', '')
        if not name:
            continue
        methods = item.get('supportedGenerationMethods') or []
        if 'generateContent' not in methods and 'generateText' not in methods:
            continue
        row = {'model_id': name, 'capabilities': ['text', 'json'],
               'context_tokens': item.get('inputTokenLimit')}
        lowered = name.lower()
        if any(tag in lowered for tag in ('vision', 'flash', 'pro', 'ultra', 'gemini')):
            row['capabilities'].append('vision')
        if 'image' in lowered:
            row['capabilities'] = ['image', 'text']
        rows.append(row)
    return rows


@sensitive_variables('secret')
def list_azure(provider, secret=None, base_url=None, extra=None):
    base = base_url or provider.default_base_url
    if not secret or not base:
        raise NotConfigured()
    api_version = (extra or {}).get('api_version') or '2024-10-21'
    data = _get(_join(base, f'/openai/models?api-version={api_version}'),
                headers={'api-key': secret})
    items = data.get('data') if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise AdapterError('invalid_response')
    return _rows_from_ids(i.get('id') for i in items if isinstance(i, dict) and i.get('id'))


@sensitive_variables('secret')
def list_bedrock(provider, secret=None, base_url=None, extra=None):
    extra = extra or {}
    access_key = extra.get('access_key') or secret
    secret_key = extra.get('secret_key')
    region = extra.get('region') or 'us-east-1'
    if not access_key or not secret_key:
        raise NotConfigured()
    try:
        import boto3
    except ImportError:
        raise AdapterError('provider_dependency_missing') from None
    try:
        client = boto3.client('bedrock', region_name=region,
                              aws_access_key_id=access_key,
                              aws_secret_access_key=secret_key)
        response = client.list_foundation_models()
    except Exception as exc:
        raise AdapterError(f'request_failed:{type(exc).__name__}') from None
    rows = []
    for summary in response.get('modelSummaries', []):
        mid = summary.get('modelId')
        if not mid:
            continue
        caps = ['text']
        if summary.get('inputModalities') and 'IMAGE' in summary['inputModalities']:
            caps.append('vision')
        rows.append({'model_id': mid, 'capabilities': caps,
                     'status': 'ga' if summary.get('modelLifecycle', {}).get('status') == 'ACTIVE'
                     else 'deprecated',
                     'status_note': summary.get('modelLifecycle', {}).get('status', '')})
    return rows


@sensitive_variables('secret')
def list_vertex(provider, secret=None, base_url=None, extra=None):
    extra = extra or {}
    project = extra.get('project')
    token = secret
    if not token or not project:
        raise NotConfigured()
    base = base_url or provider.default_base_url
    endpoint = provider.list_endpoint or '/v1/publishers/google/models'
    data = _get(_join(base, f'{endpoint.lstrip("/")}?project={project}'),
                headers={'Authorization': f'Bearer {token}'})
    items = data.get('publisherModels') or data.get('models') if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise AdapterError('invalid_response')
    rows = []
    for item in items:
        name = (item.get('name') or item.get('versionId') or '')
        mid = name.rsplit('/', 1)[-1]
        if mid:
            rows.append({'model_id': mid})
    return rows


def list_ollama(provider, secret=None, base_url=None):
    base = base_url or provider.default_base_url
    if not base:
        raise NotConfigured()
    data = _get(_join(base, provider.list_endpoint or '/api/tags'))
    items = data.get('models') if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise AdapterError('invalid_response')
    return _rows_from_ids((i.get('name') for i in items if isinstance(i, dict) and i.get('name')),
                          extra={'weights': 'open'})


def list_tgi(provider, secret=None, base_url=None):
    base = base_url or provider.default_base_url
    if not base:
        raise NotConfigured()
    data = _get(_join(base, provider.list_endpoint or '/info'))
    model_id = data.get('model_id') if isinstance(data, dict) else None
    if not model_id:
        raise AdapterError('invalid_response')
    return [{'model_id': model_id, 'weights': 'open'}]


ADAPTERS = {
    'openai': list_openai,
    'openai_compatible': list_openai_compatible_optional_key,
    'anthropic': list_anthropic,
    'gemini': list_gemini,
    'azure': list_azure,
    'bedrock': list_bedrock,
    'vertex': list_vertex,
    'ollama': list_ollama,
    'tgi': list_tgi,
}


def list_models(provider, secret=None, base_url=None, extra=None):
    if provider.protocol not in ADAPTERS:
        raise AdapterError('unsupported_protocol')
    if provider.protocol in ('azure', 'bedrock', 'vertex'):
        return ADAPTERS[provider.protocol](provider, secret=secret, base_url=base_url, extra=extra)
    if provider.protocol in ('ollama', 'tgi'):
        return ADAPTERS[provider.protocol](provider, secret=secret, base_url=base_url)
    if provider.protocol == 'openai_compatible' and provider.group == 'self':
        # vLLM / custom servers: key optional, base URL required
        return list_openai_compatible_optional_key(provider, secret=secret, base_url=base_url)
    # hosted openai / openai_compatible / anthropic / gemini: key required
    return ADAPTERS[provider.protocol](provider, secret=secret, base_url=base_url)


def audit(actor, action, provider='', credential_id='', detail=None):
    """Write an AIAudit row; sensitive keys are redacted before storage."""
    from .ai_hub_models import AIAudit
    try:
        AIAudit.objects.create(
            who=actor if getattr(actor, 'is_authenticated', False) else None,
            action=action, provider=provider or '',
            credential_id=str(credential_id or ''), detail=redact(detail or {}),
        )
    except Exception:
        logger.exception('ai_hub audit write failed (action=%s provider=%s)', action, provider)
