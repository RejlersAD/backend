"""Project-scoped AI providers, with isolated credentials and bounded failures.

Gemini uses Google's generateContent REST API. Credentials are sent only in
the authentication header to its fixed HTTPS endpoint, never in URLs or logs.
The existing Claude client remains the Anthropic implementation.
"""
import logging
import os
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from copy import deepcopy
from threading import Lock
from types import SimpleNamespace

import requests
from decouple import config
from django.views.decorators.debug import sensitive_variables

from ..config import CLAUDE_MODEL_CHOICES, DEFAULT_CLAUDE_MODEL
from . import byok_crypto, claude_client


logger = logging.getLogger(__name__)
DEFAULT_PROVIDER = 'anthropic'
DEFAULT_GEMINI_MODEL = 'gemini-3.8-flash'
PROVIDER_CHOICES = [
    {'value': 'anthropic', 'label': 'Anthropic (Claude)'},
    {'value': 'gemini', 'label': 'Google Gemini'},
]
MODEL_CHOICES_BY_PROVIDER = {
    'anthropic': CLAUDE_MODEL_CHOICES,
    'gemini': [
        {'value': DEFAULT_GEMINI_MODEL, 'label': 'Gemini 3.8 Flash'},
        {'value': 'gemini-3.5-flash-lite', 'label': 'Gemini 3.5 Flash-Lite'},
    ],
}
DEFAULT_MODEL_BY_PROVIDER = {'anthropic': DEFAULT_CLAUDE_MODEL, 'gemini': DEFAULT_GEMINI_MODEL}
GEMINI_BYOK_ENABLED = config('PLANNING_GEMINI_BYOK_ENABLED', default=True, cast=bool)
GEMINI_REQUEST_TIMEOUT_SECONDS = config('PLANNING_GEMINI_TIMEOUT_SECONDS', default=60, cast=int)
_GEMINI_ENDPOINT = 'https://generativelanguage.googleapis.com/v1beta/models'
_GEMINI_RETRY_STATUSES = {500, 502, 503, 504}
_GEMINI_ERROR_MESSAGES = {
    'invalid_api_key': 'Google Gemini rejected the API key. Use an active Gemini API key from Google AI Studio.',
    'permission_denied': 'Google Gemini denied access. Check that this key permits the Gemini API and that the API is enabled for its Google project.',
    'quota_exceeded': 'Google Gemini quota or rate limit was reached. Check the Google project quota and billing, then retry.',
    'model_unavailable': 'The selected Gemini model is unavailable for this key. Select another Gemini model and test again.',
    'failed_precondition': 'Google Gemini could not use this project. Check the Google project billing and regional availability.',
    'provider_unavailable': 'Google Gemini is temporarily unavailable. Retry analysis shortly.',
    'request_rejected': 'Google Gemini rejected the request. Check the saved key, model and Google project settings.',
    'empty_response': 'Google Gemini returned no usable text. Retry the analysis.',
    'output_limit': 'Google Gemini reached the response token or context limit before completing the analysis. Existing source findings remain available; retry the unfinished analysis.',
    'incomplete_response': 'Google Gemini could not complete the response. Review the input and retry.',
    'timeout': 'Google Gemini timed out. Retry analysis shortly.',
    'connection_error': 'Could not connect to Google Gemini. Retry analysis shortly.',
    'invalid_response': 'Google Gemini returned an invalid response. Retry analysis shortly.',
}
_ANTHROPIC_ERROR_MESSAGES = {
    'invalid_api_key': 'Anthropic rejected the API key. Open AI settings, save an active Anthropic key, and test the connection.',
    'permission_denied': 'Anthropic denied access. Check the saved key and account permissions, then test the connection.',
    'quota_exceeded': 'Anthropic quota or rate limit was reached. Check account limits and billing, then retry.',
    'credit_balance_exhausted': 'Anthropic reported insufficient API credits. Check the API account billing balance, then resume the unfinished analysis. Saved source findings are retained.',
    'model_unavailable': 'The selected Anthropic model is unavailable for this key. Select an available model and test again.',
    'provider_unavailable': 'Anthropic is temporarily unavailable. Retry analysis shortly.',
    'request_rejected': 'Anthropic rejected the analysis request. The saved diagnostic does not identify a more specific cause. Review the provider account and request settings before resuming the unfinished analysis.',
    'request_configuration_error': 'Anthropic rejected the analysis request parameters. Review the model and request configuration before resuming; repeating unchanged requests will not resolve this error.',
    'input_limit': 'Anthropic rejected a source section because it exceeded the input limit. Retry the unfinished analysis in smaller sections. Saved source findings are retained.',
    'empty_response': 'Anthropic returned no usable text. Retry the analysis.',
    'output_limit': 'Anthropic reached the response token or context limit before completing the analysis. Existing source findings remain available; retry the unfinished analysis.',
    'incomplete_response': 'Anthropic did not complete the response. The partial response was not used; retry the analysis.',
    'timeout': 'Anthropic timed out. Retry analysis shortly.',
    'connection_error': 'Could not connect to Anthropic. Retry analysis shortly.',
    'invalid_response': 'Anthropic returned an invalid response. Retry analysis shortly.',
}


