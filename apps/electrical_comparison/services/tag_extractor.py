"""
Extracts electrical tags from a P&ID/SLD PDF (all pages) using AI Vision
(Claude or OpenAI), then validates extracted tags against the Electrical
legend (apps.pid_checker_v2.legend_defaults.SECTION_ELECTRICAL).

Vision extraction uses an electrical-specific prompt (_call_electrical_
vision / ELECTRICAL_VISION_SYSTEM_PROMPT below) — NOT apps.pid_checker_v2.
services.vision_extractor.extract_raw_text_via_vision's generic "transcribe
all text" prompt, which has no idea it's looking at an SLD or what an
electrical tag even looks like. Page rendering, retry/fallback-model
handling, and token accounting are still reused directly from that module
rather than duplicated — only the prompt and response shape differ.
"""
import json
import logging
import re

from apps.core.ai_consumer_clients import lazy_provider_client, provider_api_key
# Rendering/preprocessing is intentionally imported from THIS app's own
# electrical_vision.py, not from apps.pid_checker_v2.services.
# vision_extractor — the two render/preprocess pipelines were forked
# apart so electrical-specific tuning (higher DPI, adaptive upscaling/
# contrast for low-quality scans) can never change P&ID Verification
# V1/V2 or P&ID Checker V2's own Vision image quality. Everything else
# below (retry/fallback-model handling, provider model constants) is
# still shared, unmodified, from vision_extractor.py.
from .electrical_vision import (
    _render_single_page,
    _prepare_image_b64,
    ELECTRICAL_VISION_RENDER_DPI,
)
from apps.pid_checker_v2.services.vision_extractor import (
    _with_retries,
    _is_model_not_found_error,
    VISION_OVERVIEW_MAX_DIMENSION_PX,
    VISION_MAX_TOKENS,
    VISION_REQUEST_TIMEOUT_S,
    VISION_MODELS,
    VISION_MODEL_CLAUDE_FALLBACK,
)
from apps.pid_checker_v2.services.token_accounting import (
    read_claude_usage, read_claude_thinking_tokens, read_openai_usage,
)
from apps.pid_checker_v2.legend_defaults import DEFAULT_TEMPLATES, SECTION_ELECTRICAL

logger = logging.getLogger(__name__)

# Get electrical legend fields — the ONE source of truth for this
# section's shape. Read once at import time (a plain in-memory Python
# dict — see the IMPORTANT note below on what this does and doesn't track).
ELECTRICAL_TEMPLATE = DEFAULT_TEMPLATES[SECTION_ELECTRICAL]
ELECTRICAL_FIELDS = ELECTRICAL_TEMPLATE['definition']['fields']
_AREA_REGEX = ELECTRICAL_FIELDS[0]['regex']       # \d{1,5}
_TYPE_REGEX = ELECTRICAL_FIELDS[1]['regex']       # [A-Za-z]{1,4}
_SEQUENCE_REGEX = ELECTRICAL_FIELDS[2]['regex']   # [A-Za-z0-9]{2,6}(?:-[A-Za-z0-9]{1,6})?
_SEPARATOR = ELECTRICAL_TEMPLATE['definition']['separator']  # '-'

# Built dynamically from the 3 field regexes above + the legend's own
# separator — NOT a second hand-copied literal, so a future edit to
# legend_defaults.py's SECTION_ELECTRICAL never needs a matching manual
# edit here again (this was a real, confirmed drift risk before this fix).
#
# IMPORTANT — what this does NOT do: DEFAULT_TEMPLATES is a static
# Python dict in legend_defaults.py, not the same thing as a legend a
# user creates/edits through the Legend Sheets Canvas UI (those are
# PidCheckerV2LegendSheet DB rows — DEFAULT_TEMPLATES is only the
# starting point "Load Default" copies FROM, never read back from a
# saved/edited legend). So this still only tracks changes to this one
# Python constant, made by editing legend_defaults.py and restarting the
# server — it does NOT pick up a user's live, UI-edited electrical legend
# automatically. Flagging this plainly rather than letting that be assumed.
# FIX 1 — a literal, space-intolerant separator here meant a tag like
# "285 - U - 503A" (spaces around the dashes — a real, confirmed shape
# some drawings/Vision transcriptions produce) never matched this
# pattern AT ALL, so it was silently lost: no valid tag, no invalid
# entry either, nothing in any log — the regex simply never fired.
# _FLEX_SEP tolerates optional whitespace on either side of the
# legend's own separator, built dynamically (not a hardcoded '-') so
# this still follows the same "single source of truth" pattern as
# every other piece of this regex.
_FLEX_SEP = rf'\s*{re.escape(_SEPARATOR)}\s*'
# _SEQUENCE_REGEX's OWN internal optional-suffix separator (the '-' in
# '(?:-[A-Za-z0-9]{1,6})?', e.g. for a two-part sequence like
# "001-007") is a second literal, non-flexible dash — swapped here for
# the same _FLEX_SEP so a suffix like "001 - 007" is tolerated too,
# not just the two outer AREA-TYPE-SEQUENCE separators.
_FLEX_SEQUENCE_REGEX = _SEQUENCE_REGEX.replace(f'(?:{_SEPARATOR}', f'(?:{_FLEX_SEP}')

ELECTRICAL_TAG_PATTERN = re.compile(
    rf'\b({_AREA_REGEX}){_FLEX_SEP}({_TYPE_REGEX}){_FLEX_SEP}({_FLEX_SEQUENCE_REGEX})\b'
)

# A deliberately LOOSER pattern than ELECTRICAL_TAG_PATTERN — digits,
# letters, then anything alnum/hyphen, with no length caps — used only to
# find tag-SHAPED candidates in the raw OCR/Vision text so genuinely
# out-of-spec ones (e.g. a 6-digit area code, a 5-letter type code — both
# invalid per the legend's own \d{1,5}/[A-Za-z]{1,4} caps) can be
# surfaced as `invalid_tags` instead of silently vanishing. A match
# against ELECTRICAL_TAG_PATTERN itself can never fail per-field
# validation (its 3 groups ARE the field regexes), so this candidate
# pattern is what makes per-field validation meaningful at all. Also used
# directly on the AI's own structured `tags` list entries (see
# _classify_tag_string) to pull out the 3 parts from a string that may
# have stray whitespace/punctuation around an otherwise-clean tag.
_CANDIDATE_PATTERN = re.compile(
    rf'\b(\d+){_FLEX_SEP}([A-Za-z]+){_FLEX_SEP}([A-Za-z0-9]+(?:{_FLEX_SEP}[A-Za-z0-9]+)*)\b'
)

