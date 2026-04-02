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


def _unique(items) -> list:
    seen = set()
    result = []
    for item in items:
        key = item.upper() if isinstance(item, str) else item
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result
