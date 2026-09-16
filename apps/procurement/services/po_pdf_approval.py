"""Read buyer sign-off candidates from at most two purchase-order cover pages.

Names, titles and dates are literal source text. Ink flags identify candidates,
not verified signatures, stamps, identities, or authorization to approve a PO.
"""

from datetime import datetime
import hashlib
import re

import numpy as np
from PIL import Image
import pymupdf
from scipy.ndimage import binary_opening, find_objects, label


MAX_COVER_PAGES = 2
OCR_DPI = 200
TITLE_WORDS = re.compile(r'\b(?:vice\s+president|president|CEO|chief|director|manager|officer|head|authorized\s+signatory)\b', re.I)
FIELD_LABEL = re.compile(r'^(?:date|signature|stamp|approved\s+by|name|order\s+confirmation|seller|phone|fax|email|www\.)\b', re.I)


class POApprovalPreviewError(ValueError):
    pass


def _text(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def _bounds(words):
    return [min(item['bbox'][0] for item in words), min(item['bbox'][1] for item in words),
            max(item['bbox'][2] for item in words), max(item['bbox'][3] for item in words)]


def _lines(words, clip=None):
    selected = [word for word in words if not clip or (
        clip[0] <= (word['bbox'][0] + word['bbox'][2]) / 2 <= clip[2]
        and clip[1] <= (word['bbox'][1] + word['bbox'][3]) / 2 <= clip[3]
    )]
    rows = []
    for word in sorted(selected, key=lambda item: ((item['bbox'][1] + item['bbox'][3]) / 2, item['bbox'][0])):
        centre = (word['bbox'][1] + word['bbox'][3]) / 2
        tolerance = max(2, (word['bbox'][3] - word['bbox'][1]) * 0.4)
        if not rows or abs(centre - rows[-1]['centre']) > tolerance:
            rows.append({'centre': centre, 'words': [word]})
        else:
            rows[-1]['words'].append(word)
    return [{'text': _text(' '.join(word['text'] for word in sorted(row['words'], key=lambda item: item['bbox'][0]))),
             'bbox': _bounds(row['words']), 'words': row['words']} for row in rows]


def _native_words(page):
    return [{'text': word[4], 'bbox': list(word[:4]), 'method': 'native', 'confidence': 100}
            for word in page.get_text('words') if str(word[4]).strip()]


def _ocr_words(page, clip=None, *, dpi=OCR_DPI):
    """One bounded local OCR call; no full-document or external-model extraction."""
    import pytesseract

    rect = pymupdf.Rect(clip) if clip else page.rect
    pix = page.get_pixmap(dpi=dpi, clip=rect, colorspace=pymupdf.csRGB, alpha=False)
    data = pytesseract.image_to_data(
        Image.frombytes('RGB', (pix.width, pix.height), pix.samples),
        lang='eng', config=f'--dpi {dpi} --psm {6 if clip else 3}',
        output_type=pytesseract.Output.DICT, timeout=15,
    )
    scale = 72 / dpi
    words = []
    for index, raw in enumerate(data.get('text', [])):
        text = _text(raw)
        if not text:
            continue
        x = rect.x0 + data['left'][index] * scale
        y = rect.y0 + data['top'][index] * scale
        words.append({'text': text, 'bbox': [x, y, x + data['width'][index] * scale, y + data['height'][index] * scale],
                      'method': 'ocr', 'confidence': float(data['conf'][index])})
    return words


def _anchor(words, pattern):
    for line in _lines(words):
        items = sorted(line['words'], key=lambda word: word['bbox'][0])
        for index in range(len(items)):
            for count in (2, 3, 1):
                candidate = items[index:index + count]
                if len(candidate) == count and re.fullmatch(pattern, ' '.join(word['text'] for word in candidate), re.I):
                    return _bounds(candidate)
    return None


def _buyer_region(page, words):
    header = ' '.join(line['text'] for line in _lines(words, [0, 0, page.rect.width, page.rect.height * 0.4]))
    heading = ' '.join(line['text'] for line in _lines(words, [0, 0, page.rect.width, page.rect.height * 0.15]))
    if not re.search(r'\bPURCHASE\s+ORDER\b', header, re.I) or re.search(r'\b(?:quotation|sales\s+proposal)\b', heading, re.I):
        return None
    approved = _anchor(words, r'(?:Approved\s+by|Buyer\s+approval|Authori[sz]ed\s+by)\s*:?')
    if not approved or approved[0] >= page.rect.width * 0.55:
        return None
    seller = _anchor(words, r'(?:Order\s+Confirmation|Seller\s+Signature)\s*:?')
    right = seller[0] - 8 if seller and seller[0] > approved[2] else page.rect.width * 0.52
    return [max(0, approved[0] - 4), max(0, approved[1] - 1), right, page.rect.height * 0.96]


def _person_name(text):
    text = re.sub(r'^(?:name|approved\s+by)\s*:\s*', '', text, flags=re.I).strip(' ()[]{}|\'"‘’“”')
    tokens = text.split()
    if not 2 <= len(tokens) <= 7 or len(text) > 150 or TITLE_WORDS.search(text) or FIELD_LABEL.search(text):
        return ''
    if re.search(r'\d|[@:/_]|\b(?:rejlers|international|engineering|solutions|company|LLC|FZ|Abu\s+Dhabi|Middle\s+East)\b', text, re.I):
        return ''
    return text if all(re.fullmatch(r"[^\W\d_][^\W\d_.'’-]*\.?", token, re.UNICODE) for token in tokens) else ''


def _literal_date(text):
    text = _text(text).strip(' :')
    for fmt in ('%Y-%m-%d', '%d.%m.%Y', '%d/%m/%Y', '%d-%m-%Y', '%d %B %Y', '%d %b %Y'):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return ''


def _provenance(line, page):
    return {'page': page, 'bbox': [round(number, 2) for number in line['bbox']],
            'method': 'ocr' if any(word['method'] == 'ocr' for word in line['words']) else 'native',
            'text': line['text']}


def _read_identity(words, region, page_number):
    lines = _lines(words, region)
    fields = {'approved_by_name': '', 'approved_by_title': '', 'approved_date': '', 'provenance': {}}
    title_indexes = [index for index, line in enumerate(lines) if TITLE_WORDS.search(line['text']) and not FIELD_LABEL.search(line['text'])]
    name_options = []
    for index in title_indexes:
        if index and (name := _person_name(lines[index - 1]['text'])):
            name_options.append((name, index - 1))
    if not name_options:
        name_options = [(name, index) for index, line in enumerate(lines) if (name := _person_name(line['text']))]
    names = {name for name, _index in name_options}
    if len(names) == 1:
        name, index = name_options[0]
        fields['approved_by_name'] = name
        fields['provenance']['approved_by_name'] = _provenance(lines[index], page_number)
        title_lines = []
        for following in lines[index + 1:index + 4]:
            if FIELD_LABEL.search(following['text']) or following['bbox'][1] - (title_lines[-1]['bbox'][3] if title_lines else lines[index]['bbox'][3]) > 20:
                break
            if re.match(r'^(?:Rejlers\s+International|Rejlers\s+(?:Abu|Middle)|P\.?\s*O\.?\s*Box|Tel\b)', following['text'], re.I):
                break
            if len(re.sub(r'[^A-Za-z]', '', following['text'])) < 3:
                break
            if not title_lines and not TITLE_WORDS.search(following['text']):
                break
            if title_lines and not TITLE_WORDS.search(following['text']) and not title_lines[-1]['text'].endswith((',', '/', '-')):
                break
            title_lines.append(following)
        if title_lines:
            fields['approved_by_title'] = ' / '.join(line['text'] for line in title_lines)
            combined = {'text': fields['approved_by_title'], 'bbox': _bounds(title_lines),
                        'words': [word for line in title_lines for word in line['words']]}
            fields['provenance']['approved_by_title'] = _provenance(combined, page_number)
    dates = []
    for line in lines:
        match = re.match(r'^(?:Approval\s+)?Date\s*:\s*(.*)$', line['text'], re.I)
        if match and (value := _literal_date(match.group(1))):
            value_words = [word for word in line['words'] if re.search(r'\d', word['text'])]
            if all(word['method'] == 'native' or word['confidence'] >= 70 for word in value_words):
                dates.append((value, line))
    if len({value for value, _line in dates}) == 1:
        fields['approved_date'] = dates[0][0]
        fields['provenance']['approved_date'] = _provenance(dates[0][1], page_number)
    return fields


def _ink_candidates(page, region, words, name_bounds=None):
    rect = pymupdf.Rect(region)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=rect, colorspace=pymupdf.csRGB, alpha=False)
    pixels = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    ink = pixels.mean(axis=2) < 180
    for word in words:
        x0, y0, x1, y1 = word['bbox']
        if word['method'] != 'native' and word['confidence'] < 70:
            continue
        left, right = max(0, int((x0 - rect.x0) * 2) - 2), min(pix.width, int((x1 - rect.x0) * 2) + 2)
        top, bottom = max(0, int((y0 - rect.y0) * 2) - 2), min(pix.height, int((y1 - rect.y0) * 2) + 2)
        if right > left and bottom > top:
            ink[top:bottom, left:right] = False
    ink &= ~binary_opening(ink, structure=np.ones((1, max(30, pix.width // 3)), dtype=bool))
    ink &= ~binary_opening(ink, structure=np.ones((max(30, pix.height // 3), 1), dtype=bool))
    labeled, _count = label(ink)
    stamp = False
    for component in find_objects(labeled):
        if component is None:
            continue
        ys, xs = component
        height, width = ys.stop - ys.start, xs.stop - xs.start
        if width >= 45 and height >= 35 and 0.6 <= width / height <= 2.3:
            stamp = True
            break
    signature_end = int((name_bounds[1] - rect.y0) * 2) if name_bounds else int(pix.height * 0.45)
    signing_ink = ink[:max(0, min(pix.height, signature_end))]
    yy, xx = np.where(signing_ink)
    signature = bool(xx.size >= 40 and xx.max() - xx.min() >= 35 and yy.max() - yy.min() >= 8)
    return signature, stamp


def preview_signed_po_approval(pdf_bytes):
    if not isinstance(pdf_bytes, bytes) or not pdf_bytes.startswith(b'%PDF'):
        raise POApprovalPreviewError('Select a valid PDF file.')
    try:
        document = pymupdf.open(stream=pdf_bytes, filetype='pdf')
    except Exception as exc:
        raise POApprovalPreviewError('The PDF could not be read.') from exc
    evidence = {'approved_by_name': '', 'approved_by_title': '', 'approved_date': '',
                'signature_detected': False, 'stamp_detected': False, 'page': None,
                'requires_review': True, 'detection_meaning': 'candidate_only', 'issues': [], 'provenance': {}}
    inspected = []
    try:
        if document.needs_pass:
            raise POApprovalPreviewError('The PDF is password protected.')
        for index in range(min(MAX_COVER_PAGES, len(document))):
            page = document[index]
            inspected.append(index + 1)
            words = _native_words(page)
            region = _buyer_region(page, words)
            if not region and (len(words) < 20 or page.get_images()):
                try:
                    words = _ocr_words(page)
                    region = _buyer_region(page, words)
                except Exception:
                    evidence['issues'].append(f'Page {index + 1}: local OCR was unavailable; review the original PDF.')
            if not region:
                continue
            fields = _read_identity(words, region, index + 1)
            name_evidence = fields['provenance'].get('approved_by_name', {})
            if fields['approved_by_name'] and name_evidence.get('method') == 'ocr':
                # Small printed names can intersect a handwritten signature.
                # Re-read that source line at higher resolution; never replace
                # its text using an employee directory or a known-name list.
                x0, y0, x1, y1 = name_evidence['bbox']
                name_clip = [max(region[0], x0 - 12), max(region[1], y0 - 4),
                             min(region[2], x1 + 8), min(region[3], y1 + 6)]
                try:
                    name_lines = _lines(_ocr_words(page, name_clip, dpi=400))
                    candidates = [(name, line) for line in name_lines if (name := _person_name(line['text']))]
                    if len({name for name, _line in candidates}) == 1:
                        fields['approved_by_name'] = candidates[0][0]
                        fields['provenance']['approved_by_name'] = {
                            **_provenance(candidates[0][1], index + 1), 'refinement': 'source_line_crop',
                        }
                except Exception:
                    evidence['issues'].append('Confirm the approver name spelling against the original PDF.')
            if any(not fields[key] for key in ('approved_by_name', 'approved_by_title', 'approved_date')) and any(word['method'] == 'native' for word in words):
                try:
                    supplemental = _read_identity(_ocr_words(page, region), region, index + 1)
                    for key in ('approved_by_name', 'approved_by_title', 'approved_date'):
                        if not fields[key] and supplemental[key]:
                            fields[key] = supplemental[key]
                            fields['provenance'][key] = supplemental['provenance'][key]
                except Exception:
                    evidence['issues'].append('Some approval text could not be read by local OCR.')
            name_bounds = fields['provenance'].get('approved_by_name', {}).get('bbox')
            signature, stamp = _ink_candidates(page, region, words, name_bounds)
            evidence.update(fields, page=index + 1, signature_detected=signature, stamp_detected=stamp)
            evidence['provenance']['buyer_region'] = {'page': index + 1, 'bbox': region, 'method': 'layout'}
            break
        for field, label_text in (('approved_by_name', 'Approver name'), ('approved_by_title', 'Approver title'), ('approved_date', 'Approval date')):
            if not evidence[field]:
                evidence['issues'].append(f'{label_text} was not reliably readable in the buyer approval block.')
        evidence['issues'].append('Signature and stamp indicators are candidates only. Confirm them against the original PDF before importing.')
        return {'source_sha256': hashlib.sha256(pdf_bytes).hexdigest(), 'page_count': len(document),
                'pages_inspected': inspected, 'approval_evidence': evidence}
    finally:
        document.close()
