"""
Equipment Analysis Views - P&ID Equipment List Extraction
"""

import json
import logging
import os
import re
import uuid
from functools import lru_cache

from django.http import HttpResponse
from rest_framework import status as drf_status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

logger = logging.getLogger(__name__)

_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), 'config', 'equipment_type_config.json'
)


@lru_cache(maxsize=1)
def _load_config() -> dict:
    try:
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as fh:
            raw = json.load(fh)
        return {k: v for k, v in raw.items() if not k.startswith('_')}
    except Exception as exc:
        logger.warning('[EquipmentList] Could not load config: %s - using defaults', exc)
        return {
            'extraction': {'context_window_chars': 120, 'description_max_words': 5},
            'type_labels': {
                'V': 'Vessel', 'P': 'Pump', 'E': 'Heat Exchanger', 'T': 'Tank',
                'K': 'Compressor', 'C': 'Column / Tower', 'H': 'Heater / Cooler',
                'D': 'Drum / Separator', 'R': 'Reactor',
            },
            'fluid_keywords': ['crude', 'gas', 'oil', 'water', 'steam'],
            'excel_columns': [
                {'key': 'sl_no',            'label': 'S. No',             'width': 6 },
                {'key': 'tag',              'label': 'Tag Number',         'width': 14},
                {'key': 'type_label',       'label': 'Equipment Type',     'width': 22},
                {'key': 'description',      'label': 'Description',        'width': 30},
                {'key': 'drawing_ref',      'label': 'Drawing Reference',  'width': 22},
                {'key': 'line_connections', 'label': 'Line Connections',   'width': 30},
                {'key': 'service_fluid',    'label': 'Service / Fluid',    'width': 20},
            ],
        }


_LINE_TAG_RE = re.compile(
    r'(?<![A-Za-z0-9])'
    r'(\d+(?:\.\d+)?)\s*["\u201c\u201d\u2019\'`]{1,2}'
    r'[\s\-_]{0,3}([A-Z]{1,4})[\s\-_]+(\d{3,6})[\s\-_]+(\d{4,8})'
    r'(?:[\s\-_]+([A-Z0-9]{1,8}))?'
    r'(?![A-Za-z0-9])',
    re.IGNORECASE,
)


def _normalize_text(text: str) -> str:
    """
    Normalize Unicode variants that CAD tools write in place of ASCII characters.
    Runs on every extracted text string before regex matching.

    Soft-coded by category — add mappings here without touching callers.
    """
    # Non-ASCII hyphens / dashes → ASCII hyphen  (most common CAD encoding issue)
    # U+2010 HYPHEN, U+2011 NON-BREAKING HYPHEN, U+2012/2013/2014 DASHES,
    # U+2212 MINUS SIGN, U+FE63 SMALL HYPHEN-MINUS, U+FF0D FULLWIDTH HYPHEN
    UNICODE_HYPHENS = '\u2010\u2011\u2012\u2013\u2014\u2212\ufe63\uff0d'
    for ch in UNICODE_HYPHENS:
        text = text.replace(ch, '-')
    # Non-breaking space → regular space
    text = text.replace('\u00a0', ' ').replace('\u202f', ' ')
    # Collapse repeated ASCII hyphens (OCR artifact: "V-803--TF" → "V-803-TF")
    import re as _re
    text = _re.sub(r'-{2,}', '-', text)
    return text