def ai_failure_guidance(error):
    """Render only recognized codes, never a stored/provider-supplied message."""
    if not isinstance(error, dict) or error.get('provider') not in ('gemini', 'anthropic'):
        return None
    provider = error['provider']
    messages = _GEMINI_ERROR_MESSAGES if provider == 'gemini' else _ANTHROPIC_ERROR_MESSAGES
    code = error.get('code')
    message = messages.get(code) if isinstance(code, str) else None
    if not message:
        return None
    http_status = error.get('http_status')
    if type(http_status) is not int or not 400 <= http_status <= 599:
        http_status = None
    if http_status:
        label = 'Google Gemini' if provider == 'gemini' else 'Anthropic'
        message = f'{label} returned HTTP {http_status}. {message}'
    settings_errors = {'invalid_api_key', 'permission_denied', 'model_unavailable', 'failed_precondition',
                       'request_rejected', 'request_configuration_error', 'credit_balance_exhausted'}
    return {'code': code, 'http_status': http_status, 'message': message,
            'next_action': 'ai_settings' if code in settings_errors else 'retry_analysis'}


def pauses_analysis(error, *, consecutive_failures=1):
    """Stop a pass when further source sections cannot fix the provider error.

    Unknown 400s and transient outages require two consecutive matching failures.
    A later explicit continuation gets a fresh attempt with completed work cached.
    """
    if not isinstance(error, dict) or error.get('provider') not in {'anthropic', 'gemini'}:
        return False
    code = error.get('code')
    if code in {'invalid_api_key', 'permission_denied', 'model_unavailable', 'failed_precondition',
                'credit_balance_exhausted', 'quota_exceeded', 'request_configuration_error'}:
        return True
    return consecutive_failures >= 2 and (
        (code == 'request_rejected' and error.get('http_status') == 400)
        or code in {'timeout', 'connection_error', 'provider_unavailable'}
    )


def analysis_concurrency():
    """Bound provider pressure per analysis; this is not a global rate limit."""
    try:
        return min(4, max(1, int(os.environ.get('PLANNING_AI_CONCURRENCY', '2'))))
    except (TypeError, ValueError):
        return 2


class AnalysisRequests:
    """Network workers; callers consume results and persist on their own thread.

    No work is queued beyond the concurrency limit. Closing drains calls already
    started; it cannot cancel a request the provider has already accepted.
    """

    def __init__(self, project, user, concurrency):
        # Freeze the selected credential settings and avoid deferred ORM reads
        # or thread-local database connections in provider workers.
        self.project = SimpleNamespace(pk=getattr(project, 'pk', None), id=getattr(project, 'id', None),
                                       ai_settings=deepcopy(_settings(project)))
        self.user = user
        self.concurrency = concurrency
        self.executor = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix='planning-ai')
        self.pending = {}
        self.progress = {}
        self.lock = Lock()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.executor.shutdown(wait=True, cancel_futures=True)

    def submit(self, key, **request):
        if len(self.pending) >= self.concurrency:
            raise RuntimeError('Analysis concurrency limit reached')

        def receive(event):
            count = event.get('response_characters_received')
            if type(count) is int and count >= 0:
                with self.lock:
                    self.progress[key] = count  # At most one pending update per request.

        def call():
            error, usage = {}, []
            try:
                result = call_project_ai(self.project, user=self.user, error_details=error,
                                         progress_callback=receive,
                                         usage_callback=lambda **values: usage.append(values), **request)
            except Exception:
                result = None
                error = {'provider': project_provider(self.project), 'code': 'invalid_response', 'http_status': None}
            return result, error, usage

        self.pending[self.executor.submit(call)] = key

    def receive(self):
        wait(self.pending, timeout=0.25, return_when=FIRST_COMPLETED)
        with self.lock:
            progress, self.progress = self.progress, {}
        # Retain submission order for results that became ready together.
        completed = [(self.pending.pop(future), future.result())
                     for future in list(self.pending) if future.done()]
        return progress, completed

    def has_completed(self):
        return any(future.done() for future in self.pending)

    @staticmethod
    def persist_usage(records):
        if records:
            from apps.rbac.ai_telemetry import record_usage
            for values in records:
                record_usage(**values)


