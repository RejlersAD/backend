"""Read labeled PO commercial values without mixing columns or later pages."""

from __future__ import annotations

import re

import pymupdf

from .po_pdf_approval import _lines, _native_words, _ocr_words


_FIELD_LABELS = {
    "seller_reference": r"(?:Seller\s+(?:Reference|Ref\.?(?:\s+No\.?)?)|Order\s+Reference)\s*:",
    "payment_terms": r"Payment\s+Terms?\s*:",
    "payment_mode": r"Payment\s+Mode\s*:",
    "delivery_terms": r"Delivery\s+Terms?\s*:",
}
_OTHER_LABELS = (
    r"(?:Seller(?:\s+(?:Address|Country|Name|Contact(?:\s+Person)?|Email|Phone|Fax))?"
    r"|Buyer(?:\s+(?:Reference|Address))?|Invoicing(?:\s+Address)?|Invoice\s+Address"
    r"|Quote\s+Ref(?:erence)?\.?|(?:Trade\s+)?License\s+No\.?"
    r"|(?:Expected\s+)?Delivery\s+Date|Start\s+Date|End\s+Date|Marking|Project"
    r"|Purchase\s+Summary|Total\s+(?:(?:Purchase|Estimated)\s+)?Price|Total\s+Sum"
    r"|VAT(?:\s*\([^)]*\))?|Approved\s+by|Order\s+Confirmation"
    r"|Currency|Attention|P\.?\s*O\.?\s*Box|Date|Email|Phone|Fax)\s*:"
)
_BOUNDARY = re.compile(
    r"\b(?:" + "|".join([*_FIELD_LABELS.values(), _OTHER_LABELS, r"(?:Terms|Mode)\s*:"]) + r")",
    re.I,
)
_PAGE = re.compile(r"---\s*Page\s+\d+\s*---", re.I)
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_FOOTER = re.compile(r"^[ \t]*(?:HOME\s+OF\s+THE|Rejlers\s+International\s+Engineering|www\.)", re.I | re.M)
_LABEL_PATTERNS = [(key, re.compile(pattern, re.I)) for key, pattern in _FIELD_LABELS.items()]
_LABEL_PATTERNS.append(("_boundary", re.compile(_OTHER_LABELS, re.I)))


def _clean(value, field):
    if field == "seller_reference":
        value = _EMAIL.sub("", value)
    return re.sub(r"\s+", " ", value).strip(" \t\r\n|;,")


def _text_fields(text):
    """The first explicit field owns its value, even when that value is blank."""
    fields = {}
    pages = _PAGE.split(str(text or ""))
    # A leading page marker produces an empty segment, not an extra source page.
    if pages and not pages[0].strip():
        pages = pages[1:]
    for page_index, page in enumerate(pages[:4]):
        for field, pattern in _FIELD_LABELS.items():
            if field in fields or (field == "seller_reference" and page_index):
                continue
            start = re.search(r"\b" + pattern, page, re.I)
            if not start:
                continue
            tail = page[start.end():]
            end = _BOUNDARY.search(tail)
            value = tail[:end.start()] if end else tail
            # Paragraph separation is a boundary when the next label is absent.
            if not end:
                value = _FOOTER.split(value, maxsplit=1)[0]
                value = re.split(r"\n[ \t]*\n", value, maxsplit=1)[0]
            fields[field] = _clean(value, field)
    return fields


def _bounds(words):
    return [min(word["bbox"][0] for word in words), min(word["bbox"][1] for word in words),
            max(word["bbox"][2] for word in words), max(word["bbox"][3] for word in words)]


