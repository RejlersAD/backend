"""BYOK Vision-AI instrument-tag extractor for P&ID Checker V2.

Mirrors ``vision_extractor.py`` (line-tag extractor) but with a prompt
tuned to identify **instrument tags** (ISA-5.1 loops such as LT-8019,
PT-8003ATF, PCV-8004B TF, SDV-8003TF, FE-8001) on the drawing.

Return payload shape mirrors the line-tag extractor::

    {
        'provider': 'openai'|'claude',
        'model':    <str>,
        'tags':     [{'tag': 'LT-8019TF', 'function_code': 'LT',
                      'loop_number': '8019', 'site_symbol': 'TF',
                      'service': 'Level transmitter'}, …],
        'call_count': <int>,
        'raw':      <str>,
    }
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from .vision_extractor import (
    SUPPORTED_PROVIDERS,
    VISION_MODELS,
    VISION_INCLUDE_OVERVIEW,
    VISION_TILE_ROWS,
    VISION_TILE_COLS,
    VISION_TILE_OVERLAP_FRAC,
    VISION_TILE_MAX_DIMENSION_PX,
    VISION_OVERVIEW_MAX_DIMENSION_PX,
    _render_pages,
    _tile_image,
    _downscale,
    _image_to_b64_png,
    _call_openai,
    _call_claude,
    _call_vision as _shared_call_vision,
)

logger = logging.getLogger(__name__)


# ─── Soft-coded config ────────────────────────────────────────────────
INSTRUMENT_TAG_PATTERN = re.compile(
    r'^[A-Z]{1,4}-\d{2,4}[A-Z]?(?:[A-Z]{2})?$'
)

# Attribute keys we ask Vision to read *verbatim* from the drawing and that
# the cross-check compares against the Instrument Index Excel row.  Keep
# this tuple aligned with EL/model column keys in instrument_cross_check.py.
INSTRUMENT_ATTRIBUTE_KEYS = (
    'instrument_type',
    'service_description',
    'range_min',
    'range_max',
    'range_unit',
    'cal_min',
    'cal_max',
    'cal_unit',
    'ex_class',
    'power_supply',
    'manufacturer',
    'model',
)

INSTRUMENT_ATTRIBUTE_LABELS = {
    'instrument_type':     'Type',
    'service_description': 'Service',
    'range_min':           'Range Min',
    'range_max':           'Range Max',
    'range_unit':          'Range Unit',
    'cal_min':             'Calibration Min',
    'cal_max':             'Calibration Max',
    'cal_unit':            'Calibration Unit',
    'ex_class':            'Ex Classification',
    'power_supply':        'Power Supply',
    'manufacturer':        'Manufacturer',
    'model':               'Model',
}

VISION_SYSTEM_PROMPT = (
    "You are an expert instrumentation engineer specialised in reading P&ID drawings. "
    "Your task is to enumerate every unique ISA-5.1 instrument loop tag visible on the drawing."
)

VISION_USER_PROMPT = """Identify EVERY unique instrument loop tag on this P&ID image.

Instrument tags follow the ISA-5.1 shape:  FUNC-LOOP[SUFFIX][SITE]
Examples:  LT-8019TF, LT-8019 TF, PT-8003A, PT-8003ATF, PCV-8004B TF,
           SDV-8003TF, FE-8001, PSV-8006, LI-2, FT-8103.

Field rules:
- FUNC  = 1-4 uppercase ISA function letters (LT, PT, FT, PCV, FCV, LCV, PSV, SDV, TIT, FIT, LG, FE, TW, PY, LY…).
- LOOP  = 2-4 digits, optional trailing single letter for parallel duty (8003A, 8004B).
- SITE  = optional 2-letter site symbol (TF = Mubarraz Island; may appear fused "PT-8003ATF"
          or space-separated "LT-8019 TF").

INCLUDE:
- Transmitters, indicators, controllers, valves, switches, elements, PSVs, gauges.
- Both field-mounted and panel-mounted symbols.
- Tags on branches to vessels, on flare / drain / vent lines.

EXCLUDE strictly:
- Equipment tags (V-803-TF, P-801-A, E-401, T-101, K-501).
- Line tags (4"-FL-AC6N-8112, 20"-PL-DC3N-8106).
- Reference / drawing numbers (PJ6-EXD-MRI-BQDA-0023).
- Note / type callouts (NOTE 4, TYPE 8, DETAIL A).

SCAN THE ENTIRE IMAGE METHODICALLY:
- Instruments are drawn as circles or hexagons; the tag sits inside the balloon.
- Read text rotated 90° / 270° along vertical lines.
- Include peripheral / marginal instruments.

Return ONLY a JSON array of objects — no prose, no markdown fences.
Each object has these fields (leave "" if not visible):
  {
    "tag": "LT-8019TF",
    "function_code": "LT",
    "loop_number": "8019",
    "site_symbol": "TF",
    "service": "Level transmitter",
    "attributes": {
      "instrument_type":     "",
      "service_description": "",
      "range_min":           "",
      "range_max":           "",
      "range_unit":          "",
      "cal_min":             "",
      "cal_max":             "",
      "cal_unit":            "",
      "ex_class":            "",
      "power_supply":        "",
      "manufacturer":        "",
      "model":               ""
    }
  }

Attribute rules — read verbatim from the drawing, DO NOT invent:
- The attribute keys above are fixed. Keep them exactly as shown.
- Read values in the original units and formatting on the drawing.
- Leave a value as "" (empty string) when the attribute is not visible.
- range_min / range_max = process range printed near the balloon
  (e.g. "0", "100" for "0-100 mmH2O"). range_unit = "mmH2O".