def _settings(project):
    settings = getattr(project, 'ai_settings', None)
    return settings if isinstance(settings, dict) else {}


def project_provider(project):
    return _settings(project).get('provider') or DEFAULT_PROVIDER


@sensitive_variables()
def get_project_ai_config(project):
    """Return decrypted credentials for the selected provider only, or None."""
    provider = project_provider(project)
    settings = _settings(project)
    if settings.get('api_key_provider', provider) != provider:
        return None
    if provider == 'anthropic':
        configuration = claude_client.get_claude_config(project)
        return {**configuration, 'provider': provider} if configuration else None
    if provider != 'gemini' or not GEMINI_BYOK_ENABLED or not settings.get('enabled'):
        return None
    api_key = byok_crypto.decrypt_api_key(settings.get('api_key_encrypted'))
    # In addition to the persisted provider binding, reject recognizable keys
    # from other providers, including historical single-key configurations.
    if not api_key or api_key.startswith('sk-'):
        return None
    model = settings.get('model')
    if model not in {choice['value'] for choice in MODEL_CHOICES_BY_PROVIDER[provider]}:
        model = DEFAULT_MODEL_BY_PROVIDER[provider]
    return {'provider': provider, 'api_key': api_key, 'model': model}


class _ProviderError(Exception):
    def __init__(self, code, *, http_status=None):
        self.code = code
        self.http_status = http_status
        super().__init__(_GEMINI_ERROR_MESSAGES[code])


def _gemini_http_error(response):
    """Map provider failures without exposing raw bodies, URLs or credentials."""
    status = response.status_code
    error_status = ''
    reasons = set()
    try:
        error = response.json().get('error', {})
        error_status = error.get('status', '')
        reasons = {item.get('reason') for item in error.get('details', []) if isinstance(item, dict)}
    except (ValueError, TypeError, AttributeError):
        pass
    if status == 401 or 'API_KEY_INVALID' in reasons:
        return _ProviderError('invalid_api_key', http_status=status)
    if status == 403:
        return _ProviderError('permission_denied', http_status=status)
    if status == 429:
        return _ProviderError('quota_exceeded', http_status=status)
    if status == 404:
        return _ProviderError('model_unavailable', http_status=status)
    if status == 400 and error_status == 'FAILED_PRECONDITION':
        return _ProviderError('failed_precondition', http_status=status)
    if status >= 500:
        return _ProviderError('provider_unavailable', http_status=status)
    return _ProviderError('request_rejected', http_status=status)


@sensitive_variables()
def _request_gemini(configuration, *, system_prompt, user_prompt, max_tokens, json_output):
    generation = {'maxOutputTokens': max_tokens, 'thinkingConfig': {'thinkingLevel': 'low'}}
    if json_output:
        generation['responseMimeType'] = 'application/json'
    # Retry one explicit transient HTTP failure. Timeouts and connection errors
    # are not replayed because the provider may already be generating a reply.
    for attempt in range(2):
        response = requests.post(
            f"{_GEMINI_ENDPOINT}/{configuration['model']}:generateContent",
            headers={'x-goog-api-key': configuration['api_key'], 'Content-Type': 'application/json'},
            json={
                'systemInstruction': {'parts': [{'text': system_prompt}]},
                'contents': [{'role': 'user', 'parts': [{'text': user_prompt}]}],
                'generationConfig': generation,
            },
            timeout=(10, GEMINI_REQUEST_TIMEOUT_SECONDS), allow_redirects=False,
        )
        if attempt or response.status_code not in _GEMINI_RETRY_STATUSES:
            break
        response.close()
        time.sleep(1)
    if response.status_code != 200:
        raise _gemini_http_error(response)
    payload = response.json()
    candidates = payload.get('candidates') or []
    if not candidates:
        raise _ProviderError('empty_response')
    candidate = candidates[0]
    finish = candidate.get('finishReason')
    if finish not in {'STOP', 'MAX_TOKENS'}:
        raise _ProviderError('incomplete_response')
    text = ''.join(
        part['text'] for part in candidate.get('content', {}).get('parts', [])
        if isinstance(part.get('text'), str) and not part.get('thought')
    ).strip()
    if not text:
        raise _ProviderError('empty_response')
    usage = payload.get('usageMetadata') or {}
    return {
        'text': text,
        'tokens_input': usage.get('promptTokenCount', 0) or 0,
        'tokens_output': (usage.get('candidatesTokenCount', 0) or 0) + (usage.get('thoughtsTokenCount', 0) or 0),
        'stop_reason': 'max_tokens' if finish == 'MAX_TOKENS' else 'end_turn',
    }


