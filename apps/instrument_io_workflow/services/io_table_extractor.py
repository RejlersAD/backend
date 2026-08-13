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
    r'\b(\d{2,4})\s*[-\u2010-\u2015]\s*([A-Z]{1,4})\s*[-\u2010-\u2015]\s*(\d{3,5}[A-Z]?)\b',
    re.I,
)
_CABLE_RE = re.compile(r'\b(\d{2,4})\s+([A-Z])\s+(\d{2})\s+(\d{3})\b')
_UNIT_RE = re.compile(r'\bUNIT\s*:?\s*(\d{2,4})\b')


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


def _build_header_map(header_cells: List[str]) -> Optional[Dict[int, str]]:
    """{col_index → canonical_name}; require ≥ 4 recognised columns."""
    norm = [(c or '').strip().lower() for c in header_cells]
    mapping: Dict[int, str] = {}
    for canonical, aliases in IO_HEADER_ALIASES.items():
        for idx, cell in enumerate(norm):
            if idx in mapping:
                continue
            if any(a == cell or a in cell for a in aliases):
                mapping[idx] = canonical
                break
    return mapping if len(mapping) >= 4 else None


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
                # IO sheets often have 2-3 header rows (group label + actual cols).
                for hi in range(min(4, len(raw))):
                    header_map = _build_header_map(raw[hi])
                    if header_map:
                        header_row_idx = hi
                        break
                if not header_map:
                    continue

                for r in raw[header_row_idx + 1:]:
                    record: Dict[str, str] = {c: '' for c in IO_LIST_CANONICAL_COLUMNS}
                    for col_idx, canonical in header_map.items():
                        if col_idx < len(r):
                            record[canonical] = (r[col_idx] or '').strip()
                    if not record.get('tag_number'):
                        continue  # tag number is the natural key
                    record['page_number'] = pidx + 1
                    rows.append(record)
                    page_yielded = True

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