# Type code lookup from electrical legend
TYPE_CODE_LOOKUP = ELECTRICAL_FIELDS[1].get('lookup', {})

# Type codes that are tag-SHAPED (match _TYPE_REGEX) but are NOT
# electrical equipment — pipe/vessel/instrument tags and other
# abbreviations commonly misread off an SLD/P&ID drawing. Read dynamically
# from legend_defaults.py's SECTION_ELECTRICAL (ELECTRICAL_TEMPLATE
# itself, not a second hand-copied literal here) so a code added via the
# Legend Manager UI's 'invalid_type_codes' field is picked up
# automatically — same "single source of truth" pattern TYPE_CODE_LOOKUP
# above already follows.
INVALID_TYPE_CODES = {c.upper() for c in ELECTRICAL_TEMPLATE['definition'].get('invalid_type_codes', [])}

# FIX 1/2/3 — same "single source of truth in legend_defaults.py" pattern
# as INVALID_TYPE_CODES above: these three are read dynamically from the
# legend's own 'definition' dict rather than hardcoded here, so editing
# legend_defaults.py (min_area_digits / dominant_area /
# placeholder_sequences) is the only change ever needed to retune them.
#
# MIN_AREA_DIGITS — an area code shorter than this is rejected outright
# (e.g. '5' in '5-U-503B' is a misread fragment, never a real area).
MIN_AREA_DIGITS = int(ELECTRICAL_TEMPLATE['definition'].get('min_area_digits', 3))
# DOMINANT_AREA — this project's expected/canonical area code, if the
# legend declares one. None means "no fixed dominant area configured for
# this legend" — _flag_suspicious_areas then falls back to its own
# per-extraction majority-area detection instead.
DOMINANT_AREA = ELECTRICAL_TEMPLATE['definition'].get('dominant_area') or None
# PLACEHOLDER_SEQUENCES — known illustrative/example sequences (legend/
# symbol-key placeholders) that must always be filtered, in addition to
# the generic all-letters/repeated-letter pattern checks below.
PLACEHOLDER_SEQUENCES = {
    s.upper() for s in ELECTRICAL_TEMPLATE['definition'].get('placeholder_sequences', [])
}

# "CODE=DESCRIPTION, CODE=DESCRIPTION, ..." — built dynamically from
# TYPE_CODE_LOOKUP (itself read from legend_defaults.py's SECTION_
# ELECTRICAL at import time above) so the Vision prompt's "known type
# codes" list can never drift out of sync with the legend the way a
# second hand-copied list would.
_TYPE_CODE_PROMPT_LIST = ', '.join(f'{code}={desc}' for code, desc in TYPE_CODE_LOOKUP.items())
# Plain "PM, NPM, U, ..." form of the same codes — used by the prompt's
# "ONLY extract tags with these known electrical type codes" instruction,
# where a bare code list reads more clearly than the CODE=DESCRIPTION form.
_TYPE_CODE_LIST_PLAIN = ', '.join(TYPE_CODE_LOOKUP.keys())

# ═══════════════════════════════════════════════════════════════════════
# FIX 1 — electrical-specific Vision prompt. extract_raw_text_via_vision's
# own RAW_TEXT_USER_PROMPT is a generic "transcribe everything on this
# P&ID page" instruction built for Tesseract-OCR replacement — it never
# tells the model it's reading an SLD, never names a single electrical
# type code, and asks for a transcription rather than a tag list. This
# prompt instead tells the model exactly what it's looking for, in what
# format, using which known type codes, and what NOT to extract.
# ═══════════════════════════════════════════════════════════════════════
ELECTRICAL_VISION_SYSTEM_PROMPT = f"""You are an expert electrical engineer reading a Single Line Diagram (SLD) drawing.
Your task is to find and extract all electrical equipment tag numbers from the drawing. Tag numbers follow the format:
AREA-TYPECODE-SEQUENCE
Example: 285-U-503A, 285-PM-411B, 285-TSG-001, 285-JB-001-007

Known type codes in this project:
{_TYPE_CODE_PROMPT_LIST}

Only extract real equipment tags.
Do NOT extract:
- Drawing numbers (A1-285-E-0229)
- Note references (NOTE-1, NOTE-6)
- Cable references (1CX300sq.mm/PH)
- Voltage ratings (380V, 22kV)
- Current ratings (4000A, 1600A)

IMPORTANT: Only extract ELECTRICAL equipment tags.
DO NOT extract:
- Pipe tags (type codes: P, PL, PP)
- Vessel tags (type codes: V, VL, VT)
- Instrument tags (type codes: FT, LT, PT, TT)
- Unknown abbreviations (NER, NW, UM, H)
- Drawing reference numbers
- Cable specifications

ONLY extract tags with these known electrical type codes:
{_TYPE_CODE_LIST_PLAIN}

If a tag's type code is not in this list, do NOT include it in the results.

Tags may appear with or without spaces around hyphens. Always return tags in normalized format without spaces:
CORRECT: 285-U-503A
INCORRECT: 285 - U - 503A"""

ELECTRICAL_VISION_USER_PROMPT = """Please extract all electrical equipment tag numbers from this Single Line Diagram.

Look for tags in these locations:
- Inside switchboard/panel boxes
- Next to motor symbols (circle with M)
- Next to transformer symbols
- In labels and callouts
- In title blocks and references

Do NOT extract these examples:
- 285-P-0028 (pipe tag)
- 285-V-453 (vessel tag)
- 285-FT-101 (instrument tag)
- 285-NER-001 (unknown)

DO extract these examples:
- 285-U-503A (switchboard)
- 285-PM-411B (pump motor)
- 285-TF-001A (transformer)
- 285-GD-401A (emergency generator)

Return ONLY a JSON object, no prose, no markdown fences:
{"tags": ["285-U-503A", "285-PM-411B", "..."], "raw_text": "full transcribed text"}"""


