"""
IO List structured-table extractor.

Cost-optimised pipeline:
  1. PyMuPDF page.find_tables() — text-based PDFs, FREE
  2. Header alias matcher → canonical 40-column row schema
  3. Vision fallback (gpt-4o-mini) — opt-in, page-targeted, hard-capped

The vision fallback function is a stub by default; turning it on requires
INSTRUMENT_IO_ENABLE_VISION_FALLBACK=true AND an OpenAI key. It is called
ONLY for pages classified as 'io_table' that returned zero rows from
PyMuPDF — never speculatively.
"""

from __future__ import annotations

import logging
import re
from typing import List, Dict, Optional, Tuple

import fitz  # PyMuPDF

from .config import (
    IO_LIST_CANONICAL_COLUMNS,
    IO_HEADER_ALIASES,
    ENABLE_VISION_FALLBACK,
    VISION_MAX_PAGES_PER_DOC,
    ENABLE_LOCAL_OCR,
    LOCAL_OCR_RENDER_DPI,
    LOCAL_OCR_THRESHOLD,
)

logger = logging.getLogger(__name__)

_OCR_TAG_RE = re.compile(
    r'\b(\d{2,4})\s*[-\u2010-\u2015]\s*([A-Z]{1,4})\s*[-\u2010-\u2015]\s*(\d{2,5}[A-Z]?)\b',
    re.I,
)
_CABLE_RE = re.compile(r'\b(\d{2,4})\s+([A-Z])\s+(\d{2})\s+(\d{3})\b')
_UNIT_RE = re.compile(r'\bUNIT\s*:?\s*(\d{2,4})\b')
_MONTH_ABBREVIATIONS = frozenset({
    'JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
    'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC',
})


def _rows_from_ocr_text(text: str, page_number: int) -> List[Dict]:
    """Build partial canonical rows from a cable-block drawing OCR transcript."""
    upper = (text or '').upper()
    cables = []
    for match in _CABLE_RE.finditer(upper):
        cable = ' '.join(match.groups())
        if cable not in cables:
            cables.append(cable)
    page_cable = cables[0] if len(cables) == 1 else ''

    unit_match = _UNIT_RE.search(upper)
    drawing_unit = unit_match.group(1) if unit_match else (
        page_cable.split()[0] if page_cable else ''
    )
    tags = []
    seen = set()
    for match in _OCR_TAG_RE.finditer(upper):
        prefix = match.group(1)
        # OCR occasionally drops one digit from a faint unit prefix (13 vs
        # 113). Correct only that narrow case when the title block exposes the
        # drawing unit; do not rewrite unrelated cross-unit tags.
        if (drawing_unit and len(prefix) + 1 == len(drawing_unit)
                and prefix in drawing_unit):
            prefix = drawing_unit
        tag = f'{prefix}-{match.group(2)}-{match.group(3)}'
        if tag not in seen:
            seen.add(tag)
            tags.append((tag, match.group(2)))

    system = 'DCS' if 'DCS SYSTEM CABINET' in upper else (
        'ESD' if 'ESD SYSTEM CABINET' in upper else ''
    )
    rows = []
    for tag, instrument_type in tags:
        record = {column: '' for column in IO_LIST_CANONICAL_COLUMNS}
        record.update({
            'tag_number': tag,
            'instrument_type': instrument_type,
            'kind': 'instrument',
            'unit': drawing_unit,
            'from_location': 'FIELD' if 'FIELD' in upper else '',
            'system': system,
            'pri_cable_no': page_cable,
            'remarks': 'Extracted from cable block diagram using local OCR; verify remaining fields.',
            'page_number': page_number,
        })
        rows.append(record)
    return rows


def _extract_drawing_rows_with_local_ocr(page, page_number: int) -> List[Dict]:
    """OCR faint CAD annotations after thresholding; no external API is used."""
    if not ENABLE_LOCAL_OCR:
        return []
    try:
        import pytesseract
        from PIL import Image, ImageOps

        scale = LOCAL_OCR_RENDER_DPI / 72
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        image = Image.frombytes('RGB', [pixmap.width, pixmap.height], pixmap.samples)
        grayscale = ImageOps.grayscale(image)
        thresholded = grayscale.point(
            lambda value: 0 if value < LOCAL_OCR_THRESHOLD else 255,
        )
        text = pytesseract.image_to_string(thresholded, config='--psm 11')
        return _rows_from_ocr_text(text, page_number)
    except Exception as exc:
        logger.warning('[IOWF] Local OCR failed on drawing page %d: %s', page_number, exc)
        return []


