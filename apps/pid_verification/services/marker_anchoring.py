"""
Marker anchoring — precise drawing coordinates for P&ID overlay markers.

Why this exists
---------------
Many P&IDs are exported with their drawing text as vector glyph OUTLINES
(SHX/CAD fonts): the sheet looks like text but PyMuPDF finds only the few
real text runs (title block, reference table). The extractor previously ran
OCR only when a page had *zero* text words, so these hybrid sheets never got
OCR coordinates and every marker fell back to AI-Vision's coarse zone guess
(measured median error ~18% of the sheet) or to a hash pseudo-position.

What it does
------------
* ``ocr_anchor_needed`` — decides, per page, whether OCR anchoring is needed:
  always when there is no text layer, and also when the text layer carries
  fewer drawing tags than a threshold (hybrid sheets).
* ``ocr_page_words`` — Tesseract word boxes in ROTATED page space (the space
  the rendered drawing image uses), read at 0° and 90° so vertical text is
  captured too.
* ``assemble_ocr_tags`` — builds tag IDs from OCR words, including ISA
  bubbles where the function letters and loop number sit on two stacked lines
  (e.g. "PSV" over "8003" → ``PSV-8003``). Only whitelisted ISA / valve /
  equipment prefixes are accepted, so title-block text, ESD diamond numbers
  and partial reads cannot become markers.
* ``rotate_box`` — maps text-layer boxes from PyMuPDF's unrotated space into
  rotated page space so vector positions line up on rotated sheets.

Every entry produced here carries ``src`` ('vector' | 'ocr') so consumers can
tell exact positions from approximate ('vision') ones.

All thresholds are soft-coded via Django settings (see ``_cfg``).
"""

import io
import logging
import re

from django.conf import settings
logger = logging.getLogger(__name__)


# ── Soft-coded defaults (override any key via settings.PIDV_MARKER_ANCHORING) ──
_DEFAULTS = {
    # 'auto' → OCR when the text layer lacks drawing tags; 'always' / 'never'.
    'mode': 'auto',
    # Hybrid-sheet threshold: fewer tag tokens than this in the text layer → OCR.
    'min_vector_tags': 5,
    # OCR render resolution, capped so huge sheets stay within max_pixels.
    'dpi': 300,
    'max_pixels': 120_000_000,
    # Orientations read (degrees clockwise the page is turned before OCR).
    'angles': (0, 90),
    'min_conf': 30,
    # Stacked ISA bubble geometry, as multiples of the prefix text height.
    'stack_dy_min': 0.5,
    'stack_dy_max': 2.6,
    'stack_dx_max': 1.6,
    # Loop-number shape: 3–5 digits plus optional suffix letter.
    'number_pattern': r'^(\d{3,5})([A-Z]?)$',
    # Accepted tag prefixes beyond the extractor's instrument/valve defaults.
    'extra_prefixes': (
        'PG', 'TG', 'LG', 'FG', 'PDI', 'PDT', 'PDIT', 'FQ', 'FQI', 'FE', 'TE', 'LE', 'PE',
        'TW', 'FO', 'RO', 'PCV', 'LCV', 'FCV', 'TCV', 'PRV', 'TSV', 'BDV', 'SDV', 'ESDV',
        'XV', 'XY', 'HS', 'HV', 'ZSO', 'ZSC', 'ZS', 'CP', 'SP', 'SC', 'CSO', 'CSC',
        'PIC', 'LIC', 'FIC', 'TIC', 'PAH', 'PAL', 'PAHH', 'PALL', 'LAH', 'LAL', 'LAHH',
        'LALL', 'TAH', 'TAL', 'TAHH', 'TALL', 'FAH', 'FAL', 'FAHH', 'FALL', 'PDAH',
        'PSH', 'PSL', 'PSHH', 'PSLL', 'LSH', 'LSL', 'LSHH', 'LSLL', 'TSH', 'TSL',
        'FSH', 'FSL', 'AAH', 'AAHH', 'AIT', 'AE',
    ),
    # Single-letter equipment prefixes (V-803, P-101A, E-201-TF …). Single-letter
    # tags are only accepted when the OCR token itself contains a dash, so
    # ESD-diamond numbers such as "3.207" can never read as "S-207".
    'equipment_prefixes': ('V', 'E', 'T', 'K', 'C', 'P', 'H', 'X', 'F', 'R', 'D', 'M', 'G', 'Z'),
    # ── Geometric ISA bubble detection (OpenCV; skipped if unavailable) ──
    'bubble_detection': True,
    # Bubble radius band as a fraction of the rendered page width.
    'bubble_radius_frac': (0.0055, 0.0095),
    'bubble_hough_param2': 55,
    # Fraction of the circumference that must be drawn (rejects arcs/curves).
    'bubble_ring_min': 0.7,
    # Inner crop half-size as a fraction of the radius (text sits inside).
    'bubble_crop_k': 0.78,
    'bubble_crop_target_px': 160,
    # Hough runs on a copy scaled to this width (px) for scale-stable detection;
    # crops for OCR are always taken from the full-resolution render.
    'bubble_detect_width': 9400,
    # ── Loop-number sanity (needs >= min_known known tags to learn from) ──
    # Reads whose loop-number digit count is used by < share of the drawing's
    # known tags are rejected (e.g. "GWR-195" on a 4-digit 80xx drawing).
    'number_length_min_known': 5,
    'number_length_min_share': 0.10,
    # A trailing digit that is really a suffix letter (8003B read as 80038).
    'suffix_digit_to_letter': {'8': 'B', '4': 'A'},
    # ── Known-tag reconciliation (OCR misreads such as 8003B → 80038) ──
    # Substitutions OCR commonly confuses on CAD fonts; a read is corrected
    # only when exactly ONE known drawing tag is within one such edit.
    'confusable_pairs': (
        ('3', '5'), ('3', '8'), ('8', 'B'), ('B', 'E'), ('5', 'S'), ('0', 'O'),
        ('0', 'D'), ('1', 'I'), ('1', 'L'), ('6', 'G'), ('2', 'Z'), ('6', '8'),
    ),
}


