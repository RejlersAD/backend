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
upscaling, stronger contrast, region-based rendering) actually touches.
"""
import base64
import io
import logging
import math

import pymupdf  # PyMuPDF
from PIL import Image, ImageEnhance, ImageFilter, ImageStat
logger = logging.getLogger(__name__)


Image.MAX_IMAGE_PIXELS = None

# Higher than vision_extractor.py's own VISION_RENDER_DPI (300) — complex
# SLDs pack dense, small-text equipment tags/symbols into a single
# sheet, so the extra render detail genuinely helps here even though it
# costs a larger image per page.
# BUG FIX: was raised to 600 to fix illegible text, which in turn broke
# large-format sheets (A0/A1) with PyMuPDF's own "Overly large image"
# raster-size ceiling — a 33"x47" sheet at 600 DPI is already a ~560
# megapixel pixmap, well past what get_pixmap() will rasterize. Region-
# based rendering (_render_electrical_regions below, same technique
# apps.pid_verification.services.piping_valve_mto_extractor._render_
# native_regions already uses for this exact problem) is now the real
# extraction path, rendering at ELECTRICAL_VISION_DETAIL_DPI per region
# instead of the whole page at once — this constant now only matters as
# a safe whole-page-render fallback DPI (_render_single_page is no
# longer called by the live Vision flow, but is kept working and at a
# size that won't hit the same ceiling if anything calls it directly
# again).
ELECTRICAL_VISION_RENDER_DPI = 240

# ─── Region-based rendering (avoids whole-page "Overly large image") ──
# Same three constants, same role, as piping_valve_mto_extractor.py's
# own VISION_DETAIL_DPI/VISION_REGION_EDGE_PX/VISION_REGION_OVERLAP —
# kept as this module's own copy (not imported from there) for the same
# "electrical tuning must never affect another module" reasoning this
# whole file was forked for in the first place.
ELECTRICAL_VISION_DETAIL_DPI = 240
ELECTRICAL_VISION_REGION_EDGE_PX = 1568
ELECTRICAL_VISION_REGION_OVERLAP = 0.08
# BUG FIX: the adaptive grid above had no upper bound — a large-format
# sheet (A0/A1) could produce 48+ regions for ONE page, and since each
# region is one Vision API call, a multi-page document could need 100+
# sequential calls, well past what fits in the Celery task's 20-minute
# hard limit (confirmed: "0 pages done after 6 minutes" on a 2-page A0
# document was this, not a hang). Caps the grid to at most this many
# regions per page, scaling columns/rows down proportionally (not just
# truncating) so the whole page is still covered, just at a coarser
# grid — see _render_electrical_regions for the actual scale-down.
ELECTRICAL_VISION_MAX_REGIONS_PER_PAGE = 16


def _render_single_page(pdf_bytes: bytes, page_index: int, dpi: int | None = None) -> Image.Image:
    """Render exactly ONE page to a high-res PIL image at
    ELECTRICAL_VISION_RENDER_DPI (or `dpi`, if given).

    Unlike rendering every page, this does NOT rasterize any page other
    than `page_index` — pymupdf.open() only parses the document's xref
    table; get_pixmap() is what actually does the (expensive) rendering
    work, and it's called here for a single page only. Used by callers
    that process pages independently (one Vision call per page) so N
    page-calls cost O(N) total renders instead of O(N^2).
    """
    resolved_dpi = dpi or ELECTRICAL_VISION_RENDER_DPI
    doc = pymupdf.open(stream=pdf_bytes, filetype='pdf')
    mat = pymupdf.Matrix(resolved_dpi / 72, resolved_dpi / 72)
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
# Was 2.0 — raised to 2.5 (Claude reporting illegible text on real
# pages, not just ones the _is_low_quality_image() heuristic happens
# to flag).
ELECTRICAL_VISION_PREPROCESS_CONTRAST_FACTOR = 2.5
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
        # Was a single SHARPEN pass only — the illegible-text complaint
        # applies to normal (not just heuristic-flagged-low-quality)
        # pages too, so this path now gets the same double-SHARPEN +
        # EDGE_ENHANCE treatment, just at the lower (2.5, not 3.0)
        # contrast factor.
        out = ImageEnhance.Contrast(out).enhance(ELECTRICAL_VISION_PREPROCESS_CONTRAST_FACTOR)
        out = out.filter(ImageFilter.SHARPEN)
        out = out.filter(ImageFilter.SHARPEN)
        out = out.filter(ImageFilter.EDGE_ENHANCE)

    return out


def _prepare_image_b64(img: Image.Image, max_dim: int) -> str:
    """Downscale → preprocess → base64-PNG-encode: the standard prep
    every electrical SLD page goes through before a Vision call."""
    return _image_to_b64_png(_preprocess_for_vision(_downscale(img, max_dim)))


def _render_electrical_regions(pdf_bytes: bytes, page_index: int) -> list[tuple[str, dict]]:
    """Clip ONE page of the original PDF into a grid of overlapping,
    native-resolution regions — same technique apps.pid_verification.
    services.piping_valve_mto_extractor._render_native_regions() uses,
    adapted to this module's own constants/preprocessing.

    Each region is sized to fit within ELECTRICAL_VISION_REGION_EDGE_PX
    on its longest side regardless of the page's own physical size — a
    bigger sheet (A0/A1) gets MORE, smaller regions; a normal-size sheet
    may need only one. This is what avoids rendering an entire large
    sheet as one giant pixmap (the "Overly large image" failure a
    single whole-page render at a high DPI can hit directly on a big
    engineering drawing) — each region is clipped and rasterized
    straight from the PDF's own vector data (pymupdf's `clip` param), never
    cropped from an already-rendered bitmap, so no detail is lost
    compared to a whole-page render at the same DPI.

    Regions overlap their grid cell by ELECTRICAL_VISION_REGION_OVERLAP
    (8%) so a tag sitting on a region boundary still appears whole in
    at least one region instead of being cut in half in both neighbours.
    Each region is run through THIS module's own _preprocess_for_vision
    (contrast/sharpen tuned for electrical SLDs) before being PNG-
    encoded (via _image_to_b64_png — must stay PNG, since the caller
    declares every image's media_type as 'image/png'; a JPEG body sent
    under that declaration is rejected by both Claude and OpenAI as a
    format mismatch).

    The grid is capped at ELECTRICAL_VISION_MAX_REGIONS_PER_PAGE —
    columns/rows are scaled down PROPORTIONALLY (never just truncated)
    when the raw grid would exceed it, so a huge sheet still gets full
    coverage at a coarser grid instead of silently losing whatever
    regions got cut off.

    Returns a list of (region_b64, region_info) tuples, where
    region_info = {'page': page_index, 'box': (x0, y0, x1, y1), 'dpi': float}
    — box is in PDF point coordinates, for logging/debugging only (unlike
    Valve MTO's own region use, nothing here does geometric row-matching
    against region bounds: electrical tags are plain text, so simple
    text-based dedup across regions — already done by every caller's own
    page-level `seen_tags` set — is enough, no bbox tracking needed)."""
    doc = pymupdf.open(stream=pdf_bytes, filetype='pdf')
    try:
        page = doc[page_index]
        width, height = page.rect.width, page.rect.height
        columns = max(1, math.ceil(width * ELECTRICAL_VISION_DETAIL_DPI / 72 / ELECTRICAL_VISION_REGION_EDGE_PX))
        rows = max(1, math.ceil(height * ELECTRICAL_VISION_DETAIL_DPI / 72 / ELECTRICAL_VISION_REGION_EDGE_PX))
        # Scale the whole grid down proportionally (never just truncate
        # rows/columns) when it exceeds the per-page cap, so a huge
        # sheet still gets full-page coverage at a coarser grid instead
        # of silently losing whatever regions got cut off.
        total = columns * rows
        if total > ELECTRICAL_VISION_MAX_REGIONS_PER_PAGE:
            scale = (ELECTRICAL_VISION_MAX_REGIONS_PER_PAGE / total) ** 0.5
            columns = max(1, round(columns * scale))
            rows = max(1, round(rows * scale))
        logger.info(
            '[ElecCompare] Page %d: %d regions (%d cols x %d rows)',
            page_index + 1, columns * rows, columns, rows,
        )
        regions: list[tuple[str, dict]] = []
        for r in range(rows):
            for c in range(columns):
                core = pymupdf.Rect(c * width / columns, r * height / rows,
                                 (c + 1) * width / columns, (r + 1) * height / rows)
                margin_x = width / columns * ELECTRICAL_VISION_REGION_OVERLAP
                margin_y = height / rows * ELECTRICAL_VISION_REGION_OVERLAP
                clip = pymupdf.Rect(max(0, core.x0 - margin_x), max(0, core.y0 - margin_y),
                                 min(width, core.x1 + margin_x), min(height, core.y1 + margin_y))
                # Raster bounds round outwards at both edges, same as
                # piping_valve_mto_extractor's own region renderer.
                zoom = min(ELECTRICAL_VISION_DETAIL_DPI / 72,
                           (ELECTRICAL_VISION_REGION_EDGE_PX - 2) / max(clip.width, clip.height))
                pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)
                image = Image.open(io.BytesIO(pix.tobytes('png')))
                processed = _preprocess_for_vision(image)
                regions.append((
                    _image_to_b64_png(processed),
                    {'page': page_index, 'box': tuple(clip), 'dpi': round(zoom * 72, 1)},
                ))
        return regions
    finally:
        doc.close()
