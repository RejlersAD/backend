"""
Piping Valve MTO — PDF Extractor
=================================

Vision-assisted extractor that reads a P&ID / valve-data PDF and returns the
canonical Valve MTO row schema consumed by the frontend
(`frontend/src/pages/Engineering/Piping/ValveMTO.jsx`).

Design notes
------------
* Soft-coded: every threshold, regex, prompt template and model name lives
  at module level so they can be tuned without code changes.
* Text/OCR supplies context only. Physical valves must be identified by
  Vision; provider failures never become synthetic MTO rows.
* Survey scope is all visible valves, including existing items. Quantities
  require engineering review before they can be used for procurement.
* Returns the frontend's row keys directly — no mapping layer needed.

Public entry point
------------------
    extract_valve_mto(pdf_path: str) -> dict

Returned shape::

    {
      "status": "ok" | "error",
      "engine": "text" | "vision" | "text+vision",
      "page_count": int,
      "rows":         [ { "sl_no": 1, "area": "...", ... }, ... ],
      "project_meta": { "doc_no": "...", "doc_title": "...", ... },
      "warnings":     [ "..." ]
    }
"""
from __future__ import annotations
from apps.core.ai_consumer_clients import lazy_provider_client, personal_provider_client, provider_api_key
from apps.core.ai_credentials import AICredentialUnavailable

import base64
import io
import math
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─── Soft-coded constants (env-overridable) ─────────────────────────────────────
TEXT_SUFFICIENT_CHARS  = int(os.getenv('VALVE_MTO_TEXT_THRESHOLD', '1500'))

# ─── Tesseract OCR fallback (soft-coded, env-overridable) ──────────────────────
# Modeled on the Line List engine (apps/designiq/pid_ocr_extractor_v2.py):
# conditional pytesseract import with graceful fallback, soft-coded PSM set,
# PyMuPDF rasterisation. Deliberately simpler than the Line List engine —
# rotation 0 only (no multi-angle passes), no tiling — to keep the fallback
# fast; it exists to recover tag/line text when the PDF text layer is thin
# (< TEXT_SUFFICIENT_CHARS) or Vision is down (e.g. OpenAI 429), not to
# replace Vision's structured extraction.
VALVE_MTO_OCR_ENABLED = os.getenv('VALVE_MTO_OCR_ENABLED', '1').strip().lower() not in ('0', 'false', 'no')
VALVE_MTO_OCR_DPI     = int(os.getenv('VALVE_MTO_OCR_DPI', '300'))
VALVE_MTO_OCR_PSMS    = (6, 11)   # PSM 6 = uniform block, PSM 11 = sparse text
_psms_env = os.getenv('VALVE_MTO_OCR_PSMS', '').strip()
if _psms_env:
    try:
        VALVE_MTO_OCR_PSMS = tuple(int(p.strip()) for p in _psms_env.split(',') if p.strip()) or (6, 11)
    except ValueError:
        pass

# Conditional import for pytesseract (graceful fallback if not installed —
# the extractor behaves exactly as before in that case).
try:
    import pytesseract
    PYTESSERACT_AVAILABLE = True
except ImportError:
    pytesseract = None
    PYTESSERACT_AVAILABLE = False

# Hard cap on pages we'll process — protects against runaway docs.
VISION_MAX_PAGES       = int(os.getenv('VALVE_MTO_VISION_MAX_PAGES', '50'))
# How many pages to bundle into a single OpenAI call (smaller = more accurate, larger = cheaper).
# Default lowered to 1 so each Vision call focuses on a single page — dramatically
# improves line-number recall on dense valve MTO drawings. Override via env if
# token-cost is more important than completeness.
VISION_BATCH_SIZE      = int(os.getenv('VALVE_MTO_VISION_BATCH_SIZE', '1'))
# How many batches to run in parallel. Raised 4 -> 6: Thorough Scan (2x2
# tiles x 2 passes = 8 Vision calls/page, see _scan_mode_params) made
# per-page call volume 8x what it was when 4 was chosen — more
# concurrency keeps real-world wall-clock time down for the now much
# larger batch counts a dense multi-page thorough-scan extraction
# produces (can legitimately run 25+ minutes end-to-end).
VISION_PARALLEL_BATCHES = int(os.getenv('VALVE_MTO_VISION_PARALLEL', '6'))
VISION_IMAGE_DPI       = int(os.getenv('VALVE_MTO_VISION_DPI', '120'))
VISION_MAX_EDGE_PX     = int(os.getenv('VALVE_MTO_VISION_MAX_EDGE', '1600'))
VISION_MODEL           = os.getenv('VALVE_MTO_VISION_MODEL', 'gpt-4o-mini')
VISION_TEMPERATURE     = 0.0
# BUG FIX (real, confirmed): was 90s — too tight for Thorough Scan's
# per-tile calls under real-world provider load/queueing, especially
# with VISION_PARALLEL_BATCHES raised to 6 (more concurrent requests can
# mean more queueing latency per individual call, not just more total
# throughput). 300s (5 min) is a PER-CALL cap, not the extraction's own
# overall runtime limit — the background job thread that makes these
# calls has no timeout of its own (see piping_valve_mto_view.py's own
# docstring) and simply runs for as long as its batches take, so a
# 25+ minute extraction was never actually blocked by this constant
# directly; raising it just stops an individual slow call from failing
# prematurely and forcing a (usually successful, just slower) retry-via-
# batch-failure instead.
VISION_TIMEOUT_SECS    = float(os.getenv('VALVE_MTO_VISION_TIMEOUT', '300'))
JPEG_QUALITY           = 80
# BUG FIX (reverted by explicit request): was raised 240 -> 300 for
# better small-text legibility, but confirmed to make real extraction
# RESULTS worse, not better — piping_class recall dropped 42 -> 23 and
# line-number recall dropped significantly too. Root cause not fully
# diagnosed, but likely connected to the region-count overshoot at 300
# DPI confirmed in the same turn (the proportional cap's rounding
# doesn't hold a strict ceiling, so 300 DPI produced MORE regions per
# page than intended, each covering a smaller, more zoomed-in slice —
# plausibly hurting Vision's ability to read a line number that spans
# across a region boundary more often than at the lower DPI). Reverted
# to 240 — the value this was actually validated against.
VISION_DETAIL_DPI      = int(os.getenv('VALVE_MTO_DETAIL_DPI', '240'))
VISION_REGION_EDGE_PX  = int(os.getenv('VALVE_MTO_REGION_EDGE', '1568'))
VISION_REGION_OVERLAP  = 0.08
# BUG FIX: the adaptive grid in _render_native_regions had no upper
# bound — a large-format sheet (A0/A1) at VISION_DETAIL_DPI could
# produce 40+ regions for ONE page, and each region is one Vision API
# call. Caps the grid to at most this many regions per page, scaling
# columns/rows down PROPORTIONALLY (never just truncating) so a huge
# sheet still gets full coverage at a coarser grid instead of silently
# losing whatever regions got cut off. Same technique/value as
# apps.electrical_comparison.services.electrical_vision's own
# ELECTRICAL_VISION_MAX_REGIONS_PER_PAGE, which this was modeled on.
VISION_MAX_REGIONS_PER_PAGE = int(os.getenv('VALVE_MTO_MAX_REGIONS_PER_PAGE', '16'))
MAX_ROWS               = int(os.getenv('VALVE_MTO_MAX_ROWS', '2000'))

# Provider models remain module-specific; credentials follow central policy.
SUPPORTED_PROVIDERS = ('openai', 'claude')
VISION_MODELS = {
    'openai': VISION_MODEL,
    'claude': os.getenv('VALVE_MTO_CLAUDE_MODEL', 'claude-opus-5-5'),
}
# BUG FIX: thinking={'type': 'disabled'} is rejected by non-Opus Claude
# 5.x models (confirmed with Sonnet 5.5), and Opus 5.5 has always-on
# adaptive thinking that doesn't need (or accept) an explicit disable
# either — so no Claude model call in this module sends a `thinking`
# kwarg at all any more (see _call_vision_batch_json / test_api_key).
TEST_CONNECTION_MAX_TOKENS = 16

# Approximate list-price $/1K tokens per model (as of this feature's
# authoring) — used only for an estimated cost figure surfaced in the
# activity feed, never for billing. Unlisted/unknown models cost $0
# (COST_PER_1K_TOKENS.get(model, {}) falls back to an empty dict).
COST_PER_1K_TOKENS = {
    'claude-opus-5-5': {'input': 0.015, 'output': 0.075},
    'claude-sonnet-5-5': {'input': 0.003, 'output': 0.015},
    'claude-haiku-5-5': {'input': 0.00025, 'output': 0.00125},
    'gpt-4o': {'input': 0.005, 'output': 0.015},
    'gpt-4o-mini': {'input': 0.00015, 'output': 0.0006},
}
# Fixed conversion rate (AED is pegged to USD) — not a live FX rate.
AED_PER_USD = 3.67

# Canonical row schema — must match frontend `valveMTO.config.js` VALVE_COLUMNS.
# NOTE: order does not affect extraction; new keys are additive. Frontend
# default-row init iterates this list and treats unknown keys as text.
ROW_KEYS = [
    'sl_no', 'area', 'type', 'pms_class', 'piping_class', 'rating', 'size_1', 'size_2',
    'bore', 'line_number', 'line_list', 'valve_tag', 'description', 'qty_island', 'qty_field', 'unit',
    'remarks', 'pid_number', 'facing', 'confidence', 'location_hint', 'qty_observed', 'tab',
]

NUMERIC_KEYS      = {'sl_no', 'qty_island', 'qty_field'}
SURVEY_SCOPE_WARNING = (
    'This register counts all visible valves, including existing and out-of-scope items. '
    'Review scope, classification, and quantities before using it for procurement.'
)

# ─── New prompt schema mapping (UPPER_SNAKE → canonical lowercase) ──────
# The Borouge Unit 47 PE4&PE5 prompt (VISION_PROMPT_TEMPLATE below) returns
# rows under sheet-level "valves" with UPPER_SNAKE keys; older cached
# responses / fallback paths may still use the legacy lowercase "rows"
# schema. Both are accepted; this map is the single translation point.
UPPER_SNAKE_ROW_MAP = {
    'SL_NO': 'sl_no', 'AREA': 'area', 'TYPE': 'type',
    'PIPING_MATERIAL_CLASS': 'pms_class', 'PIPING_CLASS': 'piping_class',
    'RATING_FACING': 'rating', 'FACING': 'facing',
    'SIZE_1_NB': 'size_1', 'SIZE_2_NB': 'size_2', 'PID_NUMBER': 'pid_number',
    'LINE_NUMBER': 'line_number', 'VALVE_TAG': 'valve_tag',
    'DESCRIPTION': 'description', 'QTY_ISLAND': 'qty_island',
    'QTY_FIELD': 'qty_field', 'UNIT': 'unit', 'REMARKS': 'remarks',
    'CONFIDENCE': 'confidence', 'LOCATION_HINT': 'location_hint',
    'BORE': 'bore', 'LINE_LIST': 'line_list',
}

# ─── Section-4 text-cleaning validators (soft-coded regexes) ────────────
# Implemented in CODE (not only in the prompt) so a corrupted/merged value
# can never pass through even when the model disobeys.
DRAWING_NO_PATTERN = r'^[A-Z0-9]+(?:-[A-Z0-9]+)+-\d+[A-Z]?$'
LINE_NO_PATTERN = r'^[\d/-]+"?(?:x[\d/-]+")?-(?=[A-Z0-9-]*[A-Z])(?:[A-Z0-9]+-){2,}[A-Z0-9]+$'

# ─── CLASS → RATING lookup (soft-coded) ─────────────────────────────────
# Intentionally blank values — the user fills ratings from the PMS/legend
# later. Rule (see prompt §3 RATING_FACING): if rating missing and the
# class has a table value → fill it; otherwise leave empty and append
# 'RATING FROM PMS' to remarks. Never guess.
CLASS_RATING_TABLE = {
    'A0KU16': '', 'A1AU01': '', 'A2AU05': '', 'A2AU16': '', 'A2AP01': '',
    'A2GP01': '', 'A0KP01': '', 'A0KU01': '', 'B2AP01': '', 'B2GP01': '',
    'B2GP98': '', 'D0KP01': '', 'D2AP01': '', 'D2GP01': '', 'E0KP01': '',
}

# ─── Soft-coded line-number harvester ───────────────────────────────────
# Multi-format regex patterns mirrored from the Line List engine
# (apps/designiq/pid_ocr_extractor_v2.py → COVERAGE_AUDIT_CONFIG). Scans the
# PyMuPDF-extracted text and surfaces EVERY plausible line designation. The
# harvested list is then injected into the Vision prompt as a hint so the
# model does not skip line numbers that are clearly present in the embedded
# PDF text layer. Pure additive helper — never overrides Vision output.
#
# Supported shapes (any of these match):
#   • Onshore         2"-D-6152-033842-X-N
#   • Industrial      2"-2600-FL-352-32070R-E
#   • Offshore        604-LFG-3-AC2GA0-2012
#   • ADNOC           6"-CD-AC3N-8256
#   • General         4"-41-SWR-64313-A2AU16-V
#   • Borouge/Linde   1"-63-UA-149472-A1AU01-V
LINE_NUMBER_HARVEST_PATTERNS: List[str] = [
    # SIZE"-FLUID-SEQ-PIPESPEC-DEPT-INSUL (Onshore)
    r'\b\d{1,2}(?:/\d{1,2})?["]?[-\s]+[A-Z]{1,4}[-\s]+\d{3,6}[-\s]+\d{4,8}(?:[-\s]+[A-Z0-9]{1,4}){0,3}\b',
    # SIZE"-UNIT-SERVICE-SEQ-PIPECLASS(-END) (Industrial / Samsung / Foster Wheeler)
    r'\b\d{1,2}(?:/\d{1,2})?["]?[-\s]+\d{3,4}[-\s]+[A-Z]{1,6}[-\s]+\d{3,5}[-\s]+\d{5}[A-Z]{1,2}(?:[-\s]+[A-Z])?\b',
    # AREA-FLUID-SIZE-PIPECLASS-SEQ (Offshore)
    r'\b\d{2,4}[-\s]+[A-Z]{1,4}[-\s]+\d{1,2}["]?[-\s]+[A-Z0-9]{4,10}[-\s]+\d{3,6}\b',
    # SIZE"-FLUID-CLASS-SEQ (ADNOC compact)
    r'\b\d{1,2}(?:/\d{1,2})?["]?[-\s]+[A-Z]{1,4}[-\s]+[A-Z0-9]{3,6}[-\s]+\d{3,6}\b',
    # SIZE"-AREA-SERVICE-SEQ-PIPECLASS-END (Borouge / Linde area-first)
    r'\b\d{1,2}(?:/\d{1,2})?["]?[-\s]+\d{2,3}[-\s]+[A-Z]{1,3}[-\s]+\d{4,6}[-\s]+[A-Z0-9]{5,7}[-\s]+[A-Z]{1,2}\b',
]
_LINE_NUMBER_HARVEST_RE = [re.compile(p, re.IGNORECASE) for p in LINE_NUMBER_HARVEST_PATTERNS]

