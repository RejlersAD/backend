"""
Tag Position Locator — additive, best-effort helper for the P&ID Drawing Canvas
"Suggested From/To" enhancement.

⚠️ SCOPE / SAFETY NOTE
This module is intentionally standalone and NEVER touches the core OCR /
FROM-TO detection pipeline (parse_with_regex, spatial_matching,
geometric_from_to_detector, from_to_integration/OpenAI Vision). It is called
as an additive, isolated step AFTER those phases complete for a given page,
and its only job is to locate where each already-extracted line_number's own
tag/label text sits on the rendered page image (a single point), so the
frontend can offer it as an optional "suggested" marker. Any failure here
must be swallowed and logged — it must never affect extraction results.

Uses a single `pytesseract.image_to_data()` call on the page image that has
ALREADY been rendered by the caller (no new expensive PDF rendering).
"""

import logging
import difflib
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import pytesseract
    from pytesseract import Output
    PYTESSERACT_AVAILABLE = True
except ImportError:
    pytesseract = None
    Output = None
    PYTESSERACT_AVAILABLE = False

# ---------------------------------------------------------------------------
# Soft-coded thresholds/constants — tune here without touching matching logic.
# ---------------------------------------------------------------------------
# Minimum difflib ratio for a fuzzy (non-exact) match to be accepted.
TAG_POSITION_FUZZY_THRESHOLD = 0.82

# Tesseract word-confidence values below this are ignored (noise).
TAG_POSITION_MIN_WORD_CONFIDENCE = 20

# Max number of consecutive words considered when building a sliding text
# window for fuzzy matching against a line_number (line numbers are short).
TAG_POSITION_MAX_WORDS_WINDOW = 6


def _cluster_key(data: Dict, idx: int) -> tuple:
    """Tesseract's own block/paragraph/line grouping = reading-order cluster."""
    return (data['block_num'][idx], data['par_num'][idx], data['line_num'][idx])


def _build_clusters(data: Dict) -> List[Dict]:
    """
    Groups Tesseract word-level output into reading-order clusters (one
    cluster ≈ one line of text on the drawing), each with concatenated text
    and a bounding box (union of its words).
    """
    clusters: Dict[tuple, Dict] = {}
    n = len(data.get('text', []))
    for i in range(n):
        text = (data['text'][i] or '').strip()
        try:
            conf = float(data['conf'][i])
        except (ValueError, TypeError):
            conf = -1
        if not text or conf < TAG_POSITION_MIN_WORD_CONFIDENCE:
            continue

        key = _cluster_key(data, i)
        left, top = data['left'][i], data['top'][i]
        width, height = data['width'][i], data['height'][i]

        cluster = clusters.setdefault(key, {
            'words': [],
            'left': left, 'top': top,
            'right': left + width, 'bottom': top + height,
        })
        cluster['words'].append(text)
        cluster['left'] = min(cluster['left'], left)
        cluster['top'] = min(cluster['top'], top)
        cluster['right'] = max(cluster['right'], left + width)
        cluster['bottom'] = max(cluster['bottom'], top + height)

    return list(clusters.values())


def locate_tag_positions(
    img,
    line_numbers: List[str],
    normalize_fn: Optional[Callable[[str], str]] = None,
) -> Dict[str, Dict]:
    """
    Best-effort locator: for each line_number, try to find where its own tag
    text sits on the given page image.

    Args:
        img: PIL Image of the already-rendered page (any mode; converted to
             RGB internally since pytesseract expects that).
        line_numbers: list of line_number strings already extracted for this
             page (from the UNCHANGED core extraction — read-only input).
        normalize_fn: optional callable (e.g. bound `_normalize_ocr_text`)
             applied to both OCR text and target line numbers before
             comparison, to stay consistent with the extractor's own
             O→0 normalization rules. Falls back to `str.upper()` if omitted.

    Returns:
        Dict[line_number] = {'x_pct': float, 'y_pct': float, 'confidence': 'high'|'medium'}
        Empty dict on any failure or if pytesseract is unavailable. Never raises.
    """
    if not PYTESSERACT_AVAILABLE or not line_numbers:
        return {}

    norm = normalize_fn if normalize_fn else (lambda s: (s or '').upper())

    try:
        width, height = img.size
        if width <= 0 or height <= 0:
            return {}

        rgb_img = img.convert('RGB') if img.mode != 'RGB' else img
        data = pytesseract.image_to_data(rgb_img, output_type=Output.DICT)
        clusters = _build_clusters(data)
        if not clusters:
            return {}

        # Pre-compute normalized cluster texts (joined, no separators AND
        # space-joined) so both spaced and glued line-number renderings match.
        for c in clusters:
            c['norm_joined'] = norm(''.join(c['words']))
            c['norm_spaced'] = norm(' '.join(c['words']))

        results: Dict[str, Dict] = {}
        for raw_line_number in line_numbers:
            target = norm(raw_line_number)
            if not target:
                continue

            best_match = None
            best_confidence = None
            best_ratio = 0.0

            for c in clusters:
                # Exact substring match — highest confidence, preferred path.
                if target in c['norm_joined'] or target in c['norm_spaced']:
                    best_match = c
                    best_confidence = 'high'
                    best_ratio = 1.0
                    break

            if best_match is None:
                # Fuzzy fallback — sliding windows over the cluster's words.
                for c in clusters:
                    words = c['words']
                    for win in range(1, min(TAG_POSITION_MAX_WORDS_WINDOW, len(words)) + 1):
                        for start in range(0, len(words) - win + 1):
                            candidate = norm(''.join(words[start:start + win]))
                            ratio = difflib.SequenceMatcher(None, target, candidate).ratio()
                            if ratio > best_ratio:
                                best_ratio = ratio
                                best_match = c
                if best_match is not None and best_ratio >= TAG_POSITION_FUZZY_THRESHOLD:
                    best_confidence = 'medium'
                else:
                    best_match = None

            if best_match is None:
                continue

            center_x = (best_match['left'] + best_match['right']) / 2.0
            center_y = (best_match['top'] + best_match['bottom']) / 2.0
            results[raw_line_number] = {
                'x_pct': round((center_x / width) * 100.0, 3),
                'y_pct': round((center_y / height) * 100.0, 3),
                'confidence': best_confidence,
            }

        return results

    except Exception as e:
        logger.warning(f"⚠️ locate_tag_positions failed (non-fatal, best-effort): {e}")
        return {}
