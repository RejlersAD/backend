"""
Valve MTO — P&ID PDF parsing (Stage 1) and vector symbol candidates.

Built on PyMuPDF (fitz). Everything here is TEXT-LAYER / VECTOR parsing —
no OCR, no vision calls. The extraction pipeline (engine.py) consumes
these structures; detection heuristics are deliberately isolated in this
module so they can be tuned against the reference P&ID
(50249-05-AD-1050) without touching views/models.

Key entry points
────────────────
parse_drawing(pdf_bytes, filename)  → full Stage-1 drawing summary dict
apply_line_pattern(labels, preset, custom_regex)
                                    → per-label parse results + coverage
detect_valve_symbol_candidates(doc) → vector ellipse candidates (tunable)
render_evidence_crop(doc, page, rect) → PNG bytes for the evidence drawer

NOTE: cloud detection and symbol-group detection are heuristic v1 — the
reference drawing was not available when this was written, so thresholds
(cloud arc counts, ellipse bbox size ~20×10 pt) are starting points,
clearly marked TUNABLE below.
"""
from __future__ import annotations

import base64
import io
import logging
import re
from dataclasses import dataclass, field

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────
# Line-number pattern presets (§3). Each preset is a compiled regex with
# NAMED groups; which names a preset provides varies (e.g. the unit-based
# presets have no 'service'). 'size'/'class' are the groups the register
# cares about most; every preset provides them.
# ─────────────────────────────────────────────────────────────────────────
_SIZE = r'(?P<size>\d{1,2}(?:-\d/\d)?)["″]?'
_CLASS = r'(?P<class>[A-Z0-9]{3,7})'
_INSUL = r'(?:-(?P<insul>[A-Z]{1,2}\d?))?'

LINE_PATTERN_PRESETS = {
    # P-51220-18"-31170-C
    'service-seq-size-class-insul': (
        rf'(?P<fluid>[A-Z]{{1,4}})-(?P<seq>\d{{3,6}})-{_SIZE}-{_CLASS}{_INSUL}'
    ),
    # 8"-2600-P-305-11051YR-I
    'size-unit-fluid-seq-class-insul': (
        rf'{_SIZE}-(?P<unit>\d{{3,4}})-(?P<fluid>[A-Z]{{1,4}})-(?P<seq>\d{{3,6}})-{_CLASS}{_INSUL}'
    ),
    # 2600-AY-005-17091R-V
    'unit-fluid-seq-class-insul': (
        rf'(?P<unit>\d{{3,4}})-(?P<fluid>[A-Z]{{1,4}})-(?P<seq>\d{{3,6}})-{_CLASS}{_INSUL}'
    ),
    # 6-PL-DC3N-8166-P
    'size-fluid-class-seq-insul': (
        rf'(?P<size>\d{{1,2}})-(?P<fluid>[A-Z]{{1,4}})-{_CLASS}-(?P<seq>\d{{3,6}}){_INSUL}'
    ),
}
# Required named groups for a Custom preset regex (§3).
CUSTOM_PATTERN_GROUPS = ('size', 'class', 'fluid', 'seq', 'insul')

# Title-block probes (§3). Kept permissive — a title block is free text,
# so each probe is "find the first plausible token", not a strict schema.
RE_COMPANY_DRAWING_NO = re.compile(r'\b(\d{5}-\d{2}-[A-Z]{2}-\d{4})\b')
RE_CONTRACTOR_NO = re.compile(r'\b([A-Z]{2}\d{3,5}-\d{5}-\d{2}-[A-Z]{2}-\d{4})\b')
RE_PROJECT_NO = re.compile(r'\b(\d{5}-\d{5})\b')
RE_SHEET = re.compile(r'\b(\d{1,2})\s*(?:of|OF|Of)\s*(\d{1,2})\b')
RE_REV = re.compile(r'\b(?:REV\.?|REVISION)\s*[:.]?\s*([A-Z]?\d{1,2})\b', re.IGNORECASE)
RE_KNOWN_COMPANY = re.compile(r'\b(ADNOC(?:\s+[A-Z&]+)*)\b')