# Cap how many candidates we forward to the Vision prompt (avoid token blow-up).
LINE_NUMBER_HARVEST_MAX_CANDIDATES = int(os.getenv('VALVE_MTO_LINE_HARVEST_MAX', '200'))


def _harvest_line_numbers(text: str) -> List[str]:
    """Return a deduplicated, normalised list of plausible line numbers.

    Pure additive helper used to inject high-quality candidates into the
    Vision prompt. Never raises — returns ``[]`` on any failure. Order is
    preserved (first occurrence wins) so the prompt list reads top-to-bottom
    of the source document.
    """
    if not text:
        return []
    from .local_valve_mto import LINE_CHARACTERS
    text = text.translate(LINE_CHARACTERS)
    seen: set = set()
    out: List[str] = []
    try:
        for regex in _LINE_NUMBER_HARVEST_RE:
            for match in regex.findall(text):
                # ``findall`` returns either str or tuple depending on groups.
                tag = match if isinstance(match, str) else (match[0] if match else '')
                tag = re.sub(r'\s+', '', tag).strip().upper()
                # Drop obvious noise — must contain at least one digit AND one letter.
                if not tag or not re.search(r'\d', tag) or not re.search(r'[A-Z]', tag):
                    continue
                if tag in seen:
                    continue
                seen.add(tag)
                out.append(tag)
                if len(out) >= LINE_NUMBER_HARVEST_MAX_CANDIDATES:
                    return out
    except Exception as exc:                                            # pragma: no cover
        logger.warning('[ValveMTO] line-number harvest failed: %s', exc)
    return out

# ─── Soft-coded valve-suffix → Remark dictionary ─────────────────────────
# Standard P&ID condition / operator codes that are usually appended to a
# valve tag (e.g. ``BV-1234-LO``, ``GV-08-FBLC``). When a valve_tag contains
# any of these tokens we surface the matching code in the Remarks column so
# the engineer sees "LO, LC, TSO, …" without having to decode the tag.
#
# Order matters — longer codes are checked first so ``FBLC`` is not chopped
# down to ``LC`` mid-match. Edit this list to add new project conventions
# (e.g. car-sealed, fail-safe, normally-closed) without touching the core
# extraction logic.
VALVE_TAG_REMARK_CODES: List[Tuple[str, str]] = [
    ('FBLO',  'FBLO'),  # Full-Bore Locked Open
    ('FBLC',  'FBLC'),  # Full-Bore Locked Closed
    ('CSO',   'CSO'),   # Car-Sealed Open
    ('CSC',   'CSC'),   # Car-Sealed Closed
    ('TSO',   'TSO'),   # Tight Shut-Off
    ('NRV',   'NRV'),   # Non-Return Valve
    ('LO',    'LO'),    # Locked Open
    ('LC',    'LC'),    # Locked Closed
    ('NO',    'NO'),    # Normally Open
    ('NC',    'NC'),    # Normally Closed
    ('FO',    'FO'),    # Fail Open
    ('FC',    'FC'),    # Fail Closed
    ('FL',    'FL'),    # Fail Last
    ('FI',    'FI'),    # Fail Indeterminate
]
# Pre-compile a single regex with ordered alternation; word-boundary on both
# sides of the token avoids matching letters embedded inside a longer word.
# Boundary excludes letters on both sides (so FLOW does not match FL, FCV does
# not match FC) but ALLOWS digits/hyphens/spaces — that way patterns like
# ``BV-LO-1234``, ``V101LO``, ``LO/LC`` or ``LO 6"`` all match.
_VALVE_TAG_REMARK_RE = re.compile(
    r'(?<![A-Z])(' + '|'.join(re.escape(c) for c, _ in VALVE_TAG_REMARK_CODES) + r')(?![A-Z])',
    re.IGNORECASE,
)

# ─── Soft-coded spelled-out phrase → code dictionary ────────────────────
# Vision often emits the English phrase in `description`/`remarks` instead of
# the short token (e.g. ``Locked Open`` rather than ``LO``). Map these to
# the canonical code so the Remarks column stays consistent. Order matters:
# longer/more-specific phrases first so ``FAIL OPEN`` is not stolen by ``OPEN``.
VALVE_PHRASE_REMARK_CODES: List[Tuple[str, str]] = [
    ('FULL-BORE LOCKED OPEN',   'FBLO'),
    ('FULL BORE LOCKED OPEN',   'FBLO'),
    ('FULL-BORE LOCKED CLOSED', 'FBLC'),
    ('FULL BORE LOCKED CLOSED', 'FBLC'),
    ('CAR-SEALED OPEN',         'CSO'),
    ('CAR SEALED OPEN',         'CSO'),
    ('CAR-SEALED CLOSED',       'CSC'),
    ('CAR SEALED CLOSED',       'CSC'),
    ('TIGHT SHUT-OFF',          'TSO'),
    ('TIGHT SHUT OFF',          'TSO'),
    ('TIGHT SHUTOFF',           'TSO'),
    ('NON-RETURN VALVE',        'NRV'),
    ('NON RETURN VALVE',        'NRV'),
    ('LOCKED OPEN',             'LO'),
    ('LOCKED CLOSED',           'LC'),
    ('NORMALLY OPEN',           'NO'),
    ('NORMALLY CLOSED',         'NC'),
    ('FAIL OPEN',               'FO'),
    ('FAIL CLOSED',             'FC'),
    ('FAIL CLOSE',              'FC'),
    ('FAIL LAST',               'FL'),
    ('FAIL IN PLACE',           'FL'),
    ('FAIL INDETERMINATE',      'FI'),
]
_VALVE_PHRASE_REMARK_RE = re.compile(
    r'(?<![A-Z])(' + '|'.join(re.escape(p) for p, _ in VALVE_PHRASE_REMARK_CODES) + r')(?![A-Z])',
    re.IGNORECASE,
)

# Fields scanned when deriving Remarks from a row. Vision can misplace the
# operational status code into ANY string column (tag, description, type,
# line_number, even pms_class), so scan every text field. Order controls
# which field's match wins when duplicates appear (purely cosmetic since
# duplicates are deduped against canonical codes).
REMARK_SOURCE_FIELDS: Tuple[str, ...] = (
    'valve_tag', 'remarks', 'description',
    'type', 'line_number', 'pms_class', 'rating', 'bore',
)

# Substrings of the original `remarks` text that should be DROPPED when
# merging derived codes back — these are placeholder/empty-ish values the
# Vision model sometimes emits. Anything else free-text is preserved.
_REMARK_DROP_PATTERNS: Tuple[str, ...] = (
    'n/a', 'na', 'none', 'null', '-', '—', '–', '.',
)


def _derive_remarks_from_row(row: Dict[str, Any]) -> str:
    """Return a comma-separated list of suffix codes found anywhere in the row.

    Scans REMARK_SOURCE_FIELDS for BOTH short-token codes (LO/LC/…) and
    spelled-out phrase variants ("Locked Open" → LO). Empty string when no
    codes match. Order of returned codes follows VALVE_TAG_REMARK_CODES so
    longer/more-specific codes win and duplicates are dropped.
    """
    found: List[str] = []
    label_by_code = {c.upper(): label for c, label in VALVE_TAG_REMARK_CODES}

    def _add(label: str) -> None:
        if label and label not in found:
            found.append(label)

    for field in REMARK_SOURCE_FIELDS:
        text = row.get(field, '') or ''
        if not text:
            continue
        s = str(text)
        # 1) Spelled-out phrases first (longer matches win).
        for raw in _VALVE_PHRASE_REMARK_RE.findall(s):
            phrase = raw.upper().replace('-', ' ')
            for ph, code in VALVE_PHRASE_REMARK_CODES:
                if ph.upper().replace('-', ' ') == phrase:
                    _add(label_by_code.get(code.upper(), code))
                    break
        # 2) Short-token codes (LO, LC, TSO, FBLC, FBLO, …).
        for raw in _VALVE_TAG_REMARK_RE.findall(s):
            _add(label_by_code.get(raw.upper(), ''))

    # Preserve the canonical code-list order from VALVE_TAG_REMARK_CODES so
    # output is deterministic regardless of which field surfaced the code.
    order = {label: i for i, (_, label) in enumerate(VALVE_TAG_REMARK_CODES)}
    found.sort(key=lambda l: order.get(l, 999))
    return ', '.join(found)


def _merge_remarks(original: str, derived: str) -> str:
    """Combine derived codes with any pre-existing free-text remarks.

    Keeps the original free-text (if meaningful) and APPENDS any derived
    codes that aren't already present — so legitimate engineering notes are
    never destroyed by the code-derivation pass.
    """
    orig = (original or '').strip()
    der  = (derived or '').strip()
    if not der:
        return orig
    if not orig or orig.lower() in _REMARK_DROP_PATTERNS:
        return der
    # Skip merge when the original is just the same code list (case-insensitive,
    # whitespace-insensitive comparison).
    norm = lambda s: re.sub(r'\s+', '', s).upper()
    if norm(orig) == norm(der):
        return der
    # If every derived code already appears as a sub-string of the original,
    # leave the original untouched.
    orig_up = orig.upper()
    extra = [c.strip() for c in der.split(',') if c.strip() and c.strip().upper() not in orig_up]
    if not extra:
        return orig
    return f"{orig} ({', '.join(extra)})"


# Backwards-compatible thin wrapper (kept in case other modules import it).
def _derive_remarks_from_tag(valve_tag: str) -> str:
    return _derive_remarks_from_row({'valve_tag': valve_tag})

# Soft-coded fatal-error classifier. If a batch exception's stringified form
# contains any of these substrings (case-insensitive), the whole job is
# considered unrecoverable and the snapshot status is flipped to 'error' so
# the frontend can show a meaningful message instead of a silent zero-row
# result. Map: substring -> human-friendly message.
OPENAI_FATAL_ERROR_PATTERNS: List[Tuple[str, str]] = [
    ('429',
     'The AI provider reached a usage or rate limit (429). No valve MTO was generated. '
     'Check quota and billing for the selected credential source, or retry after the rate limit clears.'),
    ('insufficient_quota',
     'The selected AI account has no remaining quota. Check its API billing '
     'or replace the key in the selected credential source, then retry.'),
    ('invalid_api_key',
     'The selected API key is invalid or revoked. Replace it in the selected '
     'credential source, then retry.'),
    ('error code: 401',
     'The selected AI provider rejected the API key (401 Unauthorized). '
     'Check the selected credential source.'),
    ('error code: 403',
     'The selected AI provider denied access to the Vision model (403). '
     'Verify this API key has access to the configured model.'),
    ('billing_hard_limit_reached',
     'The selected AI account reached its billing limit. Raise the limit or top up credit.'),
    ('model_not_found',
     'The configured Vision model was not found for this API key (model_not_found). '
     'Check the VALVE_MTO_VISION_MODEL / VALVE_MTO_CLAUDE_MODEL setting or pick another model.'),
    ('error code: 404',
     'The AI provider returned 404 — the configured Vision model does not exist or this '
     'key has no access to it. Check the configured model name.'),
    ('does not have access to model',
     'This API key has no access to the configured Vision model. '
     'Pick an available model or use a key with access.'),
    ('timed out',
     'The AI provider request timed out. Retry; if it persists, lower the image DPI/pages per batch.'),
    ('timeout',
     'The AI provider request timed out. Retry; if it persists, lower the image DPI/pages per batch.'),
    ('connection error',
     'Could not connect to the AI provider. Check outbound network access from the server, then retry.'),
    ('credit balance is too low',
     'The selected Anthropic account has insufficient prepaid credits. '
     'Top up the balance in the Anthropic console, then retry.'),
    ('invalid x-api-key',
     'The Anthropic API key is invalid or revoked. Replace it in the central AI credentials, then retry.'),
    ('authentication_error',
     'The AI provider rejected the API key (authentication_error). '
     'Check the configured key in the central AI credentials.'),
    ('overloaded',
     'The AI provider is temporarily overloaded (529). Retry the extraction in a few minutes.'),
    ('not_found_error',
     'The AI provider returned not_found — the configured Vision model does not exist or this '
     'key has no access to it. Check the configured model name.'),
]


def _classify_openai_error(exc: Exception) -> Optional[str]:
    """Return a friendly message if the exception matches a fatal pattern."""
    msg = str(exc).lower()
    for needle, friendly in OPENAI_FATAL_ERROR_PATTERNS:
        if needle.lower() in msg:
            return friendly
    return None

# FIX 3 — recovers a valve tag from the row's own DESCRIPTION text when
# Vision left VALVE_TAG empty but still wrote the tag into its free-text
# description (e.g. "MOTOR OPERATED VALVE MOV-2002, ...").
_VALVE_TAG_FROM_DESCRIPTION_RE = re.compile(r'\b(MOV|LV|HV|XV|SDV|PCV|FCV|TCV|BDV|PSV)-\d{3,5}\b')