def resolve_equipment_type(tag: str) -> str:
    """Resolve a tag's equipment type from the electrical legend lookup,
    purely from the tag text itself — used both for P&ID-extracted tags
    and for reference-only tags (e.g. an Excel row the P&ID never showed),
    so every result row can show a sensible Equipment Type regardless of
    which side it came from."""
    match = ELECTRICAL_TAG_PATTERN.search(tag.upper())
    if not match:
        return ''
    type_code = match.group(2).upper()
    return TYPE_CODE_LOOKUP.get(type_code, f'Unknown ({type_code})')


def _get_page_count(pdf_bytes: bytes) -> int:
    import fitz  # PyMuPDF — same bytes-stream pattern as
    # apps.pid_checker_v2.services.vision_extractor's own page rendering.
    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    try:
        return doc.page_count
    finally:
        doc.close()


# ═══════════════════════════════════════════════════════════════════════
# FIX 3 — validation / false-positive filtering, shared by both the
# model's structured `tags` list and the supplementary raw_text regex
# scan below. Placeholder/example sequences (XXXX, YYYY, AAAA, repeated-
# letter runs) are filtered here too — this runs on every drawing page's
# extracted tags regardless of whether that page also has legend-like
# text embedded in it, which is what actually enforces "never report a
# tag that's just an illustrative example" (see _classify_match below).
# ═══════════════════════════════════════════════════════════════════════
_FALSE_POSITIVE_PATTERNS = (
    re.compile(r'sq\s*\.?\s*mm', re.IGNORECASE),            # cable references, e.g. 1CX300sq.mm/PH
    re.compile(r'^\d+(?:\.\d+)?\s*k?v$', re.IGNORECASE),    # voltage ratings, e.g. 380V, 22kV
    re.compile(r'^\d+(?:\.\d+)?\s*a$', re.IGNORECASE),      # current ratings, e.g. 4000A, 1600A
)

# A sequence made entirely of letters (XXXX, YYYY, AAAA) — the legend's
# own _SEQUENCE_REGEX allows this shape (it's alnum, not digit-required),
# but real sequences always carry at least one digit; an all-letter value
# is a legend/symbol-key illustrative placeholder, never real equipment.
_ALL_LETTERS_SEQUENCE_PATTERN = re.compile(r'^[A-Za-z]{3,}$')
# The same letter repeated 3+ times anywhere in the sequence (XXX, YYYY,
# AAA as a run inside a longer string) — same placeholder signal, catches
# cases _ALL_LETTERS_SEQUENCE_PATTERN alone wouldn't (e.g. a mixed value
# that still obviously isn't real, like "1XXX").
_REPEATED_LETTER_PATTERN = re.compile(r'([A-Za-z])\1{2,}')
# 4+ digit, ALL-numeric LEAD portion of the sequence (e.g. 9059, or 9059-C
# with the legend's own optional letter-suffix still attached) — not
# rejected (a real sequence legitimately could be this), just flagged
# 'suspicious' on the returned dict since every real example seen in
# this legend/project is a 3-digit-plus-letter-suffix shape (411B, 503A,
# ...), not a bare 4+ digit run.
_SUSPICIOUS_ALL_DIGITS_SEQUENCE_PATTERN = re.compile(r'^\d{4,}(?:-[A-Za-z0-9]{1,6})?$')

# FIX 3 — a trailing '-G1'/'-G2'-style suffix on an otherwise valid
# sequence is a common misread (often a generator-set/train/revision
# marker mistakenly captured as part of the tag, e.g. the real equipment
# is "285-U-503A" but Vision/OCR reads "285-U-503A-G1") rather than a
# real part of the equipment tag itself.
_MISREAD_SUFFIX_PATTERN = re.compile(r'-G\d+$', re.IGNORECASE)


def _strip_misread_suffix(sequence: str):
    """Returns (clean_sequence, stripped_suffix_or_None). Only strips the
    suffix — never rejects the tag — so e.g. '503A-G1' becomes '503A'
    rather than being discarded or kept with the bogus suffix attached."""
    m = _MISREAD_SUFFIX_PATTERN.search(sequence)
    if not m:
        return sequence, None
    return sequence[:m.start()], m.group(0)


def _is_one_digit_misread(area_a: str, area_b: str) -> bool:
    """True if area_a and area_b are the same length and differ by
    exactly one digit substitution (e.g. '283' vs '285'), or are a
    transposition of the same digits (e.g. '258' vs '285') — both
    classic single-character OCR/Vision misreads of an area code."""
    if len(area_a) != len(area_b) or area_a == area_b:
        return False
    diff_positions = sum(1 for a, b in zip(area_a, area_b) if a != b)
    if diff_positions == 1:
        return True
    return sorted(area_a) == sorted(area_b)


def _flag_suspicious_areas(tags: list) -> list:
    """FIX 2/3 — flags (never discards or auto-corrects) any tag whose
    area code doesn't belong, relative to this project's dominant area.

    Two modes, depending on whether the legend declares a fixed
    'dominant_area' (see DOMINANT_AREA above):

    - DOMINANT_AREA configured (the normal case for a project like this
      one, pinned to '285'): ANY tag whose area differs from it at all
      is flagged suspicious — '258-U-505A', '250-SB-12', '725-...', etc.
      are all real-world examples of misreads/foreign areas this is
      meant to catch, not just the narrower single-digit-misread shape.
    - DOMINANT_AREA not configured: falls back to this extraction's own
      per-run majority-area detection (Counter-based) and only flags a
      tag whose area is a classic single-digit OCR/Vision misread of
      that majority area — the original, narrower heuristic, kept for
      legends/projects with no single fixed expected area.

    Run once, as post-processing, over the full set of valid tags from
    one extraction — a single tag has no way to know what the
    "dominant" area even is on its own."""
    if not tags:
        return tags

    if DOMINANT_AREA:
        for t in tags:
            if t['area'] != DOMINANT_AREA:
                t['suspicious'] = True
                # "⚠️ Needs Review" prefix — never removed/discarded (see
                # this function's own docstring), just a clearer, ready-
                # to-display marker on the reason text itself.
                t['suspicious_reason'] = (
                    f"⚠️ Needs Review — area '{t['area']}' does not match this project's dominant area '{DOMINANT_AREA}'"
                )
                logger.info(
                    "[ElecCompare][Misread] Tag %s: area '%s' does not match configured dominant area '%s' — "
                    "flagged suspicious, NOT auto-corrected",
                    t['tag'], t['area'], DOMINANT_AREA,
                )
        return tags

    from collections import Counter
    area_counts = Counter(t['area'] for t in tags)
    dominant_area, dominant_count = area_counts.most_common(1)[0]
    # Only meaningful once there's an actual dominant area to compare
    # against — a 1-2 tag extraction has no reliable "most common" signal.
    if dominant_count < 2:
        return tags
    for t in tags:
        if t['area'] != dominant_area and _is_one_digit_misread(t['area'], dominant_area):
            t['suspicious'] = True
            t['suspicious_reason'] = (
                f"⚠️ Needs Review — area '{t['area']}' looks like a misread of this document's dominant area '{dominant_area}'"
            )
            logger.info(
                "[ElecCompare][Misread] Tag %s: area '%s' looks like a misread of dominant area '%s' — "
                "flagged suspicious, NOT auto-corrected",
                t['tag'], t['area'], dominant_area,
            )
    return tags


