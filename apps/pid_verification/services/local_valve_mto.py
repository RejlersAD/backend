"""Local, review-only valve take-off from workbook pictures and spatial OCR.

Coordinates are in displayed PDF points, respecting page rotation.
One row represents one physical symbol match,
not a text reference or a procurement-approved quantity.
"""
from __future__ import annotations

import base64
import io
import logging
import math
import re
from dataclasses import dataclass

import pymupdf
import numpy as np
from PIL import Image
logger = logging.getLogger(__name__)


SYMBOL_DPI = 150
OCR_DPI = 300
OCR_TILE = 2400
OCR_OVERLAP = 400
MATCH_THRESHOLD = 0.90
MATCH_WIDTHS = (12, 16, 20, 24, 30, 38, 48)
REVIEW_WARNING = (
    'Local symbol matches are an engineering-review draft, not an exhaustive or approved MTO. '
    'All visible scope is included, including existing valves. Verify each match, missing valves, '
    'line association, scope and specifications before procurement.'
)
LINE_PATTERN = re.compile(
    r'(?<![A-Z0-9])(\d+(?:[-/]\d+)?["\u201d]?-(?:[A-Z0-9]+-){2,}[A-Z0-9]+)(?![A-Z0-9])'
)
TAG_PATTERN = re.compile(r'^(?:[A-Z]{1,3}V|[A-Z]{1,2}XV)-\d{2,}(?:-[A-Z0-9]+)*$')
LINE_CHARACTERS = str.maketrans({
    **dict.fromkeys('\u2010\u2011\u2012\u2013\u2014\u2212', '-'),
    **dict.fromkeys('\u201c\u201d\u2033', '"'),
})


def _extract_class_from_line_number(line_number):
    """Fallback positional parser for PIPING_CLASS, used only when
    specification_lookup.enrich_specifications' own lookup-based match
    found nothing (e.g. the user has no active legend entry registered
    for this exact code yet). Handles two line-number layouts seen across
    projects/drawings \u2014 in both, the piping class ends up at the 3rd
    '-'-separated segment:

    Format 1 \u2014 SIZE-SERVICE-CLASS-NUMBER[-P]:
    6"-PL-DC3N-8166-P, 2"-FL-AC6N-8022, 4"-PL-DC3N-8052-P
    tokens[0] is the size (has a digit) and tokens[1] the 2-letter
    service code.

    Format 2 \u2014 SERVICE-SIZE-CLASS-AREA-NUMBER-N:
    IA-3"-AS1A0A-FA-1003-N, DR-2"-AS1A0A-FA-1018-N
    tokens[0] is the 2-letter service code (no digit) and tokens[1] the
    size.

    Format 3 \u2014 SERVICE-SEQ-SIZE-CLASS[-INSUL]:
    P-55027-12"-33460-C, P-U51316-8"-PA0D1, P-51524A-1"-PA0D-C
    tokens[0] service, tokens[1] sequence (optional letter prefix/suffix),
    tokens[2] size, class at tokens[3]. The class may be digit-only here
    (e.g. 33460) \u2014 the surrounding shape is strict enough that this is a
    read, not a guess.

    Returns None (never raises, never guesses a class from an unrelated
    format) whenever the line number doesn't match either shape or the
    candidate segment doesn't look like a real class code."""
    tokens = [t.strip() for t in str(line_number or '').upper().split('-') if t.strip()]
    if len(tokens) < 3:
        return None
    if (len(tokens) >= 4
            and re.fullmatch(r'[A-Z]{1,4}', tokens[0])
            and re.fullmatch(r'[A-Z]?\d{3,6}[A-Z]?', tokens[1])
            and re.fullmatch(r'\d{1,2}(?:-\d/\d)?["\u2033]?', tokens[2])):
        class_candidate = tokens[3]
        if re.fullmatch(r'[A-Z0-9]{3,7}', class_candidate):
            return class_candidate
        return None
    if any(ch.isdigit() for ch in tokens[0]):
        # Format 1: size first, then the 2-letter service code.
        size_token, service_token = tokens[0], tokens[1]
    else:
        # Format 2/3: 2-letter service code first, then the size.
        service_token, size_token = tokens[0], tokens[1]
    class_candidate = tokens[2]
    if not any(ch.isdigit() for ch in size_token):
        return None
    if not re.fullmatch(r'[A-Z]{2}', service_token):
        return None
    if not (2 <= len(class_candidate) <= 6):
        return None
    if not any(ch.isalpha() for ch in class_candidate):
        return None
    if not any(ch.isdigit() for ch in class_candidate):
        return None
    if class_candidate.isdigit():
        return None
    return class_candidate


