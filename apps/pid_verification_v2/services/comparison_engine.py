"""
P&ID Verification V2 — Comparison Engine

Cross-document comparison service for identifying discrepancies between:
  1. P&ID and Legend sheets
  2. P&ID and Line List
  3. P&ID and Equipment Register
  4. P&ID and Instrument Index

Returns structured comparison results with discrepancy categories:
  - missing: Items in reference but not in P&ID
  - extra: Items in P&ID but not in reference
  - mismatch: Items present in both but with different attributes

All comparison logic is soft-coded for easy tuning.
"""

import logging
import re
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# SOFT-CODED CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

# Fuzzy matching threshold (0.0-1.0). Items with similarity >= threshold are considered matches.
COMPARISON_MATCH_THRESHOLD = 0.85

# Attribute weight configuration for computing overall similarity
# Higher weight = attribute has more influence on match decision
ATTRIBUTE_WEIGHTS = {
    'line_list': {
        'tag': 1.0,           # Line tag is critical
        'size': 0.8,          # Size is important
        'fluid_code': 0.6,    # Fluid code matters
        'spec': 0.5,          # Material spec
        'from_to': 0.4,       # From-To location
    },
    'equipment': {
        'tag': 1.0,           # Equipment tag is critical
        'type': 0.7,          # Equipment type
        'description': 0.5,   # Description
        'service': 0.6,       # Service/duty
    },
    'instrument': {
        'tag': 1.0,           # Instrument tag is critical
        'type': 0.8,          # Instrument type (PT, FT, etc.)
        'service': 0.6,       # Service description
        'range': 0.4,         # Operating range
    },
}

# Legend symbol matching — exact match required (no fuzzy matching for symbols)
LEGEND_EXACT_MATCH = True

# A normalized tag key must contain at least one letter AND one digit to be
# treated as a real tag (line/equipment/instrument tags always mix letters
# and digits — e.g. V803TF, 6FLAC3N8183, BDHS8001TF). A key that's purely
# numeric, purely alphabetic, or too short is far more likely to be leftover
# OCR/PDF-parsing noise (stray page numbers, note markers, single letters)
# than a genuine tag — comparing against it produces a false "missing"/
# "extra" finding for nothing. See _looks_like_valid_tag().
_MIN_VALID_TAG_LEN = 3


# ═══════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ComparisonFinding:
    """Single discrepancy found during comparison"""
    category: str           # 'missing', 'extra', 'mismatch'
    comparison_type: str    # 'legend', 'linelist', 'equipment', 'instrument'
    item_id: str           # Identifier (tag, symbol name, etc.)
    severity: str          # 'critical', 'major', 'minor'
    issue_observed: str    # Human-readable description
    pid_value: Any         # Value found in P&ID (None if missing)
    ref_value: Any         # Value found in reference doc (None if extra)
    similarity: float      # Match score 0.0-1.0 (1.0 = exact match)
    evidence: str          # Supporting data for review


@dataclass
class ComparisonResult:
    """Complete comparison results for one comparison type"""
    comparison_type: str        # 'legend', 'linelist', 'equipment', 'instrument'
    total_pid_items: int       # Items extracted from P&ID
    total_ref_items: int       # Items in reference document
    matched_count: int         # Items that matched successfully
    missing_count: int         # Items in ref but not in P&ID
    extra_count: int           # Items in P&ID but not in ref
    mismatch_count: int        # Items with attribute differences
    findings: List[ComparisonFinding]
    summary: str               # One-line summary
    # Items the AI smart-comparison pass (see _resolve_ambiguous_pairs)
    # couldn't confidently classify as match/mismatch — flagged for manual
    # engineer review rather than silently guessed either way. Always 0
    # when no BYOK Claude key was provided (naive fuzzy-match fallback
    # never produces "uncertain" — see compare_with_*()).
    uncertain_count: int = 0


# ═══════════════════════════════════════════════════════════════════════════
# FUZZY MATCHING UTILITIES
# ═══════════════════════════════════════════════════════════════════════════

def fuzzy_match(str1: str, str2: str, threshold: float = COMPARISON_MATCH_THRESHOLD) -> float:
    """
    Compute fuzzy similarity between two strings.
    
    Returns:
        Float 0.0-1.0 where 1.0 = exact match
    """
    if not str1 or not str2:
        return 0.0
    
    str1_clean = str1.strip().lower()
    str2_clean = str2.strip().lower()
    
    if str1_clean == str2_clean:
        return 1.0
    
    # Use SequenceMatcher for fuzzy comparison
    return SequenceMatcher(None, str1_clean, str2_clean).ratio()


import re as _re

# Collapse any run of whitespace (including embedded newlines from PDF-table
# row-merging artifacts) or underscores into a single hyphen.
_TAG_SEPARATOR_RE = _re.compile(r'[\s_]+')
# Collapse repeated hyphens produced by the above (e.g. a stray space right
# before an existing hyphen: "V-803 -TF" -> "V-803--TF" -> "V-803-TF").
_TAG_REPEATED_HYPHEN_RE = _re.compile(r'-{2,}')
# Final character whitelist — real tags are letters/digits/hyphens, plus '/'
# for combined multi-unit tags like "P-851A/B/C" and pipe-size fractions
# like "3/4"/"1-1/2". This also strips the inch mark (") itself, so
# "3/4\"" -> "3/4" and "6\"" -> "6" line up with a reference cell that
# never had the mark at all.
_TAG_JUNK_CHAR_RE = _re.compile(r'[^A-Z0-9\-/]')
# A compound fraction written with a period instead of a hyphen —
# "1.1/2" meaning 1-1/2 inches — before the period gets silently dropped
# by the junk-char strip (which would wrongly merge it into "11/2").
_TAG_FRACTION_DOT_RE = _re.compile(r'(\d{1,2})\.(\d{1,2}/\d{1,2})')
# A letter-run directly touching a digit-run with NO separator at all
# ("FT8001", "PSV8003", "V803TF") — only applied when the tag has no
# hyphen anywhere, see normalize_tag() below for why.
_TAG_LETTER_DIGIT_BOUNDARY_RE = _re.compile(r'(?<=[A-Z])(?=[0-9])|(?<=[0-9])(?=[A-Z])')
# A site-symbol/train-letter suffix (1-4 letters, e.g. TF, CF, HF, A, B)
# glued directly onto the final digit run with no hyphen — real data shows
# this specific partial-separator pattern even on OTHERWISE hyphenated
# tags (e.g. Instrument Index export "BDHS-8001TF" instead of
# "BDHS-8001-TF"). Anchored to the END of the string so it can never touch
# a letter/digit/letter code like "AC3N" sitting mid-string in a line tag.
_TAG_TRAILING_SUFFIX_RE = _re.compile(r'(\d)([A-Z]{1,4})$')