# A candidate line label: contains at least two dash-separated segments,
# one numeric seq and one class-ish token, no spaces-heavy prose.
RE_LINE_LABELISH = re.compile(
    r'\b[A-Z0-9]{1,6}(?:-[A-Z0-9"″]{1,8}){2,6}\b'
)
RE_TAGISH = re.compile(
    r'\b(?:\d{2}[A-Z]{2}\d[A-Z0-9]*|[A-Z]{1,3}V?-\d{2,4}[A-Z]?|[A-Z]{2,4}-\d{3,5}[A-Z]?)\b'
)


@dataclass
class PageText:
    page_no: int
    text: str
    words: list = field(default_factory=list)  # (x0,y0,x1,y1,word,...)


def _page_texts(doc) -> list[PageText]:
    out = []
    for i in range(doc.page_count):
        page = doc.load_page(i)
        out.append(PageText(
            page_no=i,
            text=page.get_text('text') or '',
            words=page.get_text('words') or [],
        ))
    return out


def parse_title_block(pages: list[PageText]) -> dict:
    """Title-block text search. P&ID title blocks live bottom-right of the
    LAST (or only) page — but the tokens are distinctive enough that a
    whole-document search is safe; first match wins.

    Returns keys: company_drawing_no, contractor_no, rev, sheet, title,
    company, project — any may be '' when not found (never guessed)."""
    full = '\n'.join(p.text for p in pages)
    result = {
        'company_drawing_no': '', 'contractor_no': '', 'rev': '',
        'sheet': '', 'title': '', 'company': '', 'project': '',
    }
    m = RE_COMPANY_DRAWING_NO.search(full)
    if m:
        result['company_drawing_no'] = m.group(1)
    m = RE_CONTRACTOR_NO.search(full)
    if m:
        result['contractor_no'] = m.group(1)
    m = RE_PROJECT_NO.search(full)
    if m:
        result['project'] = m.group(1)
    m = RE_REV.search(full)
    if m:
        result['rev'] = m.group(1).upper()
    m = RE_SHEET.search(full)
    if m:
        result['sheet'] = f'{m.group(1)} of {m.group(2)}'
    m = RE_KNOWN_COMPANY.search(full)
    if m:
        result['company'] = m.group(1).strip()
    # Title: the longest ALL-CAPS-ish line containing 'P&ID' or 'PIPING'
    # near the drawing number. Heuristic v1 — TUNABLE with the reference.
    if result['company_drawing_no']:
        for line in full.splitlines():
            s = line.strip()
            if len(s) >= 15 and ('P&ID' in s or 'PIPING' in s) and s.upper() == s:
                result['title'] = s
                break
    return result


def extract_line_labels(pages: list[PageText]) -> list[dict]:
    """Pull every text token that looks like a line number, with page +
    bbox (bbox from the word positions; a label may span several words,
    so we first try whole-word matches, then multi-word joins).

    v1: word-level regex match. Multi-part labels split across words are
    joined when adjacent on the same line — TUNABLE with the reference.
    """
    labels = []
    for pt in pages:
        # Group words into visual lines (same block/line index).
        words = pt.words
        line_map = {}
        for w in words:
            key = (w[5], w[6])  # block_no, line_no
            line_map.setdefault(key, []).append(w)
        for key, ws in line_map.items():
            ws.sort(key=lambda w: w[7])
            text = ' '.join(w[4] for w in ws)
            for m in RE_LINE_LABELISH.finditer(text):
                token = m.group(0)
                if not any(ch.isdigit() for ch in token):
                    continue
                labels.append({
                    'text': token,
                    'page': pt.page_no + 1,
                })
    # De-duplicate identical labels on the same page (leaders repeat).
    seen = set()
    unique = []
    for lb in labels:
        k = (lb['text'], lb['page'])
        if k not in seen:
            seen.add(k)
            unique.append(lb)
    return unique


