"""
PFD Drawing Extraction
======================
Extracts structured engineering data from a single PFD drawing (PDF page).
Returns a dict consumed by the rule engine.

Extracted fields:
  equipment_tags   — list of strings matching V/E/P/K/T/R/C/F-NNN patterns
  stream_numbers   — list of int (stream identifiers like 1, 2, 101 …)
  title_block      — dict: drawing_number, revision, project_name (may be empty string)
  relief_devices   — list of strings  (PSV/PRV/SRV/BDV/TSV tags)
  control_valves   — list of strings  (FCV/PCV/HCV/XCV tags)
  utility_headers  — list of strings  (CW/IA/N2/LP/HP/MW labels)
  holds            — list of strings  (HOLD-XXX markers)
  notes            — list of strings  (NOTE 1 / NOTE 2 …)
  vessels_hx       — list of strings  (E-NNN / V-NNN for SFT-001 check)
  raw_text         — full page text
"""
import logging
import re
from typing import Any, Dict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------
_RE_EQUIP_TAG    = re.compile(r'\b([VEPKTRFC]-\d{3,4}[A-Z]?)\b')
_RE_STREAM_NUM   = re.compile(r'\b(\d{1,4})\b')
_RE_STREAM_LABEL = re.compile(r'(?:STREAM\s*#?\s*(\d+)|^(\d{1,4})$)', re.MULTILINE)
_RE_DWG_NUMBER   = re.compile(
    r'(?:DWG\.?\s*(?:NO\.?|NUMBER|#)\s*:?\s*([A-Z0-9\-\/]+))',
    re.IGNORECASE,
)
_RE_REVISION     = re.compile(r'\b(?:REV\.?\s*|REVISION\s*)([A-Z0-9]+)\b', re.IGNORECASE)
_RE_RELIEF       = re.compile(r'\b((?:PSV|PRV|SRV|BDV|TSV)-\d{3,4}[A-Z]?)\b')
_RE_CTRL_VALVE   = re.compile(r'\b((?:FCV|PCV|HCV|LCV|TCV|PV|XCV)-\d{3,4}[A-Z]?)\b')
_RE_UTILITY      = re.compile(r'\b(CW|IA|N2|LP\s*STEAM|HP\s*STEAM|LP|HP|MW|BFW|COND)\b')
_RE_HOLD         = re.compile(r'\b(HOLD[-\s]?\w+)\b', re.IGNORECASE)
_RE_NOTE         = re.compile(r'\b(NOTE\s+\d+)\b', re.IGNORECASE)
_RE_VESSEL_HX    = re.compile(r'\b([VE]-\d{3,4}[A-Z]?)\b')


def extract_drawing(file_path: str, page_index: int = 0) -> Dict[str, Any]:
    """
    Extract all relevant PFD elements from a single page.
    Falls back to empty-list results when PDF libraries are unavailable.
    """
    raw_text = _get_page_text(file_path, page_index)

    equipment_tags  = _unique(_RE_EQUIP_TAG.findall(raw_text))
    stream_numbers  = _extract_stream_numbers(raw_text)
    title_block     = _extract_title_block(raw_text)
    relief_devices  = _unique(_RE_RELIEF.findall(raw_text))
    control_valves  = _unique(_RE_CTRL_VALVE.findall(raw_text))
    utility_headers = _unique(_RE_UTILITY.findall(raw_text))
    holds           = _unique(_RE_HOLD.findall(raw_text))
    notes           = _unique([m.group(1) for m in _RE_NOTE.finditer(raw_text)])
    vessels_hx      = _unique(_RE_VESSEL_HX.findall(raw_text))
    tag_positions   = _extract_tag_positions(file_path, page_index)

    return {
        'equipment_tags':  equipment_tags,
        'stream_numbers':  stream_numbers,
        'title_block':     title_block,
        'relief_devices':  relief_devices,
        'control_valves':  control_valves,
        'utility_headers': utility_headers,
        'holds':           holds,
        'notes':           notes,
        'vessels_hx':      vessels_hx,
        'raw_text':        raw_text,
        'tag_positions':   tag_positions,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_page_text(file_path: str, page_index: int) -> str:
    try:
        import fitz
        pdf  = fitz.open(file_path)
        page = pdf[page_index] if page_index < len(pdf) else pdf[0]
        text = page.get_text('text')
        pdf.close()
        return text
    except ImportError:
        logger.warning('[PFDQ Extraction] PyMuPDF unavailable — returning empty text')
        return ''
    except Exception as exc:
        logger.exception('[PFDQ Extraction] Failed to extract text: %s', exc)
        return ''


def _extract_stream_numbers(text: str):
    """
    Return sorted list of unique integer stream numbers found in text.
    Filters out numbers that are likely coordinates or years (>9000).
    """
    nums = set()
    for m in _RE_STREAM_NUM.finditer(text):
        n = int(m.group(1))
        if 1 <= n <= 9000:
            nums.add(n)
    return sorted(nums)


def _extract_title_block(text: str) -> dict:
    dwg_no   = ''
    revision = ''

    m = _RE_DWG_NUMBER.search(text)
    if m:
        dwg_no = m.group(1).strip()

    m = _RE_REVISION.search(text)
    if m:
        revision = m.group(1).strip()

    return {
        'drawing_number': dwg_no,
        'revision':       revision,
        'project_name':   '',
    }


# ---------------------------------------------------------------------------
# Coordinate extraction — word-level bounding boxes via PyMuPDF + OCR fallback
# ---------------------------------------------------------------------------

# Soft-coded: patterns whose matches get spatial positions stored in tag_positions.
# Extend this list to add new tag classes without changing the pipeline.
_POSITIONAL_PATTERNS = [
    _RE_EQUIP_TAG,
    _RE_RELIEF,
    _RE_CTRL_VALVE,
    _RE_VESSEL_HX,
    _RE_HOLD,
]

# Soft-coded OCR constants — tune without touching logic
_OCR_DPI            = 150   # render DPI for OCR (higher = slower but more accurate)
_OCR_CONF_THRESHOLD = 40    # minimum pytesseract word confidence (0-100) to accept
_OCR_LANG           = 'eng' # tesseract language


def _match_all_patterns(clean: str) -> list:
    """Return list of tag strings that match any positional pattern in a single word."""
    tags = []
    for pat in _POSITIONAL_PATTERNS:
        m = pat.search(clean)
        if m:
            tag = m.group(1) if m.lastindex else m.group(0)
            if tag:
                tags.append(tag)
    # Standalone stream numbers (1–4 digit labels common on PFDs)
    if re.match(r'^\d{1,4}$', clean):
        n = int(clean)
        if 1 <= n <= 9000:
            tags.append(clean)
    return list(set(tags))


def _push_position(positions: dict, tag: str, cx: float, cy: float) -> None:
    """Append an occurrence for tag; create entry on first encounter."""
    occ = {'x_pct': cx, 'y_pct': cy}
    if tag not in positions:
        positions[tag] = {'x_pct': cx, 'y_pct': cy, 'all': [occ]}
    else:
        positions[tag]['all'].append(occ)


def _apply_words_to_positions(words: list, pw: float, ph: float, positions: dict) -> None:
    """Populate positions from PyMuPDF word list."""
    for w in words:
        x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4]
        clean = text.strip()
        if not clean:
            continue
        cx = round((x0 + x1) / 2 / pw * 100, 2)
        cy = round((y0 + y1) / 2 / ph * 100, 2)
        for tag in _match_all_patterns(clean):
            _push_position(positions, tag, cx, cy)