def _extract_text_from_pdf(file_obj, config=None) -> str:
    """
    Extract all text from a PDF with three progressive strategies.

    Strategy 1 — block text  : get_text('text') reading-order blocks (fast).
    Strategy 2 — spatial words: get_text('words') sorted by (y-bucket, x) to
        reconstruct left→right order regardless of CAD stream order.
    Strategy 3 — span proximity: iterate spans/chars to bond fragments that a
        CAD tool stored as separate micro-elements (e.g. "V-308" + "-TF").

    All three are run for EVERY vector PDF and their results concatenated so
    the downstream regex sees the text in every possible form.

    Scanned PDFs: OCR fallback (Tesseract) triggered when the combined vector
    text is shorter than the soft-coded min_vector_chars threshold.

    All config values are soft-coded in equipment_type_config.json.
    """
    cfg        = config or {}
    ext_cfg    = cfg.get('extraction', {})
    ocr_angles = ext_cfg.get('ocr_rotation_angles', [0, 90])
    ocr_psm_modes  = ext_cfg.get('ocr_psm_modes', [11, 6])
    ocr_scale      = float(ext_cfg.get('ocr_render_scale', 3.0))
    # Soft-coded: primary render scale for large-format pages (A0/A1 P&IDs)
    _OCR_SCALE_LARGE      = float(ext_cfg.get('ocr_render_scale_large_format', 4.0))
    # Soft-coded: additional full-page scales pooled on large pages
    _OCR_EXTRA_SCALES     = [float(s) for s in ext_cfg.get('ocr_additional_scales_large_format', [2.0])]
    _LARGE_PAGE_THRESHOLD = float(ext_cfg.get('ocr_large_page_threshold_pts', 900))
    # Soft-coded: tile-based OCR grid for large-format pages
    _TILE_ROWS     = int(ext_cfg.get('ocr_tile_rows', 3))
    _TILE_COLS     = int(ext_cfg.get('ocr_tile_cols', 4))
    _TILE_SCALE    = float(ext_cfg.get('ocr_tile_scale', 3.0))
    _TILE_PSM      = int(ext_cfg.get('ocr_tile_psm', 6))
    _TILE_OVERLAP  = float(ext_cfg.get('ocr_tile_overlap_frac', 0.12))
    # Soft-coded: vertical bucket height (pts) for spatially-sorted word pass
    _Y_BUCKET_PTS  = int(ext_cfg.get('spatial_word_y_bucket_pts', 15))
    # Soft-coded: max x-gap (pts) to bond two horizontally adjacent span fragments
    _SPAN_BOND_GAP = float(ext_cfg.get('span_bond_gap_pts', 20.0))
    # Soft-coded: threshold below which OCR fallback is triggered (chars)
    _MIN_VECTOR    = int(ext_cfg.get('min_vector_chars_for_ocr_skip', 200))
    # Soft-coded: always append OCR results even when vector text is long enough
    _ALWAYS_OCR    = bool(ext_cfg.get('always_include_ocr', True))

    text_parts: list = []
    file_bytes = None

    try:
        import fitz
        file_bytes = file_obj.read()
        doc = fitz.open(stream=file_bytes, filetype='pdf')

        for page in doc:

            # ── Strategy 1: block text ──────────────────────────────────
            # TEXT_DEHYPHENATE excluded: structural hyphens in tags (V-308-TF)
            # must NOT be removed when they span a line boundary in the stream.
            blk_text = _normalize_text(page.get_text('text') or '')
            text_parts.append(blk_text)

            # ── Strategy 2: spatially-sorted word tokens ────────────────
            # get_text('words') → (x0,y0,x1,y1, word, block, line, word_no)
            words_raw = page.get_text('words')
            if words_raw:
                spatial = sorted(
                    words_raw,
                    key=lambda w: (round(w[1] / _Y_BUCKET_PTS) * _Y_BUCKET_PTS, w[0]),
                )
                spatial_text = _normalize_text(' '.join(w[4] for w in spatial))
                text_parts.append(spatial_text)

            # ── Strategy 3: span proximity bonding ─────────────────────
            # CAD tools (AutoCAD, SmartPlant, AVEVA) often write each word or
            # sub-token as an independent text span with a small positional gap.
            # get_text('words') treats a gap as a word boundary, so "V-308-TF"
            # may arrive as ["V-308", "-TF"] → joined with space → "V-308 -TF"
            # which breaks the regex.
            #
            # This pass iterates over character-level spans, sorts them by
            # (y-bucket, x) and bonds adjacent fragments whose right-edge to
            # next-left-edge gap is ≤ _SPAN_BOND_GAP pts, producing the
            # reconstructed token before adding a space.
            try:
                span_tokens: list = []  # (x0, reconstructed_text)
                raw_dict = page.get_text('rawdict')
                for blk in raw_dict.get('blocks', []):
                    for ln in blk.get('lines', []):
                        for sp in ln.get('spans', []):
                            txt = (sp.get('text') or '').strip()
                            if not txt:
                                continue
                            x0  = float(sp['bbox'][0])
                            y0  = float(sp['bbox'][1])
                            x1  = float(sp['bbox'][2])
                            row = round(y0 / _Y_BUCKET_PTS) * _Y_BUCKET_PTS
                            span_tokens.append((row, x0, x1, txt))

                span_tokens.sort(key=lambda t: (t[0], t[1]))

                bonded_parts: list = []
                buf = ''
                last_x1 = None
                last_row = None

                for row, x0, x1, txt in span_tokens:
                    if last_row is None:
                        buf = _normalize_text(txt)
                        last_x1 = x1
                        last_row = row
                    elif row == last_row and last_x1 is not None and (x0 - last_x1) <= _SPAN_BOND_GAP:
                        # Bond: adjacent on same row without visible gap
                        buf += _normalize_text(txt)
                        last_x1 = x1
                    else:
                        if buf:
                            bonded_parts.append(buf)
                        buf = _normalize_text(txt)
                        last_x1 = x1
                        last_row = row

                if buf:
                    bonded_parts.append(buf)

                if bonded_parts:
                    text_parts.append(' '.join(bonded_parts))
            except Exception as exc:
                logger.debug('[EquipmentList] Span bond pass failed: %s', exc)

        doc.close()
    except Exception as exc:
        logger.debug('[EquipmentList] PyMuPDF issue: %s', exc)

    full_text = '\n'.join(text_parts).strip()
    print(f'[EQ-DIAG] Vector text len={len(full_text)}  preview={repr(full_text[:200])}', flush=True)

    if _ALWAYS_OCR or len(full_text) < _MIN_VECTOR:
        # ── OCR fallback ─────────────────────────────────────────────────
        try:
            import fitz
            import pytesseract
            from PIL import Image, ImageEnhance, ImageFilter
            import io

            if file_bytes is None:
                file_obj.seek(0)
                file_bytes = file_obj.read()
            doc = fitz.open(stream=file_bytes, filetype='pdf')
            ocr_parts: list = []
            for page in doc:
                _r = page.rect
                _page_min_dim = min(abs(_r.width), abs(_r.height))
                _is_large = _page_min_dim > _LARGE_PAGE_THRESHOLD

                # Build list of scales to run for this page:
                # large-format pages run the primary large scale PLUS any
                # additional scales (e.g. 4.0 + 2.0) — different scales produce
                # different word-boundary decisions in Tesseract so pooling them
                # maximises tag coverage.
                if _is_large:
                    _scales_to_run = [_OCR_SCALE_LARGE] + _OCR_EXTRA_SCALES
                else:
                    _scales_to_run = [ocr_scale]

                seen_snippets: set = set()
                for _run_scale in _scales_to_run:
                    print(f'[EQ-DIAG] OCR page min_dim={_page_min_dim:.0f}  scale={_run_scale}', flush=True)
                    mat      = fitz.Matrix(_run_scale, _run_scale)
                    pix      = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
                    base_img = Image.open(io.BytesIO(pix.tobytes('png')))
                    _effective_dpi = int(72 * _run_scale)
                    base_img = ImageEnhance.Contrast(base_img).enhance(1.8)
                    base_img = base_img.filter(ImageFilter.SHARPEN)

                    for angle in ocr_angles:
                        rotated = base_img.rotate(-angle, expand=True) if angle != 0 else base_img
                        for psm in ocr_psm_modes:
                            ocr_text = pytesseract.image_to_string(
                                rotated, config=f'--oem 1 --psm {psm} --dpi {_effective_dpi}'
                            )
                            if not ocr_text.strip():
                                continue
                            fingerprint = ' '.join(ocr_text.split())[:200]
                            if fingerprint not in seen_snippets:
                                seen_snippets.add(fingerprint)
                                ocr_parts.append(_normalize_text(ocr_text))

                # ── Tile-based OCR pass for large-format pages ────────────────
                # Splits the page into a grid of tiles and OCRs each tile
                # independently.  Tesseract processes smaller, focused regions
                # more accurately than a single A0-scale image, so this pass
                # catches equipment tags in dense areas of the drawing that the
                # full-page pass misses.
                if _is_large and _TILE_ROWS > 0 and _TILE_COLS > 0:
                    print(f'[EQ-DIAG] Tiling {_TILE_ROWS}x{_TILE_COLS} scale={_TILE_SCALE} psm={_TILE_PSM}', flush=True)
                    _tile_dpi = int(72 * _TILE_SCALE)
                    _tmat  = fitz.Matrix(_TILE_SCALE, _TILE_SCALE)
                    _tpix  = page.get_pixmap(matrix=_tmat, colorspace=fitz.csGRAY)
                    _tfull = Image.open(io.BytesIO(_tpix.tobytes('png')))
                    _tfull = ImageEnhance.Contrast(_tfull).enhance(2.0)
                    _tfull = _tfull.filter(ImageFilter.SHARPEN)
                    _tw, _th = _tfull.size
                    for _ri in range(_TILE_ROWS):
                        for _ci in range(_TILE_COLS):
                            _x0 = max(0, int(_ci * _tw / _TILE_COLS - _tw * _TILE_OVERLAP / 2))
                            _y0 = max(0, int(_ri * _th / _TILE_ROWS - _th * _TILE_OVERLAP / 2))
                            _x1 = min(_tw, int((_ci + 1) * _tw / _TILE_COLS + _tw * _TILE_OVERLAP / 2))
                            _y1 = min(_th, int((_ri + 1) * _th / _TILE_ROWS + _th * _TILE_OVERLAP / 2))
                            _tile = _tfull.crop((_x0, _y0, _x1, _y1))
                            _tile_text = pytesseract.image_to_string(
                                _tile, config=f'--oem 1 --psm {_TILE_PSM} --dpi {_tile_dpi}'
                            )
                            if not _tile_text.strip():
                                continue
                            _fp = ' '.join(_tile_text.split())[:200]
                            if _fp not in seen_snippets:
                                seen_snippets.add(_fp)
                                ocr_parts.append(_normalize_text(_tile_text))
            doc.close()
            ocr_combined = '\n'.join(ocr_parts)
            print(f'[EQ-DIAG] OCR text len={len(ocr_combined)}  preview={repr(ocr_combined[:200])}', flush=True)
            # When always_include_ocr=true, APPEND to existing vector text.
            # When it's a pure OCR fallback (vector text was too short), REPLACE.
            if _ALWAYS_OCR and full_text:
                full_text = full_text + '\n' + ocr_combined
            else:
                full_text = ocr_combined
        except Exception as exc:
            logger.debug('[EquipmentList] Tesseract fallback issue: %s', exc)
            print(f'[EQ-DIAG] Tesseract fallback error: {exc}', flush=True)

    return full_text




# ---------------------------------------------------------------------------
# Equipment Register (18-field tabular document) extraction
# All thresholds / field-header variants are in equipment_type_config.json
# ---------------------------------------------------------------------------

_PAGE_Y_OFFSET      = 50000   # Vertical offset per PDF page so rows stay distinct
_Y_CLUSTER_TOL     = 12      # px — words within this y-distance are on the same row (vector PDF)
_Y_CLUSTER_TOL_OCR = 22      # px — wider tolerance for OCR; coords can drift more on scanned pages


def _cluster_words_into_rows(word_triples: list, y_tol: int = _Y_CLUSTER_TOL) -> list:
    """
    word_triples: list of (text, x, y) — may span multiple pages.
    Returns sorted list-of-rows, each row = [(text, x, y), ...] sorted by x.
    """
    if not word_triples:
        return []

    word_triples = sorted(word_triples, key=lambda w: (round(w[2] / _PAGE_Y_OFFSET), w[2]))
    rows: list = []
    current: list = [word_triples[0]]
    row_y = word_triples[0][2]

    for item in word_triples[1:]:
        # Treat items on different pages as always new rows
        same_page = abs(item[2] - row_y) < _PAGE_Y_OFFSET // 2
        if same_page and abs(item[2] - row_y) <= y_tol:
            current.append(item)
        else:
            rows.append(sorted(current, key=lambda w: w[1]))
            current = [item]
            row_y = item[2]

    if current:
        rows.append(sorted(current, key=lambda w: w[1]))
    return rows


