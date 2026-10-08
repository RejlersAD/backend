"""
Parses Equipment List and Load List files — Excel (.xlsx) OR PDF.
Looks for TAG NUMBER / DESCRIPTION columns (case insensitive).

PDF extraction is 2-tier, both FREE (no AI Vision / no API cost for
these reference files — AI Vision is reserved for P&ID drawing
extraction only, see services/tag_extractor.py):
  Tier 1 — PyMuPDF page.find_tables() — text-based PDFs
  Tier 2 — pdfplumber page.extract_tables() — fallback for tables
           PyMuPDF's detector misses (different algorithm, same
           bytes-stream pattern as apps.pid_verification_v2.tasks's own
           _parse_instrument_index_pdf)
If both tiers find zero rows, parse_excel_tags raises ValueError with a
clear, actionable message rather than silently returning an empty list.
Each tier stops as soon as it finds at least one valid tag.
"""
import io
import logging
import re

import pandas as pd

from apps.pid_checker_v2.legend_defaults import DEFAULT_TEMPLATES, SECTION_ELECTRICAL

logger = logging.getLogger(__name__)

# Get electrical legend fields — the ONE source of truth for this
# section's shape. Same pattern as services/tag_extractor.py's own
# ELECTRICAL_TAG_PATTERN (read that file's own comment for the full
# "what this does and doesn't track" explanation — short version: this
# tracks edits to legend_defaults.py's SECTION_ELECTRICAL Python constant
# (+ a server restart), NOT a user's live legend edited through the
# Legend Sheets Canvas UI, which is separate DB-backed data this never
# reads). Previously a hand-copied literal here — a real, confirmed drift
# risk (tag_extractor.py's version was fixed first; this file wasn't)
# — now built dynamically so the two can never go out of sync again.
ELECTRICAL_TEMPLATE = DEFAULT_TEMPLATES[SECTION_ELECTRICAL]
ELECTRICAL_FIELDS = ELECTRICAL_TEMPLATE['definition']['fields']
_AREA_REGEX = ELECTRICAL_FIELDS[0]['regex']       # \d{1,5}
_TYPE_REGEX = ELECTRICAL_FIELDS[1]['regex']       # [A-Za-z]{1,4}
_SEQUENCE_REGEX = ELECTRICAL_FIELDS[2]['regex']   # [A-Za-z0-9]{2,6}(?:-[A-Za-z0-9]{1,6})?
_SEPARATOR = ELECTRICAL_TEMPLATE['definition']['separator']  # '-'

ELECTRICAL_TAG_PATTERN = re.compile(
    rf'\b({_AREA_REGEX}){re.escape(_SEPARATOR)}({_TYPE_REGEX}){re.escape(_SEPARATOR)}({_SEQUENCE_REGEX})\b'
)

# FIX — same "single source of truth in legend_defaults.py" pattern as
# services/tag_extractor.py's own INVALID_TYPE_CODES/PLACEHOLDER_SEQUENCES
# (read dynamically, never hand-copied). Previously this file had NO
# equivalent of either check at all — a real, confirmed gap: a pipe/
# vessel/instrument tag (285-P-0028, 285-V-453, 285-FT-101, ...) or a
# legend/symbol-key placeholder example (285-U-XXXX) from an Equipment
# List or Load List file matched ELECTRICAL_TAG_PATTERN just as readily
# as real equipment and was never filtered, even after tag_extractor.py
# (the separate P&ID/Vision path) was fixed to filter both.
INVALID_TYPE_CODES = {c.upper() for c in ELECTRICAL_TEMPLATE['definition'].get('invalid_type_codes', [])}
PLACEHOLDER_SEQUENCES = {s.upper() for s in ELECTRICAL_TEMPLATE['definition'].get('placeholder_sequences', [])}

