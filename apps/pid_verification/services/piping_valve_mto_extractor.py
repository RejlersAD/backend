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
* Fast & cheap: text-first via PyMuPDF; Vision (GPT-4o) only runs when text
  yields fewer than `TEXT_SUFFICIENT_CHARS` characters AND `OPENAI_API_KEY`
  is configured. If OpenAI is unavailable the extractor still returns any
  rows that text-regex could find (graceful degradation).
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
from apps.core.ai_consumer_clients import lazy_provider_client, provider_api_key

import base64
import io
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─── Soft-coded constants (env-overridable) ─────────────────────────────────────
TEXT_SUFFICIENT_CHARS  = int(os.getenv('VALVE_MTO_TEXT_THRESHOLD', '1500'))
# Hard cap on pages we'll process — protects against runaway docs.
VISION_MAX_PAGES       = int(os.getenv('VALVE_MTO_VISION_MAX_PAGES', '50'))
# How many pages to bundle into a single OpenAI call (smaller = more accurate, larger = cheaper).
# Default lowered to 1 so each Vision call focuses on a single page — dramatically
# improves line-number recall on dense valve MTO drawings. Override via env if
# token-cost is more important than completeness.
VISION_BATCH_SIZE      = int(os.getenv('VALVE_MTO_VISION_BATCH_SIZE', '1'))
# How many batches to run in parallel.
VISION_PARALLEL_BATCHES = int(os.getenv('VALVE_MTO_VISION_PARALLEL', '4'))
VISION_IMAGE_DPI       = int(os.getenv('VALVE_MTO_VISION_DPI', '120'))
VISION_MAX_EDGE_PX     = int(os.getenv('VALVE_MTO_VISION_MAX_EDGE', '1600'))
VISION_MODEL           = os.getenv('VALVE_MTO_VISION_MODEL', 'gpt-4o-mini')
VISION_TEMPERATURE     = 0.0
VISION_TIMEOUT_SECS    = float(os.getenv('VALVE_MTO_VISION_TIMEOUT', '90'))
JPEG_QUALITY           = 80
MAX_ROWS               = int(os.getenv('VALVE_MTO_MAX_ROWS', '2000'))

# ─── BYOK (bring-your-own-key) providers ────────────────────────────────
# Added so a user can supply their own OpenAI OR Claude key instead of
# relying on the admin-managed one (see _resolve_vision_credential below).
# Model names match apps.instrument_io_workflow's own VISION_MODELS
# constant for consistency across the two BYOK-vision features in this
# codebase — same convention, independently maintained per that app's
# established isolation rule (no cross-app import of the constant itself).
SUPPORTED_PROVIDERS = ('openai', 'claude')
VISION_MODELS = {
    'openai': VISION_MODEL,
    'claude': os.getenv('VALVE_MTO_CLAUDE_MODEL', 'claude-sonnet-5'),
}
TEST_CONNECTION_MAX_TOKENS = 5

# Canonical row schema — must match frontend `valveMTO.config.js` VALVE_COLUMNS.
# NOTE: order does not affect extraction; new keys are additive. Frontend
# default-row init iterates this list and treats unknown keys as text.
ROW_KEYS = [
    'sl_no', 'area', 'type', 'pms_class', 'piping_class', 'rating', 'size_1', 'size_2',
    'bore', 'line_number', 'line_list', 'valve_tag', 'description', 'qty_island', 'qty_field', 'unit',
    'remarks',
]

# Soft-coded list (kept small — vision model picks the closest match).
# BUG FIX (real, confirmed root cause of "valves missing from ISLAND/FIELD
# tabs"): 'COMBINED' used to be a THIRD accepted value here, matching the
# prompt's own old schema hint — so a row Vision (correctly, per that old
# instruction) tagged "COMBINED" would show in the All Valves / COMBINED
# MTO tabs but in NEITHER ISLAND NOR FIELD (the frontend's two area-filtered
# tabs only match those two exact values — see ValveMTO.jsx's areaFilter).
# area is now a strict binary choice — see VISION_PROMPT_TEMPLATE's own
# MANDATORY AREA RULES section and _default_missing_area's fallback below.
VALID_AREAS       = ['ISLAND', 'Field']
NUMERIC_KEYS      = {'sl_no', 'qty_island', 'qty_field', 'unit'}

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
    # Generic hyphenated tag — broad fallback (3-6 segments, alphanumerics)
    r'\b\d{1,2}(?:/\d{1,2})?["]?[-\s]+[A-Z0-9]{1,6}[-\s]+[A-Z0-9]{1,8}[-\s]+[A-Z0-9]{2,10}(?:[-\s]+[A-Z0-9]{1,10}){0,3}\b',
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
    ('insufficient_quota',
     'OpenAI account has no remaining quota. Top up the API plan or rotate '
     'OPENAI_API_KEY, then retry the extraction.'),
    ('invalid_api_key',
     'OPENAI_API_KEY is invalid or revoked. Update the backend env var and '
     'restart the container.'),
    ('error code: 401',
     'OpenAI rejected the API key (401 Unauthorized). Check OPENAI_API_KEY.'),
    ('error code: 403',
     'OpenAI denied access to the Vision model (403). Verify the project '
     'has access to the configured VALVE_MTO_VISION_MODEL.'),
    ('billing_hard_limit_reached',
     'OpenAI hard billing limit reached. Raise the limit or top up credit.'),
]


def _classify_openai_error(exc: Exception) -> Optional[str]:
    """Return a friendly message if the exception matches a fatal pattern."""
    msg = str(exc).lower()
    for needle, friendly in OPENAI_FATAL_ERROR_PATTERNS:
        if needle.lower() in msg:
            return friendly
    return None

