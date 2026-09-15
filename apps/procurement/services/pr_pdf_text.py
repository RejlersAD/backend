"""Read PR PDFs without degrading embedded text by unnecessarily running OCR.

The native reader reconstructs physical rows from word positions.  This matters
for requisition tables, whose PDF drawing order can put all prices after all
descriptions.  Scanned pages are sent to OCR individually, including scans in an
otherwise digital PDF.  No document is stored or changed by this module.
"""

from __future__ import annotations

import re
from statistics import median
from typing import Callable

from .pr_pdf_layout import extract_native_page_layout, extract_scanned_page_layout


_PAGE_MARKER = re.compile(r"(?m)^\s*---\s*Page\s+\d+\s*---\s*$", re.IGNORECASE)


def _clean_page_text(text: str) -> str:
    return _PAGE_MARKER.sub("", text or "").strip()


def _physical_rows(words: list) -> str:
    """Keep table cells together even when they are separate PDF text blocks."""
    words = [word for word in words if str(word[4]).strip()]
    if not words:
        return ""
    word_height = median(max(1, word[3] - word[1]) for word in words)
    tolerance = max(2, word_height * 0.4)
    rows: list[dict] = []
    for word in sorted(words, key=lambda item: ((item[1] + item[3]) / 2, item[0])):
        centre = (word[1] + word[3]) / 2
        if not rows or abs(centre - rows[-1]["centre"]) > tolerance:
            rows.append({"centre": centre, "words": [word]})
        else:
            rows[-1]["words"].append(word)
            rows[-1]["centre"] = median(
                (item[1] + item[3]) / 2 for item in rows[-1]["words"]
            )

    lines = []
    for row in rows:
        parts = []
        previous = None
        for word in sorted(row["words"], key=lambda item: item[0]):
            if previous is not None:
                # A substantial gap identifies separately placed table cells.
                parts.append(" | " if word[0] - previous[2] > word_height * 1.8 else " ")
            parts.append(str(word[4]).strip())
            previous = word
        line = "".join(parts)
        # Some templates put a currency and its amount into separate cells.
        # Keep their source values, but don't let the layout separator prevent
        # a downstream money reader from recognizing the pair.
        line = re.sub(
            r"\b(USD|AED|EUR|GBP|SAR|QAR|KWD|INR|CNY)\s*\|\s*(?=\d)",
            r"\1 ", line,
        )
        lines.append(line)
    return "\n".join(lines)


def _has_large_image(page) -> bool:
    page_area = max(1, page.rect.width * page.rect.height)
    return any(
        max(0, info["bbox"][2] - info["bbox"][0])
        * max(0, info["bbox"][3] - info["bbox"][1])
        / page_area >= 0.3
        for info in page.get_image_info()
    )


def _usable_native_text(text: str, large_image: bool) -> bool:
    significant = sum(character.isalnum() for character in text)
    if significant < 3 or text.count("\ufffd") > max(1, len(text) * 0.02):
        return False
    # A native logo/footer must not cause the scanned body to be skipped.
    if large_image and (significant < 180 or len(text.split()) < 35):
        return False
    return True


def _default_ocr(pdf_bytes: bytes) -> str:
    """Run the same layout reader when no legacy fallback was supplied."""
    import pymupdf

    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as document:
        return "\n".join(extract_scanned_page_layout(page, page_number=index + 1)["text"]
                         for index, page in enumerate(document))


def extract_pr_pdf_text(
    pdf_bytes: bytes,
    *,
    fallback: Callable[[bytes], str] | None = None,
) -> dict:
    """Return text, method, per-page provenance and warnings for a PR PDF.

    Scans always use layout OCR first. ``fallback`` receives a one-page PDF only
    if that path fails, or the original bytes if the native reader cannot open
    the document. This retains existing mocked OCR boundaries without routing
    production scans through the old unstructured OCR implementation.
    """
    ocr = fallback or _default_ocr
    warnings = []
    try:
        import pymupdf

        document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        if document.needs_pass:
            document.close()
            raise ValueError("The PDF is password protected.")
    except Exception:
        # Also preserves compatibility with callers that mock the OCR boundary.
        text = _clean_page_text(ocr(pdf_bytes))
        return {
            "text": text,
            "method": "fallback",
            "pages": [],
            "page_layout": [],
            "warnings": ["The native PDF reader was unavailable; OCR fallback was used."
                         if text else "No readable text could be extracted from this PDF."],
        }

    page_results = []
    page_layout = []
    text_parts = []
    try:
        for page_index, page in enumerate(document):
            number = page_index + 1
            words = page.get_text("words")
            native_text = _physical_rows(words)
            large_image = _has_large_image(page)
            layout = {"page": number, "width": page.rect.width, "height": page.rect.height,
                      "coordinate_space": "points", "dpi": None, "tokens": [],
                      "regions": [], "table_rows": [], "structure": {}}
            if _usable_native_text(native_text, large_image):
                text, method = native_text, "native"
                layout = extract_native_page_layout(page, words, page_number=number)
            elif not native_text and not page.get_images() and not page.get_drawings():
                text, method = "", "blank"
            else:
                try:
                    result = extract_scanned_page_layout(page, page_number=number)
                    text = _clean_page_text(result["text"])
                    layout = result["layout"]
                    warnings.extend(result.get("warnings", []))
                    method = "ocr"
                    if not text:
                        raise ValueError("OCR returned no readable text")
                except Exception:
                    text = ""
                    if fallback:
                        try:
                            with pymupdf.open() as single_page:
                                single_page.insert_pdf(document, from_page=page_index, to_page=page_index)
                                text = _clean_page_text(fallback(single_page.tobytes()))
                        except Exception:
                            pass
                    if text:
                        method = "ocr_fallback"
                        warnings.append(f"Page {number}: table-aware OCR failed; fallback text requires careful review.")
                    else:
                        text = native_text
                        method = "native_partial" if text else "unreadable"
                        warnings.append(
                            f"Page {number}: the scanned content could not be read. "
                            "Review this page in the PDF; extracted fields may be incomplete."
                        )
            page_results.append({"page": number, "method": method, "characters": len(text)})
            page_layout.append(layout)
            # Keep page markers even for unreadable pages so page references stay valid.
            text_parts.append(f"--- Page {number} ---\n{text}")
    finally:
        document.close()

    methods = {page["method"] for page in page_results if page["method"] != "blank"}
    method = next(iter(methods)) if len(methods) == 1 else "mixed" if methods else "blank"
    return {"text": "\n\n".join(text_parts), "method": method,
            "pages": page_results, "page_layout": page_layout, "warnings": warnings}