- cal_min / cal_max / cal_unit = calibrated range only if separately shown.
- ex_class = hazardous-area classification (e.g. "Ex ia IIC T4").
- power_supply = "24VDC", "230VAC", "Loop-powered", etc.

Canonicalise the tag: strip whitespace inside so "LT-8019 TF" becomes "LT-8019TF".

Be exhaustive. A typical process P&ID has 20-80 instrument tags.
"""


# ─── Public API ───────────────────────────────────────────────────────
def extract_instrument_tags_via_vision(
    pdf_bytes: bytes,
    provider: str,
    api_key: str,
) -> dict:
    """Multi-tile Vision extraction of instrument tags from a P&ID PDF."""
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider '{provider}'. Choose one of {SUPPORTED_PROVIDERS}.")
    if not api_key or not api_key.strip():
        raise ValueError("api_key is required for Vision extraction")

    all_raw: list[str] = []
    merged: dict[str, dict] = {}
    call_count = 0

    for page_idx, page_image in enumerate(_render_pages(pdf_bytes)):
        if VISION_INCLUDE_OVERVIEW:
            overview = _downscale(page_image, VISION_OVERVIEW_MAX_DIMENSION_PX)
            raw = _call_vision(provider, api_key, _image_to_b64_png(overview))
            call_count += 1
            all_raw.append(f'[page {page_idx} overview]\n{raw}')
            _merge_tags(merged, _parse_instrument_list(raw))

        for tile_idx, tile in enumerate(_tile_image(page_image,
                                                    VISION_TILE_ROWS,
                                                    VISION_TILE_COLS,
                                                    VISION_TILE_OVERLAP_FRAC)):
            tile = _downscale(tile, VISION_TILE_MAX_DIMENSION_PX)
            raw = _call_vision(provider, api_key, _image_to_b64_png(tile))
            call_count += 1
            all_raw.append(f'[page {page_idx} tile {tile_idx}]\n{raw}')
            _merge_tags(merged, _parse_instrument_list(raw))

    tags_sorted = sorted(merged.values(),
                         key=lambda t: (t.get('function_code') or '', t.get('loop_number') or ''))
    return {
        'provider': provider,
        'model': VISION_MODELS[provider],
        'tags': tags_sorted,
        'raw': '\n\n---\n\n'.join(all_raw),
        'call_count': call_count,
    }


# ─── Helpers ──────────────────────────────────────────────────────────
def _call_vision(provider: str, api_key: str, image_b64: str) -> str:
    return _shared_call_vision(provider, api_key, image_b64, VISION_USER_PROMPT)


def _merge_tags(merged: dict[str, dict], new_tags: list[dict]) -> None:
    for t in new_tags:
        tag = t.get('tag')
        if not tag:
            continue
        existing = merged.get(tag)
        if not existing:
            merged[tag] = t
            continue
        if not existing.get('service') and t.get('service'):
            existing['service'] = t['service']
        # Merge per-attribute — keep the richest non-empty value seen
        # across tiles for each key.
        cur_attrs = existing.setdefault('attributes', {})
        new_attrs = t.get('attributes') or {}
        for k in INSTRUMENT_ATTRIBUTE_KEYS:
            v_new = str(new_attrs.get(k) or '').strip()
            if not v_new:
                continue
            v_cur = str(cur_attrs.get(k) or '').strip()
            if not v_cur or len(v_new) > len(v_cur):
                cur_attrs[k] = v_new


def _parse_instrument_list(raw: str) -> list[dict]:
    if not raw:
        return []
    text = raw.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)

    parsed: Optional[list] = None
    try:
        candidate = json.loads(text)
        if isinstance(candidate, list):
            parsed = candidate
    except json.JSONDecodeError:
        m = re.search(r'\[.*\]', text, flags=re.DOTALL)
        if m:
            try:
                candidate = json.loads(m.group(0))
                if isinstance(candidate, list):
                    parsed = candidate
            except json.JSONDecodeError:
                parsed = None

    if parsed is None:
        parsed = [
            {'tag': tok}
            for tok in re.findall(r'[A-Z]{1,4}-\d{2,4}[A-Z]?(?:\s?[A-Z]{2})?', text)
        ]

    results: list[dict] = []
    for item in parsed:
        if isinstance(item, str):
            tag = _clean_tag(item)
            entry = {'tag': tag, 'function_code': '', 'loop_number': '',
                     'site_symbol': '', 'service': '', 'attributes': {}}
        elif isinstance(item, dict):
            tag = _clean_tag(item.get('tag') or item.get('name') or '')
            raw_attrs = item.get('attributes') or {}
            if not isinstance(raw_attrs, dict):
                raw_attrs = {}
            attrs: dict[str, str] = {}
            for k in INSTRUMENT_ATTRIBUTE_KEYS:
                v = str(raw_attrs.get(k) or '').strip()
                if v.lower() in ('n/a', 'na', '-', '--'):
                    v = ''
                attrs[k] = v
            entry = {
                'tag': tag,
                'function_code': str(item.get('function_code') or '').strip().upper(),
                'loop_number':   str(item.get('loop_number')   or '').strip(),
                'site_symbol':   str(item.get('site_symbol')   or '').strip().upper(),
                'service':       str(item.get('service')       or item.get('description') or '').strip(),
                'attributes':    attrs,
            }
        else:
            continue

        if not tag or not INSTRUMENT_TAG_PATTERN.match(tag):
            continue
        results.append(entry)
    return results


def _clean_tag(s: str) -> str:
    if not s:
        return ''
    return re.sub(r'\s+', '', s.strip().upper())
