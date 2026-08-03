"""BYOK Vision-AI line-tag extractor for P&ID Checker V2.

Uses OpenAI or Anthropic Vision APIs to identify pipeline line tags on a
P&ID drawing image. The user's API key is passed per-request — never
stored server-side.

All configuration is soft-coded at the top of this file: provider models,
render DPI, max image dimension, tokens, temperature, and the extraction
prompt.
"""
from __future__ import annotations

import base64
import io
import json
import re
import logging
from typing import Optional

import fitz  # PyMuPDF
from PIL import Image

Image.MAX_IMAGE_PIXELS = None

logger = logging.getLogger(__name__)


# ─── Soft-coded config ────────────────────────────────────────────────
SUPPORTED_PROVIDERS = ('openai', 'claude')

VISION_MODELS = {
    'openai': 'gpt-4o',
    'claude': 'claude-sonnet-4-5-20250929',
}

VISION_MAX_TOKENS = 4096
VISION_TEMPERATURE = 0.0             # deterministic — we want factual extraction

# Determinism / anti-hallucination controls (soft-coded).
#   • VISION_SEED is passed to OpenAI's chat.completions.create as `seed=`.
#     gpt-4o honours it and produces reproducible outputs for the same image
#     + prompt + seed. Anthropic does not yet accept a seed parameter.
#   • VISION_CONSENSUS_MIN_HITS is the minimum number of independent Vision
#     passes (overview + tiles) a tag must appear in before we keep it.
#       - 1  = keep every tag (highest recall, some hallucinations pass through)
#       - 2  = balanced (recommended default — a real tag usually shows up in
#              the overview AND at least one tile because of the 15% overlap)
#       - 3+ = precision-first (may drop small edge-of-page tags)
#   • VISION_KEEP_UNCONFIRMED lets callers opt-out of the hit-count filter and
#     retain every tag regardless (useful for legend-driven extraction where
#     the user explicitly listed the rules).
#   • VISION_CONSENSUS_RECALL_FLOOR guards against consensus dropping too
#     many tags on drawings where a lot of items really do live at the edge
#     of a single tile. If consensus would keep fewer than this fraction
#     of the merged set, we downgrade min_hits to 1 for that run.
VISION_SEED = 42
VISION_CONSENSUS_MIN_HITS = 2
VISION_KEEP_UNCONFIRMED = False
VISION_CONSENSUS_RECALL_FLOOR = 0.70

# Retry policy for transient upstream errors (Claude 529 overloaded,
# OpenAI 429/500/502/503/504). Exponential backoff with jitter.
VISION_RETRY_MAX_ATTEMPTS = 4
VISION_RETRY_BASE_DELAY_S = 2.0
VISION_RETRY_MAX_DELAY_S = 30.0
VISION_RETRY_STATUS_CODES = (429, 500, 502, 503, 504, 529)

# ─── Tiling strategy ──────────────────────────────────────────────────
# Large P&IDs contain small, rotated, and peripheral tags. A single
# whole-page Vision call misses ~30-40 % of them because the image is
# either downscaled too far or the model's attention is diluted across a
# huge canvas. We therefore:
#   1) Render each PDF page at HIGH dpi
#   2) Do one "overview" pass on a downscaled full page (context)
#   3) Slice the high-res page into overlapping tiles and send each one
#      as its own Vision call
#   4) Merge & dedupe the tags across all calls
#
# Overlap is essential — a tag that straddles a tile edge would otherwise
# be split and lost.
VISION_RENDER_DPI = 300              # high-res source render
VISION_TILE_ROWS = 2                 # 2 × 2 tiles = 4 zoomed-in calls
VISION_TILE_COLS = 2
VISION_TILE_OVERLAP_FRAC = 0.15      # 15 % overlap between adjacent tiles
VISION_TILE_MAX_DIMENSION_PX = 2000  # each tile is downscaled to this longest side
VISION_OVERVIEW_MAX_DIMENSION_PX = 2000  # full-page overview pass
VISION_INCLUDE_OVERVIEW = True        # set False to skip full-page pass (save 1 call/page)

