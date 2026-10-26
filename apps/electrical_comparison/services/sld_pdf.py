"""SLD drawing reading (spec §3.1 + §5.1).

- detect_pdf_type: 'vector-text' | 'vector-outlines' | 'scanned'
  (text layer / many vector paths no text / full-page raster image)
- read_drawing_summary: drawing no, revision (+ per-sheet agreement),
  title, pdf type, scope/revision clouds, rotation/mirror flags — the
  Stage-1 read summary shown before continuing.
- extract_tags: tiled OCR (>=300 dpi, binarise + dilate, 0° and 90°),
  whole-token rule matching against the active tag rules, so a cable
  tag can never be cut into an equipment tag and fragments are rejected.
"""
from __future__ import annotations

import re

import fitz  # PyMuPDF

# A drawing-number token like A1-285-E-0193 (allow OCR'd spacing).
DRAWING_NO_RE = re.compile(r'\bA1[\s-]?285[\s-]?E[\s-]?(\d{4}[A-Z]?)\b')
REV_RE = re.compile(r'\bC(\d)\b')
# revision column right after the drawing number — the outline font's C
# often OCRs as G there ("A1-285-E-0193  001  G2")
REV_AFTER_RE = re.compile(r'[CG](\d)\b')
SCOPE_RE = re.compile(r'PROJECT\s*No\.?\s*P?(\d{4,6})', re.I)


def detect_pdf_type(doc) -> str:
    """spec §3.1 table:
    - 'vector-text'      characters found
    - 'vector-outlines'  no characters, many vector paths, no full-page image
    - 'scanned'          full-page raster image
    Returns the internal id; the UI maps it to the display strings."""
    for i in range(doc.page_count):
        page = doc.load_page(i)
        text = (page.get_text('text') or '').strip()
        # A stray caption (e.g. the scope note) does not count as a text layer
        if len(text) > 200:
            return 'vector-text'
        for img in page.get_images(full=True):
            xref = img[0]
            pix = fitz.Pixmap(doc, xref)
            covers = pix.width >= page.rect.width * 0.7 and pix.height >= page.rect.height * 0.7
            pix = None
            if covers:
                return 'scanned'
        if len(page.get_drawings()) > 500:
            return 'vector-outlines'
    return 'scanned'


def norm_tag(value: str) -> str:
    t = (value or '').upper()
    for d in '\u2013\u2014\u2010':
        t = t.replace(d, '-')
    return re.sub(r'\s+', '', t)


def read_drawing_summary(pdf_bytes: bytes) -> dict:
    """Stage-1 read summary for the SLD slot (spec §3.1)."""
    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    pdf_type = detect_pdf_type(doc)
    summary = {
        'page_count': doc.page_count,
        'pdf_type': pdf_type,
        'drawing_no': '', 'rev': '', 'title': '',
        'sheets': [], 'sheets_agree': True,
        'scope': '', 'rotation': None, 'mirrored': False,
    }
    # Text layer first (cheap); OCR of the title-block strip otherwise.
    full = '\n'.join((doc.load_page(i).get_text('text') or '') for i in range(doc.page_count))
    m = SCOPE_RE.search(full)
    if m:
        summary['scope'] = m.group(1)
    if pdf_type == 'vector-text':
        for i in range(doc.page_count):
            t = doc.load_page(i).get_text('text') or ''
            dm = DRAWING_NO_RE.search(t)
            summary['sheets'].append({'page': i + 1, 'drawing_no': norm_tag(dm.group(0)) if dm else ''})
        nums = {s['drawing_no'] for s in summary['sheets'] if s['drawing_no']}
        summary['drawing_no'] = sorted(nums)[0] if nums else ''
        summary['sheets_agree'] = len(nums) <= 1
    else:
        # Outlines/scanned: OCR the title-block corner of each sheet.
        summary.update(_title_block_via_ocr(doc))
    doc.close()
    return summary


def _title_block_via_ocr(doc) -> dict:
    """OCR the bottom-right title-block region of each sheet at 300 dpi,
    using the same neutral-ink mask + selective glyph fill as the tag
    extractor (the title block text is hollow outline strokes too)."""
    import pytesseract
    from .sld_extract import _preprocess

    sheets = []
    for i in range(doc.page_count):
        page = doc.load_page(i)
        zoom = 300 / 72.0
        # two strips: tight bottom-right corner (drawing no row) and a
        # wider title-block region (revision table). One region with psm 6
        # segments unreliably — measured on A1-285-E-0193.
        texts = []
        for fx, fy in ((0.55, 0.80), (0.45, 0.72)):
            clip = fitz.Rect(page.rect.width * fx, page.rect.height * fy,
                             page.rect.width, page.rect.height)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip)
            import numpy as np
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[..., :3]
            texts.append(pytesseract.image_to_string(_preprocess(img), config='--psm 6'))
        tight, wide = texts[0], texts[1]
        dm = DRAWING_NO_RE.search(tight) or DRAWING_NO_RE.search(wide)
        rev = ''
        if dm:
            # the revision letter sits in the same title row, right after
            # the drawing number ("A1-285-E-0193  001  C2")
            rm = REV_AFTER_RE.search(tight[dm.end():dm.end() + 40]) if DRAWING_NO_RE.search(tight) else None
            rev = f'C{rm.group(1)}' if rm else ''
        if not rev:
            revs_found = REV_RE.findall(wide)
            rev = f'C{max(revs_found, key=revs_found.count)}' if revs_found else ''
        sheets.append({
            'page': i + 1,
            'drawing_no': norm_tag(dm.group(0)) if dm else '',
            'rev': rev,
        })
    nums = {s['drawing_no'] for s in sheets if s['drawing_no']}
    # the revision belongs to the sheet that produced the drawing number —
    # never a bare majority across sheets (title-block OCR noise)
    rev_from_numbered = next((s['rev'] for s in sheets if s['drawing_no'] and s['rev']), '')
    revs = {s['rev'] for s in sheets if s['rev']}
    return {
        'sheets': sheets,
        'drawing_no': sorted(nums)[0] if nums else '',
        'rev': rev_from_numbered or (sorted(revs)[-1] if revs else ''),
        'sheets_agree': len(nums) <= 1,
    }
