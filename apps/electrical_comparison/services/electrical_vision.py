"""
Electrical SLD image rendering/preprocessing for AI Vision.

Forked from apps.pid_checker_v2.services.vision_extractor's own render/
preprocess pipeline (_render_single_page, _downscale, _preprocess_for_
vision, _prepare_image_b64) rather than shared with it — so tuning this
for complex electrical Single Line Diagrams (higher DPI, adaptive
upscaling/contrast for low-quality scans) can never silently change
P&ID Verification V1/V2 or P&ID Checker V2's own Vision image quality.
Those three features import vision_extractor.py's own unmodified
pipeline directly and never touch this file; this file is imported ONLY
by apps.electrical_comparison.services.tag_extractor.

Everything else electrical comparison needs from Vision (retry/
fallback-model handling, provider model constants, token accounting)
is still imported directly from vision_extractor.py unchanged — only
rendering/preprocessing is forked, since that's the only part this
session's electrical-specific tuning (DPI, low-quality detection,
upscaling, stronger contrast) actually touches.
"""
import base64
import io

import fitz  # PyMuPDF
from PIL import Image, ImageEnhance, ImageFilter, ImageStat

Image.MAX_IMAGE_PIXELS = None

# Higher than vision_extractor.py's own VISION_RENDER_DPI (300) — complex
# SLDs pack dense, small-text equipment tags/symbols into a single
# sheet, so the extra render detail genuinely helps here even though it
# costs a larger image per page.
ELECTRICAL_VISION_RENDER_DPI = 400


def _render_single_page(pdf_bytes: bytes, page_index: int, dpi: int | None = None) -> Image.Image:
    """Render exactly ONE page to a high-res PIL image at
    ELECTRICAL_VISION_RENDER_DPI (or `dpi`, if given).

    Unlike rendering every page, this does NOT rasterize any page other
    than `page_index` — fitz.open() only parses the document's xref
    table; get_pixmap() is what actually does the (expensive) rendering
    work, and it's called here for a single page only. Used by callers
    that process pages independently (one Vision call per page) so N
    page-calls cost O(N) total renders instead of O(N^2).
    """
    resolved_dpi = dpi or ELECTRICAL_VISION_RENDER_DPI
    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    mat = fitz.Matrix(resolved_dpi / 72, resolved_dpi / 72)
    pix = doc[page_index].get_pixmap(matrix=mat, alpha=False)
    return Image.open(io.BytesIO(pix.tobytes('png')))


def _image_to_b64_png(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    return base64.b64encode(buf.getvalue()).decode('utf-8')


def _downscale(img: Image.Image, max_dim: int) -> Image.Image:
    w, h = img.size
    longest = max(w, h)
    if longest <= max_dim:
        return img
    scale = max_dim / longest
    return img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)


# ─── Preprocessing for scanned / low-quality electrical SLDs ──────────
# Applied to every electrical drawing page right before it reaches
# Vision. ELECTRICAL_VISION_RENDER_DPI (above) already covers the
# "render at high DPI" requirement; this adds adaptive enhancement on
# top for pages that still come out blurry/low-detail (e.g. a scanned
# SLD rather than a clean digital-PDF render).
ELECTRICAL_VISION_PREPROCESS_ENABLED = True
ELECTRICAL_VISION_PREPROCESS_CONTRAST_FACTOR = 2.0
# Stronger contrast pass used instead of the normal factor above when
# _is_low_quality_image() flags a page as blurry/low-detail.
ELECTRICAL_VISION_PREPROCESS_LOW_QUALITY_CONTRAST_FACTOR = 3.0
# A page narrower/shorter than this on either side is upscaled (LANCZOS)
# up to ELECTRICAL_VISION_UPSCALE_MIN_DIMENSION_PX on its longest side
# before any filter runs — a genuinely small source image (e.g. a
# low-res embedded scan) gives every filter below more real pixels to
# work with, rather than each filter sharpening/enhancing an undersized
# image that then still looks small.
ELECTRICAL_VISION_LOW_RES_THRESHOLD_PX = 1500
ELECTRICAL_VISION_UPSCALE_MIN_DIMENSION_PX = 2000
# _is_low_quality_image's edge-map standard-deviation cutoff — real
# line-drawing/text content produces many scattered edges (high
# variance) under PIL's FIND_EDGES filter; a blurry/washed-out/blank
# scan produces almost none (low variance, most pixels look alike).
# Starting value, not yet tuned against a large real-page sample —
# adjust if real electrical SLD pages get mis-flagged either direction
# once this runs against production scans.
ELECTRICAL_VISION_LOW_QUALITY_EDGE_STDDEV_THRESHOLD = 15.0


