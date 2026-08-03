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
from functools import lru_cache
from typing import Any, Dict

from .legend_knowledge import load_legend_knowledge

logger = logging.getLogger(__name__)

# Soft-coded standard NPS inch sizes accepted by deterministic parser.
_STANDARD_NPS_INCH = {
    0.5, 0.75, 1.0, 1.25, 1.5,
    2.0, 2.5, 3.0, 4.0, 5.0, 6.0,
    8.0, 10.0, 12.0, 14.0, 16.0,
    18.0, 20.0, 24.0,
}

# Soft-coded: occurrence deduplication grid size (% of drawing width / height).
# OCR reads of the *same physical annotation* within this radius are merged into
# a single occurrence.  Multi-pass scanning and word-window concatenation often
# produce 2-3 readings of the same label differing by 1-2 % — using a 3 % grid
# collapses those into one entry, preventing inflated occurrence counts.
# Increase to merge more aggressively; decrease for finer spatial precision.
_OCC_DEDUP_GRID_PCT = 3.0

# Fixed OCR config for deterministic output
TESSERACT_CONFIGS = [
    '--oem 1 --psm 11',  # Sparse text mode
    '--oem 1 --psm 6',   # Block text mode
    '--oem 1 --psm 12',  # Sparse text with OSD
    '--oem 1 --psm 4',   # Column-aware mode for engineering sheets
]


