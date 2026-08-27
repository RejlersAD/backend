"""BYOK Vision-AI symbol recognition for P&ID Checker V2.

Given a P&ID drawing and a project's manually-uploaded reference symbol
pictures (LegendSymbolImage — see SymbolImageUploadView), asks a Vision
model to identify which of those symbols appear on the drawing. This is a
separate, best-effort feature — unlike the line/equipment/instrument tag
extractors it does not aim for exhaustive coverage or exact tag-format
matching; every result carries a confidence level and is explicitly
flagged as needing engineer verification.

Reuses the shared PDF-rendering / downscaling / retry helpers from
``vision_extractor.py`` so behaviour (DPI, retry policy, provider config)
stays consistent with the rest of the BYOK Vision system. Text extraction
(line tags, equipment tags, instrument tags) is untouched by this module.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from .vision_extractor import (
    SUPPORTED_PROVIDERS,
    VISION_MODELS,
    VISION_MODEL_CLAUDE_FALLBACK,
    VISION_MAX_TOKENS,
    VISION_TEMPERATURE,
    VISION_REQUEST_TIMEOUT_S,
    VISION_OVERVIEW_MAX_DIMENSION_PX,
    VISION_TILE_ROWS,
    VISION_TILE_COLS,
    VISION_TILE_OVERLAP_FRAC,
    VISION_TILE_MAX_DIMENSION_PX,
    _render_pages,
    _prepare_image_b64,
    _tile_image,
    _with_retries,
    _is_model_not_found_error,
)

logger = logging.getLogger(__name__)


# ─── Soft-coded config ────────────────────────────────────────────────
VALID_CONFIDENCE_LEVELS = ('high', 'medium', 'low')
RESULT_NOTE = 'AI best guess - verify'

# Vision providers cap how many images fit in one request. The reference
# set (a project's uploaded LegendSymbolImage pictures) is split into
# batches of this size; every P&ID page/tile gets one Vision call PER
# BATCH, so a project with many uploaded pictures means proportionally
# more calls — pass thorough=False unless the tile pass is also needed.
SYMBOL_IMAGES_PER_BATCH = 20

# How many Vision calls to run concurrently (I/O-bound, so this is safe —
# see the ThreadPoolExecutor usage below). Keeps wall-clock time roughly
# proportional to (number of calls / this), instead of the full serial sum,
# so a project with many uploaded reference pictures doesn't blow past the
# frontend's request timeout.
VISION_CONCURRENT_CALLS = 4

VISION_SYSTEM_PROMPT = (
    "You are an expert process engineer specialised in reading P&ID drawings. "
    "Your task is to identify symbols on a drawing by comparing them against "
    "a set of labeled reference pictures the user provides alongside it."
)

VISION_USER_PROMPT_TEMPLATE = """This may be a scanned P&ID engineering drawing — image quality,
contrast, or resolution may be low, and symbol linework may be faint,
skewed, or partially obscured. Compare shapes carefully even when the
drawing isn't crisp; a rough match to a reference picture is still worth
reporting at lower confidence rather than skipping it.

I am providing:
1. The P&ID drawing image
2. A set of reference symbol pictures, each preceded by a text label naming
   the exact symbol it shows — these were manually curated and uploaded by
   an engineer, not extracted from a legend sheet PDF

Identify ALL symbols visible in the P&ID that match one of the reference
pictures.

For each symbol found, provide:
- Symbol type (use the EXACT label text from the matching reference picture)
- Approximate location (top/middle/bottom, left/center/right)
- Confidence level (high/medium/low)

All known symbol names for this project (not every one has a reference
picture uploaded yet):
{known_symbols}

Confidence guide:
- HIGH: the shape clearly and unambiguously matches a reference picture
- MEDIUM: a reasonable match, but linework is faint/partial or the shape is
  slightly different from the reference
- LOW: a rough guess — the area is unclear but resembles this symbol type

Important:
- Match against the reference PICTURES first — use the text list above only
  to recognise a symbol name that doesn't have a picture yet