def _is_low_quality_image(img: Image.Image) -> bool:
    """Cheap, Vision-free heuristic: True if `img` looks like a blurry/
    low-detail scan rather than a crisp digital-PDF render or a clear
    scan. Downsamples first for speed (this is a pre-check, not the
    actual image sent to Vision), runs PIL's own FIND_EDGES filter, and
    checks how much variance the resulting edge map has — real content
    (text, equipment symbols, wiring) produces lots of scattered edges;
    a flat/blurry/washed-out page produces almost none, since there's
    nothing sharp enough for the edge filter to catch."""
    sample = img.convert('L') if img.mode != 'L' else img
    if max(sample.size) > 800:
        scale = 800 / max(sample.size)
        sample = sample.resize((
            max(1, int(sample.width * scale)),
            max(1, int(sample.height * scale)),
        ))
    edges = sample.filter(ImageFilter.FIND_EDGES)
    stat = ImageStat.Stat(edges)
    edge_stddev = stat.stddev[0] if stat.stddev else 0.0
    return edge_stddev < ELECTRICAL_VISION_LOW_QUALITY_EDGE_STDDEV_THRESHOLD


def _preprocess_for_vision(img: Image.Image) -> Image.Image:
    """Grayscale + (upscale if small) + denoise + contrast boost + sharpen
    — improves legibility of scanned/low-quality electrical SLD pages
    before they reach Vision. A no-op when
    ELECTRICAL_VISION_PREPROCESS_ENABLED is False, so it can be turned
    off without a code change if it ever hurts an already-clean digital
    PDF more than it helps a scanned one.

    A page detected as low quality gets a stronger pass than a normal
    page would: upscaling (if it's also small — see
    ELECTRICAL_VISION_LOW_RES_THRESHOLD_PX), a higher contrast factor,
    SHARPEN applied twice, and an additional EDGE_ENHANCE pass — see
    _is_low_quality_image()'s own docstring for how that's detected.
    """
    if not ELECTRICAL_VISION_PREPROCESS_ENABLED:
        return img

    out = img.convert('L')                                  # grayscale first

    # Upscale a small source image BEFORE any filter runs, so denoise/
    # contrast/sharpen all have real pixels to work with rather than
    # stretching an already-filtered image afterward.
    if out.width < ELECTRICAL_VISION_LOW_RES_THRESHOLD_PX or out.height < ELECTRICAL_VISION_LOW_RES_THRESHOLD_PX:
        longest = max(out.size)
        if longest < ELECTRICAL_VISION_UPSCALE_MIN_DIMENSION_PX:
            scale = ELECTRICAL_VISION_UPSCALE_MIN_DIMENSION_PX / longest
            out = out.resize(
                (max(1, int(out.width * scale)), max(1, int(out.height * scale))),
                Image.LANCZOS,
            )

    out = out.filter(ImageFilter.MedianFilter(size=3))       # remove scan noise/speckle

    if _is_low_quality_image(out):
        out = ImageEnhance.Contrast(out).enhance(ELECTRICAL_VISION_PREPROCESS_LOW_QUALITY_CONTRAST_FACTOR)
        out = out.filter(ImageFilter.SHARPEN)
        out = out.filter(ImageFilter.SHARPEN)
        out = out.filter(ImageFilter.EDGE_ENHANCE)
    else:
        out = ImageEnhance.Contrast(out).enhance(ELECTRICAL_VISION_PREPROCESS_CONTRAST_FACTOR)
        out = out.filter(ImageFilter.SHARPEN)                # crisp up faint/blurry text

    return out


def _prepare_image_b64(img: Image.Image, max_dim: int) -> str:
    """Downscale → preprocess → base64-PNG-encode: the standard prep
    every electrical SLD page goes through before a Vision call."""
    return _image_to_b64_png(_preprocess_for_vision(_downscale(img, max_dim)))