# Prompt is intentionally explicit — every column is described including
# accepted values so the model emits clean JSON.
VISION_PROMPT_TEMPLATE = """\
You are a senior piping engineer extracting a VALVE MATERIAL TAKE-OFF (Valve MTO)
from the attached drawing/datasheet pages (this batch covers pages {page_range} of a
larger document). Return ONLY a valid JSON object — no prose.

Schema:
{{
  "project_meta": {{
    "doc_no": "<COMPANY Document No., e.g. PJ6-EXD-GEN-TX0T-0004>",
    "doc_title": "<title, e.g. PIPING VALVES MTO>",
    "doc_desc": "<doc description>",
    "revision": "<numeric or alphanumeric revision>",
    "doc_date": "<YYYY-MM-DD if visible>",
    "project_name": "<project name if visible>"
  }},
  "rows": [
    {{
      "sl_no":       <integer>,
      "area":        "ISLAND" | "Field",
      "type":        "<BALL VALVE | GATE VALVE | GLOBE VALVE | CHECK VALVE | PLUG VALVE | BUTTERFLY VALVE | NEEDLE VALVE>",
      "pms_class":   "<piping material class — long descriptive name, e.g. 'CS A106 Gr B'>",
      "piping_class": "<piping spec class — short code only, e.g. 'A1A', 'B1B', 'CS1A'>",
      "rating":      "<e.g. CLASS 150 RF, CLASS 600 RTJ>",
      "size_1":      "<nominal bore in inches with double quotes, e.g. 2\\"",
      "size_2":      "<reduced size if any, else empty string>",
      "bore":        "FB" | "RB" | "",
      "line_number": "<piping line number / line tag / P&ID number the valve sits on, e.g. 6\"-P-12345-A1A-N>",
      "line_list":   "<line-list document ref or LL row id if visible (e.g. 'LL-001', 'PJ6-LL-003'), else empty string>",
      "valve_tag":   "<valve tag id>",
      "description": "<short service description>",
      "qty_island":  <integer total in ISLAND — see MANDATORY FIELDS below for the default when not explicitly shown>,
      "qty_field":   <integer total in FIELD — see MANDATORY FIELDS below for the default when not explicitly shown>,
      "unit":        <integer total quantity (units) for this valve row, 0 if none>,
      "remarks":     "<operational status codes if visible: LO, LC, CSO, CSC, TSO, FBLO, FBLC, NRV, NO, NC, FO, FC, FL, FI — comma-separated; otherwise free text>"
    }}
  ]
}}

MANDATORY AREA RULE — read this before extracting anything. "area" is
NEVER blank and NEVER any value other than exactly "ISLAND" or "Field"
(capitalisation matters) — there is no third option, do not write
"COMBINED", "N/A", "Both", or leave it empty. For EVERY valve you find:
  - Set "area" to "ISLAND" if the valve sits inside the island / process
    plot (the main process unit boundary — typically the dense, central
    cluster of equipment and piping on the drawing).
  - Set "area" to "Field" if the valve sits in the field / remote /
    off-plot piping (tie-in lines, utility stations, remote block
    valves, anything clearly outside the main process unit boundary).
  Determine which one by actually looking for these area indicators on
  the drawing, in this order of reliability:
    1. Battery limit markers/lines — a drawn boundary (often a dashed or
       heavy line, sometimes labelled "BL", "BATTERY LIMIT", or "ISBL"/
       "OSBL") separating the plant/island from the field. A valve on
       the ISBL (inside battery limit) side is ISLAND; a valve on the
       OSBL (outside battery limit) side is Field.
    2. Explicit "ISLAND" / "FIELD" text labels or title-block/area
       callouts printed near the valve or its enclosing zone.
    3. Area/zone boundary lines or a legend/key on the drawing that
       assigns named zones to Island vs Field.
  If NONE of these indicators are visible or the valve's position is
  genuinely ambiguous even after checking all three, DEFAULT TO
  "ISLAND" — never leave "area" blank and never invent a third value.
  This default exists so every row is still usable in the ISLAND/FIELD
  breakdown even on a drawing with no explicit area markings; it is a
  deliberate, documented fallback, not a guess to avoid.

MANDATORY FIELDS — "type", "size_1", and "line_number" must never be
left as an empty string for a valve you can actually see and tag:
  - "type": identify the valve body shape (see the FUNC/type list in the
    schema above) — every valve symbol has a distinguishable body shape;
    look again at the symbol before defaulting to something generic.
  - "size_1": the nominal bore is virtually always printed beside the
    valve tag or on the line it sits on — read it from there if it
    isn't directly on the valve.
  - "line_number": read the line this valve sits on (see the LINE
    NUMBER rule below) — only leave this empty if the valve genuinely
    has no line callout anywhere near it after checking the whole page.
  - "qty_island" / "qty_field": when a quantity is NOT explicitly
    written next to the valve (most single valves on a P&ID represent
    exactly one physical valve), default the quantity for whichever
    field matches this row's own "area" to 1 (qty_island=1 if
    area="ISLAND", qty_field=1 if area="Field") rather than 0 — a valve
    you found and tagged is a real, physical valve, so its default
    count is 1, not "none". Only use an explicit larger/smaller number
    when the drawing actually states one (e.g. a note "(TYP. OF 4)").

CUSTOMER-CONFIRMED FORMAT REFERENCE — real, project-confirmed tag/line
conventions for this customer. Treat these as ground truth alongside (not
instead of) the general line-number formats in the "line_number" rule
below — use whichever actually matches what's printed on the drawing.

  PIPING LINE FORMAT: FF-DD-111XXX-XXXX-X
    FF     = Fluid Code (see FLUID CODES below — use these to VALIDATE a
             line number: if the leading segment matches one of these
             known codes, that confirms it is a real line number, not
             noise/an unrelated tag)
    DD     = Line Diameter (inches)
    111XXX = Project Identifier + Line Number
    XXXX   = Piping Material Code
    X      = Insulation Type (see INSULATION CODES below)

  FLUID CODES (FF segment above — customer-confirmed, exhaustive list;
  a line number's leading 1-3 letter code should match one of these):
<<<FLUID_CODES_TABLE>>>
  When reading a "line_number", check its leading letters against this
  list — a match is strong confirmation the reading is correct; if the
  leading letters DON'T match any of these AND don't match any of the
  general line-number formats in the "line_number" rule below either,
  look again before accepting the reading (it may be a misread digit vs.
  letter, or a genuinely different kind of tag, not a line at all).

  VALVE IDENTIFICATION: V111XXXX
    V    = Valve
    111  = Project Indicator (USGIF)
    XXXX = Sequential Number (0001-9999)

  DRAWING NUMBER FORMAT: AD111-XXX-D-XXXXX (the drawing's own title-block
  number, not a line or valve tag — use it to confirm which area/platform
  the whole sheet belongs to, which in turn applies to every valve and
  line read off that sheet):
    AD111 = Project Indicator (USGIF)
    XXX   = Area Code (see AREA CODES below — either the 026-446 span or
            the 501-550 span)
    D     = Discipline Code (Process and P&IDs)
    XXXXX = Drawing Number (10000-19999)

  SPECIAL VALVE CODES — format XXX-CODE-XXXXX, where XXX is this
  project's AREA CODE and XXXXX is a sequence number whose valid range
  depends on the code itself (see each one below). XXX is valid when it
  is one of the specific named codes in the AREA CODES list below (the
  026-446 span and the 501-550 span both contain named codes — but not
  every number in either span is a real code, only the ones actually
  listed there). When a
  valve tag contains one of these codes, set "type" from the code itself
  (this overrides a visual best-guess from the FUNC/type list in the
  schema above — the tag's own code is more reliable than the drawn body
  shape alone). Use the area-code and sequence-number ranges below to
  VALIDATE a tag reading — a tag whose XXX or XXXXX falls clearly outside
  these ranges is worth a second look before accepting it as correct:
    PSV = Pressure Safety Valve   — XXX-PSV-XXXXX, sequence 2002-2201
                                     (e.g. 501-PSV-2001)
    SDV = Shutdown Valve          — XXX-SDV-XXXXX, sequence 0001-9999
    BDV = Blowdown Valve          — XXX-BDV-XXXXX, sequence 0001-9999
    SV  = Solenoid Valve          — XXX-SV-XXXXX,  sequence 0001-9999
    MOV = Motor Operated Valve    — XXX-MOV-XXXXX, sequence from 1001

  INSULATION CODES (the trailing single letter in the piping line format
  above — read it into context, it is not one of the existing schema
  columns on its own):
    A = Acoustic
    C = Cold Conservation
    F = Fire Proofing
    H = Heat Conservation
    P = Personnel Protection
    T = Heat Tracing
    E = Electrical Heat Tracing

  AREA CODES (customer-confirmed, exhaustive list, two spans — 026-446
  and 501-550) — this is the AREA segment of the Offshore line-number
  format in the "line_number" rule below (e.g. "604-LFG-3-AC2GA0-2012" —
  "604" is the area code; this project's actual area codes are the
  3-digit numbers below, not 604 itself, which was only an illustrative
  example), and also the XXX segment of the DRAWING NUMBER FORMAT above:
    026=GENERAL AND MISCELLANEOUS
    176=CAP GAS WELLS PLATFORM (US-58)
    177=GAS TREATMENT PLATFORM
    178=ABK TIE-IN PLATFORM
    179=COLLECTOR/SEPARATOR PLATFORM
    180=ORIGINAL CENTRAL COLLECTOR PLATFORM
    181=NORTHERN RISER PLATFORM
    182=EASTERN RISER PLATFORM
    183=UMM SHAIF EXISTING ACCOMMODATION PLATFORM
    184=POWER GENERATION PLATFORM
    185=WATER INJECTION PLATFORM (5 MODULES)
    186=WATER INJECTION PLATFORM (2 MODULES)
    187=BRIDGE B1, 188=BRIDGE B2, 189=BRIDGE B3, 190=BRIDGE B4
    191=BRIDGE B5, 192=BRIDGE B6, 193=BRIDGE B7, 194=BRIDGE B8
    195=BRIDGE B9, 196=BRIDGE B10, 197=BRIDGE B11
    350=EXTENSION TO GAS TREATMENT PLATFORM
    351=FLARES 1, 2 AND TA
    352=UMM SHAIF ADDITIONAL ACCOMMODATION
    359=BRIDGE SUPPORT TOWER S1
    360=BRIDGE SUPPORT TOWER S2
    361=BRIDGE SUPPORT TOWER S3
    391=CRESTAL GAS INJECTION TOWER US 272
    392=CRESTAL GAS INJECTION TOWER US 290
    414=NEW 36" MOL (SUBMARINE)
    415=NEW 36" MOL RISER PLATFORM
    418=BRIDGE SUPPORT TOWER S5
    419=BRIDGE SUPPORT TOWER S4
    420=BRIDGE B15, 421=BRIDGE B14, 423=BRIDGE B13
    422=TAWEELAH ALPHA PLATFORM
    427=NEW GAS TREATMENT PLATFORM
    428=THAMAMA PILOT GAS INJECTION US 213
    441=ARAB D GAS INJECTION TOWER US 251
    446=ARAB D GAS INJECTION TOWER US 250
    501=GENERAL & MISCELLANEOUS
    502=COLLECTOR SEPARATOR PLATFORM-1 (CSP-1)
    503=UMM SHAIF WATER DISPOSAL UNIT (UWDT)
    504=FLARE TOWER-4 (FT-4), 505=FLARE TOWER-5 (FT-5)
    510=BRIDGE B16, 511=BRIDGE B17, 512=BRIDGE B18, 513=BRIDGE B19
    514=BRIDGE B20, 515=BRIDGE B21, 516=BRIDGE B22, 517=BRIDGE B23
    520=BRIDGE SUPPORT TOWER S6, 521=BRIDGE SUPPORT TOWER S7
    522=BRIDGE SUPPORT TOWER S8, 523=BRIDGE SUPPORT TOWER S9
    524=BRIDGE SUPPORT TOWER S10
    531=COMPRESSION PLATFORM-1 (CP-1)
    532=UMM SHAIF ACCOMMODATION PLATFORM (UAP)
    533=FLARE TOWER-6 (FT-6)
    538=BRIDGE B55, 539=BRIDGE B56, 540=BRIDGE B57
    547=BRIDGE SUPPORT TOWER S55, 548=BRIDGE SUPPORT TOWER S56
  Use these area codes for three things:
  1. VALIDATE line numbers — when a line number follows the Offshore
     AREA-FLUID-SIZE-PIPECLASS-SEQ format, confirm its leading area
     segment matches one of these known codes; that confirms the whole
     reading is a genuine line number, the same way the FLUID CODES list
     above corroborates the FLUID segment.
  2. IDENTIFY the specific platform/structure a valve physically sits
     on — when you can read an area code from a nearby line number or
     from a title-block/drawing-reference callout, match it to its name
     above (e.g. "179" = "COLLECTOR/SEPARATOR PLATFORM").
  3. INFORM (not replace) the "area" field decision in the MANDATORY
     AREA RULE above — this list does NOT itself state which named
     platforms count as ISLAND vs Field (that mapping was not provided
     and must not be guessed/invented); instead, once you know WHICH
     named platform a valve is on via its area code, apply the SAME
     battery-limit/ISBL-OSBL reasoning from the MANDATORY AREA RULE to
     THAT platform — a bridge, riser platform, or tie-in platform
     physically separate from the main process platform is a real,
     visible signal the valve likely belongs on the Field side, while a
     valve on the main/central platform (e.g. a collector or treatment
     platform) likely belongs on the ISLAND side — but always let what's
     actually drawn (battery-limit lines, explicit labels) decide over
     an assumption based on the platform name alone.

PROJECT-SPECIFIC EXTRACTION RULES — read before extracting anything:
1. SIZE CUTOFF: only extract valves 2" NB (nominal bore) and above.
   Read "size_1" first, then decide — a valve smaller than 2" (e.g. 3/4",
   1", 1-1/2") must NOT be reported as a row at all, even if its tag,
   type, and everything else about it is perfectly legible. This is a
   real project scope limit, not a data-quality judgement call — do not
   report a small valve with a note about its size instead of just
   omitting it; omit it entirely. Everything else in this prompt about
   being exhaustive ("extract EVERY valve row") means every valve AT OR
   ABOVE this 2" cutoff, not literally every valve symbol on the page.
2. LEGEND STANDARD: this project's legend/symbol conventions follow the
   CSP-1 standard — when a symbol or abbreviation is ambiguous, prefer
   the CSP-1 reading over a generic/unrelated convention.
3. TAG NUMBERING STANDARD: tag numbering on this project follows
   AO-ENG-D-PRO-001 — use it as the authoritative reference for how tags
   are structured (alongside the CUSTOMER-CONFIRMED FORMAT REFERENCE
   above, which documents that standard's actual formats) when a tag's
   structure is otherwise unclear.

Rules:
- For "remarks": this column is CRITICAL. Inspect EVERY valve symbol and its adjacent legend annotation on the drawing very carefully — valves are often labelled with a 2-4 letter operational-status code in small text right next to the valve body (sometimes above, below, or attached by a leader line).
  Always emit these codes when visible (comma-separated, in this exact spelling): LO (Locked Open), LC (Locked Closed), CSO (Car-Sealed Open), CSC (Car-Sealed Closed), TSO (Tight Shut-Off), FBLO (Full-Bore Locked Open), FBLC (Full-Bore Locked Closed), NRV (Non-Return Valve), NO (Normally Open), NC (Normally Closed), FO (Fail Open), FC (Fail Closed), FL (Fail Last), FI (Fail Indeterminate).
  These codes may ALSO appear inside the valve tag itself (e.g. ``BV-LO-1234``, ``GV-08-FBLC``) — include them either way. If the legend uses the spelled-out phrase (e.g. "Locked Open"), emit the matching short code instead. Only fall back to free text when NONE of these codes apply.
- For "line_number" (a.k.a. P&ID NUMBER): this column is ALSO CRITICAL. Extract the COMPLETE line designation for every valve row. Line numbers follow one of these standard formats — recognise ALL of them, do not skip lines simply because they look unusual:
    * Onshore         2"-D-6152-033842-X-N            (SIZE-FLUID-SEQ-PIPESPEC-DEPT-INSUL)
    * Industrial      2"-2600-FL-352-32070R-E         (SIZE-UNIT-SERVICE-SEQ-PIPECLASS-END)
    * Offshore        604-LFG-3-AC2GA0-2012           (AREA-FLUID-SIZE-PIPECLASS-SEQ — see AREA CODES above for this project's real area codes)
    * ADNOC compact   6"-CD-AC3N-8256                 (SIZE-FLUID-CLASS-SEQ)
    * General         4"-41-SWR-64313-A2AU16-V        (auto-detect any hyphenated tag with size + alphanumerics)
    * Borouge/Linde   1"-63-UA-149472-A1AU01-V        (SIZE-AREA-SERVICE-SEQ-PIPECLASS-END)
  When the same valve sits on more than one line, emit a separate row per line. Never leave line_number empty if the line designation is visible anywhere on the drawing or in the text excerpt below.
- For "line_list": if the drawing references a line-list document number (e.g. 'PJ6-LL-003', 'LL-001'), emit it verbatim; otherwise leave empty — do not invent.
- Output only valid JSON; do not wrap in markdown fences.
- Extract EVERY valve row 2" NB and above visible in the attached pages — do not summarise or skip (see PROJECT-SPECIFIC EXTRACTION RULES above for the size cutoff itself).
- Use empty strings for unknown text fields and 0 for unknown numeric fields.
- Do not invent valve tags or sizes — leave empty if uncertain.
- Renumber sl_no starting from 1 within this batch (the server merges batches).
- Maximum {max_rows} rows per batch.

Candidate line numbers detected in the PDF text layer (validated against standard formats — every one of these IS a real line; cross-check the drawing and assign each to the correct valve row when applicable):
{candidate_line_numbers}

Project Legend Sheet data (from this project's own uploaded legend — use
this as the AUTHORITATIVE source for valve type codes, PMS/piping class
codes, and valve symbol meanings whenever it conflicts with general
ISA/piping convention; if this section says "(none uploaded)", fall back
to your own general engineering knowledge exactly as before):
{legend_context}

Embedded text excerpt (use as ground truth where it conflicts with the image):
---
{text_excerpt}
---
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


def _coerce_row(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Force a raw dict into the canonical row schema."""
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

    # Area normalisation (case-insensitive against VALID_AREAS) + mandatory
    # fallback. BUG FIX: area used to stay blank (or an unrecognised value
    # like the old prompt's "COMBINED") whenever it didn't cleanly match —
    # a blank/unrecognised area makes a row invisible in BOTH the ISLAND
    # and FIELD tabs (see ValveMTO.jsx's areaFilter), which was the real,
    # confirmed root cause of "valves missing from ISLAND/FIELD tabs".
    # Per the prompt's own MANDATORY AREA RULE, "ISLAND" is now the
    # documented default for a genuinely ambiguous/unmarked valve — this
    # is the code-level safety net for whenever the model doesn't comply
    # with that instruction anyway (a prompt can never fully guarantee
    # compliance).
    normalized_area = ''
    for a in VALID_AREAS:
        if row['area'].lower() == a.lower():
            normalized_area = a
            break
    row['area'] = normalized_area or 'ISLAND'

    # Quantity default: a row Vision actually found and tagged (has a
    # type or a valve_tag — i.e. a real valve, not a synthetic line-only
    # row from the line-number recovery pass, which has neither) is one
    # real, physical valve by default when no explicit count is shown —
    # 0 would make a genuinely found valve silently disappear from the
    # Pivot Summary / quantity totals. Only the ONE quantity field
    # matching this row's own (now always-set) area gets defaulted; the
    # other area's quantity stays 0, since this single row only
    # represents itself.
    if row['type'] or row['valve_tag']:
        if row['qty_island'] == 0 and row['qty_field'] == 0:
            if row['area'] == 'ISLAND':
                row['qty_island'] = 1
            else:
                row['qty_field'] = 1

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
LEGEND_CONTEXT_MAX_CHARS = int(os.getenv('VALVE_MTO_LEGEND_CONTEXT_MAX_CHARS', '4000'))