# Project-neutral schema; drawing annotations and supplied legends govern interpretation.
VISION_PROMPT_TEMPLATE = """\
You are a senior piping material engineer preparing a VALVE MATERIAL TAKE-OFF (MTO) from the supplied Piping & Instrumentation Diagram (P&ID). Read the project and drawing conventions from this document and the supplied legends. Do not assume a plant, unit, project, or tag format.

You will receive one P&ID sheet as an image (and possibly its text layer). This call covers page(s) {page_range} of a larger document. Your job is to output ONE ROW PER PHYSICAL VALVE drawn on the sheet that is within scope. You are extracting VALVES, not text.

### 1. What counts as a valve
Valves are GRAPHIC SYMBOLS drawn on the piping lines. They usually have NO text label. You must find them visually.
Identify each symbol using the supplied project legend. Default interpretation if the legend is not provided:
- GATE: two triangles tip-to-tip (bow-tie), both hollow
- GLOBE: bow-tie with a filled dot/circle at the centre
- BALL: bow-tie with a hollow circle at the centre
- CHECK: bow-tie with one triangle filled, or a single triangle with flow arrow / "Z" line
- BUTTERFLY: bow-tie or two vertical lines with a diagonal/disc line between them
- PLUG: bow-tie with a filled rectangle/square at the centre
- NEEDLE: small bow-tie with a "T" or needle marker
- CONTROL VALVE: valve body with an actuator (dome/box) on top and an instrument tag bubble (e.g. PV-2590, FV-…)
- ON/OFF / SHUTDOWN VALVE: valve with actuator and tag XV-…, SDV-…, BDV-…
- SAFETY / RELIEF VALVE: angle-body valve with spring symbol, tag PSV-…
- Vent / drain valves: small valve on a short branch marked "V" (vent) or "D" (drain). These ARE valves; count them.
Do NOT count: instruments without a valve body, spectacle blinds, spacers, reducers, flanges, strainers, steam traps (ST-1), utility station boxes (STA), off-page connector arrows, line-number diamonds, revision triangles.
If a symbol is unclear, still output the row and set TYPE = "UNIDENTIFIED" and CONFIDENCE = "LOW". Never skip a valve.

Project Legend Sheet data (from this user's own active legend sheets per section — AUTHORITATIVE whenever it conflicts with the defaults above; if it says "(none uploaded)", fall back to the defaults):
{legend_context}

### 2. Survey scope: all visible valves, subject to review
- Include EVERY physical valve visible on the sheet, including existing, vendor-supplied, temporary, and out-of-scope valves.
- Do not exclude a valve merely because it is outside a scope or revision cloud.
- Use explicit drawing scope notes and legends to flag EXISTING / NEW / VENDOR-SUPPLIED / TEMPORARY / OUT-OF-SCOPE in REMARKS only when supported by the source.
- If scope or installation status is unclear, write "SCOPE/STATUS REQUIRES REVIEW"; never infer that an existing valve is a new purchase.
- Quantities are observed valve counts, NOT approved procurement quantities. Flag this in warnings.

### 3. Field rules
- SL_NO: Running number on this sheet, starting at 1.
- AREA: Extract the area/unit number. Look for it in these places:
  1. From valve tag number: PSV-1004A → area = 427 (from drawing context); tag format PREFIX-AREA-NUMBER, e.g. PSV-1004A on drawing 427 → AREA = 427.
  2. From drawing number in title block: Drawing 427-xxx → AREA = 427.
  3. From line number: IA-3-AS1A0A-FA-427-1003-N → AREA = 427.
  Use the 3-digit unit/area number. Same area for all valves on the same sheet.
- PID_NUMBER: The drawing number of THIS sheet, taken ONLY from its title block, using its actual project format. NEVER use reference-document numbers or off-page connector destinations. Same value for every row of the sheet.
- LINE_NUMBER: The pipe designation connected to the valve, using the actual project format. For a branch valve, use the header it branches from only when connectivity is visible.

  Line number formats vary by project:
  Format 1: 6-PL-DC3N-8166-P
    (SIZE-SERVICE-CLASS-NUMBER-P)
  Format 2: IA-3-AS1A0A-FA-1003-N
    (SERVICE-SIZE-CLASS-AREA-NUMBER-N)
  Format 3: DR-2-AS1A0A-FA-1018-N
    (DRAIN-SIZE-CLASS-AREA-NUMBER-N)

  Extract the COMPLETE line number exactly as shown on drawing. Include all segments with dashes.
- FLUID_CODES: <<<FLUID_CODES_TABLE>>>
- PIPING_CLASS: Extract the piping specification code for this valve.
  Look in these places in order:
  1. Line number connected to this valve
     e.g. 6-PL-DC3N-8166-P → DC3N
  2. Nearby line numbers on the same pipe
     run or header this valve branches from
  3. A piping class label printed near
     the valve or on the pipe itself
  4. Any spec break annotation nearby

  Examples of piping class codes:
  DC3N, AC3N, BC3N, AC6N, FL, CD, PL,
  AB, AC3, DC3, BC3, AS1A0A, AC1A3B

  If truly not visible anywhere → leave empty
  Do not assume or invent a class.
- PIPING_MATERIAL_CLASS: The material-class description from an explicit drawing annotation or a verified project lookup for PIPING_CLASS. Otherwise leave empty; do not replace it with an unrelated project's class.
- SIZE_1_NB: Valve size. Use a size label printed directly next to the valve symbol (e.g. 1", 3/4", 2") if present; otherwise the size at the start of LINE_NUMBER. Output in inches as written, e.g. 3/4", 1-1/2", 8".
- SIZE_2_NB: Only for PSVs / reducing valves with inlet × outlet sizes (e.g. 2"x3" → SIZE_1 = 2", SIZE_2 = 3"). Otherwise leave empty.
- RATING_FACING: Extract pressure rating. Look for rating:
  - Directly on valve symbol
  - In a box or bubble near the valve
  - Near PSV valves: look for SET PRESSURE, e.g. "130 psig", "180 psig", "1.5G3"
  - Flange rating: 150#, 300#, 600#, 900#
  - PN rating: PN40, PN100
  For PSV valves the set pressure is often shown in a nearby note box.
  If unknown leave empty, put "RATING FROM PMS" in REMARKS.
- FACING: Only the explicitly shown or verified facing/end-connection code (RF, FF, RTJ, SW, BW, NPT); otherwise empty. Keep it separate from the pressure rating.
- BORE: Full/reduced bore only when explicitly shown; otherwise empty.
- LINE_LIST: An explicitly supplied line-list reference, not a copy of a drawing number or unrelated text. Otherwise empty.
- TYPE: From the symbol (section 1): GATE / GLOBE / BALL / CHECK / BUTTERFLY / PLUG / NEEDLE / CONTROL / ON-OFF / PSV / UNIDENTIFIED.
- VALVE_TAG: Only if a tag is printed for this valve. Common tag prefixes:
  - MOV-XXXX (Motor Operated Valve)
  - SDV-XXXX (Shutdown Valve)
  - XV-XXXX (On/Off Valve)
  - PCV-XXXX (Pressure Control Valve)
  - FCV-XXXX (Flow Control Valve)
  - LV-XXXX (Level Valve)
  - HV-XXXX (Hand Valve)
  - PSV-XXXX (Pressure Safety Valve)
  - BDV-XXXX (Blowdown Valve)
  - TCV-XXXX (Temperature Control Valve)
  Look for tag labels:
  - Directly on or next to the valve symbol
  - Inside a bubble or box near the valve
  - In the actuator label above the valve
  - In description text below the valve
  Look for tag text in these locations:
  - Above the valve symbol (actuator area)
  - Below the valve symbol (label area)
  - Inside instrument bubbles connected to the valve
  Manual valves normally have NO tag. Leave empty — do NOT invent a tag.
  Prefix with unit if shown (e.g. 47-PSV-2004).
- DESCRIPTION: <TYPE> VALVE, <SIZE_1>[x<SIZE_2>], <RATING_FACING>, CLASS <PIPING_MATERIAL_CLASS>; add service purpose if obvious (VENT / DRAIN / ISOLATION / BYPASS). For PSVs use the text in the PSV table if present (e.g. "BUTANE DRYER A SAFETY VALVE").
- TAB: "island" or "field" only when installation allocation is explicitly supported by source location/notes or an exact project mapping. AREA is the drawing title and never determines this allocation.
- QTY_ISLAND / QTY_FIELD: 1 per physical valve in the verified TAB's column, 0 in the other. If installation allocation is unknown, leave both 0 and request quantity allocation review.
- UNIT: "NOS"
- REMARKS: Valve position/status text adjacent to the valve: LO (locked open), LC (locked closed), NC (normally closed), NO, CSO, CSC; plus any note that refers to this valve (quote the note number and text).
- CONFIDENCE: HIGH / MEDIUM / LOW: how sure you are of TYPE + SIZE + CLASS together.
- LOCATION_HINT: Grid zone of the valve on the sheet (border letters/numbers, e.g. "D-7") so a checker can find it.
- VALVE_BBOX: The tight valve-body rectangle [x0,y0,x1,y1], each coordinate from 0 to 1000 relative to THIS drawing region, not a legend picture. This establishes its physical location; do not substitute a text-label or instrument-bubble rectangle.
- Never allocate unknown installation scope to FIELD by default, or infer scope from words in the drawing title. Keep both allocation quantities zero.

### 4. Text-cleaning rules (important)
- Never join two separate text items into one value. A P&ID number ends after its last digit group, e.g. 47-PU-152-20112-02. Anything appended (LOW, NOTE, REV, THIS, FOR, Q1, TOHP, digits from an adjacent ref) is a different text item and must be dropped.
- Respect this project's drawing and line formats, not formats from another project.
- If a value is unreadable or joined to an adjacent text item, leave it empty and set CONFIDENCE = "LOW". Do not output a corrupted value.

### 5. Self-check before answering
1. Count the valve symbols inside scope on the image a second time. The number of rows must equal that count.
2. Every row has PID_NUMBER = the title-block number.
3. No row is an off-page connector, line label, or note.
4. Every piping class/material value is supported by its connected line or a supplied project lookup.
5. If the sheet has in-scope piping but you found 0 valves, re-examine. Lines almost always have at least vent/drain/isolation valves.

### 6. Output
Return ONLY valid JSON, no commentary. Populate this schema from the source, not from example defaults. If no physical valves are verified, return an empty valves array.
{{
  "pid_number": "",
  "sheet_title": "",
  "revision": "",
  "scope_rule_applied": "ALL VISIBLE VALVES - REVIEW REQUIRED",
  "valve_count": 1,
  "valves": [ {{ "SL_NO": 1, "AREA": "", "TYPE": "UNIDENTIFIED", "PIPING_MATERIAL_CLASS": "", "PIPING_CLASS": "", "RATING_FACING": "", "FACING": "", "SIZE_1_NB": "", "SIZE_2_NB": "", "BORE": "", "PID_NUMBER": "", "LINE_NUMBER": "", "LINE_LIST": "", "VALVE_TAG": "", "DESCRIPTION": "", "QTY_ISLAND": 0, "QTY_FIELD": 0, "UNIT": "NOS", "REMARKS": "SCOPE/STATUS REQUIRES REVIEW", "CONFIDENCE": "LOW", "LOCATION_HINT": "", "VALVE_BBOX": null }} ],
  "excluded": [],
  "warnings": []
}}

Unverified line-number candidates from text/OCR. Associate one only with a visibly connected physical valve. Never turn these candidates into valve rows on their own:
{candidate_line_numbers}

Text excerpt for context (may contain OCR errors and document-reference numbers). Verify against the drawing; text alone is not evidence of a valve:
---
{text_excerpt}
---

Maximum {max_rows} rows per sheet.

"""

# BUG FIX: the fluid code table used to be a hand-duplicated literal
# here AND in ValveMTO.jsx (for the auto-created "Fluid Code - Standard"
# default legend) — no mechanism kept them in sync. apps.valve_mto.
# fluid_codes.FLUID_CODES is now the single source of truth for both;
# this substitutes the table into the template ONCE at module import
# time (a plain string .replace(), not a per-request .format() kwarg —
# this data never varies per request, so it doesn't need to be threaded
# through every call site the way user-specific data like
# legend_context does). ValveMTO.jsx fetches the SAME dict at runtime
# via GET /api/v1/valve-mto/fluid-codes/ (apps/valve_mto/views.py).
from apps.valve_mto.fluid_codes import FLUID_CODES, format_fluid_codes_for_prompt  # noqa: E402
VISION_PROMPT_TEMPLATE = VISION_PROMPT_TEMPLATE.replace(
    '<<<FLUID_CODES_TABLE>>>', format_fluid_codes_for_prompt(FLUID_CODES),
)

# ─── Helpers ────────────────────────────────────────────────────────────
def _page_count(pdf_path: str) -> int:
    try:
        import fitz
        doc = fitz.open(pdf_path)
        n = doc.page_count
        doc.close()
        return n
    except Exception:                                  # pragma: no cover
        return 0


def _extract_text(pdf_path: str, on_text_progress=None) -> str:
    """
    Best-effort text via PyMuPDF (fast). pdfplumber is only consulted as a
    fallback when PyMuPDF returns less than ``TEXT_SUFFICIENT_CHARS`` —
    pdfplumber is *much* slower (often 10-30× on large searchable PDFs)
    and Vision already handles image-only drawings, so the fallback rarely
    pays for itself.

    ``on_text_progress(current_page, total_pages)`` fires per page so the
    job snapshot keeps advancing during this otherwise-silent phase.
    """
    parts: List[str] = []
    try:
        import fitz
        doc = fitz.open(pdf_path)
        total = doc.page_count
        for i, page in enumerate(doc):
            t = page.get_text() or ''
            if t.strip():
                parts.append(t)
            if on_text_progress:
                try:
                    on_text_progress(i + 1, total)
                except Exception:
                    pass
        doc.close()
    except Exception as exc:                           # pragma: no cover
        logger.warning('PyMuPDF failed: %s', exc)

    combined = '\n'.join(parts)
    if len(combined) >= TEXT_SUFFICIENT_CHARS or os.getenv('VALVE_MTO_DISABLE_PDFPLUMBER', '1') == '1':
        return combined

    # Slow fallback only when PyMuPDF clearly under-extracted.
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ''
                if t.strip():
                    parts.append(t)
    except Exception:                                  # pragma: no cover
        pass
    return '\n'.join(parts)


def _ocr_pages_text(pdf_path: str, on_progress=None) -> str:
    """Tesseract OCR fallback — render each page via PyMuPDF at
    ``VALVE_MTO_OCR_DPI`` and OCR the raster (rotation 0 only, PSM set from
    ``VALVE_MTO_OCR_PSMS``). Returns combined text, '' when unavailable or
    nothing was read. NEVER raises — a broken/missing Tesseract must leave
    the extractor behaving exactly as if this layer didn't exist.
    """
    if not (VALVE_MTO_OCR_ENABLED and PYTESSERACT_AVAILABLE):
        return ''
    parts: List[str] = []
    try:
        import fitz
        from PIL import Image
        doc = fitz.open(pdf_path)
        total = doc.page_count
        zoom = VALVE_MTO_OCR_DPI / 72.0
        mat = fitz.Matrix(zoom, zoom)
        for i, page in enumerate(doc):
            try:
                pix = page.get_pixmap(matrix=mat, alpha=False)
                img = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
                page_txt: List[str] = []
                for psm in VALVE_MTO_OCR_PSMS:
                    try:
                        t = pytesseract.image_to_string(img, config=f'--psm {psm}')
                        if t and t.strip():
                            page_txt.append(t)
                    except Exception:
                        pass
                if page_txt:
                    parts.append('\n'.join(page_txt))
            except Exception:
                pass
            if on_progress:
                try:
                    on_progress(i + 1, total)
                except Exception:
                    pass
        doc.close()
        if parts:
            logger.info('[ValveMTO] Tesseract OCR fallback read %d chars from %d page(s)',
                        sum(len(p) for p in parts), total)
    except Exception as exc:                           # pragma: no cover
        logger.warning('[ValveMTO] Tesseract OCR fallback failed: %s', exc)
    return '\n'.join(parts)