def normalize_tag(value: Any, strip_leading_digit_noise: bool = False) -> str:
    """
    THE single shared tag-normalization function — used for every
    line-list/equipment/instrument/legend comparison in this module, applied
    identically to BOTH sides (P&ID-extracted tags and reference Excel/PDF
    tags) before matching. Canonicalizes formatting noise while preserving
    the tag's real structure (hyphens, suffixes), so all of these resolve to
    the same key:
        "V-803-TF"          -> "V-803-TF"
        "V-803 -TF"         -> "V-803-TF"   (stray space before hyphen)
        "v_803_tf"          -> "V-803-TF"   (underscores, lowercase)
        "V 803 TF"          -> "V-803-TF"   (bare spaces instead of hyphens)
        "V803TF"            -> "V-803-TF"   (no separators at all)
        "PSV 8003" / "PSV - 8003" / "PSV--8003" / "PSV8003" -> "PSV-8003"
        "3/4\""              -> "3/4"        (inch mark stripped)
        "1.1/2" / "1 1/2"   -> "1-1/2"      (compound-fraction dot/space)
        " PSV-8003 "        -> "PSV-8003"   (leading/trailing junk)
        "-3\\nBDHS -8001 TF" -> "BDHS-8001-TF"  (strip_leading_digit_noise=True)

    Steps: uppercase -> newlines/tabs to spaces -> strip -> fold a
    period-separated compound fraction ("1.1/2") to hyphen form -> collapse
    whitespace/underscore runs to a single hyphen -> collapse repeated
    hyphens -> strip stray leading/trailing hyphens -> drop any leftover
    non-tag characters (including the inch mark) -> if no hyphen survived
    anywhere, insert one at every letter/digit boundary to recover a
    squished-together tag's structure -> optionally strip a leaked leading
    1-2 digit fragment.

    The letter/digit boundary-insertion step only fires when the string has
    ZERO hyphens left at that point — a normal multi-component tag (line
    tags especially, e.g. "6\"-FL-AC3N-8110") always has explicit hyphens
    between its parts by the time it reaches this step, and its SPEC
    component ("AC3N") legitimately mixes letters and digits WITHIN one
    token that must never be split. Only a tag that lost every separator
    (typically a raw instrument/equipment read like "FT8001"/"V803TF")
    reaches this step with no hyphen at all, which is exactly the case that
    needs one inserted.

    `strip_leading_digit_noise` defaults to False because line-list tags
    legitimately START with digits that are part of the tag itself (the
    pipe SIZE, e.g. "6-FL-AC3N-8183" must keep its leading "6") — stripping
    unconditionally would silently corrupt those. Pass True only for
    instrument-index tags, where a leaked 1-2 digit prefix (e.g.
    "3-BDHS-8001-TF") is a PDF row-merge artifact (a bled-in page/note
    number), never a real part of an ISA-5.1 instrument tag.

    The ORIGINAL tag string (unmodified) is always what gets shown in
    finding messages, `item_id`, and `evidence` — this normalized form is
    used ONLY as the matching key.
    """
    if not value:
        return ''
    s = str(value).upper()
    s = s.replace('\r', '\n').replace('\n', ' ').replace('\t', ' ')
    s = s.strip()
    s = _TAG_FRACTION_DOT_RE.sub(r'\1-\2', s)
    s = _TAG_SEPARATOR_RE.sub('-', s)
    s = _TAG_REPEATED_HYPHEN_RE.sub('-', s)
    s = s.strip('-')
    s = _TAG_JUNK_CHAR_RE.sub('', s)
    if '-' not in s:
        s = _TAG_LETTER_DIGIT_BOUNDARY_RE.sub('-', s)
    else:
        s = _TAG_TRAILING_SUFFIX_RE.sub(r'\1-\2', s)
    if strip_leading_digit_noise:
        m = _re.match(r'^\d{1,2}-([A-Z].*)$', s)
        if m:
            s = m.group(1)
    return s


def _looks_like_valid_tag(normalized: str) -> bool:
    """Validation gate applied AFTER normalize_tag(), before a key is used
    for matching (requirement: never let malformed noise create a false
    comparison finding). A real tag mixes letters and digits and has some
    minimum length; a normalized value that's purely numeric, purely
    alphabetic, or too short is far more likely to be leftover OCR/PDF
    noise than a genuine tag."""
    if len(normalized) < _MIN_VALID_TAG_LEN:
        return False
    has_letter = any(c.isalpha() for c in normalized)
    has_digit = any(c.isdigit() for c in normalized)
    return has_letter and has_digit


def _build_tag_index(items: List[Dict[str, Any]], key_field: str, source_label: str,
                      strip_leading_digit_noise: bool = False) -> Dict[str, Dict[str, Any]]:
    """Shared helper for every compare_with_*() function: normalize +
    validate every item's tag, skip (and log) anything that doesn't look
    like a real tag instead of letting it silently create a false
    missing/extra finding, and build the {normalized_key: item} index used
    for set-based matching. First occurrence wins on a duplicate key."""
    index: Dict[str, Dict[str, Any]] = {}
    for item in items:
        raw = item.get(key_field, '')
        if not raw:
            continue
        norm = normalize_tag(raw, strip_leading_digit_noise=strip_leading_digit_noise)
        if not _looks_like_valid_tag(norm):
            logger.warning(
                "[ComparisonEngine] Skipping unrecognizable %s tag %r (normalized to %r) — "
                "not used for matching to avoid a false finding",
                source_label, raw, norm,
            )
            continue
        if norm not in index:
            index[norm] = item
    return index


# Backward-compat alias — several other modules (apps.pid_verification_v2.
# services.orchestrator's tag_to_drawing mapping, etc.) import _normalize_tag
# by this name directly; keep it pointing at the same robust implementation
# rather than duplicating normalization logic under two names.
_normalize_tag = normalize_tag


# ═══════════════════════════════════════════════════════════════════════════
# FUZZY MISSING↔EXTRA PAIRING (deterministic, no AI cost)
# ═══════════════════════════════════════════════════════════════════════════
# compare_with_equipment_list() / compare_with_instrument_index() key their
# pid/ref lookups by normalize_tag(), so any tag that fails to normalize to
# EXACTLY the same string on both sides (an OCR misread character, a digit
# the model dropped, an extra site-symbol letter) falls straight through to
# "missing" on one side and "extra" on the other, forever — with nothing to
# reconcile them afterward. This is the same structural gap
# pid_checker_v2/services/equipment_cross_check.py had before its fix; this
# is the same fix, ported here for this comparison engine's data shapes.
#
# Deterministic, not AI: a candidate pair must share the same leading
# item-symbol letters AND the same first digit run, AND score >= threshold
# on whole-string similarity — cheap enough to run unconditionally, and
# specific enough that it only ever rescues a genuine near-miss (never
# merges two actually-different tags).
FUZZY_TAG_MATCH_THRESHOLD = 0.85

_FUZZY_TAG_ALPHA_RE   = re.compile(r'[A-Z]+')
_FUZZY_TAG_NUMERIC_RE = re.compile(r'\d+')


def _fuzzy_tag_alpha(tag: str) -> str:
    """Leading item-symbol letters, e.g. 'V' from 'V-803-TF'."""
    m = _FUZZY_TAG_ALPHA_RE.match(normalize_tag(tag))
    return m.group(0) if m else ''


def _fuzzy_tag_numeric(tag: str) -> str:
    """First digit run, e.g. '803' from 'V-803-TF'."""
    m = _FUZZY_TAG_NUMERIC_RE.search(normalize_tag(tag))
    return m.group(0) if m else ''