def extract_drawing(file_path: str, page_index: int = 0, legend_data: dict | None = None) -> Dict[str, Any]:
    """
    Extract all P&ID elements from a single page/drawing.
    Returns ExtractionResult dict (see module docstring).
    legend_data: optional per-project legend (overrides global if provided).
    """
    raw_text = _run_ocr(file_path, page_index)
    tag_positions = _extract_tag_positions(file_path, page_index)

    # Resolve prefix sets: use per-project legend if available, else global.
    if legend_data:
        instr_pfix, valve_pfix = _legend_prefixes_from(legend_data)
    else:
        instr_pfix, valve_pfix = None, None

    return {
        'tags':                _extract_tags(raw_text),
        'instruments':         _extract_instruments(raw_text, instr_pfix),
        'valves':              _extract_valves(raw_text, valve_pfix),
        'equipment':           _extract_equipment(raw_text),
        'pipelines':           [],   # Requires CV pipeline (deferred to graph builder)
        'notes':               _extract_notes(raw_text),
        'holds':               _extract_holds(raw_text),
        'line_sizes':          _extract_line_sizes(raw_text),
        'raw_text':            raw_text,
        'tag_positions':       tag_positions,   # {tag: {x_pct, y_pct}} real diagram coords
        # Multi-angle pipeline designation extraction (soft-coded, additive).
        'line_tags':           _extract_pipeline_tags_multi_angle(file_path, page_index),
        # Revision / scope-change signals (soft-coded, vector PDFs only).
        'red_annotations':     _extract_red_annotations(file_path, page_index),
        # Reducer notations e.g. 6"x2" and valve-type size contexts.
        'reducers':            _extract_reducers(raw_text),
        'valve_size_contexts': _extract_valve_size_contexts(raw_text),
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
        from PIL import Image, ImageOps, ImageFilter
        import fitz  # PyMuPDF

        ext = file_path.rsplit('.', 1)[-1].lower()

        images = []
        if ext == 'pdf':
            doc = fitz.open(file_path)
            page = doc[page_index]
            import io
            # Multi-DPI pass improves recovery of small text like 6"/4" labels.
            for dpi in (150, 300, 450):
                mat = fitz.Matrix(dpi / 72, dpi / 72)
                pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
                images.append(Image.open(io.BytesIO(pix.tobytes('png'))))
            doc.close()
        else:
            images.append(Image.open(file_path).convert('L'))

        def _variants(img):
            base = img.convert('L')
            out = [base]

            # Variant 1: auto-contrast + mild sharpen
            v1 = ImageOps.autocontrast(base)
            v1 = v1.filter(ImageFilter.SHARPEN)
            out.append(v1)

            # Variant 2: binarized text map for faint scans
            v2 = ImageOps.autocontrast(base)
            v2 = v2.point(lambda p: 255 if p > 170 else 0)
            out.append(v2)

            return out

        all_text_parts = []
        seen_lines = set()
        for img in images:
            for variant in _variants(img):
                for cfg in TESSERACT_CONFIGS:
                    txt = pytesseract.image_to_string(variant, config=cfg)
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
_LINE_SIZE_PATTERN = re.compile(r'\b(\d+(?:\.\d+)?)\s*(?:"|”|\'\'|mm|DN)(?=\s|$|[^A-Za-z0-9_])', re.IGNORECASE)
_EQUIPMENT_TYPES   = re.compile(r'\b(V|E|T|K|C|P|H|X|F|R)-\d{3,5}\b')

# Pipeline line designation pattern.
# Matches designations like: 2"-D-6152-033842-X-N  or  4"-D-5690-013842-X_N
# Format: {NPS}[inch_mark]{sep}{fluid_code}{sep}{area_code}{sep}{seq_no}[{sep}{pipe_class}][{sep}{insulation}]
# Separators may be -, _, or a space (OCR noise tolerance).
_PIPELINE_DESIG_RE = re.compile(
    r'(?<![A-Za-z0-9])'                               # not preceded by alphanumeric
    r'(\d+(?:\.\d+)?)'                                 # group 1: NPS size (e.g. 2, 4, 6)
    r'\s*["\u201c\u201d\u2019\u2018\'`]{1,2}'          # inch mark (straight or smart quotes)
    r'[\s\-_]{0,3}'                                    # optional gap after inch mark
    r'([A-Z]{1,4})'                                    # group 2: fluid/service code (D, BD, HO)
    r'[\s\-_]+'                                        # separator
    r'(\d{3,6})'                                       # group 3: area / service code (3-6 digits)
    r'[\s\-_]+'                                        # separator
    r'(\d{4,8})'                                       # group 4: sequence number (4-8 digits)
    r'(?:[\s\-_]+([A-Z0-9]{1,8}))?'                   # group 5: pipe class (optional)
    r'(?:[\s\-_]+([A-Z0-9]{1,4}))?'                   # group 6: insulation/spec (optional)
    r'(?![A-Za-z0-9])',                                # not followed by alphanumeric
    re.IGNORECASE,
)

_DEFAULT_VALVE_PREFIXES = {
    'HV', 'FV', 'XV', 'PV', 'SDV', 'BDV', 'PSV', 'PRV', 'CV', 'LV', 'TV',
    # Mubarraz project additions (PJ6-EXD-GEN-BQDA-0002)
    'SSV', 'MSV', 'MOV', 'SOV', 'DBB', 'RD', 'RO', 'SV',
}
_DEFAULT_INSTRUMENT_PREFIXES = {
    'FT', 'FI', 'FIC', 'PT', 'PI', 'PIC', 'LT', 'LI', 'LIC',
    'TT', 'TI', 'TIC', 'AT', 'AI', 'FY', 'PY', 'LY',
    # Mubarraz project additions (PJ6-EXD-GEN-BQDA-0002)
    'ZT', 'ZSH', 'ZSL', 'ZSHH', 'ZSLL', 'ZI',
    'ST', 'SI', 'IT', 'II', 'VT', 'VI', 'VSH', 'VSHH',
    'DT', 'DI', 'DPT', 'DPI',
    'WT', 'WI', 'JT', 'JI', 'OT', 'OI', 'UT', 'UI', 'RT',
    'NOC', 'GWR', 'WC', 'GVF',
}


@lru_cache(maxsize=1)
def _legend_prefixes() -> tuple[set[str], set[str]]:
    """Load dynamic prefixes from persisted legend knowledge JSON."""
    data = load_legend_knowledge()
    instrument = set(_DEFAULT_INSTRUMENT_PREFIXES)
    valves = set(_DEFAULT_VALVE_PREFIXES)

    for p in data.get('instrument_prefixes', []):
        if isinstance(p, str):
            instrument.add(p.upper().strip())
    for p in data.get('valve_prefixes', []):
        if isinstance(p, str):
            valves.add(p.upper().strip())

    return instrument, valves


def _legend_prefixes_from(legend_data: dict) -> tuple[set[str], set[str]]:
    """Compute prefix sets from per-project legend data (no caching)."""
    instrument = set(_DEFAULT_INSTRUMENT_PREFIXES)
    valves = set(_DEFAULT_VALVE_PREFIXES)
    for p in legend_data.get('instrument_prefixes', []):
        if isinstance(p, str):
            instrument.add(p.upper().strip())
    for p in legend_data.get('valve_prefixes', []):
        if isinstance(p, str):
            valves.add(p.upper().strip())
    return instrument, valves


def _extract_tags(text: str):
    return sorted(set(_TAG_PATTERN.findall(text)))


def _extract_instruments(text: str, override_prefixes: set | None = None):
    instrument_prefixes, _ = _legend_prefixes() if override_prefixes is None else (override_prefixes, None)
    items = []
    for m in _TAG_PATTERN.finditer(text):
        tag = m.group(1)
        prefix = tag.split('-')[0]
        if prefix in instrument_prefixes:
            items.append({'tag': tag, 'type': prefix})
    return items


def _extract_valves(text: str, override_prefixes: set | None = None):
    _, valve_prefixes = _legend_prefixes() if override_prefixes is None else (None, override_prefixes)
    items = []
    for m in _TAG_PATTERN.finditer(text):
        tag = m.group(1)
        prefix = tag.split('-')[0]
        if prefix in valve_prefixes:
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
    holds = [m.group(0).strip() for m in _HOLD_PATTERN.finditer(text)]
    # Capture common drawing hold headers even when no HOLD-<n> token exists.
    for line in text.splitlines():
        ln = line.strip()
        if not ln:
            continue
        if 'HOLD' in ln.upper() and ln not in holds:
            holds.append(ln[:200])
    return holds


def _extract_line_sizes(text: str):
    items = []

    def _normalize_size(raw: str) -> str | None:
        token = ' '.join(raw.replace('”', '"').split())
        token = token.replace("''", '"')

        # Extract numeric value and unit while tolerating OCR whitespace/newlines.
        m = re.match(r'^(\d+(?:\.\d+)?)\s*("|mm|DN)$', token, flags=re.IGNORECASE)
        if not m:
            return None

        num = m.group(1)
        unit = m.group(2)
        try:
            value = float(num)
        except ValueError:
            return None

        # Soft-coded plausibility guard for inch annotations.
        if unit == '"':
            if value <= 0 or value > 24:
                return None
            if value not in _STANDARD_NPS_INCH:
                return None
            if value.is_integer():
                return f'{int(value)}"'
            return f'{value}"'

        return f'{num}{unit}'

    seen = set()
    for m in _LINE_SIZE_PATTERN.finditer(text):
        normalized = _normalize_size(m.group(0).strip())
        if not normalized:
            continue
        key = (normalized, 'unknown')
        if key in seen:
            continue
        seen.add(key)
        items.append({
            'text': normalized,
            'direction': 'unknown',   # Direction requires CV; set to unknown for now
        })
    return items


def _extract_tag_positions(file_path, page_index):
    # type: (str, int) -> dict
    """
    Extract real bounding-box anchor coordinates for every locatable text
    element on the page, stored as percentage offsets of the page dimensions.

    Path A (vector PDF): PyMuPDF get_text('words') + adjacent-pair + span-level.
    Path B (scanned PDF): Tesseract image_to_data() at 300 dpi, used when
      Path A yields 0 word tokens (image-only / scanned PDF).

    For line sizes, ALL body occurrences are accumulated; the centroid and
    full list are stored so the frontend renders one dot per pipe occurrence.
    Returns {} on any error so callers fall back to stableUnit hash positions.
    """
    positions = {}
    _ls_all   = {}

    # Title-block exclusion zones (fraction of page; tune for non-standard layouts)
    _TB_Y_FRAC = 0.88
    _TB_X_FRAC = 0.88
    _DEDUP_PCT = 1.5

    # Quote chars used as inch marks in PDF/OCR text.
    _QUOTE_CLASS = u'["\u201c\u201d\'\u2019]'
    # Matches NPS annotations inside any text token, including pipeline
    # designations like "4"-BD-4860-033842-X-N" where the size precedes a dash.
    # End condition: the unit char must NOT be immediately followed by a digit
    # (avoids matching "1" inside "12345"), but allows "-", letters, and spaces.
    _LS_PAT = re.compile(
        u'(?:^|\\b)(\\d+(?:\\.\\d+)?)\\s*(' + _QUOTE_CLASS + u'{1,2}|mm|DN)(\\d*)(?![\\d])',
        re.IGNORECASE,
    )
    _NUM_ONLY  = re.compile(r'^\d+(?:\.\d+)?$')
    _UNIT_ONLY = re.compile(u'^(' + _QUOTE_CLASS + u'{1,2}|mm)$', re.IGNORECASE)

    def _canonical_ls(num_str, unit, suffix=''):
        try:
            val = float(num_str)
        except (ValueError, TypeError):
            return None
        u = unit.strip().lower()
        is_inch = (u in ('"',) or
                   u == u'\u201c' or u == u'\u201d' or u == u'\u2019' or
                   u in ("''", '""'))
        if is_inch:
            if val <= 0 or val > 24 or val not in _STANDARD_NPS_INCH:
                return None
            return ('%d"' % int(val)) if val == int(val) else ('%s"' % num_str)
        if u == 'mm':
            return ('%dmm' % int(val)) if val == int(val) else ('%smm' % num_str)
        if u == 'dn':
            suf = str(suffix).strip()
            return ('DN%s%s' % (int(val), suf)) if suf else ('DN%d' % int(val))
        return None

    def _pct(v, dim):
        return round(float(v) / float(dim) * 100.0, 2)

    def _record(key, xp, yp):
        pt   = {'x_pct': xp, 'y_pct': yp}
        buck = _ls_all.setdefault(key, [])
        for ex in buck:
            if abs(ex['x_pct'] - xp) < _DEDUP_PCT and abs(ex['y_pct'] - yp) < _DEDUP_PCT:
                return
        buck.append(pt)

    def _scan_text(text, xp, yp):
        for m in _LS_PAT.finditer(text):
            key = _canonical_ls(m.group(1), m.group(2), m.group(3))
            if key:
                _record(key, xp, yp)

    def _process_words(words, dim_w, dim_h):
        # words: list of (x0, y0, x1, y1, text)
        for idx, (x0, y0, x1, y1, word) in enumerate(words):
            if not word:
                continue
            xp = _pct((x0 + x1) / 2.0, dim_w)
            yp = _pct((y0 + y1) / 2.0, dim_h)

            # Tags: FT-101, XV-202A, etc.
            tm = _TAG_PATTERN.fullmatch(word)
            if tm:
                tag = tm.group(1)
                if tag not in positions:
                    positions[tag] = {'x_pct': xp, 'y_pct': yp}
                continue

            # Strategy 1: word is entirely a line-size annotation.
            _scan_text(word, xp, yp)

            # Strategy 2: adjacent pair -- pure number + lone quote next token.
            if _NUM_ONLY.match(word) and idx + 1 < len(words):
                nxt = words[idx + 1]
                if _UNIT_ONLY.match(nxt[4]):
                    combined = word + nxt[4].strip()
                    pair_xp = _pct((x0 + nxt[2]) / 2.0, dim_w)
                    pair_yp = _pct((min(y0, nxt[1]) + max(y1, nxt[3])) / 2.0, dim_h)
                    _scan_text(combined, pair_xp, pair_yp)

    try:
        import fitz
        ext = file_path.rsplit('.', 1)[-1].lower()
        if ext != 'pdf':
            return positions

        doc = fitz.open(file_path)
        if page_index >= len(doc):
            doc.close()
            return positions

        page   = doc[page_index]
        page_w = page.rect.width  or 1
        page_h = page.rect.height or 1

        # --- Path A: embedded-text PDF ---
        raw_words = [
            (w[0], w[1], w[2], w[3], w[4].strip())
            for w in page.get_text('words')
            if w[4].strip()
        ]

        if raw_words:
            _process_words(raw_words, page_w, page_h)
            # Strategy 3: span-level scanning (no punctuation split in font runs).
            try:
                for blk in page.get_text('dict', flags=0)['blocks']:
                    for ln in blk.get('lines', []):
                        for sp in ln.get('spans', []):
                            s = sp.get('text', '').strip()
                            if s:
                                b = sp['bbox']
                                _scan_text(s,
                                           _pct((b[0] + b[2]) / 2.0, page_w),
                                           _pct((b[1] + b[3]) / 2.0, page_h))
            except Exception:
                pass

        else:
            # --- Path B: scanned/image PDF -- OCR with per-word bounding boxes ---
            try:
                import pytesseract
                from PIL import Image
                import io

                dpi = 300
                mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
                pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY, alpha=False)
                img = Image.open(io.BytesIO(pix.tobytes('png')))
                img_w, img_h = img.size

                data = pytesseract.image_to_data(
                    img,
                    config='--oem 1 --psm 11',
                    output_type=pytesseract.Output.DICT,
                )
                n = len(data.get('text', []))
                ocr_words = []
                for i in range(n):
                    txt = str(data['text'][i]).strip()
                    if not txt:
                        continue
                    try:
                        conf = int(data['conf'][i])
                    except (ValueError, TypeError):
                        conf = -1
                    if conf < 20:   # reject very-low-confidence noise
                        continue
                    lft = int(data['left'][i])
                    top = int(data['top'][i])
                    wid = int(data['width'][i])
                    hgt = int(data['height'][i])
                    ocr_words.append((lft, top, lft + wid, top + hgt, txt))

                _process_words(ocr_words, img_w, img_h)

            except ImportError:
                pass
            except Exception as exc:
                logger.debug('[PIDExtraction] OCR coord pass skipped: %s', exc)

        doc.close()

        # --- Finalise line-size positions ---
        # Soft-coded: drawing content centroid used to pick the most representative
        # NPS occurrence.  The arithmetic average of many "4\"" tokens scattered
        # across a large drawing often lands between actual text elements (i.e. on a
        # pipe line or blank space) rather than on a readable label.
        # Using the occurrence closest to this centroid gives a pinpoint anchor.
        # Adjust _CONTENT_CX_PCT / _CONTENT_CY_PCT if your drawing type has a
        # non-standard content layout (e.g. portrait sheets, left-anchored title block).
        _CONTENT_CX_PCT = 50.0   # typical P&ID main-area horizontal centre
        _CONTENT_CY_PCT = 40.0   # biased slightly above mid — instruments cluster here
        for key, pts in _ls_all.items():
            if key in positions:
                continue
            body = [p for p in pts
                    if p['y_pct'] / 100.0 < _TB_Y_FRAC and p['x_pct'] / 100.0 < _TB_X_FRAC]
            use  = body if body else pts
            # Pick the single occurrence nearest the content centroid rather than the
            # arithmetic average (which can be a phantom midpoint between occurrences).
            best = min(use, key=lambda p: (p['x_pct'] - _CONTENT_CX_PCT) ** 2
                                         + (p['y_pct'] - _CONTENT_CY_PCT) ** 2)
            positions[key] = {
                'x_pct': round(best['x_pct'], 2),
                'y_pct': round(best['y_pct'], 2),
                'all':   use,
            }

    except ImportError:
        pass
    except Exception as exc:
        logger.debug('[PIDExtraction] tag_positions extraction skipped: %s', exc)

    return positions


