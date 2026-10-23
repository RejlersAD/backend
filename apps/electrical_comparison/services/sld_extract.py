"""Reading the SLD (spec §5.1).

Text source:
- text layer when present;
- otherwise tiled OCR at >=300 dpi: neutral-ink mask (drops red cloud
  strokes), binarise, and SELECTIVE glyph fill (hole-filling only on
  glyph-sized components) because the reference drawing renders text as
  hollow vector outlines;
- 0° and 90° passes (load descriptions are vertical).

Acceptance: a token becomes a tag only if the WHOLE token matches a tag
rule (cable rule first — a cable can never become equipment). Fragments
like '285-U-503' or '285-U-503B-FEEDER' never match. Normalisation:
uppercase, unicode dashes -> '-', no spaces, P3ED08 == P-3ED08.

Per tag: sheet, x, y (PDF points), category, fed-from board, rating and
an evidence crop (stored as a file; cropUrl in the payload).
"""
from __future__ import annotations

import re

import fitz
import numpy as np
import pytesseract
from PIL import Image
from pytesseract import Output
from scipy import ndimage

DPI = 300
TILE = 2000
OVERLAP = 200

# ── tag rules (§4.2, project area injected) — order matters ─────────────
# Segments are strict ([0-9]+[A-Z]{0,2}) so OCR letter-for-digit noise
# (e.g. O for 0) fails the raw match and triggers the correction pass.
RULE_PATTERNS = [
    ('cable', r'^\d{3}-[A-Z]{2,3}-\d+[A-Z]{0,2}-(?:PHV|P)-\d{3}$'),
    ('board', r'^\d{3}-(?:SB|DB)-\d+[A-Z]{0,2}$'),
    ('board-u', r'^\d{3}-U-\d{3}[A-Z]{0,2}$'),  # geometry-aware: title=board, feeder=load
    ('placeholder', r'^(?:\d{3}-U-)?(?:XXXX|YYYY)$'),
    ('transformer', r'^\d{3}-TF-\d+[A-Z]?$|^\d{3}-U-558[A-Z]$'),
    ('generator', r'^\d{3}-GD-\d+[A-Z]$'),
    ('subpanel', r'^[LP]-?[A-Z]*\d[0-9A-Z]*(?:-[0-9A-Z]+)*$'),
    ('motor', r'^\d{3}-(?:PM|UM|NM|NPM|KM|RM)-\d+[A-Z]{0,2}$'),
    ('load', r'^\d{3}-(?:H|EH|E|Z|G)-\d+[A-Z]{0,2}$'),
]
GENERIC_TAG = re.compile(r'^\d{3}-[A-Z]{2,3}-[0-9A-Z]+$')
KW_RE = re.compile(r'^(\d+(?:\.\d+)?)\s*kW$', re.I)

TO_DIGIT = {'O': '0', 'Q': '0', 'I': '1', 'L': '1', 'S': '5', 'B': '8',
            'G': '6', 'Z': '2', 'A': '4', 'g': '9', 'o': '0'}
TO_LETTER = {'0': 'O', '1': 'I', '5': 'S', '8': 'B', '6': 'G', '2': 'Z', '4': 'A'}


def norm_tag(value: str) -> str:
    t = (value or '').upper()
    for d in '\u2013\u2014\u2010':
        t = t.replace(d, '-')
    t = re.sub(r'\s+', '', t)
    return re.sub(r'-{2,}', '-', t)  # OCR doubles em-dashes


def _variants(n):
    """Correction candidates, cheapest first: digit-class subs (1–2),
    suffix letter-class subs, and area-prefix repairs for this font's
    systematic misreads (5→3/6/9, 8→6/9)."""
    yield n
    dpos = [i for i, c in enumerate(n) if c in TO_DIGIT]
    for i in dpos:
        yield n[:i] + TO_DIGIT[n[i]] + n[i + 1:]
    for a in range(len(dpos)):
        for b in range(a + 1, len(dpos)):
            i, j = dpos[a], dpos[b]
            yield n[:i] + TO_DIGIT[n[i]] + n[i + 1:j] + TO_DIGIT[n[j]] + n[j + 1:]
    tail = n.rsplit('-', 1)[-1]
    off = len(n) - len(tail)
    for k, c in enumerate(tail):
        if c in TO_LETTER:
            i = off + k
            yield n[:i] + TO_LETTER[c] + n[i + 1:]
    m = re.match(r'^(\d{3})-', n)
    if m:
        area = m.group(1)
        if area[1] in '369':
            yield n[0] + '5' + n[2:]
        for i, c in enumerate(area):
            if c in '69':
                yield n[:i] + '8' + n[i + 1:]