def extract_pid_drawing_row_for_page_via_local_ocr(
    pdf_bytes: bytes, page_index: int,
) -> List[Dict]:
    """Single-page local-OCR fallback for a P&ID drawing page — used both
    by the synchronous path (no BYOK key supplied) and by
    tasks.process_pid_vision_page's per-page Celery fan-out (no key, or
    the Vision call itself failed for that one page). Reuses the same OCR
    primitives as _extract_drawing_rows_with_local_ocr; shapes rows as a
    P&ID drawing instrument tag (kind='instrument') rather than a
    cable-block-diagram row, and swaps in the P&ID-appropriate remark.
    """
    if not ENABLE_LOCAL_OCR:
        return []
    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    try:
        if page_index < 0 or page_index >= len(doc):
            return []
        page = doc[page_index]
        rows = _extract_drawing_rows_with_local_ocr(page, page_index + 1)
        for row in rows:
            row['kind'] = 'instrument'
            row['remarks'] = (
                'Extracted from P&ID drawing via local OCR — '
                'add an API key for better accuracy.'
            )
        return rows
    except Exception as exc:
        logger.warning(
            '[IOWF] Local OCR failed on P&ID drawing page %d: %s',
            page_index + 1, exc,
        )
        return []
    finally:
        doc.close()


MIN_TEXT_PATTERN_MATCHES = 2

# Fields the alternating-row-shape merge (see the "no tag" branch inside
# _extract_rows_with_pymupdf's per-row loop) trusts from a row2-shape row,
# via secondary_map. NOT every canonical secondary_map happens to claim —
# a real document (id 107) has a genuine THIRD row shape (HMI Description/
# Status/HMI Tag/...) that secondary_map can't distinguish from row2's
# own, so letting every field through risked silently writing that third
# shape's values under row2's field names too. 'loop_number' is left out
# for a different reason: on this table it lives on a separate row this
# function doesn't map at all, not on row2 itself.
_ALT_ROW2_FIELDS = frozenset({
    'loop_number', 'service_description', 'system', 'to_location',
    'signal_type', 'wet_dry', 'device_location', 'sys_range_min',
    'sys_range_max', 'alarm_h', 'alarm_hh', 'alarm_l', 'alarm_ll',
    'sys_cab_no', 'intercon_dwg', 'cable_size',
})

# Status words distinguishing a row3/HMI-shape row's idx3 (Status) from a
# row1-shape row's own idx3 (From, a location) — see the detection this
# guards inside _extract_rows_with_pymupdf's per-row loop.
_ROW3_STATUS_WORDS = frozenset({'N-NEW', 'NEW', 'EXISTING'})


def _extract_rows_from_text_patterns(page_text: str, page_number: int) -> List[Dict]:
    """Fallback for an io_table page whose PDF has NO detectable grid lines
    at all — page.find_tables() returns zero usable tables. Bug hit live:
    a real "Instrument Cable Schedule" document (id 106) has full, legible
    row data on its io_table pages, but PyMuPDF can't segment them into
    columns because the source PDF draws no visible cell borders.

    get_text('text') DOES recover every field's value on these pages, but
    NOT in the header's left-to-right column order — this exporter's
    internal paint order interleaves fields in a document-specific
    sequence that doesn't match the visual table layout at all (confirmed
    by inspecting the raw text: a row's tokens come out in a completely
    different order than its own header row's columns). That rules out
    this file's normal approach — position-based column mapping — even in
    principle here: there is no reliable "column N" to read from a text
    stream with no columns.

    Deliberately conservative instead: only the tag number (via the same
    regex the cable-block-diagram OCR fallback below already uses) and
    its own instrument-type code are extracted with real confidence.
    Every other field on a real row (description, cable size, status,
    locations...) sits at an unpredictable position relative to its own
    tag in this text stream, so guessing at them risks silently
    mislabeling real engineering data under the wrong column — worse than
    leaving it blank. Flagged via `remarks` for manual review instead,
    matching the same "flag for verification" convention the OCR fallback
    already uses rather than inventing a new one.
    """
    rows: List[Dict] = []
    seen_tags: set = set()
    prev_tag = ''
    for match in _OCR_TAG_RE.finditer(page_text or ''):
        prefix, itype, suffix = match.groups()
        if itype.upper() in _MONTH_ABBREVIATIONS and len(suffix) == 2:
            # A date like "27-FEB-24" matches this regex's shape (2-4
            # digits, 1-4 letters, 2-5 digits) exactly — bug hit live on
            # document 102's title block. A real instrument-type code is
            # never a month name, so this is a safe, narrow exclusion.
            continue
        tag = f'{prefix}-{itype.upper()}-{suffix.upper()}'
        if tag == prev_tag:
            # Real documents of this shape print the same instrument's
            # Cable Tag No. and Item Tag No. back-to-back — collapse that
            # adjacent repeat into one row, not two.
            continue
        prev_tag = tag
        if tag in seen_tags:
            continue
        seen_tags.add(tag)
        record: Dict[str, str] = {c: '' for c in IO_LIST_CANONICAL_COLUMNS}
        record.update({
            'tag_number': tag,
            'instrument_type': itype.upper(),
            'page_number': page_number,
            'remarks': (
                'Extracted from a PDF with no table gridlines using pattern '
                'matching on the tag number only — every other field could '
                'not be reliably positioned in this document and was left '
                'blank. Verify manually against the source PDF.'
            ),
        })
        rows.append(record)
    # A page with a genuine data table yields many tag matches; a single
    # hit is far more likely to be one stray tag-shaped reference code in
    # a title block or reference-document list (bug hit live: '15-ES-02'
    # on an otherwise normally-extracting document) than a real row. Below
    # this floor, treat it the same as finding nothing rather than risk
    # adding one low-value, possibly-wrong row to an already-working page.
    return rows if len(rows) >= MIN_TEXT_PATTERN_MATCHES else []