_SEQUENCE_LEAD_DIGITS_PATTERN = re.compile(r'^(\d+)')


def _flag_suspicious_sequences(tags: list) -> list:
    """FIX 4 — flags (never discards or auto-corrects) a 4+ digit
    sequence whose first 3 digits match a 3-digit sequence seen
    elsewhere in this SAME extraction, in the SAME area — e.g. this
    document has a real '285-U-503A' and ALSO a '285-U-5039': '5039'
    is almost certainly '503' plus one extra misread trailing digit,
    not a genuinely different piece of equipment. Run once, as post-
    processing, over the full set of valid tags from one extraction —
    a single tag has no way to know what other sequences exist on its
    own."""
    if not tags:
        return tags
    known_3digit_leads = set()
    for t in tags:
        m = _SEQUENCE_LEAD_DIGITS_PATTERN.match(t['sequence'])
        if m and len(m.group(1)) == 3:
            known_3digit_leads.add((t['area'], m.group(1)))
    for t in tags:
        m = _SEQUENCE_LEAD_DIGITS_PATTERN.match(t['sequence'])
        if not m or len(m.group(1)) < 4:
            continue
        lead3 = m.group(1)[:3]
        if (t['area'], lead3) in known_3digit_leads:
            t['suspicious'] = True
            t['suspicious_reason'] = (
                f"⚠️ Needs Review — sequence '{t['sequence']}' looks like a misread of this document's "
                f"known sequence '{lead3}' with an extra trailing digit"
            )
            logger.info(
                "[ElecCompare][Misread] Tag %s: sequence '%s' looks like a misread of known sequence '%s' "
                "(extra trailing digit) — flagged suspicious, NOT auto-corrected",
                t['tag'], t['sequence'], lead3,
            )
    return tags


def _is_known_false_positive(candidate_text: str) -> bool:
    """Obvious non-tag shapes the model occasionally returns despite the
    prompt's own exclusion list: cable references, voltage/current
    ratings. Drawing numbers (A1-285-E-0229) are handled separately — see
    their own prefix check at each call site, since that shape needs to
    look at what comes immediately BEFORE a match, not just the matched
    text itself."""
    return any(p.search(candidate_text) for p in _FALSE_POSITIVE_PATTERNS)


def _is_placeholder_sequence(sequence: str) -> bool:
    """True for a legend/symbol-key illustrative placeholder sequence
    (XXXX, YYYY, AAAA, or any run of the same letter 3+ times) — these
    pass the legend's own _SEQUENCE_REGEX but are never real equipment.
    Used both to filter individual tag candidates (_classify_match) and
    to recognise a legend/symbol-key PAGE by the presence of such a tag
    (_classify_page)."""
    sequence = sequence.upper()
    if sequence in PLACEHOLDER_SEQUENCES:
        return True
    return bool(_ALL_LETTERS_SEQUENCE_PATTERN.match(sequence)) or bool(_REPEATED_LETTER_PATTERN.search(sequence))


