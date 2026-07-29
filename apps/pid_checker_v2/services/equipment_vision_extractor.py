"""BYOK Vision-AI equipment-tag extractor for P&ID Checker V2.

Mirrors ``vision_extractor.py`` (line-tag extractor) but with a prompt
tuned to identify **equipment tags** (vessels, pumps, exchangers, tanks,
compressors, columns…) on the drawing. The shared image tiling /
downscaling / API-call helpers are reused so both extractors stay in sync
whenever the tile strategy is tuned.

Return payload shape mirrors the line-tag extractor so the front-end
cross-check panel can consume both with the same code path::

    {
        'provider': 'openai'|'claude',
        'model':    <str>,
        'tags':     [{'tag': 'V-803-TF', 'kind': 'vessel',
                      'description': 'MRD Oil Slug Catcher'}, …],
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
)

logger = logging.getLogger(__name__)


# ─── Soft-coded config ────────────────────────────────────────────────
# Equipment kind → prefix map. Used both to steer the model and to
# classify results after parsing. Extend here for site-specific prefixes.
EQUIPMENT_KIND_PREFIXES = {
    'vessel':      ('V', 'D', 'K'),      # V-803-TF, D-101, K-202
    'pump':        ('P',),                # P-801-A/B
    'compressor':  ('C', 'K'),            # C-401, K-501 (rotating)
    'exchanger':   ('E', 'HX'),           # E-401, HX-1201
    'tank':        ('T', 'TK'),           # T-101, TK-002
    'column':      ('C', 'T'),            # C-301 (dist. column), T-401
    'filter':      ('F', 'FS'),           # F-101 strainer / filter
    'reactor':     ('R',),                # R-201
    'furnace':     ('H', 'F'),            # H-401 fired heater
    'separator':   ('S', 'V'),            # S-101 / V-101
    'silencer':    ('SL',),
    'blower':      ('B',),
    'agitator':    ('A', 'M'),
}

# Regex accepting the union of accepted equipment tag shapes. Optional
# site symbol (TF, CF, HF …) after the numeric block, optional /A|/B
# duty suffix, optional trailing "-###" letter for parallel trains.
EQUIPMENT_TAG_PATTERN = re.compile(
    r'^(?:[A-Z]{1,3})-\d{2,4}[A-Z]?(?:[-/][A-Z0-9]{1,3})?(?:-[A-Z]{2})?$'
)

VISION_SYSTEM_PROMPT = (
    "You are an expert process engineer specialised in reading P&ID drawings. "
    "Your task is to enumerate every unique equipment tag visible on the drawing "
    "(vessels, pumps, compressors, exchangers, tanks, columns, filters, reactors, "
    "furnaces, separators, silencers)."
)

VISION_USER_PROMPT = """Identify EVERY unique piece of process equipment on this P&ID image.

Equipment tags follow the shape:  PREFIX-NUMBER[SUFFIX][-SITE]
Examples:  V-803-TF, V-804-TF, P-801-A, P-801-B, E-401, HX-1201, T-101, C-301, K-501,
           F-101, R-201, S-101, D-102, TK-002.

Prefix legend (typical):
  V   = vessel / drum / KO drum      P   = pump                     E, HX = heat exchanger
  T, TK = tank / storage             C   = column / compressor      K     = compressor
  R   = reactor                      F, FS = filter / strainer      S     = separator
  H   = fired heater / furnace        B   = blower / fan            D     = drum