class TagRules:
    """Active legend rules; defaults = §4.2 table for the drawing's area."""

    def __init__(self, area='285'):
        self.area = area
        self.rules = [(c, re.compile(p)) for c, p in RULE_PATTERNS]

    def categorize(self, token, rot=0):
        """(category, tag) — whole-token rule match with OCR-confusion
        correction (cheapest correction wins). rot=90 marks text read from
        a vertical run: a U- tag there is a feeder load, not a board title.
        Fragments never match."""
        n = norm_tag(token)
        if not re.match(r'^[0-9A-Z-]+$', n):
            return None, n
        if '-' not in n and not re.match(r'^[LP]\d', n):
            return None, n
        if n in ('XXXX', 'YYYY') or re.fullmatch(rf'{self.area}-U-(?:XXXX|YYYY)', n):
            return 'board', n
        interface_hit = None
        best = None  # (substitutions, category, tag) — fewest corrections wins
        for v in _variants(n):
            subs = 0 if v == n else 1
            m = re.match(r'^(\d{3})-(.+)$', v)
            if not m:
                if v != n:
                    continue  # never "correct" plain words into subpanels
                rx = dict(self.rules)['subpanel']
                if rx.fullmatch(v) and len(re.findall(r'\d', v)) >= 2:
                    best = best or (subs, 'subpanel', v)
                continue
            area = m.group(1)
            if area == self.area:
                for cat, rx in self.rules:
                    if rx.fullmatch(v):
                        if cat == 'subpanel' and len(re.findall(r'\d', v)) < 2:
                            break
                        eff = cat
                        if cat == 'board-u':
                            eff = 'load' if rot == 90 else 'board'
                        if not best or subs < best[0]:
                            best = (subs, eff, v)
            elif GENERIC_TAG.fullmatch(v) or self.rules[0][1].fullmatch(v):
                interface_hit = interface_hit or v
        if best:
            return best[1], best[2]
        if interface_hit:
            return 'interface', interface_hit
        return None, n


# ── raster pipeline ─────────────────────────────────────────────────────

def _render(page, rot=0):
    zoom = DPI / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[..., :3]
    if rot == 90:
        img = np.ascontiguousarray(np.rot90(img, k=-1))
    return img, zoom


def _preprocess(tile):
    """Neutral-ink mask (drops red revision clouds) + selective glyph fill."""
    r = tile[..., 0].astype(int)
    g = tile[..., 1].astype(int)
    b = tile[..., 2].astype(int)
    gray = tile.mean(axis=2)
    neutral = (abs(r - g) < 25) & (abs(g - b) < 25) & (gray < 245)
    lbl, _ = ndimage.label(neutral)
    filled = np.zeros_like(neutral)
    for sl in ndimage.find_objects(lbl) or []:
        h = sl[0].stop - sl[0].start
        w = sl[1].stop - sl[1].start
        if h <= 120 and w <= 120:  # glyph-sized only — board outlines stay lines
            filled[sl] |= ndimage.binary_fill_holes(neutral[sl])
        else:
            filled[sl] |= neutral[sl]
    return np.where(filled, 0, 255).astype(np.uint8)


def _ocr(img, rot=0):
    """Whole-image tiled OCR -> [(text, x, y, conf)] in IMAGE pixels of the
    rotated image."""
    h, w = img.shape[:2]
    step = TILE - OVERLAP
    out = []
    for ty in range(0, h, step):
        for tx in range(0, w, step):
            tile = _preprocess(img[ty:ty + TILE, tx:tx + TILE])
            data = pytesseract.image_to_data(tile, output_type=Output.DICT, config='--psm 11')
            for i, t in enumerate(data['text']):
                t = t.strip()
                conf = float(data['conf'][i])
                if not t or conf < 20:
                    continue
                out.append((t, tx + data['left'][i], ty + data['top'][i], conf))
    return out


def _rot90_back(x, y, img_shape):
    """Map a point in the 90°-rotated image back to original image coords."""
    h, w = img_shape[:2]
    return y, h - x  # inverse of rot90(k=-1)


