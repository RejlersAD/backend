"""Drawing/document-number detection — project-agnostic (any client scheme).

The drawing number of a sheet is read from the title block: the token that
is (a) printed largest and (b) sits in the bottom-right region. When an
IO-list baseline exists, its P&ID column is the authoritative vocabulary —
a title-block candidate that appears in the baseline always wins over a
generic guess, so C&E/body references to other drawings never mislabel the
sheet.
"""
import re

from . import settings as S

_TOKEN_RE = re.compile(S.DRAWING_TOKEN_RE)
_REV_SUFFIX_RE = re.compile(S.DRAWING_REV_SUFFIX_RE)


def norm_drawing(ref):
    """'13-PP-152-72065-P2' → '13-PP-152-72065' — revision/page suffixes and
    case/spacing never distinguish drawings."""
    s = re.sub(r'\s+', '', str(ref or '')).upper()
    return _REV_SUFFIX_RE.sub('', s)


def title_block_candidates(page):
    """(size, corner_distance², token) for doc-number-shaped spans in the
    bottom-right region of the sheet (the title block zone)."""
    out = []
    for sp in page.spans:
        if sp.cx <= 0.6 * page.width or sp.cy <= 0.5 * page.height:
            continue
        for m in _TOKEN_RE.finditer(sp.text or ''):
            corner_d = (page.width - sp.cx) ** 2 + (page.height - sp.cy) ** 2
            out.append((sp.size, -corner_d, m.group(0)))
    return out


def detect_sheet_drawing(page, baseline_drawings=()):
    """The drawing number of one sheet. Baseline vocabulary wins; otherwise
    the largest title-block token; otherwise the most common token anywhere
    on the page. '' when nothing plausible is found."""
    base = {norm_drawing(d) for d in baseline_drawings if norm_drawing(d)}
    cands = title_block_candidates(page)
    if base:
        in_base = [c for c in cands if norm_drawing(c[2]) in base]
        if in_base:
            return norm_drawing(max(in_base)[2])
    if cands:
        return norm_drawing(max(cands)[2])
    nums = _TOKEN_RE.findall(page.text or '')
    if not nums:
        return ''
    return norm_drawing(max(((nums.count(n), n) for n in set(nums))))


def drawing_refs_in_text(text):
    """All drawing-number-shaped tokens in a text blob (upload summaries)."""
    return [norm_drawing(m) for m in _TOKEN_RE.findall(text or '')]