# Line-tag pattern (mirrors line_tag_extractor.py) — used to filter
# hallucinated / malformed tags the model may return.
LINE_TAG_PATTERN = re.compile(
    r'^(\d{1,2}(?:[-/]\d{1,2}(?:/\d)?)?)"\-'
    r'([A-Z]{2,4})\-'
    r'([A-Z0-9]{3,6})\-'
    r'(\d{3,5})$'
)

SERVICE_GROUPS = {
    'FL': 'Flare', 'SG': 'Sour Gas', 'FG': 'Fuel Gas',
    'PL': 'Pipeline', 'CD': 'Closed Drain', 'OW': 'Oily Water',
    'PW': 'Produced Water', 'IA': 'Instrument Air', 'PA': 'Plant Air',
    'NG': 'Natural Gas', 'HC': 'Hydrocarbon',
}

VISION_SYSTEM_PROMPT = (
    "You are an expert process engineer specialised in reading P&ID drawings. "
    "Your task is to enumerate every unique pipeline line tag on the drawing."
)

VISION_USER_PROMPT = """Extract EVERY unique pipeline line tag visible in this P&ID image.

A pipeline line tag has the exact format:  SIZE"-SERVICE-SPEC-SERIAL
Examples: 8"-FL-AC6N-8114, 2"-SG-AC3N-8110, 3/4"-CD-AC3N-8263, 20"-PL-DC3N-8106,
          12"-PL-AC3N-8114, 1-1/2"-IA-AC3N-8201

Field rules:
- SIZE = integer (2, 4, 20) OR fraction (3/4, 1-1/2), always with a trailing "
- SERVICE = 2-4 uppercase letters (FL, SG, CD, PL, FG, OW, PW, IA, PA, NG, HC, VT, BD, ...)
- SPEC = 3-6 alphanumerics starting with letters, containing a digit (AC3N, DC3N, AC6N, AC3)
- SERIAL = 3-5 digits (8112, 8106, 8263)

SCAN THE ENTIRE IMAGE METHODICALLY:
- Sweep top to bottom, left to right
- Look at every corner and margin — tags often sit at the far edges
- Read text that is rotated 90° or 270° along vertical pipe runs
- Include tiny tags on drain, vent, blowdown, sample, purge and utility lines
- Include tags labelled on branches connecting to instruments, PSVs, and vessels
- Recheck tags that touch or overlap with equipment symbols

EXCLUDE strictly:
- Equipment tags (e.g. V-803-TF, P-801-A, E-401, T-101)
- Instrument tags (e.g. PT-8001, FT-8103, LI-2, PSV-8006)
- Reference document numbers (e.g. PJ6-EXD-MRI-BQDA-0023)
- Note / type callouts (NOTE 4, TYPE 8, DETAIL A)
- Any string that does NOT match the SIZE"-SERVICE-SPEC-SERIAL pattern

Return ONLY a JSON array of strings — no prose, no markdown fences:
["6\\"-FL-AC6N-8112", "2\\"-SG-AC3N-8110", "3/4\\"-CD-AC3N-8263", ...]

Be exhaustive: a typical process P&ID contains 15–40 line tags. If you find fewer than 15, you have almost certainly missed some — re-scan every corner and rotated label before finalising.
"""