def _cfg(key):
    overrides = getattr(settings, 'PIDV_MARKER_ANCHORING', None) or {}
    return overrides.get(key, _DEFAULTS[key])


def _tag_prefixes():
    from apps.pid_verification.services.extraction import (
        _DEFAULT_INSTRUMENT_PREFIXES, _DEFAULT_VALVE_PREFIXES,
    )
    return set(_DEFAULT_INSTRUMENT_PREFIXES) | set(_DEFAULT_VALVE_PREFIXES) | set(_cfg('extra_prefixes'))


_CLEAN_RE = re.compile(r'[^A-Z0-9\-]')
_PREFIX_RE = re.compile(r'^[A-Z]{1,5}$')
# Single-token tag: PREFIX-NUMBER[letter][-SUFFIX]  e.g. PT-8004, V-803-TF, SDV-8018
_SINGLE_RE = re.compile(r'^([A-Z]{1,5})[-\u2013]?(\d{3,5}[A-Z]?)(-[A-Z]{1,4})?$')


def _clean(text):
    return _CLEAN_RE.sub('', (text or '').upper().replace('\u2013', '-'))


def rotate_box(page, x0, y0, x1, y1):
    """Map a text-layer box from unrotated page space to rotated page space."""
    if not page.rotation:
        return x0, y0, x1, y1
    import pymupdf
    r = pymupdf.Rect(x0, y0, x1, y1) * page.rotation_matrix
    r.normalize()
    return r.x0, r.y0, r.x1, r.y1


def ocr_anchor_needed(raw_words):
    """True when this page needs OCR to anchor markers (see module docstring)."""
    from apps.pid_verification.services.extraction import _tesseract_available

    mode = _cfg('mode')
    if mode == 'never' or not _tesseract_available():
        return False
    if mode == 'always' or not raw_words:
        return True
    prefixes = _tag_prefixes() | set(_cfg('equipment_prefixes'))
    tag_tokens = 0
    for w in raw_words:
        m = _SINGLE_RE.match(_clean(str(w[4]).strip('()[]{}.,;:')))
        if m and m.group(1) in prefixes:
            tag_tokens += 1
    return tag_tokens < int(_cfg('min_vector_tags'))