# Equipment-List panel/switchgear type codes — mirrors apps.pid_checker_v2.
# legend_defaults's SECTION_ELECTRICAL TYPE_CODE_LOOKUP: U='SWITCHBOARD /
# MCC', JB='JUNCTION BOX', BD='BUS DUCT', AB='ADAPTER BOX', SB='GIS PANEL',
# TSG='TEMPORARY SWITCHGEAR'. Used both to filter these OUT of Load List
# results below, and by views.py's _build_panel_verification to find every
# OTHER panel-type Equipment List entry with no Load List data.
PANEL_TYPE_CODES = {'U', 'JB', 'BD', 'AB', 'TSG', 'SB'}

# Load List motor type codes — PM='PUMP MOTOR', NPM='NON-ESSENTIAL PUMP
# MOTOR', RM='ROTATING MACHINE'. A Load List's table/header text can still
# contain panel tags (the "PANEL TAG:" header line itself, or panel rows
# carried over from the Equipment List) that the generic tag regex would
# otherwise pick up as if they were motors — only these 3 codes are kept.
LOAD_LIST_MOTOR_TYPE_CODES = {'PM', 'NPM', 'RM'}

# Expanded alias lists (FIX 2) — shared by every tier (Excel columns,
# PyMuPDF/pdfplumber table header rows) so a header-matching tweak only
# ever needs to happen in one place.
_TAG_HEADER_ALIASES = {
    'tag number', 'tag no', 'tag no.', 'tag', 'tag_number',
    'tag #', 'tag#', 'item tag', 'equipment tag', 'equipment tag no',
    'tag number:', 'tag no:', 'tag:',
    'tag num', 'tagno', 'tagnumber',
    'tag number (kks)', 'kks tag',
}
_DESC_HEADER_ALIASES = {
    'description', 'desc', 'equipment description',
    'equipment desc', 'item description',
    'description:', 'name', 'equipment name',
    'item name', 'load description',
}


def _find_tag_desc_columns(header_row):
    """header_row: list of cell values (one table row). Returns
    (tag_col_index, desc_col_index) — either may be None.

    FIX 4 — two passes: exact match first (as before), then — only for
    whichever column wasn't found by exact match — a substring pass, so
    a real-world header like "Tag Number (KKS)" or "Old Tag / Description"
    still resolves via the alias contained within it."""
    cells = [str(c or '').lower().strip() for c in (header_row or [])]

    tag_idx = desc_idx = None
    for i, cell in enumerate(cells):
        if tag_idx is None and cell in _TAG_HEADER_ALIASES:
            tag_idx = i
        if desc_idx is None and cell in _DESC_HEADER_ALIASES:
            desc_idx = i

    if tag_idx is None:
        for i, cell in enumerate(cells):
            if any(alias in cell for alias in _TAG_HEADER_ALIASES):
                tag_idx = i
                break
    if desc_idx is None:
        for i, cell in enumerate(cells):
            if any(alias in cell for alias in _DESC_HEADER_ALIASES):
                desc_idx = i
                break

    return tag_idx, desc_idx


def _rows_from_table(table_rows, file_type):
    """table_rows: list[list[str]] — raw cells from one PyMuPDF/pdfplumber
    table (header row(s) + data rows). Returns the same
    [{'tag', 'description', 'source'}] shape the Excel path returns, so
    callers never need to know which tier/format a result came from."""
    if not table_rows or len(table_rows) < 2:
        return []

    # IO-list-style PDFs can have title/revision blocks or several
    # header/group-label rows before the real column header — same
    # "search the first N rows" approach as io_table_extractor.py's own
    # header detection. Was 4 rows (FIX 3 — raised to 10 to handle PDFs
    # with more preamble above the actual table header).
    tag_idx = desc_idx = None
    header_row_idx = -1
    for hi in range(min(10, len(table_rows))):
        t, d = _find_tag_desc_columns(table_rows[hi])
        if t is not None:
            tag_idx, desc_idx, header_row_idx = t, d, hi
            break
    if tag_idx is None:
        return []

    results = []
    for row in table_rows[header_row_idx + 1:]:
        if tag_idx >= len(row):
            continue
        tag_val = str(row[tag_idx] or '').strip()
        match = ELECTRICAL_TAG_PATTERN.search(tag_val)
        if not match:
            continue
        tag = match.group(0).upper()
        description = ''
        if desc_idx is not None and desc_idx < len(row) and row[desc_idx]:
            description = str(row[desc_idx]).strip()
        results.append({'tag': tag, 'description': description, 'source': file_type})
    return results