# ─── Public API ───────────────────────────────────────────────────────
def extract_line_tags_via_vision(
    pdf_bytes: bytes,
    provider: str,
    api_key: str,
    *,
    legend_prompt: str | None = None,
) -> dict:
    """Return dict with merged, deduped tags from multi-tile Vision passes.

    If ``legend_prompt`` is provided (from a user's active Legend Sheet) it
    replaces the built-in tag-format rules — everything else in the prompt
    (scan strategy, exclusions, JSON output format) stays the same.
    """
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider '{provider}'. Choose one of {SUPPORTED_PROVIDERS}.")
    if not api_key or not api_key.strip():
        raise ValueError("api_key is required for Vision extraction")

    user_prompt = _compose_user_prompt(legend_prompt)

    from .token_accounting import UsageMeter
    meter = UsageMeter(feature='line_extraction')
    all_raw: list[str] = []
    all_tags: dict[str, dict] = {}
    hit_counts: dict[str, int] = {}
    call_count = 0

    for page_idx, page_image in enumerate(_render_pages(pdf_bytes)):
        # 1) Optional low-res overview pass — helps the model see the drawing layout.
        if VISION_INCLUDE_OVERVIEW:
            overview = _downscale(page_image, VISION_OVERVIEW_MAX_DIMENSION_PX)
            raw, in_t, out_t = _call_vision(provider, api_key, _image_to_b64_png(overview), user_prompt)
            meter.add(provider, VISION_MODELS[provider], in_t, out_t)
            call_count += 1
            all_raw.append(f'[page {page_idx} overview]\n{raw}')
            seen_in_pass: set[str] = set()
            for tag in _parse_tag_list(raw):
                all_tags.setdefault(tag['tag'], tag)
                if tag['tag'] not in seen_in_pass:
                    hit_counts[tag['tag']] = hit_counts.get(tag['tag'], 0) + 1
                    seen_in_pass.add(tag['tag'])

        # 2) High-detail overlapping tile passes.
        for tile_idx, tile in enumerate(_tile_image(page_image,
                                                   VISION_TILE_ROWS,
                                                   VISION_TILE_COLS,
                                                   VISION_TILE_OVERLAP_FRAC)):
            tile = _downscale(tile, VISION_TILE_MAX_DIMENSION_PX)
            raw, in_t, out_t = _call_vision(provider, api_key, _image_to_b64_png(tile), user_prompt)
            meter.add(provider, VISION_MODELS[provider], in_t, out_t)
            call_count += 1
            all_raw.append(f'[page {page_idx} tile {tile_idx}]\n{raw}')
            seen_in_pass = set()
            for tag in _parse_tag_list(raw):
                all_tags.setdefault(tag['tag'], tag)
                if tag['tag'] not in seen_in_pass:
                    hit_counts[tag['tag']] = hit_counts.get(tag['tag'], 0) + 1
                    seen_in_pass.add(tag['tag'])

    # Consensus filter — a tag must have been seen by ≥N independent passes.
    # This is what tames run-to-run hallucination: a real tag reliably shows
    # up in the overview + at least one overlapping tile; a hallucinated tag
    # typically appears in only one pass and disappears on the next run.
    min_hits = 1 if VISION_KEEP_UNCONFIRMED else max(1, int(VISION_CONSENSUS_MIN_HITS))
    # If a single-pass run produced everything (VISION_INCLUDE_OVERVIEW=False,
    # 1×1 tiles), enforcing ≥2 would empty the result — degrade gracefully.
    if call_count <= 1:
        min_hits = 1
    # Recall floor guard — if the strict filter would keep less than the
    # allowed fraction of merged tags, back off to min_hits=1 for this run.
    if min_hits > 1 and all_tags:
        would_keep = sum(1 for t in all_tags if hit_counts.get(t, 0) >= min_hits)
        if would_keep < VISION_CONSENSUS_RECALL_FLOOR * len(all_tags):
            logger.info("[vision] consensus would keep %d/%d (<%.0f%%); relaxing min_hits→1",
                        would_keep, len(all_tags), VISION_CONSENSUS_RECALL_FLOOR * 100)
            min_hits = 1

    confirmed: dict[str, dict] = {}
    for tag_str, tag in all_tags.items():
        hits = hit_counts.get(tag_str, 0)
        enriched = dict(tag)
        enriched['hit_count'] = hits
        enriched['confidence'] = round(hits / max(call_count, 1), 3)
        if hits >= min_hits:
            confirmed[tag_str] = enriched

    dropped = len(all_tags) - len(confirmed)
    if dropped:
        logger.info("[vision] consensus filter kept %d/%d tags (min_hits=%d, passes=%d)",
                    len(confirmed), len(all_tags), min_hits, call_count)

    tags_sorted = sorted(confirmed.values(),
                         key=lambda t: (t.get('service') or '', _serial_int(t.get('serial') or ''), t.get('size') or ''))
    return {
        'provider': provider,
        'model': VISION_MODELS[provider],
        'tags': tags_sorted,
        'raw': '\n\n---\n\n'.join(all_raw),
        'call_count': call_count,
        'token_usage': meter.summary(),
        'consensus_min_hits': min_hits,
        'dropped_by_consensus': dropped,
    }