def apply_line_pattern(labels: list[dict], preset: str, custom_regex: str = '') -> dict:
    """Parse each label with the selected preset (or custom named-group
    regex). Returns {preset, parsed: [...], parsed_count, total,
    unparsed: [...], classes: [...]}."""
    if preset == 'custom':
        try:
            rx = re.compile(custom_regex)
        except re.error as exc:
            return {'error': f'Invalid custom regex: {exc}'}
    else:
        pattern = LINE_PATTERN_PRESETS.get(preset)
        if not pattern:
            return {'error': f'Unknown preset: {preset}'}
        rx = re.compile(pattern)

    parsed, unparsed, classes = [], [], set()
    for lb in labels:
        m = rx.search(lb['text'])
        if m:
            groups = {k: v for k, v in m.groupdict().items() if v}
            parsed.append({**lb, 'groups': groups})
            if groups.get('class'):
                classes.add(groups['class'])
        else:
            unparsed.append(lb)
    return {
        'preset': preset,
        'parsed': parsed,
        'parsed_count': len(parsed),
        'total': len(labels),
        'unparsed': unparsed,
        'classes': sorted(classes),
    }


def detect_pdf_type(doc) -> str:
    """'vector' when a text layer exists on ≥1 page, else 'raster'."""
    for i in range(doc.page_count):
        if (doc.load_page(i).get_text('text') or '').strip():
            return 'vector'
    return 'raster'


# ─────────────────────────────────────────────────────────────────────────
# Cloud detection (§3) — TUNABLE v1.
# A revision/scope cloud is drawn as a run of small arcs. PyMuPDF reports
# each arc as a curve item in page.get_drawings(); a path with many short
# curve segments in a compact bbox is treated as a cloud candidate.
# Scope vs revision split: revision clouds sit next to a revision tag
# (triangle / rev number); scope clouds enclose valves/equipment. v1
# cannot reliably distinguish → both are reported with a 'kind' guess.
# ─────────────────────────────────────────────────────────────────────────
CLOUD_MIN_CURVES = 8        # arcs needed before a path counts as a cloud
CLOUD_MAX_ARC_LEN = 40.0    # pt — cloud arcs are short


def detect_clouds(doc) -> dict:
    scope, revision = 0, 0
    thumbnails = []
    for i in range(doc.page_count):
        page = doc.load_page(i)
        for d in page.get_drawings():
            curves = [it for it in d['items'] if it[0] == 'c']
            if len(curves) < CLOUD_MIN_CURVES:
                continue
            rect = d['rect']
            if rect.width < 30 or rect.height < 20:
                continue
            arc_lens = [(abs(it[3].x - it[1].x) + abs(it[3].y - it[1].y)) for it in curves]
            if arc_lens and (sum(arc_lens) / len(arc_lens)) > CLOUD_MAX_ARC_LEN:
                continue
            # Heuristic kind: clouds near a pure-number text (rev tag) are
            # revision clouds — v1 simplification, TUNABLE.
            nearby = page.get_text('text', clip=fitz.Rect(
                rect.x0 - 20, rect.y0 - 20, rect.x1 + 20, rect.y1 + 20,
            )) or ''
            kind = 'revision' if re.search(r'\b[A-Z]?\d{1,2}\b', nearby) else 'scope'
            if kind == 'revision':
                revision += 1
            else:
                scope += 1
            try:
                pix = page.get_pixmap(clip=rect, matrix=fitz.Matrix(0.5, 0.5))
                thumbnails.append({
                    'page': i + 1,
                    'kind': kind,
                    'rect': [rect.x0, rect.y0, rect.x1, rect.y1],
                    'png_b64': base64.b64encode(pix.tobytes('png')).decode('ascii'),
                })
            except Exception:
                thumbnails.append({'page': i + 1, 'kind': kind,
                                   'rect': [rect.x0, rect.y0, rect.x1, rect.y1]})
    return {'scope': scope, 'revision': revision, 'thumbnails': thumbnails}


def count_tags_and_notes(pages: list[PageText]) -> dict:
    tags = 0
    notes = 0
    for pt in pages:
        tags += len(RE_TAGISH.findall(pt.text))
        notes += len(re.findall(r'^\s*\d{1,2}[.)]\s+\S', pt.text, flags=re.MULTILINE))
    return {'tags': tags, 'notes': notes}