def _fuzzy_pair_missing_extra(
    pid_dict: Dict[str, Dict[str, Any]],
    ref_dict: Dict[str, Dict[str, Any]],
    missing: set,
    extra: set,
    label: str,
) -> List[Tuple[str, str, float]]:
    """Deterministically pair entries of `missing` (normalized ref-only
    keys) against `extra` (normalized pid-only keys) that are almost
    certainly the same tag misread/misformatted on one side. Mutates
    `missing`/`extra` IN PLACE, removing every paired key from both (so the
    caller's subsequent missing/extra finding loops and counts are
    automatically correct) — the caller is responsible for crediting each
    returned pair as a match (matched_count += len(pairs)).

    Returns the list of (missing_key, extra_key, similarity_score) pairs
    found, purely for logging/traceability.
    """
    if not missing or not extra:
        return []

    used_extra: set = set()
    pairs: List[Tuple[str, str, float]] = []

    for miss_key in sorted(missing):
        miss_tag = ref_dict[miss_key].get('tag', miss_key)
        miss_alpha = _fuzzy_tag_alpha(miss_tag)
        miss_num = _fuzzy_tag_numeric(miss_tag)

        best_key = None
        best_score = 0.0
        for ex_key in extra:
            if ex_key in used_extra:
                continue
            ex_tag = pid_dict[ex_key].get('tag', ex_key)
            if _fuzzy_tag_alpha(ex_tag) != miss_alpha:
                continue
            if _fuzzy_tag_numeric(ex_tag) != miss_num:
                continue
            score = SequenceMatcher(None, normalize_tag(miss_tag), normalize_tag(ex_tag)).ratio()
            if score > best_score:
                best_score = score
                best_key = ex_key

        if best_key is not None and best_score >= FUZZY_TAG_MATCH_THRESHOLD:
            used_extra.add(best_key)
            pairs.append((miss_key, best_key, best_score))

    for miss_key, ex_key, score in pairs:
        missing.discard(miss_key)
        extra.discard(ex_key)
        logger.info(
            "[ComparisonEngine] Fuzzy-matched %s: %r <-> %r (similarity %.2f) — "
            "resolved as MATCH instead of permanent missing/extra",
            label, ref_dict[miss_key].get('tag', miss_key), pid_dict[ex_key].get('tag', ex_key), score,
        )

    return pairs


# ═══════════════════════════════════════════════════════════════════════════
# SMART (AI) VALUE COMPARISON — resolves pairs the naive fuzzy_match()
# threshold can't confidently call, e.g. unit differences ("150 psig" vs
# "150"), format differences ("CS + LINING" vs "CS + Lining"), and ranges
# ("Min:60/Max:105" vs "60°F"). Purely additive: with no BYOK Claude key,
# every compare_with_*() function falls straight back to the original
# immediate fuzzy_match threshold decision — existing free-tier behavior is
# unchanged.
# ═══════════════════════════════════════════════════════════════════════════

# ─── Deterministic pre-processing (requirement: try this BEFORE ever
# spending an AI call) ──────────────────────────────────────────────────
# Unit words that annotate a value without changing the number itself —
# stripping them and comparing the bare numbers is always safe (e.g.
# "150 psig" and "150" are the same value; the unit was just implied).
_NO_OP_UNIT_WORDS = frozenset({
    'PSIG', 'PSI', 'BAR', 'BARG', 'KPA', 'MPA',
    'DEGF', 'DEGC', 'F', 'C', 'KG', 'LB', 'LBS', '',
    # Volume/area units (m³/m² folded to M3/M2 by _clean_for_comparison) —
    # these are a different physical quantity than the length units in
    # _UNIT_CONVERSION_TO_MM, so treat them as no-op (compare bare numbers)
    # rather than trying to convert them.
    'M3', 'M2', 'CM3', 'MM3', 'L', 'LITER', 'LITERS', 'GAL', 'GALLONS',
    # Pipe/line NOMINAL size — "6 IN" / "6 INCH" vs a bare "6" is the same
    # nominal pipe size, not a unit conversion (NPS is dimensionless by
    # convention here; no mm conversion is meaningful for a nominal size
    # label the way it is for a physical length like Length T/T).
    'IN', 'INCH', 'INCHES', 'NPS', 'NB', 'DN',
})

# Units that DO require a conversion factor before comparing against a
# bare number — keyed by unit -> multiplier to the base unit (mm; this
# codebase's dimension/length columns are in mm when no unit is given).
_UNIT_CONVERSION_TO_MM = {
    'M': 1000.0, 'METER': 1000.0, 'METERS': 1000.0,
    'MM': 1.0, 'MILLIMETER': 1.0, 'MILLIMETERS': 1.0,
    'CM': 10.0, 'CENTIMETER': 10.0, 'CENTIMETERS': 10.0,
}

_NUMERIC_RELATIVE_TOLERANCE = 0.01  # 1% — absorbs rounding in source docs
# Unit group allows a trailing digit (M3, M2, CM3, ...) so volume/area units
# like "m³" (folded to "M3" by _clean_for_comparison) still parse — a plain
# [A-Z]* group can't match past the digit and silently fails the whole regex.
_VALUE_NUM_UNIT_RE = re.compile(r'^(-?\d+(?:\.\d+)?)\s*([A-Z]*\d?)$')
_RANGE_RE = re.compile(r'MIN[:\s]*(-?\d+(?:\.\d+)?)\s*/?\s*MAX[:\s]*(-?\d+(?:\.\d+)?)')
# Two bare numbers separated by a slash, no MIN/MAX labels (e.g. "60 / 105")
# — some P&ID/reference cells express a range this way instead of spelling
# out "Min:"/"Max:".
_UNLABELED_RANGE_RE = re.compile(r'^(-?\d+(?:\.\d+)?)\s*/\s*(-?\d+(?:\.\d+)?)$')


def _clean_for_comparison(value: Any) -> str:
    """Requirement steps 1-2: uppercase, collapse whitespace, and fold
    common unicode unit characters (m³ -> M3, ° dropped) so formatting
    noise never causes a spurious AI call for something that's actually
    identical, e.g. 'CS + Lining' vs 'CS + LINING' or '327 m³' vs '327 M3'."""
    s = str(value or '').strip().upper()
    s = s.replace('³', '3').replace('²', '2').replace('°', ' ')
    # Inch marks (straight/curly double-quote, prime) — "6"" and "6″" for a
    # pipe size are the same nominal value as a bare "6" or "6 IN".
    s = s.replace('"', '').replace('”', '').replace('″', '')
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _parse_number_and_unit(s: str) -> Optional[Tuple[float, str]]:
    m = _VALUE_NUM_UNIT_RE.match(s)
    if not m:
        return None
    try:
        return float(m.group(1)), m.group(2).strip()
    except ValueError:
        return None


def _parse_range(s: str) -> Optional[Tuple[float, float]]:
    """Parse a Min/Max range out of a cleaned value string, whether labeled
    ("MIN: 60 / MAX: 105") or a bare two-number pair ("60 / 105"). Returns
    (lo, hi) with lo <= hi, or None if `s` isn't a range."""
    rm = _RANGE_RE.search(s)
    if rm:
        lo, hi = float(rm.group(1)), float(rm.group(2))
        return (lo, hi) if lo <= hi else (hi, lo)
    um = _UNLABELED_RANGE_RE.match(s)
    if um:
        lo, hi = float(um.group(1)), float(um.group(2))
        return (lo, hi) if lo <= hi else (hi, lo)
    return None


def _fmt_num(x: float) -> str:
    """15.0 -> '15', 15000.0 -> '15000', 13.2 -> '13.2' — trims a
    pointless trailing '.0' without mangling real decimals."""
    return f'{x:g}'


def _try_deterministic_value_match(pid_value: Any, ref_value: Any) -> Optional[str]:
    """Back-compat wrapper — see _try_deterministic_value_match_ex() for
    the full docstring and the explanatory-note variant used by the
    attribute-comparison UI."""
    status, _note = _try_deterministic_value_match_ex(pid_value, ref_value)
    return status