def _classify_match(area: str, type_code: str, sequence: str):
    """Per-field legend validation + unknown_type/equipment_type/
    suspicious enrichment for one area/type_code/sequence triple — shared
    by both _extract_candidates_from_text (raw_text scan) and
    _classify_tag_string (model's own structured tags list). Returns
    (valid_dict_or_None, invalid_dict_or_None) — both None for a
    placeholder sequence (discarded entirely, not even reported as
    invalid, since it's known noise rather than a legend violation)."""
    # FIX 2 — normalize away any spaces the flexible separator patterns
    # above (_FLEX_SEP/_FLEX_SEQUENCE_REGEX) tolerated around a dash,
    # e.g. a sequence captured as "001 - 007" becomes "001-007" here —
    # every downstream consumer (dedup via `tag` string, DB storage,
    # the UI) expects the normalized no-space form, not whatever
    # spacing the source drawing/Vision transcription happened to have.
    area = re.sub(r'\s*-\s*', '-', area.strip())
    type_code = re.sub(r'\s*-\s*', '-', type_code.strip().upper())
    sequence = re.sub(r'\s*-\s*', '-', sequence.strip().upper())

    if _is_placeholder_sequence(sequence):
        return None, None

    # FIX 3 — strip a common misread suffix (-G1, -G2, ...) BEFORE
    # building/validating the tag, so "285-U-503A-G1" is corrected to
    # "285-U-503A" rather than either kept with the bogus suffix
    # attached or rejected outright for an otherwise-valid tag.
    sequence, stripped_suffix = _strip_misread_suffix(sequence)

    tag = f'{area}{_SEPARATOR}{type_code}{_SEPARATOR}{sequence}'

    area_ok = bool(re.fullmatch(_AREA_REGEX, area))
    type_ok = bool(re.fullmatch(_TYPE_REGEX, type_code))
    seq_ok = bool(re.fullmatch(_SEQUENCE_REGEX, sequence))
    # FIX 1 — an area code shorter than the legend's min_area_digits is a
    # misread fragment of a real area code (e.g. '5' in '5-U-503B'), not
    # a real area on its own, even though it still matches the legend's
    # own \d{1,5} area regex — reject it outright, same as a bad type
    # code, rather than letting it through as a "valid" tag.
    if area_ok and len(area) < MIN_AREA_DIGITS:
        area_ok = False
    if area_ok and type_ok and seq_ok:
        # FIX 1 — a type_code that's tag-SHAPED but is a KNOWN non-
        # electrical code (pipe/vessel/instrument/other abbreviation,
        # per legend_defaults.py's invalid_type_codes) is never included
        # in results — these are misreads of a different tagging
        # system entirely, not "an electrical type we don't know about
        # yet" (that case is unknown_type below, which IS still kept).
        if type_code in INVALID_TYPE_CODES:
            logger.info(
                "[ElecCompare][FalsePositive] Filtered tag %r — type_code '%s' is a known non-electrical "
                "code (pipe/vessel/instrument/other), not real equipment",
                tag, type_code,
            )
            return None, {
                'tag': tag, 'area': area, 'type_code': type_code, 'sequence': sequence,
                'valid': False,
                'reason': (
                    f"type_code '{type_code}' is a known non-electrical code "
                    f"(pipe/vessel/instrument/other) — filtered as false positive"
                ),
            }
        if stripped_suffix:
            logger.info(
                "[ElecCompare][Misread] Corrected tag to %r — stripped suspected misread suffix %r from sequence",
                tag, stripped_suffix,
            )
        return {
            'tag': tag,
            'area': area,
            'type_code': type_code,
            'sequence': sequence,
            'equipment_type': TYPE_CODE_LOOKUP.get(type_code, f'Unknown ({type_code})'),
            'valid': True,
            # FIX 3 — a type_code outside the legend lookup is still
            # included (never silently dropped), just flagged so the
            # caller/UI can distinguish "known equipment type" from
            # "tag-shaped, but this type code isn't in our legend yet".
            'unknown_type': type_code not in TYPE_CODE_LOOKUP,
            # FIX 3 — a 4+ digit all-numeric sequence (e.g. 9059) is
            # unusual for this project's real tags (which are 3-digit +
            # letter-suffix) — still included, just flagged for review
            # rather than silently trusted or silently dropped.
            'suspicious': bool(_SUSPICIOUS_ALL_DIGITS_SEQUENCE_PATTERN.match(sequence)),
            # FIX 3 — set when a misread suffix was stripped, so the
            # caller/UI can tell a corrected tag apart from one that was
            # always clean.
            'corrected': bool(stripped_suffix),
        }, None
    reasons = []
    if not area_ok:
        if len(area) < MIN_AREA_DIGITS and bool(re.fullmatch(_AREA_REGEX, area)):
            reasons.append(f"area '{area}' is shorter than the legend's min_area_digits ({MIN_AREA_DIGITS}) — likely a misread fragment")
        else:
            reasons.append(f"area '{area}' does not match {_AREA_REGEX}")
    if not type_ok:
        reasons.append(f"type_code '{type_code}' does not match {_TYPE_REGEX}")
    if not seq_ok:
        reasons.append(f"sequence '{sequence}' does not match {_SEQUENCE_REGEX}")
    return None, {
        'tag': tag, 'area': area, 'type_code': type_code, 'sequence': sequence,
        'valid': False, 'reason': '; '.join(reasons),
    }


def _extract_candidates_from_text(raw_text: str):
    """Returns (valid_tags: list[dict], invalid_tags: list[dict]) found in
    one page's raw text — a supplementary pass over the full page
    transcription, run alongside the model's own structured `tags` list
    so a tag the model transcribed but left out of that list isn't lost."""
    valid, invalid = [], []
    for m in _CANDIDATE_PATTERN.finditer(raw_text):
        # FIX 3 — a drawing number like "A1-285-E-0229" matches this loose
        # shape starting from "285-E-0229" (the "A1-" prefix sits just
        # outside the \b-bounded match), so check the few characters
        # immediately BEFORE the match for that prefix shape rather than
        # only the 3 captured groups themselves.
        context_before = raw_text[max(0, m.start() - 6):m.start()]
        if re.search(r'[A-Za-z]\d*-$', context_before) or _is_known_false_positive(m.group(0)):
            continue
        v, i = _classify_match(m.group(1), m.group(2), m.group(3))
        if v:
            valid.append(v)
        if i:
            invalid.append(i)
    return valid, invalid


def _classify_tag_string(tag_str: str):
    """Validate+classify one tag STRING returned directly in the model's
    'tags' list — same false-positive/per-field-regex/unknown_type logic
    as _extract_candidates_from_text, adapted for a standalone string
    with no surrounding page text (so the drawing-number check here looks
    at the string's OWN start, e.g. "A1-285-E-0229", instead of text
    immediately before a substring match). Returns
    (valid_dict_or_None, invalid_dict_or_None) — both None for a string
    that isn't tag-shaped at all or is a known false-positive shape."""
    tag_str = (tag_str or '').strip()
    if not tag_str:
        return None, None
    if re.match(r'^[A-Za-z]\d+-', tag_str) or _is_known_false_positive(tag_str):
        return None, None
    m = _CANDIDATE_PATTERN.search(tag_str)
    if not m:
        # FIX 4 — this is the genuinely-lost case (as opposed to the
        # drawing-number/known-false-positive checks above, which are
        # deliberate, expected filtering and would just be log noise):
        # the model returned this string as a tag, but it didn't match
        # even the loose candidate shape at all — logged so a real
        # drop (e.g. an unusual format this pattern still doesn't
        # tolerate) is actually visible instead of vanishing silently.
        logger.warning(
            '[ElecCompare] Tag-shaped text dropped (no match): %s', tag_str,
        )
        return None, None
    return _classify_match(m.group(1), m.group(2), m.group(3))


# ═══════════════════════════════════════════════════════════════════════
# FIX 4 — legend/symbol-key pages are never skipped outright any more
# (an earlier version of this fix did skip them by keyword, which missed
# a real legend page that had no skip keyword on it and leaked its own
# placeholder example tags — 285-U-XXXX, 285-U-YYYY — into the results).
# Instead: detect them, use their text as CONTEXT injected into every
# drawing page's Vision prompt (so the model can recognise what a symbol
# means), and never attempt tag extraction on the legend page itself.
# Genuinely empty/administrative pages (notes, revision history, BOM,
# near-blank) are still skipped — those carry no useful information
# either way, which is a different situation from a legend page.
# ═══════════════════════════════════════════════════════════════════════
_SKIP_PAGE_KEYWORDS = (
    'NOTES', 'NOTE NO.', 'REVISION HISTORY',
    'GENERAL NOTES', 'BILL OF MATERIAL',
)
_LEGEND_PAGE_KEYWORDS = ('LEGENDS', 'SYMBOL KEY')
_MIN_DRAWING_PAGE_TEXT_LEN = 50

