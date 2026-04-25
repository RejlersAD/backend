"""
🎯 Breaker / Page-Connector Inference for Line List From / To
============================================================

P&ID drawings use small flag-shaped annotations called *breakers* (also known
as page connectors, off-page references, tie points, interface points) to
indicate where a pipe enters or leaves a drawing. Examples seen in real
project drawings:

    E3-SP-0385          off-page reference
    E3-US-8101          utility-station continuation
    TP-B-U3-H3-L-1021   tie-point
    IP-B-U3-H3-L-1030E  interface-point

The piping line tag and its surrounding breaker tags are placed close together
on the page. We exploit this:

    1. Re-open the PDF after extraction and harvest every word + bounding box
       on each page (PyMuPDF "words" mode, fast, vector-aware).
    2. Run configurable regex patterns to identify breaker tags AND line tags
       with their bboxes. Adjacent words on the same baseline are joined so
       multi-token tags survive PyMuPDF tokenisation.
    3. For every detected line tag, find the nearest breaker on the same
       horizontal band — left side → FROM, right side → TO (P&ID flow
       convention is left → right).
    4. Match back to each line_item by tag text and fill in `from_line` /
       `to_line` only where they're empty (we never overwrite values produced
       by the higher-confidence spatial / vision / geometric detectors).

This module is **additive** — no other extraction code is modified. Disable
it by simply not calling ``infer_breakers_for_lines``.

All thresholds and patterns are module-level constants (soft-coded) — edit
them freely to tune per-project behaviour.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Soft-coded breaker tag patterns. Add a regex here when a new project format
# appears — no other code change is needed.
#
# Two TIERS of breakers are recognised:
#
#  • LONG-FORM (high-confidence, cross-drawing references)
#       E3-SP-0385       off-sheet reference
#       E3-US-8101       utility-station continuation
#       TP-B-U3-H3-L-1021    tie-point
#       IP-B-U3-H3-L-1030E   interface-point
#       Generic XXX-NNNN dash-separated project tags
#    These are unambiguous and are matched anywhere on the page.
#
#  • SHORT-FORM (flag-glyph codes printed inside small flag/pentagon symbols)
#       A, AA, AAA, AAAA      single-letter continuity flags
#       AP33, AP7A, AP7E, AP7X   2-letter prefix + 2-char suffix
#       CA81, CA82, CA83, SR80   2-letter prefix + 2-digit number
#    Short flags are noisy (2-4 chars) so they only count as a breaker when
#    they sit very close to a line tag (see BREAKER_SHORT_MAX_HORIZONTAL_PT).
# ---------------------------------------------------------------------------
BREAKER_LONG_PATTERNS: List[str] = [
    # Off-page / sheet references:  E3-SP-0385, E3-US-8101, E3-US-8302
    r'\bE\d-[A-Z]{2,3}-\d{3,5}[A-Z]?\b',
    # Tie-points / interface-points: TP-B-U3-H3-L-1021, IP-B-U3-H3-L-1030E
    r'\b(?:TP|IP)-[A-Z0-9]+(?:-[A-Z0-9]+){2,6}\b',
    # Generic dash-separated project breakers:  ABCD-1234, EQX-007-12
    r'\b[A-Z]{2,4}-\d{3,5}(?:-\d{1,3})?\b',
]

BREAKER_SHORT_PATTERNS: List[str] = [
    # 2-letter prefix + 2 alphanumerics:  AP7A, AP7X, AP7E
    r'\b[A-Z]{2}\d[A-Z0-9]\b',
    # 2-letter prefix + 2-3 digits:       AP33, CA81, CA82, CA83, SR80
    r'\b[A-Z]{2}\d{2,3}\b',
    # 2 letters + 2 digits + trailing letter:  CA81A, AP33B
    r'\b[A-Z]{2}\d{2}[A-Z]\b',
    # Repeated capital letters:           A, AA, AAA, AAAA  (continuity flags)
    r'\b([A-Z])\1{0,3}\b',
]

_BREAKER_LONG_RE  = re.compile('|'.join(f'(?:{p})' for p in BREAKER_LONG_PATTERNS),  re.IGNORECASE)
_BREAKER_SHORT_RE = re.compile('|'.join(f'(?:{p})' for p in BREAKER_SHORT_PATTERNS), re.IGNORECASE)
_BREAKER_RE       = re.compile(
    '|'.join(f'(?:{p})' for p in BREAKER_LONG_PATTERNS + BREAKER_SHORT_PATTERNS),
    re.IGNORECASE,
)

# Short flags repeat all over the drawing (legend, BOM, notes), so we must
# exclude common false-positive words that match the regex by accident.
SHORT_FLAG_STOPWORDS: set = {
    'AND', 'FOR', 'THE', 'NOT', 'ALL', 'OUT', 'NEW', 'YES', 'NO', 'ON', 'OFF',
    'IN', 'OF', 'TO', 'BY', 'AT', 'AS', 'IS', 'BE', 'OR', 'IF', 'AN',
    'PI', 'PT', 'PSI', 'BAR', 'MIN', 'MAX', 'REV', 'REF', 'SHT', 'NTS',
    'IFC', 'IFD', 'IFR', 'EPC', 'PSA', 'EU3', 'AAA', 'BBB',  # known noise
}

# Line-tag patterns — used to find the bbox of each line tag on the page so
# we can do spatial proximity to breakers. Kept generic; we don't need 100%
# coverage here — only items we *can* anchor get breaker enrichment.
LINE_TAG_PATTERNS: List[str] = [
    # Onshore  2"-D-6152-033842-X-N    or  4"-63-IA-140061-A0KU01-V
    r'\d+(?:\.\d+)?["”]?-\d{1,3}-[A-Z]{1,4}-\d{4,8}-[A-Z0-9]{4,10}(?:-[A-Z0-9]+)?',
    # Offshore 604-LFG-3-AC2GA0-2012
    r'\d{2,4}-[A-Z]{1,4}-\d+["”]?-[A-Z0-9]{4,10}-\d{2,5}',
    # Industrial / project 2"-2600-FL-352-32070R-E
    r'\d+(?:/\d+)?["”]?-\d{3,5}-[A-Z]{1,4}-\d{2,5}-[A-Z0-9]{4,10}-[A-Z]',
]
_LINE_TAG_RE = re.compile('|'.join(f'(?:{p})' for p in LINE_TAG_PATTERNS), re.IGNORECASE)

# Vertical tolerance: how far above/below a line tag a breaker can sit and
# still be considered "on the same line". Expressed in PDF points (1 pt = 1/72").
# 60 pt ≈ 0.83" — generous enough for typical CAD spacing.
BREAKER_SAME_BAND_PT: float = 60.0

# Hard cap on horizontal distance for LONG-form breaker association
# (E3-SP-XXXX, TP-..., IP-...). These are unambiguous so we allow a wide reach.
BREAKER_LONG_MAX_HORIZONTAL_PT: float = 600.0

# Tighter horizontal cap for SHORT-form flag codes (AP33, CA81, A, AA…).
# Short codes appear all over the drawing — only those right next to the
# line tag are considered the actual breaker.
BREAKER_SHORT_MAX_HORIZONTAL_PT: float = 180.0

# When we infer From/To we tag the line with this method name so the existing
# `flow_detection_method` audit trail still works.
DETECTION_METHOD = 'breaker_inference'


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _word_box(w) -> Tuple[float, float, float, float, float, float]:
    """PyMuPDF word tuple → (x0, y0, x1, y1, cx, cy)."""
    x0, y0, x1, y1 = float(w[0]), float(w[1]), float(w[2]), float(w[3])
    return x0, y0, x1, y1, (x0 + x1) / 2.0, (y0 + y1) / 2.0


def _norm_tag(s: str) -> str:
    """Normalise a tag for fuzzy matching (uppercase, strip whitespace)."""
    return re.sub(r'\s+', '', s or '').upper()


def _baseline_groups(words) -> List[List]:
    """Group words by approximate y-baseline so we can join split tags."""
    if not words:
        return []
    # PyMuPDF "words": (x0, y0, x1, y1, text, block_no, line_no, word_no)
    # Block + line is the most reliable grouping when available.
    grouped: Dict[Tuple[int, int], List] = {}
    for w in words:
        key = (int(w[5]) if len(w) > 5 else 0,
               int(w[6]) if len(w) > 6 else 0)
        grouped.setdefault(key, []).append(w)
    return [sorted(ws, key=lambda x: x[0]) for ws in grouped.values()]


def _scan_line_for_pattern(line_words, regex) -> List[dict]:
    """
    Slide over consecutive words on a baseline and report every regex match.
    Returns a list of {text, x0, y0, x1, y1, cx, cy}.
    """
    if not line_words:
        return []
    hits: List[dict] = []
    # Pre-compute prefix concatenations so we can slice substrings cheaply.
    texts = [str(w[4] or '') for w in line_words]
    # Try every starting word; greedily extend up to N=8 words to form a tag.
    MAX_SPAN = 8
    used: List[bool] = [False] * len(line_words)
    for i in range(len(line_words)):
        if used[i]:
            continue
        joined = ''
        for j in range(i, min(i + MAX_SPAN, len(line_words))):
            joined = joined + texts[j] if joined else texts[j]
            m = regex.fullmatch(joined.strip()) or regex.search(joined)
            if m:
                tag = m.group(0)
                # Only accept if tag length matches our concatenation
                if _norm_tag(tag) == _norm_tag(joined[m.start():m.end()]):
                    span_words = line_words[i:j + 1]
                    x0 = min(float(w[0]) for w in span_words)
                    y0 = min(float(w[1]) for w in span_words)
                    x1 = max(float(w[2]) for w in span_words)
                    y1 = max(float(w[3]) for w in span_words)
                    hits.append({
                        'text': tag,
                        'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1,
                        'cx': (x0 + x1) / 2.0, 'cy': (y0 + y1) / 2.0,
                    })
                    for k in range(i, j + 1):
                        used[k] = True
                    break
    return hits


def _classify_breaker(tag: str) -> Optional[str]:
    """Return 'long', 'short', or None if `tag` is not a real breaker."""
    if not tag:
        return None
    norm = _norm_tag(tag)
    if norm in SHORT_FLAG_STOPWORDS:
        return None
    if _BREAKER_LONG_RE.fullmatch(norm) or _BREAKER_LONG_RE.search(norm):
        return 'long'
    if _BREAKER_SHORT_RE.fullmatch(norm):
        return 'short'
    return None


def _harvest_page(page, pattern_re) -> List[dict]:
    """Run a pattern over a PyMuPDF page and return all hits with bboxes.

    Each hit dict carries a ``tier`` key set to ``'long'`` or ``'short'`` so
    downstream logic can apply tier-specific spatial caps.
    """
    try:
        words = page.get_text("words") or []
    except Exception as e:
        logger.debug(f'[breaker] get_text("words") failed: {e}')
        return []
    if not words:
        return []
    hits: List[dict] = []

    # 1) Single-word matches
    for w in words:
        text = str(w[4] or '').strip()
        if not text:
            continue
        m = pattern_re.search(text)
        if not m:
            continue
        tag = m.group(0)
        tier = _classify_breaker(tag)
        if tier is None:
            continue
        x0, y0, x1, y1, cx, cy = _word_box(w)
        hits.append({'text': tag, 'tier': tier,
                     'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1,
                     'cx': cx, 'cy': cy})

    # 2) Multi-word matches (tags split across tokens) — only meaningful for
    # long-form patterns (short flags are always single tokens).
    for line_words in _baseline_groups(words):
        for h in _scan_line_for_pattern(line_words, pattern_re):
            tier = _classify_breaker(h['text'])
            if tier is None:
                continue
            h['tier'] = tier
            hits.append(h)

    # 3) Dedup by (text, rounded center)
    seen: set = set()
    unique: List[dict] = []
    for h in hits:
        key = (_norm_tag(h['text']), round(h['cx'] / 6.0), round(h['cy'] / 6.0))
        if key in seen:
            continue
        seen.add(key)
        unique.append(h)
    return unique


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def infer_breakers_for_lines(line_items: List[Dict], pdf_path: str) -> int:
    """
    Mutate each line item in-place adding `from_line` / `to_line` derived
    from nearby breaker tags. Only fills empty fields — never overwrites.

    Args:
        line_items: list of dicts produced by ``PIDLineExtractorV2`` (each
                    has at minimum ``original_detection`` / ``line_number``
                    and ``page``).
        pdf_path:   absolute path to the source PDF (still on disk).

    Returns:
        Number of items enriched (for logging only).
    """
    if not line_items or not pdf_path:
        return 0
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.warning('[breaker] PyMuPDF not installed; skipping inference')
        return 0
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        logger.warning(f'[breaker] cannot open PDF: {e}')
        return 0

    # Build a per-page index of breakers and line tags (only for pages that
    # actually contain detected line items — saves work on giant docs).
    pages_used = {it.get('page') for it in line_items if it.get('page')}
    per_page_breakers: Dict[int, List[dict]] = {}
    per_page_line_tags: Dict[int, List[dict]] = {}
    try:
        for page_num in pages_used:
            try:
                page = doc[int(page_num) - 1]
            except Exception:
                continue
            per_page_breakers[page_num] = _harvest_page(page, _BREAKER_RE)
            per_page_line_tags[page_num] = _harvest_page(page, _LINE_TAG_RE)
            logger.info(
                f'[breaker] page {page_num}: '
                f'{len(per_page_breakers[page_num])} breakers, '
                f'{len(per_page_line_tags[page_num])} line tags harvested'
            )
    finally:
        try:
            doc.close()
        except Exception:
            pass

    enriched = 0
    for item in line_items:
        page = item.get('page')
        if not page:
            continue
        breakers = per_page_breakers.get(page) or []
        line_tags = per_page_line_tags.get(page) or []
        if not breakers or not line_tags:
            continue

        existing_from = (item.get('from_line') or item.get('from_equipment') or '').strip()
        existing_to   = (item.get('to_line')   or item.get('to_equipment')   or '').strip()
        if existing_from and existing_to:
            continue

        item_tag = _norm_tag(item.get('original_detection') or item.get('line_number') or '')
        if not item_tag:
            continue

        # Find this line tag's bbox on the page (best fuzzy match).
        anchor: Optional[dict] = None
        for lt in line_tags:
            if _norm_tag(lt['text']) == item_tag:
                anchor = lt
                break
        if anchor is None:
            # Fallback: substring (handles stripped-trailing-suffix variants)
            for lt in line_tags:
                if item_tag in _norm_tag(lt['text']) or _norm_tag(lt['text']) in item_tag:
                    anchor = lt
                    break
        if anchor is None:
            continue

        # Filter breakers to same horizontal band, then apply tier-specific
        # horizontal caps (short flags must be very close; long-form references
        # may be further away).
        same_band: List[dict] = []
        for b in breakers:
            if abs(b['cy'] - anchor['cy']) > BREAKER_SAME_BAND_PT:
                continue
            cap = (BREAKER_LONG_MAX_HORIZONTAL_PT
                   if b.get('tier') == 'long'
                   else BREAKER_SHORT_MAX_HORIZONTAL_PT)
            if abs(b['cx'] - anchor['cx']) > cap:
                continue
            same_band.append(b)
        if not same_band:
            continue

        # Tier preference — long-form references win when both tiers are
        # present on the same side. We sort by (tier_rank, distance).
        def _rank(b):
            tier_rank = 0 if b.get('tier') == 'long' else 1
            return (tier_rank, abs(b['cx'] - anchor['cx']))

        left  = sorted([b for b in same_band if b['cx'] < anchor['x0']], key=_rank)
        right = sorted([b for b in same_band if b['cx'] > anchor['x1']], key=_rank)
        left_pick  = left[0]  if left  else None
        right_pick = right[0] if right else None

        changed = False
        if not existing_from and left_pick:
            item['from_line'] = left_pick['text'].upper()
            changed = True
        if not existing_to and right_pick:
            item['to_line'] = right_pick['text'].upper()
            changed = True
        if changed:
            item.setdefault('flow_detection_method', DETECTION_METHOD)
            enriched += 1

    if enriched:
        logger.info(f'[breaker] enriched From/To on {enriched}/{len(line_items)} line items')
    else:
        logger.info('[breaker] no items enriched (no breaker proximity found)')
    return enriched
