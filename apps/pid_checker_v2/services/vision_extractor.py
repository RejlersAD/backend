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

# Claude model selection — env/setting-overridable so a new model can be
# picked up without a code change or deploy. VISION_MODEL_CLAUDE_FALLBACK
# is the previously-hardcoded model: if the configured/primary model call
# fails specifically because THAT MODEL isn't available (retired, typo'd,
# not yet enabled on the caller's account — not a generic transient
# error), one retry is made against the fallback automatically — see
# _call_vision() and _is_model_not_found_error() below.
VISION_MODEL_CLAUDE_LATEST = 'claude-sonnet-5'
VISION_MODEL_CLAUDE_OPUS = 'claude-opus-5'
VISION_MODEL_CLAUDE_FALLBACK = 'claude-sonnet-4-5-20250929'  # previous default

# Models a caller may explicitly request per-request (e.g. the "Claude
# Model" dropdown in PIDVerification.jsx) — validated against this in
# IdentifySymbolsView rather than trusting an arbitrary client-supplied
# string.
ALLOWED_CLAUDE_VISION_MODELS = (
    VISION_MODEL_CLAUDE_LATEST,
    VISION_MODEL_CLAUDE_OPUS,
    VISION_MODEL_CLAUDE_FALLBACK,
)


def _resolve_claude_vision_model() -> str:
    """CLAUDE_VISION_MODEL env var (checked first) or Django setting of the
    same name overrides the default; otherwise use the latest model."""
    import os
    override = os.environ.get('CLAUDE_VISION_MODEL', '').strip()
    if not override:
        try:
            from django.conf import settings
            override = (getattr(settings, 'CLAUDE_VISION_MODEL', '') or '').strip()
        except Exception:  # noqa: BLE001 — settings may not be configured yet (e.g. tooling)
            override = ''
    return override or VISION_MODEL_CLAUDE_LATEST


# Per-request timeout for the Vision provider SDK clients. Without this,
# a slow/stuck network path to the provider can hang for MINUTES per call
# (reproduced directly: an invalid key took 5m10s to come back as a 401)
# — far past the frontend's own request timeout, so the user sees a
# generic "failed" message while the backend is still silently working.
# Bounding each individual call keeps failures fast and diagnosable.
VISION_REQUEST_TIMEOUT_S = 45.0

VISION_MODELS = {
    'openai': 'gpt-4o',
    'claude': _resolve_claude_vision_model(),
}

VISION_MAX_TOKENS = 4096
VISION_TEMPERATURE = 0.0             # deterministic — we want factual extraction
                                      # OpenAI only — Claude Sonnet 5 / Opus 5 reject
                                      # any non-default temperature (see _call_claude)

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

