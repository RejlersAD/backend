"""Small synthetic connection probes; never documents, email or arbitrary URLs."""
import json
import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from urllib.parse import quote

from django.views.decorators.debug import sensitive_variables


_private = ContextVar('private_ai_credential_probe', default=False)


class _PrivateLogFilter(logging.Filter):
    def filter(self, record):
        return not _private.get()


_filter = _PrivateLogFilter()


@contextmanager
def private_provider_logs():
    for name in (
        'openai._base_client', 'openai._legacy_response', 'openai._response',
        'anthropic', 'anthropic._base_client', 'anthropic._legacy_response', 'anthropic._response',
        'anthropic.lib.credentials._auth', 'anthropic.lib.credentials._providers',
        'httpx', 'httpx2', 'httpcore.connection', 'httpcore.http11', 'httpcore.http2',
        'httpcore.proxy', 'httpcore.socks', 'urllib3.connectionpool',
    ):
        logging.getLogger(name).addFilter(_filter)
    token = _private.set(True)
    try:
        yield
    finally:
        _private.reset(token)


SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['ok'],
          'properties': {'ok': {'type': 'boolean'}}}
PROMPT = 'Synthetic RADAI connection check. Return a JSON object with ok set to true.'
FAILURE_REASONS = frozenset({
    'provider_authentication', 'provider_permission', 'provider_rate_limit', 'provider_request',
    'provider_timeout', 'provider_unavailable', 'provider_dependency_missing', 'invalid_response',
    'credit_balance_exhausted', 'model_unavailable', 'request_configuration_error',
    'provider_incomplete', 'provider_refused',
})


def _http_reason(status):
    return {400: 'provider_request', 401: 'provider_authentication', 403: 'provider_permission',
            404: 'model_unavailable', 422: 'provider_request', 429: 'provider_rate_limit'}.get(status, 'provider_unavailable')


@sensitive_variables()
def _failure_reason(error):
    if type(error).__name__ == 'BadRequestError':
        body = getattr(error, 'body', None)
        details = body.get('error', body) if isinstance(body, dict) else None
        message = details.get('message') if isinstance(details, dict) else None
        if isinstance(message, str):
            message = message.strip().lower()
            if message.startswith(('your credit balance is too low', 'credit balance is too low',
                                   'credit balance too low', 'insufficient credits', 'insufficient credit balance')):
                return 'credit_balance_exhausted'
            if re.match(r'^(?:the )?(?:selected )?model(?:\s|:)', message) and any(
                    phrase in message for phrase in ('not found', 'does not exist', 'not available',
                                                     'not supported', 'do not have access')):
                return 'model_unavailable'
            if re.match(r'^(?:max_tokens|thinking|output_config)(?:\.|:|\s)', message):
                return 'request_configuration_error'
    return {
        'AuthenticationError': 'provider_authentication', 'PermissionDeniedError': 'provider_permission',
        'RateLimitError': 'provider_rate_limit', 'BadRequestError': 'provider_request',
        'NotFoundError': 'model_unavailable', 'UnprocessableEntityError': 'provider_request',
        'APITimeoutError': 'provider_timeout', 'Timeout': 'provider_timeout',
        'ConnectTimeout': 'provider_timeout', 'ReadTimeout': 'provider_timeout',
        'ImportError': 'provider_dependency_missing', 'ModuleNotFoundError': 'provider_dependency_missing',
    }.get(type(error).__name__, 'provider_unavailable')


@sensitive_variables()
def probe_credential(credential):
    """One bounded request to the selected official provider; no retries."""
    try:
        with private_provider_logs():
            if credential.provider == 'openai':
                from openai import OpenAI
                with OpenAI(api_key=credential.api_key, base_url='https://api.openai.com/v1',
                            organization='', project='', timeout=20, max_retries=0) as client:
                    response = client.chat.completions.create(
                        model=credential.model, max_completion_tokens=1024, store=False,
                        messages=[{'role': 'user', 'content': PROMPT}],
                        response_format={'type': 'json_schema', 'json_schema': {
                            'name': 'radai_connection_check', 'strict': True, 'schema': SCHEMA}},
                    )
                if len(response.choices) != 1:
                    return {'success': False, 'reason': 'invalid_response'}
                if getattr(response.choices[0].message, 'refusal', None):
                    return {'success': False, 'reason': 'provider_refused'}
                if response.choices[0].finish_reason != 'stop':
                    return {'success': False, 'reason': 'provider_incomplete'}
                content = response.choices[0].message.content
            elif credential.provider == 'anthropic':
                from anthropic import Anthropic, Omit
                with Anthropic(api_key=credential.api_key, auth_token='', base_url='https://api.anthropic.com',
                               default_headers={'Authorization': Omit(), 'X-Api-Key': credential.api_key},
                               timeout=20, max_retries=0) as client:
                    response = client.messages.create(
                        model=credential.model, max_tokens=1024, messages=[{'role': 'user', 'content': PROMPT}],
                        extra_body={'output_config': {'format': {'type': 'json_schema', 'schema': SCHEMA}}},
                    )
                blocks = [block for block in response.content if getattr(block, 'type', None) == 'text']
                if response.stop_reason == 'refusal':
                    return {'success': False, 'reason': 'provider_refused'}
                if response.stop_reason != 'end_turn':
                    return {'success': False, 'reason': 'provider_incomplete'}
                if len(blocks) != 1:
                    return {'success': False, 'reason': 'invalid_response'}
                content = blocks[0].text
            elif credential.provider == 'gemini':
                import requests
                response = requests.post(
                    'https://generativelanguage.googleapis.com/v1beta/models/' + quote(credential.model, safe='') + ':generateContent',
                    headers={'x-goog-api-key': credential.api_key}, timeout=(5, 20),
                    json={'contents': [{'role': 'user', 'parts': [{'text': PROMPT}]}],
                          'generationConfig': {'maxOutputTokens': 1024, 'responseMimeType': 'application/json'}},
                )
                if not 200 <= response.status_code < 300:
                    return {'success': False, 'reason': _http_reason(response.status_code)}
                candidates = response.json().get('candidates', [])
                if len(candidates) != 1 or candidates[0].get('finishReason') != 'STOP':
                    return {'success': False, 'reason': 'invalid_response'}
                content = ''.join(part.get('text', '') for part in candidates[0].get('content', {}).get('parts', []))
            else:
                return {'success': False, 'reason': 'provider_request'}
        if not isinstance(content, str) or len(content) > 2000:
            return {'success': False, 'reason': 'invalid_response'}
        parsed = json.loads(content)
        if not isinstance(parsed, dict) or set(parsed) != {'ok'} or parsed['ok'] is not True:
            return {'success': False, 'reason': 'invalid_response'}
        return {'success': True, 'reason': ''}
    except (ValueError, TypeError, AttributeError, KeyError, IndexError):
        return {'success': False, 'reason': 'invalid_response'}
    except Exception as error:
        return {'success': False, 'reason': _failure_reason(error)}