# Cable Schedule cell labels (real example: document 106/107's
# "Instrument Cable Schedule" tables) — each table CELL is self-labeled,
# e.g. one cell's whole text is literally "Item Tag No.\n113-PDT -3194".
# Matched by prefix against the cell's own flattened text, so the label
# can itself span multiple lines ("Item\nStatus\nN-NEW") without needing
# to know the exact line-break position in advance.
# 'item tag no' is handled specially below (it appears twice per row: once
# for the FROM instrument, once for the TO junction/location) rather than
# through this straight label->canonical mapping.
_CABLE_SCHEDULE_ITEM_TAG_LABEL = 'item tag no'
_CABLE_SCHEDULE_CELL_LABELS = (
    ('cable tag no', 'tag_number'),
    ('cable type', 'instrument_type'),
    ('item description', 'service_description'),
    ('item status', 'status'),
    ('cable description', 'remarks'),
)


def _split_labeled_cell(cell_text: str) -> Tuple[Optional[str], str]:
    """If `cell_text` starts with one of the known Cable Schedule labels
    (ignoring internal line breaks/case), return (canonical_field_or_
    '__item_tag__', value_with_label_removed). Otherwise (None, cell_text).
    """
    if not cell_text:
        return None, cell_text
    flat = re.sub(r'\s+', ' ', cell_text).strip()
    low = flat.lower()
    if low.startswith(_CABLE_SCHEDULE_ITEM_TAG_LABEL):
        return '__item_tag__', flat[len(_CABLE_SCHEDULE_ITEM_TAG_LABEL):].strip(' .:')
    for label, canonical in _CABLE_SCHEDULE_CELL_LABELS:
        if low.startswith(label):
            return canonical, flat[len(label):].strip(' .:')
    return None, cell_text


def _extract_rows_from_labeled_table_cells(raw_rows: List[List[str]], page_number: int) -> List[Dict]:
    """Cable Schedule pages (real example: document 106) don't give
    PyMuPDF a normal header-row + column-index grid at all — find_tables()
    instead returns a table whose individual CELLS are each self-labeled
    (see the module comment above _CABLE_SCHEDULE_CELL_LABELS). Read those
    labels directly rather than relying on column position, which this
    file's normal _build_header_map approach needs and which genuinely
    doesn't exist for this shape.

    'Item Tag No.' / 'Item Description' / 'Item Status' each appear TWICE
    per logical row — once for the FROM instrument, once for the TO
    junction box/marshalling location (the source table's own header has
    'From (origin)'/'To (Destination)' group labels over exactly this
    pair) — tracked by counting 'Item Tag No.' occurrences within one row:
    1st = tag_number/from_location, 2nd = to_location. A row that never
    shows a first 'Item Tag No.' cell contributes nothing (there's no tag
    to anchor it to — matches this file's existing "tag is the natural
    key" rule elsewhere).
    """
    rows: List[Dict] = []
    for row in raw_rows:
        record: Dict[str, str] = {}
        item_tag_seen = 0
        for cell in row:
            target, value = _split_labeled_cell(cell)
            if target is None or not value:
                continue
            if target == '__item_tag__':
                item_tag_seen += 1
                if item_tag_seen == 1:
                    record.setdefault('tag_number', re.sub(r'\s*-\s*', '-', value))
                    record.setdefault('from_location', value)
                else:
                    record.setdefault('to_location', value)
            else:
                record.setdefault(target, value)
        if not record.get('tag_number'):
            continue
        full: Dict[str, str] = {c: '' for c in IO_LIST_CANONICAL_COLUMNS}
        full.update(record)
        full['page_number'] = page_number
        full['remarks'] = (
            (full.get('remarks', '') + ' ' if full.get('remarks') else '')
            + 'Extracted from self-labeled table cells (Cable Schedule '
              'format, no column grid) — verify manually against the '
              'source PDF.'
        ).strip()
        rows.append(full)
    return rows


