"""
Drawing Segmentation Service
=============================
Splits a PDF/image document into independent P&ID drawings.
Each page is treated as one drawing (deterministic, no AI).
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
logger = logging.getLogger(__name__)



@dataclass
class SegmentedDrawing:
    drawing_id: str
    page_index: int
    title: str = ''
    metadata: dict = field(default_factory=dict)


def segment_document(document_id: str, file_path: str) -> List[SegmentedDrawing]:
    """
    Segment a document into individual drawings.

    Strategy (deterministic):
      • PDF  → one drawing per page
      • Image → single drawing
      • DWG  → single drawing (DWG parsing requires AutoCAD libraries; treat as one)

    Returns a sorted list of SegmentedDrawing objects.
    """
    ext = file_path.rsplit('.', 1)[-1].lower()
    drawings: List[SegmentedDrawing] = []

    if ext == 'pdf':
        drawings = _segment_pdf(document_id, file_path)
        if not drawings:
            logger.warning(
                '[PIDVerification] PDF segmentation produced 0 pages for %s; applying single-page fallback',
                document_id,
            )
            drawings = [
                SegmentedDrawing(
                    drawing_id=f'{document_id}-DRAWING-1',
                    page_index=0,
                    title='',
                    metadata={'source_format': 'pdf', 'fallback': 'empty_pdf_page_list'},
                )
            ]
    else:
        # Single-drawing documents
        drawings = [
            SegmentedDrawing(
                drawing_id=f'{document_id}-DRAWING-1',
                page_index=0,
                title='',
                metadata={'source_format': ext},
            )
        ]

    logger.info(
        '[PIDVerification] Segmented document %s → %d drawing(s)',
        document_id, len(drawings)
    )
    return drawings


def _segment_pdf(document_id: str, file_path: str) -> List[SegmentedDrawing]:
    """One SegmentedDrawing per PDF page using PyMuPDF (pymupdf) or fallback."""
    try:
        import pymupdf  # PyMuPDF
        doc = pymupdf.open(file_path)
        results = []
        for i, page in enumerate(doc):
            title_info = _extract_title_block(page)
            results.append(
                SegmentedDrawing(
                    drawing_id=f'{document_id}-DRAWING-{i + 1}',
                    page_index=i,
                    title=title_info.get('title', ''),
                    metadata={
                        'page_count': len(doc),
                        'page_index': i,
                        # Title-block extraction details (drawing number /
                        # revision) — consumed by report/export layers.
                        'title_block': title_info,
                    },
                )
            )
        doc.close()
        return results
    except ImportError:
        logger.warning('[PIDVerification] PyMuPDF not installed – treating PDF as single drawing')
        return [
            SegmentedDrawing(
                drawing_id=f'{document_id}-DRAWING-1',
                page_index=0,
                title='',
                metadata={'source_format': 'pdf', 'fallback': True},
            )
        ]
    except Exception as exc:
        logger.error('[PIDVerification] PDF segmentation error: %s', exc)
        return [
            SegmentedDrawing(
                drawing_id=f'{document_id}-DRAWING-1',
                page_index=0,
                title='',
                metadata={'error': str(exc)},
            )
        ]


def _extract_title_from_text(text: str) -> str:
    """
    Heuristic: first non-empty line ≤ 120 chars is likely the drawing title.
    Fully deterministic.

    NOTE: only used as a last-resort fallback by _extract_title_block()
    when no coordinate data is available (scanned pages without a text
    layer). Reading the FIRST line of the page's raw text stream was the
    29 Oct 2026 bug that reported the sheet title as "AP7E" (a vent code
    that happened to be first in reading order).
    """
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and len(stripped) <= 120:
            return stripped
    return ''


# ── Title-block regions (soft-coded, fractions of VISUAL page size) ──────
# Engineering drawing title blocks sit in a BOTTOM corner of the sheet —
# conventionally bottom-right (ISO 7200 / ASME Y14, Borouge/ADNOC sheets),
# but rotated sheets (page.rotation 90/270, e.g. the Mubarraz "P&ID.pdf"
# AFC sheet) present the title block at the visual bottom-LEFT in text
# coordinates. Both bottom corners are searched; the region yielding the
# better title stack + drawing number wins.
_TITLEBLOCK_X_MIN = 0.55   # right 45% of the sheet (right region)
_TITLEBLOCK_X_MAX = 0.45   # left 45% of the sheet  (left region)
_TITLEBLOCK_Y_MIN = 0.70   # bottom 30% of the sheet

# Drawing-number patterns, e.g. Borouge "13-PP-152-20070", Mubarraz
# "PJ6-EXD-MRI-BQDA-0007", "P16093-16-01-08-1677-1.PID" — dash-joined
# groups of letters/digits, at least 3 groups.
_DRAWING_NO_RE = re.compile(
    r'\b(?:\d{2}[A-Z]?-){1,3}[A-Z0-9]{2,}(?:-[A-Z0-9]{2,}){1,3}\b'      # 13-PP-152-20070
    r'|\b[A-Z]{1,4}\d{0,6}(?:-[A-Z0-9]{1,8}){3,5}(?:\.[A-Z]{2,4})?\b'   # PJ6-EXD-..., P16093-...
)
# Revision token: "REV Q", "REV. A", "REVISION: 0" — a separator MUST
# follow REV/REVISION so "REVIEW" is never misread as revision "IEW".
_REV_RE = re.compile(r'\bREV(?:ISION)?[.\s:]+([A-Z0-9]{1,3})\b', re.IGNORECASE)

# Title-block boilerplate lines (confidentiality notices, copyright text)
# never constitute the drawing title — filtered from title candidates.
# Word-boundary matching: plain substring matching false-positive'd on the
# real title "01 FURNACE DISTRIBUTION HEADER I" (contains "distribut").
_BOILERPLATE_RE = re.compile(
    r'\b(confidential|copyright|distributed|communicated|reproduced|'
    r'permission|proprietary|unauthorized|unauthorised|disclosure)\b',
    re.IGNORECASE,
)
# Title-block LABEL cells ("BOROUGE DRAWING NO.", "CONTRACTOR DRAWING NO.",
# "STATUS", "DATE") are not title text either.
_LABEL_RE = re.compile(
    r'\b(drawing\s+no\.?|contractor|status|origin\.?|check\.?|appr\.?|date|'
    r'project\s+no\.?|job\s+code|scale|sheet\s+of)\b',
    re.IGNORECASE,
)

# ── Numbering-table header LABELS (bug fix 29 Oct 2026, second pass) ─────
# On some sheets the drawing number is split across a numbered-fields table
# ("DRG.No. | DOC. CODE | SERIAL No. | REV. | SHT | OF | SHT"), and those
# column-header labels were picked as the "title" ("DRG.No. DOC. CODE
# SERIAL No.", "SHEET NO."). A line whose alphabetic tokens are mostly
# (>= 60%) label words is a table header / field label, never a title.
_LABEL_TOKEN_RE = re.compile(
    r'^(drg|dwg|doc|document|drawing|ser|serial|rev|sheet|sht|no|num|number|'
    r'code|date|scale|size|zone|area|p|of|title|proj|project|loc|location|'
    r'desc|description|orig|origin|chk|check|ch\'d|appr|ap\'d|dr\'n|drn|'
    r'drawn|status|issue|contractor|consultant|vendor|client|reference|'
    r'documents|drawings|company|page)\.?$',
    re.IGNORECASE,
)
_LABEL_TOKEN_MIN_RATIO = 0.6

# Issue-status rows of the revision table ("ISSUED FOR CONSTRUCTION",
# "AS BUILT ISSUE", "RELEASE FOR 90% MODEL REVIEW") are not the title.
_ISSUE_STATUS_RE = re.compile(
    r'^(?:RE-)?(?:ISSUED?|APPROVED|RELEASED)\s+FOR\b|^AS[- ]BUILT\b',
    re.IGNORECASE,
)
# Off-sheet continuation flags ("TO CLOSED DRAIN", "FROM TK-101") are
# drawing content, not title text.
_CONT_FLAG_RE = re.compile(r'^(?:TO|FROM)\s+\S', re.IGNORECASE)
# Reference-listing rows ("E : 61-FSLL-14005 / GFHSSB1 / ...").
_REF_ROW_RE = re.compile(r'^[A-Z]{1,2}\s*:')
# Pure code/tag lines (e.g. '013842-X', '4"-D-5690-013842-X-N') — every
# whitespace-separated token contains a digit or inch mark and consists of
# code characters only. MUST require a digit: an earlier version matched
# any ALL-CAPS text once spaces were stripped, which swallowed real title
# lines like 'PIPING AND INSTRUMENT DIAGRAM' / 'BOROUGE 3 PROJECT'.
_CODE_TOKEN_RE = re.compile(r'^(?=.*[\d"\-/])[A-Z0-9"\'\.\-/]+$')
# Revision-table label cells ("REV.", "REV").
_REV_LABEL_RE = re.compile(r'^REV\.?$', re.IGNORECASE)
# Company address lines ("HAMDAN STREET,PO BOX 39317", "REJLERS TOWER 13TH
# FLOOR") — they sit in the title block but are never the drawing title.
_ADDRESS_RE = re.compile(
    r'\b(p\.?\s?o\.?\s*box|street|tower|floor|suite|po\s*box)\b',
    re.IGNORECASE,
)

# Title-stack clustering tolerances (PDF points, ~1/72 inch).
_TITLESTACK_MAX_Y_GAP = 40.0   # consecutive title lines
_TITLESTACK_MAX_X_GAP = 150.0  # horizontal alignment of a stack


def _is_label_line(text: str) -> bool:
    """True when >= _LABEL_TOKEN_MIN_RATIO of the line's alphabetic tokens
    are numbering-table label words (DRG, DOC, CODE, SERIAL, NO, SHEET...)."""
    tokens = [t for t in re.split(r'[^A-Za-z\']+', text) if t and any(c.isalpha() for c in t)]
    if not tokens:
        return False
    hits = sum(1 for t in tokens if _LABEL_TOKEN_RE.match(t))
    return hits / len(tokens) >= _LABEL_TOKEN_MIN_RATIO


def _visual_page_size(page, rect) -> tuple:
    """(width, height) of the VISUAL page, accounting for rotation.

    Empirically (PyMuPDF in this stack) get_text() line bboxes are in
    visual/rotated space — on a rotation=270 page, y extends to the
    rotated height. So swap width/height for 90/270 rotation.
    """
    if getattr(page, 'rotation', 0) in (90, 270):
        return rect.height, rect.width
    return rect.width, rect.height


# ── Anchor-based title block (Linde/Borouge layout) ─────────────────────
# Sheets like Borouge 13-PP-152-20070 carry TWO title blocks: the ACTIVE
# one (upper right, with the current revision table) and a SUPERSEDED one
# (bottom right, crossed out with a red X). Selecting by "newest revision
# table date" is the deterministic proxy for "not under crossing lines".
#
# Anchors (exact label cells): 'DRAWING TITLE:' / 'PROJECT DRAWING NO' /
# 'REV:' — values are read positionally relative to the label.
_TB_TITLE_LABEL_RE  = re.compile(r'^DRAWING\s+TITLE:?$', re.IGNORECASE)
_TB_DRGNO_LABEL_RE  = re.compile(r'^(?:PROJECT\s+)?DRAWING\s+NO\.?$', re.IGNORECASE)
_TB_REV_LABEL_RE    = re.compile(r'^REV\.?:?$', re.IGNORECASE)
_TB_DATE_RE         = re.compile(r'^\d{2}\.\d{2}\.\d{4}$')
_TB_REV_TOKEN_RE    = re.compile(r'^[A-Z]\d?$')


def _anchor_title_block(lines: List[tuple]) -> Optional[dict]:
    """Extract title/drawing-number/revision from a label-anchored title
    block (Linde layout). `lines` = [(text, y_mid, x_mid)] in % coords.
    Returns None when no anchor labels exist on the page."""
    label_title = [l for l in lines if _TB_TITLE_LABEL_RE.match(l[0])]
    label_drgno = [l for l in lines if _TB_DRGNO_LABEL_RE.match(l[0])]
    label_rev = [l for l in lines if _TB_REV_LABEL_RE.match(l[0])]
    if not (label_title or label_drgno):
        return None

    res = {'title': '', 'drawing_number': '', 'revision': '', 'method': 'anchor_labels'}

    if label_title:
        lt, ly, lx = label_title[0]
        values = sorted(
            (l for l in lines
             if l[2] > lx + 3 and -1.5 < l[1] - ly <= 3.5
             and not _TB_TITLE_LABEL_RE.match(l[0])
             and not _TB_DRGNO_LABEL_RE.match(l[0])
             and not _TB_REV_LABEL_RE.match(l[0])
             and not _TB_REV_TOKEN_RE.match(l[0])
             and not _is_label_line(l[0])),
            key=lambda l: l[1],
        )
        res['title'] = ' '.join(v[0] for v in values)[:512]

    if label_drgno:
        dt, dy, dx = label_drgno[0]
        cands = []
        for l in lines:
            m = _DRAWING_NO_RE.search(l[0])
            if not (m and -1.5 < l[1] - dy <= 6 and l[2] > dx - 12):
                continue
            # Contractor doc numbers ('&AA-13-P-FP 2007.001-P2 (EN)') are
            # not the project drawing number.
            if re.match(r'^[A-Z]{2}-\d{2}-P-[A-Z]{2}', m.group(0)):
                continue
            cands.append((abs(l[1] - dy) + abs(l[2] - dx), m.group(0)))
        if cands:
            res['drawing_number'] = min(cands)[1]
            # Base number without '-P2' sheet-suffix, for register matching.
            res['drawing_number_base'] = re.sub(r'-P\d+$', '', res['drawing_number'])

    if label_rev:
        rt, ry, rx = label_rev[0]
        vals = [
            l for l in lines
            if _TB_REV_TOKEN_RE.match(l[0]) and 0.2 < l[2] - rx <= 3 and abs(l[1] - ry) <= 1.2
        ]
        if vals:
            res['revision'] = vals[0][0].upper()

    # Revision table: newest date row anywhere in the right area.
    best_date, best_rev = None, None
    for l in lines:
        if not _TB_DATE_RE.match(l[0]) or l[2] < 75:
            continue
        d = datetime.strptime(l[0], '%d.%m.%Y').date()
        # revision letter of the same row, just left of the date
        rev_tok = [
            r for r in lines
            if _TB_REV_TOKEN_RE.match(r[0]) and abs(r[1] - l[1]) <= 0.6
            and l[2] - 3 <= r[2] < l[2]
        ]
        if best_date is None or d > best_date:
            best_date = d
            best_rev = rev_tok[0][0].upper() if rev_tok else None
    if best_date:
        res['rev_date'] = best_date.isoformat()
        if best_rev:
            res['table_revision'] = best_rev
            # The revision table's latest row wins over the 'REV:' cell.
            res['revision'] = best_rev
    return res


def _region_rev_date(region_lines: List[tuple]) -> Optional[str]:
    """Newest dd.mm.yyyy date found in a corner region (ISO string)."""
    best = None
    for text, y, x in region_lines:
        if _TB_DATE_RE.match(text):
            d = datetime.strptime(text, '%d.%m.%Y').date()
            if best is None or d > best:
                best = d
    return best.isoformat() if best else None


def _title_clusters(candidates: List[tuple]) -> List[List[tuple]]:
    """All clusters of title-candidate lines forming contiguous stacks:
    consecutive lines (sorted top-down) within _TITLESTACK_MAX_Y_GAP
    vertically and _TITLESTACK_MAX_X_GAP horizontally of the previous line."""
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda c: (c[1], c[2]))
    clusters: List[List[tuple]] = []
    current: List[tuple] = []
    for cand in ordered:
        if current:
            _, py, px = current[-1]
            _, cy, cx = cand
            if 0 <= cy - py <= _TITLESTACK_MAX_Y_GAP and abs(cx - px) <= _TITLESTACK_MAX_X_GAP:
                current.append(cand)
            else:
                clusters.append(current)
                current = [cand]
        else:
            current = [cand]
    if current:
        clusters.append(current)
    return clusters


def _region_title_block(region_lines: List[tuple], vw: float) -> dict:
    """Extract title / drawing number / revision from ONE corner region's
    lines [(text, y, x)]. Returns dict incl. a 'score' used to pick between
    the bottom-right and bottom-left regions."""
    res = {'title': '', 'drawing_number': '', 'revision': '', 'score': 0}

    title_candidates: List[tuple] = []   # (text, y, x)
    drawing_no_candidates: List[tuple] = []  # (number_text, y, x)
    rev_label_positions: List[tuple] = []

    for text, y_top, x0 in region_lines:
        m = _DRAWING_NO_RE.search(text)
        if m:
            drawing_no_candidates.append((m.group(0), y_top, x0))
            continue
        if not res['revision']:
            r = _REV_RE.search(text)
            if r:
                res['revision'] = r.group(1).upper()
                continue
        if _REV_LABEL_RE.match(text):
            rev_label_positions.append((y_top, x0))
            continue
        # Title candidate: has letters, not a short all-caps code, not
        # boilerplate, not a numbering-table label/header (DRG.No., DOC.
        # CODE, SERIAL No., SHEET NO., ...), not an issue-status row, not a
        # continuation flag, not a reference-listing row, not a bare code.
        if (
            any(c.isalpha() for c in text)
            and not (len(text) <= 6 and text.upper() == text)
            and not _BOILERPLATE_RE.search(text)
            and not _LABEL_RE.search(text)
            and not _is_label_line(text)
            and not _ISSUE_STATUS_RE.match(text)
            and not _CONT_FLAG_RE.match(text)
            and not _REF_ROW_RE.match(text)
            and not _ADDRESS_RE.search(text)
            and not (
                # bare code line: every token is a code-like token
                all(_CODE_TOKEN_RE.match(tok) for tok in text.split())
            )
        ):
            # Field VALUE cells: a label line ("Linde Project No.:") at the
            # same height to the LEFT means this line is that label's value
            # (e.g. 'Ruwais3_LEI'), not title text.
            if any(
                abs(y_top - ly) <= 5 and lx < x0
                for lt, ly, lx in region_lines
                if (lt, ly, lx) != (text, y_top, x0)
                and (_is_label_line(lt) or _LABEL_RE.search(lt))
            ):
                continue
            title_candidates.append((text, y_top, x0))

    # ── Title = best contiguous stack of descriptive lines ───────────────
    # Rank clusters by: size, then proximity to a drawing-number candidate
    # (the title sits next to the sheet's own number), then total length.
    clusters = _title_clusters(title_candidates)

    def _cluster_dist(cl) -> float:
        if not drawing_no_candidates:
            return 0.0
        return min(
            abs(ty - dy) + abs(tx - dx)
            for _, ty, tx in cl
            for _, dy, dx in drawing_no_candidates
        ) / max(vw, 1.0)

    stack = []
    if clusters:
        stack = max(
            clusters,
            key=lambda cl: (len(cl), -_cluster_dist(cl), sum(len(t) for t, _, _ in cl)),
        )
    if stack:
        res['title'] = ' '.join(t for t, _, _ in stack)[:512]

    # ── Drawing number = candidate closest to the title stack ────────────
    # (the sheet's own number sits next to the title; reference/legend
    # document numbers and 'COMPANY PROJECT NO.' values sit farther away).
    if drawing_no_candidates:
        if stack:
            def _dist(cand):
                _, cy, cx = cand
                return min(abs(cy - ty) + abs(cx - tx) for _, ty, tx in stack)
            best = min(drawing_no_candidates, key=_dist)
        else:
            # No title stack: bottom-most candidate (own number sits in the
            # bottom cell; reference numbers appear higher).
            best = max(drawing_no_candidates, key=lambda c: c[1])
        res['drawing_number'] = best[0]

    # ── Revision via label proximity: token just below a "REV." label ────
    if not res['revision'] and rev_label_positions:
        for ly, lx in sorted(rev_label_positions, reverse=True):  # bottom-most first
            for text, y_top, x0 in region_lines:
                if (
                    re.fullmatch(r'[A-Z0-9]{1,3}', text)
                    and 0 < y_top - ly <= 15
                    and abs(x0 - lx) <= 60
                ):
                    res['revision'] = text.upper()
                    break
            if res['revision']:
                break

    res['score'] = (10 if res['drawing_number'] else 0) + min(len(stack), 3)
    return res


def _extract_title_block(page) -> dict:
    """
    Read the sheet title / drawing number / revision from the TITLE-BLOCK
    corner of the sheet using PyMuPDF span coordinates — never from the
    first/biggest text on the sheet.

    Heuristic (documented, deterministic; third pass 29 Oct 2026):
      1. Work in VISUAL page coordinates (rotation-aware — rotated sheets
         can present the title block at the visual bottom-LEFT).
      2. Search BOTH bottom corners (right 45% / left 45% x bottom 30%);
         the region with the better score (drawing number found + title
         stack size) wins.
      3. Per region: title = largest contiguous stack of descriptive lines
         (boilerplate, numbering-table labels like DRG.No./DOC. CODE/
         SERIAL No./SHEET NO., issue-status rows, continuation flags,
         reference-listing rows and bare codes are all excluded);
         drawing number = pattern match CLOSEST to the title stack;
         revision = "REV Q"-style token or the token below a "REV." label.
      4. Fallback (no region lines / scanned page): legacy first-line
         heuristic on the page's plain text.

    Verified 29 Oct 2026 on:
      - Borouge 13-PP-152-20070 (bottom-right block)
      - P16093-16-01-08-1677-1 Rev 1 (numbered-fields table headers present)
      - Mubarraz "P&ID.pdf" (rotation=270, bottom-left block)

    Returns {'title', 'drawing_number', 'revision', 'method'}.
    """
    result = {'title': '', 'drawing_number': '', 'revision': '', 'method': 'title_block_region'}
    try:
        page_dict = page.get_text('dict')
        rect = page.rect
        vw, vh = _visual_page_size(page, rect)
        y_cut = vh * _TITLEBLOCK_Y_MIN

        all_lines: List[tuple] = []  # (text, y_top, x0)
        for block in page_dict.get('blocks', []):
            if block.get('type') != 0:  # text blocks only
                continue
            for line in block.get('lines', []):
                line_text = ''.join(s.get('text', '') for s in line.get('spans', [])).strip()
                if not line_text:
                    continue
                x0, y0, x1, y1 = line['bbox']
                all_lines.append((line_text, (y0 + y1) / 2.0, (x0 + x1) / 2.0))

        bottom = [(t, y, x) for t, y, x in all_lines if y >= y_cut]
        regions = [
            [(t, y, x) for t, y, x in bottom if x >= vw * _TITLEBLOCK_X_MIN],  # bottom-right
            [(t, y, x) for t, y, x in bottom if x <= vw * _TITLEBLOCK_X_MAX],  # bottom-left
        ]

        # ── Anchor-based block (Linde layout, ACTIVE title block) ────────
        # NOTE: all_lines above are in PDF POINTS; _anchor_title_block's
        # tolerances are in % — normalize before calling.
        _w = rect.width or 1.0
        _h = rect.height or 1.0
        anchor = _anchor_title_block([(t, y / _h * 100, x / _w * 100) for t, y, x in all_lines])
        if anchor and anchor.get('drawing_number'):
            corner_results = []
            for region_lines in regions:
                if not region_lines:
                    continue
                c = _region_title_block(region_lines, vw)
                c['rev_date'] = _region_rev_date(region_lines)
                corner_results.append(c)
            # "Not under crossing lines" proxy: the active block carries the
            # NEWEST revision-table date. If a corner block's revision date
            # is newer than the anchor block's, the corner block wins;
            # otherwise the anchor block (top-right on Linde sheets) does.
            anchor_date = anchor.get('rev_date') or ''
            newer_corner = [
                c for c in corner_results
                if c.get('drawing_number') and (c.get('rev_date') or '') > anchor_date
            ]
            if newer_corner:
                best = max(newer_corner, key=lambda c: c.get('rev_date') or '')
                result.update({k: best.get(k, '') for k in ('title', 'drawing_number', 'revision')})
                result['method'] = 'title_block_region'
            else:
                result.update({k: anchor.get(k, '') for k in ('title', 'drawing_number', 'revision')})
                result['method'] = anchor.get('method', 'anchor_labels')
                for k in ('rev_date', 'table_revision', 'drawing_number_base'):
                    if anchor.get(k):
                        result[k] = anchor[k]
            if not result['title'] and result['drawing_number']:
                result['title'] = result['drawing_number']
            return result

        best = None
        for region_lines in regions:
            if not region_lines:
                continue
            cand = _region_title_block(region_lines, vw)
            if best is None or cand['score'] > best['score']:
                best = cand

        if best is not None and (best['title'] or best['drawing_number']):
            result.update({k: best[k] for k in ('title', 'drawing_number', 'revision')})
            if not result['title'] and result['drawing_number']:
                # No descriptive line — fall back to the drawing number so
                # the sheet is still identifiable.
                result['title'] = result['drawing_number']
            return result

        # Scanned page or non-standard layout — legacy fallback.
        result['title'] = _extract_title_from_text(page.get_text('text')[:200].strip())
        result['method'] = 'first_line_fallback'
        return result
    except Exception as exc:
        logger.warning('[PIDVerification] Title-block extraction failed (%s); using first-line fallback', exc)
        try:
            result['title'] = _extract_title_from_text(page.get_text('text')[:200].strip())
        except Exception:
            pass
        result['method'] = 'first_line_fallback'
        return result