# ─────────────────────────────────────────────────────────────────────────
# Vector valve symbol candidates — TUNABLE v1 (engine.py consumes this).
# Gate/ball/butterfly valves on vector P&IDs are drawn as paired
# triangles (bow-tie) or ellipse outlines (~20×10 pt on the reference).
# NOT valves (§5): spectacle blinds, spec-break arrows, reducers,
# flanged connections '||', thermowells, instrument bubbles, fittings.
# v1 detects closed ellipse-ish paths in a plausible size band and
# records the raw shape metrics so the engine can filter further.
# ─────────────────────────────────────────────────────────────────────────
ELLIPSE_MIN_W, ELLIPSE_MAX_W = 8.0, 40.0    # pt — TUNABLE (ref ~20)
ELLIPSE_MIN_H, ELLIPSE_MAX_H = 4.0, 30.0    # pt — TUNABLE (ref ~10)


def detect_valve_symbol_candidates(doc) -> list[dict]:
    """Return candidate valve symbols: [{page, x, y, w, h, fill, curves}].
    Only closed ellipse-like drawings in the size band. Grouping into
    symbol groups (hollow vs filled ellipse etc.) happens in engine.py.
    """
    candidates = []
    for i in range(doc.page_count):
        page = doc.load_page(i)
        for d in page.get_drawings():
            rect = d['rect']
            w, h = rect.width, rect.height
            # Allow rotated ellipses (H/V swap) — check both orientations.
            in_band = (
                (ELLIPSE_MIN_W <= w <= ELLIPSE_MAX_W and ELLIPSE_MIN_H <= h <= ELLIPSE_MAX_H)
                or (ELLIPSE_MIN_W <= h <= ELLIPSE_MAX_W and ELLIPSE_MIN_H <= w <= ELLIPSE_MAX_H)
            )
            if not in_band:
                continue
            curves = [it for it in d['items'] if it[0] == 'c']
            if len(curves) < 3:  # an ellipse needs ≥ ~4 beziers
                continue
            if not d.get('closePath', True) and d.get('fill') is None:
                continue
            candidates.append({
                'page': i + 1,
                'x': (rect.x0 + rect.x1) / 2,
                'y': (rect.y0 + rect.y1) / 2,
                'w': w, 'h': h,
                'fill': bool(d.get('fill')),
                'curves': len(curves),
                'rect': [rect.x0, rect.y0, rect.x1, rect.y1],
            })
    return candidates


def render_evidence_crop(doc, page_no: int, rect, zoom: float = 3.0) -> bytes:
    """PNG crop around a symbol bbox (padded) for the evidence drawer."""
    page = doc.load_page(page_no - 1)
    r = fitz.Rect(rect)
    r.x0 -= 30; r.y0 -= 30; r.x1 += 30; r.y1 += 30
    r &= page.rect
    pix = page.get_pixmap(clip=r, matrix=fitz.Matrix(zoom, zoom))
    return pix.tobytes('png')


def parse_drawing(pdf_bytes: bytes, filename: str = '') -> dict:
    """Full Stage-1 parse. Never raises on a bad PDF — returns
    {'error': ...} instead so the view can 400 cleanly."""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    except Exception as exc:
        return {'error': f'Could not open PDF: {exc}'}
    try:
        if doc.page_count == 0:
            return {'error': 'PDF has no pages.'}
        pages = _page_texts(doc)
        pdf_type = detect_pdf_type(doc)
        title_block = parse_title_block(pages)
        line_labels = extract_line_labels(pages)
        clouds = detect_clouds(doc)
        counts = count_tags_and_notes(pages)
        return {
            'filename': filename,
            'page_count': doc.page_count,
            'pdf_type': pdf_type,
            'title_block': title_block,
            'line_labels': line_labels,
            'clouds': clouds,
            'counts': {
                'line_labels': len(line_labels),
                'tags': counts['tags'],
                'notes': counts['notes'],
            },
        }
    finally:
        doc.close()
