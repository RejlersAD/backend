"""
Master Index Service
--------------------

Column-class dispatcher for the NONTEF Master Index workflow.

This module WRAPS `extractor.py` without touching its regex patterns or
format-specific readers. All behaviour is driven by two JSON config files:

    config/master_index_template.json  - column schema + classes + rules
    config/document_taxonomy.json      - type/sub-type/discipline lookup

Column classes
--------------
* auto_serial    - system-assigned 1-based row index
* file_derived   - read from the file object (name, path, ext, page count)
* batch_default  - taken directly from the batch's ``batch_defaults`` dict
* ai_extract     - regex / taxonomy / keyword extraction from file text
* derived        - computed from another column via a named rule

The dispatcher returns a plain ``dict`` keyed by column ``key`` — the exact
shape stored in ``NonTeffBatchItem.fields`` and consumed by the exporter.
"""

from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional

from .extractor import (
    DATE_PATTERN,
    DOCUMENT_NO_PATTERN,
    EQUIPMENT_NO_PATTERN,
    REVISION_PATTERN,
    _first_match,
    _all_matches,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SOFT-CODED paths & constants
# ---------------------------------------------------------------------------

_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config')
TEMPLATE_PATH = os.path.join(_CONFIG_DIR, 'master_index_template.json')
TAXONOMY_PATH = os.path.join(_CONFIG_DIR, 'document_taxonomy.json')
PATTERNS_PATH = os.path.join(_CONFIG_DIR, 'extraction_patterns.json')

# File format → internal format key (kept separate from views.py on purpose
# so the service stays self-contained and testable).
_FORMAT_BY_EXT = {
    '.pdf':  'pdf',
    '.xlsx': 'excel', '.xls': 'excel',
    '.docx': 'word',  '.doc': 'word',
    '.dwg':  'autocad', '.dxf': 'autocad',
}

# Default cap for text snippets scanned by AI extractors (keeps things fast).
_MAX_SCAN_CHARS = 20_000

# Keyword → status label (mirror of extractor.NON_TEFF_STATUS_KEYWORDS but
# canonicalised).
_STATUS_KEYWORDS = [
    ('issued for construction', 'IFC'),
    ('issued for approval',     'IFA'),
    ('issued for comment',      'IFR'),
    ('issued for review',       'IFR'),
    ('for information',         'FOR INFORMATION'),
    ('for approval',            'IFA'),
    ('approved',                'APPROVED'),
    ('preliminary',             'PRELIMINARY'),
    ('draft',                   'DRAFT'),
]

# Title-line heuristic: first non-noise line of 5..90 chars containing letters.
_TITLE_NOISE = re.compile(r'^(page|rev|revision|date|sheet|of)\b', re.IGNORECASE)

# Unit-code patterns (e.g. "Unit 12", "UNIT-05", "U05")
_UNIT_PATTERN = re.compile(r'\b(?:UNIT[-\s]?([0-9]{1,3})|U([0-9]{2,3}))\b', re.IGNORECASE)


# ---------------------------------------------------------------------------
# Config loaders (cached — live-reload by clearing the cache during tests)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def load_template() -> Dict[str, Any]:
    with open(TEMPLATE_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_taxonomy() -> Dict[str, Any]:
    with open(TAXONOMY_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_patterns() -> Dict[str, Any]:
    """Load soft-coded extraction patterns. Compiled on demand, cached."""
    with open(PATTERNS_PATH, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    # Pre-compile each pattern for speed
    flag_map = {
        'IGNORECASE': re.IGNORECASE,
        'MULTILINE':  re.MULTILINE,
        'DOTALL':     re.DOTALL,
    }
    compiled: Dict[str, List[Dict[str, Any]]] = {}
    for field, entries in cfg.get('patterns', {}).items():
        out = []
        for e in entries:
            flags = 0
            for f_name in e.get('flags', []):
                flags |= flag_map.get(f_name, 0)
            try:
                out.append({
                    'regex': re.compile(e['pattern'], flags),
                    'group': int(e.get('group', 1)),
                    'mode':  e.get('mode', 'first'),
                })
            except re.error:
                logger.exception('Invalid pattern for field %s: %s', field, e.get('pattern'))
        compiled[field] = out
    return {
        'compiled':   compiled,
        'stop_words': {w.upper() for w in cfg.get('stop_words', [])},
    }


def get_columns() -> List[Dict[str, Any]]:
    return load_template()['columns']


def get_na_value() -> str:
    return load_template().get('default_na_value', 'NA')


def get_limits() -> Dict[str, Any]:
    return load_template().get('limits', {})


def get_batch_default_hints() -> Dict[str, Any]:
    return load_template().get('batch_default_hints', {})


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def detect_format(file_name: str) -> Optional[str]:
    return _FORMAT_BY_EXT.get(os.path.splitext(file_name.lower())[1])


def read_file_text(file_path: str, fmt: Optional[str] = None) -> str:
    """
    Read raw text from a file for AI extraction.

    Reuses the same libraries that extractor.py uses, but concatenates pages
    into one string capped at _MAX_SCAN_CHARS for responsive extraction.
    """
    fmt = fmt or detect_format(file_path)
    if not fmt:
        return ''
    try:
        if fmt == 'pdf':
            import pdfplumber
            chunks: List[str] = []
            with pdfplumber.open(file_path) as pdf:
                for p in pdf.pages:
                    chunks.append(p.extract_text() or '')
                    if sum(len(c) for c in chunks) > _MAX_SCAN_CHARS:
                        break
            return '\n'.join(chunks)[:_MAX_SCAN_CHARS]
        if fmt == 'excel':
            import openpyxl
            wb = openpyxl.load_workbook(file_path, data_only=True, read_only=True)
            out: List[str] = []
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    out.append(' '.join(str(c) for c in row if c is not None))
                    if sum(len(x) for x in out) > _MAX_SCAN_CHARS:
                        break
                if sum(len(x) for x in out) > _MAX_SCAN_CHARS:
                    break
            return '\n'.join(out)[:_MAX_SCAN_CHARS]
        if fmt == 'word':
            import docx
            doc = docx.Document(file_path)
            return '\n'.join(p.text for p in doc.paragraphs)[:_MAX_SCAN_CHARS]
    except Exception:
        logger.exception('read_file_text failed for %s', file_path)
    return ''


def pdf_page_count(file_path: str) -> Optional[int]:
    try:
        import pdfplumber
        with pdfplumber.open(file_path) as pdf:
            return len(pdf.pages)
    except Exception:
        return None


def detect_paper_size(file_path: str, fmt: Optional[str]) -> str:
    """
    Best-effort paper-size code (A4/A3/A2/A1/A0) inferred from PDF page box.
    Returns empty string when unavailable.
    """
    if fmt != 'pdf':
        return ''
    try:
        import pdfplumber
        with pdfplumber.open(file_path) as pdf:
            if not pdf.pages:
                return ''
            page = pdf.pages[0]
            w_mm = float(page.width) * 25.4 / 72.0
            h_mm = float(page.height) * 25.4 / 72.0
            short, long_ = sorted((w_mm, h_mm))
            # ISO 216 nominal sizes, with ±5 mm tolerance
            iso = {'A4': (210, 297), 'A3': (297, 420), 'A2': (420, 594),
                   'A1': (594, 841), 'A0': (841, 1189)}
            for name, (s, l_) in iso.items():
                if abs(short - s) <= 5 and abs(long_ - l_) <= 5:
                    return name
    except Exception:
        pass
    return ''


# ---------------------------------------------------------------------------
# AI extractors
# ---------------------------------------------------------------------------

def _extract_title(text: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if 5 <= len(s) <= 90 and re.search(r'[A-Za-z]', s) and not _TITLE_NOISE.match(s):
            # Skip bare document numbers
            if DOCUMENT_NO_PATTERN.fullmatch(s):
                continue
            return s
    return ''


def _extract_status(text: str) -> str:
    low = text.lower()
    for kw, label in _STATUS_KEYWORDS:
        if kw in low:
            return label
    return ''


def _extract_unit(text: str) -> str:
    m = _UNIT_PATTERN.search(text)
    if not m:
        return ''
    return f"U{(m.group(1) or m.group(2)).zfill(2)}"


def _classify_type(text: str, taxonomy: Dict[str, Any]) -> str:
    """
    Simple keyword classifier: return the first document_type whose key appears
    in the text (case-insensitive). Falls back to discipline keywords.
    """
    if not text:
        return ''
    low = text.lower()
    for t in taxonomy.get('document_types', {}):
        if t.lower() in low:
            return t
    return ''


def _narrow_subtype(text: str, parent_type: str, taxonomy: Dict[str, Any]) -> str:
    if not parent_type or not text:
        return ''
    low = text.lower()
    for sub in taxonomy.get('document_types', {}).get(parent_type, []):
        if sub and sub.lower() in low:
            return sub
    return ''


def _pattern_lookup(field_key: str, text: str) -> str:
    """
    Soft-coded pattern-based extractor. Looks up patterns by field_key in
    extraction_patterns.json and returns the first / all matches filtered by
    the configured stop-words.
    """
    if not text or not field_key:
        return ''
    cfg = load_patterns()
    entries = cfg['compiled'].get(field_key, [])
    stop = cfg['stop_words']
    for entry in entries:
        regex = entry['regex']
        group = entry['group']
        mode  = entry['mode']
        if mode == 'all_csv':
            hits = []
            seen = set()
            for m in regex.finditer(text):
                try:
                    val = (m.group(group) or '').strip().rstrip('.,;:')
                except IndexError:
                    continue
                if not val or val.upper() in stop or val.upper() in seen:
                    continue
                seen.add(val.upper())
                hits.append(val)
            if hits:
                return ','.join(hits)
        else:  # 'first'
            for m in regex.finditer(text):
                try:
                    val = (m.group(group) or '').strip().rstrip('.,;:')
                except IndexError:
                    continue
                if val and val.upper() not in stop:
                    return val
    return ''


# ---------------------------------------------------------------------------
# Column-class dispatcher
# ---------------------------------------------------------------------------

def _value_file_derived(column: Dict[str, Any], *, file_name: str,
                         relative_path: str, file_path: str, fmt: str) -> str:
    key = column['key']
    if key == 'file_name':
        return os.path.splitext(file_name)[0]
    if key == 'full_path':
        return relative_path or file_name
    if key == 'file_format':
        return os.path.splitext(file_name)[1].lstrip('.').upper()
    if key == 'no_of_sheets':
        pages = pdf_page_count(file_path) if fmt == 'pdf' else None
        return str(pages) if pages else ''
    if key == 'paper_size':
        return detect_paper_size(file_path, fmt)
    return ''


def _value_ai_extract(column: Dict[str, Any], *, text: str, file_name: str,
                      taxonomy: Dict[str, Any], accum: Dict[str, Any]) -> str:
    extractor = column.get('extractor')
    if extractor == 'filename_stem':
        return os.path.splitext(file_name)[0]
    if extractor == 'taxonomy_classifier':
        return _classify_type(text, taxonomy)
    if extractor == 'taxonomy_narrow':
        return _narrow_subtype(text, accum.get('document_type', ''), taxonomy)
    if extractor == 'title_scan':
        return _extract_title(text)
    if extractor == 'date_any':
        return _first_match(DATE_PATTERN, text)
    if extractor == 'rev_token':
        return _first_match(REVISION_PATTERN, text)
    if extractor == 'status_keyword':
        return _extract_status(text)
    if extractor == 'unit_code':
        # Return bare numeric unit code (matches reference format: "43" not "U43")
        m = _UNIT_PATTERN.search(text or '')
        if m:
            return (m.group(1) or m.group(2) or '').lstrip('0') or '0'
        return _pattern_lookup('unit', text)
    if extractor == 'equipment_tag':
        return _all_matches(EQUIPMENT_NO_PATTERN, text)
    if extractor == 'pattern_lookup':
        return _pattern_lookup(column['key'], text)
    # Fallback: try pattern_lookup using the column key — lets us enable
    # extraction on any ai_extract column just by adding patterns to JSON.
    return _pattern_lookup(column['key'], text)


def _value_batch_or_extract(column: Dict[str, Any], *, batch_defaults: Dict[str, Any],
                             text: str, na_value: str) -> str:
    """
    Hybrid class: prefer the batch_default value when meaningfully set;
    otherwise fall back to pattern extraction on document text.
    """
    key = column['key']
    bd = (batch_defaults.get(key) or '').strip()
    if bd and bd.upper() != na_value.upper():
        return bd
    # Try per-field patterns
    return _pattern_lookup(key, text)


def _value_derived(column: Dict[str, Any], *, accum: Dict[str, Any],
                   taxonomy: Dict[str, Any]) -> str:
    rule = column.get('rule')
    source = accum.get(column.get('derive_from', ''), '')
    if rule == 'type_to_discipline':
        return taxonomy.get('type_to_discipline', {}).get(source, '')
    if rule == 'yn_if_present':
        return 'Y' if source and str(source).strip().upper() not in ('', 'NA') else 'N'
    return ''


def build_row(*, row_index: int, file_name: str, relative_path: str,
              file_path: str, batch_defaults: Dict[str, Any]) -> Dict[str, Any]:
    """
    Main entry point: produce a fully-populated Master Index row for a file.

    Parameters
    ----------
    row_index : 1-based index within the batch (for SR NO).
    file_name : basename on disk.
    relative_path : path relative to the uploaded folder root.
    file_path : absolute path for reading content.
    batch_defaults : column.key -> value applied to batch_default columns.

    Returns
    -------
    dict keyed by column.key. Values are always strings.
    """
    columns = get_columns()
    taxonomy = load_taxonomy()
    na = get_na_value()
    fmt = detect_format(file_name) or ''

    text = read_file_text(file_path, fmt) if fmt in ('pdf', 'excel', 'word') else ''

    row: Dict[str, Any] = {}
    # Two-pass: first resolve non-derived columns so derived rules can read them.
    for col in columns:
        cls = col.get('class')
        key = col['key']
        try:
            if cls == 'auto_serial':
                value = str(row_index)
            elif cls == 'file_derived':
                value = _value_file_derived(
                    col, file_name=file_name, relative_path=relative_path,
                    file_path=file_path, fmt=fmt,
                )
            elif cls == 'batch_default':
                value = batch_defaults.get(key, '')
            elif cls == 'batch_or_extract':
                value = _value_batch_or_extract(
                    col, batch_defaults=batch_defaults, text=text, na_value=na,
                )
            elif cls == 'ai_extract':
                value = _value_ai_extract(
                    col, text=text, file_name=file_name,
                    taxonomy=taxonomy, accum=row,
                )
            elif cls == 'derived':
                value = ''  # filled in second pass
            else:
                value = ''
        except Exception:
            logger.exception('Column %s failed', key)
            value = ''
        row[key] = '' if value is None else str(value).strip()

    # Second pass: derived columns (may reference values above).
    for col in columns:
        if col.get('class') != 'derived':
            continue
        try:
            row[col['key']] = str(_value_derived(col, accum=row, taxonomy=taxonomy)).strip()
        except Exception:
            logger.exception('Derived column %s failed', col['key'])
            row[col['key']] = ''

    # NA fallback — applied to ai_extract, derived, and batch_or_extract columns.
    for col in columns:
        if col.get('class') in ('ai_extract', 'derived', 'batch_or_extract'):
            if not row.get(col['key']):
                row[col['key']] = col.get('fallback', na)

    return row