def _parse_pdf_tier1_pymupdf(pdf_bytes, file_type):
    import fitz  # PyMuPDF
    results = []
    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    try:
        for page_num, page in enumerate(doc):
            try:
                tables = page.find_tables()
            except Exception as exc:
                logger.warning('[ElecCompare][Tier1] page %d: find_tables() failed: %s', page_num, exc)
                continue
            table_list = list(tables)
            logger.info('[ElecCompare][Tier1] page %d: %d table(s) found', page_num, len(table_list))
            for ti, tbl in enumerate(table_list):
                try:
                    raw = tbl.extract()
                except Exception as exc:
                    logger.warning('[ElecCompare][Tier1] page %d table %d: extract() failed: %s', page_num, ti, exc)
                    continue
                header_preview = raw[0] if raw else None
                logger.info('[ElecCompare][Tier1] page %d table %d: %d row(s), header row: %r', page_num, ti, len(raw), header_preview)
                rows = _rows_from_table(raw, file_type)
                logger.info('[ElecCompare][Tier1] page %d table %d: %d tag(s) matched', page_num, ti, len(rows))
                results.extend(rows)
    finally:
        doc.close()
    return results


def _parse_pdf_tier2_pdfplumber(pdf_bytes, file_type):
    import pdfplumber
    results = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_num, page in enumerate(pdf.pages):
            tables = page.extract_tables() or []
            logger.info('[ElecCompare][Tier2] page %d: %d table(s) found', page_num, len(tables))
            for ti, table in enumerate(tables):
                header_preview = table[0] if table else None
                logger.info('[ElecCompare][Tier2] page %d table %d: %d row(s), header row: %r', page_num, ti, len(table), header_preview)
                rows = _rows_from_table(table, file_type)
                logger.info('[ElecCompare][Tier2] page %d table %d: %d tag(s) matched', page_num, ti, len(rows))
                results.extend(rows)
    return results


def _parse_excel(file_obj, file_type):
    try:
        df = pd.read_excel(file_obj, engine='openpyxl')

        tag_col = None
        desc_col = None
        for col in df.columns:
            col_lower = str(col).lower().strip()
            if col_lower in _TAG_HEADER_ALIASES:
                tag_col = col
            if col_lower in _DESC_HEADER_ALIASES:
                desc_col = col

        logger.info(
            '[ElecCompare][Excel] %s columns: %r (tag_col=%r, desc_col=%r)',
            file_type, list(df.columns), tag_col, desc_col,
        )
        if not tag_col:
            return []

        results = []
        for _, row in df.iterrows():
            tag_val = str(row[tag_col]).strip()
            match = ELECTRICAL_TAG_PATTERN.search(tag_val)
            if not match:
                continue
            tag = match.group(0).upper()
            description = ''
            if desc_col and pd.notna(row.get(desc_col)):
                description = str(row[desc_col]).strip()
            results.append({'tag': tag, 'description': description, 'source': file_type})

        return results

    except Exception as e:
        raise ValueError(f'Failed to parse Excel file: {str(e)}')


def _parse_pdf(file_obj, file_type):
    try:
        pdf_bytes = file_obj.read()
    except Exception as e:
        raise ValueError(f'Failed to read PDF file: {str(e)}')

    try:
        results = _parse_pdf_tier1_pymupdf(pdf_bytes, file_type)
        if results:
            return results

        results = _parse_pdf_tier2_pdfplumber(pdf_bytes, file_type)
        if results:
            return results
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f'Failed to parse PDF file: {str(e)}')

    # Both free tiers found zero rows — no AI Vision fallback for
    # Equipment List / Load List files (AI Vision is reserved for P&ID
    # drawing extraction only, per explicit instruction). Clear,
    # actionable error instead of a silent empty result.
    raise ValueError(
        'Could not extract table from PDF. '
        'Please ensure the PDF contains a proper TAG NUMBER table '
        'and is not a scanned image.'
    )