def _try_deterministic_value_match_ex(pid_value: Any, ref_value: Any) -> Tuple[Optional[str], Optional[str]]:
    """
    Requirement 3-4: try a simple, free comparison (case/whitespace
    normalization, then numeric comparison with unit conversion) BEFORE
    ever spending an AI call. Returns (status, note):
      - ('MATCH', note) when confidently resolvable this way. `note` is a
        short human-readable explanation of the equivalence (e.g. "15 M =
        15000 mm") when the match came from a real unit conversion or
        range check — something not obvious from the two raw values sitting
        side by side — and None when the two values were already
        identical after simple case/whitespace normalization (no
        explanation needed).
      - (None, None) to defer (to AI if a key is available, otherwise to
        the existing fuzzy-match-threshold fallback).

    Deliberately never returns a deterministic 'MISMATCH' itself — a rigid
    negative call here would recreate exactly the false-positive problem
    the whole smart-comparison feature exists to fix (see V-803-TF/line-
    list/instrument bugs fixed earlier). AI, or the pre-existing
    fuzzy-match fallback when no key is available, makes that call instead.

    Handles, in order:
      - exact match after case/whitespace/unicode-unit normalization
        ("CS + Lining" vs "CS + LINING"; "327 m³" vs "327 M3")
      - a bare number vs the same number with a no-op unit suffix
        ("150" vs "150 psig"; "-13.2" vs "-13.2 F")
      - a bare number vs an explicitly-unitted number needing conversion
        ("15000" vs "15.0 M"; assumes the bare side is millimeters)
      - a single value falling inside a "Min: X / Max: Y" range on the
        other side ("60 F" / "105 F" vs "Min: 60 / Max: 105")
    """
    a = _clean_for_comparison(pid_value)
    b = _clean_for_comparison(ref_value)
    if not a or not b:
        return None, None
    if a == b:
        return 'MATCH', None

    range_a = _parse_range(a)
    range_b = _parse_range(b)

    # --- Range vs range: both sides express Min/Max — compare the two
    # endpoints separately (requirement: an "OT Min" field should match the
    # reference range's Min, "OT Max" should match its Max — not just "is
    # some single value inside the range"). ---
    if range_a is not None and range_b is not None:
        lo_a, hi_a = range_a
        lo_b, hi_b = range_b
        if (abs(lo_a - lo_b) <= _NUMERIC_RELATIVE_TOLERANCE * max(1.0, abs(lo_a), abs(lo_b))
                and abs(hi_a - hi_b) <= _NUMERIC_RELATIVE_TOLERANCE * max(1.0, abs(hi_a), abs(hi_b))):
            return 'MATCH', f'Range endpoints match: Min {_fmt_num(lo_a)}, Max {_fmt_num(hi_a)}'

    # --- Range vs single value (e.g. a separate "OT Min"/"OT Max" field on
    # one side holding just a bare number, compared against the OTHER
    # endpoint of a combined "Min: X / Max: Y" range) ---
    for range_pair, single_str in ((range_a, b), (range_b, a)):
        if range_pair is None:
            continue
        lo, hi = range_pair
        single = _parse_number_and_unit(single_str)
        if single is not None:
            num, _unit = single
            if (lo - _NUMERIC_RELATIVE_TOLERANCE <= num <= hi + _NUMERIC_RELATIVE_TOLERANCE
                    or abs(num - lo) <= _NUMERIC_RELATIVE_TOLERANCE * max(1.0, abs(num), abs(lo))
                    or abs(num - hi) <= _NUMERIC_RELATIVE_TOLERANCE * max(1.0, abs(num), abs(hi))):
                return 'MATCH', f'{_fmt_num(num)} falls within range Min {_fmt_num(lo)} / Max {_fmt_num(hi)}'

    # --- Numeric comparison (no-op unit strip, then unit conversion) ---
    pa = _parse_number_and_unit(a)
    pb = _parse_number_and_unit(b)
    if pa is None or pb is None:
        return None, None
    num_a, unit_a = pa
    num_b, unit_b = pb

    def _close(x: float, y: float) -> bool:
        return abs(x - y) <= _NUMERIC_RELATIVE_TOLERANCE * max(1.0, abs(x), abs(y))

    if unit_a in _NO_OP_UNIT_WORDS and unit_b in _NO_OP_UNIT_WORDS:
        if _close(num_a, num_b):
            # Only one side reaches here with a genuinely blank unit vs a
            # named one (or two different no-op unit words) — two sides
            # already spelled identically would have hit the a == b exact
            # match above and never gotten this far, so this is always
            # explaining a real, if minor, formatting difference.
            if unit_a and not unit_b:
                note = f'{_fmt_num(num_a)} {unit_a} = {_fmt_num(num_b)} (unit implied)'
            elif unit_b and not unit_a:
                note = f'{_fmt_num(num_b)} {unit_b} = {_fmt_num(num_a)} (unit implied)'
            elif unit_a != unit_b:
                note = f'{_fmt_num(num_a)} {unit_a} = {_fmt_num(num_b)} {unit_b}'
            else:
                note = None
            return 'MATCH', note

    # Only meaningful once at least one side specifies a convertible unit —
    # two bare/no-op-unit numbers were already handled above.
    if unit_a in _UNIT_CONVERSION_TO_MM or unit_b in _UNIT_CONVERSION_TO_MM:
        mm_a = num_a * _UNIT_CONVERSION_TO_MM[unit_a] if unit_a in _UNIT_CONVERSION_TO_MM else (num_a if unit_a in _NO_OP_UNIT_WORDS else None)
        mm_b = num_b * _UNIT_CONVERSION_TO_MM[unit_b] if unit_b in _UNIT_CONVERSION_TO_MM else (num_b if unit_b in _NO_OP_UNIT_WORDS else None)
        if mm_a is not None and mm_b is not None and _close(mm_a, mm_b):
            # Note the side that actually carries the convertible unit —
            # that's the one whose equivalence to the bare mm number on
            # the other side isn't obvious at a glance.
            if unit_a in _UNIT_CONVERSION_TO_MM:
                note = f'{_fmt_num(num_a)} {unit_a} = {_fmt_num(mm_a)} mm'
            elif unit_b in _UNIT_CONVERSION_TO_MM:
                note = f'{_fmt_num(num_b)} {unit_b} = {_fmt_num(mm_b)} mm'
            else:
                note = None
            return 'MATCH', note

    return None, None


def _resolve_ambiguous_pairs(pending: List[Dict[str, Any]], api_key: Optional[str],
                              model: Optional[str] = None) -> List[Dict[str, Any]]:
    """Send every pending {'label','pid_value','ref_value', ...} pair to
    Claude in as few batched calls as possible (see ai_analysis.
    smart_compare_batch's SMART_COMPARE_BATCH_SIZE) and return one
    {'result','confidence','explanation'} dict per pending item, same
    order. Never raises — an AI failure degrades every pair to UNCERTAIN
    ("please verify manually") rather than losing the comparison."""
    if not pending or not api_key:
        return []
    try:
        from apps.pid_verification_v2.services.ai_analysis import smart_compare_batch
        return smart_compare_batch(
            [{'label': p['label'], 'pid_value': p['pid_value'], 'ref_value': p['ref_value']} for p in pending],
            api_key, model=model,
        )
    except Exception as exc:
        logger.warning('[ComparisonEngine] Smart value comparison failed (non-fatal): %s', exc, exc_info=True)
        return [
            {'result': 'UNCERTAIN', 'confidence': 'LOW', 'explanation': 'AI comparison unavailable — please verify manually.'}
            for _ in pending
        ]


