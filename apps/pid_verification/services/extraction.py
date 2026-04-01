"""
Extraction Service
==================
AI-assisted OCR/text extraction for a single drawing.
AI is used ONLY for text recognition and symbol bounding boxes.
All downstream validation is deterministic (rule engine).

Returns a structured ExtractionResult dict:
{
  "tags":        ["FV-101", "FT-201", ...],
  "instruments": [{"tag": "FT-201", "type": "FT", "x": 120, "y": 340}, ...],
  "valves":      [{"tag": "FV-101", "type": "gate", "connected": true}, ...],
  "equipment":   [{"tag": "E-100", "type": "vessel"}, ...],
  "pipelines":   [{"line_id": "L1", "from": "A", "to": "B", "size": "6\""}, ...],
  "notes":       ["NOTE 1: All valves NPS >= 2\"...", ...],
  "holds":       ["HOLD-1: Client approval pending", ...],
  "line_sizes":  [{"text": "6\"", "x": 50, "y": 200, "direction": "H"}, ...],
}
"""
import logging
import re
from typing import Any, Dict

logger = logging.getLogger(__name__)

# Fixed OCR config for deterministic output
TESSERACT_CONFIGS = [
    '--oem 1 --psm 11',  # Sparse text mode
    '--oem 1 --psm 6',   # Block text mode (helps with line-size labels)
]


def extract_drawing(file_path: str, page_index: int = 0) -> Dict[str, Any]:
    """
    Extract all P&ID elements from a single page/drawing.
    Returns ExtractionResult dict (see module docstring).
    """
    raw_text = _run_ocr(file_path, page_index)
    return {
        'tags':        _extract_tags(raw_text),
        'instruments': _extract_instruments(raw_text),
        'valves':      _extract_valves(raw_text),
        'equipment':   _extract_equipment(raw_text),
        'pipelines':   [],   # Requires CV pipeline (deferred to graph builder)
        'notes':       _extract_notes(raw_text),
        'holds':       _extract_holds(raw_text),
        'line_sizes':  _extract_line_sizes(raw_text),
        'raw_text':    raw_text,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run_ocr(file_path: str, page_index: int) -> str:
    """
    Run OCR on the specified page.
    Falls back to plain text extraction if pytesseract is unavailable.
    temperature=0 equivalent: fixed model config, no randomness.
    """
    try:
        import pytesseract
        from PIL import Image
        import fitz  # PyMuPDF

        ext = file_path.rsplit('.', 1)[-1].lower()

        images = []
        if ext == 'pdf':
            doc = fitz.open(file_path)
            page = doc[page_index]
            import io
            # Multi-DPI pass improves recovery of small text like 6"/4" labels.
            for dpi in (150, 300):
                mat = fitz.Matrix(dpi / 72, dpi / 72)
                pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
                images.append(Image.open(io.BytesIO(pix.tobytes('png'))))
            doc.close()
        else:
            images.append(Image.open(file_path).convert('L'))

        all_text_parts = []
        seen_lines = set()
        for img in images:
            for cfg in TESSERACT_CONFIGS:
                txt = pytesseract.image_to_string(img, config=cfg)
                for line in txt.splitlines():
                    line_norm = line.strip()
                    if line_norm and line_norm not in seen_lines:
                        seen_lines.add(line_norm)
                        all_text_parts.append(line_norm)

        return '\n'.join(all_text_parts)
    except ImportError:
        logger.warning('[PIDExtraction] pytesseract/fitz not available – using empty extraction')
        return ''
    except Exception as exc:
        logger.error('[PIDExtraction] OCR error: %s', exc)
        return ''


# Regex patterns – all deterministic
_TAG_PATTERN       = re.compile(r'\b([A-Z]{1,4}-[0-9]{3,5}[A-Z]?)\b')
_NOTE_PATTERN      = re.compile(r'NOTE\s*\d+[:\s].{5,200}', re.IGNORECASE)
_HOLD_PATTERN      = re.compile(r'HOLD[- ]\d+[:\s].{5,200}', re.IGNORECASE)
_LINE_SIZE_PATTERN = re.compile(r'\b(\d+(?:\.\d+)?)\s*(?:"|\'\'|mm|DN)\b')
_VALVE_TYPES       = re.compile(r'\b(HV|FV|XV|PV|SDV|BDV|PSV|PRV|CV|LV|TV)\b')
_INSTRUMENT_TYPES  = re.compile(r'\b(FT|FI|FIC|PT|PI|PIC|LT|LI|LIC|TT|TI|TIC|AT|AI|FY|PY|LY)\b')
_EQUIPMENT_TYPES   = re.compile(r'\b(V|E|T|K|C|P|H|X|F|R)-\d{3,5}\b')


def _extract_tags(text: str):
    return sorted(set(_TAG_PATTERN.findall(text)))


def _extract_instruments(text: str):
    items = []
    for m in _TAG_PATTERN.finditer(text):
        tag = m.group(1)
        prefix = tag.split('-')[0]
        if _INSTRUMENT_TYPES.match(prefix):
            items.append({'tag': tag, 'type': prefix})
    return items


def _extract_valves(text: str):
    items = []
    for m in _TAG_PATTERN.finditer(text):
        tag = m.group(1)
        prefix = tag.split('-')[0]
        if _VALVE_TYPES.match(prefix):
            items.append({'tag': tag, 'type': prefix, 'connected': None})
    return items


def _extract_equipment(text: str):
    items = []
    for m in _EQUIPMENT_TYPES.finditer(text):
        items.append({'tag': m.group(0), 'type': m.group(0).split('-')[0]})
    # Deduplicate by tag
    seen = set()
    unique = []
    for item in items:
        if item['tag'] not in seen:
            seen.add(item['tag'])
            unique.append(item)
    return unique


def _extract_notes(text: str):
    return [m.group(0).strip() for m in _NOTE_PATTERN.finditer(text)]


def _extract_holds(text: str):
    return [m.group(0).strip() for m in _HOLD_PATTERN.finditer(text)]


def _extract_line_sizes(text: str):
    items = []
    for m in _LINE_SIZE_PATTERN.finditer(text):
        items.append({
            'text': m.group(0).strip(),
            'direction': 'unknown',   # Direction requires CV; set to unknown for now
        })
    return items
