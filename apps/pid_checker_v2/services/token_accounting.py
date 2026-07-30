"""Token & cost accounting for BYOK AI calls in P&ID Checker V2.

Every OpenAI / Anthropic call in this app funnels through here so we can
capture prompt/completion tokens, compute a USD cost, and persist a
per-run breakdown. Pricing is soft-coded at the top of the file and can
be overridden per (provider, model) row in `apps.rbac.AIPricingConfig`.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ─── Soft-coded pricing (USD per 1M tokens) ───────────────────────────
# Update this table when providers publish new prices. `AIPricingConfig`
# rows override any entry here (see `price_lookup`).
MODEL_PRICING_PER_1M: dict[str, dict[str, float]] = {
    # OpenAI
    'gpt-4o':                {'input': 2.50,  'output': 10.00},
    'gpt-4o-2024-08-06':     {'input': 2.50,  'output': 10.00},
    'gpt-4o-mini':           {'input': 0.15,  'output': 0.60},
    'gpt-4-turbo':           {'input': 10.00, 'output': 30.00},
    'gpt-4-vision-preview':  {'input': 10.00, 'output': 30.00},

    # Anthropic Claude
    'claude-sonnet-4-5-20250929': {'input': 3.00,  'output': 15.00},
    'claude-3-5-sonnet-20241022': {'input': 3.00,  'output': 15.00},
    'claude-3-5-haiku-20241022':  {'input': 0.80,  'output': 4.00},
    'claude-3-opus-20240229':     {'input': 15.00, 'output': 75.00},
}

# Fallback used when neither AIPricingConfig nor MODEL_PRICING_PER_1M
# knows the model — keeps cost estimation defensive rather than crashing.
PROVIDER_DEFAULT_PRICING: dict[str, dict[str, float]] = {
    'openai': {'input': 2.50,  'output': 10.00},
    'claude': {'input': 3.00,  'output': 15.00},
}

PER_MILLION = Decimal('1000000')


# ─── SDK-response readers ─────────────────────────────────────────────
def read_openai_usage(resp: Any) -> tuple[int, int]:
    """Return (input_tokens, output_tokens) from an OpenAI ChatCompletion."""
    usage = getattr(resp, 'usage', None)
    if usage is None:
        return (0, 0)
    return (
        int(getattr(usage, 'prompt_tokens', 0) or 0),
        int(getattr(usage, 'completion_tokens', 0) or 0),
    )


def read_claude_usage(resp: Any) -> tuple[int, int]:
    """Return (input_tokens, output_tokens) from an Anthropic Messages resp."""
    usage = getattr(resp, 'usage', None)
    if usage is None:
        return (0, 0)
    return (
        int(getattr(usage, 'input_tokens', 0) or 0),
        int(getattr(usage, 'output_tokens', 0) or 0),
    )


# ─── Pricing lookup ───────────────────────────────────────────────────
def price_lookup(provider: str, model_name: str) -> dict[str, float]:
    """Return {'input': $/1M, 'output': $/1M} for the given (provider, model).

    Lookup order: AIPricingConfig active row → MODEL_PRICING_PER_1M →
    PROVIDER_DEFAULT_PRICING. Never raises.
    """
    override = _try_db_override(provider, model_name)
    if override is not None:
        return override
    if model_name in MODEL_PRICING_PER_1M:
        return MODEL_PRICING_PER_1M[model_name]
    return PROVIDER_DEFAULT_PRICING.get(
        provider, {'input': 0.0, 'output': 0.0},
    )


def _try_db_override(provider: str, model_name: str) -> Optional[dict[str, float]]:
    try:
        from apps.rbac.ai_champion_models import AIPricingConfig
        row = (AIPricingConfig.objects
               .filter(provider=provider, model_name=model_name, is_active=True)
               .order_by('-effective_from')
               .first())
        if row is None:
            return None
        # AIPricingConfig stores USD per 1K; convert to per-1M for uniformity.
        return {
            'input':  float(row.input_cost_per_1k) * 1000.0,
            'output': float(row.output_cost_per_1k) * 1000.0,
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug('AIPricingConfig lookup skipped (%s / %s): %s',
                     provider, model_name, exc)
        return None


def compute_cost_usd(provider: str, model_name: str,
                     input_tokens: int, output_tokens: int) -> Decimal:
    p = price_lookup(provider, model_name)
    inp = Decimal(str(p.get('input', 0.0))) * (Decimal(input_tokens) / PER_MILLION)
    out = Decimal(str(p.get('output', 0.0))) * (Decimal(output_tokens) / PER_MILLION)
    return (inp + out).quantize(Decimal('0.000001'))


# ─── Meter ────────────────────────────────────────────────────────────
class UsageMeter:
    """Accumulator threaded through every AI call in a single logical run."""

    def __init__(self, feature: str = ''):
        self.feature = feature
        self._rows: list[dict[str, Any]] = []

    def add(self, provider: str, model: str, input_tokens: int,
            output_tokens: int, feature: str = '') -> None:
        if not provider or not model:
            return
        self._rows.append({
            'provider': provider,
            'model': model,
            'input_tokens': int(input_tokens or 0),
            'output_tokens': int(output_tokens or 0),
            'feature': feature or self.feature,
        })

    def summary(self) -> dict[str, Any]:
        by_model: dict[tuple, dict[str, Any]] = {}
        total_in = 0
        total_out = 0
        total_cost = Decimal('0')
        for r in self._rows:
            k = (r['provider'], r['model'])
            slot = by_model.setdefault(k, {
                'provider': r['provider'],
                'model': r['model'],
                'calls': 0,
                'input_tokens': 0,
                'output_tokens': 0,
                'cost_usd': Decimal('0'),
            })
            slot['calls'] += 1
            slot['input_tokens'] += r['input_tokens']
            slot['output_tokens'] += r['output_tokens']
            cost = compute_cost_usd(
                r['provider'], r['model'], r['input_tokens'], r['output_tokens'],
            )
            slot['cost_usd'] += cost
            total_in += r['input_tokens']
            total_out += r['output_tokens']
            total_cost += cost
        return {
            'calls': len(self._rows),
            'input_tokens': total_in,
            'output_tokens': total_out,
            'total_tokens': total_in + total_out,
            'cost_usd': str(total_cost.quantize(Decimal('0.000001'))),
            'by_model': [
                {**v, 'cost_usd': str(v['cost_usd'].quantize(Decimal('0.000001')))}
                for v in by_model.values()
            ],
        }

    def is_empty(self) -> bool:
        return not self._rows
