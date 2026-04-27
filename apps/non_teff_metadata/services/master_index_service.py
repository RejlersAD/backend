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

# ---------------------------------------------------------------------------
# SOFT-CODED text-quality config — used to reject OCR garbage.
#
# Symptoms in scanned PDFs (e.g. "^5 - 50/ A Po A^ -p AyfA=>^ yoyAAp:>yA77^"):
#   • high ratio of non-alphanumeric chars
#   • very few vowels relative to consonants
#   • lots of single-letter "words"
#   • unusual punctuation runs (^^, =>, :>)
# Each threshold below tunes one heuristic. Lower numbers = stricter.
# ---------------------------------------------------------------------------
TEXT_QUALITY_CONFIG = {
    'min_alpha_ratio':         0.55,   # at least 55% letters/digits
    'min_letter_ratio':        0.45,   # at least 45% letters (digits + alpha alone is suspicious)
    'min_vowel_ratio':         0.18,   # vowels / total letters
    'max_special_ratio':       0.30,   # punctuation + symbols cap
    'min_avg_word_len':        2.5,    # average alphabetic-word length
    'max_single_letter_ratio': 0.35,   # single-letter "words" cap
    'forbidden_runs':          re.compile(r'[\^~`]{2,}|=>{2,}|:>|<>{2,}|[\^=]{3,}'),
    # Any single ^, ~, `, |, \, =, < or > embedded inside an alphabetic
    # token is a strong OCR-garbage signal (e.g. "t-i^APiM", "AyfA=>").
    'junk_in_word':            re.compile(r"[A-Za-z][\^~`|\\=<>][A-Za-z]|[A-Za-z][\^~`|\\=<>]+"),
    'max_junk_word_ratio':     0.20,   # how many tokens may carry junk
    'min_length':              5,
    'max_length':              120,
}
# Strip / collapse control characters, mojibake, weird whitespace.
_CONTROL_CHARS    = re.compile(r'[\x00-\x08\x0b-\x1f\x7f-\x9f]')
_MULTI_SPACE      = re.compile(r'\s{2,}')
_LEADING_TRAILING_JUNK = re.compile(r'^[^A-Za-z0-9]+|[^A-Za-z0-9.)]+$')
_VOWEL_SET        = set('aeiouAEIOU')


def _normalize_text(text: str) -> str:
    """Strip control chars, collapse whitespace, trim leading/trailing junk."""
    if not text:
        return ''
    s = _CONTROL_CHARS.sub(' ', text)
    s = _MULTI_SPACE.sub(' ', s).strip()
    s = _LEADING_TRAILING_JUNK.sub('', s)
    return s


def _is_clean_text(text: str, cfg: Dict[str, Any] = TEXT_QUALITY_CONFIG) -> bool:
    """
    Heuristic OCR-garbage detector. Returns True only when the line looks
    like real human-readable text. All thresholds are soft-coded above.
    """
    if not text:
        return False
    n = len(text)
    if n < cfg['min_length'] or n > cfg['max_length']:
        return False
    if cfg['forbidden_runs'].search(text):
        return False

    alnum   = sum(1 for c in text if c.isalnum())
    letters = sum(1 for c in text if c.isalpha())
    vowels  = sum(1 for c in text if c in _VOWEL_SET)
    spaces  = text.count(' ')
    special = n - alnum - spaces

    if alnum / n < cfg['min_alpha_ratio']:           return False
    if letters / n < cfg['min_letter_ratio']:        return False
    if special / n > cfg['max_special_ratio']:       return False
    if letters and vowels / letters < cfg['min_vowel_ratio']:
        return False

    # Word-level checks (ignore tokens that are pure punctuation).
    words = [w for w in re.split(r'\s+', text) if any(c.isalpha() for c in w)]
    if not words:
        return False
    avg_word_len = sum(len(w) for w in words) / len(words)
    if avg_word_len < cfg['min_avg_word_len']:       return False
    single_letter = sum(1 for w in words if len(w) == 1)
    if single_letter / len(words) > cfg['max_single_letter_ratio']:
        return False
    # Reject tokens with embedded junk like "t-i^APiM", "AyfA=>".
    junk_words = sum(1 for w in words if cfg['junk_in_word'].search(w))
    if junk_words / len(words) > cfg['max_junk_word_ratio']:
        return False

    return True


# ---------------------------------------------------------------------------
# SOFT-CODED value-level junk filter — used by every pattern_lookup field
# (document_no, drawing_no, subject, project_title, etc.).
#
# Looser than `_is_clean_text` so legitimate short tokens like 'PT-1234',
# '2"-P-1001-A1A', 'P16093-PR-PFD-001' still pass, but values containing
# OCR-noise characters (^, \, weird angle brackets, multiple > or <) are
# rejected.
# ---------------------------------------------------------------------------
VALUE_JUNK_CONFIG = {
    # Any of these characters anywhere in a value = reject. They almost
    # never appear in real engineering field values.
    'forbidden_chars':       set('^~`|\\'),
    # Run patterns that signal mojibake even when individual chars are valid.
    'forbidden_run_pattern': re.compile(r'>\s*[A-Za-z]|[A-Za-z]\s*<|=>{1,}|:>|<>{2,}|[<>]{2,}'),
    # Reject values where >40% of chars are non-alphanumeric (excluding spaces,
    # hyphens, slashes, dots, parens, ampersands and quotes which are valid).
    'allowed_specials':      set(' -_./()&\'",:'),
    'max_junk_char_ratio':   0.20,
    # Minimum letters or digits (rejects pure-symbol values).  Set to 1 so
    # legitimate single-character codes still pass — e.g. revision "A",
    # class_review "2", review code "B".  The forbidden_chars / junk_ratio
    # / forbidden_run_pattern guards continue to block real noise.
    'min_alnum':             1,
}