# Essential — injected whenever the user has an active legend for these;
# each maps 1:1 to one of Valve MTO's own extracted fields (type,
# pms_class/piping_class, line_number respectively).
LEGEND_SECTIONS_ESSENTIAL = ('valve', 'piping', 'line_list')
# Optional — only relevant to the 'area' (ISLAND/Field) field, via
# battery-limit/scope-boundary markers; injected only when present.
LEGEND_SECTIONS_OPTIONAL = ('scope_symbols', 'limit_line')

_LEGEND_SECTION_PROMPT_LABELS = {
    'valve': 'Valve Types',
    'piping': 'Piping Classes',
    'line_list': 'Line Format',
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
    """Formats this user's own ACTIVE legend sheets (apps.pid_checker_v2,
    sections in LEGEND_SECTIONS_ESSENTIAL/_OPTIONAL) into the
    "LEGEND REFERENCE:" block injected into the Vision prompt.

    Returns '' (never raises) when the user has no active legend for any
    of these sections — Vision then falls back to its own general
    knowledge exactly as it did before this feature existed; a missing
    legend must never block or degrade a Valve MTO extraction.
    """
    if not user_id:
        return ''
    try:
        from apps.pid_checker_v2.models import PidCheckerV2LegendSheet
    except Exception as exc:  # noqa: BLE001
        logger.warning('[ValveMTO] pid_checker_v2 legend model unavailable: %s', exc)
        return ''

    try:
        all_sections = LEGEND_SECTIONS_ESSENTIAL + LEGEND_SECTIONS_OPTIONAL
        by_section = {
            s.section: s for s in PidCheckerV2LegendSheet.objects.filter(
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


def _resolve_vision_credential(vision_provider: Optional[str], vision_api_key: Optional[str]) -> Tuple[str, str]:
    """Returns (provider, api_key), or (provider, '') if none is available.

    BYOK-first: a user-supplied key always wins for whichever provider
    they picked — never silently substituted for the admin key. Only
    when NO user key is given does this fall back to the admin-managed
    OpenAI credential (apps.core.ai_consumer_clients), matching the
    extractor's pre-BYOK default behaviour exactly (so an existing
    deployment with only an admin OpenAI key configured keeps working
    unchanged). There is no admin-managed fallback for Claude — a user
    who wants Claude must supply their own key.
    """
    provider = (vision_provider or 'openai').strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        provider = 'openai'
    api_key = (vision_api_key or '').strip()
    if api_key:
        return provider, api_key
    if provider == 'openai':
        # BUG FIX (real, confirmed): the admin-managed credential registry
        # (apps.core.ai_credentials) can raise AICredentialUnavailable not
        # only when no key is configured (that case returns '' cleanly)
        # but also when its OWN backing table/registry itself isn't
        # available (e.g. a DatabaseError resolving AIProviderConfiguration
        # — seen live in this exact environment) — an uncaught raise here
        # would crash the whole background extraction thread instead of
        # falling through to "no key, show the BYOK prompt", which is
        # already this function's designed behaviour for the plain
        # "nothing configured" case. Treat both the same way.
        try:
            from apps.core.ai_credentials import AICredentialUnavailable
            return provider, provider_api_key('openai', fallback=(lambda: (os.getenv('OPENAI_API_KEY')))) or ''
        except AICredentialUnavailable:
            return provider, os.getenv('OPENAI_API_KEY') or ''
    return provider, ''


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


def _call_vision_batch_json(provider: str, api_key: str, prompt: str, batch_imgs: List[str]) -> Dict[str, Any]:
    """One Vision call for one batch of page images, for EITHER provider —
    returns the parsed {'rows': [...], 'project_meta': {...}} dict (or
    raises on any failure, which callers already handle per-batch).
    Shared by both the non-streaming and streaming extraction paths so
    Claude support only had to be added in one place.
    """
    if provider == 'claude':
        import anthropic
        client = lazy_provider_client(
            'anthropic', anthropic.Anthropic, api_key=lambda: (api_key), timeout=VISION_TIMEOUT_SECS,
        )
        content: List[Dict[str, Any]] = [{'type': 'text', 'text': prompt}]
        for b64 in batch_imgs:
            content.append({
                'type': 'image',
                'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': b64},
            })
        resp = client.messages.create(
            model=VISION_MODELS['claude'],
            max_tokens=8192,
            # Same fix as apps.instrument_io_workflow's own Claude call —
            # claude-sonnet-5 emits extended 'thinking' content blocks by
            # default, which can consume the whole token budget before
            # any of the actual JSON answer is written. This task needs a
            # direct read-and-list answer, not multi-step reasoning.
            thinking={'type': 'disabled'},
            system='You are an expert piping engineer extracting a Valve Material Take-Off (MTO) table from a P&ID drawing. Return ONLY a JSON object — no prose, no markdown fences.',
            messages=[{'role': 'user', 'content': content}],
        )
        parts = [b.text for b in resp.content if getattr(b, 'type', None) == 'text']
        raw = ''.join(parts) or '{}'
        return _extract_json_object(raw)

    # openai (default)
    from openai import OpenAI
    client = lazy_provider_client('openai', OpenAI, api_key=lambda: (api_key), timeout=VISION_TIMEOUT_SECS)
    content = [{'type': 'text', 'text': prompt}]
    for b64 in batch_imgs:
        content.append({'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{b64}'}})
    resp = client.chat.completions.create(
        model=VISION_MODELS['openai'],
        temperature=VISION_TEMPERATURE,
        response_format={'type': 'json_object'},
        messages=[{'role': 'user', 'content': content}],
    )
    raw = resp.choices[0].message.content or '{}'
    return json.loads(raw)


def test_api_key(provider: str, api_key: str) -> Tuple[bool, str]:
    """One minimal call to confirm a BYOK key actually works — tests
    EXACTLY the key passed in, never an admin-managed substitute (unlike
    a quirk in apps.instrument_io_workflow's own test_api_key, which can
    silently test the admin key instead when one is configured — that
    would defeat the purpose of a "Test Connection" button for a key the
    user just typed, so this does not replicate that behaviour)."""
    provider = (provider or '').strip().lower()
    api_key = (api_key or '').strip()
    if provider not in SUPPORTED_PROVIDERS:
        return False, f"Unsupported provider '{provider}'."
    if not api_key:
        return False, 'API key is required.'
    try:
        if provider == 'claude':
            import anthropic
            client = lazy_provider_client(
                'anthropic', anthropic.Anthropic, api_key=lambda: (api_key), timeout=VISION_TIMEOUT_SECS,
            )
            client.messages.create(
                model=VISION_MODELS['claude'],
                max_tokens=TEST_CONNECTION_MAX_TOKENS,
                thinking={'type': 'disabled'},
                messages=[{'role': 'user', 'content': 'Hi'}],
            )
        else:
            from openai import OpenAI
            client = lazy_provider_client('openai', OpenAI, api_key=lambda: (api_key), timeout=VISION_TIMEOUT_SECS)
            client.chat.completions.create(
                model=VISION_MODELS['openai'],
                max_tokens=TEST_CONNECTION_MAX_TOKENS,
                messages=[{'role': 'user', 'content': 'Hi'}],
            )
        return True, 'API key is valid and working!'
    except Exception as exc:  # noqa: BLE001
        http_status = getattr(exc, 'status_code', None) or getattr(exc, 'http_status', None)
        if http_status in (401, 403):
            return False, 'Invalid API key. Please check and try again.'
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
            return [], {}, [f'batch starting at page {batch_idx + 1} failed: {exc}']

        rows_raw = data.get('rows') or []
        meta_raw = data.get('project_meta') or {}
        rows: List[Dict[str, Any]] = []
        if isinstance(rows_raw, list):
            for r in rows_raw:
                if not isinstance(r, dict):
                    continue
                row = _coerce_row(r)
                if row['valve_tag'] or row['description'] or row['type']:
                    rows.append(row)
        return rows, _coerce_meta(meta_raw), []

    all_rows: List[Dict[str, Any]] = []
    merged_meta: Dict[str, str] = {}
    warnings: List[str] = []

    parallelism = max(1, min(VISION_PARALLEL_BATCHES, len(batches)))
    with ThreadPoolExecutor(max_workers=parallelism) as pool:
        futures = {pool.submit(_call_one_batch, idx, imgs): idx for idx, imgs in batches}
        # Collect results in submission order so row order roughly tracks page order.
        results_by_idx: Dict[int, Tuple[List[Dict[str, Any]], Dict[str, str], List[str]]] = {}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                results_by_idx[idx] = fut.result()
            except Exception as exc:                                            # pragma: no cover
                results_by_idx[idx] = ([], {}, [f'batch {idx} crashed: {exc}'])

    for idx in sorted(results_by_idx):
        rows, meta, warns = results_by_idx[idx]
        all_rows.extend(rows)
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

    return {'rows': deduped, 'project_meta': merged_meta, 'warnings': warnings}


# ─── Public API ─────────────────────────────────────────────────────────
def extract_valve_mto(pdf_path: str) -> Dict[str, Any]:
    pages = _page_count(pdf_path)
    text  = _extract_text(pdf_path)
    text_meta = _extract_meta_from_text(text)
    warnings: List[str] = []

    use_vision = len(text) < TEXT_SUFFICIENT_CHARS or True  # always on for now — drawings rarely have enough text
    vision_result: Dict[str, Any] = {'rows': [], 'project_meta': {}, 'warnings': []}
    if use_vision:
        vision_result = _extract_via_vision(pdf_path, text)
        warnings.extend(vision_result.get('warnings') or [])

    rows  = vision_result['rows']
    meta  = {**text_meta, **{k: v for k, v in vision_result['project_meta'].items() if v}}

    engine = 'vision' if rows and not text_meta else (
        'text+vision' if rows and text_meta else (
            'text' if text_meta else 'none'
        )
    )

    return {
        'status': 'ok' if rows or meta else 'empty',
        'engine': engine,
        'page_count': pages,
        'rows': rows,
        'project_meta': meta,
        'warnings': warnings,
    }


# ─── Streaming public API (used by the async job runner) ────────────────
def _dedupe_and_renumber(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for r in rows:
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
        out.append(r)
    out = out[:MAX_ROWS]
    for i, r in enumerate(out):
        r['sl_no'] = i + 1
    return out


def extract_valve_mto_streaming(
    pdf_path: str,
    on_progress=None,
    on_partial=None,
    vision_provider: Optional[str] = None,
    vision_api_key: Optional[str] = None,
    user_id=None,
) -> Dict[str, Any]:
    """
    Same logic as `extract_valve_mto` but emits incremental progress/results
    through callbacks so a long-running async job can be polled.

    Callbacks
    ---------
    * `on_progress(current_batch:int, total_batches:int, rows_so_far:int)`
    * `on_partial(rows_so_far:list, project_meta_so_far:dict)`

    vision_provider/vision_api_key: BYOK — see _resolve_vision_credential's
    own docstring for the exact precedence (user key always wins for
    whichever provider they picked; falls back to the admin-managed
    OpenAI credential only when no user key was given at all; there is
    no admin fallback for Claude). Both default to None so every existing
    caller (this function had none of these params before) keeps working
    unchanged — None resolves to the same admin-OpenAI-or-nothing
    behaviour this function always had.

    user_id: whoever started this extraction — used by
    _build_legend_context to look up their own uploaded Legend Sheet(s)
    and ground the Vision prompt in real project data. None (default,
    same as every existing caller before this feature existed) means no
    legend context, same as before.
    """
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
    text_meta = _extract_meta_from_text(text)

    provider, api_key = _resolve_vision_credential(vision_provider, vision_api_key)
    warnings: List[str] = []

    if not api_key:
        # Per the BYOK requirement: a clear, actionable message — not the
        # old internal-sounding 'no OPENAI_API_KEY' label — regardless of
        # WHICH provider/key path was missing (no user key AND no admin
        # OpenAI fallback, or Claude requested with no user key at all).
        warnings.append('Enter your OpenAI/Claude API key to extract valves from PDF.')
        return {
            'status': 'ok' if text_meta else 'empty',
            'engine': 'text' if text_meta else 'none',
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
        warnings.append(f'vision skipped — {provider} package unavailable')
        return {
            'status': 'ok' if text_meta else 'empty',
            'engine': 'text' if text_meta else 'none',
            'page_count': pages,
            'rows': [],
            'project_meta': text_meta,
            'warnings': warnings,
        }

    # Render with a per-page heartbeat so the frontend's stall timer never
    # trips during the slow PDF→JPEG phase on slim-CPU containers.
    images = _render_pages_b64(
        pdf_path,
        VISION_MAX_PAGES,
        VISION_IMAGE_DPI,
        on_render_progress=(
            (lambda cur, tot: on_progress(cur, tot, 0))
            if on_progress else None
        ),
    )
    if not images:
        warnings.append('vision skipped — no pages rendered')
        return {
            'status': 'empty',
            'engine': 'none',
            'page_count': pages,
            'rows': [],
            'project_meta': text_meta,
            'warnings': warnings,
        }

    batches: List[Tuple[int, List[str]]] = []
    for i in range(0, len(images), VISION_BATCH_SIZE):
        batches.append((i, images[i:i + VISION_BATCH_SIZE]))

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
    if legend_block != '(none uploaded)':
        logger.info('[ValveMTO] Injecting legend context (%d chars) for user_id=%s', len(legend_block), user_id)

    logger.info(
        '[ValveMTO] Streaming vision → provider=%s model=%s pages=%d batches=%d (size=%d, parallel=%d)',
        provider, VISION_MODELS[provider], len(images), total_batches, VISION_BATCH_SIZE,
        VISION_PARALLEL_BATCHES,
    )

    def _call_one_batch(batch_idx: int, batch_imgs: List[str]):
        prompt = VISION_PROMPT_TEMPLATE.format(
            max_rows=MAX_ROWS,
            text_excerpt=(text or '')[:6000],
            page_range=f'{batch_idx + 1}–{batch_idx + len(batch_imgs)}',
            candidate_line_numbers=candidate_block,
            legend_context=legend_block,
        )
        try:
            data = _call_vision_batch_json(provider, api_key, prompt, batch_imgs)
        except Exception as exc:
            logger.warning('[ValveMTO] Batch %d failed: %s', batch_idx, exc)
            # Fatal-error pattern matching (quota/auth/billing) is
            # currently OpenAI-specific (see OPENAI_FATAL_ERROR_PATTERNS)
            # — a Claude error just won't match any pattern and falls
            # through to a normal (non-fatal) per-batch failure instead
            # of an early, all-batches abort. The failure itself, and its
            # warning message, still surface correctly either way.
            fatal = _classify_openai_error(exc)
            return [], {}, [f'batch starting at page {batch_idx + 1} failed: {exc}'], fatal

        rows_raw = data.get('rows') or []
        meta_raw = data.get('project_meta') or {}
        rows: List[Dict[str, Any]] = []
        if isinstance(rows_raw, list):
            for r in rows_raw:
                if not isinstance(r, dict):
                    continue
                row = _coerce_row(r)
                if row['valve_tag'] or row['description'] or row['type']:
                    rows.append(row)
        return rows, _coerce_meta(meta_raw), [], None

    all_rows: List[Dict[str, Any]] = []
    merged_meta: Dict[str, str] = dict(text_meta)  # seed with regex-derived meta
    completed = 0
    fatal_msgs: List[str] = []

    parallelism = max(1, min(VISION_PARALLEL_BATCHES, len(batches)))
    with ThreadPoolExecutor(max_workers=parallelism) as pool:
        futures = {pool.submit(_call_one_batch, idx, imgs): idx for idx, imgs in batches}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                rows, meta, warns, fatal = fut.result()
            except Exception as exc:                                            # pragma: no cover
                rows, meta, warns, fatal = [], {}, [f'batch {idx} crashed: {exc}'], None
            all_rows.extend(rows)
            for k, v in meta.items():
                if v and not merged_meta.get(k):
                    merged_meta[k] = v
            warnings.extend(warns)
            if fatal and fatal not in fatal_msgs:
                fatal_msgs.append(fatal)
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

    # ── Soft-coded line-number recovery pass ───────────────────────────────
    # Any line number harvested from the PDF text layer that Vision DID NOT
    # surface anywhere in its output rows is appended as a "line-only" row
    # (sl_no, line_number, line_list populated; other fields empty). This
    # guarantees the user sees every detected line on the PMS table — the
    # same completeness guarantee that the dedicated Line List page offers.
    # Disabled when VALVE_MTO_APPEND_MISSING_LINES=0.
    if candidate_lines and os.getenv('VALVE_MTO_APPEND_MISSING_LINES', '1') == '1':
        _norm = lambda s: re.sub(r'\s+', '', str(s or '')).upper()
        seen_lines = {_norm(r.get('line_number')) for r in final_rows if r.get('line_number')}
        missing = [ln for ln in candidate_lines if _norm(ln) not in seen_lines]
        if missing:
            logger.info(
                '[ValveMTO] Appending %d line-only rows for line numbers Vision missed',
                len(missing),
            )
            next_sl = len(final_rows) + 1
            for ln in missing:
                synthetic = _coerce_row({'line_number': ln, 'line_list': ln})
                synthetic['sl_no'] = next_sl
                next_sl += 1
                final_rows.append(synthetic)

    # If every batch failed with a fatal error and we recovered no rows,
    # surface a clean top-level error so the frontend can display it.
    error_msg: Optional[str] = None
    if not final_rows and fatal_msgs:
        error_msg = fatal_msgs[0]
    return {
        'status': 'error' if error_msg else ('ok' if final_rows or merged_meta else 'empty'),
        'engine': 'vision' if final_rows else ('text' if text_meta else 'none'),
        'page_count': pages,
        'rows': final_rows,
        'project_meta': merged_meta,
        'warnings': warnings,
        'error': error_msg,
        'harvested_line_numbers': candidate_lines,
    }
