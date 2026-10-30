"""§6.1.1 — page reading: spans, words, strokes, and the hidden smart-tag layer.

The reference P&IDs carry a hidden layer of white 1.1 pt texts
(`IN#13-LT-7201A`, `EQ#13-VV-705`, `PI#…`) placed exactly on the instrument
bubbles / equipment bodies they name. The engine treats that layer as the
ground-truth tag list and confirms each visible bubble pair against it.
"""
import re
from dataclasses import dataclass, field

from . import settings as S


@dataclass
class Span:
    text: str
    bbox: tuple           # (x0, y0, x1, y1)
    size: float
    color: int
    direction: tuple      # (dx, dy) of the writing direction
    page: int

    @property
    def cx(self):
        return (self.bbox[0] + self.bbox[2]) / 2

    @property
    def cy(self):
        return (self.bbox[1] + self.bbox[3]) / 2

    @property
    def vertical(self):
        return abs(self.direction[1]) > abs(self.direction[0])


@dataclass
class Stroke:
    """A straight segment extracted from a drawing path."""
    p0: tuple
    p1: tuple
    width: float
    color: int
    page: int
    dashed: bool = False
    fill: bool = False
    closed_path: bool = False
    path_bbox: tuple = None
    path_item_count: int = 0
    path_area: float = 0.0
    shape_idx: int = -1          # index into PageData.shapes (parent path)

    @property
    def length(self):
        return ((self.p1[0] - self.p0[0]) ** 2 + (self.p1[1] - self.p0[1]) ** 2) ** 0.5

    @property
    def horizontal(self):
        return abs(self.p1[1] - self.p0[1]) < 0.5

    @property
    def vertical(self):
        return abs(self.p1[0] - self.p0[0]) < 0.5


@dataclass
class PathShape:
    """A whole vector path — used for bubble outlines and equipment bodies."""
    bbox: tuple
    width: float
    color: int
    fill_color: int
    item_count: int
    page: int
    closed: bool
    items: list = field(default_factory=list)
    idx: int = -1

    @property
    def area(self):
        return max(0.0, (self.bbox[2] - self.bbox[0]) * (self.bbox[3] - self.bbox[1]))

    @property
    def square_bbox(self):
        w = self.bbox[2] - self.bbox[0]
        h = self.bbox[3] - self.bbox[1]
        return 0.75 <= (w / h if h else 0) <= 1.33


@dataclass
class PageData:
    page_no: int
    width: float
    height: float
    spans: list           # all visible + hidden text spans
    smart_tags: list      # IN#/EQ# hidden spans only
    strokes: list         # straight line segments
    shapes: list          # full paths (bubbles, equipment, frames)
    text: str             # whole-page text (newlines kept)


def _rgb(color_int):
    return ((color_int >> 16) & 255, (color_int >> 8) & 255, color_int & 255)


def is_smart_tag(span: Span) -> bool:
    """White, tiny, prefixed — the hidden smart-tag layer signature."""
    if not span.text.startswith(S.SMART_TAG_PREFIXES):
        return False
    return span.size <= S.SMART_TAG_MAX_SIZE_PT and span.color == S.SMART_TAG_WHITE


def _flatten_path_items(items):
    """Expand a PyMuPDF path's items into straight segments + shape stats."""
    segments = []
    for it in items:
        kind = it[0]
        if kind == 'l':                       # line
            segments.append((it[1], it[2]))
        elif kind == 're':                    # rect → 4 edges
            r = it[1]
            corners = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
            segments += [(corners[i], corners[(i + 1) % 4]) for i in range(4)]
        elif kind == 'qu':                    # quad → 4 edges
            q = it[1]
            pts = [(q.ul.x, q.ul.y), (q.ur.x, q.ur.y), (q.lr.x, q.lr.y), (q.ll.x, q.ll.y)]
            segments += [(pts[i], pts[(i + 1) % 4]) for i in range(4)]
        # curves ('c') contribute to the item count but not straight segments
    return segments


def read_page(doc, page_no: int) -> PageData:
    page = doc.load_page(page_no)
    spans = []
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines', []):
            direction = line.get('dir', (1.0, 0.0))
            for sp in line['spans']:
                text = sp['text'].strip()
                if not text:
                    continue
                spans.append(Span(
                    text=text, bbox=tuple(sp['bbox']), size=sp['size'],
                    color=sp.get('color', 0), direction=direction, page=page_no,
                ))
    smart = [s for s in spans if is_smart_tag(s)]

    strokes, shapes = [], []
    for dr in page.get_drawings():
        width = dr.get('width') or 0.0
        # PyMuPDF colours are float triples; normalise to an int
        def _ci(c):
            if c is None:
                return -1
            r, g, b = (int(round(v * 255)) for v in c[:3])
            return (r << 16) | (g << 8) | b
        stroke_c = _ci(dr.get('color'))
        fill_c = _ci(dr.get('fill'))
        rect = dr['rect']
        items = dr.get('items', [])
        shape = PathShape(
            bbox=(rect.x0, rect.y0, rect.x1, rect.y1), width=width,
            color=stroke_c, fill_color=fill_c, item_count=len(items),
            page=page_no, closed=any(it[0] in ('re', 'qu') for it in items) or bool(dr.get('closePath')),
            items=items, idx=len(shapes),
        )
        shapes.append(shape)
        shape_idx = len(shapes) - 1
        dashed = bool(dr.get('dashes') and dr['dashes'] != '[] 0')
        for p0, p1 in _flatten_path_items(items):
            seg = Stroke(
                p0=(p0.x, p0.y) if hasattr(p0, 'x') else tuple(p0),
                p1=(p1.x, p1.y) if hasattr(p1, 'x') else tuple(p1),
                width=width, color=stroke_c, page=page_no, dashed=dashed,
                fill=fill_c != -1,
                closed_path=shape.closed, path_bbox=shape.bbox,
                path_item_count=len(items), path_area=shape.area,
                shape_idx=shape_idx,
            )
            strokes.append(seg)

    return PageData(
        page_no=page_no, width=page.rect.width, height=page.rect.height,
        spans=spans, smart_tags=smart, strokes=strokes, shapes=shapes,
        text=page.get_text('text') or '',
    )


_CAUSE_NOTE_RE = re.compile(r'^\s*C\s*:\s*(\S.*)$')
_NOTE_TAG_RE = re.compile(r'\b\d{2}-[A-Z]{1,6}-\d{3,5}[A-Z]?\b')


def extract_cause_notes(page: PageData) -> list:
    """Rev 2 — cause notes: `C : <text>` lines on the sheet (e.g.
    `C : 13-PSLL-7204 / PSA2FGCSD`). Returns one dict per note line with the
    tags mentioned in it (alarm-trip cause-note checks)."""
    notes = []
    for line in (page.text or '').splitlines():
        m = _CAUSE_NOTE_RE.match(line)
        if not m:
            continue
        text = m.group(1).strip()
        notes.append({
            'sheet': page.page_no + 1,
            'page': page.page_no + 1,
            'text': text,
            'tags': sorted(set(_NOTE_TAG_RE.findall(text.upper()))),
        })
    return notes


def read_document(pdf_path: str) -> list:
    import pymupdf
    doc = pymupdf.open(pdf_path)
    try:
        return [read_page(doc, i) for i in range(doc.page_count)]
    finally:
        doc.close()