VISION_USER_PROMPT = """This image may be a scanned P&ID engineering drawing — image quality,
contrast, or resolution may be low, and text may be faint, skewed, or
partially cut off. Look very carefully; a tag doesn't have to be perfectly
crisp to count, it just has to be your best honest reading of it.

Extract EVERY unique pipeline line tag visible in this P&ID image.

A pipeline line tag has the exact format:  SIZE"-SERVICE-SPEC-SERIAL
The illustrative placeholders below show the SHAPE of a tag only — they are
NOT real values and do not appear on this drawing. Never copy one of these
placeholders into your output; every tag you report must come from
characters you actually observe in THIS image.
Placeholder shape: N"-XX-YYNN-####   (e.g. 8"-FL-AC6N-####, 3/4"-CD-AC3N-####)

Field rules:
- SIZE = integer (2, 4, 20) OR fraction (3/4, 1-1/2), always with a trailing "
- SERVICE = 2-4 uppercase letters (FL, SG, CD, PL, FG, OW, PW, IA, PA, NG, HC, VT, BD, ...)
- SPEC = 3-6 alphanumerics starting with letters, containing a digit (e.g. AC3N, DC3N, AC6N)
- SERIAL = 3-5 digits — read EVERY digit independently and carefully. Two
  tags can share the same SIZE/SERVICE/SPEC and differ only in one SERIAL
  digit (e.g. a real drawing may have both ...-7263 and ...-8263 on it) —
  do not let a tag you already reported bias your reading of a different,
  similar-looking tag elsewhere on the drawing. If a digit is genuinely
  ambiguous, give your best independent reading of THAT digit and mark
  confidence LOW rather than defaulting to a digit you've already used.

SCAN THE ENTIRE IMAGE METHODICALLY:
- Sweep top to bottom, left to right
- Look at every corner and margin — tags often sit at the far edges
- Read text that is rotated 90° or 270° along vertical pipe runs, or skewed
  from an imperfect scan
- Include tiny tags on drain, vent, blowdown, sample, purge and utility lines
- Include tags labelled on branches connecting to instruments, PSVs, and vessels
- Recheck tags that touch or overlap with equipment symbols
- If a character is smudged, low-contrast, or ambiguous, give your single
  best reading rather than skipping the tag — just reflect that in confidence

EXCLUDE strictly (these are illustrative SHAPES only, not real values — do
not let them bias your reading of similar-looking text elsewhere):
- Equipment tags (shape PREFIX-###[-SITE], e.g. V-###-XX, P-###A).
- Instrument tags (shape FUNC-####, e.g. PT-####, FT-####, LI-#, PSV-####).
- Reference document numbers (shape PJ#-XXX-XXX-XXXX-####).
- Note / type callouts (NOTE #, TYPE #, DETAIL #).
- Any string that does NOT match the SIZE"-SERVICE-SPEC-SERIAL pattern

For each tag, also provide:
- Approximate location on the drawing (top/middle/bottom, left/center/right — e.g. "top-left")
- Confidence: HIGH (every character clearly legible), MEDIUM (mostly legible,
  one or two characters are a best guess), or LOW (significant portions were
  smudged/unclear and this is a rough reading)

Return ONLY a JSON array of objects — no prose, no markdown fences, and every
"tag" value must be a literal transcription of what you see in the image, not
a copy of the placeholder shape above:
[{{"tag": "<size>\\"-<service>-<spec>-<serial>", "location": "top-left", "confidence": "high"}}, ...]

Be exhaustive: a typical process P&ID contains 15–40 line tags. If you find fewer than 15, you have almost certainly missed some — re-scan every corner and rotated label before finalising.
"""


# ─── Public API ───────────────────────────────────────────────────────
def extract_line_tags_via_vision(
    pdf_bytes: bytes,
    provider: str,
    api_key: str,
    *,
    legend_prompt: str | None = None,
    model: str | None = None,
) -> dict:
    """Return dict with merged, deduped tags from multi-tile Vision passes.

    If ``legend_prompt`` is provided (from a user's active Legend Sheet) it
    replaces the built-in tag-format rules — everything else in the prompt
    (scan strategy, exclusions, JSON output format) stays the same.

    ``model`` optionally overrides VISION_MODELS[provider] for this call
    (Claude only — same "Claude Model" dropdown pattern as
    symbol_shape_extractor.py). Callers must validate it against
    ALLOWED_CLAUDE_VISION_MODELS before passing it through.
    """
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider '{provider}'. Choose one of {SUPPORTED_PROVIDERS}.")
    if not api_key or not api_key.strip():
        raise ValueError("api_key is required for Vision extraction")

    user_prompt = _compose_user_prompt(legend_prompt)
    resolved_model = model or VISION_MODELS[provider]

    from .token_accounting import UsageMeter
    meter = UsageMeter(feature='line_extraction')
    all_raw: list[str] = []
    all_tags: dict[str, dict] = {}
    call_count = 0

    for page_idx, page_image in enumerate(_render_pages(pdf_bytes)):
        # 1) Optional low-res overview pass — helps the model see the drawing layout.
        if VISION_INCLUDE_OVERVIEW:
            overview_b64 = _prepare_image_b64(page_image, VISION_OVERVIEW_MAX_DIMENSION_PX)
            raw, in_t, out_t = _call_vision(provider, api_key, overview_b64, user_prompt, model=model)
            meter.add(provider, resolved_model, in_t, out_t)
            call_count += 1
            all_raw.append(f'[page {page_idx} overview]\n{raw}')
            for tag in _parse_tag_list(raw):
                _merge_tag(all_tags, tag)

        # 2) High-detail overlapping tile passes.
        for tile_idx, tile in enumerate(_tile_image(page_image,
                                                   VISION_TILE_ROWS,
                                                   VISION_TILE_COLS,
                                                   VISION_TILE_OVERLAP_FRAC)):
            tile_b64 = _prepare_image_b64(tile, VISION_TILE_MAX_DIMENSION_PX)
            raw, in_t, out_t = _call_vision(provider, api_key, tile_b64, user_prompt, model=model)
            meter.add(provider, resolved_model, in_t, out_t)
            call_count += 1
            all_raw.append(f'[page {page_idx} tile {tile_idx}]\n{raw}')
            for tag in _parse_tag_list(raw):
                _merge_tag(all_tags, tag)

    tags_sorted = sorted(all_tags.values(),
                         key=lambda t: (t.get('service') or '', _serial_int(t.get('serial') or ''), t.get('size') or ''))
    return {
        'provider': provider,
        'model': resolved_model,
        'tags': tags_sorted,
        'raw': '\n\n---\n\n'.join(all_raw),
        'call_count': call_count,
        'token_usage': meter.summary(),
    }