def _apply_ocr_to_positions(page, pw: float, ph: float, positions: dict) -> None:
    """
    OCR fallback: render the page to a raster image and extract tag positions
    via pytesseract.  Used when the PDF has no embedded text layer (vector/scanned).
    """
    try:
        import fitz
        import io
        import pytesseract
        from PIL import Image

        mat = fitz.Matrix(_OCR_DPI / 72, _OCR_DPI / 72)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = Image.open(io.BytesIO(pix.tobytes('png')))
        img_w, img_h = img.width, img.height

        data = pytesseract.image_to_data(
            img, lang=_OCR_LANG,
            output_type=pytesseract.Output.DICT,
        )
        found = 0
        for i, word in enumerate(data['text']):
            conf = int(data['conf'][i])
            if conf < _OCR_CONF_THRESHOLD:
                continue
            clean = word.strip()
            if not clean:
                continue
            x = data['left'][i]
            y = data['top'][i]
            w = data['width'][i]
            h = data['height'][i]
            cx = round((x + w / 2) / img_w * 100, 2)
            cy = round((y + h / 2) / img_h * 100, 2)
            for tag in _match_all_patterns(clean):
                _push_position(positions, tag, cx, cy)
                found += 1
        logger.info('[PFDQ] OCR extracted %d tag position(s)', found)
    except ImportError:
        logger.debug('[PFDQ] pytesseract/PIL unavailable — skipping OCR fallback')
    except Exception as exc:
        logger.warning('[PFDQ] OCR extraction failed: %s', exc)


def _extract_tag_positions(file_path: str, page_index: int) -> dict:
    """
    Return {tag: {'x_pct': float, 'y_pct': float, 'all': [{'x_pct', 'y_pct'}, ...]}}

    Strategy:
      1. PyMuPDF word-level extraction  (instant, accurate for text-layer PDFs)
      2. pytesseract OCR fallback       (slower, handles vector/scanned PDFs)

    The 'all' array stores EVERY occurrence so the frontend can pick the one
    nearest the drawing content centroid, filtering out title-block hits.
    Falls back to empty dict on any error or missing dependency.
    """
    positions: Dict[str, Any] = {}
    try:
        import fitz
        pdf  = fitz.open(file_path)
        page = pdf[page_index] if page_index < len(pdf) else pdf[0]
        pw   = page.rect.width
        ph   = page.rect.height
        if pw <= 0 or ph <= 0:
            pdf.close()
            return positions

        # Stage 1: text layer extraction
        words = page.get_text('words')
        _apply_words_to_positions(words, pw, ph, positions)

        # Stage 2: OCR fallback when drawing has no text layer
        if not positions:
            logger.debug('[PFDQ] No text layer — attempting OCR fallback')
            _apply_ocr_to_positions(page, pw, ph, positions)

        pdf.close()
    except ImportError:
        logger.debug('[PFDQ] PyMuPDF unavailable — tag_positions will be empty')
    except Exception as exc:
        logger.warning('[PFDQ] Tag position extraction failed: %s', exc)
    return positions


def _unique(items) -> list:
    seen = set()
    result = []
    for item in items:
        key = item.upper() if isinstance(item, str) else item
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result