- Even on a low-quality scan, report your best-guess match at LOW confidence
  rather than omitting it entirely
- Do not invent a symbol type that has no resemblance at all to what's shown
- All results need engineer verification

Return ONLY a JSON array of objects — no prose, no markdown fences. Each object:
  {{"symbol_type": "GATE VALVE (NORMAL OPEN)", "location": "top-left", "confidence": "high"}}
"""


# ─── Public API ───────────────────────────────────────────────────────
def identify_symbols_via_vision(
    pid_pdf_bytes: bytes,
    legend_symbol_images: list[dict],
    api_key: str,
    provider: str = "claude",
    thorough: bool = False,
    model: str | None = None,
) -> dict:
    """Identify P&ID symbols by comparing against manually-uploaded
    reference pictures (LegendSymbolImage), not a Legends.pdf.

    ``legend_symbol_images`` is a list of ``{'symbol_type': str, 'b64': str}``
    — each ``b64`` is the already-normalized (200x200 PNG) picture's raw
    base64, straight from LegendSymbolImage.image_file, no re-rendering
    needed. Each picture is sent with an explicit text label right before
    it, since — unlike a legend PDF page — a small isolated picture carries
    no name information on its own.

    ``model`` optionally overrides VISION_MODELS[provider] for this call
    only (e.g. the "Claude Model" dropdown in PIDVerification.jsx) —
    callers should validate it against ALLOWED_CLAUDE_VISION_MODELS before
    passing it through, since it reaches the provider SDK directly.

    thorough=False (default): one overview call per P&ID page per legend
    image batch. thorough=True: adds the same 2x2 overlapping-tile pass the
    line-tag extractor uses, multiplied by the same per-batch factor.
    Results are deduplicated afterwards since overlapping tiles/batches can
    report the same symbol instance more than once.

    Returns::

        {
            'provider': 'openai'|'claude',
            'model':    <str>,
            'reference_source': 'uploaded_symbol_images',
            'reference_image_count': <int>,
            'symbols':  [{'symbol_type', 'location', 'confidence', 'note'}, ...],
            'total_count': <int>,
            'call_count': <int>,
            'raw':      <str>,
            'token_usage': <UsageMeter.summary()>,
        }
    """
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider '{provider}'. Choose one of {SUPPORTED_PROVIDERS}.")
    if not api_key or not api_key.strip():
        raise ValueError("api_key is required for Vision extraction")
    if not pid_pdf_bytes:
        raise ValueError("pid_pdf_bytes is required")
    if not legend_symbol_images:
        raise ValueError(
            "legend_symbol_images is required — no reference pictures have "
            "been uploaded for this project yet (Legend Sheets → Reference "
            "pictures → Upload)."
        )

    user_prompt = VISION_USER_PROMPT_TEMPLATE.format(known_symbols=_build_known_symbols_block())

    legend_batches = [
        legend_symbol_images[i:i + SYMBOL_IMAGES_PER_BATCH]
        for i in range(0, len(legend_symbol_images), SYMBOL_IMAGES_PER_BATCH)
    ]

    from .token_accounting import UsageMeter
    meter = UsageMeter(feature='symbol_identification')
    resolved_model = model or VISION_MODELS[provider]

    # Build the full list of (label, image_b64) calls to make up front. A
    # project with many uploaded reference pictures means many legend
    # batches — run them concurrently rather than one-at-a-time, or a
    # project like one with ~184 pictures (10 batches) would take 10x a
    # single call's latency *per page*, easily blowing past the frontend's
    # request timeout even in "quick" mode. Each call is independent (its
    # own HTTP request, no shared mutable state), so a bounded thread pool
    # is safe here — this is I/O-bound waiting on the Vision API, not CPU
    # work fighting the GIL.
    jobs: list[tuple[str, str, list[dict]]] = []
    for page_idx, pid_page in enumerate(_render_pages(pid_pdf_bytes)):
        overview_b64 = _prepare_image_b64(pid_page, VISION_OVERVIEW_MAX_DIMENSION_PX)
        for batch_idx, batch in enumerate(legend_batches):
            jobs.append((f'page {page_idx} overview / symbols batch {batch_idx}', overview_b64, batch))

        if thorough:
            for tile_idx, tile in enumerate(_tile_image(pid_page, VISION_TILE_ROWS,
                                                          VISION_TILE_COLS, VISION_TILE_OVERLAP_FRAC)):
                tile_b64 = _prepare_image_b64(tile, VISION_TILE_MAX_DIMENSION_PX)
                for batch_idx, batch in enumerate(legend_batches):
                    jobs.append((f'page {page_idx} tile {tile_idx} / symbols batch {batch_idx}', tile_b64, batch))

    from concurrent.futures import ThreadPoolExecutor

    all_raw: list[str] = []
    symbols: list[dict] = []
    call_count = 0

    with ThreadPoolExecutor(max_workers=min(VISION_CONCURRENT_CALLS, len(jobs) or 1)) as pool:
        futures = {
            pool.submit(_call_vision_labeled, provider, api_key, image_b64, batch, user_prompt, model): label
            for label, image_b64, batch in jobs
        }
        for future in futures:
            label = futures[future]
            raw, in_t, out_t = future.result()
            meter.add(provider, resolved_model, in_t, out_t)
            call_count += 1
            all_raw.append(f'[{label}]\n{raw}')
            symbols.extend(_parse_symbol_list(raw))

    symbols = _dedupe_symbols(symbols)

    return {
        'provider': provider,
        'model': resolved_model,
        'thorough': thorough,
        'reference_source': 'uploaded_symbol_images',
        'reference_image_count': len(legend_symbol_images),
        'symbols': symbols,
        'total_count': len(symbols),
        'call_count': call_count,
        'raw': '\n\n---\n\n'.join(all_raw),
        'token_usage': meter.summary(),
    }


# ─── Helpers ──────────────────────────────────────────────────────────
def _dedupe_symbols(symbols: list[dict]) -> list[dict]:
    """Collapse exact (symbol_type, location, confidence) repeats.

    Overlapping tiles/legend batches — and the overview pass covering the
    same area a tile also covers — can report the same physical symbol
    instance more than once. Order is preserved (first occurrence wins).
    """
    seen: set[tuple] = set()
    out: list[dict] = []
    for s in symbols:
        key = (s['symbol_type'], s['location'], s['confidence'])
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _build_known_symbols_block() -> str:
    """Flatten every symbol name registered in legend_defaults.py into a
    text block the prompt can reference, grouped by section label."""
    from ..legend_defaults import SECTIONS, SECTION_LABELS, DEFAULT_TEMPLATES

    lines: list[str] = []
    for section in SECTIONS:
        template = DEFAULT_TEMPLATES.get(section)
        if not template:
            continue
        names: set[str] = set()
        for field in template.get('definition', {}).get('fields', []):
            lookup = field.get('lookup') or {}
            names.update(str(v) for v in lookup.values() if v)
        if names:
            label = SECTION_LABELS.get(section, section)
            lines.append(f"- {label}: " + ", ".join(sorted(names)))
    return "\n".join(lines)


def _call_vision_labeled(provider: str, api_key: str, pid_b64: str,
                          legend_batch: list[dict], user_prompt: str, model: str | None = None):
    if provider == 'claude':
        fn = (lambda k, imgs, p: _call_claude_labeled(k, imgs, p, model=model)) if model else _call_claude_labeled
    else:
        fn = _call_openai_labeled
    effective_model = model or VISION_MODELS['claude']
    try:
        return _with_retries(provider, fn, api_key, (pid_b64, legend_batch), user_prompt)
    except Exception as exc:  # noqa: BLE001
        if provider == 'claude' and effective_model != VISION_MODEL_CLAUDE_FALLBACK \
                and _is_model_not_found_error(exc):
            logger.warning(
                "[symbol_shape_extractor] model '%s' unavailable (%s) — "
                "retrying once with fallback model '%s'",
                effective_model, exc, VISION_MODEL_CLAUDE_FALLBACK,
            )
            fallback_fn = lambda k, imgs, p: _call_claude_labeled(k, imgs, p, model=VISION_MODEL_CLAUDE_FALLBACK)  # noqa: E731
            return _with_retries(provider, fallback_fn, api_key, (pid_b64, legend_batch), user_prompt)
        logger.error(
            "[symbol_shape_extractor] Vision call failed — provider=%s model=%s error_type=%s: %s",
            provider, effective_model if provider == 'claude' else VISION_MODELS.get(provider),
            type(exc).__name__, exc,
        )
        raise


def _call_claude_labeled(api_key: str, images: tuple, user_prompt: str, model: str | None = None):
    import anthropic
    from .token_accounting import read_claude_usage
    pid_b64, legend_batch = images
    client = anthropic.Anthropic(api_key=api_key, timeout=VISION_REQUEST_TIMEOUT_S)
    content = [
        {'type': 'text', 'text': 'P&ID drawing image:'},
        {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': pid_b64}},
        {'type': 'text', 'text': 'Reference symbol pictures (each labeled with its exact name):'},
    ]
    for item in legend_batch:
        content.append({'type': 'text', 'text': f"Symbol: {item['symbol_type']}"})
        content.append({'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': item['b64']}})
    content.append({'type': 'text', 'text': user_prompt})

    resp = client.messages.create(
        model=model or VISION_MODELS['claude'],
        max_tokens=VISION_MAX_TOKENS,
        # No `temperature` — see the matching comment in vision_extractor.py's
        # _call_claude(): Claude Sonnet 5 / Opus 5 reject any non-default
        # temperature/top_p/top_k with a 400 on every request (confirmed in
        # Anthropic's docs). OpenAI (_call_openai_labeled below) is unaffected.
        system=VISION_SYSTEM_PROMPT,
        messages=[{'role': 'user', 'content': content}],
    )
    parts = [b.text for b in resp.content if getattr(b, 'type', None) == 'text']
    inp, out = read_claude_usage(resp)
    return ''.join(parts), inp, out


def _call_openai_labeled(api_key: str, images: tuple, user_prompt: str):
    import openai
    from .token_accounting import read_openai_usage
    pid_b64, legend_batch = images
    client = openai.OpenAI(api_key=api_key, timeout=VISION_REQUEST_TIMEOUT_S)
    content = [
        {'type': 'text', 'text': user_prompt},
        {'type': 'text', 'text': 'P&ID drawing image:'},
        {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{pid_b64}', 'detail': 'high'}},
        {'type': 'text', 'text': 'Reference symbol pictures (each labeled with its exact name):'},
    ]
    for item in legend_batch:
        content.append({'type': 'text', 'text': f"Symbol: {item['symbol_type']}"})
        content.append({'type': 'image_url', 'image_url': {'url': f"data:image/png;base64,{item['b64']}", 'detail': 'high'}})

    resp = client.chat.completions.create(
        model=VISION_MODELS['openai'],
        max_tokens=VISION_MAX_TOKENS,
        temperature=VISION_TEMPERATURE,
        messages=[
            {'role': 'system', 'content': VISION_SYSTEM_PROMPT},
            {'role': 'user', 'content': content},
        ],
    )
    text = resp.choices[0].message.content or ''
    inp, out = read_openai_usage(resp)
    return text, inp, out


def _parse_symbol_list(raw: str) -> list[dict]:
    """Parse model output into structured symbol dicts."""
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

    if not parsed:
        return []

    results: list[dict] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        symbol_type = str(item.get('symbol_type') or '').strip()
        if not symbol_type:
            continue
        location = str(item.get('location') or '').strip() or 'unspecified'
        confidence = str(item.get('confidence') or '').strip().lower()
        if confidence not in VALID_CONFIDENCE_LEVELS:
            confidence = 'low'
        results.append({
            'symbol_type': symbol_type,
            'location': location,
            'confidence': confidence,
            'note': RESULT_NOTE,
        })
    return results