def _evaluate_attribute(tag: str, comparison_type: str, label: str,
                         pid_value: Any, ref_value: Any, api_key: Optional[str],
                         pending_ai: List[Dict[str, Any]]) -> Tuple[str, Optional['ComparisonFinding']]:
    """Shared per-attribute evaluator used for every 'extra' attribute
    beyond the primary tag match (equipment/instrument 'service', 'range',
    etc.) — same match order as the inline 'type' blocks above: blank-side
    skip -> fuzzy threshold -> deterministic (case/unit/range) match -> AI
    (if a key is available, appended to `pending_ai`) -> immediate mismatch.

    Returns ('matched'|'pending'|'mismatch', finding_or_None). 'pending'
    means an entry was appended to `pending_ai` — the caller resolves it
    later via _resolve_ambiguous_pairs and applies the result itself.
    """
    if not pid_value or not ref_value:
        return 'matched', None
    similarity = fuzzy_match(str(pid_value), str(ref_value))
    if similarity >= COMPARISON_MATCH_THRESHOLD:
        return 'matched', None
    if _try_deterministic_value_match(pid_value, ref_value) == 'MATCH':
        return 'matched', None
    if api_key:
        pending_ai.append({
            'tag': tag, 'label': label, 'pid_value': pid_value, 'ref_value': ref_value,
        })
        return 'pending', None
    finding = ComparisonFinding(
        category='mismatch',
        comparison_type=comparison_type,
        item_id=tag,
        severity='major',
        issue_observed=f'{tag}: {label} mismatch between P&ID and reference',
        pid_value=pid_value,
        ref_value=ref_value,
        similarity=similarity,
        evidence=f'P&ID: {pid_value}, Reference: {ref_value}',
    )
    return 'mismatch', finding


def weighted_similarity(pid_item: Dict, ref_item: Dict, weights: Dict[str, float]) -> float:
    """
    Compute weighted similarity between P&ID item and reference item.
    
    Args:
        pid_item: Item extracted from P&ID
        ref_item: Item from reference document
        weights: Attribute weights configuration
    
    Returns:
        Overall similarity score 0.0-1.0
    """
    total_weight = 0.0
    weighted_sum = 0.0
    
    for attr, weight in weights.items():
        pid_val = str(pid_item.get(attr, '')).strip()
        ref_val = str(ref_item.get(attr, '')).strip()
        
        if pid_val or ref_val:
            similarity = fuzzy_match(pid_val, ref_val)
            weighted_sum += similarity * weight
            total_weight += weight
    
    if total_weight == 0:
        return 0.0
    
    return weighted_sum / total_weight


# ═══════════════════════════════════════════════════════════════════════════
# COMPARISON FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def compare_with_legend(
    pid_symbols: List[Dict[str, Any]],
    legend_data: Optional[Dict[str, Any]]
) -> ComparisonResult:
    """
    Compare P&ID extracted symbols with Legend sheet.
    
    Args:
        pid_symbols: Symbols/tags extracted from P&ID
        legend_data: Legend knowledge data (symbol definitions)
    
    Returns:
        ComparisonResult with legend-specific findings
    """
    findings = []
    
    # Handle case where legend is not available
    if not legend_data:
        return ComparisonResult(
            comparison_type='legend',
            total_pid_items=len(pid_symbols),
            total_ref_items=0,
            matched_count=0,
            missing_count=0,
            extra_count=0,
            mismatch_count=0,
            findings=[],
            summary='Legend sheet not available for comparison'
        )
    
    # Extract legend symbols from legend_data
    legend_symbols = legend_data.get('symbols', [])
    
    # Convert to sets for comparison
    pid_symbol_set = {s.get('symbol_type', '').strip().upper() for s in pid_symbols if s.get('symbol_type')}
    legend_symbol_set = {s.get('type', '').strip().upper() for s in legend_symbols if s.get('type')}
    
    matched = pid_symbol_set & legend_symbol_set
    missing = legend_symbol_set - pid_symbol_set
    extra = pid_symbol_set - legend_symbol_set
    
    # Generate findings for missing symbols
    for symbol in missing:
        findings.append(ComparisonFinding(
            category='missing',
            comparison_type='legend',
            item_id=symbol,
            severity='major',
            issue_observed=f'Symbol "{symbol}" is defined in Legend but not found on P&ID',
            pid_value=None,
            ref_value=symbol,
            similarity=0.0,
            evidence=f'Legend defines {symbol} but P&ID does not use it'
        ))
    
    # Generate findings for extra symbols (not in legend)
    for symbol in extra:
        findings.append(ComparisonFinding(
            category='extra',
            comparison_type='legend',
            item_id=symbol,
            severity='critical',
            issue_observed=f'Symbol "{symbol}" found on P&ID but not defined in Legend sheet',
            pid_value=symbol,
            ref_value=None,
            similarity=0.0,
            evidence=f'P&ID uses {symbol} which is not in approved Legend'
        ))
    
    return ComparisonResult(
        comparison_type='legend',
        total_pid_items=len(pid_symbol_set),
        total_ref_items=len(legend_symbol_set),
        matched_count=len(matched),
        missing_count=len(missing),
        extra_count=len(extra),
        mismatch_count=0,
        findings=findings,
        summary=f'{len(extra)} unapproved symbols, {len(missing)} legend symbols unused'
    )