def extract_tags(pdf_bytes, analysis=None, area='285'):
    """-> [SldTag] per the §7 data contract (cropUrl filled when storage
    is available; evidence rendering is best-effort)."""
    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    rules = TagRules(area)
    found = {}

    for pno in range(doc.page_count):
        page = doc.load_page(pno)
        for rot in (0, 90):
            img, zoom = _render(page, rot)
            for text, x, y, conf in _ocr(img, rot):
                cat, tag = rules.categorize(text, rot=rot)
                if not cat:
                    continue
                if rot == 90:
                    ox, oy = _rot90_back(x, y, img.shape)
                else:
                    ox, oy = x, y
                key = (tag, pno + 1)
                prev = found.get(key)
                if prev and prev['_conf'] >= conf:
                    continue
                found[key] = {
                    'tag': tag, 'norm': tag, 'sheet': pno + 1,
                    'x': ox / zoom, 'y': oy / zoom,   # PDF points
                    'category': cat, 'fedFrom': '', 'bus': '', 'feeder': '',
                    'rating': '', 'description': '', 'cropUrl': '',
                    '_conf': conf,
                }

    tags = list(found.values())
    _dedupe_by_position(tags)
    _drop_fragments(tags)
    _assign_fed_from(tags)
    _attach_ratings(doc, tags, zoom=DPI / 72.0)
    _render_crops(doc, tags, analysis)
    for t in tags:
        t.pop('_conf', None)
    doc.close()
    return tags


def _dedupe_by_position(tags, tol=12.0):
    """The 0° and 90° passes can read the SAME glyph as two different tags
    (e.g. 283-PM-405A vs 285-PM-405A). Same sheet + same position (PDF
    points) = one physical tag: keep the highest-confidence read. This
    also prevents phantom 'wrong area code' drafting findings caused by
    OCR noise twins rather than real drawing errors."""
    by_pos = {}
    for t in tags:
        key = (t['sheet'], round(t['x'] / tol), round(t['y'] / tol))
        prev = by_pos.get(key)
        if prev is None or t.get('_conf', 0) > prev.get('_conf', 0):
            by_pos[key] = t
    tags[:] = list(by_pos.values())


def _drop_fragments(tags):
    """Drop tag X when a longer tag Y on the same sheet has X as a strict
    prefix — X is an OCR split fragment (spec §0 problem 3: '285-U-503',
    '285-U-503B-FEEDER' are never tags)."""
    by_sheet = {}
    for t in tags:
        by_sheet.setdefault(t['sheet'], set()).add(t['tag'])
    tags[:] = [t for t in tags
               if not any(t['tag'] != other and other.startswith(t['tag'])
                          for other in by_sheet[t['sheet']])]


def _assign_fed_from(tags):
    """fed-from board = the nearest board title ABOVE the tag on the same
    sheet (the board outline contains the feeder — spec §5.1)."""
    boards = [t for t in tags if t['category'] == 'board']
    for t in tags:
        if t['category'] not in ('motor', 'load', 'subpanel'):
            continue
        same = [b for b in boards if b['sheet'] == t['sheet'] and b['y'] < t['y']]
        if not same:
            continue
        nearest = min(same, key=lambda b: (t['y'] - b['y']) + abs(b['x'] - t['x']) * 0.3)
        t['fedFrom'] = nearest['tag']


def _attach_ratings(doc, tags, zoom):
    """Grab the nearest 'NNkW' token below/beside a load tag as its rating."""
    kw_tokens = []
    for pno in range(doc.page_count):
        page = doc.load_page(pno)
        img, _z = _render(page)
        for text, x, y, conf in _ocr(img):
            m = KW_RE.match(text.strip())
            if m:
                kw_tokens.append((pno + 1, float(m.group(1)), x / zoom, y / zoom))
    for t in tags:
        if t['category'] not in ('motor', 'load'):
            continue
        near = [k for k in kw_tokens if k[0] == t['sheet']
                and abs(k[2] - t['x']) < 60 and abs(k[3] - t['y']) < 120]
        if near:
            best = min(near, key=lambda k: abs(k[2] - t['x']) + abs(k[3] - t['y']))
            t['ratedKw'] = best[1]
            t['rating'] = f'{best[1]:g} kW'


def _render_crops(doc, tags, analysis):
    """Evidence crop per tag (best-effort; needs an analysis for storage)."""
    if analysis is None:
        return
    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage
    for idx, t in enumerate(tags):
        try:
            page = doc.load_page(t['sheet'] - 1)
            r = fitz.Rect(t['x'] - 60, t['y'] - 40, t['x'] + 200, t['y'] + 60) & page.rect
            pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), clip=r)
            path = f'electrical_comparison/evidence/{analysis.id}_{idx}.png'
            saved = default_storage.save(path, ContentFile(pix.tobytes('png')))
            t['cropUrl'] = default_storage.url(saved)
        except Exception:
            continue