@sensitive_variables()
def _call_gemini(project, configuration, *, system_prompt, user_prompt, max_tokens, feature, user, json_output, error_details=None, usage_callback=None):
    started = time.monotonic()
    result, error, error_code = None, '', ''
    http_status = None
    try:
        result = _request_gemini(configuration, system_prompt=system_prompt, user_prompt=user_prompt,
                                 max_tokens=max_tokens, json_output=json_output)
    except _ProviderError as exc:
        error, error_code = str(exc), exc.code
        http_status = exc.http_status
    except requests.Timeout:
        error_code = 'timeout'
    except requests.RequestException:
        error_code = 'connection_error'
    except Exception:
        error_code = 'invalid_response'
    latency_ms = int((time.monotonic() - started) * 1000)
    if result is not None:
        result['latency_ms'] = latency_ms
    else:
        failure = {'provider': 'gemini', 'code': error_code, 'http_status': http_status}
        error = ai_failure_guidance(failure)['message']
        if error_details is not None:
            error_details.update(failure)
        logger.warning('Planning Gemini request failed (project=%s, feature=%s, code=%s, http_status=%s)',
                       getattr(project, 'pk', None), feature, error_code, http_status)
    if user is not None:
        from apps.rbac.ai_telemetry import record_usage
        (usage_callback or record_usage)(user=user, provider='gemini', model=configuration['model'], feature=feature,
                     application='planning_intelligence', tokens_input=(result or {}).get('tokens_input', 0),
                     tokens_output=(result or {}).get('tokens_output', 0), latency_ms=latency_ms,
                     success=result is not None, error_code=error_code, usage_available=result is not None)
    return result, error


@sensitive_variables()
def call_project_ai(project, *, system_prompt, user_prompt, max_tokens, feature, user=None, json_output=False, error_details=None, progress_callback=None, usage_callback=None):
    """Return None on failure; optionally collect safe codes for run diagnostics."""
    if error_details is not None:
        error_details.clear()
    configuration = get_project_ai_config(project)
    if configuration is None:
        return None
    if configuration['provider'] == 'anthropic':
        progress = {'progress_callback': progress_callback} if progress_callback is not None else {}
        if usage_callback is not None:
            progress['usage_callback'] = usage_callback
        return claude_client.call_claude(project, system_prompt=system_prompt, user_prompt=user_prompt,
                                         max_tokens=max_tokens, feature=feature, user=user, error_details=error_details,
                                         **progress)
    result, _error = _call_gemini(project, configuration, system_prompt=system_prompt, user_prompt=user_prompt,
                                 max_tokens=max_tokens, feature=feature, user=user, json_output=json_output,
                                 error_details=error_details, usage_callback=usage_callback)
    return result


@sensitive_variables()
def test_project_ai_connection(project, user=None):
    """Return a public test result containing only model identity and safe errors."""
    configuration = get_project_ai_config(project)
    provider = project_provider(project)
    model = (configuration or {}).get('model', DEFAULT_MODEL_BY_PROVIDER.get(provider))
    response = {'success': False, 'provider': provider, 'model': model, 'error': ''}
    if configuration is None:
        response['error'] = 'Enable project AI and save a key for the selected provider before testing.'
        return response
    prompts = {'system_prompt': 'Reply with OK only.', 'user_prompt': 'Connection test.',
               'max_tokens': 512, 'feature': 'planning_ai_connection_test', 'user': user}
    if provider == 'gemini':
        result, response['error'] = _call_gemini(project, configuration, **prompts, json_output=False)
    else:
        errors = {}
        result = claude_client.call_claude(project, **prompts, error_details=errors)
        if result is None:
            guidance = ai_failure_guidance(errors)
            response['error'] = guidance['message'] if guidance else 'Could not connect to Anthropic. Check the saved key, model and account access, then retry.'
    response['success'] = bool(result and result.get('stop_reason') not in {'max_tokens', 'model_context_window_exceeded'})
    if result and not response['success']:
        response['error'] = 'The AI connection returned an incomplete response. Try again.'
    return response
