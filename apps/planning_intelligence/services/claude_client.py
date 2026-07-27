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
import time

from . import byok_crypto
from ..config import (
    CLAUDE_BYOK_ENABLED,
    CLAUDE_MODEL_VALUES,
    CLAUDE_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_CLAUDE_MODEL,
)

logger = logging.getLogger(__name__)


def get_claude_config(project) -> dict | None:
    """
    Return {'api_key': <decrypted>, 'model': <id>} for this project if BYOK
    is enabled/configured and usable, else None (deterministic-only).
    """
    if not CLAUDE_BYOK_ENABLED or project is None:
        return None

    ai_settings = getattr(project, 'ai_settings', None) or {}
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


def call_claude(
    project,
    *,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    feature: str,
    user=None,
) -> dict | None:
    """
    Make one Claude Messages API call scoped to `project`'s BYOK key.

    Returns {'text': str, 'tokens_input': int, 'tokens_output': int,
    'latency_ms': int} on success, or None on any failure. Always logs one
    AIUsageLog row (success or failure) when `user` is provided.
    """
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

    try:
        import anthropic

        client = anthropic.Anthropic(
            api_key=claude_config['api_key'],
            timeout=CLAUDE_REQUEST_TIMEOUT_SECONDS,
        )
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{'role': 'user', 'content': user_prompt}],
        )
        result_text = ''.join(
            block.text for block in response.content if getattr(block, 'type', None) == 'text'
        ).strip()
        usage = getattr(response, 'usage', None)
        tokens_input = getattr(usage, 'input_tokens', 0) or 0
        tokens_output = getattr(usage, 'output_tokens', 0) or 0
        success = bool(result_text)
        if not success:
            error_code = 'empty_response'
    except Exception as exc:  # noqa: BLE001 — must never propagate to callers
        error_code = type(exc).__name__
        logger.warning(
            '[Planning BYOK] Claude call failed (project=%s, feature=%s): %s',
            getattr(project, 'id', None), feature, exc,
        )
        result_text = None

    latency_ms = int((time.monotonic() - start) * 1000)
    _log_usage(
        project=project, user=user, model=model, feature=feature,
        tokens_input=tokens_input, tokens_output=tokens_output,
        latency_ms=latency_ms, success=success, error_code=error_code,
    )

    if not success:
        return None
    return {
        'text': result_text,
        'tokens_input': tokens_input,
        'tokens_output': tokens_output,
        'latency_ms': latency_ms,
    }


def _log_usage(*, project, user, model, feature, tokens_input, tokens_output, latency_ms, success, error_code):
    if user is None:
        # No authenticated user context (e.g. background/system call) — skip
        # logging rather than writing a row with a null FK.
        return
    try:
        from decimal import Decimal

        from apps.rbac.ai_champion_models import AIPricingConfig, AIUsageLog

        cost_usd = Decimal('0')
        pricing = (
            AIPricingConfig.objects
            .filter(provider='anthropic', model_name=model, is_active=True)
            .order_by('-effective_from')
            .first()
        )
        if pricing:
            cost_usd = pricing.compute_cost(tokens_input, tokens_output)

        AIUsageLog.objects.create(
            user=user,
            provider='anthropic',
            model_name=model,
            application='planning_intelligence',
            feature=feature,
            request_id=str(getattr(project, 'id', '') or ''),
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            success=success,
            error_code=error_code,
        )
    except Exception as exc:  # noqa: BLE001 — usage logging must never break the pipeline
        logger.warning('[Planning BYOK] Failed to write AIUsageLog: %s', exc)