# ═══════════════════════════════════════════════════════════════════════
# Load List panel-tag extraction — a Load List PDF's motors are fed from
# ONE panel/MCC, named on a header line like "PANEL TAG: 285-U-505A" or
# "PANEL TAG: 285-U-505A, 380V ESSENTIAL MCC" (tag + optional trailing
# description). Direct tag-to-tag matching between Equipment List
# (panels) and Load List (motors) is meaningless — they're different
# equipment classes — so the real comparison this function enables is:
# "does the panel the Load List's motors are fed from actually exist in
# the Equipment List?" (see views.py's _build_panel_verification).
# ═══════════════════════════════════════════════════════════════════════
# Matches the "PANEL TAG" LABEL only — the colon is OPTIONAL (handles
# "PANEL TAG: 285-U-505A", "PANEL TAG : 285-U-505A" and "PANEL TAG
# 285-U-505A" alike) and everything after the label is captured as-is;
# the actual tag is then found within that capture via the real,
# legend-derived ELECTRICAL_TAG_PATTERN below rather than a second
# hand-written tag shape, so the two can never drift apart.
PANEL_TAG_LABEL_PATTERN = re.compile(r'PANEL\s*TAG\s*:?\s*(.*)', re.IGNORECASE)


def extract_panel_tag_from_load_list(pdf_bytes):
    """Search every page of the Load List PDF — via BOTH PyMuPDF text
    extraction and pdfplumber (different extraction algorithms; a line
    one tier's text run joins or splits oddly, the other sometimes
    doesn't) — for a 'PANEL TAG' header line and return the FIRST one
    found as {'panel_tag': str, 'description': str}, or None if no such
    line exists anywhere in the PDF.

    Handles "PANEL TAG: 285-U-505A", "PANEL TAG : 285-U-505A",
    "PANEL TAG: 285-U-505A, 380V ESSENTIAL MCC" and "PANEL TAG
    285-U-505A" (no colon) alike. 'panel_tag' is validated against the
    same legend-derived ELECTRICAL_TAG_PATTERN as everywhere else in
    this module — a line that says "PANEL TAG" but whose following text
    doesn't actually parse as a real electrical tag is treated as not
    found, same "never fabricate a match" rule used throughout this app.
    """
    import fitz  # PyMuPDF

    page_texts = []  # [(page_num, source, text)]

    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    try:
        for page_num, page in enumerate(doc):
            text = page.get_text() or ''
            logger.info(
                '[ElecCompare][PanelTag] PyMuPDF page %d text (first 200 chars): %r',
                page_num, text[:200],
            )
            page_texts.append((page_num, 'pymupdf', text))
    finally:
        doc.close()

    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page_num, page in enumerate(pdf.pages):
                text = page.extract_text() or ''
                logger.info(
                    '[ElecCompare][PanelTag] pdfplumber page %d text (first 200 chars): %r',
                    page_num, text[:200],
                )
                page_texts.append((page_num, 'pdfplumber', text))
    except Exception as exc:
        logger.warning('[ElecCompare][PanelTag] pdfplumber text extraction failed: %s', exc)

    for page_num, source, text in page_texts:
        for line in text.splitlines():
            label_match = PANEL_TAG_LABEL_PATTERN.search(line)
            if not label_match:
                continue
            rest = label_match.group(1)
            tag_match = ELECTRICAL_TAG_PATTERN.search(rest.upper())
            if not tag_match:
                logger.warning(
                    '[ElecCompare][PanelTag] Found "PANEL TAG" label on page %d (%s) but no valid tag followed: %r',
                    page_num, source, rest[:80],
                )
                continue
            tag = tag_match.group(0).upper()
            description = rest[tag_match.end():].strip().lstrip(',').strip()
            logger.info(
                '[ElecCompare][PanelTag] Panel tag found: %s (description=%r) — page %d via %s',
                tag, description, page_num, source,
            )
            return {'panel_tag': tag, 'description': description}

    logger.warning(
        '[ElecCompare][PanelTag] No "PANEL TAG" line found anywhere in Load List PDF (%d page-text extraction(s) checked)',
        len(page_texts),
    )
    return None