# ─── Full-page text transcription (Tesseract replacement) ─────────────
# Used by apps.pid_verification / apps.pid_verification_v2's extraction.py
# as a drop-in replacement for OCR: rather than re-deriving every P&ID
# category (line tags, instrument tags, equipment tags, notes, HOLDs,
# line sizes...) as its own Vision call, this asks for one comprehensive
# text transcription of the page and hands it back as `raw_text` — the
# SAME regex-based category parsers those files already have
# (_extract_tags, _extract_instruments, _extract_valves, _extract_equipment,
# _extract_notes, _extract_holds, _extract_line_sizes) keep working
# completely unchanged, since they only ever operated on a raw_text string
# regardless of whether Tesseract or Vision produced it.
RAW_TEXT_SYSTEM_PROMPT = (
    "You are an expert process engineer transcribing all readable text from "
    "a P&ID engineering drawing image. This may be a scanned drawing — image "
    "quality, contrast, or resolution may be low, and text may be faint, "
    "skewed, rotated 90°/270° along vertical pipe runs, or partially cut "
    "off. Transcribe your best honest reading rather than skipping unclear text."
)

RAW_TEXT_USER_PROMPT = """Transcribe EVERY piece of text visible on this P&ID page image — read the
ENTIRE image methodically, top to bottom, left to right, including margins,
corners, rotated/vertical text along pipe runs, and small tags on
drain/vent/blowdown/sample/purge/utility lines. Include:
- Pipeline line tags (format SIZE"-SERVICE-SPEC-SERIAL, e.g. 6"-FL-AC6N-####)
- Instrument tags (e.g. PT-####, FT-####, PSV-####)
- Equipment tags (e.g. V-####, P-###A, E-###)
- NOTE / HOLD callouts, line-size labels, titles, revision blocks — literally
  any text on the page, even fragments
The formats above are illustrative shapes only, not real values — transcribe
the literal characters you see in THIS image. Two tags can look almost
identical and differ in only one digit; read each one's digits independently
rather than reusing a digit you already transcribed for a similar tag.

Also identify approximate locations for the most important TAG-like strings
(pipeline/instrument/equipment tags specifically — not notes or titles) so
they can be anchored on the drawing.

Return ONLY a single JSON object, no prose, no markdown fences:
{
  "raw_text": "one transcribed line/fragment per line, newline-separated",
  "located_tags": [
    {"text": "6\\"-FL-AC6N-8112", "location": "top-left"}
  ]
}
Valid "location" values: top-left, top, top-right, middle-left, center,
middle-right, bottom-left, bottom, bottom-right.
If you cannot make out ANY text at all, return {"raw_text": "", "located_tags": []} —
never invent text that isn't there."""