# Fixed column-index layout for this same Cable Schedule table shape,
# confirmed against real data on document 106 (two full pages, 12+ records
# each): the FIRST logical record (immediately under the header) collapses
# into one giant cell — see _extract_rows_from_labeled_table_cells above,
# which is what actually recovers it — but every record AFTER that follows
# this exact index order with real gridlines PyMuPDF parses cleanly.
# Column meanings/targets per the user's own confirmed reading of this
# document's header (screenshot cross-checked against the live data):
# 0=Sr.No (skip), 1=Unit No (skip, appears on a separate sparse
# companion row), 2=Cable Tag No, 3=Cable Status, 4=Cable Type,
# 5=Cable Description, 6&7=Cable Size (7 is the sparse row's copy of the
# same field), 8=Cable Code, 9=Signal/Volt, 10=IS/NIS, 11=FIR/FLR (no
# canonical target requested), 12=Item Tag No[From], 13=Item Description
# [From] (not separately requested), 14=Item Status[From], 15=Item
# Loc[From] (not requested), 16=Gland Size[From] (not requested),
# 17=Item Tag No[To], 18-20=Item Description/Status/Loc[To] (not
# requested), 21-22=sparse row's To-Loc/Gland-Size copies (not
# requested), 23=Length(m), 24=Remarks (the genuine Remarks column —
# merged with Length rather than overwritten, both map to 'remarks'),
# 25=Rev.
_CABLE_SCHEDULE_FIXED_COLUMNS: Dict[int, str] = {
    2: 'tag_number',
    3: 'system',
    4: 'instrument_type',
    5: 'service_description',
    6: 'unit',
    7: 'unit',
    8: 'loop_number',
    9: 'signal_type',
    10: 'io_type',
    12: 'from_location',
    14: 'status',
    17: 'to_location',
    23: 'remarks',
    24: 'remarks',
    25: 'revision',
}


MIN_CABLE_SCHEDULE_RECORDS = 2


def _extract_rows_from_fixed_cable_schedule_columns(
    raw_rows: List[List[str]], page_number: int,
) -> List[Dict]:
    """Read every record AFTER the first from this Cable Schedule table's
    real, reliably-indexed columns (see _CABLE_SCHEDULE_FIXED_COLUMNS
    above) — this is NOT alias/header based like _build_header_map,
    because this table's own header row is unusable (see
    _extract_rows_from_labeled_table_cells's docstring); it's a fixed
    index mapping confirmed directly against real data instead.

    A logical record spans 3 physical rows here: a main row carrying most
    columns (this is what starts a new record — detected by index 2
    matching a real tag-number pattern, NOT merely being non-blank; bug
    hit live: an unrelated revision-history table elsewhere in the same
    document also fails _build_header_map and happens to have some text
    at index 2 too, e.g. 'ISSUED FOR CONSTRUCTION' — without the pattern
    check that got treated as a tag and produced pure garbage rows), a
    sparse "companion" row directly after it carrying just Unit No / the
    Cable Size continuation / the To-side Loc+Gland-Size (indices
    1/7/21/22), and a blank spacer row. The companion row's fields are
    merged into the record that's still being built (never overwritten —
    see the general merge rule used throughout this file), and the blank
    spacer contributes nothing.

    Returns [] (a safe no-op) unless at least MIN_CABLE_SCHEDULE_RECORDS
    real tag-pattern matches were found, for the same reason as the
    plain-text fallback's own confidence floor: one isolated match is far
    more likely to be a coincidence on some other table than a genuine
    Cable Schedule.
    """
    rows: List[Dict] = []
    current: Optional[Dict[str, str]] = None

    def _flush():
        if current is not None:
            rows.append(current)

    for row in raw_rows:
        tag_cell = row[2] if len(row) > 2 else None
        tag_match = _OCR_TAG_RE.search(str(tag_cell)) if tag_cell else None
        if tag_match:
            _flush()
            prefix, itype, suffix = tag_match.groups()
            current = {c: '' for c in IO_LIST_CANONICAL_COLUMNS}
            current['tag_number'] = f'{prefix}-{itype.upper()}-{suffix.upper()}'
            current['page_number'] = page_number
        if current is None:
            continue
        for idx, canonical in _CABLE_SCHEDULE_FIXED_COLUMNS.items():
            if idx == 2 or idx >= len(row) or not row[idx]:
                continue
            val = str(row[idx]).strip().replace('\n', ' ')
            val = re.sub(r'\s+', ' ', val)
            existing = current.get(canonical, '')
            if not existing:
                current[canonical] = val
            elif val not in existing:
                current[canonical] = f'{existing} {val}'.strip()
    _flush()

    if len(rows) < MIN_CABLE_SCHEDULE_RECORDS:
        return []

    for r in rows:
        r['remarks'] = (
            (r.get('remarks', '') + ' ' if r.get('remarks') else '')
            + 'Extracted using a fixed Cable Schedule column layout '
              'confirmed for this document — verify manually against the '
              'source PDF.'
        ).strip()
    return rows


