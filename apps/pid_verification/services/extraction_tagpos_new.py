import re  # noqa: F811 – re may already be imported in the module that includes this file
import math
import logging as _logging

logger = _logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Soft-coded constants — adjust here without touching any other logic
# ─────────────────────────────────────────────────────────────────────────────

# Line-number pattern for formats like: 2"-D-6152-033842-X-N  or  4"-41-SWR-64313-A2AU16-V
# Supports both onshore (SIZE"-FLUID-SEQ-SPEC[-INS]) and offshore (AREA-FLUID-SIZE-SPEC-SEQ[-INS])
_LINE_NUMBER_PAT = re.compile(
    r'(?<![A-Z0-9])'
    r'('
    # Onshore without area: 2"-D-6152-033842
    r'\d{1,2}["\u201c\u201d]'           # SIZE (1–2 digits + inch mark)
    r'-[A-Z]{1,3}'                       # FLUID CODE
    r'-\d{3,5}'                          # SEQUENCE
    r'-[A-Z0-9]{4,10}'                   # PIPE CLASS
    r'(?:-[A-Z]{0,2})?'                  # optional INSULATION
    r'|'
    # Onshore with area: 4"-41-SWR-64313-A2AU16
    r'\d{1,2}["\u201c\u201d]'
    r'-\d{2,3}'                          # AREA
    r'-[A-Z]{1,3}'
    r'-\d{3,5}'
    r'-[A-Z0-9]{4,10}'
    r'(?:-[A-Z]{1,2})?'
    r'|'
    # Offshore/ADNOC: 604-HO-8-BC2CA0-1071-H
    r'\d{3}'                             # AREA (3 digits)
    r'-[A-Z]{1,3}'
    r'-\d{1,2}'
    r'-[A-Z0-9]{4,10}'
    r'-\d{3,5}'
    r'(?:-[A-Z]{1,2})?'
    r')',
    re.IGNORECASE,
)

# Span direction threshold for "vertical" — dot product of dir vector with (1,0)
# A horizontal span has dir≈(1,0); vertical has dir≈(0,±1).
# Soft-coded: if abs(dx) < VERTICAL_THRESHOLD the span is considered non-horizontal.
_VERTICAL_THRESHOLD = 0.5   # tune: lower = catch more angled text

# Near-duplicate threshold: two line numbers are "near-duplicates" if they share
# the same SIZE+FLUID+SPEC and their sequence numbers differ by at most this value.
_NEAR_DUP_SEQ_THRESHOLD = 2  # soft-coded

# Minimum Levenshtein ratio to flag a string-level near-duplicate (0–1).
_NEAR_DUP_LEVENSHTEIN_RATIO = 0.85  # soft-coded


def _levenshtein_ratio(a, b):
    # type: (str, str) -> float
    """Fast normalised edit distance (0 = totally different, 1 = identical)."""
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    if la == 0 or lb == 0:
        return 0.0
    if abs(la - lb) / max(la, lb) > 0.5:
        return 0.0  # short-circuit: too different in length
    # Wagner-Fischer DP
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(prev[j] + 1, curr[j - 1] + 1,
                            prev[j - 1] + (0 if ca == cb else 1)))
        prev = curr
    dist = prev[lb]
    return 1.0 - dist / max(la, lb)


