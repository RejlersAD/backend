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
# Accept BOTH hyphenated (LT-8019, PCV-8004B) and un-hyphenated tags
# (SDV8005, FIC8002, PI8003A) — many drawings drop the hyphen.
INSTRUMENT_TAG_PATTERN = re.compile(
    r'^[A-Z]{1,4}-?\d{2,4}[A-Z]?(?:[A-Z]{2})?$'
)

# Instrument extraction runs at a denser tiling than line-tag extraction —
# instrument balloons are small and easy to miss on a 2 × 2 grid.
INSTRUMENT_TILE_ROWS = 3
INSTRUMENT_TILE_COLS = 3
INSTRUMENT_TILE_OVERLAP_FRAC = 0.20
INSTRUMENT_TILE_MAX_DIMENSION_PX = 2400
INSTRUMENT_OVERVIEW_MAX_DIMENSION_PX = 2400
# Extra verification pass at a coarser grid (2 × 2) — catches balloons
# straddling the dense-grid boundaries.
INSTRUMENT_SECOND_PASS_ENABLED = True
INSTRUMENT_SECOND_PASS_ROWS = 2
INSTRUMENT_SECOND_PASS_COLS = 2
INSTRUMENT_SECOND_PASS_OVERLAP_FRAC = 0.15

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

INSTRUMENT SYMBOL SHAPES (ISA-5.1) — read the tag inside each of these:
  (1) Plain CIRCLE                       → Field-mounted instrument.
  (2) CIRCLE WITH A HORIZONTAL LINE      → Instrument located on the
      cutting it into two halves            main control panel (primary).
  (3) Circle with a DOUBLE horizontal    → Auxiliary / local panel.
      line (two parallel lines)
  (4) Circle inside a SQUARE / hexagon   → DCS / PLC / computer function.
Every one of these balloons carries a tag inside — READ IT.

Tag shapes (both are valid, drawings often mix them):
  HYPHENATED :  LT-8019, LT-8019TF, PT-8003A, PCV-8004B, SDV-8003TF, FE-8001
  FUSED     :  SDV8005, FIC8002, LCV8002, CP8003, PI8003, PI8003A, PI8003B,
               PCV8004A, LT8019, PT8003ATF

Field rules:
- FUNC  = 1-4 uppercase ISA letters (LT, PT, FT, FIC, LIC, PCV, FCV, LCV,
          PSV, SDV, TIT, FIT, LG, FE, TW, PY, LY, CP, PI, TI, LI…).
- LOOP  = 2-4 digits, optional trailing single letter for parallel duty
          (8003A, 8004B).
- SITE  = optional 2-letter site symbol (TF = Mubarraz Island); may appear
          fused ("PT8003ATF") or space-separated ("LT-8019 TF").
- The hyphen between FUNC and LOOP is OPTIONAL.
  Preserve the tag EXACTLY as it appears on the drawing — do NOT insert
  a hyphen where the drawing omits one, and do NOT delete a hyphen where
  the drawing shows one.

INCLUDE:
- Transmitters, indicators, controllers, valves, switches, elements,
  PSVs, gauges, sight-glasses, orifice plates.
- Both field-mounted and panel-mounted balloons.
- Tags on branches to vessels, on flare / drain / vent lines.
- Balloons near the borders of the drawing — check every corner.

EXCLUDE strictly:
- Equipment tags (V-803-TF, P-801-A, E-401, T-101, K-501).
- Line tags (4"-FL-AC6N-8112, 20"-PL-DC3N-8106).
- Reference / drawing numbers (PJ6-EXD-MRI-BQDA-0023).
- Note / type callouts (NOTE 4, TYPE 8, DETAIL A).

SCAN THE ENTIRE IMAGE METHODICALLY:
- Start top-left, sweep row by row down to bottom-right.
- Read text rotated 90° / 270° along vertical lines.
- Do not stop after finding a few tags — a typical P&ID has 20-80.