def compare_with_line_list(
    pid_lines: List[Dict[str, Any]],
    line_list_data: List[Dict[str, Any]],
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> ComparisonResult:
    """
    Compare P&ID pipeline designations with Line List register.

    Args:
        pid_lines: Line tags extracted from P&ID
        line_list_data: Line list from database/Excel
        api_key: optional BYOK Claude key — when present, size values a
            plain string-equality check can't confidently call (unit
            differences, formatting) are resolved via smart_compare_batch()
            instead of an immediate mismatch. See module docstring.
        model: optional model override for the smart-compare call

    Returns:
        ComparisonResult with line list comparison findings
    """
    findings = []
    matched_count = 0
    mismatch_count = 0
    uncertain_count = 0
    pending_ai: List[Dict[str, Any]] = []

    # Build lookup dictionaries keyed by a normalization-tolerant tag key so
    # formatting differences (case/hyphen/whitespace) don't cause false
    # missing/extra findings. Original tag text is preserved on each item for
    # display in messages.
    pid_line_dict = _build_tag_index(pid_lines, 'text', 'P&ID line')
    ref_line_dict = _build_tag_index(line_list_data, 'line_tag', 'Line List')

    pid_tags = set(pid_line_dict.keys())
    ref_tags = set(ref_line_dict.keys())

    # Find exact matches
    exact_matches = pid_tags & ref_tags

    # Check for attribute mismatches in exact matches
    for norm_tag in exact_matches:
        pid_line = pid_line_dict[norm_tag]
        ref_line = ref_line_dict[norm_tag]
        tag = pid_line.get('text', '') or ref_line.get('line_tag', '')

        # Compare size
        pid_size = pid_line.get('size', '')
        ref_size = ref_line.get('size', '')

        if not pid_size or not ref_size or pid_size == ref_size:
            matched_count += 1
        elif _try_deterministic_value_match(pid_size, ref_size) == 'MATCH':
            # Free, no-AI resolution — case/format normalization or a unit
            # conversion already proved these are the same value
            # ("15.0 M" vs "15000 mm") without spending an API call.
            matched_count += 1
        elif api_key:
            # Still ambiguous after the deterministic pass — defer to AI
            # instead of an immediate false-positive mismatch.
            pending_ai.append({
                'norm_tag': norm_tag, 'tag': tag, 'kind': 'size',
                'label': f'Pipe size for line {tag}', 'pid_value': pid_size, 'ref_value': ref_size,
            })
        else:
            mismatch_count += 1
            findings.append(ComparisonFinding(
                category='mismatch',
                comparison_type='linelist',
                item_id=tag,
                severity='major',
                issue_observed=f'Line {tag}: Size mismatch between P&ID ({pid_size}) and Line List ({ref_size})',
                pid_value=pid_size,
                ref_value=ref_size,
                similarity=fuzzy_match(pid_size, ref_size),
                evidence=f'P&ID shows {pid_size}, Line List shows {ref_size}'
            ))

    # Resolve every AI-deferred pair in as few batched Claude calls as
    # possible (see _resolve_ambiguous_pairs / smart_compare_batch).
    ai_results = _resolve_ambiguous_pairs(pending_ai, api_key, model)
    for pending, ai in zip(pending_ai, ai_results):
        if ai['result'] == 'MATCH':
            matched_count += 1
        elif ai['result'] == 'MISMATCH':
            mismatch_count += 1
            findings.append(ComparisonFinding(
                category='mismatch',
                comparison_type='linelist',
                item_id=pending['tag'],
                severity='major',
                issue_observed=f"Line {pending['tag']}: Size mismatch (AI, {ai['confidence']} confidence) — {ai['explanation']}",
                pid_value=pending['pid_value'],
                ref_value=pending['ref_value'],
                similarity=0.0,
                evidence=f"AI comparison: {ai['explanation']}",
            ))
        else:  # UNCERTAIN
            uncertain_count += 1
            findings.append(ComparisonFinding(
                category='uncertain',
                comparison_type='linelist',
                item_id=pending['tag'],
                severity='minor',
                issue_observed=(
                    f"Line {pending['tag']}: AI could not determine if the sizes match "
                    f"(P&ID: {pending['pid_value']!r}, Line List: {pending['ref_value']!r}). "
                    f"Please verify manually."
                ),
                pid_value=pending['pid_value'],
                ref_value=pending['ref_value'],
                similarity=0.0,
                evidence=ai['explanation'] or 'AI confidence too low to decide.',
            ))

    # Find missing lines (in Line List but not on P&ID)
    missing = ref_tags - pid_tags
    for norm_tag in missing:
        ref_line = ref_line_dict[norm_tag]
        tag = ref_line.get('line_tag', '')
        findings.append(ComparisonFinding(
            category='missing',
            comparison_type='linelist',
            item_id=tag,
            severity='major',
            issue_observed=f'Line {tag} is registered in Line List but not found on P&ID',
            pid_value=None,
            ref_value=ref_line,
            similarity=0.0,
            evidence=f'Line List entry: {tag} ({ref_line.get("size", "")}) - {ref_line.get("service", "")}'
        ))
    
    # Find extra lines (on P&ID but not in Line List)
    extra = pid_tags - ref_tags
    for norm_tag in extra:
        pid_line = pid_line_dict[norm_tag]
        tag = pid_line.get('text', '')
        findings.append(ComparisonFinding(
            category='extra',
            comparison_type='linelist',
            item_id=tag,
            severity='critical',
            issue_observed=f'Line {tag} found on P&ID but not registered in Line List',
            pid_value=pid_line,
            ref_value=None,
            similarity=0.0,
            evidence=f'P&ID shows {tag} but Line List has no entry'
        ))
    
    return ComparisonResult(
        comparison_type='linelist',
        total_pid_items=len(pid_tags),
        total_ref_items=len(ref_tags),
        matched_count=matched_count,
        missing_count=len(missing),
        extra_count=len(extra),
        mismatch_count=mismatch_count,
        findings=findings,
        summary=(
            f'{len(extra)} unregistered lines, {len(missing)} lines not on P&ID, '
            f'{mismatch_count} mismatches' + (f', {uncertain_count} uncertain' if uncertain_count else '')
        ),
        uncertain_count=uncertain_count,
    )


def compare_with_equipment_list(
    pid_equipment: List[Dict[str, Any]],
    equipment_list_data: List[Dict[str, Any]],
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> ComparisonResult:
    """
    Compare P&ID equipment tags with Equipment Register.

    Args:
        pid_equipment: Equipment/tags extracted from P&ID
        equipment_list_data: Equipment register from database
        api_key: optional BYOK Claude key — when present, type/description
            pairs the naive fuzzy_match threshold can't confidently call are
            resolved via smart_compare_batch() instead of an immediate
            mismatch. See module docstring.
        model: optional model override for the smart-compare call

    Returns:
        ComparisonResult with equipment comparison findings
    """
    findings = []
    matched_count = 0
    mismatch_count = 0
    uncertain_count = 0
    pending_ai: List[Dict[str, Any]] = []

    # Build lookup dictionaries keyed by a normalization-tolerant tag key so
    # formatting differences (case/hyphen/whitespace) don't cause false
    # missing/extra findings. Original tag text is preserved on each item for
    # display in messages.
    pid_equip_dict = _build_tag_index(pid_equipment, 'tag', 'P&ID equipment')
    ref_equip_dict = _build_tag_index(equipment_list_data, 'tag', 'Equipment List')

    pid_tags = set(pid_equip_dict.keys())
    ref_tags = set(ref_equip_dict.keys())

    # Find exact matches
    exact_matches = pid_tags & ref_tags

    # Check for attribute mismatches
    for norm_tag in exact_matches:
        pid_eq = pid_equip_dict[norm_tag]
        ref_eq = ref_equip_dict[norm_tag]
        tag = pid_eq.get('tag', '') or ref_eq.get('tag', '')

        # Compare type/description
        pid_type = pid_eq.get('type', '')
        ref_type = ref_eq.get('type', '')

        if not pid_type or not ref_type:
            matched_count += 1
        else:
            similarity = fuzzy_match(pid_type, ref_type)
            if similarity >= COMPARISON_MATCH_THRESHOLD:
                matched_count += 1
            elif _try_deterministic_value_match(pid_type, ref_type) == 'MATCH':
                # Free, no-AI resolution (case/format normalization or a
                # unit conversion already proved these match).
                matched_count += 1
            elif api_key:
                # Still ambiguous after the deterministic pass (e.g. a
                # short P&ID type code vs a long register description) —
                # defer to AI instead of an immediate false-positive mismatch.
                pending_ai.append({
                    'norm_tag': norm_tag, 'tag': tag, 'kind': 'type',
                    'label': f'Equipment type/description for {tag}', 'pid_value': pid_type, 'ref_value': ref_type,
                })
            else:
                mismatch_count += 1
                findings.append(ComparisonFinding(
                    category='mismatch',
                    comparison_type='equipment',
                    item_id=tag,
                    severity='major',
                    issue_observed=f'Equipment {tag}: Type mismatch between P&ID and Equipment List',
                    pid_value=pid_type,
                    ref_value=ref_type,
                    similarity=similarity,
                    evidence=f'P&ID: {pid_type}, Equipment List: {ref_type}'
                ))

        # Compare service/duty (e.g. volume, flowrate, design duty) — this
        # is where free-text values like "327 m³" actually live in most
        # Equipment Register exports; previously only 'type' was ever
        # checked here, so a real match/mismatch on 'service' was silently
        # never evaluated at all.
        status, attr_finding = _evaluate_attribute(
            tag, 'equipment', f'Service/duty for {tag}',
            pid_eq.get('service', ''), ref_eq.get('service', ''), api_key, pending_ai,
        )
        if status == 'matched':
            matched_count += 1
        elif status == 'mismatch':
            mismatch_count += 1
            findings.append(attr_finding)

    # Resolve every AI-deferred pair in as few batched Claude calls as
    # possible (see _resolve_ambiguous_pairs / smart_compare_batch).
    ai_results = _resolve_ambiguous_pairs(pending_ai, api_key, model)
    for pending, ai in zip(pending_ai, ai_results):
        if ai['result'] == 'MATCH':
            matched_count += 1
        elif ai['result'] == 'MISMATCH':
            mismatch_count += 1
            findings.append(ComparisonFinding(
                category='mismatch',
                comparison_type='equipment',
                item_id=pending['tag'],
                severity='major',
                issue_observed=f"Equipment {pending['tag']}: {pending['label']} mismatch (AI, {ai['confidence']} confidence) — {ai['explanation']}",
                pid_value=pending['pid_value'],
                ref_value=pending['ref_value'],
                similarity=0.0,
                evidence=f"AI comparison: {ai['explanation']}",
            ))
        else:  # UNCERTAIN
            uncertain_count += 1
            findings.append(ComparisonFinding(
                category='uncertain',
                comparison_type='equipment',
                item_id=pending['tag'],
                severity='minor',
                issue_observed=(
                    f"Equipment {pending['tag']}: AI could not determine if {pending['label'].lower()} matches "
                    f"(P&ID: {pending['pid_value']!r}, Equipment List: {pending['ref_value']!r}). "
                    f"Please verify manually."
                ),
                pid_value=pending['pid_value'],
                ref_value=pending['ref_value'],
                similarity=0.0,
                evidence=ai['explanation'] or 'AI confidence too low to decide.',
            ))

    # Missing/extra candidates BEFORE fuzzy rescue.
    missing = ref_tags - pid_tags
    extra = pid_tags - ref_tags

    # Deterministically pair near-miss tags (OCR misread, dropped digit,
    # extra site-symbol letter) instead of leaving a genuine same-item read
    # permanently split into a "missing" + an unrelated-looking "extra".
    # See _fuzzy_pair_missing_extra() docstring above.
    fuzzy_pairs = _fuzzy_pair_missing_extra(pid_equip_dict, ref_equip_dict, missing, extra, 'equipment')
    matched_count += len(fuzzy_pairs)

    # Find missing equipment
    for norm_tag in missing:
        ref_eq = ref_equip_dict[norm_tag]
        tag = ref_eq.get('tag', '')
        findings.append(ComparisonFinding(
            category='missing',
            comparison_type='equipment',
            item_id=tag,
            severity='major',
            issue_observed=f'Equipment {tag} is in Equipment Register but not shown on P&ID',
            pid_value=None,
            ref_value=ref_eq,
            similarity=0.0,
            evidence=f'Equipment List: {tag} - {ref_eq.get("description", "")}'
        ))

    # Find extra equipment
    for norm_tag in extra:
        pid_eq = pid_equip_dict[norm_tag]
        tag = pid_eq.get('tag', '')
        findings.append(ComparisonFinding(
            category='extra',
            comparison_type='equipment',
            item_id=tag,
            severity='critical',
            issue_observed=f'Equipment {tag} found on P&ID but not in Equipment Register',
            pid_value=pid_eq,
            ref_value=None,
            similarity=0.0,
            evidence=f'P&ID shows {tag} ({pid_eq.get("type", "")}) but not registered'
        ))
    
    return ComparisonResult(
        comparison_type='equipment',
        total_pid_items=len(pid_tags),
        total_ref_items=len(ref_tags),
        matched_count=matched_count,
        missing_count=len(missing),
        extra_count=len(extra),
        mismatch_count=mismatch_count,
        findings=findings,
        summary=(
            f'{len(extra)} unregistered equipment, {len(missing)} not on P&ID, '
            f'{mismatch_count} mismatches' + (f', {uncertain_count} uncertain' if uncertain_count else '')
        ),
        uncertain_count=uncertain_count,
    )


def compare_with_instrument_index(
    pid_instruments: List[Dict[str, Any]],
    instrument_index_data: List[Dict[str, Any]],
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> ComparisonResult:
    """
    Compare P&ID instrument tags with Instrument Index.

    Args:
        pid_instruments: Instruments extracted from P&ID
        instrument_index_data: Instrument index from database
        api_key: optional BYOK Claude key — when present, type/service
            pairs the naive fuzzy_match threshold can't confidently call
            are resolved via smart_compare_batch() instead of an immediate
            mismatch. See module docstring.
        model: optional model override for the smart-compare call

    Returns:
        ComparisonResult with instrument comparison findings
    """
    findings = []
    matched_count = 0
    mismatch_count = 0
    uncertain_count = 0
    pending_ai: List[Dict[str, Any]] = []

    # Build lookup dictionaries keyed by a normalization-tolerant tag key so
    # formatting differences (case/hyphen/whitespace) don't cause false
    # missing/extra findings. Original tag text is preserved on each item for
    # display in messages.
    pid_instr_dict = _build_tag_index(pid_instruments, 'tag', 'P&ID instrument', strip_leading_digit_noise=True)
    ref_instr_dict = _build_tag_index(instrument_index_data, 'tag', 'Instrument Index', strip_leading_digit_noise=True)

    pid_tags = set(pid_instr_dict.keys())
    ref_tags = set(ref_instr_dict.keys())

    # Find exact matches
    exact_matches = pid_tags & ref_tags

    # Check for attribute mismatches
    for norm_tag in exact_matches:
        pid_ins = pid_instr_dict[norm_tag]
        ref_ins = ref_instr_dict[norm_tag]
        tag = pid_ins.get('tag', '') or ref_ins.get('tag', '')

        # Compare instrument type
        pid_type = pid_ins.get('type', '')
        ref_type = ref_ins.get('type', '')

        if not pid_type or not ref_type:
            matched_count += 1
        else:
            similarity = fuzzy_match(pid_type, ref_type)
            if similarity >= COMPARISON_MATCH_THRESHOLD:
                matched_count += 1
            elif _try_deterministic_value_match(pid_type, ref_type) == 'MATCH':
                # Free, no-AI resolution (case/format normalization or a
                # unit conversion already proved these match).
                matched_count += 1
            elif api_key:
                # Still ambiguous after the deterministic pass (e.g. a
                # short P&ID type code like "PT" vs a long Index
                # description) — defer to AI instead of an immediate
                # false-positive mismatch.
                pending_ai.append({
                    'norm_tag': norm_tag, 'tag': tag, 'kind': 'type',
                    'label': f'Instrument type/service for {tag}', 'pid_value': pid_type, 'ref_value': ref_type,
                })
            else:
                mismatch_count += 1
                findings.append(ComparisonFinding(
                    category='mismatch',
                    comparison_type='instrument',
                    item_id=tag,
                    severity='major',
                    issue_observed=f'Instrument {tag}: Type mismatch between P&ID and Instrument Index',
                    pid_value=pid_type,
                    ref_value=ref_type,
                    similarity=similarity,
                    evidence=f'P&ID: {pid_type}, Index: {ref_type}'
                ))

        # Compare service description — where free-text duty/service values
        # live (analogous to the equipment 'service' fix above).
        status, attr_finding = _evaluate_attribute(
            tag, 'instrument', f'Service description for {tag}',
            pid_ins.get('service', ''), ref_ins.get('service', ''), api_key, pending_ai,
        )
        if status == 'matched':
            matched_count += 1
        elif status == 'mismatch':
            mismatch_count += 1
            findings.append(attr_finding)

        # Compare operating range — previously declared in ATTRIBUTE_WEIGHTS
        # but never actually checked here. This is what makes "OT Min: 60 /
        # OT Max: 105" vs a reference "Min: 60 / Max: 105" range resolve
        # correctly (endpoint-vs-endpoint via _try_deterministic_value_match,
        # see _parse_range).
        status, attr_finding = _evaluate_attribute(
            tag, 'instrument', f'Operating range for {tag}',
            pid_ins.get('range', ''), ref_ins.get('range', ''), api_key, pending_ai,
        )
        if status == 'matched':
            matched_count += 1
        elif status == 'mismatch':
            mismatch_count += 1
            findings.append(attr_finding)

    # Resolve every AI-deferred pair in as few batched Claude calls as
    # possible (see _resolve_ambiguous_pairs / smart_compare_batch).
    ai_results = _resolve_ambiguous_pairs(pending_ai, api_key, model)
    for pending, ai in zip(pending_ai, ai_results):
        if ai['result'] == 'MATCH':
            matched_count += 1
        elif ai['result'] == 'MISMATCH':
            mismatch_count += 1
            findings.append(ComparisonFinding(
                category='mismatch',
                comparison_type='instrument',
                item_id=pending['tag'],
                severity='major',
                issue_observed=f"Instrument {pending['tag']}: {pending['label']} mismatch (AI, {ai['confidence']} confidence) — {ai['explanation']}",
                pid_value=pending['pid_value'],
                ref_value=pending['ref_value'],
                similarity=0.0,
                evidence=f"AI comparison: {ai['explanation']}",
            ))
        else:  # UNCERTAIN
            uncertain_count += 1
            findings.append(ComparisonFinding(
                category='uncertain',
                comparison_type='instrument',
                item_id=pending['tag'],
                severity='minor',
                issue_observed=(
                    f"Instrument {pending['tag']}: AI could not determine if {pending['label'].lower()} matches "
                    f"(P&ID: {pending['pid_value']!r}, Index: {pending['ref_value']!r}). "
                    f"Please verify manually."
                ),
                pid_value=pending['pid_value'],
                ref_value=pending['ref_value'],
                similarity=0.0,
                evidence=ai['explanation'] or 'AI confidence too low to decide.',
            ))

    # Missing/extra candidates BEFORE fuzzy rescue.
    missing = ref_tags - pid_tags
    extra = pid_tags - ref_tags

    # Deterministically pair near-miss tags (OCR misread, dropped digit,
    # extra site-symbol letter) instead of leaving a genuine same-item read
    # permanently split into a "missing" + an unrelated-looking "extra".
    # See _fuzzy_pair_missing_extra() docstring above.
    fuzzy_pairs = _fuzzy_pair_missing_extra(pid_instr_dict, ref_instr_dict, missing, extra, 'instrument')
    matched_count += len(fuzzy_pairs)

    # Find missing instruments
    for norm_tag in missing:
        ref_ins = ref_instr_dict[norm_tag]
        tag = ref_ins.get('tag', '')
        findings.append(ComparisonFinding(
            category='missing',
            comparison_type='instrument',
            item_id=tag,
            severity='major',
            issue_observed=f'Instrument {tag} is in Instrument Index but not shown on P&ID',
            pid_value=None,
            ref_value=ref_ins,
            similarity=0.0,
            evidence=f'Index: {tag} - {ref_ins.get("service", "")}'
        ))

    # Find extra instruments
    for norm_tag in extra:
        pid_ins = pid_instr_dict[norm_tag]
        tag = pid_ins.get('tag', '')
        findings.append(ComparisonFinding(
            category='extra',
            comparison_type='instrument',
            item_id=tag,
            severity='critical',
            issue_observed=f'Instrument {tag} found on P&ID but not in Instrument Index',
            pid_value=pid_ins,
            ref_value=None,
            similarity=0.0,
            evidence=f'P&ID shows {tag} ({pid_ins.get("type", "")}) but not in Index'
        ))
    
    return ComparisonResult(
        comparison_type='instrument',
        total_pid_items=len(pid_tags),
        total_ref_items=len(ref_tags),
        matched_count=matched_count,
        missing_count=len(missing),
        extra_count=len(extra),
        mismatch_count=mismatch_count,
        findings=findings,
        summary=(
            f'{len(extra)} unregistered instruments, {len(missing)} not on P&ID, '
            f'{mismatch_count} mismatches' + (f', {uncertain_count} uncertain' if uncertain_count else '')
        ),
        uncertain_count=uncertain_count,
    )


# ═══════════════════════════════════════════════════════════════════════════
# MAIN COMPARISON ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════

def run_all_comparisons(
    extraction: Dict[str, Any],
    legend_data: Optional[Dict[str, Any]] = None,
    line_list_data: Optional[List[Dict[str, Any]]] = None,
    equipment_list_data: Optional[List[Dict[str, Any]]] = None,
    instrument_index_data: Optional[List[Dict[str, Any]]] = None,
    ai_api_key: Optional[str] = None,
    ai_model: Optional[str] = None,
) -> Dict[str, ComparisonResult]:
    """
    Run all 4 comparison types and return consolidated results.

    Args:
        extraction: P&ID extraction result (tags, instruments, lines, etc.)
        legend_data: Legend knowledge data
        line_list_data: Line list reference data
        equipment_list_data: Equipment register data
        instrument_index_data: Instrument index data
        ai_api_key: optional BYOK Claude key — when present, attribute
            pairs the naive fuzzy-match threshold can't confidently call
            (unit differences, formatting, ranges) are resolved via Claude
            instead of an immediate mismatch/false-positive. Omitting it
            preserves the exact original deterministic behavior.
        ai_model: optional model override for the smart-compare calls

    Returns:
        Dictionary with comparison results for each type:
        {
            'legend': ComparisonResult,
            'linelist': ComparisonResult,
            'equipment': ComparisonResult,
            'instrument': ComparisonResult
        }
    """
    results = {}

    # Extract P&ID elements
    pid_symbols = extraction.get('symbols', [])
    pid_lines = extraction.get('line_tags', [])
    pid_equipment = extraction.get('equipment', [])
    pid_instruments = extraction.get('instruments', [])

    # Run each comparison
    logger.info('[ComparisonEngine] Running legend comparison...')
    results['legend'] = compare_with_legend(pid_symbols, legend_data)

    logger.info('[ComparisonEngine] Running line list comparison...')
    results['linelist'] = compare_with_line_list(pid_lines, line_list_data or [], api_key=ai_api_key, model=ai_model)

    logger.info('[ComparisonEngine] Running equipment comparison...')
    results['equipment'] = compare_with_equipment_list(pid_equipment, equipment_list_data or [], api_key=ai_api_key, model=ai_model)

    logger.info('[ComparisonEngine] Running instrument comparison...')
    results['instrument'] = compare_with_instrument_index(pid_instruments, instrument_index_data or [], api_key=ai_api_key, model=ai_model)

    # Log summary
    total_findings = sum(len(r.findings) for r in results.values())
    total_uncertain = sum(r.uncertain_count for r in results.values())
    logger.info(
        '[ComparisonEngine] Comparison complete: %d total discrepancies found across 4 comparison types%s',
        total_findings,
        f' ({total_uncertain} flagged uncertain for manual review)' if total_uncertain else '',
    )

    return results