def _parse_line_number(ln):
    # type: (str) -> dict
    """
    Parse a line number string into its components.
    Returns a dict with keys: size, area, fluid, sequence, spec, insulation, raw.
    Any unparseable field is None.
    Soft-coded: regex patterns live in _LINE_NUMBER_PAT above.
    """
    ln = ln.strip().upper()
    result = {'raw': ln, 'size': None, 'area': None,
              'fluid': None, 'sequence': None, 'spec': None, 'insulation': None}

    # Remove trailing/leading noise
    ln = re.sub(r'^[\s\-]+|[\s\-]+$', '', ln)

    # Split on dash, preserving inch-mark that may be attached to first token
    parts = re.split(r'-', ln)
    if len(parts) < 4:
        return result

    # Part 0: SIZE (e.g. 2" or 2)
    size_m = re.match(r"^(\d{1,2})[\"'\u201c\u201d]?$", parts[0])
    if size_m:
        result['size'] = int(size_m.group(1))
        offset = 1
    else:
        result['size'] = None
        offset = 0

    remaining = parts[offset:]
    if not remaining:
        return result

    # Detect if next part is a 2–3 digit AREA code
    if re.match(r'^\d{2,3}$', remaining[0]):
        result['area'] = remaining[0]
        remaining = remaining[1:]

    if not remaining:
        return result

    # FLUID code: 1–3 letters
    if re.match(r'^[A-Z]{1,3}$', remaining[0]):
        result['fluid'] = remaining[0]
        remaining = remaining[1:]

    if len(remaining) < 2:
        return result

    # SEQUENCE: 3–5 digits
    if re.match(r'^\d{3,5}$', remaining[0]):
        result['sequence'] = int(remaining[0])
        remaining = remaining[1:]

    # PIPE CLASS (next part): 4–10 alphanumeric chars
    if remaining and re.match(r'^[A-Z0-9]{4,10}$', remaining[0]):
        result['spec'] = remaining[0]
        remaining = remaining[1:]

    # Optional INSULATION suffix
    if remaining and re.match(r'^[A-Z]{0,2}$', remaining[0]):
        result['insulation'] = remaining[0] or None

    return result


def detect_duplicate_line_numbers(all_line_numbers_raw):
    # type: (list) -> list
    """
    Programmatically detect exact-duplicate and near-duplicate line numbers.

    Parameters
    ----------
    all_line_numbers_raw : list of str
        All raw line number strings found on the drawing (may contain duplicates).

    Returns
    -------
    list of dict, each with keys:
        rule_id          – 'LSZ-DUP-001' (exact) or 'LSZ-DUP-002' (near)
        severity         – 'critical' (exact) or 'major' (near)
        category         – 'line_duplicate'
        issue_observed   – human-readable description
        action_required  – remediation text
        evidence         – the two conflicting raw strings
        direction        – None (not position-specific)

    Soft-coded:
        _NEAR_DUP_SEQ_THRESHOLD      controls the sequence-number window
        _NEAR_DUP_LEVENSHTEIN_RATIO  controls the string-similarity fallback
    """
    issues = []
    seen = []   # list of parsed dicts

    for raw in all_line_numbers_raw:
        parsed = _parse_line_number(raw)
        parsed['raw'] = raw

        for prev in seen:
            # ── EXACT duplicate ──────────────────────────────────────────────
            if raw.strip().upper() == prev['raw'].strip().upper():
                issues.append({
                    'rule_id':        'LSZ-DUP-001',
                    'severity':       'critical',
                    'category':       'line_duplicate',
                    'issue_observed': (
                        f"Exact duplicate line number detected: '{raw}' appears more than "
                        f"once on the drawing."
                    ),
                    'action_required': (
                        "Remove or renumber one occurrence. Each piping line must have a "
                        "unique designation per P&ID numbering standard."
                    ),
                    'evidence':  [raw, prev['raw']],
                    'direction': None,
                })
                continue  # don't also raise near-dup for same pair

            # ── Near-duplicate: same size + fluid + spec, sequence off by ≤N ─
            if (parsed['size'] is not None and
                    parsed['size'] == prev['size'] and
                    parsed['fluid'] == prev['fluid'] and
                    parsed['spec']  == prev['spec']  and
                    parsed['sequence'] is not None and
                    prev['sequence'] is not None):
                diff = abs(parsed['sequence'] - prev['sequence'])
                if 0 < diff <= _NEAR_DUP_SEQ_THRESHOLD:
                    issues.append({
                        'rule_id':        'LSZ-DUP-002',
                        'severity':       'major',
                        'category':       'line_duplicate',
                        'issue_observed': (
                            f"Near-duplicate line numbers: '{raw}' and '{prev['raw']}' share "
                            f"SIZE={parsed['size']}\", FLUID={parsed['fluid']}, "
                            f"SPEC={parsed['spec']} — sequence differs by {diff}."
                        ),
                        'action_required': (
                            "Verify both line numbers are intentional. Common causes: "
                            "OCR misread (6→8), typo in drafting, or missing revision update."
                        ),
                        'evidence':  [raw, prev['raw']],
                        'direction': None,
                    })
                    continue

            # ── Levenshtein string-level fallback (catches OCR mutations) ─────
            ratio = _levenshtein_ratio(
                raw.strip().upper(), prev['raw'].strip().upper()
            )
            if ratio >= _NEAR_DUP_LEVENSHTEIN_RATIO and ratio < 1.0:
                issues.append({
                    'rule_id':        'LSZ-DUP-003',
                    'severity':       'major',
                    'category':       'line_duplicate',
                    'issue_observed': (
                        f"Possible duplicate line number (OCR/typo variant): '{raw}' and "
                        f"'{prev['raw']}' are {int(ratio * 100)}% similar."
                    ),
                    'action_required': (
                        "Cross-check both line labels on drawing — likely an OCR misread "
                        "or a one-character drafting error."
                    ),
                    'evidence':  [raw, prev['raw']],
                    'direction': None,
                })

        seen.append(parsed)

    return issues