def _compose_user_prompt(legend_prompt: str | None) -> str:
    """If a Legend Sheet is active, prepend its rules block to the base prompt."""
    if not legend_prompt:
        return VISION_USER_PROMPT
    return (
        "Use the following LEGEND SHEET rules — they override any pattern you may know:\n"
        "──────────────────────────────────────────────────────────\n"
        f"{legend_prompt}\n"
        "──────────────────────────────────────────────────────────\n\n"
        + VISION_USER_PROMPT
    )


# ─── Helpers ──────────────────────────────────────────────────────────
def _serial_int(s: str) -> int:
    try:
        return int(s)
    except (TypeError, ValueError):
        return 0


def _render_pages(pdf_bytes: bytes) -> list[Image.Image]:
    """Render each PDF page to a high-res PIL image at VISION_RENDER_DPI."""
    pages: list[Image.Image] = []
    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    mat = fitz.Matrix(VISION_RENDER_DPI / 72, VISION_RENDER_DPI / 72)
    for page in doc:
        pix = page.get_pixmap(matrix=mat, alpha=False)
        pages.append(Image.open(io.BytesIO(pix.tobytes('png'))))
    return pages


def _tile_image(img: Image.Image, rows: int, cols: int, overlap_frac: float) -> list[Image.Image]:
    """Split an image into rows × cols overlapping tiles."""
    if rows <= 1 and cols <= 1:
        return [img]
    w, h = img.size
    tile_w = w / cols
    tile_h = h / rows
    ov_w = tile_w * overlap_frac
    ov_h = tile_h * overlap_frac
    tiles: list[Image.Image] = []
    for r in range(rows):
        for c in range(cols):
            left   = max(0, int(c * tile_w - ov_w))
            top    = max(0, int(r * tile_h - ov_h))
            right  = min(w, int((c + 1) * tile_w + ov_w))
            bottom = min(h, int((r + 1) * tile_h + ov_h))
            tiles.append(img.crop((left, top, right, bottom)))
    return tiles


def _image_to_b64_png(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    return base64.b64encode(buf.getvalue()).decode('utf-8')


def _downscale(img: Image.Image, max_dim: int) -> Image.Image:
    w, h = img.size
    longest = max(w, h)
    if longest <= max_dim:
        return img
    scale = max_dim / longest
    return img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)


def _parse_tag_list(raw: str) -> list[dict]:
    """Parse model output into structured tag dicts."""
    if not raw:
        return []
    # Strip common markdown fences the model may add despite instructions.
    text = raw.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)

    # Try direct JSON parse; fall back to extracting the first [...] block.
    candidates: list[str] = []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            candidates = [str(x) for x in parsed]
    except json.JSONDecodeError:
        m = re.search(r'\[.*\]', text, flags=re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group(0))
                if isinstance(parsed, list):
                    candidates = [str(x) for x in parsed]
            except json.JSONDecodeError:
                pass

    # Fallback: scrape line-shaped tokens straight from the text.
    if not candidates:
        candidates = re.findall(r'\d{1,2}(?:[-/]\d{1,2})?"?-[A-Z]{2,4}-[A-Z0-9]{3,6}-\d{3,5}', text)

    tags: list[dict] = []
    for cand in candidates:
        norm = _normalise_candidate(cand)
        if not norm:
            continue
        m = LINE_TAG_PATTERN.match(norm)
        if not m:
            continue
        size, service, spec, serial = m.groups()
        tags.append({
            'tag': norm,
            'size': size,
            'service': service,
            'spec': spec,
            'serial': serial,
            'service_group': SERVICE_GROUPS.get(service, service),
        })
    return tags