def _extract_words_with_coords(file_obj, config: dict) -> tuple:
    """
    Returns (word_triples, used_ocr).
    word_triples: [(text, x, y), ...] from the PDF.
    Tries vector (PyMuPDF) first; falls back to pytesseract image_to_data.
    """
    cfg       = config.get('extraction', {})
    ocr_scale = float(cfg.get('ocr_render_scale', 3.0))
    word_list: list = []

    try:
        import fitz
        file_bytes = file_obj.read()
        doc = fitz.open(stream=file_bytes, filetype='pdf')
        for page_num, page in enumerate(doc):
            for entry in page.get_text('words'):
                x0, y0, x1, y1, word = entry[0], entry[1], entry[2], entry[3], entry[4]
                w = word.strip()
                if w:
                    word_list.append((w, x0, y0 + page_num * _PAGE_Y_OFFSET))
        doc.close()
    except Exception as exc:
        logger.debug('[EquipRegister] PyMuPDF words error: %s', exc)

    if len(word_list) > 30:
        return word_list, False  # vector PDF — use directly

    # ------- OCR fallback -------
    try:
        import fitz, pytesseract, io
        from PIL import Image, ImageEnhance, ImageFilter

        file_obj.seek(0)
        file_bytes = file_obj.read()
        doc = fitz.open(stream=file_bytes, filetype='pdf')
        ocr_words: list = []

        for page_num, page in enumerate(doc):
            mat = fitz.Matrix(ocr_scale, ocr_scale)
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
            img = Image.open(io.BytesIO(pix.tobytes('png')))
            img = ImageEnhance.Contrast(img).enhance(2.0)
            img = img.filter(ImageFilter.SHARPEN)

            # Try 0° first; also 90° for landscape CAD title blocks
            for angle in cfg.get('ocr_rotation_angles', [0, 90, 180, 270])[:2]:
                rotated = img.rotate(-angle, expand=True) if angle else img
                try:
                    data = pytesseract.image_to_data(
                        rotated,
                        config='--oem 1 --psm 6',
                        output_type=pytesseract.Output.DICT,
                    )
                    for i, word in enumerate(data['text']):
                        w = str(word).strip()
                        raw_conf = data['conf'][i]
                        conf = int(raw_conf) if str(raw_conf).lstrip('-').isdigit() else 0
                        if w and conf > 20:
                            x = float(data['left'][i]) / ocr_scale
                            y = float(data['top'][i]) / ocr_scale + page_num * _PAGE_Y_OFFSET
                            ocr_words.append((w, x, y))
                except Exception:
                    continue

        doc.close()
        return ocr_words, True

    except Exception as exc:
        logger.debug('[EquipRegister] OCR fallback error: %s', exc)
        return [], True


def _norm_header(text: str) -> str:
    """Normalise a column header for fuzzy matching: uppercase, collapse punctuation/spaces.

    Strips & so 'P&ID' and 'P & ID' and 'P ID' all normalise to the same 'P ID' form,
    which means variants only need to cover the stripped form once.
    """
    s = text.upper()
    s = re.sub(r'[.\-/()\[\]&,]', ' ', s)   # & added: P&ID → P ID, A&E → A E
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _find_header_range(rows: list, field_variants: dict, min_cols: int) -> tuple:
    """
    Scan first 50 rows for the table header row(s).
    Supports single-row and double-row headers (common in CAD documents).
    Returns (start_idx, end_idx_exclusive) or None if not found.
    """
    scan_limit = min(50, len(rows))
    best_score = 0
    best_range: tuple = (0, 1)

    all_variants_norm = {
        k: [_norm_header(v) for v in variants]
        for k, variants in field_variants.items()
    }

    for start in range(scan_limit):
        for span in (1, 2):
            end = min(start + span, len(rows))
            combined_norm = _norm_header(
                ' '.join(t for row in rows[start:end] for (t, x, y) in row)
            )
            score = sum(
                1 for variants_norm in all_variants_norm.values()
                if any(v in combined_norm for v in variants_norm)
            )
            if score > best_score:
                best_score = score
                best_range = (start, end)

    if best_score < min_cols:
        return None
    return best_range


