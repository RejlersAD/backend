"""Explicit operation-scoped clients for existing AI consumers.

Constructing a client does not query the database or open a provider connection.
Each SDK operation resolves the current registry credential, including when the
consumer keeps its client for the lifetime of a worker process. This module does
not patch SDKs, Django settings, or process environment variables.
"""
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
import logging
import os
import threading

from django.conf import settings
from django.views.decorators.debug import sensitive_variables

from . import ai_credentials


_ALIASES = {'claude': 'anthropic', 'google': 'gemini'}
_ENVIRONMENT_KEYS = {
    'openai': ('OPENAI_API_KEY',),
    'anthropic': ('ANTHROPIC_API_KEY', 'CLAUDE_API_KEY'),
    'gemini': ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'GOOGLE_GENERATIVEAI_API_KEY'),
}
_PRIVATE_CALL = ContextVar('ai_consumer_private_call', default=False)
_FILTER_LOCK = threading.Lock()


class _PrivateCallFilter(logging.Filter):
    def filter(self, record):
        return not _PRIVATE_CALL.get()


_PRIVATE_FILTER = _PrivateCallFilter()


@contextmanager
def _private_provider_logs():
    # Filters use context-local state; concurrent non-AI requests are unaffected.
    with _FILTER_LOCK:
        for name in (
            'openai', 'openai._base_client', 'openai._legacy_response', 'openai._response',
            'anthropic', 'anthropic._base_client', 'anthropic._legacy_response', 'anthropic._response',
            'anthropic.lib.credentials._auth', 'anthropic.lib.credentials._providers',
            'httpx', 'httpx2', 'httpcore', 'httpcore.connection', 'httpcore.http11',
            'httpcore.http2', 'httpcore.proxy', 'httpcore.socks', 'google.genai',
            'google.genai._api_client', 'google.genai.models', 'google.auth',
            'google.auth._default', 'google.auth.transport.requests',
        ):
            logger = logging.getLogger(name)
            if _PRIVATE_FILTER not in logger.filters:
                logger.addFilter(_PRIVATE_FILTER)
    token = _PRIVATE_CALL.set(True)
    try:
        yield
    finally:
        _PRIVATE_CALL.reset(token)


class AIProviderOperationError(RuntimeError):
    """An allowlisted diagnostic without provider bodies, requests, or keys."""
    def __init__(self, error):
        status = getattr(error, 'status_code', None) or getattr(error, 'code', None)
        self.status_code = status if isinstance(status, int) and 400 <= status <= 599 else None
        self.code = {
            400: 'request_rejected', 401: 'authentication_failed', 403: 'permission_denied',
            404: 'model_unavailable', 429: 'rate_limit',
        }.get(self.status_code, 'provider_unavailable')
        if type(error).__name__ in {'APITimeoutError', 'TimeoutError', 'ReadTimeout'}:
            self.code = 'provider_timeout'
        super().__init__({
            'request_rejected': 'The AI provider rejected this request (400).',
            'authentication_failed': 'The AI provider rejected the configured credentials (401 authentication).',
            'permission_denied': 'The configured AI account cannot perform this request (403 permission denied).',
            'model_unavailable': 'The configured AI model is unavailable (404).',
            'rate_limit': 'The AI provider reached a usage or rate limit (429).',
            'provider_timeout': 'The AI provider request timed out.',
            'provider_unavailable': 'The AI provider is unavailable. Retry later.',
        }[self.code])


def _provider(value):
    return _ALIASES.get(value, value)


@sensitive_variables()
def _legacy_key(provider, fallback):
    value = fallback() if callable(fallback) else fallback
    if value:
        return value
    for name in _ENVIRONMENT_KEYS.get(provider, ()):
        value = os.environ.get(name) or getattr(settings, name, '')
        if value:
            return value
    return ''


@sensitive_variables()
def provider_api_key(provider, fallback=None):
    """Resolve a consumer key; legacy inputs never override managed state."""
    provider = _provider(provider)
    return ai_credentials.get_provider_api_key(provider, fallback=lambda: _legacy_key(provider, fallback))


def provider_available(provider, fallback=None):
    """Readiness for existing gates; never place a resolved key in job arguments."""
    if _provider(provider) not in _ENVIRONMENT_KEYS:
        return False
    return bool(provider_api_key(provider, fallback))


def provider_managed(provider):
    if _provider(provider) not in _ENVIRONMENT_KEYS:
        return False
    return ai_credentials.get_provider_configuration(_provider(provider))['managed']