def _maybe_ocr_text(pdf_path: str, text: str, warnings: List[str], reason: str,
                    on_progress=None, force: bool = False) -> Tuple[str, bool]:
    """Run the Tesseract fallback when (a) the PDF text layer is below
    TEXT_SUFFICIENT_CHARS, or (b) ``force`` (Vision attempted but every batch
    failed — e.g. 429 — so OCR the pages before the line-only fallback).
    Returns (possibly-augmented text, ocr_used). Adds a warnings entry when
    OCR actually contributed.
    """
    if not force and len(text or '') >= TEXT_SUFFICIENT_CHARS:
        return text, False
    ocr_text = _ocr_pages_text(pdf_path, on_progress=on_progress)
    if not ocr_text.strip():
        return text, False
    warnings.append(f'{reason} — used Tesseract OCR fallback')
    return ((text or '') + '\n' + ocr_text), True



def _render_pages_b64(pdf_path: str, max_pages: int, dpi: int, on_render_progress=None) -> List[str]:
    """
    Render PDF pages to base64 JPEG strings.

    Soft-coded:
      * `max_pages` — hard cap (VISION_MAX_PAGES)
      * `dpi`       — render DPI (VISION_IMAGE_DPI)
      * `VISION_MAX_EDGE_PX` / `JPEG_QUALITY`

    `on_render_progress(current_page, total_pages)` fires after each page so
    the async job runner can keep its heartbeat alive even before any AI
    batch has completed (PDF rendering on a slim CPU can take minutes).
    """
    images: List[str] = []
    try:
        import fitz
        from PIL import Image
        doc = fitz.open(pdf_path)
        total_to_render = min(doc.page_count, max_pages)
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
            # Cap the longest edge — P&IDs are huge, we don't need 4k pixels
            # to read tag text reliably.
            longest = max(img.size)
            if longest > VISION_MAX_EDGE_PX:
                ratio = VISION_MAX_EDGE_PX / float(longest)
                new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
                img = img.resize(new_size, Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=JPEG_QUALITY, optimize=True)
            images.append(base64.b64encode(buf.getvalue()).decode('ascii'))
            if on_render_progress:
                try:
                    on_render_progress(i + 1, total_to_render)
                except Exception:
                    pass
        doc.close()
    except Exception as exc:                           # pragma: no cover
        logger.warning('Failed rendering PDF pages: %s', exc)
    return images


def _enhance_native_drawing(image):
    """Darken faint neutral CAD strokes without changing coloured scope linework."""
    import numpy as np
    from PIL import Image
    pixels = np.asarray(image.convert('RGB')).copy()
    neutral = pixels.max(axis=2).astype(int) - pixels.min(axis=2).astype(int) <= 3
    ink = neutral & (pixels.min(axis=2) < 250)
    pixels[neutral] = 255
    pixels[ink] = 0
    return Image.fromarray(pixels)


def _drawing_titles(pdf_path, max_pages):
    import fitz
    from .local_valve_mto import drawing_title
    with fitz.open(pdf_path) as document:
        return {index: drawing_title(document[index]) for index in range(min(max_pages, len(document)))}


def _render_native_regions(pdf_path, max_pages, on_render_progress=None):
    """Clip the original PDF before rasterization; never enlarge a low-res preview."""
    import fitz
    from PIL import Image
    from .local_valve_mto import embedded_text_boxes, drawing_title

    if VISION_DETAIL_DPI <= 0 or VISION_REGION_EDGE_PX < 3:
        raise ValueError('Valve MTO detail DPI and region edge must be positive.')
    regions = []
    with fitz.open(pdf_path) as document:
        for page_index, page in enumerate(document):
            if page_index >= max_pages:
                break
            width, height = page.rect.width, page.rect.height
            columns = max(1, math.ceil(width * VISION_DETAIL_DPI / 72 / VISION_REGION_EDGE_PX))
            rows = max(1, math.ceil(height * VISION_DETAIL_DPI / 72 / VISION_REGION_EDGE_PX))
            # Scale the whole grid down proportionally (never just
            # truncate rows/columns) when it exceeds the per-page cap,
            # so a huge sheet still gets full-page coverage at a
            # coarser grid instead of silently losing whatever regions
            # got cut off.
            total_regions = columns * rows
            if total_regions > VISION_MAX_REGIONS_PER_PAGE:
                scale = (VISION_MAX_REGIONS_PER_PAGE / total_regions) ** 0.5
                columns = max(1, round(columns * scale))
                rows = max(1, round(rows * scale))
                logger.info(
                    '[ValveMTO] Page %d: region grid capped to %d regions (%d cols x %d rows)',
                    page_index + 1, columns * rows, columns, rows,
                )
            words = embedded_text_boxes(page)
            sheet_title = drawing_title(page, words)
            identifiers = {
                item.text for item in words
                if item.box[0] > width * 0.65 and item.box[1] > height * 0.9
                and re.fullmatch(DRAWING_NO_PATTERN, item.text, re.IGNORECASE)
            }
            identifier = next(iter(identifiers)) if len(identifiers) == 1 else ''
            for r in range(rows):
                for c in range(columns):
                    core = fitz.Rect(c * width / columns, r * height / rows,
                                     (c + 1) * width / columns, (r + 1) * height / rows)
                    margin_x, margin_y = width / columns * VISION_REGION_OVERLAP, height / rows * VISION_REGION_OVERLAP
                    clip = fitz.Rect(max(0, core.x0 - margin_x), max(0, core.y0 - margin_y),
                                     min(width, core.x1 + margin_x), min(height, core.y1 + margin_y))
                    # Raster bounds round outwards at both edges.
                    zoom = min(VISION_DETAIL_DPI / 72, (VISION_REGION_EDGE_PX - 2) / max(clip.width, clip.height))
                    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
                    image = _enhance_native_drawing(Image.frombytes('RGB', (pix.width, pix.height), pix.samples))
                    buffer = io.BytesIO()
                    image.save(buffer, format='JPEG', quality=95)
                    native_text = '\n'.join(item.text for item in words if fitz.Rect(item.box).intersects(clip))
                    regions.append({
                        'page': page_index, 'box': tuple(clip), 'core': tuple(core),
                        'page_size': (width, height), 'pid_number': identifier, 'sheet_title': sheet_title,
                        'image': base64.b64encode(buffer.getvalue()).decode('ascii'),
                        'size': image.size, 'dpi': round(zoom * 72, 1), 'text': native_text,
                    })
                    if on_render_progress:
                        on_render_progress(len(regions), len(regions) + rows * columns - (r * columns + c + 1))
    return regions


def _region_text(region):
    """Local rotated OCR is a region-specific hint, never a drawing overlay."""
    if not (VALVE_MTO_OCR_ENABLED and PYTESSERACT_AVAILABLE):
        return region['text'], False, ['Region OCR unavailable; verify text against the native drawing image.']
    from PIL import Image
    parts = [region['text']]
    warnings = []
    with Image.open(io.BytesIO(base64.b64decode(region['image']))) as image:
        for angle in (0, 90, 180, 270):
            view = image.rotate(angle, expand=True)
            for psm in VALVE_MTO_OCR_PSMS:
                try:
                    text = pytesseract.image_to_string(view, config=f'--psm {psm}', timeout=30)
                except (RuntimeError, pytesseract.TesseractError, pytesseract.TesseractNotFoundError) as exc:
                    logger.warning('[ValveMTO] Region OCR page=%d angle=%d PSM=%d failed: %s',
                                   region['page'] + 1, angle, psm, exc)
                    warnings.append(f'Page {region["page"] + 1}: rotated region OCR failed; text evidence requires image verification.')
                    continue
                if text.strip():
                    parts.append(text.strip())
    return '\n'.join(dict.fromkeys(parts)), len(parts) > 1, warnings


def _locate_region_rows(data, region):
    """Keep only bodies centred in this region's core; overlap provides context."""
    rows = data.get('valves') or data.get('rows') or []
    if not isinstance(rows, list):
        return data
    kept = []
    warnings = list(data.get('warnings') or [])
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        box = raw.get('VALVE_BBOX')
        valid = (isinstance(box, list) and len(box) == 4
                 and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                         and math.isfinite(v) and 0 <= v <= 1000 for v in box))
        if valid:
            valid = box[0] < box[2] and box[1] < box[3]
        if valid:
            x0, y0, x1, y1 = region['box']
            global_box = (x0 + box[0] / 1000 * (x1 - x0), y0 + box[1] / 1000 * (y1 - y0),
                          x0 + box[2] / 1000 * (x1 - x0), y0 + box[3] / 1000 * (y1 - y0))
            cx, cy = (global_box[0] + global_box[2]) / 2, (global_box[1] + global_box[3]) / 2
            left, top, right, bottom = region['core']
            width, height = region['page_size']
            if not (left <= cx < right or (right == width and cx == right)):
                continue
            if not (top <= cy < bottom or (bottom == height and cy == bottom)):
                continue
            raw['location_hint'] = f'Page {region["page"] + 1} [{", ".join(f"{v:.1f}" for v in global_box)}] pt'
        else:
            raw['CONFIDENCE'] = raw['confidence'] = 'LOW'
            raw['REMARKS'] = _merge_remarks(str(raw.get('REMARKS') or raw.get('remarks') or ''),
                                           'PHYSICAL LOCATION UNVERIFIED - CHECK DUPLICATE/MISSED VALVES')
            warnings.append('Some valve bodies have no valid source bounding box; count and physical locations require review.')
        if region['pid_number']:
            raw['PID_NUMBER'] = raw['pid_number'] = region['pid_number']
        if region.get('sheet_title'):
            raw['AREA'] = raw['area'] = region['sheet_title']
        kept.append(raw)
    return {**data, 'sheet_title': region.get('sheet_title') or data.get('sheet_title', ''),
            'valves': kept, 'rows': kept, 'warnings': list(dict.fromkeys(warnings))}

# ─── Scan mode (Quick vs Thorough) ──────────────────────────────────────
# Soft-coded: the frontend's scan-mode selector (ValveMTO.jsx) sends
# 'quick' or 'thorough' with the extraction request; piping_valve_mto_
# view.py defaults to 'thorough' when not provided at all (old clients /
# any caller that predates this feature), matching this module's own
# default below.
SCAN_MODE_QUICK = 'quick'
SCAN_MODE_THOROUGH = 'thorough'
SCAN_MODE_DEFAULT = SCAN_MODE_THOROUGH


def _scan_mode_params(scan_mode: Optional[str]) -> Tuple[int, int, int]:
    """Returns (tile_rows, tile_cols, passes) for a scan mode.

    Quick  = 1x1 tiles, 1 pass  = 1 Vision call per page (today's
             original, un-tiled behaviour — unchanged).
    Legacy image tiling uses 2x2 tiles and two passes.
    Streaming thorough mode now uses adaptive native PDF regions with one
    pass per region instead; its grid grows with physical sheet size.
    The legacy 2x2 image grid uses 2 passes = 8 Vision calls per page —
             each tile lets Vision focus closely on a quarter of a dense
             P&ID instead of reading the whole page at once (more legible
             tag text, fewer missed valves), and the 2nd pass over each
             tile catches whatever the 1st pass's own run-to-run Vision
             sampling variance missed (real even at temperature=0 — see
             VISION_TEMPERATURE — API-level non-determinism is well-
             documented for both providers, not something this module
             can fully eliminate, so 2 independent passes genuinely do
             surface some extra valves rather than finding the exact
             same set twice).
    Any unrecognised value falls back to thorough (the documented
    default), same as a fully-missing scan_mode.
    """
    mode = (scan_mode or SCAN_MODE_DEFAULT).strip().lower()
    if mode == SCAN_MODE_QUICK:
        return 1, 1, 1
    return 2, 2, 2


def _tile_image_b64(b64_jpeg: str, rows: int, cols: int, overlap_frac: float = 0.08) -> List[str]:
    """Splits one rendered page image into a ROWS x COLS grid of
    overlapping sub-images (each re-encoded to its own base64 JPEG), so
    Vision can focus closely on one section of a dense P&ID at a time
    instead of the whole page at once.

    rows<=1 and cols<=1 is a no-op (returns the original image
    unchanged, no re-encode) — exactly 'quick' scan mode's 1x1 grid.

    `overlap_frac` extends each tile slightly past its grid cell (except
    where it's already at the image's own edge), so a valve symbol or
    tag sitting exactly on a tile boundary still appears whole in at
    least one tile instead of being cut in half in both neighbouring
    ones.
    """
    if rows <= 1 and cols <= 1:
        return [b64_jpeg]
    try:
        from PIL import Image
        raw = base64.b64decode(b64_jpeg)
        img = Image.open(io.BytesIO(raw)).convert('RGB')
        width, height = img.size
        tile_w = width / cols
        tile_h = height / rows
        overlap_x = tile_w * overlap_frac
        overlap_y = tile_h * overlap_frac
        tiles: List[str] = []
        for r in range(rows):
            for c in range(cols):
                left = max(0, c * tile_w - overlap_x)
                top = max(0, r * tile_h - overlap_y)
                right = min(width, (c + 1) * tile_w + overlap_x)
                bottom = min(height, (r + 1) * tile_h + overlap_y)
                crop = img.crop((int(left), int(top), int(right), int(bottom)))
                buf = io.BytesIO()
                crop.save(buf, format='JPEG', quality=JPEG_QUALITY, optimize=True)
                tiles.append(base64.b64encode(buf.getvalue()).decode('ascii'))
        return tiles
    except Exception as exc:                            # pragma: no cover
        logger.warning('[ValveMTO] Tiling failed (%s) — falling back to the untiled page image', exc)
        return [b64_jpeg]