def extract_line_numbers_from_page(file_path, page_index):
    # type: (str, int) -> list
    """
    Extract all line number strings from a single PDF page, including those
    written at a vertical or arbitrary angle.

    Extraction strategy (soft-coded, see constants above):
      Step 1 – Vector PDF (Path A):
          a. span-level dict extraction with 'dir' (direction cosine vector).
             Spans with abs(dir[0]) < _VERTICAL_THRESHOLD are treated as rotated.
             For rotated spans the raw text is concatenated character-by-character
             from the 'chars' list if available, otherwise the span text is used.
          b. Adjacent-word reconstruction for split line numbers (e.g. "2" –D–6152" split
             across two word tokens).

      Step 2 – Scanned / image-only PDF (Path B):
          Tesseract with PSM 0 (orientation detection) + PSM 12 (sparse + OSD) to find
          rotated text regions. The image is also rotated 90° and 270° and re-OCR'd
          to catch vertical line labels.

    Returns
    -------
    list of dict, each with keys:
        raw       – the raw matched string
        x_pct     – x centre as % of page width  (0–100)
        y_pct     – y centre as % of page height (0–100)
        direction – 'H' (horizontal), 'V' (vertical ~90°), 'A' (arbitrary angle),
                    or 'U' (unknown / scanned)
    """
    found = []  # list of {'raw', 'x_pct', 'y_pct', 'direction'}
    _seen_keys = set()  # dedup by (normalised_raw, coarse_x, coarse_y)

    def _add(raw, x_pct, y_pct, direction):
        key = (raw.strip().upper(), round(x_pct, 0), round(y_pct, 0))
        if key in _seen_keys:
            return
        _seen_keys.add(key)
        found.append({'raw': raw.strip(), 'x_pct': x_pct,
                      'y_pct': y_pct, 'direction': direction})

    def _scan_for_line_numbers(text, x_pct, y_pct, direction):
        for m in _LINE_NUMBER_PAT.finditer(text):
            _add(m.group(1), x_pct, y_pct, direction)

    def _pct(v, dim):
        return round(float(v) / float(dim) * 100.0, 2)

    def _classify_direction(dir_vec):
        """Return 'H', 'V', or 'A' from a (dx, dy) unit direction vector."""
        dx, dy = dir_vec
        if abs(dx) >= _VERTICAL_THRESHOLD:
            return 'H'
        if abs(dy) >= _VERTICAL_THRESHOLD:
            return 'V'
        return 'A'

    try:
        import fitz

        if not file_path.lower().endswith('.pdf'):
            return found

        doc = fitz.open(file_path)
        if page_index >= len(doc):
            doc.close()
            return found

        page   = doc[page_index]
        page_w = page.rect.width  or 1.0
        page_h = page.rect.height or 1.0

        # ── Path A: vector / embedded-text PDF ───────────────────────────────
        try:
            page_dict = page.get_text('rawdict', flags=fitz.TEXT_PRESERVE_WHITESPACE)
        except Exception:
            page_dict = page.get_text('dict', flags=0)

        blocks = page_dict.get('blocks', [])
        has_text = any(b.get('type') == 0 for b in blocks)  # 0 = text block

        if has_text:
            for blk in blocks:
                if blk.get('type') != 0:
                    continue
                for ln in blk.get('lines', []):
                    # 'dir' is (cos θ, sin θ) of the text baseline direction
                    line_dir = ln.get('dir', (1.0, 0.0))
                    orientation = _classify_direction(line_dir)

                    for sp in ln.get('spans', []):
                        bbox = sp.get('bbox', (0, 0, 0, 0))
                        xp = _pct((bbox[0] + bbox[2]) / 2.0, page_w)
                        yp = _pct((bbox[1] + bbox[3]) / 2.0, page_h)

                        # For rotated spans: reconstruct text from char bboxes
                        # (avoids garbled order that fitz may produce for vertical text)
                        if orientation != 'H' and 'chars' in sp:
                            chars_sorted = sorted(
                                sp['chars'],
                                key=lambda c: (c.get('bbox', (0,))[1],
                                               c.get('bbox', (0,))[0])
                            )
                            text = ''.join(c.get('c', '') for c in chars_sorted)
                        else:
                            text = sp.get('text', '')

                        text = text.strip()
                        if text:
                            _scan_for_line_numbers(text, xp, yp, orientation)

            # Adjacent-word reconstruction: reassemble split tokens like
            # ['2"', '-D-', '6152', '-033842'] into the full line number.
            words_raw = page.get_text('words')
            words = [(w[0], w[1], w[2], w[3], w[4].strip())
                     for w in words_raw if w[4].strip()]
            for i, (x0, y0, x1, y1, tok) in enumerate(words):
                # Slide a 4-token window and join to try matching the pattern
                for window in range(2, 6):
                    if i + window > len(words):
                        break
                    group = words[i:i + window]
                    # All tokens must be on roughly the same baseline (within 3% page height)
                    ys = [((g[1] + g[3]) / 2.0 / page_h) * 100.0 for g in group]
                    if max(ys) - min(ys) > 3.0:
                        break
                    combined = ''.join(g[4] for g in group)
                    cx = _pct((group[0][0] + group[-1][2]) / 2.0, page_w)
                    cy = _pct(sum(ys) / len(ys), 100.0)
                    _scan_for_line_numbers(combined, cx, cy, 'H')

        else:
            # ── Path B: scanned / image-only PDF ─────────────────────────────
            try:
                import pytesseract
                from PIL import Image, ImageOps
                import io

                dpi = 300
                mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
                pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY, alpha=False)
                img_bytes = pix.tobytes('png')
                base_img = Image.open(io.BytesIO(img_bytes)).convert('L')
                img_w, img_h = base_img.size

                # Scan 4 orientations: 0°, 90°, 180°, 270°
                # Vertical line numbers appear as horizontal text one of these rotations.
                # Soft-coded: direction label maps to rotation angle
                _ROTATIONS = [
                    (0,   'H',  img_w, img_h),
                    (90,  'V',  img_h, img_w),   # 90° CW → vertical-up text reads H
                    (270, 'V',  img_h, img_w),   # 90° CCW → vertical-down text reads H
                    (180, 'H',  img_w, img_h),   # upside-down (rare)
                ]
                for angle, orient, w, h in _ROTATIONS:
                    rotated = base_img.rotate(angle, expand=True) if angle else base_img

                    data = pytesseract.image_to_data(
                        rotated,
                        config='--oem 1 --psm 11',
                        output_type=pytesseract.Output.DICT,
                    )
                    n = len(data.get('text', []))
                    for k in range(n):
                        txt = str(data['text'][k]).strip()
                        if not txt:
                            continue
                        try:
                            conf = int(data['conf'][k])
                        except (ValueError, TypeError):
                            conf = -1
                        if conf < 25:
                            continue
                        lft  = int(data['left'][k])
                        top  = int(data['top'][k])
                        wid  = int(data['width'][k])
                        hgt  = int(data['height'][k])
                        # Map rotated coords back to approximate original page %
                        # (exact mapping not needed; we just need a rough position)
                        xp = _pct(lft + wid / 2.0, w or 1)
                        yp = _pct(top + hgt / 2.0, h or 1)
                        _scan_for_line_numbers(txt, xp, yp, orient)

                    # Also try window-joining adjacent tokens within the same line block
                    word_buf = []
                    for k in range(n):
                        txt = str(data['text'][k]).strip()
                        if not txt:
                            if word_buf:
                                combined = ''.join(t for _, _, t in word_buf)
                                cx = _pct(
                                    (word_buf[0][0] + word_buf[-1][0]) / 2.0, w or 1
                                )
                                cy = _pct(word_buf[0][1], h or 1)
                                _scan_for_line_numbers(combined, cx, cy, orient)
                                word_buf = []
                            continue
                        try:
                            conf = int(data['conf'][k])
                        except (ValueError, TypeError):
                            conf = -1
                        if conf < 25:
                            if word_buf:
                                combined = ''.join(t for _, _, t in word_buf)
                                cx = _pct(
                                    (word_buf[0][0] + word_buf[-1][0]) / 2.0, w or 1
                                )
                                cy = _pct(word_buf[0][1], h or 1)
                                _scan_for_line_numbers(combined, cx, cy, orient)
                                word_buf = []
                            continue
                        word_buf.append((
                            int(data['left'][k]),
                            int(data['top'][k]),
                            txt,
                        ))

            except ImportError:
                pass
            except Exception as exc:
                logger.debug('[PIDLineNum] Scanned OCR rotation pass skipped: %s', exc)

        doc.close()

    except ImportError:
        pass
    except Exception as exc:
        logger.debug('[PIDLineNum] extract_line_numbers_from_page failed: %s', exc)

    return found