# Page classification outcomes for _classify_page.
PAGE_TYPE_DRAWING = 'drawing'
PAGE_TYPE_LEGEND = 'legend'
PAGE_TYPE_SKIP = 'skip'


def _is_legend_page(text: str) -> bool:
    """True if this page's text names itself as a legend/symbol-key page."""
    upper_text = text.upper()
    return any(keyword in upper_text for keyword in _LEGEND_PAGE_KEYWORDS)


def _classify_page(pdf_bytes: bytes, page_index: int):
    """Returns (page_type, text) where page_type is one of
    PAGE_TYPE_DRAWING / PAGE_TYPE_LEGEND / PAGE_TYPE_SKIP.

    Order: a REAL (non-placeholder) tag match always means "drawing page"
    and is checked FIRST — a real drawing page whose title block happens
    to also say "NOTES" or "LEGENDS" must never be treated as anything
    but a drawing page just because that keyword is also present.
    Only once no real tag is found do the legend-keyword / skip-keyword /
    length checks get a chance to classify the page as legend or skip.
    """
    import fitz
    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    try:
        text = doc[page_index].get_text() or ''
    finally:
        doc.close()

    candidates = list(_CANDIDATE_PATTERN.finditer(text))
    has_real_tag = any(not _is_placeholder_sequence(m.group(3)) for m in candidates)
    if has_real_tag:
        return PAGE_TYPE_DRAWING, text

    if _is_legend_page(text):
        return PAGE_TYPE_LEGEND, text

    upper_text = text.upper()
    for keyword in _SKIP_PAGE_KEYWORDS:
        if keyword in upper_text:
            return PAGE_TYPE_SKIP, text

    # Only placeholder-shaped candidates (no real tag) and no explicit
    # legend keyword — still almost certainly a legend/symbol-key page
    # (this is exactly the real page that slipped through before: no
    # skip keyword, just illustrative 285-U-XXXX/YYYY examples).
    has_placeholder_tag = any(_is_placeholder_sequence(m.group(3)) for m in candidates)
    if has_placeholder_tag:
        return PAGE_TYPE_LEGEND, text

    if len(text.strip()) < _MIN_DRAWING_PAGE_TEXT_LEN:
        return PAGE_TYPE_SKIP, text

    return PAGE_TYPE_DRAWING, text


# Legend context is capped before being injected into a drawing page's
# system prompt — it's background knowledge about symbols, not the
# primary extraction target, so an unusually large legend page's full
# text shouldn't bloat (and add token cost to) every subsequent page's
# Vision call.
_LEGEND_CONTEXT_MAX_CHARS = 4000


def _compose_electrical_system_prompt(legend_context: str | None) -> str:
    """Prepend/append this document's own legend/symbol-key page text (if
    any was found) to ELECTRICAL_VISION_SYSTEM_PROMPT as extra context —
    same "inject real document-specific context into the base prompt"
    pattern as vision_extractor.py's own _compose_user_prompt (used there
    for a Legend Sheet's rules). The model is explicitly told to use it
    for symbol recognition only, never as a source of tags to report."""
    if not legend_context:
        return ELECTRICAL_VISION_SYSTEM_PROMPT
    return (
        ELECTRICAL_VISION_SYSTEM_PROMPT
        + "\n\nContext — this drawing's own LEGEND/SYMBOL KEY page text is "
          "below. Use it only to help you recognise what a symbol represents. "
          "NEVER report a tag that appears ONLY here as an illustrative/"
          "placeholder example (e.g. XXXX, YYYY, or any similar example "
          "value) — only report tags you actually see on the drawing page "
          "you are looking at right now:\n"
        "──────────────────────────────────────────────────────────\n"
        + legend_context[:_LEGEND_CONTEXT_MAX_CHARS]
        + "\n──────────────────────────────────────────────────────────"
    )


# ═══════════════════════════════════════════════════════════════════════
# FIX 1 — _call_electrical_vision: renders the page the same way
# vision_extractor.py's own extract_raw_text_via_vision does
# (_render_single_page + _prepare_image_b64), reuses its retry/fallback-
# model machinery (_with_retries, _is_model_not_found_error,
# VISION_MODEL_CLAUDE_FALLBACK), but sends ELECTRICAL_VISION_SYSTEM_PROMPT
# / ELECTRICAL_VISION_USER_PROMPT instead of that function's fixed
# generic transcription prompt, and parses a {"tags": [...], "raw_text":
# str} response instead of {"raw_text": str, "located_tags": [...]}.
# ═══════════════════════════════════════════════════════════════════════
def _parse_electrical_vision_response(raw: str) -> dict:
    """Parse the {'tags': [...], 'raw_text': str} JSON
    ELECTRICAL_VISION_USER_PROMPT asks for; falls back to treating the
    whole reply as raw_text (same fallback contract as vision_extractor.
    _parse_raw_text_response) if the model didn't return valid JSON."""
    if not raw:
        return {'tags': [], 'raw_text': ''}
    text = raw.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r'\{.*\}', text, flags=re.DOTALL)
        if not m:
            return {'tags': [], 'raw_text': text}
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError:
            return {'tags': [], 'raw_text': text}
    if not isinstance(parsed, dict):
        return {'tags': [], 'raw_text': text}
    tags = parsed.get('tags') or []
    if not isinstance(tags, list):
        tags = []
    return {
        'tags': [str(t).strip() for t in tags if str(t).strip()],
        'raw_text': parsed.get('raw_text') or '',
    }