# ---------------------------------------------------------------------------
# Pipeline line designation extractor (multi-angle)
# ---------------------------------------------------------------------------

def _normalize_pipeline_desig_key(size_num: str, fluid: str, area: str, seq: str,
                                   pipe_class: str = '', insulation: str = '') -> str:
    """Canonical dedup key: uppercase, no spaces."""
    parts = [f'{size_num}"-{fluid.upper()}-{area}-{seq}']
    if pipe_class:
        parts[0] += f'-{pipe_class.upper()}'
    if insulation:
        parts[0] += f'-{insulation.upper()}'
    return parts[0]


def _extract_pipeline_tags_multi_angle(file_path: str, page_index: int) -> list:
    """
    Detect pipeline line designations like ``2"-D-6152-033842-X-N`` from a single
    drawing page in both **horizontal** and **vertical** orientations.

    Strategy
    --------
    * **Vector PDF** (embedded text): PyMuPDF ``get_text('dict')`` exposes the
      ``dir`` vector for each text line, allowing H vs V classification without
      any image rotation.
    * **Scanned / image PDF**: The page is rendered at 200 DPI then OCR'd three
      times — original (0°), rotated 90° CW, and rotated 90° CCW — so that text
      written vertically along pipe runs in either direction is captured.

    Returns a deduplicated list of pipeline tag dicts, one entry per unique
    canonical designation.  Each entry::

        {
          "text":        "2\\"-D-6152-033842-X-N",
          "size":        "2\\"",
          "fluid_code":  "D",
          "area_code":   "6152",
          "sequence_no": "033842",
          "pipe_class":  "X",
          "insulation":  "N",
          "occurrences": [
            {"direction": "H", "x_pct": 45.2, "y_pct": 30.1},
            {"direction": "V", "x_pct": 45.5, "y_pct": 30.3},
          ],
          "count":       2,
          "directions":  ["H", "V"],
          "multi_angle": True,   # same tag confirmed in ≥2 orientations
        }
    """
    accumulated = {}   # norm_key → entry dict (+_seen helper for dedup)

    def _nps_valid(num_str: str) -> bool:
        try:
            return float(num_str) in _STANDARD_NPS_INCH
        except (ValueError, TypeError):
            return False

    def _record(size_num, fluid, area, seq, pcls, insul, direction, x_pct, y_pct):
        if not _nps_valid(size_num):
            return
        norm_key = _normalize_pipeline_desig_key(size_num, fluid, area, seq, pcls, insul)
        if norm_key not in accumulated:
            accumulated[norm_key] = {
                'text':        norm_key,
                'size':        f'{size_num}"',
                'fluid_code':  fluid.upper(),
                'area_code':   area,
                'sequence_no': seq,
                'pipe_class':  pcls.upper(),
                'insulation':  insul.upper(),
                'occurrences': [],
                '_seen':       set(),
            }
        entry  = accumulated[norm_key]
        # Use a grid-based dedup key so that OCR reads of the same physical
        # label within _OCC_DEDUP_GRID_PCT radius are collapsed into one
        # occurrence.  The stored x_pct/y_pct keep full 2-decimal precision.
        occ_key = (direction,
                   round(x_pct / _OCC_DEDUP_GRID_PCT),
                   round(y_pct / _OCC_DEDUP_GRID_PCT))
        if occ_key not in entry['_seen']:
            entry['_seen'].add(occ_key)
            entry['occurrences'].append({
                'direction': direction,
                'x_pct':     round(x_pct, 2),
                'y_pct':     round(y_pct, 2),
            })

    def _scan_text(text, direction, x_pct, y_pct):
        for m in _PIPELINE_DESIG_RE.finditer(text):
            _record(
                m.group(1),          # NPS size
                m.group(2),          # fluid code
                m.group(3),          # area code
                m.group(4),          # sequence number
                m.group(5) or '',    # pipe class
                m.group(6) or '',    # insulation
                direction, x_pct, y_pct,
            )

    try:
        import fitz
        if file_path.rsplit('.', 1)[-1].lower() != 'pdf':
            return []

        doc    = fitz.open(file_path)
        if page_index >= len(doc):
            doc.close()
            return []

        page   = doc[page_index]
        page_w = page.rect.width  or 1
        page_h = page.rect.height or 1

        def _pct(v, dim):
            return round(float(v) / float(dim) * 100.0, 2)

        # ── Path A: vector PDF – span direction vectors ──────────────────
        raw_words = [w for w in page.get_text('words') if w[4].strip()]
        has_text  = len(raw_words) > 5

        if has_text:
            try:
                for blk in page.get_text('dict', flags=0)['blocks']:
                    for ln in blk.get('lines', []):
                        dir_v     = ln.get('dir', (1, 0))
                        direction = 'V' if abs(dir_v[0]) < 0.3 else 'H'
                        spans_list = ln.get('spans', [])

                        # Scan each span individually
                        for sp in spans_list:
                            txt = sp.get('text', '').strip()
                            if not txt:
                                continue
                            b  = sp['bbox']
                            xp = _pct((b[0] + b[2]) / 2.0, page_w)
                            yp = _pct((b[1] + b[3]) / 2.0, page_h)
                            _scan_text(txt, direction, xp, yp)

                        # Also scan the full joined line text (catches tokens split across spans)
                        if len(spans_list) > 1:
                            line_txt = ' '.join(sp.get('text', '') for sp in spans_list)
                            b0 = spans_list[0]['bbox']
                            bN = spans_list[-1]['bbox']
                            xp = _pct((b0[0] + bN[2]) / 2.0, page_w)
                            yp = _pct((b0[1] + bN[3]) / 2.0, page_h)
                            _scan_text(line_txt, direction, xp, yp)

                    # Block-level scan: join spans from ALL lines in this block.
                    # Catches pipeline designations whose NPS/fluid/area/seq tokens
                    # fall in separate PyMuPDF "lines" within the same text block
                    # (common in engineering PDFs with mixed font sizes for the
                    # inch-mark character vs the rest of the designation).
                    _blk_all_spans = [
                        sp for _bln in blk.get('lines', [])
                        for sp in _bln.get('spans', [])
                        if sp.get('text', '').strip()
                    ]
                    if len(_blk_all_spans) > 2:
                        _blk_joined = ' '.join(sp['text'] for sp in _blk_all_spans)
                        if _PIPELINE_DESIG_RE.search(_blk_joined):
                            _b0 = _blk_all_spans[0]['bbox']
                            _bN = _blk_all_spans[-1]['bbox']
                            _bx = _pct((_b0[0] + _bN[2]) / 2.0, page_w)
                            _by = _pct((_b0[1] + _bN[3]) / 2.0, page_h)
                            _blk_lines = blk.get('lines', [])
                            _blk_dir = 'V' if _blk_lines and abs(_blk_lines[0].get('dir', (1, 0))[0]) < 0.3 else 'H'
                            _scan_text(_blk_joined, _blk_dir, _bx, _by)
            except Exception as e:
                logger.debug('[PIDLineTags] Vector path error: %s', e)

            # ── Word-level pass: scan every word token individually ─────────
            # get_text('words') can return tokens as full "4\"-D-6024-123456-A-N"
            # even when the dict-based span view splits them across font runs.
            # Dedup in _record() prevents double-counting.
            # Also run a 2-word sliding window (no-space concatenation) to catch
            # designations split at the inch-mark: "4\"" adjacent to "-D-6024-...".
            # Soft-coded proximity threshold: words within 20pt are on the same line.
            _WIN_PROX_PT = 20
            try:
                for _wi, _w in enumerate(raw_words):
                    _wx = _pct((_w[0] + _w[2]) / 2.0, page_w)
                    _wy = _pct((_w[1] + _w[3]) / 2.0, page_h)
                    _scan_text(_w[4], 'H', _wx, _wy)

                    # 2-word window: concatenate with the immediately following token
                    if _wi + 1 < len(raw_words):
                        _w2 = raw_words[_wi + 1]
                        _mid_y1 = (_w[1]  + _w[3])  / 2.0
                        _mid_y2 = (_w2[1] + _w2[3]) / 2.0
                        if abs(_mid_y1 - _mid_y2) < _WIN_PROX_PT:
                            _comb = _w[4] + _w2[4]
                            if len(_comb) > 10 and _PIPELINE_DESIG_RE.search(_comb):
                                _cwx = _pct((_w[0] + _w2[2]) / 2.0, page_w)
                                _cwy = _pct((_w[1] + _w2[3]) / 2.0, page_h)
                                _scan_text(_comb, 'H', _cwx, _cwy)
            except Exception as _e:
                logger.debug('[PIDLineTags] Word-window pass error: %s', _e)

        else:
            # ── Path B: scanned / image PDF – multi-angle Tesseract ─────
            try:
                import pytesseract
                from PIL import Image
                import io

                dpi = 200   # reduced for rotated passes: speed vs quality trade-off
                mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
                pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY, alpha=False)
                orig_img      = Image.open(io.BytesIO(pix.tobytes('png')))
                orig_w, orig_h = orig_img.size
                cfg = '--oem 1 --psm 11'

                def _ocr_words(img):
                    """Return list of (text, x_frac, y_frac) with conf >= 15."""
                    try:
                        data = pytesseract.image_to_data(
                            img, config=cfg, output_type=pytesseract.Output.DICT)
                    except Exception:
                        return []
                    out = []
                    iw, ih = img.size
                    for i in range(len(data.get('text', []))):
                        txt = str(data['text'][i]).strip()
                        if not txt:
                            continue
                        try:
                            conf = int(data['conf'][i])
                        except (ValueError, TypeError):
                            conf = -1
                        if conf < 15:
                            continue
                        lft = int(data['left'][i])
                        top = int(data['top'][i])
                        wid = int(data['width'][i])
                        hgt = int(data['height'][i])
                        out.append((txt,
                                    (lft + wid / 2.0) / iw,
                                    (top + hgt / 2.0) / ih))
                    return out

                def _full_page_text(img):
                    try:
                        return pytesseract.image_to_string(img, config='--oem 1 --psm 6')
                    except Exception:
                        return ''

                # --- 0° (horizontal) ----------------------------------------
                for txt, rx, ry in _ocr_words(orig_img):
                    _scan_text(txt, 'H', rx * 100.0, ry * 100.0)
                _scan_text(_full_page_text(orig_img), 'H', 50.0, 50.0)  # compound token recovery

                # --- 90° CW (PIL ROTATE_270) — picks up bottom-to-top text --
                try:
                    img_cw = orig_img.transpose(Image.Transpose.ROTATE_270)
                except AttributeError:
                    img_cw = orig_img.transpose(4)  # ROTATE_270 = 4 (Pillow < 10 compat)
                for txt, rx, ry in _ocr_words(img_cw):
                    # Map back: CW90 inverse → orig_x ≈ ry, orig_y ≈ 1 - rx
                    _scan_text(txt, 'V', ry * 100.0, (1.0 - rx) * 100.0)
                _scan_text(_full_page_text(img_cw), 'V', 50.0, 50.0)

                # --- 90° CCW (PIL ROTATE_90) — picks up top-to-bottom text --
                try:
                    img_ccw = orig_img.transpose(Image.Transpose.ROTATE_90)
                except AttributeError:
                    img_ccw = orig_img.transpose(2)  # ROTATE_90 = 2 (Pillow < 10 compat)
                for txt, rx, ry in _ocr_words(img_ccw):
                    # Map back: CCW90 inverse → orig_x ≈ 1 - ry, orig_y ≈ rx
                    _scan_text(txt, 'V', (1.0 - ry) * 100.0, rx * 100.0)
                _scan_text(_full_page_text(img_ccw), 'V', 50.0, 50.0)

            except ImportError:
                logger.warning('[PIDLineTags] pytesseract/PIL not available – rotated OCR skipped')
            except Exception as exc:
                logger.warning('[PIDLineTags] Scanned path error: %s', exc)

        doc.close()

    except ImportError:
        pass
    except Exception as exc:
        logger.debug('[PIDLineTags] Extraction skipped: %s', exc)

    # ── Cloud-truncation resolution pass ─────────────────────────────────
    # When a revision cloud partially covers a line designation, OCR reads a
    # truncated label with missing pipe_class / insulation suffix, producing
    # a second key in `accumulated` that is the same physical line.
    # Strategy: for any entry with NO pipe_class, look for a partner entry
    # that has the same (size_num, fluid_code, area_code, sequence_no) AND
    # a non-empty pipe_class.  If found, merge the orphan's occurrences into
    # the full entry and flag `cloud_truncation_detected` so the rule engine
    # can issue a targeted critical finding.
    # Soft-coded: only resolves when pipe_class is entirely absent (the
    # minimal cloud-cover case).  Two full entries with DIFFERENT pipe classes
    # are NOT merged — those represent genuine specification-break lines.
    _base_to_full: dict = {}   # (size, fluid, area, seq) → norm_key of best full entry
    for _nk, _e in accumulated.items():
        if _e['pipe_class']:
            _base = (_e['size'].rstrip('"'), _e['fluid_code'], _e['area_code'], _e['sequence_no'])
            _existing = _base_to_full.get(_base)
            if _existing is None or len(accumulated[_existing]['pipe_class']) < len(_e['pipe_class']):
                _base_to_full[_base] = _nk

    _to_delete = []
    for _nk, _e in accumulated.items():
        if _e['pipe_class']:
            continue   # already a full entry — skip
        _base = (_e['size'].rstrip('"'), _e['fluid_code'], _e['area_code'], _e['sequence_no'])
        _full_nk = _base_to_full.get(_base)
        if _full_nk is None:
            continue   # no full partner — keep truncated as-is
        _full_e = accumulated[_full_nk]
        # Merge orphan occurrences into the full entry
        _full_seen = _full_e.setdefault('_seen', set())
        for _occ in _e['occurrences']:
            _occ_key = (_occ['direction'], round(_occ['x_pct']), round(_occ['y_pct']))
            if _occ_key not in _full_seen:
                _full_seen.add(_occ_key)
                _full_e['occurrences'].append(_occ)
        _full_e['cloud_truncation_detected'] = True   # flag for LSZ-009 rule
        _to_delete.append(_nk)

    for _k in _to_delete:
        del accumulated[_k]

    # ── Build final output (strip internal _seen helper) ─────────────────
    result = []
    for entry in accumulated.values():
        entry.pop('_seen', None)
        occs       = entry['occurrences']
        directions = sorted({o['direction'] for o in occs})
        entry['count']       = len(occs)
        entry['directions']  = directions
        entry['multi_angle'] = len(directions) > 1
        result.append(entry)

    # Sort: largest NPS first, then fluid → area → seq
    result.sort(key=lambda e: (
        -(float(e['size'].replace('"', '') or 0)),
        e['fluid_code'],
        e['area_code'],
        e['sequence_no'],
    ))
    return result