# Coarse 3x3-grid location -> approximate (x_pct, y_pct) center, used to
# turn Vision's qualitative location strings into the same {tag: {x_pct,
# y_pct}} shape the Tesseract-based _extract_tag_positions() used to
# produce from real pixel bounding boxes. This is a genuine accuracy
# regression (a zone center vs. an exact pixel) — documented, not hidden —
# but keeps the Drawing Layout overlay feature functional rather than empty.
_LOCATION_TO_PCT = {
    'top-left': (15, 15), 'top': (50, 15), 'top-right': (85, 15),
    'middle-left': (15, 50), 'center': (50, 50), 'middle-right': (85, 50),
    'bottom-left': (15, 85), 'bottom': (50, 85), 'bottom-right': (85, 85),
}


def extract_raw_text_via_vision(pdf_bytes: bytes, page_index: int, api_key: str,
                                 provider: str = 'claude', model: str | None = None) -> dict:
    """Replace Tesseract OCR with ONE Vision call for a single page: renders
    just that page (preprocessed — grayscale/denoise/contrast/sharpen, same
    as every other Vision call in this module), asks the model to
    transcribe all visible text, and returns it in the shape
    apps.pid_verification(_v2)'s extraction.py needs.

    Returns {'raw_text': str, 'tag_positions': {tag: {'x_pct', 'y_pct'}}, 'token_usage': dict}.

    Raises ValueError if api_key is missing — callers must surface this as
    a clear "AI Vision key required" error, not swallow it into an empty
    result (see extraction.py's VisionAPIKeyRequiredError).
    """
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider '{provider}'. Choose one of {SUPPORTED_PROVIDERS}.")
    if not api_key or not api_key.strip():
        raise ValueError("api_key is required for Vision-based text extraction")

    page_img = _render_single_page(pdf_bytes, page_index)
    image_b64 = _prepare_image_b64(page_img, VISION_OVERVIEW_MAX_DIMENSION_PX)

    from .token_accounting import UsageMeter
    meter = UsageMeter(feature='raw_text_extraction')
    resolved_model = model or VISION_MODELS[provider]

    raw, in_t, out_t = _call_raw_text_vision(provider, api_key, image_b64, model=model)
    meter.add(provider, resolved_model, in_t, out_t)

    parsed = _parse_raw_text_response(raw)

    tag_positions = {}
    for item in parsed.get('located_tags', []):
        text = str(item.get('text') or '').strip()
        loc = str(item.get('location') or '').strip().lower()
        if text and loc in _LOCATION_TO_PCT:
            x_pct, y_pct = _LOCATION_TO_PCT[loc]
            tag_positions[text] = {'x_pct': x_pct, 'y_pct': y_pct}

    return {
        'raw_text': parsed.get('raw_text', ''),
        'tag_positions': tag_positions,
        'provider': provider,
        'model': resolved_model,
        'token_usage': meter.summary(),
    }