@dataclass(frozen=True)
class TextBox:
    text: str
    box: tuple[float, float, float, float]


def unrotate_box(box, angle, width, height):
    """Map a clockwise-rotated tile rectangle to the original tile."""
    x0, y0, x1, y1 = box
    if angle == 90:
        return y0, height - x1, y1, height - x0
    if angle == 180:
        return width - x1, height - y1, width - x0, height - y0
    if angle == 270:
        return width - y1, x0, width - y0, x1
    return x0, y0, x1, y1


def _load_references(user_id):
    from apps.valve_mto.models import ValveMTOLegend

    references = []
    # FIX 1 — scope_symbols/limit_line added alongside valve/
    # control_valve_regulator so their own imported pictures (area-
    # boundary markers) are loaded as references too, not just the
    # valve/regulator symbol pictures.
    for legend in ValveMTOLegend.objects.filter(
        created_by_id=user_id, is_active=True,
        section__in=('valve', 'control_valve_regulator', 'scope_symbols', 'limit_line'),
    ):
        for sheet in legend.definition.get('imported_sheets', []):
            if sheet['name'].lower() in ('esd valve types', 'motor & valve control types', 'typical assemblies'):
                continue
            for picture in sheet.get('images', []):
                name = picture['name'].upper()
                # FIX 2 — the VALVE/REGULATOR name filter only makes
                # sense for the valve/control_valve_regulator sections
                # (it exists to reject non-valve pictures that leaked
                # into an imported valve-type sheet). scope_symbols/
                # limit_line pictures are area-boundary markers, not
                # valves, and would never contain those words — keep
                # every picture from those two sections unfiltered.
                if legend.section in ('valve', 'control_valve_regulator'):
                    if not re.search(r'\b(?:VALVE|REGULATOR)\b', name):
                        continue
                # scope_symbols and limit_line: keep all images
                url = picture.get('data_url', '')
                if not url.startswith('data:image/png;base64,'):
                    continue
                data = base64.b64decode(url.split(',', 1)[1], validate=True)
                with Image.open(io.BytesIO(data)) as image:
                    references.append((name, np.asarray(image.convert('RGB'))))
    return references


def embedded_text_boxes(page):
    return [TextBox(word[4], tuple(pymupdf.Rect(word[:4]) * page.rotation_matrix))
            for word in page.get_text('words')]

def drawing_title_from_boxes(boxes, width, height):
    """Read the labelled title cell in displayed coordinates, on either side."""
    bottom = [word for word in boxes if word.box[1] > height * 0.65]
    titles = set()
    for label in bottom:
        if not re.fullmatch(r'(?:DRG\.?\s*|DWG\.?\s*|DRAWING\s*)?TITLE[:.]?', label.text, re.I):
            continue
        x0, y0, x1, y1 = label.box
        below = [word.box[1] for word in bottom
                 if word.box[1] > y1 and abs(word.box[0] - x0) < width * 0.04
                 and re.fullmatch(r'(?:DWG|DRG|DRAWING|SCALE|PROJECT|DATE|REV)\.?[:.]?', word.text, re.I)]
        lower = min(below) if below else min(height, y1 + height * 0.06)
        dividers = [word.box[0] for word in bottom
                    if word.box[0] > x1 and y0 <= word.box[1] < lower
                    and re.fullmatch(r'(?:SHEET|REV(?:ISION)?|SCALE|DATE)[:.]?', word.text, re.I)]
        right = min(dividers) if dividers else min(width, x0 + width * 0.4)
        values = [word for word in bottom if word is not label
                  and word.box[0] >= x0 - width * 0.025 and word.box[2] <= right
                  and (word.box[1] + word.box[3]) / 2 < lower and word.box[3] > y0
                  and (word.box[1] > y1 or word.box[0] > x1 + 2)
                  and not re.fullmatch(r'(?:DRG|DWG|DRAWING)\.?', word.text, re.I)]
        lines = []
        for word in sorted(values, key=lambda item: (item.box[1], item.box[0])):
            center = (word.box[1] + word.box[3]) / 2
            line = next((line for line in lines if abs(line[0] - center) < max(3, (word.box[3] - word.box[1]) / 2)), None)
            if line is None:
                line = [center, []]
                lines.append(line)
            line[1].append(word)
        title = ' '.join(' '.join(word.text for word in sorted(line[1], key=lambda word: word.box[0]))
                         for line in lines).strip()
        if title:
            titles.add(title)
    if len(titles) > 1:
        logger.warning('[ValveMTO] Conflicting drawing title cells; title requires review.')
    return next(iter(titles)) if len(titles) == 1 else ''