# ---------------------------------------------------------------------------
# Red annotation extraction (vector PDFs via PyMuPDF span color)
# ---------------------------------------------------------------------------

# Soft-coded RGB thresholds for "red" text detection.
# Red-colored text in P&IDs indicates revision marks, HOLD items, and scope changes.
# Tune these if the project uses a different shade of red (e.g. darker crimson).
_RED_R_MIN = 150   # Minimum red channel (0–255)
_RED_G_MAX = 100   # Maximum green channel — keeps yellow/orange out
_RED_B_MAX = 100   # Maximum blue channel — keeps magenta/pink out

def _extract_red_annotations(file_path: str, page_index: int) -> list:
    """
    Return all text spans from the PDF page whose font color is red (or red-adjacent).

    This is a vector-PDF only feature: PyMuPDF exposes the integer RGB color value
    for each text span in 'dict' mode.  Scanned / image PDFs return an empty list
    because the page is a raster image with no per-character color metadata.

    Each returned item::

        {
          "text":  "H @ 65 bar",
          "x_pct": 45.2,   # horizontal centre as % of page width
          "y_pct": 30.1,   # vertical centre as % of page height
          "rgb":   (218, 0, 0),  # original R, G, B values
        }
    """
    items: list = []
    try:
        import fitz
        if file_path.rsplit('.', 1)[-1].lower() != 'pdf':
            return items

        doc = fitz.open(file_path)
        if page_index >= len(doc):
            doc.close()
            return items

        page   = doc[page_index]
        page_w = page.rect.width  or 1
        page_h = page.rect.height or 1

        seen_texts: set = set()
        for blk in page.get_text('dict', flags=0).get('blocks', []):
            for ln in blk.get('lines', []):
                for sp in ln.get('spans', []):
                    color = sp.get('color', 0)
                    # Unpack integer RGB (PyMuPDF stores as 0xRRGGBB integer)
                    r = (color >> 16) & 0xFF
                    g = (color >> 8)  & 0xFF
                    b =  color        & 0xFF
                    if r < _RED_R_MIN or g > _RED_G_MAX or b > _RED_B_MAX:
                        continue
                    text = sp.get('text', '').strip()
                    if not text or len(text) < 2:
                        continue
                    # Deduplicate identical text at very close positions
                    key = text.upper()
                    if key in seen_texts:
                        continue
                    seen_texts.add(key)
                    bbox = sp['bbox']
                    x_pct = round((bbox[0] + bbox[2]) / 2.0 / page_w * 100, 2)
                    y_pct = round((bbox[1] + bbox[3]) / 2.0 / page_h * 100, 2)
                    items.append({
                        'text':  text,
                        'x_pct': x_pct,
                        'y_pct': y_pct,
                        'rgb':   (r, g, b),
                    })

        doc.close()
    except ImportError:
        pass
    except Exception as exc:
        logger.debug('[PIDExtraction] Red annotation extraction skipped: %s', exc)

    return items


