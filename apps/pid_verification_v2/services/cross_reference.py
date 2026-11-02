"""
Cross-Reference Service — P&ID Verification V2
==============================================

This module was referenced by tasks.py (_persist_segment_result /
_finalize_cross_reference) but never existed — both imports failed
non-fatally (logged "Cross-reference indexing failed … (non-fatal)" on
every run, observed live 29 Oct 2026), so multi-page tag indexing and
continuation/connector detection were silently disabled. Recreated here.

Three capabilities:
  1. detect_continuation_refs(raw_text) — find off-page / C&E connector
     references in one page's raw text: lines of the form
     "C : GFHSSB1 / HSSB-F5 / 1-EX-N000 / 13-PP-152-25370 / 6518"
     (cause & effect reference rows) and "TO/FROM <drawing no>" flags.
     Returns dicts: {drawing_no, connector_no, direction, service,
     in_register, text}.
  2. group_multi_page_tags(tag_rows) — group PIDVTagIndex rows by
     normalized tag across pages; returns tags appearing on >1 page.
  3. resolve_continuations(page_refs, known_pages) — try to resolve each
     page's continuation refs to another known page of the document
     (by sheet/drawing number); status confirmed / missing / unresolvable.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional

from .segmentation import _DRAWING_NO_RE

logger = logging.getLogger(__name__)

# Pipeline line designations (2"-D-6158-033842-X-N) embed drawing-number-
# like tails ('D-6158-033842-X-N') — mask them before scanning for
# connector drawing references. Mirrors extraction.py's pattern family.
_PIPELINE_DESIG_MASK_RE = re.compile(
    r'\d+(?:\.\d+)?\s*["\u201c\u201d\u2019\u2018\'`]{1,2}\s*-\s*'
    r'[A-Z]{1,4}\s*-\s*\d{3,6}\s*-\s*\d{4,8}(?:\s*-\s*[A-Z0-9]{1,8}){0,2}',
    re.IGNORECASE,
)

# C&E / cause-effect reference row: "C : ... / <drawing no> / <conn no>"
# or "E : ...". Direction letter C (cause) / E (effect) is kept raw.
_CE_ROW_RE = re.compile(r'^\s*([CE])\s*:\s*(.+)$', re.IGNORECASE)

# Off-sheet continuation flag: "TO 13-PP-152-20071" / "FROM 13-PP-152-20070"
_TO_FROM_RE = re.compile(r'^\s*(TO|FROM)\s+', re.IGNORECASE)

# Trailing bare serial (connector number) after the drawing number, e.g.
# the '6518' in "… / 13-PP-152-25370 / 6518".
_TRAILING_SERIAL_RE = re.compile(r'(\d{3,6})\s*$')

# Explicit sheet references, e.g. "SHT 2", "SHEET 3 OF 5".
_SHEET_REF_RE = re.compile(r'\bSHEET\s*(?:NO\.?|#)?\s*(\d+)\b|\bSHT\.?\s*(\d+)\b', re.IGNORECASE)


def detect_continuation_refs(raw_text: str) -> List[dict]:
    """
    Detect off-page connector / C&E reference rows in one page's raw text.

    Returns a list of dicts:
      {drawing_no, connector_no, direction, service, in_register, text}
    Fields with no extracted value are None — never fabricated.
    """
    refs: List[dict] = []
    if not raw_text:
        return refs

    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # Skip pipeline designations (their tails look like drawing numbers).
        masked = _PIPELINE_DESIG_MASK_RE.sub(' ', stripped)

        m_ce = _CE_ROW_RE.match(stripped)
        m_tf = _TO_FROM_RE.match(masked)
        direction = None
        if m_ce:
            direction = m_ce.group(1).upper()   # 'C' (cause) / 'E' (effect)
        elif m_tf:
            direction = m_tf.group(1).upper()   # 'TO' / 'FROM'

        dm = _DRAWING_NO_RE.search(masked)
        if not dm:
            continue
        # Require connector context: a C:/E: cause-effect row, a TO/FROM
        # flag, or a '/'-separated reference row. Bare drawing numbers in
        # running text (and OCR garbage that merely pattern-matches, e.g.
        # '12%50-FG-142001- AZAPOB-V' observed 29 Oct 2026) are excluded.
        if not (m_ce or m_tf or '/' in stripped):
            continue

        connector_no = None
        tail = masked[dm.end():]
        tm = _TRAILING_SERIAL_RE.search(tail)
        if tm:
            connector_no = tm.group(1)

        service = None
        if m_ce:
            parts = [p.strip() for p in m_ce.group(2).split('/') if p.strip()]
            service_parts = [
                p for p in parts
                if not _DRAWING_NO_RE.fullmatch(p) and not p.isdigit()
            ]
            service = ' / '.join(service_parts) or None

        refs.append({
            'drawing_no': dm.group(0),
            'connector_no': connector_no,
            'direction': direction,
            'service': service,
            'in_register': None,  # unknown without a connector register
            'text': stripped[:300],
        })

    # Dedupe identical rows (merged text sources repeat lines).
    seen = set()
    unique = []
    for r in refs:
        key = (r['drawing_no'], r['direction'], r['text'])
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def group_multi_page_tags(tag_rows: List[dict]) -> List[dict]:
    """
    Group PIDVTagIndex rows (dicts with tag/raw_tag/page_number/tag_type)
    by tag; return tags appearing on more than one page:
      [{'tag', 'pages': sorted unique page indices, 'tag_type'}]
    """
    by_tag: Dict[str, dict] = {}
    for row in tag_rows:
        tag = (row.get('tag') or '').strip()
        if not tag:
            continue
        entry = by_tag.setdefault(tag, {'tag': tag, 'pages': set(), 'tag_type': row.get('tag_type') or ''})
        entry['pages'].add(row.get('page_number', 0))
    out = [
        {**e, 'pages': sorted(e['pages'])}
        for e in by_tag.values()
        if len(e['pages']) > 1
    ]
    out.sort(key=lambda e: e['tag'])
    return out


def resolve_continuations(page_refs: Dict[int, List[dict]], known_pages: set) -> List[dict]:
    """
    Resolve each page's continuation refs against the document's pages.

    A ref is 'confirmed' when it names a sheet number that exists in
    known_pages, 'missing' when it names one that doesn't, and
    'unresolvable' when it carries no sheet number at all (e.g. only a
    drawing number we cannot map to a page).

    Returns [{'from_page', 'to_page', 'pattern', 'matched_text', 'status'}].
    """
    out: List[dict] = []
    for from_page, refs in (page_refs or {}).items():
        for ref in refs or []:
            text = ref.get('text', '') if isinstance(ref, dict) else str(ref)
            to_page: Optional[int] = None
            sm = _SHEET_REF_RE.search(text)
            if sm:
                sheet_no = int(sm.group(1) or sm.group(2))
                # page_index is 0-based; sheet numbers are 1-based.
                candidate = sheet_no - 1
                to_page = candidate
                status = 'confirmed' if candidate in known_pages else 'missing'
            else:
                status = 'unresolvable'
            out.append({
                'from_page': from_page,
                'to_page': to_page if status != 'unresolvable' else None,
                'pattern': (ref.get('direction') if isinstance(ref, dict) else None) or 'REF',
                'matched_text': text,
                'status': status,
            })
    return out