def _normalise_candidate(s: str) -> Optional[str]:
    """Coerce a raw candidate into the canonical  SIZE\"-SERVICE-SPEC-SERIAL  form."""
    if not s:
        return None
    s = s.strip().strip('"').strip("'")
    # Ensure we have a size-quote separator: "6-FL-AC6N-8112" → "6\"-FL-AC6N-8112"
    if '"-' not in s:
        m = re.match(r'^(\d{1,2}(?:[-/]\d{1,2})?)-([A-Z]{2,4}-)', s)
        if m:
            s = s.replace(m.group(1) + '-', m.group(1) + '"-', 1)
    return s


def _call_vision(provider: str, api_key: str, image_b64: str, user_prompt: str = VISION_USER_PROMPT) -> str:
    if provider == 'openai':
        fn = _call_openai
    elif provider == 'claude':
        fn = _call_claude
    else:
        raise ValueError(f"unknown provider {provider}")
    return _with_retries(provider, fn, api_key, image_b64, user_prompt)


def _with_retries(provider, fn, api_key, image_b64, user_prompt):
    import random
    import time
    last_exc = None
    for attempt in range(1, VISION_RETRY_MAX_ATTEMPTS + 1):
        try:
            return fn(api_key, image_b64, user_prompt)
        except Exception as exc:  # noqa: BLE001
            status = _extract_status_code(exc)
            retriable = status in VISION_RETRY_STATUS_CODES or _is_overloaded_error(exc)
            last_exc = exc
            if not retriable or attempt == VISION_RETRY_MAX_ATTEMPTS:
                raise
            delay = min(VISION_RETRY_BASE_DELAY_S * (2 ** (attempt - 1)),
                        VISION_RETRY_MAX_DELAY_S)
            delay += random.uniform(0, delay * 0.25)
            logger.warning(
                "[vision] %s transient error (status=%s attempt=%d/%d): %s — retrying in %.1fs",
                provider, status, attempt, VISION_RETRY_MAX_ATTEMPTS, exc, delay,
            )
            time.sleep(delay)
    raise last_exc  # pragma: no cover


def _extract_status_code(exc) -> Optional[int]:
    for attr in ('status_code', 'http_status', 'code'):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    resp = getattr(exc, 'response', None)
    if resp is not None:
        v = getattr(resp, 'status_code', None)
        if isinstance(v, int):
            return v
    # Fall back to parsing the message (e.g. "Error code: 529 - {...}")
    m = re.search(r'\b(4\d{2}|5\d{2})\b', str(exc))
    return int(m.group(1)) if m else None


def _is_overloaded_error(exc) -> bool:
    msg = str(exc).lower()
    return 'overloaded' in msg or 'overloaded_error' in msg


def _call_openai(api_key: str, image_b64: str, user_prompt: str = VISION_USER_PROMPT):
    import openai
    from .token_accounting import read_openai_usage
    client = openai.OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=VISION_MODELS['openai'],
        max_tokens=VISION_MAX_TOKENS,
        temperature=VISION_TEMPERATURE,
        seed=VISION_SEED,
        messages=[
            {'role': 'system', 'content': VISION_SYSTEM_PROMPT},
            {
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': user_prompt},
                    {
                        'type': 'image_url',
                        'image_url': {
                            'url': f'data:image/png;base64,{image_b64}',
                            'detail': 'high',
                        },
                    },
                ],
            },
        ],
    )
    text = resp.choices[0].message.content or ''
    inp, out = read_openai_usage(resp)
    return text, inp, out


def _call_claude(api_key: str, image_b64: str, user_prompt: str = VISION_USER_PROMPT):
    import anthropic
    from .token_accounting import read_claude_usage
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=VISION_MODELS['claude'],
        max_tokens=VISION_MAX_TOKENS,
        temperature=VISION_TEMPERATURE,
        system=VISION_SYSTEM_PROMPT,
        messages=[
            {
                'role': 'user',
                'content': [
                    {
                        'type': 'image',
                        'source': {
                            'type': 'base64',
                            'media_type': 'image/png',
                            'data': image_b64,
                        },
                    },
                    {'type': 'text', 'text': user_prompt},
                ],
            }
        ],
    )
    parts = [b.text for b in resp.content if getattr(b, 'type', None) == 'text']
    inp, out = read_claude_usage(resp)
    return ''.join(parts), inp, out