def _call_claude_electrical(api_key, image_b64, user_prompt, model=None, system_prompt=None):
    import anthropic
    client = lazy_provider_client(
        'anthropic', anthropic.Anthropic, api_key=lambda: (api_key), timeout=VISION_REQUEST_TIMEOUT_S,
    )
    resp = client.messages.create(
        model=model or VISION_MODELS['claude'],
        max_tokens=VISION_MAX_TOKENS,
        # Same adaptive-thinking shape vision_extractor.py's own
        # _call_claude/_call_raw_text_vision use — see those functions'
        # comments for why (thinking.type.enabled is rejected outright by
        # this account's model; adaptive + output_config.effort is the
        # API it actually accepts). No `temperature` for the same reason.
        thinking={'type': 'adaptive', 'display': 'summarized'},
        output_config={'effort': 'high'},
        system=system_prompt or ELECTRICAL_VISION_SYSTEM_PROMPT,
        messages=[{'role': 'user', 'content': [
            {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': image_b64}},
            {'type': 'text', 'text': user_prompt},
        ]}],
    )
    inp, out = read_claude_usage(resp)
    thinking_tok = read_claude_thinking_tokens(resp)
    stop_reason = getattr(resp, 'stop_reason', None)
    logger.info(
        '[ElecCompare][Vision] stop_reason=%s tokens: input=%s output=%s thinking=%s (max_tokens=%s)',
        stop_reason, inp, out, thinking_tok, VISION_MAX_TOKENS,
    )
    parts = [b.text for b in resp.content if getattr(b, 'type', None) == 'text']
    return ''.join(parts), inp, out


def _call_openai_electrical(api_key, image_b64, user_prompt, system_prompt=None):
    import openai
    client = lazy_provider_client(
        'openai', openai.OpenAI, api_key=lambda: (api_key), timeout=VISION_REQUEST_TIMEOUT_S,
    )
    resp = client.chat.completions.create(
        model=VISION_MODELS['openai'],
        max_tokens=VISION_MAX_TOKENS,
        temperature=0.0,
        messages=[
            {'role': 'system', 'content': system_prompt or ELECTRICAL_VISION_SYSTEM_PROMPT},
            {'role': 'user', 'content': [
                {'type': 'text', 'text': user_prompt},
                {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{image_b64}', 'detail': 'high'}},
            ]},
        ],
    )
    text = resp.choices[0].message.content or ''
    inp, out = read_openai_usage(resp)
    return text, inp, out


def _call_electrical_vision(pdf_bytes, page_index, api_key, provider='claude', model=None,
                             legend_context: str | None = None) -> dict:
    """Render page `page_index` and ask AI Vision for electrical tags
    using ELECTRICAL_VISION_SYSTEM_PROMPT/ELECTRICAL_VISION_USER_PROMPT —
    with `legend_context` (this document's own legend/symbol-key page
    text, if any was found — see _classify_page) composed into the
    system prompt via _compose_electrical_system_prompt, so the model can
    use it to recognise symbols without treating it as a source of tags.
    Returns {'tags': [str], 'raw_text': str, 'provider': str, 'model':
    str, 'token_usage': dict}. Raises ValueError if api_key is missing —
    same fail-fast contract extract_raw_text_via_vision has."""
    api_key = provider_api_key(provider, fallback=lambda: (api_key)) if provider else api_key
    if provider not in ('openai', 'claude'):
        raise ValueError(f"Unsupported provider '{provider}'. Choose one of ('openai', 'claude').")
    if not api_key or not api_key.strip():
        raise ValueError('api_key is required for Vision-based tag extraction')

    page_img = _render_single_page(pdf_bytes, page_index, dpi=ELECTRICAL_VISION_RENDER_DPI)
    image_b64 = _prepare_image_b64(page_img, VISION_OVERVIEW_MAX_DIMENSION_PX)
    system_prompt = _compose_electrical_system_prompt(legend_context)

    resolved_model = model or VISION_MODELS[provider]
    if provider == 'openai':
        fn = lambda k, b64, p: _call_openai_electrical(k, b64, p, system_prompt=system_prompt)  # noqa: E731
    else:
        fn = lambda k, b64, p: _call_claude_electrical(k, b64, p, model=model, system_prompt=system_prompt)  # noqa: E731
    effective_model = model or VISION_MODELS['claude']

    try:
        raw, inp, out = _with_retries(provider, fn, api_key, image_b64, ELECTRICAL_VISION_USER_PROMPT)
    except Exception as exc:  # noqa: BLE001
        if provider == 'claude' and effective_model != VISION_MODEL_CLAUDE_FALLBACK \
                and _is_model_not_found_error(exc):
            logger.warning(
                "[ElecCompare][Vision] model '%s' unavailable (%s) — retrying once with fallback '%s'",
                effective_model, exc, VISION_MODEL_CLAUDE_FALLBACK,
            )
            fallback_fn = lambda k, b64, p: _call_claude_electrical(  # noqa: E731
                k, b64, p, model=VISION_MODEL_CLAUDE_FALLBACK, system_prompt=system_prompt)
            raw, inp, out = _with_retries(provider, fallback_fn, api_key, image_b64, ELECTRICAL_VISION_USER_PROMPT)
            resolved_model = VISION_MODEL_CLAUDE_FALLBACK
        else:
            raise

    parsed = _parse_electrical_vision_response(raw)
    return {
        'tags': parsed['tags'],
        'raw_text': parsed['raw_text'],
        'provider': provider,
        'model': resolved_model,
        'token_usage': {'calls': 1, 'input_tokens': inp, 'output_tokens': out},
    }


def extract_electrical_tags(pdf_bytes, api_key,
                             provider='claude', model=None):
    """
    Extract and validate electrical tags from every DRAWING page of the
    PDF. Genuinely administrative pages (notes, revision history, BOM,
    near-blank) are skipped for free before they ever reach Vision.
    Legend/symbol-key pages are NEVER skipped and NEVER have tags
    extracted from them directly — instead their text is collected as
    `legend_context` and injected into every DRAWING page's Vision
    prompt, so the model can use it to recognise symbols without ever
    treating the legend page's own illustrative examples (285-U-XXXX,
    285-U-YYYY, ...) as real equipment (see _classify_page,
    _compose_electrical_system_prompt).
    Returns:
    {
        'tags': [
            {
                'tag': '285-PM-411B',
                'area': '285',
                'type_code': 'PM',
                'sequence': '411B',
                'equipment_type': 'PUMP MOTOR',
                'valid': True,
                'unknown_type': False,
                'suspicious': False,
            }
        ],
        'invalid_tags': [ {..., 'valid': False, 'reason': str} ],
        'raw_text': str,              # all drawing pages concatenated
        'raw_text_per_page': dict,    # {str(page_index): text} for EVERY
                                       # page (drawing/legend/skip alike)
                                       # — persisted to the job record for
                                       # debugging what Vision actually
                                       # saw/returned per page.
        'provider': str,
        'model': str,
        'token_usage': dict,          # summed across every page's Vision call
        'page_count': int,            # total pages in the PDF
        'pages_skipped': int,         # administrative pages skipped
        'legend_pages_found': int,    # pages used as context only
    }
    Raises ValueError if the api_key is missing/invalid — checked
    up-front (not only inside the first Vision call) so a document whose
    every page happens to classify as legend/skip still fails fast on a
    bad key rather than silently returning zero tags with no Vision call
    ever made. Same "Invalid or missing API key" message
    UploadComparisonView's existing except ValueError handling expects.
    """
    if not (api_key and api_key.strip()):
        raise ValueError('api_key is required for Vision-based tag extraction')

    page_count = _get_page_count(pdf_bytes)

    # First pass — classify every page up front and collect ALL legend/
    # symbol-key pages' text into one combined context string, so even a
    # drawing page that comes BEFORE the legend page in the document
    # still benefits from it (not just pages after it).
    page_types = {}
    page_texts = {}
    legend_context_parts = []
    for page_index in range(page_count):
        page_type, text = _classify_page(pdf_bytes, page_index)
        page_types[page_index] = page_type
        page_texts[page_index] = text
        if page_type == PAGE_TYPE_LEGEND:
            legend_context_parts.append(text.strip())
    legend_context = '\n\n'.join(legend_context_parts) if legend_context_parts else None
    legend_pages_found = len(legend_context_parts)

    all_valid_tags = []
    all_invalid_tags = []
    raw_text_parts = []
    raw_text_per_page = {}
    seen_tags = set()
    provider_used = provider
    model_used = ''
    total_calls = 0
    total_input_tokens = 0
    total_output_tokens = 0
    total_cost_usd = 0.0
    pages_skipped = 0

    for page_index in range(page_count):
        page_type = page_types[page_index]

        if page_type == PAGE_TYPE_SKIP:
            logger.info(
                '[ElectricalComparison] Skipping page %d/%d (notes/revision-history/BOM/blank page)',
                page_index + 1, page_count,
            )
            pages_skipped += 1
            raw_text_per_page[str(page_index)] = '[SKIPPED - administrative page, not sent to Vision]'
            continue

        if page_type == PAGE_TYPE_LEGEND:
            # Never extract equipment tags from a legend/symbol-key page
            # directly — its text (including illustrative placeholder
            # tags like 285-U-XXXX) is used only as CONTEXT for drawing
            # pages, never treated as real equipment data itself.
            logger.info(
                '[ElectricalComparison] Page %d/%d is a legend/symbol-key page — using as context only, no tag extraction',
                page_index + 1, page_count,
            )
            raw_text_per_page[str(page_index)] = page_texts[page_index]
            continue

        # Drawing page — send to Vision, with legend context (if any)
        # injected into the system prompt.
        result = _call_electrical_vision(
            pdf_bytes=pdf_bytes,
            page_index=page_index,
            api_key=api_key,
            provider=provider,
            model=model,
            legend_context=legend_context,
        )
        provider_used = result.get('provider', provider)
        model_used = result.get('model', model_used)

        usage = result.get('token_usage') or {}
        total_calls += usage.get('calls', 1)
        total_input_tokens += usage.get('input_tokens', 0)
        total_output_tokens += usage.get('output_tokens', 0)
        try:
            total_cost_usd += float(usage.get('cost_usd', 0) or 0)
        except (TypeError, ValueError):
            pass

        page_text = result.get('raw_text', '')
        raw_text_parts.append(page_text)
        raw_text_per_page[str(page_index)] = page_text

        page_valid = []
        page_invalid = []

        # Primary source — the model's own structured 'tags' list
        # (FIX 1: an electrical-specific prompt that actually asks it to
        # find tags, with known type codes and exclusions, rather than
        # just transcribing all text on the page).
        for tag_str in result.get('tags', []):
            v, i = _classify_tag_string(tag_str)
            if v:
                page_valid.append(v)
            if i:
                page_invalid.append(i)

        # Supplementary source — loose regex scan of the full page
        # transcription, so a tag the model transcribed into raw_text
        # but left out of its structured 'tags' list isn't silently lost.
        # (_classify_match's own placeholder-sequence check — FIX 3 —
        # still applies here too, so a placeholder tag that leaks into a
        # drawing page's raw_text is filtered the same as everywhere else.)
        text_valid, text_invalid = _extract_candidates_from_text(page_text)
        page_valid.extend(text_valid)
        page_invalid.extend(text_invalid)

        new_on_this_page = 0
        for t in page_valid:
            if t['tag'] in seen_tags:
                continue
            seen_tags.add(t['tag'])
            all_valid_tags.append(t)
            new_on_this_page += 1
        all_invalid_tags.extend(page_invalid)

        logger.info(
            '[ElectricalComparison] page %d/%d: %d valid tag(s) (%d new), %d invalid candidate(s)',
            page_index + 1, page_count, len(page_valid), new_on_this_page, len(page_invalid),
        )

    # FIX 3 — flag (never auto-correct) any tag whose area code looks
    # like a single-digit misread of this document's own dominant area,
    # now that every page's tags are collected (a per-tag check alone
    # has no way to know what "dominant" even means).
    all_valid_tags = _flag_suspicious_areas(all_valid_tags)
    # FIX 4 — same post-processing idea, for sequence-level misreads
    # (e.g. '5039' next to a real '503A' in the same area).
    all_valid_tags = _flag_suspicious_sequences(all_valid_tags)

    # Sort by area code (numeric) then type_code.
    all_valid_tags.sort(key=lambda t: (int(t['area']), t['type_code']))

    return {
        'tags': all_valid_tags,
        'invalid_tags': all_invalid_tags,
        'raw_text': '\n'.join(raw_text_parts),
        'raw_text_per_page': raw_text_per_page,
        'provider': provider_used,
        'model': model_used,
        'token_usage': {
            'calls': total_calls,
            'input_tokens': total_input_tokens,
            'output_tokens': total_output_tokens,
            'total_tokens': total_input_tokens + total_output_tokens,
            'cost_usd': str(total_cost_usd),
        },
        'page_count': page_count,
        'pages_skipped': pages_skipped,
        'legend_pages_found': legend_pages_found,
    }