def drawing_title(page, boxes=None):
    boxes = embedded_text_boxes(page) if boxes is None else boxes
    title = drawing_title_from_boxes(boxes, page.rect.width, page.rect.height)
    if title:
        return title
    from . import piping_valve_mto_extractor as extractor
    if not (extractor.VALVE_MTO_OCR_ENABLED and extractor.PYTESSERACT_AVAILABLE):
        logger.warning('[ValveMTO] Drawing title unavailable in PDF text and title-block OCR is unavailable.')
        return ''
    pytesseract = extractor.pytesseract
    titles = set()
    for left, right in ((0, page.rect.width / 2), (page.rect.width / 2, page.rect.width)):
        clip = pymupdf.Rect(left, page.rect.height * 0.7, right, page.rect.height)
        zoom = 240 / 72
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)
        image = extractor._enhance_native_drawing(Image.frombytes('RGB', (pix.width, pix.height), pix.samples))
        for angle in (0, 90, 180, 270):
            try:
                data = pytesseract.image_to_data(
                    image.rotate(-angle, expand=True), config='--psm 11',
                    output_type=pytesseract.Output.DICT, timeout=30,
                )
            except (RuntimeError, pytesseract.TesseractError, pytesseract.TesseractNotFoundError):
                logger.exception('[ValveMTO] Title-block OCR failed at rotation %d.', angle)
                continue
            words = []
            for i, text in enumerate(data['text']):
                if not text.strip() or float(data['conf'][i]) < 30:
                    continue
                box = unrotate_box((data['left'][i], data['top'][i],
                                    data['left'][i] + data['width'][i], data['top'][i] + data['height'][i]),
                                   angle, pix.width, pix.height)
                words.append(TextBox(text, tuple(value / zoom + (clip.x0 if j % 2 == 0 else clip.y0)
                                                  for j, value in enumerate(box))))
            value = drawing_title_from_boxes(words, page.rect.width, page.rect.height)
            if value:
                titles.add(value)
    if len(titles) != 1:
        logger.warning('[ValveMTO] Title-block OCR did not identify one unambiguous DRG. TITLE.')
        return ''
    return next(iter(titles))


def vector_pipe_network(page, shape, cv):
    """Trace axis-aligned CAD segments in the displayed page coordinate frame."""
    network = np.zeros(shape, dtype='uint8')
    scale = SYMBOL_DPI / 72
    for path in page.get_drawings():
        color = path.get('color') or path.get('fill')
        if color and (max(color) - min(color) > 0.15 or min(color) >= 0.99):
            continue
        for item in path['items']:
            if item[0] != 'l':
                continue
            first, second = item[1] * page.rotation_matrix, item[2] * page.rotation_matrix
            dx, dy = abs(first.x - second.x), abs(first.y - second.y)
            if max(dx, dy) < 3 or min(dx, dy) > 0.3:
                continue
            cv.line(network, (round(first.x * scale), round(first.y * scale)),
                    (round(second.x * scale), round(second.y * scale)), 255, 2)
    return network


def _ink(rgb, cv):
    # Ignore coloured revision/scope clouds instead of matching their linework.
    neutral = rgb.max(axis=2).astype(int) - rgb.min(axis=2).astype(int) < 35
    return ((cv.cvtColor(rgb, cv.COLOR_RGB2GRAY) < 253) & neutral).astype('uint8') * 255


def _reference_mask(name, rgb, cv):
    ink = _ink(rgb, cv)
    points = cv.findNonZero(ink)
    if points is None:
        raise ValueError(f'Legend symbol "{name}" is blank.')
    x, y, width, height = cv.boundingRect(points)
    ink = ink[y:y + height, x:x + width]
    # Manual reference pictures include two pipe breaks/flanges. They are
    # illustration context, not part of the valve body to locate on a pipe.
    if re.match(r'^(?:GATE|GLOBE|BALL|PLUG|CHECK|BUTTERFLY|NEEDLE|DIAPHRAGM) VALVE$', name):
        ink = ink[:, int(width * 0.31):math.ceil(width * 0.69)]
        points = cv.findNonZero(ink)
        if points is not None:
            x, y, width, height = cv.boundingRect(points)
            ink = ink[y:y + height, x:x + width]
    return ink