# ─────────────────────────────────────────────────────────────────────────────
# Original _extract_tag_positions (preserved, extended with vertical support)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_tag_positions(file_path, page_index):
    # type: (str, int) -> dict
    """
    Extract real bounding-box anchor coordinates for every locatable text
    element on the page, stored as percentage offsets of the page dimensions.

    Path A (vector PDF): PyMuPDF get_text('words') + adjacent-pair + span-level.
      Now also handles vertical/rotated spans using span 'dir' vector.
    Path B (scanned PDF): Tesseract image_to_data() at 300 dpi, used when
      Path A yields 0 word tokens (image-only / scanned PDF).
      Extended to also re-scan at 90°/270° rotations to catch vertical text.

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
    # Span direction threshold — re-uses module-level constant but kept local too
    _VERTICAL_THRESHOLD = 0.5

    # Quote chars used as inch marks in PDF/OCR text.
    _QUOTE_CLASS = u'["\u201c\u201d\'\u2019]'
    # Matches NPS annotations: 6" 4" 12" 6'' 6mm DN100
    _LS_PAT = re.compile(
        u'(?:^|\\b)(\\d+(?:\\.\\d+)?)\\s*(' + _QUOTE_CLASS + u'{1,2}|mm|DN)(\\d*)(?:\\b|$)',
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
            # Extended: use span 'dir' vector to detect vertical/rotated text.
            try:
                try:
                    page_dict = page.get_text('rawdict', flags=fitz.TEXT_PRESERVE_WHITESPACE)
                except Exception:
                    page_dict = page.get_text('dict', flags=0)

                for blk in page_dict.get('blocks', []):
                    if blk.get('type') != 0:
                        continue
                    for ln in blk.get('lines', []):
                        line_dir = ln.get('dir', (1.0, 0.0))
                        is_vertical = abs(line_dir[0]) < _VERTICAL_THRESHOLD

                        for sp in ln.get('spans', []):
                            b = sp.get('bbox', (0, 0, 0, 0))
                            xp = _pct((b[0] + b[2]) / 2.0, page_w)
                            yp = _pct((b[1] + b[3]) / 2.0, page_h)

                            # Reconstruct text from char list for rotated spans
                            if is_vertical and 'chars' in sp:
                                chars_sorted = sorted(
                                    sp['chars'],
                                    key=lambda c: (
                                        c.get('bbox', (0,))[1],
                                        c.get('bbox', (0,))[0],
                                    ),
                                )
                                s = ''.join(c.get('c', '') for c in chars_sorted).strip()
                            else:
                                s = sp.get('text', '').strip()

                            if s:
                                _scan_text(s, xp, yp)
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

                def _ocr_image_to_words(image, w, h):
                    """Run Tesseract image_to_data and return word tuples."""
                    data = pytesseract.image_to_data(
                        image,
                        config='--oem 1 --psm 11',
                        output_type=pytesseract.Output.DICT,
                    )
                    n = len(data.get('text', []))
                    words_out = []
                    for i in range(n):
                        txt = str(data['text'][i]).strip()
                        if not txt:
                            continue
                        try:
                            conf = int(data['conf'][i])
                        except (ValueError, TypeError):
                            conf = -1
                        if conf < 20:
                            continue
                        lft = int(data['left'][i])
                        top = int(data['top'][i])
                        wid = int(data['width'][i])
                        hgt = int(data['height'][i])
                        words_out.append((lft, top, lft + wid, top + hgt, txt))
                    return words_out

                # 0° pass (horizontal text)
                ocr_words = _ocr_image_to_words(img, img_w, img_h)
                _process_words(ocr_words, img_w, img_h)

                # 90° rotation pass — catches vertical-upward line labels
                # Coordinates in rotated space: rotated(x,y) → original ≈ (y, img_w-x)
                img_90 = img.rotate(90, expand=True)
                ocr_90 = _ocr_image_to_words(img_90, img_h, img_w)
                _process_words(ocr_90, img_h, img_w)

                # 270° rotation pass — catches vertical-downward line labels
                img_270 = img.rotate(270, expand=True)
                ocr_270 = _ocr_image_to_words(img_270, img_h, img_w)
                _process_words(ocr_270, img_h, img_w)

            except ImportError:
                pass
            except Exception as exc:
                logger.debug('[PIDExtraction] OCR coord pass skipped: %s', exc)


        doc.close()

        # --- Finalise line-size positions ---
        for key, pts in _ls_all.items():
            if key in positions:
                continue
            body = [p for p in pts
                    if p['y_pct'] / 100.0 < _TB_Y_FRAC and p['x_pct'] / 100.0 < _TB_X_FRAC]
            use   = body if body else pts
            avg_x = round(sum(p['x_pct'] for p in use) / len(use), 2)
            avg_y = round(sum(p['y_pct'] for p in use) / len(use), 2)
            positions[key] = {'x_pct': avg_x, 'y_pct': avg_y, 'all': use}

    except ImportError:
        pass
    except Exception as exc:
        logger.debug('[PIDExtraction] tag_positions extraction skipped: %s', exc)

    return positions
