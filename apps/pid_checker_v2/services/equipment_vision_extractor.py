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
    VISION_CONSENSUS_MIN_HITS,
    VISION_KEEP_UNCONFIRMED,
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
Each object has these fields:
  {
    "tag": "V-803-TF",
    "kind": "vessel",
    "description": "MRD Oil Slug Catcher",
    "attributes": {
      "nominal_capacity":    "5 m3",
      "length_tt":           "3500 mm",
      "diameter_id":         "1200 mm",
      "op_pressure":         "8 barg",
      "design_pressure_min": "FV",
      "design_pressure_max": "10 barg",
      "op_temp_min":         "25 C",
      "op_temp_max":         "60 C",
      "design_temp_min":     "-10 C",
      "design_temp_max":     "80 C",
      "material_shell":      "CS + 3 mm CA",
      "material_internal":   "SS 316L cladding",
      "trim":                "SS 316"
    }
  }

- kind        — one of: vessel, pump, compressor, exchanger, tank, column, filter,
                reactor, furnace, separator, blower, other
- description — free-text service / duty if visible on the drawing; empty string otherwise.
- attributes  — READ VERBATIM from the equipment data table / callouts that sit
                next to each tag on the drawing. Use whatever unit is printed
                on the drawing (do NOT convert). Use an empty string "" for any
                attribute that is not shown for that equipment. Do not invent
                values. The keys are FIXED — do not rename or add new keys.

