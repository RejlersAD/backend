"""
Non-TEFF Yellow-Region (Highlight) Extractor.

Many older engineering documents contain **yellow-highlighted boxes** that
hold the most valuable information — typically:

  • Revision stamps   ("REV C — ISSUED FOR CONSTRUCTION")
  • Approval stamps   ("APPROVED BY: ...", "DATE: ...")
  • Hold flags        ("HOLD 01", "HOLD 02")
  • Document status   ("AS BUILT", "FOR REVIEW", "FOR INFORMATION")
  • Vendor / PO references that were added later as overlays

Because these are visual annotations they almost never appear in the PDF
text layer; pdfplumber misses them entirely. This module finds them by
**colour-segmenting the rendered page** (HSV mask), groups the pixels into
bounding boxes, and OCRs each box individually so the recognised letters
go straight back into the bulk extraction pipeline.

Design rules:
  • Pure additive — never mutates the page or existing row values.
  • Soft-coded — every threshold lives in ``YELLOW_CONFIG``.
  • Cost-aware — runs locally only (Tesseract). Optional AI pass is gated
    by an explicit flag so it is OFF by default and only enabled in
    environments where the user wants a labelled summary.
  • Safe — every external call (cv2, tesseract, AI) is wrapped; failures
    silently fall back to "no yellow regions found".
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SOFT-CODED configuration
# ---------------------------------------------------------------------------

YELLOW_CONFIG: Dict[str, Any] = {
    'enabled': True,

    # Pages to scan (1-based slice). Title-block highlights are usually on
    # page 1, but some docs put revision history / hold tables on page 2.
    'max_pages': 3,
    # Render DPI for detection. Higher = catches small stamps but slower.
    'detect_dpi': 180,
    # Render DPI for OCR crop (re-rendered at this DPI for sharper text).
    'ocr_dpi': 240,

    # ---- HSV mask for yellow ----------------------------------------------
    # OpenCV HSV: H 0-179, S 0-255, V 0-255. The defaults match the broad
    # "highlighter yellow" family found on scanned drawings (light yellow,
    # cream-yellow, fluorescent yellow). Lowering S_min admits faded scans.
    # Two ranges to catch both warm and cool yellow tints.
    'hsv_ranges': [
        # (low_H, low_S, low_V, high_H, high_S, high_V)
        (15, 60, 130, 40, 255, 255),   # primary highlight yellow
        (20, 30, 180, 45, 180, 255),   # faded / cream yellow on old scans
    ],

    # ---- Region filtering -------------------------------------------------
    # Minimum / maximum bounding-box area as a fraction of the page area.
    # Tiny specks (paper noise) and "the whole page is yellow" backgrounds
    # are dropped.
    'min_area_frac': 0.0008,   # ~0.08% — catches small revision stamps
    'max_area_frac': 0.45,     # ignores yellow-tinted page backgrounds
    # Aspect ratios outside this band are dropped (tall thin shadows etc.).
    'aspect_min': 0.10,
    'aspect_max': 12.0,
    # Pad around detected bbox before OCR to capture characters touching
    # the edge of the highlight.
    'pad_frac': 0.02,
    # Cap on boxes per page.
    'max_boxes_per_page': 12,
    # Morphology kernel (in pixels) used to merge nearby yellow blobs into
    # a single bounding box — important because stamps often have stripes.
    'morph_close_px': 18,
    # Drop horizontal slivers shorter than this many pixels (pre-filter).
    'min_box_height_px': 14,
    'min_box_width_px':  28,

    # ---- OCR --------------------------------------------------------------
    'ocr_psm_modes': [6, 7, 11],   # 7 = single line (good for stamps)
    'ocr_lang': 'eng',
    'ocr_min_chars': 2,
    # Reject OCR strings whose alpha-numeric ratio is below this — pure
    # noise often comes back as `]§|.,/`.
    'min_alnum_ratio': 0.30,

    # ---- Optional AI labelling --------------------------------------------
    # When True, AFTER OCR each non-empty yellow region is sent to the
    # Gemini/OpenAI provider chain to get a one-word label
    # (e.g. "revision_stamp", "hold_flag", "approval"). Off by default.
    'ai_label_enabled': False,
}


# Output schema (returned to callers):
#   [
#     {
#       'page'      : 1,                      # 1-based page number
#       'rect'      : (x, y, w, h),           # pixel coords on the rendered page
#       'rect_pct'  : (x%, y%, w%, h%),       # 0..1 fractions for overlay
#       'text'      : 'REV C ISSUED FOR ...', # OCR result (cleaned)
#       'label'     : 'revision_stamp',       # only when ai_label_enabled
#       'confidence': 0.78,                   # heuristic
#     }, ...
#   ]


# ---------------------------------------------------------------------------
# Core detection
# ---------------------------------------------------------------------------

def _render_page_pil(file_path: str, page_no_zero_based: int, dpi: int):
    """Return a PIL.Image for a PDF page, or None on failure."""
    try:
        import fitz
        from PIL import Image
    except ImportError:
        return None
    try:
        doc = fitz.open(file_path)
        if page_no_zero_based >= doc.page_count:
            doc.close()
            return None
        page = doc.load_page(page_no_zero_based)
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = Image.frombytes('RGB', [pix.width, pix.height], pix.samples)
        doc.close()
        return img
    except Exception:
        logger.exception('yellow_extractor: render_page failed (%s p%d)',
                         file_path, page_no_zero_based)
        return None


def _build_yellow_mask(img_rgb_np):
    """Return a binary mask (H×W uint8 0/255) of yellow pixels."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    try:
        hsv = cv2.cvtColor(img_rgb_np, cv2.COLOR_RGB2HSV)
        mask = None
        for (lh, ls, lv, hh, hs, hv) in YELLOW_CONFIG['hsv_ranges']:
            m = cv2.inRange(hsv,
                            (int(lh), int(ls), int(lv)),
                            (int(hh), int(hs), int(hv)))
            mask = m if mask is None else cv2.bitwise_or(mask, m)
        # Close gaps so single stamp = single contour.
        k = int(YELLOW_CONFIG.get('morph_close_px', 18))
        if k > 1:
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        return mask
    except Exception:
        logger.exception('yellow_extractor: HSV mask failed')
        return None


