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
The illustrative placeholders below show the SHAPE of a tag only — they are
NOT real values and do not appear on this drawing. Never copy one of these
placeholders into your output; every tag you report must come from
characters you actually observe in THIS image.
Placeholder shapes:  V-###-XX, P-###A, E-###, HX-####, T-###, C-###, K-###,
                     F-###, R-###, S-###, D-###, TK-###.

Prefix legend (typical):
  V   = vessel / drum / KO drum      P   = pump                     E, HX = heat exchanger
  T, TK = tank / storage             C   = column / compressor      K     = compressor
  R   = reactor                      F, FS = filter / strainer      S     = separator
  H   = fired heater / furnace        B   = blower / fan            D     = drum

Rules:
- NUMBER is 2-4 digits — read EVERY digit independently and carefully. Two
  tags can share the same PREFIX/SUFFIX/SITE and differ only in one NUMBER
  digit — do not let a tag you already reported bias your reading of a
  different, similar-looking tag elsewhere on the drawing. If a digit is
  genuinely ambiguous, give your best independent reading of THAT digit
  rather than defaulting to a digit you've already used.
- Optional single-letter SUFFIX indicates parallel duty  (A, B, C).
- Optional 2-letter SITE symbol follows a dash (a site/platform code specific
  to this project — read it exactly as printed).
- Ignore line tags (they contain a size like  4"-FL-AC6N-8112).
- Ignore instrument tags (e.g. LT-####, PT-####, PSV-####, FCV-####, SDV-####).
- Ignore reference / drawing numbers (e.g. PJ6-EXD-MRI-BQDA-####).
- Ignore NOTE / TYPE / DETAIL callouts.

SCAN THE ENTIRE IMAGE METHODICALLY:
- Look for tag boxes attached to vessels, pumps, exchangers, tanks.
- The equipment tag is usually printed inside or immediately next to the equipment symbol.
- The equipment title block at the top-left / top-right of the drawing often
  lists the main equipment tag alongside its description.
- Equipment tags are very often UNDERLINED. Treat the underline as pure
  decoration, not part of the text — do not let it merge visually with
  letters that have descenders, and do not skip a tag just because a line
  runs through or under it.
- Read text rotated 90° / 270°.

Return ONLY a JSON array of objects — no prose, no markdown fences.
Each object has these fields, and every value must be a literal transcription
of what you see in the image, not a copy of the placeholder shape above:
  {
    "tag": "<prefix>-<number><suffix?>-<site?>",
    "kind": "vessel",
    "description": "<service/duty text as printed, or empty string>",
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
                values — the sample attribute values above are illustrative
                only. The keys are FIXED — do not rename or add new keys.

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

# Providers whose Vision calls run at NON-zero, non-configurable temperature
# (see vision_extractor.py's VISION_TEMPERATURE comment — Claude Sonnet 5 /
# Opus 5 reject an explicit temperature parameter entirely) — meaning the
# same tile can be read slightly differently from one analysis run to the
# next. A tag that's genuinely hard to read (small text, poor scan quality,
# partial occlusion) can therefore come back correctly on one run and be
# silently absent on the next, with equipment_cross_check.py's fuzzy/AI
# pairing powerless to help (there's nothing to pair against when Vision
# simply omits the tag from that pass's output).
#
# Fix: for these providers, read every tile/overview TWICE (independent
# calls) and union the results via _merge_tags — a flaky tag only needs to
# survive ONE of two independent reads to end up in the final list. This
# roughly doubles Vision API cost/time for these providers; OpenAI (already
# deterministic at VISION_TEMPERATURE=0.0) is left single-pass.
VISION_NONDETERMINISTIC_PROVIDERS = {'claude'}
VISION_DOUBLE_PASS_READS = 2


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

    from .token_accounting import UsageMeter
    meter = UsageMeter(feature='equipment_extraction')
    model = VISION_MODELS[provider]

    # See VISION_NONDETERMINISTIC_PROVIDERS above: Claude ignores temperature
    # control, so each region is read multiple times and unioned to avoid a
    # legible-but-borderline tag flipping between Match and Missing across
    # runs with no code change in between.
    reads_per_region = VISION_DOUBLE_PASS_READS if provider in VISION_NONDETERMINISTIC_PROVIDERS else 1

    def _read_region(image_b64: str, label: str) -> None:
        nonlocal call_count
        for read_idx in range(reads_per_region):
            raw, in_t, out_t = _call_vision(provider, api_key, image_b64)
            meter.add(provider, model, in_t, out_t)
            call_count += 1
            read_tag = f' read {read_idx}' if reads_per_region > 1 else ''
            all_raw.append(f'[{label}{read_tag}]\n{raw}')
            _merge_tags(merged, _parse_equipment_list(raw))

    for page_idx, page_image in enumerate(_render_pages(pdf_bytes)):
        if VISION_INCLUDE_OVERVIEW:
            overview = _downscale(page_image, VISION_OVERVIEW_MAX_DIMENSION_PX)
            _read_region(_image_to_b64_png(overview), f'page {page_idx} overview')

        for tile_idx, tile in enumerate(_tile_image(page_image,
                                                    VISION_TILE_ROWS,
                                                    VISION_TILE_COLS,
                                                    VISION_TILE_OVERLAP_FRAC)):
            tile = _downscale(tile, VISION_TILE_MAX_DIMENSION_PX)
            _read_region(_image_to_b64_png(tile), f'page {page_idx} tile {tile_idx}')

    tags_sorted = sorted(merged.values(), key=lambda t: (t.get('kind') or '', t.get('tag') or ''))
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
    """Merge new tags into the accumulator, keeping the richest description
    and the richest non-empty value for each equipment attribute."""
    for t in new_tags:
        tag = t.get('tag')
        if not tag:
            continue
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