def _overlap(a, b):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    intersect = max(0, min(ax1, bx1) - max(ax0, bx0)) * max(0, min(ay1, by1) - max(ay0, by0))
    smaller = min((ax1 - ax0) * (ay1 - ay0), (bx1 - bx0) * (by1 - by0))
    return intersect / max(smaller, 1)


def deduplicate_matches(matches):
    kept = []
    for match in sorted(matches, key=lambda item: (item.get('method') == 'geometry', item['score']), reverse=True):
        duplicate = next((other for other in kept if _overlap(match['box'], other['box']) > 0.35), None)
        if duplicate is None:
            kept.append({**match, 'alternatives': []})
        elif match['name'] != duplicate['name'] and duplicate['score'] - match['score'] < 0.035:
            if match['name'] not in duplicate['alternatives']:
                duplicate['alternatives'].append(match['name'])
    return sorted(kept, key=lambda item: (item['box'][1], item['box'][0]))


def _filled_bowties(rgb, references, cv):
    """Find opposing triangular valve lobes despite reference raster/fill differences.

    Geometry establishes an observation, not a specific valve type. A matching
    legend family must exist; unmatched types remain UNIDENTIFIED.
    """
    if not any(re.match(r'^(GATE|GLOBE|BALL|PLUG|NEEDLE) VALVE$', name) for name, _ in references):
        return []
    dark = (cv.cvtColor(rgb, cv.COLOR_RGB2GRAY) < 130).astype('uint8') * 255
    dark = cv.morphologyEx(dark, cv.MORPH_OPEN, np.ones((2, 2), dtype='uint8'))
    contours, _ = cv.findContours(dark, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE)
    triangles = []
    for contour in contours:
        x, y, width, height = cv.boundingRect(contour)
        area = cv.contourArea(contour)
        if not (12 <= area <= 500 and 5 <= width <= 36 and 5 <= height <= 36
                and 0.25 <= area / (width * height) <= 0.8):
            continue
        mask = np.zeros((height, width), dtype='uint8')
        cv.drawContours(mask, [contour - np.array([[[x, y]]])], -1, 1, cv.FILLED)
        xp, yp = mask.sum(axis=0), mask.sum(axis=1)
        xa, xb = xp[:max(1, width // 3)].mean(), xp[-max(1, width // 3):].mean()
        ya, yb = yp[:max(1, height // 3)].mean(), yp[-max(1, height // 3):].mean()
        ratios = [xa / max(xb, 1), xb / max(xa, 1), ya / max(yb, 1), yb / max(ya, 1)]
        if max(ratios) >= 1.6:
            direction = ('right', 'left', 'down', 'up')[int(np.argmax(ratios))]
            triangles.append(((x, y, x + width, y + height), direction))
    ink = _ink(rgb, cv)
    matches = []
    for (x0, y0, x1, y1), direction in triangles:
        if direction not in ('right', 'down'):
            continue
        width, height = x1 - x0, y1 - y0
        for (a, b, c, d), opposite in triangles:
            horizontal = (direction == 'right' and opposite == 'left'
                          and abs((y0 + y1 - b - d) / 2) < 3
                          and -2 <= a - x1 <= height * 1.2 and 0.7 <= (d - b) / height <= 1.3)
            vertical = (direction == 'down' and opposite == 'up'
                        and abs((x0 + x1 - a - c) / 2) < 3
                        and -2 <= b - y1 <= width * 1.2 and 0.7 <= (c - a) / width <= 1.3)
            if not horizontal and not vertical:
                continue
            box = ((x0, min(y0, b), c, max(y1, d)) if horizontal
                   else (min(x0, a), y0, max(x1, c), d))
            bx0, by0, bx1, by1 = box
            if min(bx1 - bx0, by1 - by0) < 10:
                continue
            cx, cy = (bx0 + bx1) // 2, (by0 + by1) // 2
            if horizontal:
                first = ink[max(0, cy - 2):cy + 3, max(0, bx0 - 14):max(0, bx0 - 3)]
                second = ink[max(0, cy - 2):cy + 3, bx1 + 3:bx1 + 14]
            else:
                first = ink[max(0, by0 - 14):max(0, by0 - 3), max(0, cx - 2):cx + 3]
                second = ink[by1 + 3:by1 + 14, max(0, cx - 2):cx + 3]
            if max(np.count_nonzero(first), np.count_nonzero(second)) < 3:
                continue
            matches.append({'name': 'UNIDENTIFIED', 'score': 0.0, 'box': box,
                            'horizontal': bool(horizontal), 'method': 'geometry'})
    return matches


def detect_symbols(rgb, references, cv, on_progress=None):
    ink = _ink(rgb, cv)
    dark = (cv.cvtColor(rgb, cv.COLOR_RGB2GRAY) < 180).astype('uint8') * 255
    opened = cv.morphologyEx(dark, cv.MORPH_OPEN, np.ones((2, 2), dtype='uint8'))
    count, _, stats, _ = cv.connectedComponentsWithStats(opened)
    regions = []
    for values in stats[1:count]:
        x, y, width, height, area = map(int, values)
        if 8 <= area and 3 <= width <= 100 and 3 <= height <= 100:
            cx, cy = x + width // 2, y + height // 2
            left = ink[max(0, cy - 3):cy + 4, max(0, x - 15):x]
            right = ink[max(0, cy - 3):cy + 4, x + width:x + width + 15]
            above = ink[max(0, y - 15):y, max(0, cx - 3):cx + 4]
            below = ink[y + height:y + height + 15, max(0, cx - 3):cx + 4]
            horizontal = left.shape == right.shape and np.any(
                np.minimum((left > 0).sum(axis=1), (right > 0).sum(axis=1)) >= 9)
            vertical = above.shape == below.shape and np.any(
                np.minimum((above > 0).sum(axis=0), (below > 0).sum(axis=0)) >= 9)
            if not horizontal and not vertical:
                continue
            regions.append((max(0, x - 55), max(0, y - 55),
                            min(ink.shape[1], x + width + 55), min(ink.shape[0], y + height + 55)))
    # Large solid annotation/logo areas are never valve candidates.
    if len(regions) > 10000:
        raise ValueError('Too many local symbol candidates; split this drawing into smaller sheets for review.')

    matches = _filled_bowties(rgb, references, cv)
    for index, (name, picture) in enumerate(references):
        reference = _reference_mask(name, picture, cv)
        for width in MATCH_WIDTHS:
            height = round(reference.shape[0] * width / reference.shape[1])
            if min(width, height) < 10:
                continue
            resized = cv.resize(reference, (width, height), interpolation=cv.INTER_NEAREST)
            if resized.std() < 10:
                continue
            for angle in range(4):
                template = np.ascontiguousarray(np.rot90(resized, angle))
                th, tw = template.shape
                for rx0, ry0, rx1, ry1 in regions:
                    region = ink[ry0:ry1, rx0:rx1]
                    if region.shape[0] <= th or region.shape[1] <= tw:
                        continue
                    scores = cv.matchTemplate(region, template, cv.TM_CCOEFF_NORMED)
                    _, score, _, point = cv.minMaxLoc(scores)
                    if score < MATCH_THRESHOLD:
                        continue
                    x, y = point[0] + rx0, point[1] + ry0
                    # Require pipe evidence on both ends; a similar glyph in
                    # a note or document reference is not a valve observation.
                    horizontal = angle % 2 == 0
                    if horizontal:
                        left = ink[max(0, y + th // 2 - 3):y + th // 2 + 4, max(0, x - 10):x]
                        right = ink[max(0, y + th // 2 - 3):y + th // 2 + 4, x + tw:x + tw + 10]
                    else:
                        left = ink[max(0, y - 10):y, max(0, x + tw // 2 - 3):x + tw // 2 + 4]
                        right = ink[y + th:y + th + 10, max(0, x + tw // 2 - 3):x + tw // 2 + 4]
                    if np.count_nonzero(left) < 5 or np.count_nonzero(right) < 5:
                        continue
                    matches.append({
                        'name': name, 'score': float(score), 'box': (x, y, x + tw, y + th),
                        'horizontal': horizontal,
                    })
        if on_progress:
            on_progress(index + 1, len(references))
    return deduplicate_matches(matches)


def _ocr_page(page, pytesseract, cv, scan_mode, on_progress=None):
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(OCR_DPI / 72, OCR_DPI / 72), alpha=False)
    rgb = np.frombuffer(pixmap.samples, dtype='uint8').reshape(pixmap.height, pixmap.width, 3)
    binary = 255 - _ink(rgb, cv)
    step = OCR_TILE - OCR_OVERLAP
    tiles = [(x, y) for y in range(0, pixmap.height, step) for x in range(0, pixmap.width, step)]
    rotations = (0, 90) if scan_mode == 'quick' else (0, 90, 180, 270)
    result = []
    completed = 0
    total = len(tiles) * (len(rotations) + 1)
    for x, y in tiles:
        tile = binary[y:y + OCR_TILE, x:x + OCR_TILE]
        for angle, psm in [(rotation, 11) for rotation in rotations] + [(0, 6)]:
            rotated = np.ascontiguousarray(np.rot90(tile, -(angle // 90)))
            data = pytesseract.image_to_data(rotated, config=f'--psm {psm}',
                                           output_type=pytesseract.Output.DICT, timeout=60)
            lines = {}
            for i, text in enumerate(data['text']):
                if not text.strip() or float(data['conf'][i]) < 30:
                    continue
                key = (data['block_num'][i], data['par_num'][i], data['line_num'][i])
                box = (data['left'][i], data['top'][i],
                       data['left'][i] + data['width'][i], data['top'][i] + data['height'][i])
                lines.setdefault(key, []).append((text, box))
            for words in lines.values():
                text = ' '.join(word[0] for word in words)
                box = (min(word[1][0] for word in words), min(word[1][1] for word in words),
                       max(word[1][2] for word in words), max(word[1][3] for word in words))
                original = unrotate_box(box, angle, tile.shape[1], tile.shape[0])
                pdf_box = tuple((value + (x if j % 2 == 0 else y)) * 72 / OCR_DPI
                                for j, value in enumerate(original))
                result.append(TextBox(text, pdf_box))
                # Preserve individual annotations without the line's nearby
                # notes so a size/tag does not acquire a misleading rectangle.
                for word, word_box in words:
                    original = unrotate_box(word_box, angle, tile.shape[1], tile.shape[0])
                    result.append(TextBox(word, tuple(
                        (value + (x if j % 2 == 0 else y)) * 72 / OCR_DPI
                        for j, value in enumerate(original)
                    )))
            completed += 1
            if on_progress:
                on_progress(completed, total)
    return result


def _distance(a, b):
    return math.hypot((a[0] + a[2] - b[0] - b[2]) / 2, (a[1] + a[3] - b[1] - b[3]) / 2)


def _line_evidence(boxes):
    labels = []
    for item in boxes:
        normalized = re.sub(r'\s*-\s*', '-', item.text.upper().translate(LINE_CHARACTERS))
        for match in LINE_PATTERN.finditer(normalized):
            label = match.group(1)
            if not any(other.text == label and _overlap(other.box, item.box) > 0.2 for other in labels):
                labels.append(TextBox(label, item.box))
    return labels


def associate_line(match, lines, pipe_labels, cv):
    """Only suggest a nearby label connected to the same traced line network."""
    scale = SYMBOL_DPI / 72
    box = match['box']
    x, y = round((box[0] + box[2]) / 2), round((box[1] + box[3]) / 2)
    radius = 8
    networks = set(np.unique(pipe_labels[max(0, y - radius):y + radius + 1,
                                         max(0, x - radius):x + radius + 1])) - {0}
    candidates = []
    pdf_box = tuple(value / scale for value in box)
    for label in lines:
        if _distance(pdf_box, label.box) > 180:
            continue
        lx0, ly0, lx1, ly1 = label.box
        if (lx1 - lx0 >= ly1 - ly0) != match['horizontal']:
            continue
        center = ((pdf_box[1] + pdf_box[3]) / 2 if match['horizontal']
                  else (pdf_box[0] + pdf_box[2]) / 2)
        start, end = (ly0, ly1) if match['horizontal'] else (lx0, lx1)
        if max(start - center, center - end, 0) > 18:
            continue
        lx0, ly0, lx1, ly1 = (round(value * scale) for value in label.box)
        margin = round(18 * scale)
        label_networks = set(np.unique(pipe_labels[
            max(0, ly0 - margin):min(pipe_labels.shape[0], ly1 + margin),
            max(0, lx0 - margin):min(pipe_labels.shape[1], lx1 + margin),
        ])) - {0}
        if networks & label_networks:
            candidates.append((_distance(pdf_box, label.box), label.text))
    candidates.sort()
    if not candidates:
        return '', 'Line association not established.'
    nearest = candidates[0]
    if any(text != nearest[1] and distance < nearest[0] + 25 for distance, text in candidates[1:]):
        return '', 'Multiple connected line labels nearby; line association is ambiguous.'
    return nearest[1], 'Aligned connected-line OCR association requires engineering verification.'


def build_rows(matches, boxes, rgb, cv, page_number, pid_number='', class_codes=None, vector_network=None):
    ink = _ink(rgb, cv)
    network = cv.bitwise_or(
        cv.morphologyEx(ink, cv.MORPH_OPEN, np.ones((1, 25), dtype='uint8')),
        cv.morphologyEx(ink, cv.MORPH_OPEN, np.ones((25, 1), dtype='uint8')),
    )
    if vector_network is not None:
        network = cv.bitwise_or(network, vector_network)
    for match in matches:
        x0, y0, x1, y1 = match['box']
        if match['horizontal']:
            cv.line(network, (x0 - 3, (y0 + y1) // 2), (x1 + 3, (y0 + y1) // 2), 255, 2)
        else:
            cv.line(network, ((x0 + x1) // 2, y0 - 3), ((x0 + x1) // 2, y1 + 3), 255, 2)
    network = cv.morphologyEx(network, cv.MORPH_CLOSE, np.ones((3, 3), dtype='uint8'))
    _, pipe_labels = cv.connectedComponents(network)
    lines = _line_evidence(boxes)
    rows = []
    for match in matches:
        pdf_box = tuple(round(value * 72 / SYMBOL_DPI, 1) for value in match['box'])
        line, association_note = associate_line(match, lines, pipe_labels, cv)
        close_text = [item for item in boxes if _distance(pdf_box, item.box) < 35]
        tags = sorted({item.text.upper() for item in close_text if TAG_PATTERN.fullmatch(item.text.upper())})
        sizes = sorted({item.text.replace('\u201d', '"') for item in close_text
                        if re.fullmatch(r'\d+(?:[ /-]\d+)?["\u201d]', item.text)})
        alternatives = match['alternatives']
        evidence = ('Opposing triangular valve lobes observed; reference raster differs, valve type unconfirmed.'
                    if match.get('method') == 'geometry'
                    else f'Local legend match "{match["name"]}" (image similarity {match["score"]:.3f}).')
        notes = [
            f'REVIEW: {evidence}',
            association_note, 'Observed quantity only; existing/new scope and procurement suitability unverified.',
        ]
        if alternatives:
            notes.append('Valve type ambiguous with: ' + ', '.join(alternatives) + '.')
        if len(sizes) > 1:
            notes.append('Multiple nearby sizes; size not assigned.')
        if len(tags) > 1:
            notes.append('Multiple nearby tags; tag not assigned.')
        classes = [code for code in (class_codes or {}) if code in line.split('-')]
        piping_class = classes[0] if len(classes) == 1 else ''
        rows.append({
            'type': 'UNIDENTIFIED' if alternatives else match['name'][:128],
            'description': 'Filled bow-tie valve symbol' if match.get('method') == 'geometry' else match['name'],
            'valve_tag': tags[0] if len(tags) == 1 else '',
            'size_1': sizes[0] if len(sizes) == 1 else '',
            'line_number': line, 'pid_number': pid_number,
            'piping_class': piping_class, 'pms_class': (class_codes or {}).get(piping_class, ''),
            'rating': '', 'facing': '',
            'area': '', 'qty_island': 0, 'qty_field': 0, 'qty_observed': 1, 'unit': 'NOS',
            'confidence': 'LOW',
            'location_hint': f'Page {page_number} [{", ".join(str(value) for value in pdf_box)}] pt',
            'remarks': ' '.join(notes),
        })
    return rows


def extract_local_valve_mto(pdf_path, *, user_id=None, scan_mode='thorough',
                            on_progress=None, on_partial=None):
    try:
        import cv2
        import pytesseract
    except ImportError as exc:
        logger.exception('Local Valve MTO dependencies unavailable')
        return {'status': 'error', 'error': f'Local extraction dependency unavailable: {exc.name}.',
                'rows': [], 'warnings': [], 'engine': 'local-ocr+symbols'}
    warnings = [REVIEW_WARNING]
    rows = []
    meta = {}
    try:
        references = _load_references(user_id)
        if not references:
            raise ValueError(
                'Import and activate a complete Excel legend workbook in Valve MTO > Manage Legends first. '
                'Local extraction needs the actual valve symbol pictures, not only text definitions.'
            )
        warnings.append(
            f'Using {len(references)} individual valve/regulator/scope/limit reference pictures; '
            'non-valve equipment and composite assembly pictures are excluded from detection.'
        )
        from apps.valve_mto.specification_lookup import load_specification_lookups, enrich_specifications
        specifications = load_specification_lookups(user_id)
        class_codes = specifications['pms_class']
        with pymupdf.open(pdf_path) as document:
            page_count = len(document)
            for page_index, page in enumerate(document):
                progress = (lambda current, total: on_progress(
                    page_index * 1000 + round(current / max(total, 1) * 1000),
                    page_count * 1000, len(rows),
                )) if on_progress else None
                embedded = embedded_text_boxes(page)
                sheet_title = drawing_title(page, embedded)
                if not sheet_title:
                    warnings.append(f'Page {page_index + 1}: DRG. TITLE could not be read; AREA requires review.')
                if page_index == 0 and sheet_title:
                    meta['doc_title'] = sheet_title
                # Prefer an identifier physically in the bottom-right title
                # block, never an OCR reference-table document number.
                drawing_numbers = [
                    item.text for item in embedded
                    if item.box[0] > page.rect.width * 0.65 and item.box[1] > page.rect.height * 0.90
                    and re.fullmatch(r'[A-Z0-9]+(?:-[A-Z0-9]+)+-\d+[A-Z]?', item.text)
                ]
                pid_number = drawing_numbers[0] if len(set(drawing_numbers)) == 1 else ''
                if page_index == 0 and pid_number:
                    meta['doc_no'] = pid_number
                pix = page.get_pixmap(matrix=pymupdf.Matrix(SYMBOL_DPI / 72, SYMBOL_DPI / 72), alpha=False)
                rgb = np.frombuffer(pix.samples, dtype='uint8').reshape(pix.height, pix.width, 3)
                matches = detect_symbols(rgb, references, cv2,
                                         on_progress=(lambda c, t: progress(c, t * 5)) if progress else None)
                if not matches:
                    warnings.append(f'Page {page_index + 1}: no symbol match passed local checks; completeness is unverified.')
                    if progress:
                        progress(1, 1)
                    continue
                boxes = _ocr_page(
                    page, pytesseract, cv2, scan_mode,
                    on_progress=(lambda c, t: progress(t + c * 4, t * 5)) if progress else None,
                )
                page_rows = build_rows(
                    matches, embedded + boxes, rgb, cv2, page_index + 1, pid_number, class_codes,
                    vector_network=vector_pipe_network(page, rgb.shape[:2], cv2),
                )
                page_rows = [enrich_specifications({**row, 'area': sheet_title}, specifications) for row in page_rows]
                # FIX 2 — positional fallback when the legend-lookup match
                # above found nothing (no active legend entry yet for this
                # exact class code).
                for row in page_rows:
                    if not row.get('piping_class'):
                        extracted = _extract_class_from_line_number(row.get('line_number', ''))
                        if extracted:
                            row['piping_class'] = extracted
                            existing_remarks = row.get('remarks', '')
                            row['remarks'] = (
                                existing_remarks + ' (PMS class from line number)'
                            ).strip()
                rows.extend(page_rows)
                warnings.append(f'Page {page_index + 1}: {len(page_rows)} review candidates, not a verified total valve count.')
                logger.info('[ValveMTO local] page=%d references=%d matches=%d OCR_boxes=%d',
                            page_index + 1, len(references), len(page_rows), len(boxes))
                if on_partial:
                    on_partial(rows, meta)
            return {'status': 'ok' if rows else 'empty', 'engine': 'local-ocr+symbols',
                    'page_count': page_count, 'rows': rows, 'project_meta': meta, 'warnings': warnings}
    except Exception as exc:
        logger.exception('Local Valve MTO extraction failed')
        # Do not persist a partly scanned drawing as a completed MTO.
        return {'status': 'error', 'error': f'Local valve extraction failed: {exc}',
                'engine': 'local-ocr+symbols', 'rows': [], 'project_meta': meta, 'warnings': warnings}