def _is_clean_value(val: str, cfg: Dict[str, Any] = VALUE_JUNK_CONFIG) -> bool:
    """
    Lightweight junk gate for individual field values returned by regex
    extractors. Returns False for OCR-noise like 'DRAWING Nos 2^2-\\^.- OSS'
    or 'DRAWING N^c 7^. m->&o2.'.
    """
    if not val:
        return False
    if any(c in cfg['forbidden_chars'] for c in val):
        return False
    if cfg['forbidden_run_pattern'].search(val):
        return False
    alnum = sum(1 for c in val if c.isalnum())
    if alnum < cfg['min_alnum']:
        return False
    junk = sum(1 for c in val
               if not c.isalnum() and c not in cfg['allowed_specials'])
    if junk / len(val) > cfg['max_junk_char_ratio']:
        return False
    return True

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

    For PDFs whose embedded text layer is empty / OCR-garbage (typical for
    scanned drawings and old AutoCAD print-outs) the function transparently
    falls back to Tesseract OCR via ``vision_extractor.ocr_pdf_text``. The
    fallback is gated by soft-coded thresholds in ``VISION_CONFIG`` and is
    a no-op when Tesseract is unavailable.
    """
    fmt = fmt or detect_format(file_path)
    if not fmt:
        return ''
    text = ''
    try:
        if fmt == 'pdf':
            import pdfplumber
            chunks: List[str] = []
            with pdfplumber.open(file_path) as pdf:
                for p in pdf.pages:
                    chunks.append(p.extract_text() or '')
                    if sum(len(c) for c in chunks) > _MAX_SCAN_CHARS:
                        break
            text = '\n'.join(chunks)[:_MAX_SCAN_CHARS]
        elif fmt == 'excel':
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
            text = '\n'.join(out)[:_MAX_SCAN_CHARS]
        elif fmt == 'word':
            import docx
            doc = docx.Document(file_path)
            text = '\n'.join(p.text for p in doc.paragraphs)[:_MAX_SCAN_CHARS]
    except Exception:
        logger.exception('read_file_text failed for %s', file_path)

    # OCR fallback for PDFs whose text layer is empty / unreadable.
    if fmt == 'pdf':
        try:
            from . import vision_extractor
            if vision_extractor.needs_ocr_fallback(text):
                ocr_text = vision_extractor.ocr_pdf_text(file_path)
                if ocr_text and len(ocr_text.strip()) > len(text.strip()):
                    logger.info('OCR fallback engaged for %s (text %d → %d chars)',
                                file_path, len(text), len(ocr_text))
                    text = (text + '\n' + ocr_text)[:_MAX_SCAN_CHARS]
        except Exception:
            logger.exception('OCR fallback failed for %s', file_path)

        # Yellow-highlight extractor — pulls revision/approval/hold stamps
        # that almost never appear in the text layer of older drawings.
        try:
            from . import yellow_region_extractor
            yellow_blob = yellow_region_extractor.extract_yellow_text_blob(file_path)
            if yellow_blob:
                logger.info('Yellow-region OCR contributed %d chars for %s',
                            len(yellow_blob), file_path)
                text = (text + '\n' + yellow_blob)[:_MAX_SCAN_CHARS]
        except Exception:
            logger.exception('Yellow-region OCR failed for %s', file_path)
    return text


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
    """
    Pick the first 'clean' line from a document's text as its title.

    Resilient to OCR garbage from scanned PDFs: every candidate line is
    normalized then validated with TEXT_QUALITY_CONFIG before being
    returned. If nothing clean is found, returns '' — better an empty
    field than "^5 - 50/ A Po A^ -p AyfA=>^ yoyAAp:>yA77^APs".
    """
    if not text:
        return ''
    for raw in text.splitlines():
        s = _normalize_text(raw)
        if not s:
            continue
        if not (5 <= len(s) <= 90):
            continue
        if not re.search(r'[A-Za-z]', s):
            continue
        if _TITLE_NOISE.match(s):
            continue
        # Skip bare document numbers (e.g. "P16093-PR-PFD-001")
        if DOCUMENT_NO_PATTERN.fullmatch(s):
            continue
        # NEW: reject OCR garbage via soft-coded quality heuristics.
        if not _is_clean_text(s):
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
                # Soft-coded junk filter — reject OCR-noise values.
                if not _is_clean_value(val):
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
                if val and val.upper() not in stop and _is_clean_value(val):
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

    # ---------------------------------------------------------------
    # Vision AI enrichment (post-pass). Only fills columns still equal
    # to the NA placeholder — never overwrites a regex-extracted value.
    # No-op when OPENAI_API_KEY is missing.
    # ---------------------------------------------------------------
    try:
        from . import vision_extractor
        # Build a sanitized view where 'NA' counts as empty, so the
        # enricher knows which fields still need attention.
        view = {k: ('' if str(v).strip().upper() == na.upper() else v)
                for k, v in row.items()}
        enrichment = vision_extractor.enrich_via_vision(
            file_path=file_path, file_name=file_name,
            current_row=view, na_value=na,
            ocr_text=text,
        )
        if enrichment:
            logger.info('Vision enrichment for %s filled %d field(s): %s',
                        file_name, len(enrichment), list(enrichment.keys()))
            for k, v in enrichment.items():
                if not v:
                    continue
                # Only fill cells the regex pipeline left empty/NA.
                cur = str(row.get(k, '')).strip()
                if (not cur) or cur.upper() == na.upper():
                    row[k] = v
    except Exception:
        logger.exception('vision enrichment failed for %s', file_name)

    return row
