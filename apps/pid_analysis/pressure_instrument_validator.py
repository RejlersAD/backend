"""
Pressure Instrument Validator
=============================

Additive post-processor that runs *after* the AI vision analyzer returns its
list of instruments and *before* the response/Excel is built.

Purpose
-------
The vision model occasionally hallucinates rows with garbage tag numbers,
non-pressure prefixes (e.g. ``TT-101``), out-of-range pressures, or empty
service descriptions.  This module enforces ISA-5.1 tag patterns, sanitises
units, drops duplicates, and reports per-row provenance.

Design
------
* All rules live in `config/pressure_instrument_validation.json`.  Adding a new
  prefix or relaxing a bound requires zero code change.
* The validator is a no-op (returns the input unchanged) if the config file
  is missing or `enabled: false`.
* Original analyzer logic is **never** modified — this module only filters
  and tidies the already-extracted list.

Public entry point
------------------
    validate_instruments(instruments, drawing_info=None) -> dict

Returns::

    {
        "instruments": [...kept...],
        "summary": {
            "kept": int,
            "dropped": int,
            "reasons": {"<reason>": int, ...},
        },
        "audit": [
            {"tag": "...", "kept": True|False, "reason": "..."}, ...
        ],
    }
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent / 'config' / 'pressure_instrument_validation.json'
_CONFIG_CACHE: Dict[str, Any] | None = None


# ─── Config loading ────────────────────────────────────────────────────────
def _load_config() -> Dict[str, Any]:
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None:
        if not CONFIG_PATH.exists():
            _CONFIG_CACHE = {'enabled': False}
        else:
            with CONFIG_PATH.open('r', encoding='utf-8') as fh:
                _CONFIG_CACHE = json.load(fh)
    return _CONFIG_CACHE


def reload_config() -> None:
    global _CONFIG_CACHE
    _CONFIG_CACHE = None


# ─── Helpers ───────────────────────────────────────────────────────────────
_PRESSURE_FIELDS = (
    'operating_pressure_min', 'operating_pressure_norm', 'operating_pressure_max',
    'design_pressure_min',    'design_pressure_norm',    'design_pressure_max',
)


def _is_blank(value: Any, blank_tokens: Iterable[str]) -> bool:
    if value is None:
        return True
    s = str(value).strip()
    return s == '' or s in set(blank_tokens)


def _normalise_string(value: Any, cfg: Dict[str, Any]) -> str:
    if value is None:
        return ''
    s = str(value)
    sn = cfg.get('string_normalisation', {})
    if sn.get('trim_whitespace', True):
        s = s.strip()
    if sn.get('collapse_internal_spaces', True):
        s = re.sub(r'\s{2,}', ' ', s)
    if _is_blank(s, sn.get('blank_tokens', [])):
        return ''
    return s


def _canonicalise_units(value: str, unit_aliases: Dict[str, List[str]]) -> str:
    """Replace any alias of a unit with the canonical name (case-insensitive)."""
    if not value:
        return value
    out = value
    for canonical, aliases in unit_aliases.items():
        for alias in sorted(aliases, key=len, reverse=True):  # longest first
            pattern = re.compile(re.escape(alias), re.IGNORECASE)
            out = pattern.sub(canonical, out)
    return out


def _try_float(value: Any) -> Optional[float]:
    if value is None or value == '':
        return None
    try:
        # Strip units like "barg" / "kPa" so "5.4 barg" still parses
        m = re.search(r'[-+]?\d+(?:\.\d+)?', str(value))
        return float(m.group(0)) if m else None
    except (TypeError, ValueError):
        return None


def _within_bounds(field: str, value: Any, validators: Dict[str, Any]) -> bool:
    rule = validators.get(field)
    if not rule:
        return True
    n = _try_float(value)
    if n is None:
        return True   # non-numeric strings are not bounded
    return rule.get('min', float('-inf')) <= n <= rule.get('max', float('inf'))


def _tag_matches(tag: str, patterns: Iterable[str]) -> Optional[Tuple[str, str]]:
    """Return (prefix, number) if tag matches any pattern, else None."""
    for pat in patterns:
        try:
            m = re.match(pat, tag)
            if m:
                groups = m.groups()
                # First letter group is the prefix (PT/PI/...)
                prefix = next((g for g in groups if g and g.isalpha()), '')
                number = next((g for g in groups if g and g.isdigit()), '')
                return prefix.upper(), number
        except re.error:
            continue
    return None


# ─── Main validation pipeline ──────────────────────────────────────────────
def _record(reasons: Dict[str, int], audit: List[Dict[str, Any]],
            tag: str, kept: bool, reason: str) -> None:
    audit.append({'tag': tag, 'kept': kept, 'reason': reason})
    reasons[reason] = reasons.get(reason, 0) + 1


def validate_instruments(
    instruments: List[Dict[str, Any]] | None,
    drawing_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    instruments = instruments or []
    cfg = _load_config()

    # No-op when disabled — keep behaviour identical to before validator existed.
    if not cfg.get('enabled', False):
        return {
            'instruments': instruments,
            'summary': {'kept': len(instruments), 'dropped': 0, 'reasons': {}},
            'audit': [],
            'enabled': False,
        }

    tag_cfg     = cfg.get('tag_validation', {})
    patterns    = tag_cfg.get('patterns', [])
    min_len     = int(tag_cfg.get('min_length', 0))
    max_len     = int(tag_cfg.get('max_length', 64))
    drop_blank  = bool(tag_cfg.get('drop_if_blank', True))
    drop_dupes  = bool(tag_cfg.get('drop_if_duplicate', True))
    dupe_keys   = tag_cfg.get('duplicate_keys', ['tag_number'])

    allowed_prefixes      = set(cfg.get('allowed_prefixes', []))
    drop_unknown_prefix   = bool(cfg.get('drop_if_prefix_unknown', False))
    field_validators      = cfg.get('field_validators', {})
    unit_aliases          = cfg.get('unit_aliases', {})
    halluc                = cfg.get('hallucination_filters', {})
    sn_cfg                = cfg.get('string_normalisation', {})
    upper_tag             = bool(sn_cfg.get('uppercase_tag', True))

    kept: List[Dict[str, Any]] = []
    audit: List[Dict[str, Any]] = []
    reasons: Dict[str, int] = {}
    seen: set[Tuple] = set()

    for raw in instruments:
        # Normalise every string field once.
        item = {k: _normalise_string(v, cfg) if isinstance(v, str) else v
                for k, v in raw.items()}

        tag = item.get('tag_number') or item.get('tag') or ''
        if upper_tag and tag:
            tag = tag.upper()
            item['tag_number'] = tag

        # 1. Blank tag
        if drop_blank and not tag:
            _record(reasons, audit, tag, False, 'blank_tag')
            continue

        # 2. Length sanity
        if not (min_len <= len(tag) <= max_len):
            _record(reasons, audit, tag, False, 'tag_length')
            continue

        # 3. Pattern match
        match = _tag_matches(tag, patterns) if patterns else (None, None)
        if patterns and match is None:
            _record(reasons, audit, tag, False, 'tag_pattern_mismatch')
            continue

        # 4. Allowed prefix
        if patterns and match and allowed_prefixes:
            prefix = match[0]
            if drop_unknown_prefix and prefix not in allowed_prefixes:
                _record(reasons, audit, tag, False, f'unknown_prefix:{prefix}')
                continue

        # 5. Duplicate
        if drop_dupes:
            key = tuple(item.get(k, '') for k in dupe_keys)
            if key in seen:
                _record(reasons, audit, tag, False, 'duplicate')
                continue
            seen.add(key)

        # 6. Field bounds
        out_of_bounds = [
            f for f in field_validators
            if not _within_bounds(f, item.get(f), field_validators)
        ]
        if out_of_bounds:
            _record(reasons, audit, tag, False,
                    f'out_of_bounds:{out_of_bounds[0]}')
            continue

        # 7. Hallucination heuristics
        if halluc.get('drop_if_all_pressures_blank', False):
            if all(_try_float(item.get(f)) is None for f in _PRESSURE_FIELDS):
                _record(reasons, audit, tag, False, 'no_pressures')
                continue

        min_svc = int(halluc.get('drop_if_service_too_short', 0) or 0)
        svc = item.get('service', '') or ''
        if min_svc and len(svc) < min_svc:
            _record(reasons, audit, tag, False, 'service_too_short')
            continue

        bad_tokens = [t.lower() for t in halluc.get('drop_if_service_contains', [])]
        if bad_tokens and any(b in svc.lower() for b in bad_tokens):
            _record(reasons, audit, tag, False, 'service_blacklisted')
            continue

        # 8. Canonicalise units in any string field
        if unit_aliases:
            for k, v in list(item.items()):
                if isinstance(v, str) and v:
                    item[k] = _canonicalise_units(v, unit_aliases)

        kept.append(item)
        _record(reasons, audit, tag, True, 'ok')

    summary = {
        'kept': len(kept),
        'dropped': len(instruments) - len(kept),
        'reasons': reasons,
    }
    logger.info(
        '[PressureValidator] kept=%d dropped=%d reasons=%s',
        summary['kept'], summary['dropped'], summary['reasons'],
    )
    return {
        'instruments': kept,
        'summary': summary,
        'audit': audit,
        'enabled': True,
    }
