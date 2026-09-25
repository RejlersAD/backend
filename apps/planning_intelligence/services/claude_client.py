"""
Planning Intelligence — Claude (Anthropic) BYOK client.

This module is the ONLY place that talks to the Anthropic API for this
feature. It is intentionally defensive: any failure (missing/invalid key,
network error, rate limit, timeout, malformed response) is caught here and
turned into a `None` return value plus a logged/usage-tracked failure — it
must never raise, and it must never block the deterministic pipeline in
services/intelligence.py or services/narrative_generator.py.
"""
import logging
import re
import time
import httpx
from django.views.decorators.debug import sensitive_variables

try:  # Anthropic 1.x uses httpx2; older supported SDKs use httpx.
    import httpx2
except ImportError:
    httpx2 = None

from . import byok_crypto
from ..config import (
    CLAUDE_BYOK_ENABLED,
    CLAUDE_MODEL_VALUES,
    CLAUDE_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_CLAUDE_MODEL,
)

logger = logging.getLogger(__name__)
_TIMEOUT_ERRORS = (httpx.TimeoutException,) + ((httpx2.TimeoutException,) if httpx2 else ())
_TRANSPORT_ERRORS = (httpx.TransportError,) + ((httpx2.TransportError,) if httpx2 else ())
# These models enable thinking by default and share its budget with the answer.
# Bound this policy to verified models: later models may require thinking.
_EXTRACTION_THINKING_MODELS = {'claude-opus-5', 'claude-sonnet-5'}
_LIMIT_STOP_REASONS = {'max_tokens', 'model_context_window_exceeded'}


def _limit_failure(stop_reason, *, tokens_input, tokens_output, max_tokens):
    """Keep only known completion reasons and numeric usage, never provider text."""
    if stop_reason not in _LIMIT_STOP_REASONS:
        return None
    return {
        'provider': 'anthropic', 'code': 'output_limit', 'http_status': None,
        'stop_reason': stop_reason,
        **{name: value for name, value in {
            'tokens_input': tokens_input, 'tokens_output': tokens_output,
            'max_tokens': max_tokens,
        }.items() if type(value) is int and value >= 0},
    }


class _IncompleteStreamError(Exception):
    """The provider closed the stream without its completion event."""


@sensitive_variables()
def get_claude_config(project) -> dict | None:
    """
    Return {'api_key': <decrypted>, 'model': <id>} for this project if BYOK
    is enabled/configured and usable, else None (deterministic-only).
    """
    if not CLAUDE_BYOK_ENABLED or project is None:
        return None

    ai_settings = getattr(project, 'ai_settings', None) or {}
    if (not isinstance(ai_settings, dict)
            or ai_settings.get('provider') not in (None, '', 'anthropic')
            or ai_settings.get('api_key_provider', 'anthropic') != 'anthropic'):
        return None
    if not ai_settings.get('enabled'):
        return None

    encrypted_key = ai_settings.get('api_key_encrypted')
    if not encrypted_key:
        return None

    api_key = byok_crypto.decrypt_api_key(encrypted_key)
    if not api_key:
        return None

    model = ai_settings.get('model') or DEFAULT_CLAUDE_MODEL
    if model not in CLAUDE_MODEL_VALUES:
        model = DEFAULT_CLAUDE_MODEL

    return {'api_key': api_key, 'model': model}


@sensitive_variables()
def _bad_request_code(exc):
    """Use known provider diagnostics only to select fixed public error codes.

    The body can contain request text or credentials. Never persist or log it,
    or use str(exc), and leave unrecognized HTTP 400 causes explicitly unknown.
    SDK versions expose either the envelope or its nested error as ``body``.
    """
    body = getattr(exc, 'body', None)
    if not isinstance(body, dict):
        return 'request_rejected'
    error = body.get('error', body)
    if not isinstance(error, dict) or not isinstance(error.get('message'), str):
        return 'request_rejected'
    message = error['message'].strip().lower()
    if message.startswith(('your credit balance is too low', 'credit balance is too low', 'credit balance too low',
                           'insufficient credits', 'insufficient credit balance')):
        return 'credit_balance_exhausted'
    if message.startswith(('prompt is too long', 'request is too large', 'request too large')):
        return 'input_limit'
    if re.match(r'^(?:the )?(?:selected )?model(?:\s|:)', message) and any(
            phrase in message for phrase in ('not found', 'does not exist', 'not available',
                                             'not supported', 'do not have access')):
        return 'model_unavailable'
    if re.match(r'^(?:max_tokens|thinking|output_config)(?:\.|:|\s)', message):
        return 'request_configuration_error'
    return 'request_rejected'


def _safe_failure(exc):
    """Classify SDK failures without retaining response bodies or credentials."""
    status = getattr(exc, 'status_code', None)
    if type(status) is not int or not 400 <= status <= 599:
        status = None
    name = type(exc).__name__
    if status == 401 or name == 'AuthenticationError':
        code = 'invalid_api_key'
    elif status == 403 or name == 'PermissionDeniedError':
        code = 'permission_denied'
    elif status == 404 or name == 'NotFoundError':
        code = 'model_unavailable'
    elif status == 429 or name == 'RateLimitError':
        code = 'quota_exceeded'
    elif status == 413:
        code = 'input_limit'
    elif status == 400:
        code = _bad_request_code(exc)
    elif status is not None and status >= 500:
        code = 'provider_unavailable'
    elif name == 'APITimeoutError' or isinstance(exc, _TIMEOUT_ERRORS):
        code = 'timeout'
    elif name == 'APIConnectionError' or isinstance(exc, _TRANSPORT_ERRORS):
        code = 'connection_error'
    elif isinstance(exc, _IncompleteStreamError):
        code = 'incomplete_response'
    elif status is not None:
        code = 'request_rejected'
    else:
        code = 'invalid_response'
    return {'provider': 'anthropic', 'code': code, 'http_status': status}


