"""Line-tag extraction service for P&ID Checker V2.

Strategy (soft-coded, cascading):
    1. Fast path: read embedded text via pdfplumber + PyMuPDF.
    2. Heavy path: if fast path yields fewer than MIN_HITS_FOR_SKIP tags,
       also run tiled multi-rotation Tesseract OCR (typical for CAD-exported
       P&IDs where all canvas text is vector-outlined).

Everything is soft-coded via module-level constants at the top of this
file — pattern, service whitelist, DPI, tile size, overlap, PSMs.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, asdict
from typing import Iterable

import fitz  # PyMuPDF
import pdfplumber
import pytesseract
from PIL import Image, ImageOps, ImageFilter

Image.MAX_IMAGE_PIXELS = None  # allow very large P&ID renders

# ─── Soft-coded configuration ─────────────────────────────────────────
LINE_TAG_PATTERN = re.compile(
    r'(\d{1,2}(?:[-/]\d{1,2}(?:/\d)?)?)\s*["\u201d\']?\s*[-\u2013]\s*'
    r'([A-Z]{2,4})\s*[-\u2013]\s*'
    r'([A-Z0-9]{3,6})\s*[-\u2013]\s*'
    r'(\d{3,5})'
)

KNOWN_SERVICES = frozenset({
    'FL', 'SG', 'CD', 'PL', 'FG', 'OW', 'PW', 'IA', 'PA', 'NG', 'HC',
    'AC', 'BD', 'CW', 'DR', 'FW', 'GO', 'HW', 'IG', 'LO', 'NA', 'OG',
    'RW', 'SW', 'TG', 'VT', 'WW', 'CS', 'HP', 'LP', 'MP', 'ST', 'CO',
    'FO', 'SO', 'SA', 'CA', 'GS', 'CH', 'AB', 'AI', 'FF',
})

SPEC_SANITY_PATTERN = re.compile(r'^[A-Z]{1,3}\d[A-Z0-9]{0,3}$')

# Service → human-readable group (soft-coded so UI can group)
SERVICE_GROUPS = {
    'FL': 'Flare',        'SG': 'Sour Gas',   'FG': 'Fuel Gas',
    'PL': 'Pipeline',     'CD': 'Closed Drain', 'OW': 'Oily Water',
    'PW': 'Produced Water', 'IA': 'Instrument Air', 'PA': 'Plant Air',
    'NG': 'Natural Gas',  'HC': 'Hydrocarbon',
}

# OCR fast-path threshold: if embedded text yields ≥ this many tags,
# we skip OCR (already got a real line-list-style PDF).
MIN_HITS_FOR_SKIP_OCR = 20

# OCR tuning — bumped for dense/rotated P&ID tag recall.
OCR_RENDER_DPI = 300              # was 250 — small drain-line labels need more px
OCR_TILE_SIZE = 2400
OCR_TILE_OVERLAP = 900            # was 400 — long tags like 20"-PL-DC3N-8106 straddle borders
OCR_PSMS = (6, 11, 12)            # added PSM 12 (sparse text with OSD) for rotated tags
OCR_ROTATIONS = (0, 90, 180, 270)

# Image preprocessing before OCR — soft-coded on/off.
OCR_PREPROCESS = True
OCR_BINARISATION_THRESHOLD = 180  # 0-255; higher = keep more ink


# ─── Data classes ─────────────────────────────────────────────────────
@dataclass(frozen=True)
class LineTag:
    tag: str
    size: str
    service: str
    spec: str
    serial: str
    service_group: str

    def to_dict(self) -> dict:
        return asdict(self)


# ─── Core helpers ─────────────────────────────────────────────────────
def _mk_tag(size: str, service: str, spec: str, serial: str) -> LineTag:
    return LineTag(
        tag=f'{size}"-{service}-{spec}-{serial}',
        size=size,
        service=service,
        spec=spec,
        serial=serial,
        service_group=SERVICE_GROUPS.get(service, service),
    )


def _scan_text_blob(blob: str, found: dict[str, LineTag]) -> None:
    for m in LINE_TAG_PATTERN.finditer(blob):
        size, service, spec, serial = m.groups()
        if service not in KNOWN_SERVICES:
            continue
        if not SPEC_SANITY_PATTERN.match(spec):
            continue
        tag = _mk_tag(size, service, spec, serial)
        found.setdefault(tag.tag, tag)


# ─── Fast path: embedded text ─────────────────────────────────────────
def _extract_from_embedded_text(pdf_bytes: bytes, found: dict[str, LineTag]) -> None:
    # pdfplumber
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                _scan_text_blob(page.extract_text() or '', found)
                words = page.extract_words(use_text_flow=True) or []
                _scan_text_blob(' '.join(w['text'] for w in words), found)
    except Exception:
        pass
    # PyMuPDF
    try:
        doc = fitz.open(stream=pdf_bytes, filetype='pdf')
        for page in doc:
            _scan_text_blob(page.get_text('text') or '', found)
            _scan_text_blob(' '.join(w[4] for w in page.get_text('words')), found)
    except Exception:
        pass


# ─── Heavy path: tiled multi-rotation OCR ─────────────────────────────
def _tiles(img: Image.Image, size: int, overlap: int) -> Iterable[Image.Image]:
    W, H = img.size
    step = size - overlap
    for y in range(0, H, step):
        for x in range(0, W, step):
            yield img.crop((x, y, min(x + size, W), min(y + size, H)))


def _preprocess_for_ocr(img: Image.Image) -> Image.Image:
    """Grayscale + threshold binarisation — greatly improves recall on
    P&IDs where drawing linework confuses Tesseract."""
    if not OCR_PREPROCESS:
        return img
    gray = img.convert('L')
    # Slight sharpening helps thin CAD strokes.
    gray = gray.filter(ImageFilter.SHARPEN)
    # Binarise so drawing lines become pure black-on-white.
    return gray.point(lambda p: 255 if p > OCR_BINARISATION_THRESHOLD else 0, mode='1')


def _extract_via_ocr(pdf_bytes: bytes, found: dict[str, LineTag]) -> None:
    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    mat = fitz.Matrix(OCR_RENDER_DPI / 72, OCR_RENDER_DPI / 72)
    for page in doc:
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = Image.open(io.BytesIO(pix.tobytes('png')))
        img = _preprocess_for_ocr(img)
        for tile in _tiles(img, OCR_TILE_SIZE, OCR_TILE_OVERLAP):
            for psm in OCR_PSMS:
                for rot in OCR_ROTATIONS:
                    im = tile.rotate(rot, expand=True) if rot else tile
                    try:
                        text = pytesseract.image_to_string(
                            im, config=f'--psm {psm} --oem 3'
                        )
                    except Exception:
                        continue
                    _scan_text_blob(text, found)


# ─── Public API ───────────────────────────────────────────────────────
def extract_line_tags(pdf_bytes: bytes, *, force_ocr: bool = False) -> list[LineTag]:
    """Extract composite pipeline line tags from a P&ID / line-list PDF.

    Runs embedded-text extraction first (fast, deterministic). If that
    yields fewer than MIN_HITS_FOR_SKIP_OCR tags, also runs tiled
    multi-rotation OCR.
    """
    found: dict[str, LineTag] = {}
    _extract_from_embedded_text(pdf_bytes, found)
    embedded_hits = len(found)
    if force_ocr or embedded_hits < MIN_HITS_FOR_SKIP_OCR:
        _extract_via_ocr(pdf_bytes, found)

    def sort_key(t: LineTag):
        try:
            return (t.service, int(t.serial), t.size)
        except ValueError:
            return (t.service, 0, t.size)

    return sorted(found.values(), key=sort_key)


def summarize(tags: list[LineTag]) -> dict:
    """Group tags by service for UI convenience."""
    groups: dict[str, list[dict]] = {}
    for t in tags:
        groups.setdefault(t.service_group, []).append(t.to_dict())
    return {
        'total': len(tags),
        'by_group': groups,
        'services': sorted({t.service for t in tags}),
    }