def _build_header_map(header_cells: List[str]) -> Optional[Dict[int, str]]:
    """{col_index → canonical_name}; require ≥ 4 recognised columns.

    A raw substring test ("alias in cell") is unsafe for very short
    aliases like alarm_h/alarm_l's bare 'h'/'l': a real header such as
    "SET POINT (H)" contains the letter 'h' incidentally, so a plain
    substring check let alarm_h steal that column before set_point's own
    (longer, unambiguous) alias ever got a chance to match it — bug hit
    live: Set Point values landing in the Alarm H column. A 1-2 char
    alias is only meaningful as a match when it IS the whole cell text,
    not merely present somewhere inside a longer label.
    """
    norm = [(c or '').strip().lower() for c in header_cells]
    mapping: Dict[int, str] = {}
    for canonical, aliases in IO_HEADER_ALIASES.items():
        for idx, cell in enumerate(norm):
            if idx in mapping:
                continue
            if any(a == cell or (len(a) > 2 and a in cell) for a in aliases):
                mapping[idx] = canonical
                break
    return mapping if len(mapping) >= 4 else None


MIN_SECOND_HEADER_MATCHES = 2


def _extend_map_with_second_header_row(
    second_row: List[str], primary_map: Dict[int, str],
) -> Dict[int, str]:
    """Read a genuine SECOND header row (e.g. 'Service Description' / 'To'
    / 'Min.' / 'Max.' / 'H' / 'HH' living directly under a first header row
    that already separately satisfied _build_header_map on its own) for
    columns the first row's mapping left uncovered — WITHOUT ever touching
    a column index or canonical key the first row already claimed.

    This is deliberately additive-only, unlike the flat merge tried before
    (see the comment where this is called): that approach let a second
    row's alias silently overwrite a canonical the first row had already
    mapped correctly, corrupting instrument_type on every row of a real
    document. By construction here, a column index already in
    `primary_map`, or a canonical already used by `primary_map`, can never
    receive a new assignment — the returned dict can only ADD columns the
    first row left blank, never replace one.

    Returns {} (a safe no-op) unless at least MIN_SECOND_HEADER_MATCHES new
    columns are found, so an ordinary DATA row that happens to contain one
    alias-like word (e.g. a Remarks cell that says "System") can't get
    mistaken for a header and silently start harvesting columns.
    """
    norm = [(c or '').strip().lower() for c in second_row]
    already_used_canonicals = set(primary_map.values())
    extra: Dict[int, str] = {}
    for canonical, aliases in IO_HEADER_ALIASES.items():
        if canonical in already_used_canonicals or canonical in extra.values():
            continue
        for idx, cell in enumerate(norm):
            if idx in primary_map or idx in extra:
                continue
            if any(a == cell or (len(a) > 2 and a in cell) for a in aliases):
                extra[idx] = canonical
                break
    return extra if len(extra) >= MIN_SECOND_HEADER_MATCHES else {}