def _close(value):
    close = getattr(value, 'close', None)
    if callable(close):
        try:
            close()
        except Exception:
            # Cleanup must not disclose an SDK error or mask the operation.
            pass


def _safe_operation(operation, *args, **kwargs):
    try:
        with _private_provider_logs():
            return operation(*args, **kwargs)
    except ai_credentials.AICredentialUnavailable:
        raise
    except Exception as error:
        raise AIProviderOperationError(error) from None


class _ResultView:
    def __init__(self, result):
        self._result = result

    def __getattr__(self, name):
        value = getattr(self._result, name)
        if isinstance(value, Iterator):
            return _ResultView(value)
        if callable(value):
            return lambda *args, **kwargs: _safe_operation(value, *args, **kwargs)
        return value

    def __iter__(self):
        iterator = iter(self._result)
        while True:
            try:
                with _private_provider_logs():
                    value = next(iterator)
            except StopIteration:
                return
            except Exception as error:
                raise AIProviderOperationError(error) from None
            yield value


class _LiveResult(_ResultView):
    """Keep the SDK client alive until a stream/context manager is finished."""
    def __init__(self, result, client):
        super().__init__(result)
        self._client = client
        self._closed = False

    def __iter__(self):
        try:
            yield from super().__iter__()
        except ai_credentials.AICredentialUnavailable:
            raise
        except Exception as error:
            raise AIProviderOperationError(error) from None
        finally:
            self.close()

    def __enter__(self):
        try:
            with _private_provider_logs():
                entered = self._result.__enter__()
            return _ResultView(entered)
        except Exception as error:
            self.close()
            raise AIProviderOperationError(error) from None

    def __exit__(self, *args):
        try:
            return _safe_operation(self._result.__exit__, *args)
        finally:
            self.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        _close(self._result)
        _close(self._client)


class _Resource:
    def __init__(self, owner, path=()):
        self._owner = owner
        self._path = path

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        return _Resource(self._owner, (*self._path, name))

    def __call__(self, *args, **kwargs):
        return self._owner._invoke(self._path, args, kwargs)


class _ProviderClient(_Resource):
    def __init__(self, provider, factory, fallback, options):
        super().__init__(self)
        self._provider = _provider(provider)
        self._factory = factory
        self._fallback = fallback
        self._options = options
        self._closed = False

    def __bool__(self):
        return not self._closed and bool(provider_api_key(self._provider, self._fallback))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self._closed = True

    def with_options(self, **options):
        return _ProviderClient(self._provider, self._factory, self._fallback, {**self._options, **options})

    @sensitive_variables()
    def _invoke(self, path, args, kwargs):
        if self._closed:
            raise RuntimeError('The AI client has been closed.')
        key, metadata = ai_credentials.resolve_provider_credential(
            self._provider, fallback=lambda: _legacy_key(self._provider, self._fallback),
        )
        if not key:
            raise ai_credentials.AICredentialUnavailable()
        options = {**self._options, 'api_key': key}
        if metadata['managed']:
            # Bind central credentials to official hosts, independently of SDK
            # environment defaults and historical consumer client options.
            options.pop('http_client', None)
            options.pop('default_headers', None)
            if self._provider == 'openai':
                options.update(base_url='https://api.openai.com/v1', organization='', project='')
            elif self._provider == 'anthropic':
                from anthropic import Omit
                options.update(base_url='https://api.anthropic.com', auth_token='',
                               default_headers={'Authorization': Omit(), 'X-Api-Key': key})
            elif self._provider == 'gemini':
                options.update(vertexai=False, http_options={'base_url': 'https://generativelanguage.googleapis.com'})
        client = None
        try:
            with _private_provider_logs():
                client = self._factory(**options)
                target = client
                for name in path:
                    target = getattr(target, name)
                result = target(*args, **kwargs)
            if isinstance(result, Iterator) or (hasattr(result, '__enter__') and hasattr(result, '__exit__')):
                return _LiveResult(result, client)
            _close(client)
            return result
        except ai_credentials.AICredentialUnavailable:
            _close(client)
            raise
        except Exception as error:
            _close(client)
            raise AIProviderOperationError(error) from None


def lazy_provider_client(provider, factory, *, api_key=None, **options):
    """Explicit SDK-compatible proxy; no database or network work at import."""
    return _ProviderClient(provider, factory, api_key, options)
