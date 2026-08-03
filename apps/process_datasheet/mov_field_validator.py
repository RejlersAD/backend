"""
MOV Field Validator
===================

Additive soft-coded post-processor that runs **after**
``apps.process_datasheet.tag_validator.validate_and_filter_valves`` and **before**
the Excel generator.

Responsibilities
----------------
* Drop duplicate valves (same ``tag_no``).
* Drop rows whose ``service`` field looks fabricated (placeholder text).
* Scrub out-of-range numeric fields (sets them to ``''`` so the user can correct
  them) without dropping the entire row.
* Normalise string casing / blank tokens.

The function is a no-op when the JSON config is missing or has
``"enabled": false``.

Public entry point
------------------
    validate_mov_fields(valves) -> dict
        {
            "valves":  [...kept...],
            "summary": {"kept": int, "dropped": int, "scrubbed": int,
                        "reasons": {"<reason>": int, ...}},
            "audit":   [{"tag": "...", "kept": bool, "reason": "..."}, ...],
            "enabled": bool,
        }
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent / 'config' / 'mov_field_validation.json'
_CONFIG_CACHE: Dict[str, Any] | None = None


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


def _is_blank(value: Any, blank_tokens: Iterable[str]) -> bool:
    if value is None:
        return True
    s = str(value).strip()
    return s == '' or s in set(blank_tokens)


def _try_float(value: Any) -> Optional[float]:
    if value is None or value == '':
        return None
    try:
        m = re.search(r'[-+]?\d+(?:\.\d+)?', str(value))
        return float(m.group(0)) if m else None
    except (TypeError, ValueError):
        return None


def _record(reasons: Dict[str, int], audit: List[Dict[str, Any]],
            tag: str, kept: bool, reason: str) -> None:
    audit.append({'tag': tag, 'kept': kept, 'reason': reason})
    reasons[reason] = reasons.get(reason, 0) + 1


def validate_mov_fields(valves: List[Dict[str, Any]] | None) -> Dict[str, Any]:
    valves = valves or []
    cfg = _load_config()

    if not cfg.get('enabled', False):
        return {
            'valves': valves,
            'summary': {'kept': len(valves), 'dropped': 0,
                        'scrubbed': 0, 'reasons': {}},
            'audit': [],
            'enabled': False,
        }

    sn_cfg          = cfg.get('string_normalisation', {})
    blank_tokens    = sn_cfg.get('blank_tokens', [])
    upper_tag       = bool(sn_cfg.get('uppercase_tag', True))
    trim_ws         = bool(sn_cfg.get('trim_whitespace', True))
    drop_dupes      = bool(cfg.get('drop_if_duplicate', True))
    dupe_keys       = cfg.get('duplicate_keys', ['tag_no'])
    field_rules     = cfg.get('field_validators', {})
    halluc          = cfg.get('hallucination_filters', {})
    drop_blank_tag  = bool(halluc.get('drop_if_blank_tag', True))
    bad_svc_tokens  = [t.lower() for t in halluc.get('drop_if_service_contains', [])]

    kept: List[Dict[str, Any]] = []
    audit: List[Dict[str, Any]] = []
    reasons: Dict[str, int] = {}
    seen: set[Tuple] = set()
    scrubbed_count = 0

    for raw in valves:
        item = dict(raw)

        # String normalisation
        for k, v in list(item.items()):
            if isinstance(v, str):
                vs = v
                if trim_ws:
                    vs = vs.strip()
                if _is_blank(vs, blank_tokens):
                    vs = ''
                item[k] = vs

        tag = (item.get('tag_no') or item.get('tag') or '')
        if upper_tag and tag:
            tag = tag.upper()
            item['tag_no'] = tag

        # Drop blank tag
        if drop_blank_tag and not tag:
            _record(reasons, audit, tag, False, 'blank_tag')
            continue

        # Duplicate detection
        if drop_dupes:
            key = tuple(item.get(k, '') for k in dupe_keys)
            if key in seen:
                _record(reasons, audit, tag, False, 'duplicate')
                continue
            seen.add(key)

        # Service hallucination
        svc = (item.get('service') or '').lower()
        if bad_svc_tokens and any(b in svc for b in bad_svc_tokens):
            _record(reasons, audit, tag, False, 'service_blacklisted')
            continue

        # Numeric scrubbing — only blank the bad cell, keep the row
        for field, rule in field_rules.items():
            if field not in item:
                continue
            n = _try_float(item.get(field))
            if n is None:
                continue
            mn = rule.get('min', float('-inf'))
            mx = rule.get('max', float('inf'))
            if not (mn <= n <= mx):
                logger.info(
                    "[MOVValidator] tag=%s scrubbed %s=%s (out of [%s, %s])",
                    tag, field, item.get(field), mn, mx,
                )
                item[field] = ''
                scrubbed_count += 1
                if rule.get('scrub_only', True) is False:
                    _record(reasons, audit, tag, False,
                            f'out_of_bounds:{field}')
                    item = None
                    break
        if item is None:
            continue

        kept.append(item)
        _record(reasons, audit, tag, True, 'ok')

    summary = {
        'kept': len(kept),
        'dropped': len(valves) - len(kept),
        'scrubbed': scrubbed_count,
        'reasons': reasons,
    }
    logger.info(
        '[MOVValidator] kept=%d dropped=%d scrubbed=%d reasons=%s',
        summary['kept'], summary['dropped'], summary['scrubbed'],
        summary['reasons'],
    )
    return {
        'valves': kept,
        'summary': summary,
        'audit': audit,
        'enabled': True,
    }