def _coerce_row(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Force a raw dict into the canonical row schema.

    Accepts BOTH the legacy lowercase schema and the new prompt's
    UPPER_SNAKE schema (see UPPER_SNAKE_ROW_MAP). Applies the Section-4
    text-cleaning validators (pid/line number patterns, class cross-check)
    and the CLASS → RATING rule without assuming a particular project.
    """
    # Translate UPPER_SNAKE keys first (legacy lowercase keys win if both
    # somehow present — they were the only schema before this prompt).
    for upper, lower in UPPER_SNAKE_ROW_MAP.items():
        if upper in raw and lower not in raw:
            raw[lower] = raw[upper]

    row: Dict[str, Any] = {}
    for k in ROW_KEYS:
        v = raw.get(k, '')
        if k in NUMERIC_KEYS:
            try:
                row[k] = int(float(str(v).replace(',', '').strip() or 0))
            except (TypeError, ValueError):
                row[k] = 0
        else:
            row[k] = '' if v is None else str(v).strip()

    allocation = str(raw.get('TAB') or row['tab']).strip().lower()
    row['tab'] = allocation if allocation in ('island', 'field', 'combined') else ''
    if not row['area']:
        row['confidence'] = 'LOW'
        row['remarks'] = _merge_remarks(row['remarks'], 'DRG. TITLE NOT IDENTIFIED - VERIFY AREA')
    if row['tab'] not in ('island', 'field'):
        row['qty_island'] = row['qty_field'] = 0
        row['confidence'] = 'LOW'
        row['remarks'] = _merge_remarks(row['remarks'], 'INSTALLATION ALLOCATION UNVERIFIED - VERIFY QUANTITIES')
    row['qty_observed'] = 1 if row['type'] or row['valve_tag'] else 0

    # A detected valve is one observed item, allocated only to a known area.
    if row['type'] or row['valve_tag']:
        if row['qty_island'] == 0 and row['qty_field'] == 0:
            if row['tab'] == 'island':
                row['qty_island'] = 1
            elif row['tab'] == 'field':
                row['qty_field'] = 1

    # ── Section-4 text-cleaning validators ──────────────────────────────
    # Corrupted/merged values (e.g. '47-PU-152-20112-02LOW') are EMPTIED,
    # never passed through, and confidence is dropped to LOW.
    def _low_confidence() -> None:
        if str(row.get('confidence') or '').upper() != 'LOW':
            row['confidence'] = 'LOW'

    if row['pid_number'] and not re.fullmatch(DRAWING_NO_PATTERN, row['pid_number'], re.IGNORECASE):
        logger.info('[ValveMTO] Dropping corrupted pid_number %r (pattern mismatch)', row['pid_number'])
        row['pid_number'] = ''
        _low_confidence()
    from .local_valve_mto import LINE_CHARACTERS
    row['line_number'] = row['line_number'].translate(LINE_CHARACTERS)
    if row['line_number'] and not re.fullmatch(LINE_NO_PATTERN, row['line_number'], re.IGNORECASE):
        logger.info('[ValveMTO] Dropping corrupted line_number %r (pattern mismatch)', row['line_number'])
        row['line_number'] = ''
        _low_confidence()
    if row['piping_class'] and row['line_number'] and row['piping_class'].upper() not in row['line_number'].upper().split('-'):
        row['piping_class'] = ''
        _low_confidence()

    # ── CLASS → RATING rule ─────────────────────────────────────────────
    # Fill rating from CLASS_RATING_TABLE when the class has a value;
    # otherwise (unknown class or blank table value) leave rating empty and
    # mark 'RATING FROM PMS' in remarks. Only for real valve rows — never
    # for synthetic line-only recovery rows.
    if (row['type'] or row['valve_tag'] or row['pms_class']) and not row['rating']:
        table_val = CLASS_RATING_TABLE.get(row['pms_class'], '')
        if row['pms_class'] in CLASS_RATING_TABLE and table_val:
            row['rating'] = table_val
        else:
            row['remarks'] = _merge_remarks(row.get('remarks', ''), 'RATING FROM PMS')

    # Soft-coded Remark derivation. Vision often surfaces operational-status
    # codes (LO, LC, CSO, CSC, TSO, FBLC, FBLO, NRV, NO, NC, FO, FC, FL, FI)
    # or their spelled-out forms ("Locked Open", "Fail Closed", …) inside the
    # valve tag, description, type, line number, or remarks string itself.
    # We scan every text field and MERGE the canonical code list with any
    # pre-existing free-text remarks so legitimate notes aren't destroyed.
    derived = _derive_remarks_from_row(row)
    if derived:
        row['remarks'] = _merge_remarks(row.get('remarks', ''), derived)
    return row


def _parse_vision_payload(data: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, str], List[str], List[Dict[str, Any]]]:
    """Parse ONE Vision response payload into (rows, project_meta, warnings,
    excluded).

    Supports the new sheet-level schema (pid_number / sheet_title /
    revision / scope_rule_applied / valve_count / valves[] / excluded[] /
    warnings[]) and falls back to the legacy rows[] + project_meta schema.
    """
    if not isinstance(data, dict):
        return [], {}, [], []

    rows_raw = data.get('valves') or data.get('rows') or []
    meta = _coerce_meta(data.get('project_meta') or {})

    # Sheet-level fields (new schema) → project_meta
    if data.get('pid_number') and not meta.get('doc_no'):
        meta['doc_no'] = str(data['pid_number']).strip()
    if data.get('sheet_title') and not meta.get('doc_title'):
        meta['doc_title'] = str(data['sheet_title']).strip()
    if data.get('revision') and not meta.get('revision'):
        meta['revision'] = str(data['revision']).strip()

    warnings = [str(w) for w in (data.get('warnings') or []) if w]
    excluded: List[Dict[str, Any]] = []
    for ex in (data.get('excluded') or []):
        if isinstance(ex, dict):
            excluded.append({
                'description': str(ex.get('description') or '').strip(),
                'reason':      str(ex.get('reason') or '').strip(),
            })
        elif ex:
            excluded.append({'description': str(ex), 'reason': ''})

    rows: List[Dict[str, Any]] = []
    if isinstance(rows_raw, list):
        for r in rows_raw:
            if not isinstance(r, dict):
                continue
            raw = dict(r)
            if not raw.get('pid_number') and not raw.get('PID_NUMBER'):
                raw['pid_number'] = data.get('pid_number') or meta.get('doc_no') or ''
            if data.get('sheet_title'):
                raw['area'] = str(data['sheet_title']).strip()
            row = _coerce_row(raw)
            if row['valve_tag'] or row['type']:
                rows.append(row)
            else:
                warnings.append('Ignored an entry without a valve type or valve tag; line/document text is not a valve.')
    return rows, meta, warnings, excluded


def _coerce_meta(raw: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k in ('doc_no', 'doc_title', 'doc_desc', 'revision', 'doc_date', 'project_name'):
        v = raw.get(k, '')
        out[k] = '' if v is None else str(v).strip()
    return out


# ─── Extractors ─────────────────────────────────────────────────────────
def _extract_meta_from_text(text: str) -> Dict[str, str]:
    """Cheap regex scan for project header fields."""
    meta: Dict[str, str] = {}
    patterns = {
        'doc_no':   [r'(?:Company\s+)?Doc(?:ument)?\.?\s*No\.?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{5,})'],
        'revision': [r'\bRev(?:ision)?\.?\s*[:\-]?\s*([A-Z0-9]{1,3})\b'],
        'doc_date': [r'\bDate\s*[:\-]?\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2})',
                     r'\bDate\s*[:\-]?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})'],
        'doc_title': [r'(PIPING\s+VALVES?\s+MTO)', r'(VALVE\s+M(?:ATERIAL\s+)?T(?:AKE[\s-]?OFF)?)'],
    }
    for field, pats in patterns.items():
        for pat in pats:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                meta[field] = m.group(1).strip()
                break
    return meta


# ─── Legend Sheet context ────────────────────────────────────────────────
# CORRECTION: this used to read apps.pid_verification's OWN PIDVLegendSheet
# (an unstructured, AI-Vision-extracted legend system with no "Valve"
# section) — the wrong system. The correct one is apps.pid_checker_v2's
# structured, regex-rule PidCheckerV2LegendSheet — the same model the
# existing, shared LegendSheetsModal component (used by P&ID Verification
# V1/V2) already manages, with real 'valve'/'piping'/'line_list'/
# 'scope_symbols'/'limit_line' sections and per-(user, section)
# activation. Cross-app read-only import — apps.pid_checker_v2 is not
# edited by this feature at all, only queried.
#
# Cap so a very large/verbose legend can't blow out the prompt's token
# budget — same spirit as the existing text_excerpt[:6000] truncation.
# BUG FIX (real, confirmed): was 4000 — too small once a user's legends
# per section got merged into ONE legend bundling everything (Area Codes,
# Fluid Codes, Line/Drawing Format all in 'line_list'; Valve ID/Symbols/
# Special Numbering/Control Valve types all in 'valve'; etc. — see
# ValveMTO.jsx's SECTION_DEFAULT_LEGENDS). Confirmed live: with that
# merge, _build_legend_context() hit the 4000-char cap mid-way through
# the area-codes lookup table, truncating everything after it — including
# the "Area Boundaries" block (scope_symbols/limit_line), the MANDATORY
# AREA RULE's secondary ISLAND/FIELD signal, which sits last in the
# formatted output and so was the first thing lost. Raised to 12000,
# comfortably covering the now-larger merged-legend content (verified:
# the real formatted context for a representative merged-legend user
# stays well under this with every section intact — see this module's
# test coverage / the turn that raised this).
LEGEND_CONTEXT_MAX_CHARS = int(os.getenv('VALVE_MTO_LEGEND_CONTEXT_MAX_CHARS', '12000'))

# Essential — injected whenever the user has an active legend for these;
# each maps 1:1 to one of Valve MTO's own extracted fields (type,
# pms_class/piping_class, line_number respectively). 'instrument_signal'
# helps distinguish process piping lines from instrumentation/signal
# lines (pneumatic, hydraulic, capillary, electrical, etc.) when reading
# the drawing. 'control_valve_regulator' fills a real gap in the "type"
# field's own 15-entry VALVE SYMBOL REFERENCE: control-valve variants
# (hand-wheel, angle-type) and regulators (pressure/temperature/level/
# back-pressure) are genuine valve types that reference doesn't cover —
# see frontend ValveMTO.jsx's "Instrumentation Lines - Standard" /
# "Control Valve - Standard" default legends, which this constant must
# stay in sync with (see that file's VALVE_MTO_LEGEND_SECTIONS /
# VALVE_MTO_LEGEND_SECTIONS_UI_ONLY).
LEGEND_SECTIONS_ESSENTIAL = ('valve', 'piping', 'line_list', 'instrument_signal', 'control_valve_regulator')
# Optional — only relevant to the 'area' (ISLAND/Field) field, via
# battery-limit/scope-boundary markers; injected only when present.
LEGEND_SECTIONS_OPTIONAL = ('scope_symbols', 'limit_line')

_LEGEND_SECTION_PROMPT_LABELS = {
    'valve': 'Valve Types',
    'piping': 'Piping Classes',
    'line_list': 'Line Format',
    'instrument_signal': 'Instrumentation Line Types',
    'control_valve_regulator': 'Control Valve & Regulator Types',
    'scope_symbols': 'Area Boundaries (Scope Symbols)',
    'limit_line': 'Area Boundaries (Limit Line)',
}


def _format_legend_definition(definition: dict) -> str:
    """Turns one PidCheckerV2LegendSheet.definition (see that model/
    LegendSheetsModal.jsx's own FormEditor for the exact shape —
    {separator, fields: [{key, label, regex, suffix, optional, lookup,
    notes}]}) into a compact, human-readable block for the Vision prompt.
    Prioritises each field's "lookup" table (CODE = DESCRIPTION pairs —
    exactly what "valve type codes and descriptions" / "PMS/piping class
    definitions" means in practice) and "notes" (free-text hints the
    legend's own author wrote specifically to help an AI reader), over
    the raw regex/JSON shape, which is not itself useful prompt content.
    """
    if not isinstance(definition, dict):
        return ''
    lines: List[str] = []
    separator = definition.get('separator')
    if separator:
        lines.append(f'Format separator: "{separator}"')
    for f in (definition.get('fields') or []):
        if not isinstance(f, dict):
            continue
        label = f.get('label') or f.get('key') or ''
        notes = (f.get('notes') or '').strip()
        line = f'- {label}'
        if notes:
            line += f' ({notes})'
        lookup = f.get('lookup')
        if isinstance(lookup, dict) and lookup:
            line += ': ' + ', '.join(f'{k}={v}' for k, v in lookup.items())
        if label or notes or (isinstance(lookup, dict) and lookup):
            lines.append(line)
    return '\n'.join(lines)


def _build_legend_context(user_id) -> str:
    """Formats this user's own ACTIVE legend sheets (apps.valve_mto's OWN
    ValveMTOLegend table, sections in LEGEND_SECTIONS_ESSENTIAL/_OPTIONAL)
    into the "LEGEND REFERENCE:" block injected into the Vision prompt.

    CHANGED (architectural fix, this session): used to read
    apps.pid_checker_v2.PidCheckerV2LegendSheet — the SAME table P&ID
    Verification V1/V2 reads, which has only one active legend per (user,
    section) with no per-module dimension at all. Valve MTO's own
    auto-created defaults needing to be active here kept silently
    becoming the active legend in P&ID V1/V2 too (confirmed real,
    repeatedly reported this session). apps.valve_mto.models.ValveMTOLegend
    is a fully separate table — activating a legend here can no longer
    affect P&ID V1/V2 at all, by construction, not by convention.

    Returns '' (never raises) when the user has no active legend for any
    of these sections — Vision then falls back to its own general
    knowledge exactly as it did before this feature existed; a missing
    legend must never block or degrade a Valve MTO extraction.
    """
    if not user_id:
        return ''
    try:
        from apps.valve_mto.models import ValveMTOLegend
    except Exception as exc:  # noqa: BLE001
        logger.warning('[ValveMTO] valve_mto legend model unavailable: %s', exc)
        return ''

    try:
        all_sections = LEGEND_SECTIONS_ESSENTIAL + LEGEND_SECTIONS_OPTIONAL
        by_section = {
            s.section: s for s in ValveMTOLegend.objects.filter(
                created_by_id=user_id, is_active=True, section__in=all_sections,
            )
        }
    except Exception as exc:  # noqa: BLE001
        # A legend-lookup failure must never abort extraction itself —
        # same "never let an enrichment step break the core feature"
        # principle as this module's other soft-failing helpers.
        logger.warning('[ValveMTO] Failed to build legend context for user_id=%s: %s', user_id, exc)
        return ''

    if not by_section:
        return ''

    lines = ['LEGEND REFERENCE:']
    found_any = False
    for section in LEGEND_SECTIONS_ESSENTIAL:
        sheet = by_section.get(section)
        if not sheet:
            continue
        text = _format_legend_definition(sheet.definition)
        if text:
            lines.append(f'{_LEGEND_SECTION_PROMPT_LABELS[section]}:\n{text}')
            found_any = True

    area_blocks = []
    for section in LEGEND_SECTIONS_OPTIONAL:
        sheet = by_section.get(section)
        if not sheet:
            continue
        text = _format_legend_definition(sheet.definition)
        if text:
            area_blocks.append(f'[{_LEGEND_SECTION_PROMPT_LABELS[section]}]\n{text}')
    if area_blocks:
        lines.append('Area Boundaries:\n' + '\n'.join(area_blocks))
        found_any = True

    if not found_any:
        return ''
    return '\n\n'.join(lines)[:LEGEND_CONTEXT_MAX_CHARS]


def _resolve_vision_credential(vision_provider: Optional[str], vision_api_key: Optional[str],
                               credential_mode: str = 'central') -> Tuple[str, str]:
    """Resolve the selected provider in the worker, respecting managed policy."""
    provider = (vision_provider or 'openai').strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError('Choose OpenAI or Claude for AI Vision extraction.')
    legacy_key = (vision_api_key or '').strip()
    if credential_mode == 'byok':
        if not legacy_key:
            raise ValueError('Enter a personal API key for BYOK.')
        return provider, legacy_key
    if credential_mode != 'central':
        raise ValueError('Choose central credentials or BYOK.')
    return provider, provider_api_key(provider, fallback=lambda: legacy_key) or ''


def _build_legend_reference_images(user_id) -> List[Dict[str, str]]:
    if not user_id:
        return []
    from PIL import Image
    from .local_valve_mto import _load_references
    references = []
    seen = set()
    for name, pixels in _load_references(user_id):
        image = Image.fromarray(pixels)
        image.thumbnail((256, 256))
        buffer = io.BytesIO()
        image.save(buffer, format='JPEG', quality=85)
        encoded = base64.b64encode(buffer.getvalue()).decode('ascii')
        if (name, encoded) not in seen:
            references.append({'name': name, 'image': encoded})
            seen.add((name, encoded))
    return references


def _extract_json_object(text: str) -> Dict[str, Any]:
    """Best-effort JSON-object recovery from a Vision response.

    OpenAI calls use response_format={'type': 'json_object'}, which
    guarantees a clean, bare JSON object — json.loads(text) alone always
    succeeds there. Claude has no equivalent native JSON-mode constraint,
    so its response can arrive wrapped in a markdown code fence (```json
    ... ```) or preceded by a short sentence despite the prompt asking
    for JSON only — this strips a fence if present, then falls back to
    locating the first '{'...last '}' span, before giving up and raising
    (the caller already treats a raising batch as a failed-but-not-fatal
    batch, same as any other exception from a Vision call).
    """
    stripped = text.strip()
    if stripped.startswith('```'):
        stripped = re.sub(r'^```[a-zA-Z]*\n?', '', stripped)
        stripped = re.sub(r'\n?```$', '', stripped).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    start, end = stripped.find('{'), stripped.rfind('}')
    if start != -1 and end != -1 and end > start:
        return json.loads(stripped[start:end + 1])
    raise json.JSONDecodeError('No JSON object found in response', stripped, 0)


def _recover_truncated_rows(text: str) -> Optional[Dict[str, Any]]:
    """Best-effort recovery for a Vision response whose "rows" array was
    cut off mid-object — the response hit max_tokens partway through a
    dense page's row list, so the array never got its closing ']' (and
    often not even the last row object's closing '}'). Discarding the
    whole batch (the previous behaviour) throws away every row Vision DID
    finish writing before the cutoff — for a dense P&ID that can be
    dozens of genuine valve rows. Same technique as
    apps.instrument_io_workflow.services.pid_vision_extractor's own
    _recover_truncated_json_array, adapted for this schema's top-level
    OBJECT ({"project_meta": {...}, "rows": [...]}) rather than a bare
    top-level array: locates the "rows" key specifically (not just the
    first '[' in the text, which could theoretically belong to something
    else), walks forward string-aware tracking object/array nesting
    depth, and remembers the end of the LAST fully-closed row object
    directly inside that array.

    Returns {'rows': [...], 'project_meta': {...}} (project_meta best-
    effort recovered too, since it's written before "rows" and is often
    still intact even when "rows" gets cut off) or None if not even one
    complete row object was found.
    """
    stripped = (text or '').strip()
    if stripped.startswith('```'):
        stripped = re.sub(r'^```[a-zA-Z]*\n?', '', stripped)
        stripped = re.sub(r'\n?```$', '', stripped).strip()

    rows_key = stripped.find('"rows"')
    if rows_key == -1:
        return None
    start = stripped.find('[', rows_key)
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    last_complete_object_end = None
    for i in range(start, len(stripped)):
        ch = stripped[i]
        if in_string:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in '{[':
            depth += 1
        elif ch in '}]':
            depth -= 1
            if ch == '}' and depth == 1:
                last_complete_object_end = i + 1
            if depth == 0:
                # Array closed normally — normal parsing would already
                # have succeeded, so there's nothing to recover here.
                return None

    if last_complete_object_end is None:
        return None

    rows_json = stripped[start:last_complete_object_end] + ']'
    try:
        rows = json.loads(rows_json)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(rows, list):
        return None

    project_meta: Dict[str, Any] = {}
    meta_match = re.search(r'"project_meta"\s*:\s*(\{.*?\})\s*,\s*"rows"', stripped, re.DOTALL)
    if meta_match:
        try:
            parsed_meta = json.loads(meta_match.group(1))
            if isinstance(parsed_meta, dict):
                project_meta = parsed_meta
        except (json.JSONDecodeError, ValueError):
            pass

    return {'rows': rows, 'project_meta': project_meta}


def _call_vision_batch_json(provider: str, api_key: str, prompt: str, batch_imgs: List[str],
                             batch_idx: Optional[int] = None, credential_mode: str = 'central',
                             legend_images: Optional[List[Dict[str, str]]] = None,
                             vision_model: Optional[str] = None) -> Dict[str, Any]:
    """One Vision call for one batch of page images, for EITHER provider —
    returns the parsed {'rows': [...], 'project_meta': {...}, '_usage':
    {'input_tokens': int, 'output_tokens': int}} dict (or raises on any
    failure, which callers already handle per-batch). '_usage' is read
    straight off the API response's own usage/token-count field for
    whichever provider was called — real values, not estimates. It's an
    extra top-level key the caller reads directly; _parse_vision_payload
    only ever reads its own known keys ('valves'/'rows'/'project_meta'/
    etc.), so '_usage' riding along in the same dict is harmless there.
    Shared by both the non-streaming and streaming extraction paths so
    Claude support only had to be added in one place.

    `batch_idx` is purely for logging (which provider/model served which
    batch — real diagnostic gap found & fixed: nothing previously logged
    this per batch, only once for the whole job) and is optional so
    existing callers that don't have a batch index still work unchanged.

    `vision_model`: caller-chosen model for this provider (e.g. the
    frontend's "AI Model" dropdown). None/empty falls back to
    VISION_MODELS[provider] — the env-configured default — unchanged from
    before this parameter existed.
    """
    resolved_model = (vision_model or '').strip() or VISION_MODELS.get(provider, VISION_MODEL)
    batch_label = f'batch {batch_idx}' if batch_idx is not None else 'batch'
    logger.info('[ValveMTO] %s: calling provider=%s model=%s', batch_label, provider, resolved_model)
    if credential_mode not in ('central', 'byok'):
        raise ValueError('Choose central credentials or BYOK.')
    client_factory = personal_provider_client if credential_mode == 'byok' else lazy_provider_client
    client_key = api_key if credential_mode == 'byok' else lambda: api_key
    if legend_images:
        prompt += (
            '\nIMAGE ROLES: The first images are DRAWING TILES: extract physical valves only from these. '
            'Subsequent captioned LEGEND REFERENCE images are lookup examples, never drawing valves to count. '
            'LEGEND REFERENCE images include valve symbols, scope boundary markers, and limit line markers. '
            'Use them to identify both valve types AND area boundaries (ISLAND/FIELD classification). '
            'Use their captions and shapes together with the legend text to classify source symbols. '
            'If a source symbol does not correspond reliably, keep its type UNIDENTIFIED. '
            'Never infer missing specifications from a generic reference shape.'
        )

    if provider == 'claude':
        import anthropic
        client = client_factory(
            'anthropic', anthropic.Anthropic, api_key=client_key, timeout=VISION_TIMEOUT_SECS,
        )
        content: List[Dict[str, Any]] = [{'type': 'text', 'text': prompt}]
        for b64 in batch_imgs:
            content.append({
                'type': 'image',
                'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': b64},
            })
        for reference in legend_images or []:
            content.extend([
                {'type': 'text', 'text': f'LEGEND REFERENCE ONLY (do not count): {reference["name"]}'},
                {'type': 'image', 'source': {
                    'type': 'base64', 'media_type': 'image/jpeg', 'data': reference['image'],
                }},
            ])
        claude_model = resolved_model
        # BUG FIX (real, confirmed): sending `thinking: {'type': 'disabled'}`
        # to a non-Opus Claude 5.x model (confirmed with Sonnet 5.5) gets the
        # call rejected — that payload is only valid for models WITH
        # thinking to disable. Opus 5.5 has always-on adaptive thinking and
        # was never sent this param either (omitting it keeps the default
        # medium effort the request was tuned for). Net effect: no model in
        # this call ever needs an explicit `thinking` kwarg, so none is sent
        # for any Claude model — each one just uses its own default.
        create_kwargs: Dict[str, Any] = {}
        resp = client.messages.create(
            model=claude_model,
            # BUG FIX (real, confirmed root cause of "mostly empty rows"):
            # was 8192 — far too low for a dense P&ID page. Confirmed live
            # via server logs: 7 of 11 batches on one real extraction all
            # failed JSON parsing at ~16,500-17,050 characters, an exact
            # match for 8192 tokens' worth of output getting cut off
            # mid-row, every single time. Raised to 32000 to match the
            # same budget already used elsewhere in this codebase for
            # dense-page Vision extraction (apps.pid_checker_v2.services.
            # vision_extractor's own VISION_MAX_TOKENS), then to 40000
            # once Thorough Scan's per-tile calls started asking for a
            # full row set from a single quadrant of a dense page at
            # closer zoom — more legible detail genuinely means more rows
            # Vision can legitimately find and write per tile, so the
            # same truncation risk reappears at the old cap under heavy
            # tile density.
            max_tokens=40000,
            # create_kwargs is always empty now — see the BUG FIX comment
            # above where it's declared — no Claude model gets an explicit
            # `thinking` kwarg any more.
            system='You are an expert piping engineer extracting a Valve Material Take-Off (MTO) table from a P&ID drawing. Return ONLY a JSON object — no prose, no markdown fences.',
            messages=[{'role': 'user', 'content': content}],
            **create_kwargs,
        )
        usage = {
            'input_tokens': getattr(resp.usage, 'input_tokens', 0) or 0,
            'output_tokens': getattr(resp.usage, 'output_tokens', 0) or 0,
        }
        parts = [b.text for b in resp.content if getattr(b, 'type', None) == 'text']
        raw = ''.join(parts) or '{}'
        try:
            result = _extract_json_object(raw)
            result['_usage'] = usage
            return result
        except (json.JSONDecodeError, ValueError):
            recovered = _recover_truncated_rows(raw)
            if recovered is not None:
                logger.warning(
                    '[ValveMTO] %s: response JSON was truncated (likely hit max_tokens) — '
                    'recovered %d complete row(s) from before the cutoff instead of discarding '
                    'the whole batch', batch_label, len(recovered['rows']),
                )
                recovered['_usage'] = usage
                return recovered
            raise

    # openai (default)
    from openai import OpenAI
    client = client_factory('openai', OpenAI, api_key=client_key, timeout=VISION_TIMEOUT_SECS)
    content = [{'type': 'text', 'text': prompt}]
    for b64 in batch_imgs:
        content.append({'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{b64}'}})
    for reference in legend_images or []:
        content.extend([
            {'type': 'text', 'text': f'LEGEND REFERENCE ONLY (do not count): {reference["name"]}'},
            {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{reference["image"]}'}},
        ])
    resp = client.chat.completions.create(
        model=resolved_model,
        temperature=VISION_TEMPERATURE,
        response_format={'type': 'json_object'},
        messages=[{'role': 'user', 'content': content}],
    )
    usage = {
        'input_tokens': getattr(resp.usage, 'prompt_tokens', 0) or 0,
        'output_tokens': getattr(resp.usage, 'completion_tokens', 0) or 0,
    }
    raw = resp.choices[0].message.content or '{}'
    try:
        result = json.loads(raw)
        result['_usage'] = usage
        return result
    except (json.JSONDecodeError, ValueError):
        recovered = _recover_truncated_rows(raw)
        if recovered is not None:
            logger.warning(
                '[ValveMTO] %s: response JSON was truncated (likely hit max_tokens) — '
                'recovered %d complete row(s) from before the cutoff instead of discarding '
                'the whole batch', batch_label, len(recovered['rows']),
            )
            recovered['_usage'] = usage
            return recovered
        raise


def test_api_key(provider: str, api_key: str, model: Optional[str] = None) -> Tuple[bool, str]:
    """One minimal call to confirm a BYOK key actually works — tests
    EXACTLY the key passed in, never an admin-managed substitute (unlike
    a quirk in apps.instrument_io_workflow's own test_api_key, which can
    silently test the admin key instead when one is configured — that
    would defeat the purpose of a "Test Connection" button for a key the
    user just typed, so this does not replicate that behaviour).

    model: optional caller-chosen model override (frontend BYOK dropdown);
    None/empty falls back to VISION_MODELS[provider], unchanged."""
    provider = (provider or '').strip().lower()
    api_key = (api_key or '').strip()
    model = (model or '').strip() or None
    if provider not in SUPPORTED_PROVIDERS:
        return False, f"Unsupported provider '{provider}'."
    if not api_key:
        return False, 'API key is required.'
    try:
        if provider == 'claude':
            import anthropic
            client = personal_provider_client('anthropic', anthropic.Anthropic, api_key=api_key,
                                              timeout=20, max_retries=0)
            # Same fix as _call_vision_batch_json above: `thinking: {'type':
            # 'disabled'}` is rejected by non-Opus Claude 5.x models, so no
            # explicit `thinking` kwarg is sent for any Claude model here.
            test_kwargs: Dict[str, Any] = {}
            client.messages.create(
                model=model or VISION_MODELS['claude'], max_tokens=TEST_CONNECTION_MAX_TOKENS,
                messages=[{'role': 'user', 'content': 'Hi'}], **test_kwargs,
            )
        else:
            from openai import OpenAI
            client = personal_provider_client('openai', OpenAI, api_key=api_key, timeout=20, max_retries=0)
            client.chat.completions.create(
                model=model or VISION_MODELS['openai'],
                max_tokens=TEST_CONNECTION_MAX_TOKENS,
                messages=[{'role': 'user', 'content': 'Hi'}],
            )
        return True, 'API key is valid and working!'
    except Exception as exc:  # noqa: BLE001
        http_status = getattr(exc, 'status_code', None) or getattr(exc, 'http_status', None)
        if http_status in (401, 403):
            return False, 'Invalid API key. Please check and try again.'
        if http_status == 404:
            return False, f"Model '{model}' is not available on this key. Pick another model."
        return False, f'Connection test failed: {exc}'


def _extract_via_vision(pdf_path: str, text_excerpt: str) -> Dict[str, Any]:
    """
    Render every page (up to VISION_MAX_PAGES), split into batches of
    VISION_BATCH_SIZE pages each, then call OpenAI in parallel.
    All rows are merged across batches, deduplicated and renumbered.
    """
    api_key = provider_api_key('openai', fallback=(lambda: (os.getenv('OPENAI_API_KEY'))))
    if not api_key:
        return {'rows': [], 'project_meta': {}, 'warnings': ['vision skipped — no OPENAI_API_KEY']}

    try:
        from openai import OpenAI
    except Exception:
        return {'rows': [], 'project_meta': {}, 'warnings': ['vision skipped — openai package unavailable']}

    images = _render_pages_b64(pdf_path, VISION_MAX_PAGES, VISION_IMAGE_DPI)
    if not images:
        return {'rows': [], 'project_meta': {}, 'warnings': ['vision skipped — no pages rendered']}

    # Harvest line-number candidates from PDF text — passed to Vision as a hint.
    candidate_lines = _harvest_line_numbers(text_excerpt or '')
    candidate_block = '\n'.join(f'  • {ln}' for ln in candidate_lines) if candidate_lines else '  (none detected in text layer — rely on drawing imagery)'

    # Split into batches.
    batches: List[Tuple[int, List[str]]] = []
    for i in range(0, len(images), VISION_BATCH_SIZE):
        batches.append((i, images[i:i + VISION_BATCH_SIZE]))

    client = lazy_provider_client('openai', OpenAI, api_key=lambda: (api_key), timeout=VISION_TIMEOUT_SECS)
    logger.info(
        '[ValveMTO] Vision → model=%s pages=%d batches=%d (size=%d, parallel=%d) dpi=%d',
        VISION_MODEL, len(images), len(batches), VISION_BATCH_SIZE,
        VISION_PARALLEL_BATCHES, VISION_IMAGE_DPI,
    )

    def _call_one_batch(batch_idx: int, batch_imgs: List[str]) -> Tuple[List[Dict[str, Any]], Dict[str, str], List[str]]:
        prompt = VISION_PROMPT_TEMPLATE.format(
            max_rows=MAX_ROWS,
            text_excerpt=(text_excerpt or '')[:6000],
            page_range=f'{batch_idx + 1}–{batch_idx + len(batch_imgs)}',
            candidate_line_numbers=candidate_block,
            # This non-streaming path (unused by any live caller — see its
            # own module-level note) has no user_id to look up a legend
            # for; matches this feature's own "no legend uploaded" fallback.
            legend_context='(none uploaded)',
        )
        content: List[Dict[str, Any]] = [{'type': 'text', 'text': prompt}]
        for b64 in batch_imgs:
            content.append({
                'type': 'image_url',
                'image_url': {'url': f'data:image/jpeg;base64,{b64}'},
            })
        try:
            resp = client.chat.completions.create(
                model=VISION_MODEL,
                temperature=VISION_TEMPERATURE,
                response_format={'type': 'json_object'},
                messages=[{'role': 'user', 'content': content}],
            )
            raw = resp.choices[0].message.content or '{}'
            data = json.loads(raw)
        except Exception as exc:
            logger.warning('[ValveMTO] Batch %d failed: %s', batch_idx, exc)
            return [], {}, [f'batch starting at page {batch_idx + 1} failed: {exc}'], []

        rows, meta, warns, excluded = _parse_vision_payload(data)
        return rows, meta, warns, excluded

    all_rows: List[Dict[str, Any]] = []
    all_excluded: List[Dict[str, Any]] = []
    merged_meta: Dict[str, str] = {}
    warnings: List[str] = []

    parallelism = max(1, min(VISION_PARALLEL_BATCHES, len(batches)))
    with ThreadPoolExecutor(max_workers=parallelism) as pool:
        futures = {pool.submit(_call_one_batch, idx, imgs): idx for idx, imgs in batches}
        # Collect results in submission order so row order roughly tracks page order.
        results_by_idx: Dict[int, Tuple[List[Dict[str, Any]], Dict[str, str], List[str], List[Dict[str, Any]]]] = {}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                results_by_idx[idx] = fut.result()
            except Exception as exc:                                            # pragma: no cover
                results_by_idx[idx] = ([], {}, [f'batch {idx} crashed: {exc}'], [])

    for idx in sorted(results_by_idx):
        rows, meta, warns, excluded = results_by_idx[idx]
        all_rows.extend(rows)
        all_excluded.extend(excluded)
        for k, v in meta.items():
            if v and not merged_meta.get(k):
                merged_meta[k] = v
        warnings.extend(warns)

    # Deduplicate across batches — same valve appearing on consecutive pages
    # must not be double-counted. Key on the discriminating columns.
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for r in all_rows:
        key = (
            (r.get('area') or '').lower(),
            (r.get('valve_tag') or '').lower(),
            (r.get('pms_class') or '').lower(),
            (r.get('size_1') or '').lower(),
            (r.get('rating') or '').lower(),
            (r.get('description') or '').lower(),
            (r.get('type') or '').lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)

    # Cap and renumber.
    deduped = deduped[:MAX_ROWS]
    for i, r in enumerate(deduped):
        r['sl_no'] = i + 1

    return {'rows': deduped, 'project_meta': merged_meta, 'warnings': warnings, 'excluded': all_excluded}


# ─── Public API ─────────────────────────────────────────────────────────
def extract_valve_mto(pdf_path: str) -> Dict[str, Any]:
    return extract_valve_mto_streaming(pdf_path, scan_mode='quick')


# ─── Streaming public API (used by the async job runner) ────────────────
def _dedupe_and_renumber(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate tagged sightings within a drawing and installation area.

    Untagged sightings need matching physical bounds and attributes; a shared
    line number or coarse grid reference cannot identify one physical valve.
    Retain unlocated candidates for review rather than silently losing counts.
    """
    seen = set()
    out: List[Dict[str, Any]] = []
    for r in rows:
        area = (r.get('area') or '').lower()
        valve_tag = (r.get('valve_tag') or '').strip().lower()
        if valve_tag:
            key = ('tagged', r.get('pid_number', ''), area, valve_tag)
        else:
            if not re.fullmatch(r'Page \d+ \[[\d., -]+\] pt', str(r.get('location_hint') or '')):
                # BUG FIX: unlocated rows (no valid VALVE_BBOX from
                # Vision — see _locate_region_rows' own 'else' branch)
                # used to bypass dedup entirely and always be kept.
                # Harmless for a single whole-page render, but with
                # region-based scanning the SAME untagged valve can be
                # reported by Vision in more than one overlapping
                # region without a usable bbox in either response,
                # producing real duplicate rows. Deduplicated here by
                # content instead (not geometry, since none is
                # available) — genuinely different valves sharing a
                # type/size but differing in description still both
                # survive; line_number is deliberately left OUT of this
                # key so a region that filled it in doesn't survive
                # alongside an earlier sighting that left it empty.
                content_key = (
                    r.get('pid_number', ''),
                    r.get('type', ''),
                    str(r.get('size_1', '')),
                    str(r.get('description', ''))[:50],
                )
                if content_key in seen:
                    continue  # drop duplicate
                seen.add(content_key)
                out.append(r)
                continue
            key = (
                'untagged',
                r.get('pid_number', ''),
                r.get('location_hint', ''),
                area,
                (r.get('type') or '').lower(),
                (r.get('size_1') or '').lower(),
                (r.get('line_number') or '').lower(),
                (r.get('pms_class') or '').lower(),
                (r.get('rating') or '').lower(),
                (r.get('description') or '').lower(),
            )
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    out = out[:MAX_ROWS]
    for i, r in enumerate(out):
        r['sl_no'] = i + 1
    return out


def _match_tag_to_line_number(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """FIX 3 — post-processing: a valve (tagged, e.g. MOV-2002/SDV-2001, OR
    untagged, e.g. a manual ball valve) whose line_number Vision left
    empty can sometimes be recovered from a nearby row that shares the
    same area and size and DOES have a line number — e.g. a sibling
    valve on the same branch whose own line was read correctly. Only
    applied when there is EXACTLY ONE distinct line_number value among
    the matching rows — an ambiguous group (multiple different candidate
    lines) is left alone rather than guessed. Never invents a line
    number from scratch; only ever copies one already present on another
    row in this same extraction.

    BUG FIX: previously restricted to rows with a valve_tag, which meant
    untagged valves (e.g. manual ball valves, which normally have NO tag
    per the Vision prompt's own VALVE_TAG rule) never got this recovery
    even when they had the same area/size as a sibling with a known
    line. Removed that restriction — any row with a missing line_number
    is now eligible, tagged or not.
    """
    by_group: Dict[Tuple[str, str], set] = {}
    for r in rows:
        line_number = str(r.get('line_number') or '').strip()
        if not line_number:
            continue
        key = ((r.get('area') or '').strip().lower(), str(r.get('size_1') or '').strip().lower())
        by_group.setdefault(key, set()).add(line_number)

    for r in rows:
        line_number = str(r.get('line_number') or '').strip()
        if line_number:
            continue
        key = ((r.get('area') or '').strip().lower(), str(r.get('size_1') or '').strip().lower())
        candidates = by_group.get(key)
        if candidates and len(candidates) == 1:
            r['line_number'] = next(iter(candidates))
            existing_remarks = r.get('remarks', '')
            r['remarks'] = (existing_remarks + ' Line number from nearby valve').strip()
    return rows


def extract_valve_mto_streaming(
    pdf_path: str,
    on_progress=None,
    on_partial=None,
    vision_provider: Optional[str] = None,
    vision_api_key: Optional[str] = None,
    vision_model: Optional[str] = None,
    user_id=None,
    scan_mode: Optional[str] = None,
    extraction_mode: str = 'vision',
    credential_mode: str = 'central',
    pms_data: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Same logic as `extract_valve_mto` but emits incremental progress/results
    through callbacks so a long-running async job can be polled.

    Callbacks
    ---------
    * `on_progress(current_batch:int, total_batches:int, rows_so_far:int)`
    * `on_partial(rows_so_far:list, project_meta_so_far:dict)`

    vision_provider/vision_api_key: see _resolve_vision_credential for the
    selected provider's central policy. Explicit credential_mode='byok'
    uses only the supplied personal key, without registry or environment
    fallback. Default central mode preserves existing callers' behavior.

    vision_model: caller-chosen model for the selected provider (e.g. the
    frontend's "AI Model" dropdown). None/empty (every existing caller
    before this parameter existed) falls back to VISION_MODELS[provider],
    unchanged from before.

    user_id: whoever started this extraction — used by
    _build_legend_context to look up their own uploaded Legend Sheet(s)
    and ground the Vision prompt in real project data. None (default,
    same as every existing caller before this feature existed) means no
    legend context, same as before.

    scan_mode: 'quick' or 'thorough' — see _scan_mode_params for exactly
    what each resolves to. None/unrecognised defaults to 'thorough'
    (SCAN_MODE_DEFAULT), matching piping_valve_mto_view.py's own default
    for a request that doesn't specify one at all.

    pms_data: optional list of {code, pms_class?, rating?, facing?} dicts
    parsed client-side from an uploaded PMS Excel/CSV (ValveMTO.jsx's PMS
    Document stage). Passed straight through to
    apps.valve_mto.specification_lookup.load_specification_lookups,
    where it overlays (and takes priority over) legend-sourced mappings
    for the same code. None/empty means rating/pms_class/facing are
    filled from active legends only, unchanged from before this
    parameter existed.
    """
    if extraction_mode == 'local':
        from .local_valve_mto import extract_local_valve_mto
        return extract_local_valve_mto(
            pdf_path, user_id=user_id, scan_mode=scan_mode or 'thorough',
            on_progress=on_progress, on_partial=on_partial,
        )
    if extraction_mode != 'vision':
        return {'status': 'error', 'error': 'Choose local or vision extraction.',
                'rows': [], 'warnings': [], 'engine': 'none'}
    tile_rows, tile_cols, passes = _scan_mode_params(scan_mode)
    pages = _page_count(pdf_path)
    # Emit an immediate progress signal so the UI shows movement right after
    # the worker thread starts, even before any page is processed.
    if on_progress:
        try:
            on_progress(0, max(pages, 1), 0)
        except Exception:
            pass

    # Per-page heartbeat during text extraction (PyMuPDF) — searchable PDFs
    # can be 100+ pages and the user must see progress.
    text  = _extract_text(
        pdf_path,
        on_text_progress=(
            (lambda cur, tot: on_progress(cur, tot, 0))
            if on_progress else None
        ),
    )
    warnings: List[str] = []

    # ── Tesseract OCR fallback (thin text layer) ──────────────────────────
    # Scanned/image PDFs yield almost no embedded text; OCR the rendered
    # pages so the line-number harvester + regex meta parsing below still
    # have something to work with even when Vision is down.
    ocr_used = False
    detailed = (scan_mode or SCAN_MODE_DEFAULT).strip().lower() != SCAN_MODE_QUICK
    if len(text) < TEXT_SUFFICIENT_CHARS and not detailed:
        text, ocr_used = _maybe_ocr_text(
            pdf_path, text, warnings, 'Thin PDF text layer',
            on_progress=(
                (lambda cur, tot: on_progress(cur, tot, 0))
                if on_progress else None
            ),
        )
    text_meta = _extract_meta_from_text(text)

    try:
        provider, api_key = _resolve_vision_credential(vision_provider, vision_api_key, credential_mode)
    except (AICredentialUnavailable, ValueError) as exc:
        error = str(exc)
        logger.warning('[ValveMTO] AI configuration unavailable: %s', error)
        return {
            'status': 'error', 'error': error, 'rows': [], 'warnings': [error],
            'engine': 'none', 'page_count': pages, 'project_meta': text_meta,
        }
    resolved_vision_model = (vision_model or '').strip() or VISION_MODELS.get(provider, VISION_MODEL)

    if not api_key:
        error = 'An administrator must configure an available AI provider before valve extraction can run.'
        warnings.append(error)
        return {
            'status': 'error',
            'error': error,
            'engine': 'text+ocr' if ocr_used else ('text' if text_meta else 'none'),
            'page_count': pages,
            'rows': [],
            'project_meta': text_meta,
            'warnings': warnings,
        }

    try:
        if provider == 'claude':
            import anthropic  # noqa: F401
        else:
            from openai import OpenAI  # noqa: F401
    except Exception:
        error = f'Valve extraction cannot run: the {provider} client package is unavailable.'
        warnings.append(error)
        return {
            'status': 'error',
            'error': error,
            'engine': 'text+ocr' if ocr_used else ('text' if text_meta else 'none'),
            'page_count': pages,
            'rows': [],
            'project_meta': text_meta,
            'warnings': warnings,
        }

    # Render with a per-page heartbeat so the frontend's stall timer never
    # trips during the slow PDF→JPEG phase on slim-CPU containers.
    try:
        from apps.valve_mto.specification_lookup import load_specification_lookups, enrich_specifications
        from .local_valve_mto import _extract_class_from_line_number
        specifications = load_specification_lookups(user_id, pms_data=pms_data)
        regions = _render_native_regions(
            pdf_path, VISION_MAX_PAGES,
            on_render_progress=(lambda cur, tot: on_progress(cur, tot, 0)) if on_progress else None,
        ) if detailed else []
    except Exception as exc:
        logger.exception('[ValveMTO] Native rendering/project specification preparation failed')
        return {'status': 'error', 'error': f'Drawing evidence preparation failed: {exc}',
                'rows': [], 'warnings': [str(exc)], 'engine': 'none', 'page_count': pages}
    images = [] if detailed else _render_pages_b64(
        pdf_path,
        VISION_MAX_PAGES,
        VISION_IMAGE_DPI,
        on_render_progress=(
            (lambda cur, tot: on_progress(cur, tot, 0))
            if on_progress else None
        ),
    )
    if not images and not regions:
        warnings.append('vision skipped — no pages rendered')
        return {
            'status': 'empty',
            'engine': 'none',
            'page_count': pages,
            'rows': [],
            'project_meta': text_meta,
            'warnings': warnings,
        }

    # Build one Vision call per (page, tile, pass) — tile_rows x tile_cols
    # x passes calls for EACH page (see _scan_mode_params above; 'quick'
    # is tile_rows=tile_cols=passes=1, i.e. exactly today's original
    # one-call-per-page behaviour, unchanged). VISION_BATCH_SIZE (bundling
    # multiple PAGES into one call) doesn't compose with per-page tiling,
    # so each batch here is strictly one tile image — already this
    # module's practical default (VISION_BATCH_SIZE=1) even before this
    # feature existed.
    batches: List[Tuple[int, int, List[str]]] = []
    region_by_batch = {}
    page_titles = {}
    for region in regions:
        index = len(batches)
        region_by_batch[index] = region
        batches.append((index, region['page'], [region['image']]))
        page_titles[region['page']] = region.get('sheet_title', '')
    if regions:
        title_numbers = {r['pid_number'] for r in regions if r['pid_number']}
        if len(title_numbers) == 1:
            text_meta['doc_no'] = next(iter(title_numbers))
        warnings.append(
            f'Rendered {len(regions)} native PDF detail regions at '
            f'{min(r["dpi"] for r in regions):g}-{max(r["dpi"] for r in regions):g} DPI; '
            'original vector detail preserved before cropping. One AI pass per region.'
        )
    for page_idx, page_img in enumerate(images):
        tiles = _tile_image_b64(page_img, tile_rows, tile_cols)
        for tile_img in tiles:
            for _pass in range(passes):
                batches.append((len(batches), page_idx, [tile_img]))
    if images:
        page_titles = _drawing_titles(pdf_path, len(images))
    for index, title in page_titles.items():
        if not title:
            warnings.append(f'Page {index + 1}: DRG. TITLE could not be read; AREA requires review.')

    total_batches = len(batches)
    if on_progress:
        try:
            on_progress(0, total_batches, 0)
        except Exception:
            pass

    # Harvest line-number candidates from PDF text — passed to Vision as a hint
    # so the model does not miss line designations already present in the text layer.
    candidate_lines  = _harvest_line_numbers(text or '')
    candidate_block  = '\n'.join(f'  • {ln}' for ln in candidate_lines) if candidate_lines else '  (none detected in text layer — rely on drawing imagery)'
    if candidate_lines:
        logger.info('[ValveMTO] Harvested %d candidate line numbers from text layer', len(candidate_lines))

    legend_block = _build_legend_context(user_id) or '(none uploaded)'
    try:
        legend_images = _build_legend_reference_images(user_id)
    except Exception:
        logger.exception('[ValveMTO] Active legend pictures could not be read')
        error = 'Active legend pictures could not be read. Re-import the legend workbook before retrying.'
        return {'status': 'error', 'error': error, 'rows': [], 'warnings': [error],
                'engine': 'none', 'page_count': pages, 'project_meta': text_meta}
    warnings.append(
        f'Using {len(legend_images)} active valve/regulator legend pictures as classification references.'
        if legend_images else 'No active valve/regulator legend pictures available; symbol classification is unverified.'
    )
    if legend_block != '(none uploaded)':
        logger.info('[ValveMTO] Injecting legend context (%d chars) for user_id=%s', len(legend_block), user_id)

    logger.info(
        '[ValveMTO] Streaming vision → provider=%s model=%s pages=%d scan_mode=%s '
        'tiles=%dx%d passes=%d batches=%d (parallel=%d)',
        provider, resolved_vision_model, len({r['page'] for r in regions}) if regions else len(images), (scan_mode or SCAN_MODE_DEFAULT),
        tile_rows, tile_cols, 1 if regions else passes, total_batches, VISION_PARALLEL_BATCHES,
    )

    def _call_one_batch(batch_idx: int, page_idx: int, batch_imgs: List[str]):
        region = region_by_batch.get(batch_idx)
        region_text, region_ocr, ocr_warnings = _region_text(region) if region else (text or '', False, [])
        local_candidates = _harvest_line_numbers(region_text) if region else candidate_lines
        local_candidate_block = '\n'.join(local_candidates) or '(none verified in this region)'
        prompt = VISION_PROMPT_TEMPLATE.format(
            max_rows=MAX_ROWS,
            text_excerpt=region_text[:6000],
            page_range=f'{page_idx + 1}',
            candidate_line_numbers=local_candidate_block if region else candidate_block,
            legend_context=legend_block,
        )
        if region:
            prompt += (
                '\nThis image is a native high-detail drawing region, not a whole sheet. '
                'Return valve-body bounding boxes in normalized 0-1000 region coordinates. '
                'Use overlap for line connectivity and context, not to invent cropped valve bodies. '
                'Do not reconstruct labels from text fragments or proximity alone. '
                'Manual untagged valves may legitimately have no VALVE_TAG. '
                f'Verified title-block drawing number: {region["pid_number"] or "(not available)"}.'
            )
        if page_titles.get(page_idx):
            prompt += f'\nVerified DRG. TITLE for AREA on every row: {page_titles[page_idx]}.'
        try:
            data = _call_vision_batch_json(provider, api_key, prompt, batch_imgs, batch_idx=batch_idx,
                                           credential_mode=credential_mode, legend_images=legend_images,
                                           vision_model=resolved_vision_model)
        except Exception as exc:
            logger.warning('[ValveMTO] Batch %d (page %d) failed: %s', batch_idx, page_idx + 1, exc)
            fatal = _classify_openai_error(exc)
            return [], {}, [f'batch starting at page {page_idx + 1} failed: {exc}'], fatal, [], True, {'input_tokens': 0, 'output_tokens': 0}

        usage = data.get('_usage') or {'input_tokens': 0, 'output_tokens': 0}
        if region:
            data = _locate_region_rows(data, region)
        if page_titles.get(page_idx):
            data['sheet_title'] = page_titles[page_idx]
        rows, meta, warns, excluded = _parse_vision_payload(data)
        rows = [enrich_specifications(row, specifications) for row in rows]
        for row in rows:
            if not row.get('piping_class'):
                extracted = _extract_class_from_line_number(row.get('line_number', ''))
                if extracted:
                    row['piping_class'] = extracted
                    existing_remarks = row.get('remarks', '')
                    row['remarks'] = (
                        existing_remarks + ' (PMS class from line number)'
                    ).strip()
            # FIX 3 — if Vision left valve_tag empty but wrote the same
            # tag into its own free-text DESCRIPTION (seen happening in
            # practice — the model recognises the tag text but doesn't
            # always also populate the dedicated field), recover it from
            # there instead of leaving a tagged valve looking untagged.
            if not row.get('valve_tag'):
                description = str(row.get('description') or '')
                match = _VALVE_TAG_FROM_DESCRIPTION_RE.search(description.upper())
                if match:
                    row['valve_tag'] = match.group(0)
                    existing_remarks = row.get('remarks', '')
                    row['remarks'] = (existing_remarks + ' Tag from description').strip()
        warns.extend(ocr_warnings)
        if region_ocr:
            warns.append('Region-specific rotated OCR supplied text hints; values still require drawing verification.')
        return rows, meta, warns, None, excluded, False, usage

    all_rows: List[Dict[str, Any]] = []
    all_excluded: List[Dict[str, Any]] = []
    merged_meta: Dict[str, str] = dict(text_meta)  # seed with regex-derived meta
    completed = 0
    fatal_msgs: List[str] = []
    failed_batches = 0
    # Accumulated across all batches — this loop processes futures one at a
    # time (as_completed yields sequentially even though the underlying API
    # calls ran in parallel threads), so no lock is needed for these totals.
    total_input_tokens = 0
    total_output_tokens = 0

    parallelism = max(1, min(VISION_PARALLEL_BATCHES, len(batches)))
    with ThreadPoolExecutor(max_workers=parallelism) as pool:
        futures = {pool.submit(_call_one_batch, idx, page_idx, imgs): idx for idx, page_idx, imgs in batches}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                rows, meta, warns, fatal, excluded, failed, usage = fut.result()
            except Exception as exc:                                            # pragma: no cover
                rows, meta, warns, fatal, excluded, failed, usage = [], {}, [f'batch {idx} crashed: {exc}'], None, [], True, {'input_tokens': 0, 'output_tokens': 0}
            all_rows.extend(rows)
            all_excluded.extend(excluded)
            for k, v in meta.items():
                if v and not merged_meta.get(k):
                    merged_meta[k] = v
            warnings.extend(warns)
            if fatal and fatal not in fatal_msgs:
                fatal_msgs.append(fatal)
            if failed:
                failed_batches += 1
            total_input_tokens += usage.get('input_tokens', 0) or 0
            total_output_tokens += usage.get('output_tokens', 0) or 0
            completed += 1

            partial = _dedupe_and_renumber(list(all_rows))
            if on_progress:
                try:
                    on_progress(completed, total_batches, len(partial))
                except Exception:
                    pass
            if on_partial:
                try:
                    on_partial(partial, dict(merged_meta))
                except Exception:
                    pass

    final_rows = _dedupe_and_renumber(all_rows)
    final_rows = _match_tag_to_line_number(final_rows)

    # FIX (requested): rows that just had their line_number filled in by
    # _match_tag_to_line_number() may now be able to resolve a piping_class
    # that Vision/enrich_specifications left empty — re-run the positional
    # line-number parser on exactly those rows.
    for row in final_rows:
        if not row.get('piping_class') and row.get('line_number'):
            inferred_class = _extract_class_from_line_number(row.get('line_number', ''))
            if inferred_class:
                row['piping_class'] = inferred_class
                existing_remarks = row.get('remarks', '')
                row['remarks'] = (existing_remarks + ' (PMS class from line number)').strip()

    # ── Tesseract OCR fallback (Vision attempted but produced nothing — e.g.
    # every batch failed with a 429/quota error) ───────────────────────────
    # OCR can recover reference context, but cannot create physical valve rows.
    ocr_used = ocr_used or any(w.startswith('Region-specific rotated OCR') for w in warnings)
    vision_failed = not all_rows and (fatal_msgs or warnings)
    if vision_failed and not ocr_used:
        reason = fatal_msgs[0] if fatal_msgs else 'all Vision batches failed'
        text, ocr_used = _maybe_ocr_text(
            pdf_path, text, warnings, f'Vision unavailable ({reason})', force=True,
            on_progress=(
                (lambda cur, tot: on_progress(cur, tot, len(final_rows)))
                if on_progress else None
            ),
        )
        if ocr_used:
            # Retain recovered context for diagnostics, never as MTO rows.
            for k, v in _extract_meta_from_text(text).items():
                if v and not merged_meta.get(k):
                    merged_meta[k] = v
            candidate_lines = _harvest_line_numbers(text or '')
            if candidate_lines:
                logger.info('[ValveMTO] OCR fallback harvested %d candidate line numbers', len(candidate_lines))

    # Harvested text is a prompt hint, never evidence of a physical valve.
    error_msg: Optional[str] = None
    if not final_rows and failed_batches:
        # BUG FIX (real, confirmed in production): when the batch exception did
        # not match a known fatal pattern (e.g. model 404, timeout, connection
        # reset), fatal_msgs stayed empty and the job surfaced only the generic
        # text below — the actual provider error, already captured in the
        # per-batch warnings, was discarded, making production failures
        # undiagnosable. Fall back to the first batch failure detail so the
        # job record (and the UI) always carries the true cause.
        batch_failure = next(
            (w for w in warnings if 'failed:' in w or 'crashed:' in w), None,
        )
        error_msg = fatal_msgs[0] if fatal_msgs else (
            batch_failure
            or 'AI extraction failed and no verified valve records were produced. '
               'Review the extraction warnings and retry; existing register data must be preserved.'
        )
    if failed_batches:
        warnings.append(f'{failed_batches} of {total_batches} AI batches failed; any returned valve register is incomplete and requires review.')
    if final_rows:
        warnings.append(SURVEY_SCOPE_WARNING)
    # Surface excluded items through warnings too — the async job store only
    # forwards rows/meta/warnings, so exclusions stay visible in the job
    # snapshot until a dedicated field exists there.
    for ex in all_excluded:
        if ex.get('description'):
            warnings.append(f"Excluded: {ex['description']} — {ex.get('reason') or 'out of scope'}")
    # Engine label: 'text+ocr' marks that the Tesseract fallback contributed.
    # Only verified Vision rows count as a Vision contribution.
    if ocr_used:
        engine = 'text+ocr+vision' if all_rows else 'text+ocr'
    else:
        engine = 'vision' if final_rows else ('text' if text_meta else 'none')
    warnings = list(dict.fromkeys(warnings))

    # Estimated cost (list price, not billing) for the tokens actually
    # consumed across every batch — COST_PER_1K_TOKENS.get(model, {})
    # defaults to {} for an unlisted model, so .get('input'/'output', 0)
    # below makes an unknown model cost $0 rather than raising.
    model_costs = COST_PER_1K_TOKENS.get(resolved_vision_model, {})
    input_cost = (total_input_tokens / 1000) * model_costs.get('input', 0)
    output_cost = (total_output_tokens / 1000) * model_costs.get('output', 0)
    total_cost_usd = input_cost + output_cost
    total_cost_aed = total_cost_usd * AED_PER_USD

    return {
        'status': 'error' if error_msg else ('ok' if final_rows else 'empty'),
        'engine': engine,
        'page_count': pages,
        'rows': final_rows,
        'project_meta': merged_meta,
        'warnings': warnings,
        'error': error_msg,
        'harvested_line_numbers': candidate_lines,
        'excluded': all_excluded,
        'total_input_tokens': total_input_tokens,
        'total_output_tokens': total_output_tokens,
        'total_cost_usd': round(total_cost_usd, 4),
        'total_cost_aed': round(total_cost_aed, 4),
        'model': resolved_vision_model,
        'provider': provider,
    }