# ---------------------------------------------------------------------------
# Reducer notation extractor  (e.g. 6"x2", 6X2, 6"×2")
# ---------------------------------------------------------------------------

# Matches NxM size reduction notations, tolerating optional inch marks and spacing.
# Captures group 1 = larger dimension, group 2 = smaller dimension.
_REDUCER_RE = re.compile(
    r'\b(\d{1,2}(?:\.\d+)?)\s*(?:"|\'\')?'
    r'\s*[xX×]\s*'
    r'(\d{1,2}(?:\.\d+)?)\s*(?:"|\'\')?',
)

def _extract_reducers(raw_text: str) -> list:
    """
    Detect reducer / size-change annotations on the drawing.

    Typical P&ID notation::

        6"x2"   NPS-6 × NPS-2   6X2   6x2"

    Returns a list of dicts::

        {
          "text":        "6\"x2\"",
          "larger_inch": 6.0,
          "smaller_inch": 2.0,
          "ratio":       3.0,    # larger / smaller
        }

    Soft-coded: edit _REDUCER_RE or the plausibility bounds (0.5–24") to tune.
    """
    items: list = []
    seen: set   = set()
    for m in _REDUCER_RE.finditer(raw_text):
        try:
            a = float(m.group(1))
            b = float(m.group(2))
        except ValueError:
            continue
        if a <= 0 or b <= 0:
            continue
        # Normalise: larger first
        larger, smaller = (a, b) if a >= b else (b, a)
        # Plausibility filter — 0.5" to 24" covers all standard NPS sizes
        if larger > 24 or smaller < 0.5:
            continue
        # Avoid matching year-like numbers (e.g. 2026x18 in a title block)
        if larger > 24 or smaller > 24:
            continue
        key = (larger, smaller)
        if key in seen:
            continue
        seen.add(key)
        ratio = round(larger / smaller, 2)
        items.append({
            'text':         f'{int(larger) if larger == int(larger) else larger}"'
                            f'x{int(smaller) if smaller == int(smaller) else smaller}"',
            'larger_inch':  larger,
            'smaller_inch': smaller,
            'ratio':        ratio,
        })
    return items