Be exhaustive. A typical process P&ID has 3–15 pieces of equipment.
"""

# Kinds recognised from the model output; anything else collapses to "other".
KNOWN_KINDS = {
    'vessel', 'pump', 'compressor', 'exchanger', 'tank', 'column',
    'filter', 'reactor', 'furnace', 'separator', 'silencer', 'blower',
    'agitator', 'other',
}

# Canonical equipment attribute keys — single source of truth reused by the
# vision extractor, Excel parser, comparator service, and Excel exporter.
EQUIPMENT_ATTRIBUTE_KEYS = (
    'nominal_capacity',
    'length_tt',
    'diameter_id',
    'op_pressure',
    'design_pressure_min',
    'design_pressure_max',
    'op_temp_min',
    'op_temp_max',
    'design_temp_min',
    'design_temp_max',
    'material_shell',
    'material_internal',
    'trim',
)

# Human-readable labels for reporting / UI. Kept beside the key tuple so
# they can't drift out of sync.
EQUIPMENT_ATTRIBUTE_LABELS = {
    'nominal_capacity':    'Nominal Capacity',
    'length_tt':           'Length (T/T)',
    'diameter_id':         'Diameter (ID)',
    'op_pressure':         'Operating Pressure',
    'design_pressure_min': 'Design Pressure (min)',
    'design_pressure_max': 'Design Pressure (max)',
    'op_temp_min':         'Operating Temperature (min)',
    'op_temp_max':         'Operating Temperature (max)',
    'design_temp_min':     'Design Temperature (min)',
    'design_temp_max':     'Design Temperature (max)',
    'material_shell':      'Material of Shell',
    'material_internal':   'Material of Internal',
    'trim':                'Trim',
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
    hit_counts: dict[str, int] = {}
    call_count = 0

    from .token_accounting import UsageMeter
    meter = UsageMeter(feature='equipment_extraction')
    model = VISION_MODELS[provider]

    for page_idx, page_image in enumerate(_render_pages(pdf_bytes)):
        if VISION_INCLUDE_OVERVIEW:
            overview = _downscale(page_image, VISION_OVERVIEW_MAX_DIMENSION_PX)
            raw, in_t, out_t = _call_vision(provider, api_key, _image_to_b64_png(overview))
            meter.add(provider, model, in_t, out_t)
            call_count += 1
            all_raw.append(f'[page {page_idx} overview]\n{raw}')
            _merge_tags(merged, _parse_equipment_list(raw), hit_counts)

        for tile_idx, tile in enumerate(_tile_image(page_image,
                                                    VISION_TILE_ROWS,
                                                    VISION_TILE_COLS,
                                                    VISION_TILE_OVERLAP_FRAC)):
            tile = _downscale(tile, VISION_TILE_MAX_DIMENSION_PX)
            raw, in_t, out_t = _call_vision(provider, api_key, _image_to_b64_png(tile))
            meter.add(provider, model, in_t, out_t)
            call_count += 1
            all_raw.append(f'[page {page_idx} tile {tile_idx}]\n{raw}')
            _merge_tags(merged, _parse_equipment_list(raw), hit_counts)

    # Consensus filter — see vision_extractor.py for the rationale.
    min_hits = 1 if VISION_KEEP_UNCONFIRMED else max(1, int(VISION_CONSENSUS_MIN_HITS))
    if call_count <= 1:
        min_hits = 1
    confirmed: dict[str, dict] = {}
    for tag_str, tag in merged.items():
        hits = hit_counts.get(tag_str, 0)
        if hits >= min_hits:
            enriched = dict(tag)
            enriched['hit_count'] = hits
            enriched['confidence'] = round(hits / max(call_count, 1), 3)
            confirmed[tag_str] = enriched
    dropped = len(merged) - len(confirmed)
    if dropped:
        logger.info("[equipment-vision] consensus filter kept %d/%d tags (min_hits=%d, passes=%d)",
                    len(confirmed), len(merged), min_hits, call_count)

    tags_sorted = sorted(confirmed.values(), key=lambda t: (t.get('kind') or '', t.get('tag') or ''))
    return {
        'provider': provider,
        'model': model,
        'tags': tags_sorted,
        'raw': '\n\n---\n\n'.join(all_raw),
        'call_count': call_count,
        'token_usage': meter.summary(),
        'consensus_min_hits': min_hits,
        'dropped_by_consensus': dropped,
    }


# ─── Helpers ──────────────────────────────────────────────────────────
def _call_vision(provider: str, api_key: str, image_b64: str):
    return _shared_call_vision(provider, api_key, image_b64, VISION_USER_PROMPT)


def _merge_tags(merged: dict[str, dict], new_tags: list[dict], hit_counts: dict[str, int] | None = None) -> None:
    """Merge new tags into the accumulator, keeping the richest description
    and the richest non-empty value for each equipment attribute. Each unique
    tag observed in this pass increments its hit_count (used by the consensus
    filter to drop hallucinations that only ever appear in one pass)."""
    seen_in_pass: set[str] = set()
    for t in new_tags:
        tag = t.get('tag')
        if not tag:
            continue
        if hit_counts is not None and tag not in seen_in_pass:
            hit_counts[tag] = hit_counts.get(tag, 0) + 1
            seen_in_pass.add(tag)
        existing = merged.get(tag)
        if not existing:
            merged[tag] = t
            continue
        if not existing.get('description') and t.get('description'):
            existing['description'] = t['description']
        if existing.get('kind') == 'other' and t.get('kind') != 'other':
            existing['kind'] = t['kind']
        existing_attrs = existing.setdefault('attributes', {})
        for key in EQUIPMENT_ATTRIBUTE_KEYS:
            new_val = (t.get('attributes') or {}).get(key)
            if new_val and not existing_attrs.get(key):
                existing_attrs[key] = new_val


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
            attrs = {}
        elif isinstance(item, dict):
            tag = _clean_tag(item.get('tag') or item.get('name') or '')
            kind = str(item.get('kind') or '').strip().lower() or 'other'
            desc = str(item.get('description') or item.get('service') or '').strip()
            raw_attrs = item.get('attributes') or {}
            attrs = {}
            if isinstance(raw_attrs, dict):
                for key in EQUIPMENT_ATTRIBUTE_KEYS:
                    v = raw_attrs.get(key)
                    if v is None:
                        continue
                    s = str(v).strip()
                    if s and s.lower() not in ('n/a', 'na', '-', '--'):
                        attrs[key] = s
        else:
            continue

        if not tag or not EQUIPMENT_TAG_PATTERN.match(tag):
            continue
        if kind not in KNOWN_KINDS:
            kind = 'other'

        results.append({'tag': tag, 'kind': kind, 'description': desc, 'attributes': attrs})
    return results


def _clean_tag(s: str) -> str:
    if not s:
        return ''
    return re.sub(r'\s+', '', s.strip().upper())
