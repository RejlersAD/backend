"""Local, table-aware OCR for scanned procurement forms.

Layout evidence is returned with source pixel coordinates.  OCR confidence is
the engine's recognition score, not a statement that a business value is true.
"""

from __future__ import annotations

from statistics import median

import numpy as np
from PIL import Image, ImageOps
from scipy.ndimage import maximum_filter1d, minimum_filter1d


OCR_DPI = 400
MAX_TARGET_CELLS = 64


def _open_lines(binary, length, axis):
    eroded = minimum_filter1d(binary, size=length, axis=axis, mode="constant", cval=0)
    return maximum_filter1d(eroded, size=length, axis=axis, mode="constant", cval=0)


def _groups(indices):
    groups = []
    for index in indices:
        if not groups or index > groups[-1][-1] + 3:
            groups.append([int(index)])
        else:
            groups[-1].append(int(index))
    return [int(round(float(np.mean(group)))) for group in groups]


def _table_geometry(grayscale):
    height, width = grayscale.shape
    binary = (grayscale < 180).astype(np.uint8)
    horizontal = _open_lines(binary, max(60, width // 35), 1)
    vertical = _open_lines(binary, max(50, height // 70), 0)
    horizontal_rules = _groups(np.flatnonzero(horizontal.sum(axis=1) > width * 0.22))
    vertical_rules = _groups(np.flatnonzero(vertical.sum(axis=0) > height * 0.1))
    rows = []
    for row_index, (y0, y1) in enumerate(zip(horizontal_rules, horizontal_rules[1:])):
        if y1 - y0 < 15:
            continue
        # A boundary need only span this row; merged cells in other rows do not
        # imply the same columns throughout the document.
        row_mask = vertical[y0 + 3:y1 - 2]
        boundaries = _groups(np.flatnonzero(row_mask.sum(axis=0) > row_mask.shape[0] * 0.55))
        if len(boundaries) < 2:
            continue
        cells = [
            {"bbox": [x0, y0, x1, y1], "row_index": row_index, "column_index": column_index}
            for column_index, (x0, x1) in enumerate(zip(boundaries, boundaries[1:]))
            if x1 - x0 > 24
        ]
        if cells:
            rows.append({"bbox": [boundaries[0], y0, boundaries[-1], y1], "cells": cells})
    # Restrict removal to geometrically identified rules. Tall letters such as
    # I/l can survive the vertical opening; those are text, not table borders.
    line_mask = np.zeros_like(binary)
    for y in horizontal_rules:
        line_mask[max(0, y - 5):y + 6] |= horizontal[max(0, y - 5):y + 6]
    for row in rows:
        for cell in row["cells"]:
            x0, y0, x1, y1 = cell["bbox"]
            for x in (x0, x1):
                line_mask[y0:y1, max(0, x - 5):x + 6] |= vertical[y0:y1, max(0, x - 5):x + 6]
    line_mask = maximum_filter1d(maximum_filter1d(line_mask, size=3, axis=0), size=3, axis=1)
    cleaned = grayscale.copy()
    cleaned[line_mask.astype(bool)] = 255
    return cleaned, rows, {"horizontal_rules": horizontal_rules, "vertical_rules": vertical_rules}


def _recognize(image, *, source, bbox=None, psm=6):
    import pytesseract

    data = pytesseract.image_to_data(
        image, lang="eng", config=f"--dpi {OCR_DPI} --psm {psm} -c preserve_interword_spaces=1",
        output_type=pytesseract.Output.DICT, timeout=35,
    )
    offset_x, offset_y = (bbox[0], bbox[1]) if bbox else (0, 0)
    tokens = []
    for index, value in enumerate(data.get("text", [])):
        text = str(value).strip()
        # Keep real punctuation (notably &, %, + and currency symbols). It is
        # part of the evidence, even when Tesseract returns it as its own word.
        if not text or all(character in "|_=~" for character in text):
            continue
        x0, y0 = int(data["left"][index]) + offset_x, int(data["top"][index]) + offset_y
        confidence = max(0.0, float(data["conf"][index]))
        tokens.append({
            "text": text,
            "bbox": [x0, y0, x0 + int(data["width"][index]), y0 + int(data["height"][index])],
            "confidence": round(confidence, 2), "source": source,
        })
    return tokens


def _token_score(tokens):
    total = sum(len(token["text"]) for token in tokens)
    return round(sum(token["confidence"] * len(token["text"]) for token in tokens) / total, 2) if total else None


def _inside(token, bbox):
    x0, y0, x1, y1 = token["bbox"]
    return bbox[0] <= (x0 + x1) / 2 <= bbox[2] and bbox[1] <= (y0 + y1) / 2 <= bbox[3]


def _tokens_text(tokens):
    # Imported lazily to avoid a module import cycle with the native reader.
    from .pr_pdf_text import _physical_rows
    return _physical_rows([(*token["bbox"], token["text"]) for token in tokens])


def extract_native_page_layout(page, words, *, page_number=1):
    """Group embedded text by vector table rules without rasterizing the PDF."""
    segments = []
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "l":
                segments.append((item[1].x, item[1].y, item[2].x, item[2].y))
            elif item[0] == "re":
                rectangle = item[1]
                segments.extend([
                    (rectangle.x0, rectangle.y0, rectangle.x1, rectangle.y0),
                    (rectangle.x0, rectangle.y1, rectangle.x1, rectangle.y1),
                    (rectangle.x0, rectangle.y0, rectangle.x0, rectangle.y1),
                    (rectangle.x1, rectangle.y0, rectangle.x1, rectangle.y1),
                ])
    horizontal = [segment for segment in segments if abs(segment[1] - segment[3]) < 1
                  and abs(segment[2] - segment[0]) > page.rect.width * 0.2]
    vertical = [segment for segment in segments if abs(segment[0] - segment[2]) < 1
                and abs(segment[3] - segment[1]) > 5]

    def distinct(values):
        groups = []
        for value in sorted(values):
            if not groups or value - groups[-1][-1] > 1.5:
                groups.append([value])
            else:
                groups[-1].append(value)
        return [round(float(np.mean(group)), 2) for group in groups]

    y_rules = distinct(segment[1] for segment in horizontal)
    tokens = [{"text": word[4], "bbox": list(word[:4]), "confidence": None, "source": "native"}
              for word in words]
    rows, regions = [], []
    for row_index, (y0, y1) in enumerate(zip(y_rules, y_rules[1:])):
        boundaries = distinct(segment[0] for segment in vertical
                              if min(segment[1], segment[3]) <= y0 + 1.5
                              and max(segment[1], segment[3]) >= y1 - 1.5)
        cells = []
        for column_index, (x0, x1) in enumerate(zip(boundaries, boundaries[1:])):
            if x1 - x0 < 4:
                continue
            bbox = [x0, y0, x1, y1]
            cell_tokens = [token for token in tokens if _inside(token, bbox)]
            cell = {"kind": "table_cell", "bbox": bbox, "row_index": row_index,
                    "column_index": column_index, "text": _tokens_text(cell_tokens),
                    "tokens": cell_tokens, "confidence": None, "source": "native",
                    "contains_ink": None}
            cells.append(cell)
            regions.append(cell)
        if cells:
            rows.append({"bbox": [boundaries[0], y0, boundaries[-1], y1], "cells": cells})
    return {"page": page_number, "width": page.rect.width, "height": page.rect.height,
            "coordinate_space": "points", "dpi": None, "tokens": tokens, "regions": regions,
            "table_rows": rows, "structure": {"horizontal_rules": y_rules,
                                                "vertical_rules": distinct(segment[0] for segment in vertical)}}


def extract_scanned_page_layout(page, *, page_number=1):
    """OCR a page and its detected cells; preserve every reading for review."""
    import pymupdf

    pixmap = page.get_pixmap(dpi=OCR_DPI, colorspace=pymupdf.csRGB, alpha=False)
    grayscale = np.asarray(ImageOps.grayscale(Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)))
    cleaned, rows, structure = _table_geometry(grayscale)
    tokens = _recognize(Image.fromarray(cleaned), source="ocr_page", psm=6)
    character_height = median(token["bbox"][3] - token["bbox"][1] for token in tokens) if tokens else 35
    regions, warnings = [], []
    targeted = 0
    for row in rows:
        for cell in row["cells"]:
            bbox = cell["bbox"]
            x0, y0, x1, y1 = bbox
            inset = 5
            # The cell boundary already removes the grid. Read the original
            # pixels here so line morphology cannot erase bold letter strokes.
            crop = grayscale[y0 + inset:y1 - inset, x0 + inset:x1 - inset]
            baseline_tokens = [token for token in tokens if _inside(token, bbox)]
            has_ink = crop.size and np.count_nonzero(crop < 180) > max(12, crop.size * 0.0003)
            cell_tokens = baseline_tokens
            alternatives = []
            if has_ink and targeted < MAX_TARGET_CELLS:
                targeted += 1
                padding = 16
                padded = ImageOps.expand(Image.fromarray(crop), border=padding, fill=255)
                offset = [x0 + inset - padding, y0 + inset - padding]
                psm = 7 if crop.shape[0] < character_height * 2.8 else 6
                try:
                    cell_tokens = _recognize(padded, source="ocr_cell", bbox=offset, psm=psm)
                    score = _token_score(cell_tokens)
                    if (not cell_tokens or (score is not None and score < 55)) and psm == 7:
                        retry = _recognize(padded, source="ocr_cell", bbox=offset, psm=6)
                        alternatives.append({"text": _tokens_text(cell_tokens), "confidence": score})
                        if (_token_score(retry) or 0) > (score or 0):
                            cell_tokens = retry
                    # A scan's gray halo can turn a printed lower-case l into
                    # punctuation. Compare a clean binary reading for uncertain
                    # cells, and retain the alternate evidence for review.
                    if cell_tokens and (_token_score(cell_tokens) or 0) < 90:
                        binary = padded.point(lambda value: 0 if value < 180 else 255)
                        retry = _recognize(binary, source="ocr_cell", bbox=offset, psm=psm)
                        if (_token_score(retry) or 0) > (_token_score(cell_tokens) or 0) + 3:
                            alternatives.append({"text": _tokens_text(cell_tokens), "confidence": _token_score(cell_tokens)})
                            cell_tokens = retry
                        else:
                            alternatives.append({"text": _tokens_text(retry), "confidence": _token_score(retry)})
                    if not cell_tokens:
                        cell_tokens = baseline_tokens
                    elif (_token_score(baseline_tokens) or 0) > (_token_score(cell_tokens) or 0) + 12:
                        alternatives.append({"text": _tokens_text(cell_tokens), "confidence": _token_score(cell_tokens)})
                        cell_tokens = baseline_tokens
                except Exception:
                    warnings.append(f"Page {page_number}: a table cell could not be reread; review its source PDF.")
                    cell_tokens = baseline_tokens
            if cell_tokens and cell_tokens is not baseline_tokens:
                tokens = [token for token in tokens if not _inside(token, bbox)] + cell_tokens
            region = {
                **cell, "kind": "table_cell", "text": _tokens_text(cell_tokens),
                "confidence": _token_score(cell_tokens), "source": "ocr_cell" if any(
                    token["source"] == "ocr_cell" for token in cell_tokens
                ) else "ocr_page", "tokens": cell_tokens,
                "contains_ink": bool(has_ink),
            }
            if alternatives:
                region["alternative_readings"] = alternatives
            cell.update(region)
            regions.append(region)
    if targeted >= MAX_TARGET_CELLS:
        warnings.append(f"Page {page_number}: targeted OCR was limited to {MAX_TARGET_CELLS} cells; remaining text uses whole-page OCR.")
    layout = {
        "page": page_number, "width": pixmap.width, "height": pixmap.height,
        "coordinate_space": "pixels", "dpi": OCR_DPI,
        "tokens": sorted(tokens, key=lambda token: (token["bbox"][1], token["bbox"][0])),
        "regions": regions, "table_rows": rows, "structure": structure,
    }
    return {"text": _tokens_text(tokens), "layout": layout, "warnings": warnings}
