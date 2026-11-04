"""Conservative document revision signals; names alone do not prove authority."""

import re
from datetime import datetime


_MARKERS = re.compile(r'\b(addendum|amendment|revision|clarification|corrigendum)\b', re.I)
_NUMBER = re.compile(r'\b(?:addendum|amendment|revision|rev(?:ision)?|corrigendum)\s*(?:no\.?|#|:)?\s*(\d{1,3})\b', re.I)
_DATE = re.compile(r'\b(?:issued|issue date|dated|date)\s*[:#-]?\s*(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4}|\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{4})\b', re.I)


def revision_metadata(name, text=''):
    haystack = f'{name or ""}\n{(text or "")[:12000]}'
    marker = _MARKERS.search(haystack)
    number = _NUMBER.search(name or '') or _NUMBER.search((text or '')[:12000])
    date = _DATE.search(haystack)
    revision_date = ''
    if date:
        raw_date = date.group(1)
        for pattern in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d %B %Y', '%d %b %Y'):
            try:
                revision_date = datetime.strptime(raw_date, pattern).date().isoformat()
                break
            except ValueError:
                pass
    return {
        'is_revision': bool(marker),
        'revision_kind': marker.group(1).casefold() if marker else '',
        'revision': int(number.group(1)) if number else 0,
        'revision_date': revision_date,
        # A revision marker is a signal for review; it is not sufficient to
        # supersede another source without a unique, comparable revision.
        'revision_evidence': marker.group(0)[:120] if marker else '',
    }


def compare_revision(left, right):
    """Return -1/0/1 only for explicit, comparable numbered revisions."""
    a, b = int(left.get('revision') or 0), int(right.get('revision') or 0)
    if a and b and a != b:
        return -1 if a < b else 1
    return 0