Rules:
- NUMBER is 2-4 digits (101, 803, 1201).
- Optional single-letter SUFFIX indicates parallel duty  (A, B, C).
- Optional 2-letter SITE symbol follows a dash  (TF = Mubarraz Island, CF, HF …).
- Ignore line tags (they contain a size like  4"-FL-AC6N-8112).
- Ignore instrument tags (LT-8019, PT-8003ATF, PSV-8006, FCV-8004B, SDV-8003TF).
- Ignore reference / drawing numbers (PJ6-EXD-MRI-BQDA-0023).
- Ignore NOTE / TYPE / DETAIL callouts.

SCAN THE ENTIRE IMAGE METHODICALLY:
- Look for tag boxes attached to vessels, pumps, exchangers, tanks.
- The equipment tag is usually printed inside or immediately next to the equipment symbol.
- The equipment title block at the top-left / top-right of the drawing often lists
  the main equipment tag and its description (e.g. "V-803-TF  MRD OIL SLUG CATCHER").
- Read text rotated 90° / 270°.

Return ONLY a JSON array of objects — no prose, no markdown fences.
Each object has three fields:
  {"tag": "V-803-TF", "kind": "vessel", "description": "MRD Oil Slug Catcher"}
  {"tag": "P-801-A",  "kind": "pump",   "description": ""}

- kind        — one of: vessel, pump, compressor, exchanger, tank, column, filter,
                reactor, furnace, separator, blower, other
- description — free-text service / duty if visible on the drawing; empty string otherwise.

Be exhaustive. A typical process P&ID has 3–15 pieces of equipment.
"""

# Kinds recognised from the model output; anything else collapses to "other".
KNOWN_KINDS = {
    'vessel', 'pump', 'compressor', 'exchanger', 'tank', 'column',
    'filter', 'reactor', 'furnace', 'separator', 'silencer', 'blower',
    'agitator', 'other',
}


# ─── Public API ───────────────────────────────────────────────────────
def extract_equipment_tags_via_vision(
    pdf_bytes: bytes,
    provider: str,
    api_key: str,
) -> dict:
    """Multi-tile Vision extraction of equipment tags from a P&ID PDF."""
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
            _merge_tags(merged, _parse_equipment_list(raw))

        for tile_idx, tile in enumerate(_tile_image(page_image,
                                                    VISION_TILE_ROWS,
                                                    VISION_TILE_COLS,
                                                    VISION_TILE_OVERLAP_FRAC)):
            tile = _downscale(tile, VISION_TILE_MAX_DIMENSION_PX)
            raw = _call_vision(provider, api_key, _image_to_b64_png(tile))
            call_count += 1
            all_raw.append(f'[page {page_idx} tile {tile_idx}]\n{raw}')
            _merge_tags(merged, _parse_equipment_list(raw))

    tags_sorted = sorted(merged.values(), key=lambda t: (t.get('kind') or '', t.get('tag') or ''))
    return {
        'provider': provider,
        'model': VISION_MODELS[provider],
        'tags': tags_sorted,
        'raw': '\n\n---\n\n'.join(all_raw),
        'call_count': call_count,
    }


# ─── Helpers ──────────────────────────────────────────────────────────
def _call_vision(provider: str, api_key: str, image_b64: str) -> str:
    if provider == 'openai':
        return _call_openai(api_key, image_b64, VISION_USER_PROMPT)
    if provider == 'claude':
        return _call_claude(api_key, image_b64, VISION_USER_PROMPT)
    raise ValueError(f"unknown provider {provider}")


def _merge_tags(merged: dict[str, dict], new_tags: list[dict]) -> None:
    """Merge new tags into the accumulator, keeping the richest description."""
    for t in new_tags:
        tag = t.get('tag')
        if not tag:
            continue
        existing = merged.get(tag)
        if not existing:
            merged[tag] = t
            continue
        # Prefer the entry with a description
        if not existing.get('description') and t.get('description'):
            existing['description'] = t['description']
        if existing.get('kind') == 'other' and t.get('kind') != 'other':
            existing['kind'] = t['kind']


def _parse_equipment_list(raw: str) -> list[dict]:
    """Parse model output into structured equipment dicts."""
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
        # Fallback: scrape tag-shaped tokens from free text.
        parsed = [
            {'tag': tok}
            for tok in re.findall(r'[A-Z]{1,3}-\d{2,4}[A-Z]?(?:[-/][A-Z0-9]{1,3})?(?:-[A-Z]{2})?', text)
        ]

    results: list[dict] = []
    for item in parsed:
        if isinstance(item, str):
            tag = _clean_tag(item)
            kind = 'other'
            desc = ''
        elif isinstance(item, dict):
            tag = _clean_tag(item.get('tag') or item.get('name') or '')
            kind = str(item.get('kind') or '').strip().lower() or 'other'
            desc = str(item.get('description') or item.get('service') or '').strip()
        else:
            continue

        if not tag or not EQUIPMENT_TAG_PATTERN.match(tag):
            continue
        if kind not in KNOWN_KINDS:
            kind = 'other'

        results.append({'tag': tag, 'kind': kind, 'description': desc})
    return results


def _clean_tag(s: str) -> str:
    if not s:
        return ''
    return re.sub(r'\s+', '', s.strip().upper())