def _filter_load_list_motor_tags(results):
    """A Load List extraction (PDF table rows, or incidental matches from
    header/preamble text) can still surface panel/switchgear tags
    (PANEL_TYPE_CODES) alongside real motor tags — those belong to the
    Equipment List, never the Load List. Keeps only
    LOAD_LIST_MOTOR_TYPE_CODES (PM/NPM/RM) and collapses duplicate tags
    (the same motor can appear on more than one extracted row/page)."""
    seen = set()
    filtered = []
    skipped = 0
    for row in results:
        tag_match = ELECTRICAL_TAG_PATTERN.search(row['tag'])
        type_code = tag_match.group(2).upper() if tag_match else ''
        if type_code not in LOAD_LIST_MOTOR_TYPE_CODES:
            skipped += 1
            logger.info(
                '[ElecCompare][LoadList] Skipping non-motor tag %r (type_code=%r) — panel/other equipment, not a motor',
                row['tag'], type_code,
            )
            continue
        if row['tag'] in seen:
            continue
        seen.add(row['tag'])
        filtered.append(row)
    logger.info(
        '[ElecCompare][LoadList] %d motor tag(s) kept, %d panel/non-motor tag(s) skipped, %d total before filtering',
        len(filtered), skipped, len(results),
    )
    return filtered


def _filter_invalid_type_code_and_placeholder_tags(results):
    """Drops any row whose tag has a known non-electrical type code
    (pipe/vessel/instrument/other, per legend_defaults.py's
    invalid_type_codes — same list tag_extractor.py's P&ID/Vision path
    already filters) or whose sequence is a known legend/symbol-key
    placeholder example (e.g. XXXX, YYYY, per placeholder_sequences).
    Uses ELECTRICAL_TAG_PATTERN (the same legend-derived regex every
    other tag_match in this module already uses) to pull out the
    type_code/sequence groups, rather than a plain '-'.split(), since a
    real sequence can itself contain a hyphen (e.g. JB-001-007) and a
    naive split would misread which part is the type code."""
    filtered = []
    for row in results:
        tag_match = ELECTRICAL_TAG_PATTERN.search(row['tag'])
        type_code = tag_match.group(2).upper() if tag_match else ''
        sequence = tag_match.group(3).upper() if tag_match else ''
        if type_code in INVALID_TYPE_CODES:
            logger.info(
                '[ElecCompare][Excel] Filtered invalid type code tag: %s (type: %s)',
                row['tag'], type_code,
            )
            continue
        if sequence in PLACEHOLDER_SEQUENCES:
            logger.info(
                '[ElecCompare][Excel] Filtered placeholder sequence tag: %s (sequence: %s)',
                row['tag'], sequence,
            )
            continue
        filtered.append(row)
    return filtered


def parse_excel_tags(file_obj, file_type='equipment_list'):
    """
    Parse an Equipment List / Load List file (.xlsx OR .pdf) and extract
    electrical tags.
    Returns list of dicts:
    [{'tag': '285-PM-411B', 'description': 'Pump Motor', 'source': 'equipment_list'}]

    PDF extraction is Tier 1 (PyMuPDF) + Tier 2 (pdfplumber) only — both
    free. No AI Vision fallback for these files; raises ValueError with a
    clear message if neither free tier finds a usable table.

    For file_type='load_list', results are additionally filtered down to
    actual motor tags (PM/NPM/RM) — see _filter_load_list_motor_tags —
    since the raw table/text extraction can otherwise surface
    Equipment-List-style panel tags that leaked into the Load List PDF's
    header or table.
    """
    filename = (getattr(file_obj, 'name', '') or '').lower()
    if filename.endswith('.pdf'):
        results = _parse_pdf(file_obj, file_type)
    else:
        results = _parse_excel(file_obj, file_type)

    results = _filter_invalid_type_code_and_placeholder_tags(results)

    if file_type == 'load_list':
        results = _filter_load_list_motor_tags(results)

    return results