Return ONLY a JSON array of objects — no prose, no markdown fences.
Each object has these fields (leave "" if not visible):
  {
    "tag": "SDV8005",
    "function_code": "SDV",
    "loop_number": "8005",
    "site_symbol": "",
    "service": "Shutdown valve",
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

Only strip whitespace INSIDE a tag (so "LT-8019 TF" → "LT-8019TF"); keep
the presence or absence of the hyphen exactly as drawn.

Be exhaustive. Miss nothing.
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

    from .token_accounting import UsageMeter
    meter = UsageMeter(feature='instrument_extraction')
    model = VISION_MODELS[provider]

    for page_idx, page_image in enumerate(_render_pages(pdf_bytes)):
        if VISION_INCLUDE_OVERVIEW:
            overview = _downscale(page_image, INSTRUMENT_OVERVIEW_MAX_DIMENSION_PX)
            raw, in_t, out_t = _call_vision(provider, api_key, _image_to_b64_png(overview))
            meter.add(provider, model, in_t, out_t)
            call_count += 1
            all_raw.append(f'[page {page_idx} overview]\n{raw}')
            _merge_tags(merged, _parse_instrument_list(raw))

        # Stage 1 — dense tiling for high recall on small balloons.
        for tile_idx, tile in enumerate(_tile_image(page_image,
                                                    INSTRUMENT_TILE_ROWS,
                                                    INSTRUMENT_TILE_COLS,
                                                    INSTRUMENT_TILE_OVERLAP_FRAC)):
            tile = _downscale(tile, INSTRUMENT_TILE_MAX_DIMENSION_PX)
            raw, in_t, out_t = _call_vision(provider, api_key, _image_to_b64_png(tile))
            meter.add(provider, model, in_t, out_t)
            call_count += 1
            all_raw.append(f'[page {page_idx} dense tile {tile_idx}]\n{raw}')
            _merge_tags(merged, _parse_instrument_list(raw))

        # Stage 2 — coarser verification pass at 2 × 2 catches balloons
        # sitting on the dense-grid seams.
        if INSTRUMENT_SECOND_PASS_ENABLED:
            for tile_idx, tile in enumerate(_tile_image(page_image,
                                                        INSTRUMENT_SECOND_PASS_ROWS,
                                                        INSTRUMENT_SECOND_PASS_COLS,
                                                        INSTRUMENT_SECOND_PASS_OVERLAP_FRAC)):
                tile = _downscale(tile, INSTRUMENT_TILE_MAX_DIMENSION_PX)
                raw, in_t, out_t = _call_vision(provider, api_key, _image_to_b64_png(tile))
                meter.add(provider, model, in_t, out_t)
                call_count += 1
                all_raw.append(f'[page {page_idx} verify tile {tile_idx}]\n{raw}')
                _merge_tags(merged, _parse_instrument_list(raw))

    tags_sorted = sorted(merged.values(),
                         key=lambda t: (t.get('function_code') or '', t.get('loop_number') or ''))
    return {
        'provider': provider,
        'model': model,
        'tags': tags_sorted,
        'raw': '\n\n---\n\n'.join(all_raw),
        'call_count': call_count,
        'token_usage': meter.summary(),
    }


# ─── Helpers ──────────────────────────────────────────────────────────
def _call_vision(provider: str, api_key: str, image_b64: str):
    return _shared_call_vision(provider, api_key, image_b64, VISION_USER_PROMPT)


def _merge_tags(merged: dict[str, dict], new_tags: list[dict]) -> None:
    for t in new_tags:
        tag = t.get('tag')
        if not tag:
            continue
        key = _canonical_key(tag)
        if not key:
            continue
        existing = merged.get(key)
        if not existing:
            merged[key] = t
            continue
        # Prefer the hyphenated form as the canonical display tag when
        # different tiles disagree.
        if '-' in tag and '-' not in existing.get('tag', ''):
            existing['tag'] = tag
        for field in ('function_code', 'loop_number', 'site_symbol'):
            if not existing.get(field) and t.get(field):
                existing[field] = t[field]
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


def _canonical_key(tag: str) -> str:
    """Normalise a tag for dedup: uppercase, no whitespace, no hyphen."""
    if not tag:
        return ''
    return re.sub(r'[\s\-]+', '', tag.strip().upper())


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
            for tok in re.findall(r'[A-Z]{1,4}-?\d{2,4}[A-Z]?(?:\s?[A-Z]{2})?', text)
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