def _extract_rows_with_pymupdf(
    pdf_bytes: bytes, page_indices: List[int],
) -> Tuple[List[Dict], List[int]]:
    """
    Returns (rows, pages_that_yielded_nothing).
    rows: list of dicts with keys from IO_LIST_CANONICAL_COLUMNS + 'page_number'.
    """
    rows: List[Dict] = []
    empty_pages: List[int] = []
    if not page_indices:
        return rows, empty_pages

    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    try:
        for pidx in page_indices:
            if pidx < 0 or pidx >= len(doc):
                continue
            page = doc[pidx]
            page_yielded = False
            # True only once a page has gone through the NORMAL clean
            # header/column table path at least once — that path is
            # already complete and reliable, so the text-pattern
            # supplement further below must never re-scan a page that hit
            # this (it would risk picking up stray tag-shaped text from a
            # title block that the real per-row loop correctly ignored).
            page_yielded_via_table = False
            # Tags already captured on this page from ANY source so far
            # (normal header/column table, or the labeled-cell fallback) —
            # lets the plain-text tag-pattern fallback further below still
            # run and fill in tags those sources missed, instead of being
            # skipped just because the page already yielded SOMETHING (bug
            # hit live: labeled-cell extraction only recovers each table's
            # first self-labeled record, not every row on the page — only
            # running one fallback per page silently dropped the rest).
            page_tags_seen: set = set()
            try:
                tables = page.find_tables()
            except Exception as exc:
                logger.warning("[IOWF] find_tables failed on IO page %d: %s", pidx, exc)
                empty_pages.append(pidx)
                continue

            for tbl in tables:
                try:
                    raw = tbl.extract()
                except Exception:
                    continue
                if not raw or len(raw) < 2:
                    continue

                header_map: Optional[Dict[int, str]] = None
                header_row_idx = -1
                # IO sheets often have 2-3 header rows (group label + actual
                # cols). Only the FIRST matching row decides column
                # ownership — a flat merge of every consecutive header-like
                # row was tried once and made things worse: it silently
                # let a second row's alias overwrite a canonical the first
                # row had already mapped correctly, corrupting
                # instrument_type on every row of a real document.
                for hi in range(min(4, len(raw))):
                    header_map = _build_header_map(raw[hi])
                    if header_map:
                        header_row_idx = hi
                        break
                if not header_map:
                    # No row in this table has ≥4 recognisable header
                    # columns — before giving up on the whole table, try
                    # the two Cable Schedule shapes that need no header
                    # row at all: self-labeled cells (only ever covers
                    # this table's FIRST record — see that function's own
                    # docstring) and the fixed column-index layout every
                    # record AFTER the first reliably follows (see
                    # _extract_rows_from_fixed_cable_schedule_columns's
                    # own docstring). Combined, together they cover the
                    # whole table; deduped by tag so neither can double up
                    # a record the other already captured.
                    shape_rows = (
                        _extract_rows_from_labeled_table_cells(raw, pidx + 1)
                        + _extract_rows_from_fixed_cable_schedule_columns(raw, pidx + 1)
                    )
                    seen_shape_tags: set = set()
                    deduped_shape_rows = []
                    for r in shape_rows:
                        t = r.get('tag_number')
                        if t and t not in seen_shape_tags:
                            seen_shape_tags.add(t)
                            deduped_shape_rows.append(r)
                    if deduped_shape_rows:
                        rows.extend(deduped_shape_rows)
                        page_yielded = True
                        page_tags_seen.update(seen_shape_tags)
                    continue

                # A second header row directly under the first (e.g. real
                # document 007's row 2: 'Service Description'/'To'/'Min.'/
                # 'Max.'/'H'/'HH'/'L'/'LL' sitting under group labels like
                # 'Sys. Range'/'Alarm / Trip Settings' in row 1) carries
                # columns the first row's own mapping never covers —
                # service_description was silently unreachable for exactly
                # this reason. Unlike the flat merge above, this ONLY adds
                # columns the first row left blank (see
                # _extend_map_with_second_header_row's own docstring for
                # why that's safe) and, when it finds enough of them to be
                # confident it's really a header (not a data row), advances
                # header_row_idx so this row is skipped as data too.
                #
                # Separately — and this is NOT the same thing — some real
                # documents (id 107: 'Instrument I/O List') alternate
                # between TWO DATA row shapes per instrument, each reusing
                # the SAME column indices for DIFFERENT fields (row1-shape
                # idx2='Instrument Type', row2-shape idx2='Service
                # Description' — a genuine index collision, not a gap).
                # The extend step above can't help there (it only fills
                # indices row1 left blank), so keep row2's own COMPLETE,
                # independent mapping too — used only when a data row has
                # no tag under the primary map, to reinterpret that same
                # row under row2's own field meanings instead of row1's.
                # A THIRD data row shape, separate again from both of the
                # above — real document 107 also has a row of "computed
                # DCS point" records (e.g. 113-F-1851, 113-P-3191) that
                # read as complete primary-shape rows (idx1 IS genuinely
                # this shape's own Tag Number position too — confirmed
                # against real data) but whose OTHER fields are actually
                # row4's header meanings (HMI Description/Status/HMI Tag/
                # Voltage LVL/NO-NC/.../Unit/...), not row1's. Bug hit
                # live: instrument_type showed a service-description
                # sentence, from_location showed a status code ('N-NEW'),
                # io_type showed an HMI tag, system showed an engineering
                # unit ('mmH2O') — every field one meaning off, because
                # they were read through header_map (row1's meanings)
                # instead of this row's own. header_row_idx + 3 is row4's
                # position here (row2 = header_row_idx+1, the "Loop
                # Number"-alone row = +2, row4 = +3) — captured from the
                # ORIGINAL header_row_idx, before the secondary-row-extend
                # step below can advance it.
                original_header_row_idx = header_row_idx
                third_map: Optional[Dict[int, str]] = None
                if original_header_row_idx + 3 < len(raw):
                    third_map = _build_header_map(raw[original_header_row_idx + 3])

                secondary_map: Optional[Dict[int, str]] = None
                if header_row_idx + 1 < len(raw):
                    secondary_map = _build_header_map(raw[header_row_idx + 1])
                    extra_map = _extend_map_with_second_header_row(raw[header_row_idx + 1], header_map)
                    if extra_map:
                        header_map = {**header_map, **extra_map}
                        header_row_idx += 1

                last_record: Optional[Dict[str, str]] = None
                for r in raw[header_row_idx + 1:]:
                    # A genuine data record populates several fields — a
                    # row with only a single non-blank cell is a leftover
                    # header/label artifact (e.g. a 3rd sub-header row
                    # naming just one column, like 'Loop Number' under
                    # the Tag Number column) that slipped past the header-
                    # matching loop above by not itself reaching the >= 4
                    # recognised-columns threshold. Bug hit live: exactly
                    # this produced a bogus tag_number='Loop Number' row.
                    if sum(1 for c in r if c and str(c).strip()) < 2:
                        continue
                    record: Dict[str, str] = {c: '' for c in IO_LIST_CANONICAL_COLUMNS}
                    for col_idx, canonical in header_map.items():
                        if col_idx < len(r):
                            record[canonical] = (r[col_idx] or '').strip()
                    if record.get('tag_number'):
                        # PDF cell text can carry a stray space where the
                        # underlying PDF briefly breaks text flow around a
                        # hyphen — real example hit live on an Instrument
                        # Cable Schedule table: '113-PT -3193B' instead of
                        # '113-PT-3193B'. That embedded space breaks BOTH
                        # the comment<->tag linker's regex match
                        # (services/comment_row_linker.py /
                        # config.TAG_NUMBER_REGEX) and any legend tag-
                        # format check, since neither tolerates whitespace
                        # inside a tag. Only collapses whitespace
                        # immediately beside a hyphen — leaves the rest of
                        # the value (and every other column) untouched.
                        record['tag_number'] = re.sub(r'\s*-\s*', '-', record['tag_number'])

                        # This row has a real tag under the PRIMARY map —
                        # but real document 107's "computed DCS point"
                        # rows (113-F-1851, 113-P-3191...) ALSO do, even
                        # though every OTHER field on them is actually
                        # row4-shaped (see third_map above), not row1-
                        # shaped. Telling the two apart from field
                        # POSITION alone isn't possible (idx1 genuinely is
                        # the tag position in both shapes) — but their
                        # CONTENT is reliably different: row1-shape's
                        # idx3 (From) holds a location word ('FIELD',
                        # 'DCS'); row3-shape's idx3 (Status, under
                        # third_map) holds a status word ('N-NEW',
                        # 'EXISTING') instead — confirmed against every
                        # real occurrence of both shapes in this document.
                        # When that status vocabulary shows up where a
                        # location should be, re-read the row's non-tag
                        # fields through third_map instead of trusting
                        # header_map's (wrong, for this shape) reading.
                        if third_map and record.get('from_location', '').strip().upper() in _ROW3_STATUS_WORDS:
                            tag_number = record['tag_number']
                            page_number_placeholder = record.get('page_number', '')
                            record = {c: '' for c in IO_LIST_CANONICAL_COLUMNS}
                            record['tag_number'] = tag_number
                            record['page_number'] = page_number_placeholder
                            for col_idx, canonical in third_map.items():
                                if col_idx < len(r) and (r[col_idx] or '').strip():
                                    record[canonical] = (r[col_idx] or '').strip()

                    if not record.get('tag_number'):
                        # No tag on THIS physical row under the PRIMARY
                        # map. Two different reasons this happens, needing
                        # two different reads of the same raw row:
                        #
                        # 1. A merged/spanning tag cell, or a description
                        #    that wraps onto the next physical row — the
                        #    row genuinely has no independent meaning of
                        #    its own; `record` (still primary-map-based)
                        #    is the right thing to merge in as-is.
                        #
                        # 2. This row uses the ALTERNATE shape (real
                        #    document 107: 'Instrument I/O List' — every
                        #    instrument's data splits across TWO rows that
                        #    reuse the SAME column indices for DIFFERENT
                        #    fields; row1-shape idx2='Instrument Type',
                        #    row2-shape idx2='Service Description'). Here
                        #    `record` is actively WRONG — it read this
                        #    row's Service Description text as if it were
                        #    Instrument Type. Re-read the same raw row `r`
                        #    through `secondary_map` (row2's own, separate
                        #    header mapping) instead; if that gives real
                        #    content, merge THAT, not `record`.
                        #
                        # Bug hit live from getting this wrong once
                        # already: an earlier flat merge of both header
                        # rows into ONE map let row2's alias silently
                        # overwrite row1's — instrument_type corrupted on
                        # every row. Keeping the two maps fully separate
                        # and only switching which one reads a given row
                        # (never merging the maps themselves) avoids that.
                        # Restricted to _ALT_ROW2_FIELDS (see its own
                        # comment) — NOT every canonical secondary_map
                        # happens to claim.
                        alt_record: Dict[str, str] = {}
                        if secondary_map:
                            for col_idx, canonical in secondary_map.items():
                                if (canonical in _ALT_ROW2_FIELDS and col_idx < len(r)
                                        and (r[col_idx] or '').strip()):
                                    alt_record[canonical] = (r[col_idx] or '').strip()
                        source = alt_record if alt_record else record
                        if last_record is not None:
                            for key, val in source.items():
                                if not val or key == 'page_number':
                                    continue
                                existing = last_record.get(key, '')
                                if not existing:
                                    last_record[key] = val
                                elif val != existing and val not in existing:
                                    last_record[key] = f'{existing} {val}'.strip()
                        continue  # merged above (or nothing to merge into) — never its own row

                    record['page_number'] = pidx + 1
                    rows.append(record)
                    last_record = record
                    page_yielded = True
                    page_yielded_via_table = True
                    if record.get('tag_number'):
                        page_tags_seen.add(record['tag_number'])

            # Cable-block diagrams contain drawing geometry rather than a
            # conventional tabular text layer. Only invoke OCR when native
            # table extraction yielded no canonical rows.
            if not page_yielded:
                drawing_text = (page.get_text('text') or '').lower()
                if ('instrument cable block diagram' in drawing_text
                        and 'diagram layout' in drawing_text):
                    ocr_rows = _extract_drawing_rows_with_local_ocr(page, pidx + 1)
                    if ocr_rows:
                        rows.extend(ocr_rows)
                        page_yielded = True

            # Last resort: pattern-match tag numbers directly out of the
            # page's raw text. Runs even if this page already yielded
            # rows from the labeled-cell fallback (that one only recovers
            # each table's first self-labeled record on this document
            # shape, not every row on the page) — but NEVER when the page
            # already went through the normal clean header/column path
            # (page_yielded_via_table), which is already complete and
            # doesn't need — or want — a speculative regex pass over the
            # same page's title-block text on top of it. Only tags not
            # already captured get added.
            if not page_yielded_via_table:
                text_rows = [
                    r for r in _extract_rows_from_text_patterns(page.get_text('text') or '', pidx + 1)
                    if r.get('tag_number') not in page_tags_seen
                ]
                if text_rows:
                    rows.extend(text_rows)
                    page_yielded = True

            if not page_yielded:
                empty_pages.append(pidx)
    finally:
        doc.close()
    logger.info("[IOWF] PyMuPDF extracted %d IO rows from %d pages "
                "(%d pages yielded nothing)",
                len(rows), len(page_indices), len(empty_pages))
    return rows, empty_pages