def _call_raw_text_vision(provider: str, api_key: str, image_b64: str, model: str | None = None):
    """Same retry/fallback-model pattern as _call_vision(), but with its own
    system prompt (RAW_TEXT_SYSTEM_PROMPT) instead of the line-tag-specific
    VISION_SYSTEM_PROMPT — kept separate rather than parameterizing the
    shared _call_claude/_call_openai to avoid touching that well-exercised
    code path for an unrelated feature."""
    def _claude_call(k, b64, p, use_model=None):
        import anthropic
        from .token_accounting import read_claude_usage
        client = anthropic.Anthropic(api_key=k, timeout=VISION_REQUEST_TIMEOUT_S)
        resp = client.messages.create(
            model=use_model or model or VISION_MODELS['claude'],
            max_tokens=VISION_MAX_TOKENS,
            system=RAW_TEXT_SYSTEM_PROMPT,  # no `temperature` — see _call_claude()'s comment
            messages=[{'role': 'user', 'content': [
                {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': b64}},
                {'type': 'text', 'text': p},
            ]}],
        )
        parts = [b.text for b in resp.content if getattr(b, 'type', None) == 'text']
        inp, out = read_claude_usage(resp)
        return ''.join(parts), inp, out

    def _openai_call(k, b64, p):
        import openai
        from .token_accounting import read_openai_usage
        client = openai.OpenAI(api_key=k, timeout=VISION_REQUEST_TIMEOUT_S)
        resp = client.chat.completions.create(
            model=VISION_MODELS['openai'],
            max_tokens=VISION_MAX_TOKENS,
            temperature=VISION_TEMPERATURE,
            messages=[
                {'role': 'system', 'content': RAW_TEXT_SYSTEM_PROMPT},
                {'role': 'user', 'content': [
                    {'type': 'text', 'text': p},
                    {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{b64}', 'detail': 'high'}},
                ]},
            ],
        )
        text = resp.choices[0].message.content or ''
        inp, out = read_openai_usage(resp)
        return text, inp, out

    fn = _openai_call if provider == 'openai' else _claude_call
    effective_model = model or VISION_MODELS['claude']
    try:
        return _with_retries(provider, fn, api_key, image_b64, RAW_TEXT_USER_PROMPT)
    except Exception as exc:  # noqa: BLE001
        if provider == 'claude' and effective_model != VISION_MODEL_CLAUDE_FALLBACK \
                and _is_model_not_found_error(exc):
            logger.warning(
                "[raw_text_vision] model '%s' unavailable (%s) — retrying once with fallback '%s'",
                effective_model, exc, VISION_MODEL_CLAUDE_FALLBACK,
            )
            fallback_fn = lambda k, b64, p: _claude_call(k, b64, p, use_model=VISION_MODEL_CLAUDE_FALLBACK)  # noqa: E731
            return _with_retries(provider, fallback_fn, api_key, image_b64, RAW_TEXT_USER_PROMPT)
        raise


def _parse_raw_text_response(raw: str) -> dict:
    if not raw:
        return {'raw_text': '', 'located_tags': []}
    text = raw.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r'\{.*\}', text, flags=re.DOTALL)
        if not m:
            # Model ignored the JSON instruction — treat the whole reply as
            # the transcription rather than losing it entirely.
            return {'raw_text': text, 'located_tags': []}
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError:
            return {'raw_text': text, 'located_tags': []}
    if not isinstance(parsed, dict):
        return {'raw_text': text, 'located_tags': []}
    return {
        'raw_text': parsed.get('raw_text') or '',
        'located_tags': parsed.get('located_tags') or [],
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
_CONFIDENCE_RANK = {'low': 0, 'medium': 1, 'high': 2}


def _merge_tag(all_tags: dict[str, dict], tag: dict) -> None:
    """Insert a newly-found tag, or — if the same tag was already found by
    an earlier pass — keep whichever occurrence has the higher confidence.
    A tag read faintly on the low-res overview but clearly on a zoomed
    tile should end up reported at the clearer reading's confidence."""
    existing = all_tags.get(tag['tag'])
    if existing is None or _CONFIDENCE_RANK.get(tag['confidence'], 0) > _CONFIDENCE_RANK.get(existing['confidence'], 0):
        all_tags[tag['tag']] = tag


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


def _render_single_page(pdf_bytes: bytes, page_index: int) -> Image.Image:
    """Render exactly ONE page to a high-res PIL image at VISION_RENDER_DPI.

    Unlike _render_pages(), this does NOT rasterize every other page —
    fitz.open() only parses the document's xref table; get_pixmap() is what
    actually does the (expensive) rendering work, and it's called here for
    a single page only. Used by callers that process pages independently
    (e.g. one Celery task per page for a 35-50 page document) so N page-
    tasks cost O(N) total renders instead of O(N^2) (each task re-rendering
    every page just to discard all but one).
    """
    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    mat = fitz.Matrix(VISION_RENDER_DPI / 72, VISION_RENDER_DPI / 72)
    pix = doc[page_index].get_pixmap(matrix=mat, alpha=False)
    return Image.open(io.BytesIO(pix.tobytes('png')))


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


# ─── Preprocessing for scanned / low-quality PDFs ──────────────────────
# Applied to every P&ID page/tile right before it reaches Vision — NOT to
# uploaded reference symbol pictures (those are already clean, manually
# curated crops, not scans, and are read straight from storage rather than
# through this render pipeline). VISION_RENDER_DPI (300, above) already
# covers the "render at 300 DPI minimum" requirement.
VISION_PREPROCESS_ENABLED = True
VISION_PREPROCESS_CONTRAST_FACTOR = 2.0


def _preprocess_for_vision(img: Image.Image) -> Image.Image:
    """Grayscale + denoise + contrast boost + sharpen — improves legibility
    of scanned/low-quality P&ID pages before they reach Vision. A no-op
    when VISION_PREPROCESS_ENABLED is False, so it can be turned off
    without a code change if it ever hurts an already-clean digital PDF
    more than it helps a scanned one.
    """
    if not VISION_PREPROCESS_ENABLED:
        return img
    from PIL import ImageEnhance, ImageFilter

    out = img.convert('L')                                  # grayscale first
    out = out.filter(ImageFilter.MedianFilter(size=3))       # remove scan noise/speckle
    out = ImageEnhance.Contrast(out).enhance(VISION_PREPROCESS_CONTRAST_FACTOR)
    out = out.filter(ImageFilter.SHARPEN)                    # crisp up faint/blurry text
    return out


def _prepare_image_b64(img: Image.Image, max_dim: int) -> str:
    """Downscale → preprocess → base64-PNG-encode: the standard prep every
    P&ID page/tile goes through before a Vision call."""
    return _image_to_b64_png(_preprocess_for_vision(_downscale(img, max_dim)))


def _extract_tag_fields(item) -> tuple[str, str | None, str | None]:
    """Accept either the current {"tag", "location", "confidence"} object
    format or a plain string (older model output / regex fallback), and
    return (tag_string, location_or_None, confidence_or_None)."""
    if isinstance(item, dict):
        return str(item.get('tag') or '').strip(), item.get('location'), item.get('confidence')
    return str(item).strip(), None, None


def _parse_tag_list(raw: str) -> list[dict]:
    """Parse model output into structured tag dicts."""
    if not raw:
        return []
    # Strip common markdown fences the model may add despite instructions.
    text = raw.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)

    # Try direct JSON parse; fall back to extracting the first [...] block.
    # Each candidate is a (tag_string, location_or_None, confidence_or_None)
    # triple — location/confidence are only known when the model returned
    # proper {"tag", "location", "confidence"} objects; plain strings and
    # regex-scraped fallbacks carry None for both, which the loop below
    # defaults to 'unspecified'/'low' (we couldn't get a clean structured
    # answer, so treat it as the least certain case).
    candidates: list[tuple[str, str | None, str | None]] = []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            candidates = [_extract_tag_fields(x) for x in parsed]
    except json.JSONDecodeError:
        m = re.search(r'\[.*\]', text, flags=re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group(0))
                if isinstance(parsed, list):
                    candidates = [_extract_tag_fields(x) for x in parsed]
            except json.JSONDecodeError:
                pass

    # Fallback: scrape line-shaped tokens straight from the text.
    if not candidates:
        candidates = [(m, None, None) for m in
                      re.findall(r'\d{1,2}(?:[-/]\d{1,2})?"?-[A-Z]{2,4}-[A-Z0-9]{3,6}-\d{3,5}', text)]

    tags: list[dict] = []
    for cand, location, confidence in candidates:
        norm = _normalise_candidate(cand)
        if not norm:
            continue
        m = LINE_TAG_PATTERN.match(norm)
        if not m:
            continue
        size, service, spec, serial = m.groups()
        location = str(location or '').strip().lower() or 'unspecified'
        confidence = str(confidence or '').strip().lower()
        if confidence not in ('high', 'medium', 'low'):
            confidence = 'low'  # no confidence returned (old-format/regex-scraped) → least certain
        tags.append({
            'tag': norm,
            'size': size,
            'service': service,
            'spec': spec,
            'serial': serial,
            'service_group': SERVICE_GROUPS.get(service, service),
            'location': location,
            'confidence': confidence,
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


def _call_vision(provider: str, api_key: str, image_b64: str, user_prompt: str = VISION_USER_PROMPT,
                  model: str | None = None) -> str:
    if provider == 'openai':
        fn = _call_openai
    elif provider == 'claude':
        fn = (lambda k, b64, p: _call_claude(k, b64, p, model=model)) if model else _call_claude
    else:
        raise ValueError(f"unknown provider {provider}")
    effective_model = model or VISION_MODELS['claude']
    try:
        return _with_retries(provider, fn, api_key, image_b64, user_prompt)
    except Exception as exc:  # noqa: BLE001
        if provider == 'claude' and effective_model != VISION_MODEL_CLAUDE_FALLBACK \
                and _is_model_not_found_error(exc):
            logger.warning(
                "[vision] model '%s' unavailable (%s) — retrying once with fallback model '%s'",
                effective_model, exc, VISION_MODEL_CLAUDE_FALLBACK,
            )
            fallback_fn = lambda k, b64, p: _call_claude(k, b64, p, model=VISION_MODEL_CLAUDE_FALLBACK)  # noqa: E731
            return _with_retries(provider, fallback_fn, api_key, image_b64, user_prompt)
        raise


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


def _is_model_not_found_error(exc) -> bool:
    """True if the failure is specifically 'this model isn't available'
    (retired, typo'd, not enabled on this account) rather than a generic
    transient error — the distinction that triggers the fallback-model
    retry in _call_vision() / symbol_shape_extractor._call_vision_labeled().
    """
    status = _extract_status_code(exc)
    msg = str(exc).lower()
    return status == 404 or (
        'model' in msg and ('not_found' in msg or 'not found' in msg or 'does not exist' in msg)
    )


# ─── BYOK connectivity test ─────────────────────────────────────────────
TEST_CONNECTION_MAX_TOKENS = 5  # tiny text-only ping — no image, no retries


def test_api_key(provider: str, api_key: str) -> tuple[bool, str]:
    """One minimal text-only call to confirm a BYOK key actually works.
    Not tied to any extraction feature — just a connectivity/auth check
    against the same provider/model config the rest of BYOK Vision uses.
    """
    if provider not in SUPPORTED_PROVIDERS:
        return False, f"Unsupported provider '{provider}'."
    if not api_key or not api_key.strip():
        return False, 'API key is required.'

    try:
        if provider == 'claude':
            import anthropic
            client = anthropic.Anthropic(api_key=api_key, timeout=VISION_REQUEST_TIMEOUT_S)
            client.messages.create(
                model=VISION_MODELS['claude'],
                max_tokens=TEST_CONNECTION_MAX_TOKENS,
                messages=[{'role': 'user', 'content': 'Hi'}],
            )
        else:
            import openai
            client = openai.OpenAI(api_key=api_key, timeout=VISION_REQUEST_TIMEOUT_S)
            client.chat.completions.create(
                model=VISION_MODELS['openai'],
                max_tokens=TEST_CONNECTION_MAX_TOKENS,
                messages=[{'role': 'user', 'content': 'Hi'}],
            )
        return True, 'API key is valid and working!'
    except Exception as exc:  # noqa: BLE001
        status_code = _extract_status_code(exc)
        if status_code in (401, 403):
            return False, 'Invalid API key. Please check and try again.'
        return False, f'Connection test failed: {exc}'


def _call_openai(api_key: str, image_b64: str, user_prompt: str = VISION_USER_PROMPT):
    import openai
    from .token_accounting import read_openai_usage
    client = openai.OpenAI(api_key=api_key, timeout=VISION_REQUEST_TIMEOUT_S)
    resp = client.chat.completions.create(
        model=VISION_MODELS['openai'],
        max_tokens=VISION_MAX_TOKENS,
        temperature=VISION_TEMPERATURE,
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


def _call_claude(api_key: str, image_b64: str, user_prompt: str = VISION_USER_PROMPT, model: str | None = None):
    import anthropic
    from .token_accounting import read_claude_usage
    client = anthropic.Anthropic(api_key=api_key, timeout=VISION_REQUEST_TIMEOUT_S)
    resp = client.messages.create(
        model=model or VISION_MODELS['claude'],
        max_tokens=VISION_MAX_TOKENS,
        # No `temperature` here — Claude Sonnet 5 / Opus 5 (and the 4.7+
        # generation generally) reject ANY non-default temperature/top_p/
        # top_k with a 400 error on every request, confirmed directly in
        # Anthropic's docs (platform.claude.com/docs/en/build-with-claude/
        # thinking, "Sampling parameters"). VISION_TEMPERATURE=0.0 was fine
        # for the older fallback model but is rejected outright by the
        # current default model, so it's omitted here (OpenAI is unaffected
        # — see _call_openai above, which still sets it).
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