def _build_col_map(header_rows: list, field_variants: dict) -> dict:
    """
    Build mapping: field_key -> x_center from the header row(s).

    Handles multi-line CAD table headers by:
    1. Sorting all header words by (x, y) so same-column words are adjacent.
    2. Grouping into x-column clusters.
    3. Trying left-neighbor merges for short unmatched clusters (handles
       "Des./Set Press. Min" where Min lands in its own cluster).
    4. Greedy conflict resolution to avoid two clusters claiming the same field.
    """
    all_words_y = [(t.strip(), float(x), float(y))
                   for row in header_rows for (t, x, y) in row if t.strip()]
    if not all_words_y:
        return {}

    all_variants_norm = {
        k: [_norm_header(v) for v in variants]
        for k, variants in field_variants.items()
    }

    # ── Sort by x, then y ────────────────────────────────────────────────────
    sorted_by_x = sorted(all_words_y, key=lambda w: (w[1], w[2]))

    # ── Adaptive x-cluster tolerance ─────────────────────────────────────────
    distinct_xs = sorted(set(round(v[1]) for v in sorted_by_x))
    if len(distinct_xs) > 1:
        gaps = [distinct_xs[i + 1] - distinct_xs[i] for i in range(len(distinct_xs) - 1)]
        median_gap = sorted(gaps)[len(gaps) // 2]
        x_col_tol = max(median_gap * 0.8, 8.0)
    else:
        x_col_tol = 15.0

    # ── Form initial clusters ─────────────────────────────────────────────────
    col_clusters: list = []
    current: list = [sorted_by_x[0]]
    for we in sorted_by_x[1:]:
        cm = sum(w[1] for w in current) / len(current)
        if abs(we[1] - cm) <= x_col_tol:
            current.append(we)
        else:
            col_clusters.append(current)
            current = [we]
    if current:
        col_clusters.append(current)

    # ── Helper: build phrase + x-center from a cluster list ──────────────────
    def _cluster_info(cluster: list) -> tuple:
        ro = sorted(cluster, key=lambda w: (w[2], w[1]))
        phrase = _norm_header(' '.join(w[0] for w in ro))
        x_c = sum(w[1] for w in cluster) / len(cluster)
        return phrase, x_c

    # ── Helper: score a phrase against a single field ─────────────────────────
    def _score(phrase: str, field_key: str) -> int:
        best = 0
        for variant in all_variants_norm.get(field_key, []):
            if phrase == variant:
                s = len(variant) * 2
            elif len(variant) >= 3 and variant in phrase:
                s = len(variant)
            elif len(phrase) >= 3 and len(variant) >= 5 and phrase in variant:
                s = len(phrase)
            else:
                continue
            if s > best:
                best = s
        return best

    # ── Step 4: Build (score, x_center, field_key) candidates ────────────────
    # Each cluster produces ALL matching fields (not just best), then we do
    # greedy conflict-free assignment.  Also try LEFT-MERGE for short clusters
    # (catches "Min"/"Max" separated from their prefix by a gap).
    all_matches: list = []  # (score, x_center, field_key)

    for ci, cluster in enumerate(col_clusters):
        phrase, x_c = _cluster_info(cluster)

        # Also try merging with left neighbor (helps "Des./Set Press." + "Min")
        if ci > 0 and len(cluster) <= 2:
            merged = col_clusters[ci - 1] + cluster
            merged_phrase, merged_xc = _cluster_info(merged)
        else:
            merged_phrase, merged_xc = None, None

        for field_key in all_variants_norm:
            s = _score(phrase, field_key)
            use_x = x_c   # always use a local copy — do NOT mutate x_c
            # Prefer merged phrase only if it yields a strictly better score
            if merged_phrase is not None:
                ms = _score(merged_phrase, field_key)
                if ms > s:
                    s, use_x = ms, merged_xc
            if s > 0:
                all_matches.append((s, use_x, field_key))

    # ── Step 5: Greedy conflict-free assignment ───────────────────────────────
    # Sort by score desc, then by x (stable ordering for equal scores).
    all_matches.sort(key=lambda m: (-m[0], m[1]))

    field_best: dict = {}  # field_key -> (score, x_center)
    # Track which physical x-centers have already been "used" (±5pt tolerance)
    used_x: list = []

    for score, x_center, field_key in all_matches:
        if field_key in field_best:
            continue  # field already claimed
        # Check whether a different field already claimed this x_center
        already_used = any(abs(x_center - ux) < 5.0 for ux in used_x)
        if already_used:
            continue
        field_best[field_key] = (score, x_center)
        used_x.append(x_center)

    return {k: v[1] for k, v in field_best.items()}


def _assign_row_to_cols(data_row: list, col_map: dict) -> dict:
    """
    Assign each word in data_row to the nearest column by x-distance.
    Returns dict field_key -> value_string.
    """
    if not data_row or not col_map:
        return {}

    sorted_cols = sorted(col_map.items(), key=lambda c: c[1])   # (key, x)
    n_cols = len(sorted_cols)

    # Midpoints between adjacent columns
    boundaries = [
        (sorted_cols[i][1] + sorted_cols[i + 1][1]) / 2
        for i in range(n_cols - 1)
    ]

    buckets: dict = {k: [] for k in col_map}
    for (text, x, _y) in data_row:
        col_idx = 0
        for bi, bx in enumerate(boundaries):
            if x > bx:
                col_idx = bi + 1
            else:
                break
        assigned = sorted_cols[col_idx][0]
        buckets[assigned].append((x, text))

    return {
        k: ' '.join(txt for _, txt in sorted(items)).strip()
        for k, items in buckets.items()
        if items
    }


def _pid_item_to_register_schema(pid_item: dict) -> dict:
    """Map a P&ID-extraction item to the 18-field register schema."""
    return {
        'sl_no':               str(pid_item.get('sl_no', '')),
        'revision':            '',
        'tag':                 pid_item.get('tag', ''),
        'description':         pid_item.get('description', ''),
        'design_flowrate':     '',
        'oper_pressure':       '',
        'oper_temperature':    '',
        'design_pressure_min': '',
        'design_pressure_max': '',
        'design_temp_min':     '',
        'design_temp_max':     '',
        'moc':                 pid_item.get('material_class', ''),
        'insulation':          '',
        'dimension_length':    '',
        'dimension_diameter':  '',
        'motor_rating':        '',
        'pid_no':              pid_item.get('drawing_ref', ''),
        'quality_required':    '',
        'phase':               pid_item.get('service_fluid', ''),
        'remarks':             pid_item.get('process_notes', ''),
        # Backward-compat fields kept for status/results endpoints
        'type_label':         pid_item.get('type_label', ''),
        'area':               pid_item.get('area', ''),
        'drawing_ref':        pid_item.get('drawing_ref', ''),
        'line_connections':   pid_item.get('line_connections', []),
        'nozzle_connections': pid_item.get('nozzle_connections', []),
        'service_fluid':      pid_item.get('service_fluid', ''),
        'material_class':     pid_item.get('material_class', ''),
        'process_notes':      pid_item.get('process_notes', ''),
    }


def _extract_equipment_register_rows(file_obj, config: dict):
    """
    Extract 18-field Equipment Register from a tabular CAD/PDF document.

    Uses coordinate-based table detection (PyMuPDF words + pytesseract
    image_to_data as fallback) so it works on both vector and scanned PDFs.

    Returns list of equipment dicts if the document is a register table,
    or None if the document doesn't look like a register (triggers P&ID fallback).
    """
    field_variants = config.get('equip_register_fields', {})
    min_cols       = int(config.get('equip_register_min_columns', 4))
    min_rows       = int(config.get('equip_register_min_rows', 2))
    # Soft-coded: shortest page dimension (pts) above which we treat the doc
    # as a large-format P&ID drawing and skip register detection entirely.
    # A4 landscape smallest dim = 595 pts; A3 = 842 pts; A1/A0 >> 1000 pts.
    # Equipment registers are A4; P&IDs are A1-A0. Threshold = 900 pts.
    max_drawing_min_dim = int(config.get('equip_register_skip_if_page_dim_gt', 900))

    if not field_variants:
        return None  # Config missing — skip register mode

    # ── Page-size guard ──────────────────────────────────────────────────────
    # Large-format drawings (A1/A0 P&IDs) can accidentally match headers from
    # equipment data boxes (DIAMETER, LENGTH, OPERATING PRESS, etc.).
    # Skip register mode when the smallest page dimension exceeds the threshold.
    try:
        import fitz as _fitz
        _fb = file_obj.read()
        _doc = _fitz.open(stream=_fb, filetype='pdf')
        if _doc.page_count > 0:
            _r = _doc[0].rect
            _min_dim = min(abs(_r.width), abs(_r.height))
            print(f'[EQ-DIAG][Register] page_min_dim={_min_dim:.0f}pts  threshold={max_drawing_min_dim}', flush=True)
            if _min_dim > max_drawing_min_dim:
                _doc.close()
                print('[EQ-DIAG][Register] Large-format drawing -> skipping register mode', flush=True)
                return None
        _doc.close()
        file_obj.seek(0)
    except Exception as _exc:
        logger.debug('[EquipRegister] Page-size check failed: %s', _exc)
        try:
            file_obj.seek(0)
        except Exception:
            pass

    logger.info('[EquipRegister] Starting coordinate-based table extraction')

    word_list, used_ocr = _extract_words_with_coords(file_obj, config)
    if not word_list:
        logger.info('[EquipRegister] No words extracted')
        return None

    # Use wider y-tolerance for OCR pages — coordinates are less precise
    y_tol = _Y_CLUSTER_TOL_OCR if used_ocr else _Y_CLUSTER_TOL
    rows = _cluster_words_into_rows(word_list, y_tol=y_tol)
    if len(rows) < 3:
        logger.info('[EquipRegister] Too few rows (%d)', len(rows))
        return None

    header_range = _find_header_range(rows, field_variants, min_cols)
    if header_range is None:
        logger.info('[EquipRegister] No register header detected')
        return None

    h_start, h_end = header_range
    col_map = _build_col_map(rows[h_start:h_end], field_variants)
    if len(col_map) < min_cols:
        logger.info('[EquipRegister] Too few columns mapped (%d)', len(col_map))
        return None

    logger.info('[EquipRegister] Columns detected: %s', list(col_map.keys()))

    # All variants for repeated-header detection
    all_variants_norm = {
        k: [_norm_header(v) for v in variants]
        for k, variants in field_variants.items()
    }

    equipment: list = []
    row_counter = 0

    for row in rows[h_end:]:
        row_text = ' '.join(t for (t, x, y) in row).strip()
        if not row_text or len(row_text) < 2:
            continue

        # Skip repeated header rows (some CAD drawings repeat headers each page)
        combined_norm = _norm_header(row_text)
        hdr_score = sum(
            1 for v_list in all_variants_norm.values()
            if any(v in combined_norm for v in v_list)
        )
        if hdr_score >= min_cols:
            continue

        values = _assign_row_to_cols(row, col_map)
        tag_val = values.get('tag', '').strip()
        sl_val  = values.get('sl_no', '').strip()

        # Count non-empty fields; skip blank/near-blank rows regardless of tag presence.
        # A row that has ≥ 2 mapped fields is kept even if neither tag nor sl_no are
        # populated — this handles: (a) registers where tag column wasn't matched,
        # (b) multi-line continuation rows caught here before the post-merge pass.
        populated = sum(1 for v in values.values() if v.strip())
        if populated < 2:
            continue

        row_counter += 1
        item: dict = {
            'sl_no':               sl_val or str(row_counter),
            'revision':            values.get('revision', ''),
            'tag':                 tag_val or f'ITEM-{row_counter:03d}',
            'description':         values.get('description', ''),
            'design_flowrate':     values.get('design_flowrate', ''),
            'oper_pressure':       values.get('oper_pressure', ''),
            'oper_temperature':    values.get('oper_temperature', ''),
            'design_pressure_min': values.get('design_pressure_min', ''),
            'design_pressure_max': values.get('design_pressure_max', ''),
            'design_temp_min':     values.get('design_temp_min', ''),
            'design_temp_max':     values.get('design_temp_max', ''),
            'moc':                 values.get('moc', ''),
            'insulation':          values.get('insulation', ''),
            'dimension_length':    values.get('dimension_length', ''),
            'dimension_diameter':  values.get('dimension_diameter', ''),
            'motor_rating':        values.get('motor_rating', ''),
            'pid_no':              values.get('pid_no', ''),
            'quality_required':    values.get('quality_required', ''),
            'phase':               values.get('phase', ''),
            'remarks':             values.get('remarks', ''),
            # Backward-compat fields
            'type_label':         '',
            'area':               '',
            'drawing_ref':        '',
            'line_connections':   [],
            'nozzle_connections': [],
            'service_fluid':      values.get('oper_pressure', ''),
            'material_class':     values.get('moc', ''),
            'process_notes':      values.get('remarks', ''),
        }
        equipment.append(item)

    # Post-merge: combine continuation rows (wrapped cells) into the preceding item.
    # A row is a continuation candidate if both tag and sl_no are empty and it has
    # ≤ 3 mapped fields — typically the second line of a wrapped description cell.
    if equipment:
        merged_equip: list = [equipment[0]]
        for item in equipment[1:]:
            is_cont = (
                not item.get('tag') and not item.get('sl_no')
                and sum(1 for v in item.values() if isinstance(v, str) and v.strip()) <= 3
            )
            if is_cont and merged_equip:
                prev = merged_equip[-1]
                for fld in ('description', 'remarks', 'moc', 'insulation'):
                    if item.get(fld):
                        prev[fld] = (prev.get(fld, '') + ' ' + item[fld]).strip()
            else:
                merged_equip.append(item)
        equipment = merged_equip

    # Confirm enough populated rows to treat this as a real register
    well_populated = sum(
        1 for item in equipment
        if sum(
            1 for k, v in item.items()
            if k not in ('sl_no', 'tag', 'type_label', 'area', 'drawing_ref',
                         'line_connections', 'nozzle_connections')
            and (v if not isinstance(v, list) else v)
        ) >= 3   # lowered from 5 — sparse registers (tag + desc + one pressure) should qualify
    )

    if len(equipment) < min_rows or well_populated < min_rows:
        logger.info('[EquipRegister] Insufficient populated rows (total=%d, well_pop=%d)',
                    len(equipment), well_populated)
        return None

    logger.info('[EquipRegister] Extracted %d register rows (OCR=%s)', len(equipment), used_ocr)
    return equipment


# ---------------------------------------------------------------------------


def _extract_equipment_items(text: str, drawing_ref: str, config: dict) -> list:
    """
    Parse extracted PDF text and build a list of equipment dicts.

    All field extraction patterns are soft-coded in equipment_type_config.json.
    Add / adjust patterns there without touching this function.

    Fields returned per item
    ------------------------
    tag, type_label, description, area, drawing_ref,
    line_connections, nozzle_connections, service_fluid,
    material_class, process_notes
    """
    ext_cfg     = config.get('extraction', {})
    type_labels = config.get('type_labels', {})
    fluid_kws   = [kw for kw in config.get('fluid_keywords', []) if not kw.startswith('_')]
    ctx_win                 = int(ext_cfg.get('context_window_chars', 160))
    desc_words              = int(ext_cfg.get('description_max_words', 6))
    desc_ctx_chars          = int(ext_cfg.get('description_context_chars', 400))
    desc_min_len            = int(ext_cfg.get('description_min_word_length', 3))
    area_ctx_chars          = int(ext_cfg.get('area_context_chars', 600))
    area_from_tag_heuristic = bool(ext_cfg.get('area_from_tag_heuristic', True))
    nozzle_ctx_chars        = int(ext_cfg.get('nozzle_context_chars', 400))
    mat_ctx_chars           = int(ext_cfg.get('material_context_chars', 400))
    service_ctx_chars       = int(ext_cfg.get('service_context_chars', 400))
    note_ctx_chars          = int(ext_cfg.get('note_context_chars', 400))
    # Standards refs, conjunctions and short noise tokens to exclude from description
    _desc_stop_words        = {
        'API','ASME','ANSI','ISO','DIN','NACE','NOTE','REF','SEE','PER',
        'AND','FOR','THE','OR','TO','OF','IN','AT','BY','NO','AS','IS','ON',
    }

    # Soft-coded via tag_pattern in equipment_type_config.json.
    # The optional (?:-[A-Z0-9]{1,4})? captures project train/unit suffixes such
    # as -TF, -1F, -2A that are common in O&G tag numbering (e.g. V-308-TF,
    # V-805-1F).  Without this suffix, duplicate-deduplication collapses
    # equipment with the same base number but different trains into one row.
    _tag_pat_default = r'\b([A-Z]{1,2})-([0-9]{3,5}[A-Z]?(?:-[A-Z0-9]{1,4})?)\b'
    tag_re = re.compile(ext_cfg.get('tag_pattern', _tag_pat_default))

    # --- Soft-coded helper patterns (read once per call) ------------------
    # Used by description strategy 1: identify bare tag lines and pure-noise tokens.
    # Must also match the extended suffix form so lines like "V-308-TF" are
    # not misidentified as description text.
    _tag_like_re  = re.compile(r'^[A-Z]{1,2}-\d{3,5}[A-Z]?(?:-[A-Z0-9]{1,4})?$')
    _noise_tok_re = re.compile(r'^[\d\.\+\-\/\%\(\)\[\]]{1,6}$')
    area_re    = re.compile(
        ext_cfg.get('area_pattern',
                    r'(?:AREA|UNIT|TRAIN|BAY|SECTION|BATTERY|MODULE|MOD|ZONE|BLOCK|SKID|PLANT|FIELD|STREAM)\s*[:\-]?\s*([A-Z0-9]{1,8})'),
        re.IGNORECASE,
    )
    nozzle_re         = re.compile(
        ext_cfg.get('nozzle_pattern', r'\bN[-]?[0-9]{1,2}[A-Z]?\b')
    )
    mat_re            = re.compile(
        ext_cfg.get('material_class_pattern',
                    r'\b(A1[A-Z]R?|B1[A-Z]|C1[A-Z]|D1[A-Z]|[A-D]2[A-Z]'
                    r'|CS|SS|316L?|304L?|317L|321|347|2205|254SMO'
                    r'|DSS|SDSS|DUPLEX|INCONEL|HASTELLOY|MONEL'
                    r'|GRE|FRP|HDPE|CPVC|PVC|PVDF|A516|A240|A312|A106)\b'),
        re.IGNORECASE,
    )
    material_label_re = re.compile(
        ext_cfg.get('material_label_pattern',
                    r'(?:MATERIAL|MTL|SHELL|BODY|CASING|LINER'
                    r'|WETTED\s*PARTS?|INTERNALS?)'
                    r'\s*[:\-/]\s*([A-Z0-9][A-Z0-9/\-\s\.]{1,28})'),
        re.IGNORECASE,
    )
    service_label_re  = re.compile(
        ext_cfg.get('service_label_pattern',
                    r'(?:SERVICE|FLUID|MEDIUM|PROCESS\s*FLUID'
                    r'|CONTENTS|PRODUCT|DUTY)'
                    r'\s*[:\.\.\-]\s*([A-Za-z][A-Za-z0-9\s/\-]{1,30})'),
        re.IGNORECASE,
    )
    note_re           = re.compile(
        ext_cfg.get('note_pattern',
                    r'(?:(?:SEE\s+)?NOTE\s*[-\s\(]?[0-9]+[\)\.]*'
                    r'|\bHOLD\b(?:\s*[-]?\s*[0-9]+)?'
                    r'|\bTBD\b|\bTBC\b'
                    r'|\bREF[.\s]+DWG[.\s]+[A-Z0-9/\-]+'
                    r'|SEE\s+(?:DWG|SPEC|DOC)[.]*\s*[A-Z0-9/\-]+)'),
        re.IGNORECASE,
    )
    # -----------------------------------------------------------------------

    instr_valve_prefixes = {
        'FT','FI','FIC','FC','PT','PI','PIC','PC','LT','LI','LIC','LC',
        'TT','TI','TIC','TC','AT','AI','FY','PY','LY','TY',
        'HV','FV','XV','PV','SDV','BDV','PSV','PRV','CV','LV','TV',
        'FE','TE','LE','PE','HS','HIC','HI',
    }

    # Soft-coded: exact tag suffix values that identify non-equipment tokens
    # (e.g. project change-request numbers like PJ-2025-CR-002 which OCR
    # garbles into P-2028-CR — "CR" is a document type, not an equipment suffix).
    _exclude_suffixes = {s.upper() for s in ext_cfg.get('exclude_tag_suffixes', ['CR', 'NCR', 'WO', 'TQ', 'MDR', 'MOM', 'MR'])}

    seen = set()
    results = []

    # ── Slash-variant tag expansion ────────────────────────────────────────
    # OCR on P&IDs sometimes reads multi-unit tags like "P-851A/B/C-TF" as a
    # single token. Expand these into individual variants (P-851A-TF,
    # P-851B-TF, P-851C-TF) and append them to the text so the main loop
    # finds each unit independently.
    _slash_re      = re.compile(
        r'\b([A-Z]{1,2}-\d{3,5})([A-Z])/([A-Z])(?:/([A-Z]))?(?:-([A-Z0-9]{1,4}))?\b'
    )
    _slash_expanded: list[str] = []
    for _sm in _slash_re.finditer(text):
        _base = _sm.group(1)
        _sfx  = _sm.group(5) or ''
        for _v in [_sm.group(2), _sm.group(3)] + ([_sm.group(4)] if _sm.group(4) else []):
            _slash_expanded.append(f'{_base}{_v}' + (f'-{_sfx}' if _sfx else ''))
    if _slash_expanded:
        text = text + '\n' + '\n'.join(_slash_expanded)
        print(f'[EQ-DIAG] Slash expansion added: {_slash_expanded}', flush=True)

    for m in tag_re.finditer(text):
        prefix = m.group(1).upper()
        tag    = m.group(0)

        if prefix in instr_valve_prefixes:
            continue
        if type_labels and prefix not in type_labels:
            continue
        # Filter non-equipment project-reference suffixes
        _tag_suffix_m = re.search(r'-([A-Z]{1,4})$', tag)
        if _tag_suffix_m and _tag_suffix_m.group(1) in _exclude_suffixes:
            continue
        if tag in seen:
            continue
        seen.add(tag)

        start = max(0, m.start() - ctx_win)
        end   = min(len(text), m.end() + ctx_win)
        ctx   = text[start:end]

        type_label = type_labels.get(prefix, 'Equipment')

        # ── Description — multi-strategy extraction ───────────────────────
        after       = text[m.end(): m.end() + desc_ctx_chars]
        description = ''

        # Strategy 1: newline-segmented lines right after the tag.
        # Each line is checked for "description-likeness":
        # skip bare tag IDs, pipe designations and pure digit/symbol noise.
        desc_lines = []
        for _ln in (ln.strip() for ln in after.split('\n') if ln.strip()):
            if _tag_like_re.match(_ln):
                continue
            _toks = [t.strip('.,;:/()"\'[]') for t in _ln.split()]
            _valid = [
                t for t in _toks
                if len(t) >= desc_min_len
                and not t.isdigit()
                and not _tag_like_re.match(t)
                and not _noise_tok_re.match(t)
                and t.upper() not in _desc_stop_words
            ]
            if _valid:
                desc_lines.append(' '.join(_valid[:5]))
            if len(desc_lines) >= 2:
                break
        if desc_lines:
            description = ' '.join(desc_lines).title()

        # Strategy 2: ALL-CAPS word scan in narrower ctx_win (improved filter)
        if not description:
            _cap_words = re.findall(r'\b[A-Z][A-Z]{2,19}\b', after[:ctx_win])
            _filtered_caps = [
                w for w in _cap_words
                if not re.match(r'^[A-Z]{1,2}-\d', w)
                and w not in _desc_stop_words
                and len(w) >= desc_min_len
            ][:desc_words]
            if _filtered_caps:
                description = ' '.join(w.capitalize() for w in _filtered_caps[:3])

        # Strategy 3: fall back to the equipment TypeLabel
        if not description:
            description = type_label

        # ── Line connections (piping designation tokens) ───────────────────
        lc_tokens = []
        for lm in _LINE_TAG_RE.finditer(ctx):
            token = lm.group(0).strip()
            if token and token not in lc_tokens:
                lc_tokens.append(token)

        # ── Service / fluid — multi-strategy extraction ───────────────────
        _svc_start    = max(0, m.start() - service_ctx_chars)
        _svc_end      = min(len(text), m.end() + service_ctx_chars)
        _svc_ctx      = text[_svc_start:_svc_end]
        service_fluid = ''
        # Strategy 1: label-based — SERVICE: CRUDE OIL, FLUID: NITROGEN, MEDIUM: GAS
        _svc_lm = service_label_re.search(_svc_ctx)
        if _svc_lm:
            _raw_svc = _svc_lm.group(1).split('\n')[0].strip().rstrip('.,;')
            if len(_raw_svc) >= 2:
                service_fluid = _raw_svc[:35].title()
        # Strategy 2: keyword scan in wider context
        if not service_fluid:
            _svc_lower = _svc_ctx.lower()
            found_fluids = [kw for kw in fluid_kws if kw in _svc_lower]
            service_fluid = ', '.join(found_fluids[:2]).title() if found_fluids else ''
        # Strategy 3: derive from fluid code embedded in already-found line connection tags.
        # e.g. "4"-HO-5665-033842-X" → fluid code "HO" → "Hydrocarbon Oil".
        # Guaranteed to find something whenever line connections were extracted.
        if not service_fluid and lc_tokens:
            _lf_map = {k: v for k, v in config.get('line_fluid_code_map', {}).items()
                       if not str(k).startswith('_')}
            for _lc in lc_tokens:
                _fc_m = re.match(r'^[\d½¾¼]+\s*["\'?]\s*[-_]\s*([A-Z]{1,4})\s*[-_]', _lc)
                if _fc_m:
                    _fc = _fc_m.group(1).upper()
                    _mapped = _lf_map.get(_fc)
                    if _mapped:
                        service_fluid = _mapped
                        break

        # ── Area / Unit — multi-strategy extraction ───────────────────────
        # Strategy 1: search a wider context (soft-coded area_context_chars).
        # Uses capture group(1) — returns just the code, not the whole keyword match.
        _a_start = max(0, m.start() - area_ctx_chars)
        _a_end   = min(len(text), m.end() + area_ctx_chars)
        area_m   = area_re.search(text[_a_start:_a_end])
        area     = area_m.group(1).strip() if area_m else ''

        # Strategy 2: derive from serial number digits (O&G tag-number convention).
        # V-101 → "100", P-2201 → "2200", E-10001 → "10000"
        if not area and area_from_tag_heuristic:
            _digits = re.sub(r'[^0-9]', '', m.group(2))
            if len(_digits) >= 3:
                area = _digits[0] + '0' * (len(_digits) - 1)

        # ── Nozzle connections — multi-strategy extraction ────────────────
        _nzl_start    = max(0, m.start() - nozzle_ctx_chars)
        _nzl_end      = min(len(text), m.end() + nozzle_ctx_chars)
        _nzl_ctx      = text[_nzl_start:_nzl_end]
        # Strategy 1: N1 / N-1 / N2A nozzle tag pattern
        nozzle_tokens = list(dict.fromkeys(nozzle_re.findall(_nzl_ctx)))
        # Strategy 2: size-prefixed nozzle labels  e.g. 4"-N1, 6"-N2A
        for _snm in re.finditer(
            r'\b\d{1,3}\s*["\']\s*-?\s*(N[-]?[0-9]{1,2}[A-Z]?)\b',
            _nzl_ctx, re.IGNORECASE
        ):
            _tok = _snm.group(1).upper()
            if _tok not in nozzle_tokens:
                nozzle_tokens.append(_tok)
        # Strategy 3: functional orientation labels as fallback when no N-tags found
        # e.g. INLET, OUTLET, SUCTION, DISCHARGE on equipment bubbles
        if not nozzle_tokens:
            _orient_hits = re.findall(
                r'\b(INLET|OUTLET|SUCT(?:ION)?|DISCH(?:ARGE)?|VENT|DRAIN|BYPASS'
                r'|OVERFLOW|RECYCLE|RETURN|FEED|PRODUCT|OVERHEAD|BOTTOM(?:S)?)\b',
                _nzl_ctx, re.IGNORECASE,
            )
            for _ow in dict.fromkeys(w.capitalize() for w in _orient_hits):
                nozzle_tokens.append(_ow)
        nozzle_tokens = nozzle_tokens[:8]

        # ── Material / piping spec — multi-strategy extraction ────────────
        _mat_start     = max(0, m.start() - mat_ctx_chars)
        _mat_end       = min(len(text), m.end() + mat_ctx_chars)
        _mat_ctx       = text[_mat_start:_mat_end]
        material_class = ''
        # Strategy 1: label-based — MATERIAL: CS/SS316, SHELL: DSS, MTL: INCONEL
        _mat_lm = material_label_re.search(_mat_ctx)
        if _mat_lm:
            _raw_mat = _mat_lm.group(1).split('\n')[0].strip().rstrip('.,;/ ')
            if len(_raw_mat) >= 2:
                material_class = _raw_mat[:25].upper()
        # Strategy 2: pattern scan in wider context
        if not material_class:
            mat_matches    = mat_re.findall(_mat_ctx)
            material_class = mat_matches[0].upper() if mat_matches else ''
        # Strategy 3: derive material hint from pipe-class prefix in line connection tags.
        # Line tag format: SIZE"-FLUID-SEQ-PIPECLASS[-SUFFIX]
        # First 2 digits of the pipe-class code encode material in most spec books
        # (soft-coded in pipe_class_prefix_map — add project-specific mappings there).
        if not material_class and lc_tokens:
            _pc_prefix_map = {k: v for k, v in config.get('pipe_class_prefix_map', {}).items()
                              if not str(k).startswith('_')}
            for _lc in lc_tokens:
                # Pipe class is typically 5-8 digits separated by '-' or '_'
                _pc_m = re.search(r'[\-_](\d{4,8})(?:[\-_][A-Z0-9]{0,5})?(?:\s|$)', _lc)
                if _pc_m:
                    _prefix = _pc_m.group(1)[:2]
                    _mat = _pc_prefix_map.get(_prefix)
                    if _mat:
                        material_class = _mat
                        break

        # ── Process note references — wider context scan ──────────────────
        _nt_start     = max(0, m.start() - note_ctx_chars)
        _nt_end       = min(len(text), m.end() + note_ctx_chars)
        _nt_ctx       = text[_nt_start:_nt_end]
        note_matches  = list(dict.fromkeys(
            n.strip() for n in note_re.findall(_nt_ctx)
        ))[:3]
        process_notes = ', '.join(note_matches) if note_matches else ''

        results.append({
            'tag':               tag,
            'type_label':        type_label,
            'description':       description,
            'area':              area,
            'drawing_ref':       drawing_ref,
            'line_connections':  lc_tokens,
            'nozzle_connections': nozzle_tokens,
            'service_fluid':     service_fluid,
            'material_class':    material_class,
            'process_notes':     process_notes,
        })

    results.sort(key=lambda x: x['tag'])

    # ── Post-deduplication: canonicalize to the most-specific tag form ────
    # When the same physical equipment appears in two forms (e.g. both "V-308"
    # and "V-308-TF"), keep only the longer (more specific) form.
    # Two different equipment with the same base but different suffixes
    # (e.g. V-805-TF vs V-805-1F) are intentionally kept as separate rows.
    _suffix_re = re.compile(r'^([A-Z]{1,2}-[0-9]{3,5}[A-Z]?)-[A-Z0-9]{1,4}$')
    _full_tag_bases: set = set()
    for _item in results:
        _m = _suffix_re.match(_item['tag'])
        if _m:
            _full_tag_bases.add(_m.group(1))  # e.g. "V-308" from "V-308-TF"
    # Remove bare-base entries that have a suffixed sibling
    results = [
        _item for _item in results
        if _item['tag'] not in _full_tag_bases
    ]

    return results


_result_store: dict = {}


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def analyze_pid_equipment(request):
    """POST /api/v1/pid/equipment/analyze/"""
    config  = _load_config()
    ext_cfg = config.get('extraction', {})
    allowed = [e.lower() for e in ext_cfg.get('allowed_extensions', ['pdf'])]
    max_mb  = float(ext_cfg.get('max_file_size_mb', 50))

    pid_file = request.FILES.get('file') or (list(request.FILES.values())[0] if request.FILES else None)
    if not pid_file:
        return Response({'error': 'No file provided', 'success': False},
                        status=drf_status.HTTP_400_BAD_REQUEST)

    ext = pid_file.name.rsplit('.', 1)[-1].lower()
    if ext not in allowed:
        return Response({'error': f'Unsupported format: .{ext}. Allowed: {", ".join(allowed)}', 'success': False},
                        status=drf_status.HTTP_400_BAD_REQUEST)

    if pid_file.size > max_mb * 1024 * 1024:
        return Response({'error': f'File exceeds {max_mb} MB limit', 'success': False},
                        status=drf_status.HTTP_400_BAD_REQUEST)

    drawing_ref = pid_file.name.rsplit('.', 1)[0]
    upload_id   = f'EQ-{uuid.uuid4().hex[:12].upper()}'

    print(f'[EQ-DIAG] Analyzing: {pid_file.name}  upload_id={upload_id}', flush=True)
    _debug_info: dict = {'file': pid_file.name, 'upload_id': upload_id}

    try:
        # ── Stage 1: try Equipment Register (18-field table) extraction ──
        equipment       = _extract_equipment_register_rows(pid_file, config)
        extraction_mode = 'register'

        if equipment is None:
            # ── Stage 2: fall back to P&ID drawing mode ──
            print('[EQ-DIAG] Register mode returned None -> falling back to P&ID mode', flush=True)
            pid_file.seek(0)
            text      = _extract_text_from_pdf(pid_file, config)
            raw_items = _extract_equipment_items(text, drawing_ref, config)
            equipment = [_pid_item_to_register_schema(item) for item in raw_items]
            extraction_mode = 'pid_drawing'
            _debug_info.update({
                'text_len': len(text),
                'text_preview': text[:400] if text else '',
                'raw_items_count': len(raw_items),
                'after_dedup_count': len(equipment),
            })
            print(f'[EQ-DIAG] P&ID mode: text_len={len(text)}  raw_items={len(raw_items)}  after_dedup={len(equipment)}', flush=True)
            print(f'[EQ-DIAG] Tags found: {[i["tag"] for i in raw_items]}', flush=True)
            # Log full OCR text search for potential tags to diagnose missing ones
            import re as _re
            _all_candidates = _re.findall(r'\b[A-Z]{1,2}-[0-9]{3,5}[A-Z]?(?:-[A-Z0-9]{1,4})?\b', text)
            print(f'[EQ-DIAG] All tag-shaped tokens in text: {list(dict.fromkeys(_all_candidates))}', flush=True)
            # Targeted substring search — helps locate partially-read or misread tags
            for _substr in ['851', 'K-10', 'H-12', 'H-80', 'PX-', 'C-010', 'P-85', '010C', 'K-101']:
                _idx = text.find(_substr)
                if _idx >= 0:
                    print(f'[EQ-DIAG] substr "{_substr}" at {_idx}: {repr(text[max(0,_idx-15):_idx+35])}', flush=True)
                else:
                    print(f'[EQ-DIAG] substr "{_substr}" NOT FOUND in OCR text', flush=True)

        for idx, item in enumerate(equipment, 1):
            if not item.get('sl_no'):
                item['sl_no'] = str(idx)
            item['drawing_ref'] = drawing_ref

        _result_store[upload_id] = {
            'status':          'completed',
            'equipment':       equipment,
            'total':           len(equipment),
            'drawing_ref':     drawing_ref,
            'extraction_mode': extraction_mode,
        }

        _debug_info['extraction_mode'] = extraction_mode
        _debug_info['total'] = len(equipment)
        print(f'[EQ-DIAG] Done: {len(equipment)} items  mode={extraction_mode}', flush=True)

        resp_body: dict = {
            'success':         True,
            'upload_id':       upload_id,
            'status':          'completed',
            'equipment':       equipment,
            'total':           len(equipment),
            'drawing_ref':     drawing_ref,
            'extraction_mode': extraction_mode,
            'columns':         [c['label'] for c in config.get('excel_columns', []) if c['key'] != 'sl_no'],
        }
        # Include debug info automatically when nothing was extracted,
        # so the frontend can display diagnostic details without Docker log access.
        if len(equipment) == 0:
            resp_body['debug_info'] = _debug_info
        return Response(resp_body, status=drf_status.HTTP_200_OK)

    except Exception as exc:
        logger.error('[EquipmentList] Error: %s', exc, exc_info=True)
        _result_store[upload_id] = {'status': 'failed', 'error': str(exc)}
        return Response({'error': f'Extraction failed: {exc}', 'success': False},
                        status=drf_status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_equipment_analysis_status(request, upload_id):
    """GET /api/v1/pid/equipment/status/<upload_id>/"""
    entry = _result_store.get(upload_id)
    if not entry:
        return Response({'upload_id': upload_id, 'status': 'not_found', 'progress': 0},
                        status=drf_status.HTTP_404_NOT_FOUND)
    return Response({
        'upload_id': upload_id,
        'status':    entry.get('status', 'processing'),
        'progress':  100 if entry.get('status') == 'completed' else 50,
        'message':   entry.get('error', 'Extraction complete' if entry.get('status') == 'completed' else 'Processing...'),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_equipment_analysis_results(request, upload_id):
    """GET /api/v1/pid/equipment/results/<upload_id>/"""
    entry = _result_store.get(upload_id)
    if not entry:
        return Response({'error': 'Results not found - re-upload the file', 'upload_id': upload_id},
                        status=drf_status.HTTP_404_NOT_FOUND)
    if entry.get('status') == 'failed':
        return Response({'error': entry.get('error', 'Extraction failed'), 'upload_id': upload_id},
                        status=drf_status.HTTP_500_INTERNAL_SERVER_ERROR)

    config = _load_config()
    return Response({
        'success':     True,
        'upload_id':   upload_id,
        'equipment':   entry.get('equipment', []),
        'total':       entry.get('total', 0),
        'drawing_ref': entry.get('drawing_ref', ''),
        'columns':     [c['label'] for c in config.get('excel_columns', []) if c['key'] != 'sl_no'],
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def download_equipment_excel(request, upload_id):
    """GET /api/v1/pid/equipment/download-excel/<upload_id>/"""
    entry = _result_store.get(upload_id)
    if not entry or entry.get('status') != 'completed':
        return Response({'error': 'Results not available - re-upload the file'},
                        status=drf_status.HTTP_404_NOT_FOUND)

    config    = _load_config()
    col_defs  = config.get('excel_columns', [])
    equipment = entry.get('equipment', [])
    drawing   = entry.get('drawing_ref', 'equipment_list')

    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Equipment List'

        header_font  = Font(bold=True, color='FFFFFF', size=11)
        header_fill  = PatternFill(start_color='1E3A5F', end_color='1E3A5F', fill_type='solid')
        header_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
        thin_border  = Border(
            left=Side(style='thin'), right=Side(style='thin'),
            top=Side(style='thin'),  bottom=Side(style='thin'),
        )
        alt_fill = PatternFill(start_color='EFF6FF', end_color='EFF6FF', fill_type='solid')

        headers = [c['label'] for c in col_defs]
        for col_idx, label in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_idx, value=label)
            cell.font      = header_font
            cell.fill      = header_fill
            cell.alignment = header_align
            cell.border    = thin_border

        ws.row_dimensions[1].height = 30

        for row_idx, item in enumerate(equipment, 2):
            row_fill = alt_fill if row_idx % 2 == 0 else None
            for col_idx, col_def in enumerate(col_defs, 1):
                key   = col_def['key']
                value = item.get(key, '')
                if isinstance(value, list):
                    value = ', '.join(str(v) for v in value) if value else '-'
                elif value == '' or value is None:
                    value = '-'
                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                cell.alignment = Alignment(vertical='center', wrap_text=(key in ('line_connections', 'description')))
                cell.border    = thin_border
                if row_fill:
                    cell.fill = row_fill

        for col_idx, col_def in enumerate(col_defs, 1):
            ws.column_dimensions[get_column_letter(col_idx)].width = col_def.get('width', 18)

        ws.freeze_panes = 'A2'

        import io
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        safe_name = re.sub(r'[^\w\-]', '_', drawing)
        response  = HttpResponse(
            buf.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = f'attachment; filename="{safe_name}_equipment_list.xlsx"'
        return response

    except ImportError:
        return Response({'error': 'openpyxl is not installed on the server'},
                        status=drf_status.HTTP_501_NOT_IMPLEMENTED)
    except Exception as exc:
        logger.error('[EquipmentList] Excel error: %s', exc, exc_info=True)
        return Response({'error': f'Excel generation failed: {exc}'},
                        status=drf_status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def analyze_pid_equipment_batch(request):
    """POST /api/v1/pid/equipment/analyze-batch/
    Accepts multiple files and returns combined equipment results for all drawings.
    """
    config  = _load_config()
    ext_cfg = config.get('extraction', {})
    allowed = [e.lower() for e in ext_cfg.get('allowed_extensions', ['pdf'])]
    max_mb  = float(ext_cfg.get('max_file_size_mb', 50))

    files = list(request.FILES.values())
    if not files:
        return Response({'error': 'No files provided', 'success': False},
                        status=drf_status.HTTP_400_BAD_REQUEST)

    all_equipment = []
    drawing_refs  = []
    upload_id     = f'EQB-{uuid.uuid4().hex[:12].upper()}'

    for pid_file in files:
        ext = pid_file.name.rsplit('.', 1)[-1].lower()
        if ext not in allowed:
            return Response(
                {'error': f'Unsupported format: .{ext}. Allowed: {", ".join(allowed)}', 'success': False},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )
        if pid_file.size > max_mb * 1024 * 1024:
            return Response(
                {'error': f'{pid_file.name} exceeds {max_mb} MB limit', 'success': False},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

    for pid_file in files:
        drawing_ref = pid_file.name.rsplit('.', 1)[0]
        drawing_refs.append(drawing_ref)
        logger.info('[EquipmentList Batch] Analyzing: %s  upload_id=%s', pid_file.name, upload_id)
        try:
            # Try register mode first; fall back to P&ID mode
            equipment = _extract_equipment_register_rows(pid_file, config)
            if equipment is None:
                pid_file.seek(0)
                text      = _extract_text_from_pdf(pid_file, config)
                raw_items = _extract_equipment_items(text, drawing_ref, config)
                equipment = [_pid_item_to_register_schema(item) for item in raw_items]
            for idx, item in enumerate(equipment, 1):
                if not item.get('sl_no'):
                    item['sl_no'] = str(idx)
                item['drawing_ref'] = drawing_ref
            all_equipment.extend(equipment)
        except Exception as exc:
            logger.error('[EquipmentList Batch] Error on %s: %s', pid_file.name, exc, exc_info=True)
            _result_store[upload_id] = {'status': 'failed', 'error': str(exc)}
            return Response({'error': f'Extraction failed for {pid_file.name}: {exc}', 'success': False},
                            status=drf_status.HTTP_500_INTERNAL_SERVER_ERROR)

    # Re-number across drawings
    for idx, item in enumerate(all_equipment, 1):
        item['sl_no'] = idx

    _result_store[upload_id] = {
        'status':      'completed',
        'equipment':   all_equipment,
        'total':       len(all_equipment),
        'drawing_ref': ', '.join(drawing_refs),
    }

    logger.info('[EquipmentList Batch] Done: %d items from %d drawing(s)', len(all_equipment), len(files))

    return Response({
        'success':     True,
        'upload_id':   upload_id,
        'status':      'completed',
        'equipment':   all_equipment,
        'total':       len(all_equipment),
        'drawing_ref': ', '.join(drawing_refs),
        'columns':     [c['label'] for c in config.get('excel_columns', []) if c['key'] != 'sl_no'],
    }, status=drf_status.HTTP_200_OK)
