"""Require a literal source heading before persisting an extracted class."""

import re


CLASS_HEADING = re.compile(
    r'(?im)^\s*PIPING\s+(?:(?:MATERIAL\s+)?SPEC(?:IFICATION)?|CLASS)\s*:\s*'
    r'(?P<code>[A-Z0-9][A-Z0-9.\-]{0,31})\b'
)


def verified_pms_classes(pdf_path, classes):
    """Return classes with a matching heading on their reported source pages.

    Page numbers are physical, one-based PDF pages. A missing or unreadable
    heading stays out of generated workbooks for engineering review.
    """
    import pymupdf

    verified, rejected, page_codes = [], [], {}
    with pymupdf.open(pdf_path) as document:
        for cls in classes:
            code = str(cls.get('class_code') or '').strip().upper()
            pages = cls.get('_source_pages') or []
            try:
                first, last = int(pages[0]), int(pages[-1])
            except (IndexError, TypeError, ValueError):
                first = last = 0
            found = False
            if code and 1 <= first <= last <= len(document):
                for page_number in range(first, last + 1):
                    if page_number not in page_codes:
                        text = document[page_number - 1].get_text('text') or ''
                        page_codes[page_number] = {
                            match.group('code').upper() for match in CLASS_HEADING.finditer(text)
                        }
                    if code in page_codes[page_number]:
                        found = True
                        cls['_verified_heading_page'] = page_number
                        break
            if found:
                verified.append(cls)
            else:
                rejected.append({'class_code': code, 'source_pages': pages,
                                 'reason': 'No matching text-layer PIPING SPEC heading on reported source pages.'})
    return verified, rejected