def _vision_fallback_for_pages(
    pdf_bytes: bytes, page_indices: List[int],
) -> List[Dict]:
    """
    OPT-IN vision fallback. Returns [] by default to keep cost at zero.

    To activate: set INSTRUMENT_IO_ENABLE_VISION_FALLBACK=true AND extend this
    function to call OpenAI gpt-4o-mini with a soft-coded prompt that targets
    the IO_LIST_CANONICAL_COLUMNS schema. Hard-capped to
    VISION_MAX_PAGES_PER_DOC pages per document.
    """
    if not ENABLE_VISION_FALLBACK:
        return []
    if not page_indices:
        return []
    capped = page_indices[:VISION_MAX_PAGES_PER_DOC]
    logger.warning(
        "[IOWF] Vision fallback requested for %d page(s) but is not yet "
        "implemented. Capped list would be: %s", len(capped), capped,
    )
    # Implementation slot reserved — keep returning [] to guarantee $0 cost
    # until the user explicitly opts in to vision spend.
    return []


def extract_io_rows_from_pages(
    pdf_bytes: bytes, page_indices: List[int],
) -> List[Dict]:
    """Public entrypoint — combines free path + opt-in vision fallback."""
    rows, empty = _extract_rows_with_pymupdf(pdf_bytes, page_indices)
    if empty:
        rows.extend(_vision_fallback_for_pages(pdf_bytes, empty))
    return rows