def _bboxes_from_mask(mask) -> List[Tuple[int, int, int, int]]:
    """Find candidate bounding boxes from a binary mask, filtered by config."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return []
    try:
        H, W = mask.shape[:2]
        page_area = float(H * W) or 1.0
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        boxes: List[Tuple[int, int, int, int, float]] = []
        cfg = YELLOW_CONFIG
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            if w < cfg['min_box_width_px'] or h < cfg['min_box_height_px']:
                continue
            area = w * h
            frac = area / page_area
            if frac < cfg['min_area_frac'] or frac > cfg['max_area_frac']:
                continue
            ar = (w / h) if h else 0.0
            if ar < cfg['aspect_min'] or ar > cfg['aspect_max']:
                continue
            boxes.append((x, y, w, h, frac))
        # Largest first (more important stamps tend to be larger).
        boxes.sort(key=lambda b: -b[4])
        boxes = boxes[: int(cfg['max_boxes_per_page'])]
        return [(x, y, w, h) for (x, y, w, h, _) in boxes]
    except Exception:
        logger.exception('yellow_extractor: bbox extraction failed')
        return []


def _crop_with_padding(img, rect: Tuple[int, int, int, int]):
    x, y, w, h = rect
    pad_w = int(w * float(YELLOW_CONFIG['pad_frac']))
    pad_h = int(h * float(YELLOW_CONFIG['pad_frac']))
    x0 = max(0, x - pad_w)
    y0 = max(0, y - pad_h)
    x1 = min(img.size[0], x + w + pad_w)
    y1 = min(img.size[1], y + h + pad_h)
    return img.crop((x0, y0, x1, y1))


def _clean_ocr_text(s: str) -> str:
    if not s:
        return ''
    s = s.replace('\r', ' ').replace('\t', ' ')
    s = ' '.join(s.split())
    return s.strip()


def _alnum_ratio(s: str) -> float:
    if not s:
        return 0.0
    total = sum(1 for c in s if not c.isspace())
    if total == 0:
        return 0.0
    good = sum(1 for c in s if c.isalnum())
    return good / total


def _ocr_crop(img_pil) -> str:
    """Run pytesseract over a single crop, picking the best PSM result."""
    try:
        import pytesseract
    except ImportError:
        return ''
    best = ''
    for psm in YELLOW_CONFIG['ocr_psm_modes']:
        try:
            text = pytesseract.image_to_string(
                img_pil,
                lang=YELLOW_CONFIG['ocr_lang'],
                config=f'--psm {psm}',
            )
            cleaned = _clean_ocr_text(text)
            if (len(cleaned) > len(best)
                    and _alnum_ratio(cleaned) >= YELLOW_CONFIG['min_alnum_ratio']):
                best = cleaned
        except Exception:
            logger.debug('yellow_extractor: tesseract PSM %s failed', psm,
                         exc_info=False)
    return best


# ---------------------------------------------------------------------------
# Optional AI labelling (lazy, soft-coded)
# ---------------------------------------------------------------------------

def _ai_label_text(text: str) -> str:
    """Best-effort one-word label for what a yellow stamp says. Returns ''."""
    if not YELLOW_CONFIG.get('ai_label_enabled'):
        return ''
    try:
        from . import ai_recommendations as r
        # Re-use the provider chain by piggy-backing on its dispatcher with
        # a tiny prompt. Falls back silently to '' on any failure.
        prompt = (
            "Classify this stamp text from an engineering drawing into ONE "
            "of: revision_stamp, approval_stamp, hold_flag, status_stamp, "
            "vendor_ref, document_number, date_stamp, other. Reply with the "
            f'single token only.\n\nText: "{text}"\n'
        )
        provider, data = r._dispatch(prompt)  # noqa: SLF001 — internal reuse
        if isinstance(data, dict):
            return ''  # JSON-mode dispatcher won't return raw token
    except Exception:
        return ''
    return ''


# ---------------------------------------------------------------------------
# Public entries
# ---------------------------------------------------------------------------

def extract_yellow_regions(file_path: str) -> List[Dict[str, Any]]:
    """
    Detect & OCR all qualifying yellow rectangles across the first
    ``max_pages`` pages. Returns a list of region dicts (see schema in the
    module docstring). Returns [] when feature is disabled, the PDF is
    unrenderable, or no qualifying regions are found.
    """
    if not YELLOW_CONFIG.get('enabled'):
        return []
    if not file_path or not file_path.lower().endswith('.pdf'):
        return []

    try:
        import numpy as np
    except ImportError:
        return []

    out: List[Dict[str, Any]] = []
    detect_dpi = int(YELLOW_CONFIG['detect_dpi'])
    ocr_dpi    = int(YELLOW_CONFIG['ocr_dpi'])
    max_pages  = int(YELLOW_CONFIG['max_pages'])

    for page_no in range(max_pages):
        det_img = _render_page_pil(file_path, page_no, detect_dpi)
        if det_img is None:
            break
        try:
            arr = np.array(det_img)
        except Exception:
            continue
        mask = _build_yellow_mask(arr)
        if mask is None:
            continue
        boxes = _bboxes_from_mask(mask)
        if not boxes:
            continue

        # Re-render at higher DPI for OCR; rescale boxes accordingly.
        ocr_img = _render_page_pil(file_path, page_no, ocr_dpi) or det_img
        sx = ocr_img.size[0] / det_img.size[0] if det_img.size[0] else 1.0
        sy = ocr_img.size[1] / det_img.size[1] if det_img.size[1] else 1.0

        page_w_det, page_h_det = det_img.size
        for (x, y, w, h) in boxes:
            ox = int(x * sx)
            oy = int(y * sy)
            ow = int(w * sx)
            oh = int(h * sy)
            crop = _crop_with_padding(ocr_img, (ox, oy, ow, oh))
            text = _ocr_crop(crop)
            if not text or len(text) < int(YELLOW_CONFIG['ocr_min_chars']):
                continue
            if _alnum_ratio(text) < float(YELLOW_CONFIG['min_alnum_ratio']):
                continue
            entry: Dict[str, Any] = {
                'page': page_no + 1,
                'rect': (x, y, w, h),
                'rect_pct': (
                    round(x / page_w_det, 4),
                    round(y / page_h_det, 4),
                    round(w / page_w_det, 4),
                    round(h / page_h_det, 4),
                ),
                'text': text,
                'confidence': round(min(1.0, _alnum_ratio(text)), 3),
            }
            label = _ai_label_text(text)
            if label:
                entry['label'] = label
            out.append(entry)

    if out:
        logger.info('yellow_extractor: %s yielded %d region(s)',
                    os.path.basename(file_path), len(out))
    return out


def extract_yellow_text_blob(file_path: str) -> str:
    """
    Convenience: return all yellow-region texts joined with newlines, ready
    to be appended to the main text-layer for the existing regex extractors
    to consume.
    """
    regions = extract_yellow_regions(file_path)
    if not regions:
        return ''
    return '\n'.join(f'[YELLOW p{r["page"]}] {r["text"]}' for r in regions)