@sensitive_variables()
def call_claude(
    project,
    *,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    feature: str,
    user=None,
    error_details=None,
    progress_callback=None,
) -> dict | None:
    """
    Make one Claude Messages API call scoped to `project`'s BYOK key.

    Returns {'text': str, 'tokens_input': int, 'tokens_output': int,
    'latency_ms': int} on success, or None on any failure. Always logs one
    AIUsageLog row (success or failure) when `user` is provided.
    """
    if error_details is not None:
        error_details.clear()
    claude_config = get_claude_config(project)
    if claude_config is None:
        return None

    model = claude_config['model']
    start = time.monotonic()
    success = False
    error_code = ''
    tokens_input = 0
    tokens_output = 0
    result_text = None
    stop_reason = None
    failure = None

    try:
        import anthropic

        with anthropic.Anthropic(
            api_key=claude_config['api_key'],
            timeout=CLAUDE_REQUEST_TIMEOUT_SECONDS,
            # A timed-out generation may still be running at the provider.
            # Retrying belongs to the explicit analysis/checkpoint workflow.
            max_retries=0,
        ) as client:
            request = dict(model=model, max_tokens=max_tokens, system=system_prompt,
                           messages=[{'role': 'user', 'content': user_prompt}])
            if feature == 'document_intelligence':
                if model in _EXTRACTION_THINKING_MODELS:
                    # Source extraction needs its bounded budget for JSON text.
                    # Opus 5 permits disabled thinking only at high effort or below.
                    request.update(thinking={'type': 'disabled'}, output_config={'effort': 'high'})
                # Receive output as it is generated: the existing read timeout
                # measures inactivity instead of waiting for the entire answer.
                # SDK snapshots alone do not prove receipt of message_stop.
                response = None
                characters_received, last_progress_at = 0, None
                with client.messages.stream(**request) as stream:
                    for event in stream:
                        if event.type == 'text':
                            characters_received += len(event.text)
                            now = time.monotonic()
                            if progress_callback is not None and (last_progress_at is None or now - last_progress_at >= 5):
                                try:
                                    progress_callback({'response_characters_received': characters_received})
                                except Exception:  # Progress delivery must not discard a provider result.
                                    logger.warning('[Planning BYOK] Progress update unavailable (project=%s)',
                                                   getattr(project, 'id', None))
                                last_progress_at = now
                        if event.type == 'message_stop':
                            response = event.message
                            break
                if response is None:
                    raise _IncompleteStreamError()
            else:
                response = client.messages.create(**request)
        result_text = ''.join(
            block.text for block in response.content if getattr(block, 'type', None) == 'text'
        ).strip()
        usage = getattr(response, 'usage', None)
        stop_reason = getattr(response, 'stop_reason', None)
        tokens_input = getattr(usage, 'input_tokens', 0) or 0
        tokens_output = getattr(usage, 'output_tokens', 0) or 0
        failure = _limit_failure(stop_reason, tokens_input=tokens_input,
                                 tokens_output=tokens_output, max_tokens=max_tokens)
        success = bool(result_text)
        if not success:
            failure = failure or {'provider': 'anthropic', 'code': 'empty_response', 'http_status': None}
            error_code = failure['code']
    except Exception as exc:  # noqa: BLE001 — must never propagate to callers
        failure = _safe_failure(exc)
        error_code = failure['code']
        logger.warning(
            '[Planning BYOK] Claude call failed (project=%s, feature=%s, code=%s, http_status=%s)',
            getattr(project, 'id', None), feature, error_code, failure['http_status'],
        )
        result_text = None

    latency_ms = int((time.monotonic() - start) * 1000)
    _log_usage(
        project=project, user=user, model=model, feature=feature,
        tokens_input=tokens_input, tokens_output=tokens_output,
        latency_ms=latency_ms, success=success, error_code=error_code,
    )

    if error_details is not None and failure:
        # A nonempty limited response still reaches the existing partial-coverage
        # checks. If its JSON is truncated, the failure keeps this specific cause.
        error_details.update(failure)
    if not success:
        return None
    return {
        'text': result_text,
        'tokens_input': tokens_input,
        'tokens_output': tokens_output,
        'latency_ms': latency_ms,
        'stop_reason': stop_reason,
    }


def _log_usage(*, project, user, model, feature, tokens_input, tokens_output, latency_ms, success, error_code):
    if user is None:
        # No authenticated user context (e.g. background/system call) — skip
        # logging rather than writing a row with a null FK.
        return
    from apps.rbac.ai_telemetry import record_usage
    record_usage(user=user, provider='anthropic', model=model, feature=feature,
                 application='planning_intelligence', tokens_input=tokens_input,
                 tokens_output=tokens_output, latency_ms=latency_ms,
                 success=success, error_code=error_code,
                 usage_available=success or bool(tokens_input or tokens_output))