def render_page_gray(page):
    """Render the page (rotation applied) for OCR. Returns (PIL image, dpi)."""
    import pymupdf
    from PIL import Image

    max_px = float(_cfg('max_pixels'))
    area_pt = float(page.rect.width * page.rect.height) or 1.0
    dpi = min(float(_cfg('dpi')), 72.0 * (max_px / area_pt) ** 0.5)
    Image.MAX_IMAGE_PIXELS = max(Image.MAX_IMAGE_PIXELS or 0, int(max_px * 1.2))
    pix = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72.0, dpi / 72.0), colorspace=pymupdf.csGRAY, alpha=False)
    return Image.open(io.BytesIO(pix.tobytes('png'))), dpi


def ocr_page_words(img):
    """
    OCR word boxes in the image's own (rotated page) pixel space.
    Returns [{'t', 'x0', 'y0', 'x1', 'y1', 'c', 'a'}] where ``a`` is the angle
    the page was turned for that read (0 → horizontal text).
    """
    import pytesseract

    W, H = img.size
    min_conf = float(_cfg('min_conf'))
    words = []
    for angle in _cfg('angles'):
        im = img if angle == 0 else img.rotate(-angle, expand=True)
        try:
            data = pytesseract.image_to_data(im, config='--oem 1 --psm 11',
                                             output_type=pytesseract.Output.DICT)
        except Exception as exc:
            logger.warning('[MarkerAnchoring] OCR at %s° failed: %s', angle, exc)
            continue
        for i, txt in enumerate(data.get('text', [])):
            txt = (txt or '').strip()
            try:
                conf = float(data['conf'][i])
            except (TypeError, ValueError):
                conf = -1.0
            if not txt or conf < min_conf:
                continue
            l, t = int(data['left'][i]), int(data['top'][i])
            w, h = int(data['width'][i]), int(data['height'][i])
            if angle == 0:
                x0, y0, x1, y1 = l, t, l + w, t + h
            elif angle == 90:
                # Turned 90° clockwise: rotated (x', y') ↔ original (y', H - x').
                x0, x1 = t, t + h
                y0, y1 = H - (l + w), H - l
            else:
                continue
            words.append({'t': txt, 'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1, 'c': conf, 'a': angle})
    return words


def assemble_ocr_tags(words, img_w, img_h):
    """
    Build {tag: {'x_pct', 'y_pct', 'src': 'ocr', 'kind'}} from OCR words.
    Stacked bubble reads are preferred over single-token reads because single
    tokens of the same tag usually come from notes / reference text.
    """
    prefixes = _tag_prefixes()
    equip = set(_cfg('equipment_prefixes'))
    num_re = re.compile(_cfg('number_pattern'))
    dy_min, dy_max, dx_max = float(_cfg('stack_dy_min')), float(_cfg('stack_dy_max')), float(_cfg('stack_dx_max'))

    def pct(x, y):
        return round(x / img_w * 100.0, 2), round(y / img_h * 100.0, 2)

    found = {}

    # 1) Stacked ISA bubbles: function letters directly above the loop number.
    horiz = [w for w in words if w['a'] == 0]
    tops = [w for w in horiz if _PREFIX_RE.match(_clean(w['t'])) and _clean(w['t']) in prefixes]
    nums = [w for w in horiz if num_re.match(_clean(w['t']))]
    for p in tops:
        pcx, pcy = (p['x0'] + p['x1']) / 2.0, (p['y0'] + p['y1']) / 2.0
        ph = max(1.0, p['y1'] - p['y0'])
        best = None
        for n in nums:
            ncx, ncy = (n['x0'] + n['x1']) / 2.0, (n['y0'] + n['y1']) / 2.0
            dy = ncy - pcy
            if dy_min * ph < dy < dy_max * ph and abs(ncx - pcx) < dx_max * ph:
                score = abs(ncx - pcx) + abs(dy - 1.4 * ph)
                if best is None or score < best[0]:
                    best = (score, n)
        if best is None:
            continue
        n = best[1]
        key = f"{_clean(p['t'])}-{_clean(n['t'])}"
        cx = (min(p['x0'], n['x0']) + max(p['x1'], n['x1'])) / 2.0
        cy = (p['y0'] + n['y1']) / 2.0
        if key not in found:
            xp, yp = pct(cx, cy)
            found[key] = {'x_pct': xp, 'y_pct': yp, 'src': 'ocr', 'kind': 'stacked'}

    # 2) Single-token tags (PT-8004, V-803-TF) — only where no bubble read exists.
    for w in words:
        m = _SINGLE_RE.match(_clean(w['t']))
        if not m:
            continue
        pre, num, suffix = m.group(1), m.group(2), m.group(3) or ''
        if pre in equip and pre not in prefixes:
            if '-' not in w['t'] and '\u2013' not in w['t']:
                continue
        elif pre not in prefixes:
            continue
        cx, cy = (w['x0'] + w['x1']) / 2.0, (w['y0'] + w['y1']) / 2.0
        xp, yp = pct(cx, cy)
        for key in {f'{pre}-{num}{suffix}', f'{pre}-{num}'}:
            if key not in found:
                found[key] = {'x_pct': xp, 'y_pct': yp, 'src': 'ocr', 'kind': 'text'}
    return found


def detect_bubble_tags(img):
    """
    Geometric ISA bubble pass: find drawn circles of instrument-bubble size,
    then OCR only the inside of each one ("PT" over "8004"). Positions are the
    circle centre, so markers land dead-centre on the bubble. Returns
    {tag: {'x_pct', 'y_pct', 'src': 'ocr', 'kind': 'bubble'}}; {} when OpenCV
    is unavailable or the pass is disabled.
    """
    if not _cfg('bubble_detection'):
        return {}
    try:
        import cv2
        import numpy as np
        import pytesseract
    except ImportError:
        logger.info('[MarkerAnchoring] OpenCV unavailable - bubble detection skipped')
        return {}

    gray = np.asarray(img.convert('L'))
    H, W = gray.shape
    rf_min, rf_max = _cfg('bubble_radius_frac')
    work_w = float(_cfg('bubble_detect_width'))
    s = min(1.0, work_w / W)
    work = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1.0 else gray
    wW = work.shape[1]
    r_min, r_max = max(4, int(wW * rf_min)), max(6, int(wW * rf_max))
    circles = cv2.HoughCircles(cv2.medianBlur(work, 3), cv2.HOUGH_GRADIENT, dp=1.2,
                               minDist=r_min * 1.6, param1=120, param2=int(_cfg('bubble_hough_param2')),
                               minRadius=r_min, maxRadius=r_max)
    if circles is None:
        return {}
    circles = [(cx / s, cy / s, r / s) for cx, cy, r in circles[0]]

    dark = gray < 128
    angles = np.linspace(0, 2 * np.pi, 72, endpoint=False)

    def ring_score(cx, cy, r):
        hits = 0
        for a in angles:
            ca, sa = np.cos(a), np.sin(a)
            for dr in (-2, -1, 0, 1, 2):
                x, y = int(round(cx + (r + dr) * ca)), int(round(cy + (r + dr) * sa))
                if 0 <= x < W and 0 <= y < H and dark[y, x]:
                    hits += 1
                    break
        return hits / len(angles)

    prefixes = _tag_prefixes()
    num_re = re.compile(_cfg('number_pattern'))
    ring_min, k = float(_cfg('bubble_ring_min')), float(_cfg('bubble_crop_k'))
    target = float(_cfg('bubble_crop_target_px'))
    found = {}
    for cx, cy, r in circles:
        cx, cy, r = int(round(cx)), int(round(cy)), int(round(r))
        if ring_score(cx, cy, r) < ring_min:
            continue
        half = int(r * k)
        crop = gray[max(0, cy - half):min(H, cy + half), max(0, cx - half):min(W, cx + half)]
        if crop.size == 0:
            continue
        scale = max(1.0, target / max(1, crop.shape[0]))
        if scale > 1.0:
            crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        _, bw = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        try:
            txt = pytesseract.image_to_string(
                bw, config='--oem 1 --psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')
        except Exception:
            continue
        lines = [re.sub(r'[^A-Z0-9]', '', ln.upper()) for ln in txt.splitlines() if ln.strip()]
        if len(lines) < 2 or lines[0] not in prefixes or not num_re.match(lines[1]):
            continue
        key = f'{lines[0]}-{lines[1]}'
        found.setdefault(key, {'x_pct': round(cx / W * 100.0, 2), 'y_pct': round(cy / H * 100.0, 2),
                               'src': 'ocr', 'kind': 'bubble'})
    return found


def _one_confusable_edit(a, b, pairs):
    """True when a→b needs exactly one edit: a confusable substitution or one insert/delete."""
    if a == b:
        return False
    if len(a) == len(b):
        diffs = [(x, y) for x, y in zip(a, b) if x != y]
        return len(diffs) == 1 and frozenset(diffs[0]) in pairs
    if abs(len(a) - len(b)) != 1:
        return False
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def _allowed_number_lengths(known_tags):
    """Loop-number digit counts the drawing actually uses, or None if unknown."""
    nums = []
    for t in known_tags or []:
        m = re.search(r'-(\d{2,6})', str(t))
        if m:
            nums.append(len(m.group(1)))
    if len(nums) < int(_cfg('number_length_min_known')):
        return None
    share = float(_cfg('number_length_min_share'))
    return {n for n in set(nums) if nums.count(n) / len(nums) >= share}


def _split_tag(key):
    pre, _, rest = key.partition('-')
    m = re.match(r'^(\d+)([A-Z]?)(.*)$', rest)
    return (pre, m.group(1), m.group(2), m.group(3)) if m else (pre, None, None, None)


def reconcile_with_known_tags(found, known_tags):
    """
    Repair OCR reads whose loop number is implausible for this drawing.

    A read with a plausible shape (digit count the drawing uses) is ALWAYS
    kept exactly as read — the known-tag list comes from full-page OCR and is
    incomplete, so "correcting" a valid read could rename a real tag. Only an
    implausible read (e.g. PT-80035A, PI-80038) is repaired: first to the
    single known tag one confusable edit away, else by turning a trailing
    digit into its look-alike suffix letter (80038 → 8003B); otherwise it is
    dropped. The original read is kept in ``ocr_read``.
    """
    allowed = _allowed_number_lengths(known_tags)
    if not allowed:
        return found
    known = {str(t).upper() for t in (known_tags or []) if t}
    pairs = {frozenset(p) for p in _cfg('confusable_pairs')}
    to_letter = _cfg('suffix_digit_to_letter')
    by_prefix = {}
    for t in known:
        pre, _, rest = t.partition('-')
        by_prefix.setdefault(pre, []).append((t, rest))
    out = {}
    for key, pos in found.items():
        pre, digits, letter, tail = _split_tag(key)
        if digits is None or len(digits) in allowed or key in known:
            out.setdefault(key, pos)
            continue
        num = key.partition('-')[2]
        cands = [t for t, rest in by_prefix.get(pre, []) if _one_confusable_edit(num, rest, pairs)]
        if len(cands) == 1:
            out.setdefault(cands[0], {**pos, 'ocr_read': key})
        elif not letter and len(digits) - 1 in allowed and digits[-1] in to_letter:
            out.setdefault(f'{pre}-{digits[:-1]}{to_letter[digits[-1]]}{tail}', {**pos, 'ocr_read': key})
        else:
            logger.debug('[MarkerAnchoring] dropped implausible read %s', key)
    return out


def anchor_page_tags(page, known_tags=None):
    """
    Full OCR anchoring for one page. Returns (tag_positions, ocr_words, img_size).
    Priority: geometric bubble read > stacked bubble text > single-token text.
    """
    img, dpi = render_page_gray(page)
    img_w, img_h = img.size
    words = ocr_page_words(img)
    merged = dict(detect_bubble_tags(img))
    for key, pos in assemble_ocr_tags(words, img_w, img_h).items():
        merged.setdefault(key, pos)
    merged = reconcile_with_known_tags(merged, known_tags)
    logger.info('[MarkerAnchoring] dpi=%d words=%d tags=%d (bubble=%d)', int(dpi), len(words),
                len(merged), sum(1 for v in merged.values() if v.get('kind') == 'bubble'))
    return merged, words, (img_w, img_h)