# ---------------------------------------------------------------------------
# Valve / equipment type + size context extractor
# ---------------------------------------------------------------------------

# Equipment / valve type keywords paired with an adjacent NPS size annotation.
# Add new equipment types here to extend coverage without touching rule logic.
# Group 1 = size (digits), Group 2 = equipment type keyword.
_VALVE_SIZE_CTX_RE = re.compile(
    r'(\d{1,2}(?:\.\d+)?)\s*(?:"|\'\')\s*'
    r'(?:in\s+)?'
    r'(GLOBE\s+VALVE|GLOBE'
    r'|GATE\s+VALVE|GATE'
    r'|CHECK\s+VALVE|CHECK'
    r'|BUTTERFLY\s+VALVE|BUTTERFLY'
    r'|BALL\s+VALVE|BALL'
    r'|CONTROL\s+VALVE|DISTRIBUTED\s+CONTROL\s+VALVE'
    r'|VORTEX\s+BREAKER'
    r'|SAFETY\s+VALVE|RELIEF\s+VALVE|PSV|PRV'
    r'|STRAINER|FILTER'
    r')',
    re.IGNORECASE,
)

def _extract_valve_size_contexts(raw_text: str) -> list:
    """
    Detect valve / equipment type annotations that include an adjacent NPS size.

    Example matches::

        30" GLOBE VALVE         → size 30, type GLOBE VALVE
        20" in VORTEX BREAKER   → size 20, type VORTEX BREAKER
        16 in Distributed Control Valve → size 16, type DISTRIBUTED CONTROL VALVE

    Returns list of dicts::

        {
          "text":           "30\" GLOBE VALVE",
          "size_inch":      30.0,
          "equipment_type": "GLOBE VALVE",
        }

    Soft-coded: extend _VALVE_SIZE_CTX_RE with new equipment keywords.
    """
    items: list = []
    seen: set   = set()
    for m in _VALVE_SIZE_CTX_RE.finditer(raw_text):
        try:
            size_val = float(m.group(1))
        except ValueError:
            continue
        if size_val <= 0 or size_val > 60:
            continue
        equip_type = ' '.join(m.group(2).upper().split())   # normalise whitespace
        key = (round(size_val, 1), equip_type)
        if key in seen:
            continue
        seen.add(key)
        items.append({
            'text':           m.group(0).strip()[:120],
            'size_inch':      size_val,
            'equipment_type': equip_type,
        })
    return items