def _label_anchors(words):
    anchors = []
    for line in _lines(words):
        row = sorted(line["words"], key=lambda word: word["bbox"][0])
        for index in range(len(row)):
            for count in range(min(5, len(row) - index), 0, -1):
                group = row[index:index + count]
                text = " ".join(word["text"] for word in group)
                match = next((key for key, pattern in _LABEL_PATTERNS if pattern.fullmatch(text)), None)
                if match:
                    anchors.append({"field": match, "bbox": _bounds(group), "label": text})
                    break
    # The PO label column wraps independently of its value and adjacent column.
    # Join only nearby, vertically aligned label words, never a value-column word.
    for word in words:
        if word["text"].lower() not in {"payment", "delivery", "seller"}:
            continue
        x0, y0, _, y1 = word["bbox"]
        height = max(1, y1 - y0)
        for following in sorted(words, key=lambda item: item["bbox"][1]):
            fx, fy, _, _ = following["bbox"]
            if abs(fx - x0) > height * 0.8 or not height * 0.2 < fy - y0 < height * 2.3:
                continue
            if any(abs(other["bbox"][0] - x0) <= height * 0.8
                   and y0 + height * 0.2 < other["bbox"][1] < fy for other in words):
                continue
            text = f'{word["text"]} {following["text"]}'
            match = next((key for key, pattern in _LABEL_PATTERNS if pattern.fullmatch(text)), None)
            if match:
                anchors.append({"field": match, "bbox": _bounds([word, following]), "label": text})
                break
    return sorted(anchors, key=lambda anchor: (anchor["bbox"][1], anchor["bbox"][0]))


def _layout_fields(page, words):
    anchors = _label_anchors(words)
    fields = {}
    for anchor in anchors:
        field = anchor["field"]
        if field not in _FIELD_LABELS or field in fields:
            continue
        x0, y0, x1, y1 = anchor["bbox"]
        # Exclude lower confirmation/signature blocks from the cover fields.
        if any(other["bbox"][1] < y0 and re.search(
            r"(?:Purchase\s+Summary|Approved\s+by|Order\s+Confirmation)",
            other["label"], re.I,
        ) for other in anchors if other["field"] == "_boundary"):
            continue
        height = max(4, min(word["bbox"][3] - word["bbox"][1] for word in words
                            if x0 <= word["bbox"][0] <= x1 and y0 <= word["bbox"][1] <= y1))
        # A second label column forms a hard horizontal boundary even where
        # the corresponding right-hand field starts on a different baseline.
        right_labels = [other["bbox"][0] for other in anchors
                        if other["bbox"][0] > max(x1 + height, page.rect.width * 0.48)
                        and x0 < page.rect.width * 0.48]
        right = min(right_labels) - 2 if right_labels else page.rect.width
        below = [other["bbox"][1] for other in anchors
                 if other["bbox"][1] > y0 + height * 0.5
                 and x0 - height <= other["bbox"][0] <= x1 + height
                 and other["bbox"][0] < right
                 and other["bbox"] != anchor["bbox"]]
        bottom = min(below) - 1 if below else min(page.rect.height, y1 + height * 4)
        clip = [x1 + 1, y0 - height * 0.25, right, bottom]
        value = "\n".join(line["text"] for line in _lines(words, clip))
        fields[field] = _clean(value, field)
    return fields


def extract_po_commercial_fields(pdf_bytes, text):
    """Prefer first-cover geometry; keep bounded text for unreadable layouts."""
    fields = _text_fields(text)
    cover = _PAGE.split(str(text or ""))
    cover = next((page for page in cover if page.strip()), "")
    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as document:
            if not len(document):
                return {field: fields.get(field, "") for field in _FIELD_LABELS}
            page = document[0]
            native = _layout_fields(page, _native_words(page))
            fields.update(native)
            hinted_fields = {field for field, pattern in _FIELD_LABELS.items()
                             if re.search(pattern, cover, re.I)}
            if re.search(r"\bPayment\b", cover, re.I):
                hinted_fields.update(("payment_terms", "payment_mode"))
            if hinted_fields.difference(native):
                # One cover-only PSM6 pass retains word positions and handles
                # wrapped label columns, including partially native covers.
                words = _ocr_words(page, clip=page.rect)
                fields.update({key: value for key, value in _layout_fields(page, words).items()
                               if key not in native})
    except (ImportError, RuntimeError, ValueError, OSError):
        # Unavailable local OCR must not replace bounded literal text with guesses.
        pass
    return {field: fields.get(field, "") for field in _FIELD_LABELS}
