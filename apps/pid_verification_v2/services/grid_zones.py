"""
Grid references (spec §2.5) — read the border zone labels from the PDF
text layer and locate a bbox in them.

  - single characters within 1.5 % of the page edges;
  - digits on the top edge → column labels; letters on the left edge →
    row labels;
  - the label ORDER found on the drawing is used (Borouge: columns 8…1
    left to right, rows F…A top to bottom);
  - no labels found → grid is '-'. Never guess.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_EDGE_PCT = 1.5


def read_grid_zones(page) -> Optional[dict]:
    """page: a fitz.Page. Returns {'rows': [(letter, y_pct)], 'cols':
    [(digit, x_pct)]} in drawing order, or None when no labels are found."""
    try:
        words = page.get_text('words')
    except Exception:
        return None
    W = page.rect.width or 1.0
    H = page.rect.height or 1.0
    cols, rows = [], []
    for x0, y0, x1, y1, text, *_ in words:
        t = str(text).strip()
        if len(t) != 1:
            continue
        cx, cy = (x0 + x1) / 2 / W * 100, (y0 + y1) / 2 / H * 100
        if t.isdigit() and cy <= _EDGE_PCT:
            cols.append((t, cx))
        elif t.isalpha() and t.isupper() and cx <= _EDGE_PCT:
            rows.append((t, cy))
    if not cols or not rows:
        return None
    cols.sort(key=lambda c: c[1])   # left → right as drawn
    rows.sort(key=lambda r: r[1])   # top → bottom as drawn
    return {'rows': rows, 'cols': cols}


def _band(pos, labels):
    """Which band a %-position falls into: boundaries are the midpoints
    between adjacent labels; the outer bands extend to the page edges."""
    for i, (label, c) in enumerate(labels):
        left = (labels[i - 1][1] + c) / 2 if i > 0 else -1e9
        right = (c + labels[i + 1][1]) / 2 if i < len(labels) - 1 else 1e9
        if left <= pos < right:
            return label
    return labels[-1][0]


def grid_label(zones: Optional[dict], x_pct: float, y_pct: float) -> str:
    """row_letter + column_digit of the zone containing (x, y) in %."""
    if not zones:
        return '-'
    row = _band(y_pct, zones['rows'])
    col = _band(x_pct, zones['cols'])
    return f'{row}{col}'
