"""Comments Resolution Sheet extractor with continuation-row support."""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional

import fitz

from .config import COMMENT_HEADER_ALIASES, COMMENT_SHEET_COLUMNS, STATUS_CODE_MEANING

logger = logging.getLogger(__name__)

_SERIAL_RE = re.compile(r'^\s*(\d+)\s*[.)]?\s*$')
_DOC_STATUS_RE = re.compile(r'document\s+status\s+code\s*:?\s*([1-4])', re.I)
_LOGICAL_FIELDS = ('s_no', 'company_comment', 'contractor_reply', 'company_decision')


def _clean(value) -> str:
    return ' '.join(str(value or '').split())


def _match_header_columns(header_cells: List[str]) -> Optional[Dict[int, str]]:
    """Return physical-column anchors for at least three logical headers."""
    mapping: Dict[int, str] = {}
    used_fields = set()
    norm = [_clean(c).lower() for c in header_cells]
    for canonical, aliases in COMMENT_HEADER_ALIASES.items():
        for idx, cell in enumerate(norm):
            if idx in mapping or canonical in used_fields:
                continue
            if any(alias in cell for alias in aliases):
                mapping[idx] = canonical
                used_fields.add(canonical)
                break
    required = {'s_no', 'company_comment', 'contractor_reply'}
    return mapping if required.issubset(used_fields) else None


def _normalise_status_code(raw: str) -> Dict[str, str]:
    raw = _clean(raw)
    for ch in raw:
        if ch in STATUS_CODE_MEANING:
            return {'code': ch, 'meaning': STATUS_CODE_MEANING[ch]}
    return {'code': raw, 'meaning': ''}


def _append(record: Dict[str, str], field: str, value: str) -> None:
    value = _clean(value)
    if not value or field == 's_no':
        return
    if record[field]:
        record[field] = f'{record[field]}\n{value}'
    else:
        record[field] = value


def _column_anchors(row_len: int, header_map: Optional[Dict[int, str]]) -> Dict[str, float]:
    """Map merged physical columns onto four stable logical CRS columns."""
    denominator = max(row_len - 1, 1)
    anchors = {
        's_no': 0.0,
        'company_comment': 1 / 3,
        'contractor_reply': 2 / 3,
        'company_decision': 1.0,
    }
    if header_map:
        for idx, field in header_map.items():
            if field in anchors:
                anchors[field] = idx / denominator
    return anchors


def _field_for_cell(index: int, row_len: int, header_map: Optional[Dict[int, str]]) -> str:
    position = index / max(row_len - 1, 1)
    anchors = _column_anchors(row_len, header_map)
    # Once a serial cell has started the logical row, every other populated
    # cell belongs to one of the three content columns. Excluding s_no avoids
    # losing comments in merged tables where content begins physically close
    # to the serial-number column.
    content_fields = _LOGICAL_FIELDS[1:] if index > 0 else _LOGICAL_FIELDS
    return min(content_fields, key=lambda field: abs(position - anchors[field]))


def _extract_document_status(row: List[str]) -> str:
    cells = [_clean(cell) for cell in row]
    joined = ' '.join(cells)
    match = _DOC_STATUS_RE.search(joined)
    if match:
        return match.group(1)
    for idx, cell in enumerate(cells):
        if 'document status code' in cell.lower():
            for candidate in cells[idx + 1:]:
                if candidate in STATUS_CODE_MEANING:
                    return candidate
    return ''


def _consume_rows(
    rows: List[List[str]],
    state: Dict,
) -> None:
    """Consume one physical table, merging wrapped/continued rows into state."""
    header_map = None
    header_indexes = set()
    for idx, row in enumerate(rows[:8]):
        candidate = _match_header_columns(row)
        if candidate:
            header_map = candidate
            header_indexes.add(idx)
            break

    for row_index, raw_row in enumerate(rows):
        row = list(raw_row or [])
        if not row:
            continue

        doc_status = _extract_document_status(row)
        if doc_status:
            state['status_code'] = doc_status

        if row_index in header_indexes:
            continue

        cells = [_clean(cell) for cell in row]
        serial_index = next(
            (idx for idx, cell in enumerate(cells[:2]) if _SERIAL_RE.match(cell)),
            None,
        )

        if serial_index is not None:
            if state.get('current'):
                state['records'].append(state['current'])
            serial = _SERIAL_RE.match(cells[serial_index]).group(1)
            state['current'] = {
                **{column: '' for column in COMMENT_SHEET_COLUMNS},
                's_no': serial,
                'status_code': state.get('status_code', ''),
                'page_number': state['page_number'],
            }

        current = state.get('current')
        if not current:
            continue

        for cell_index, value in enumerate(cells):
            if not value or cell_index == serial_index:
                continue
            lower = value.lower()
            if ('document status code' in lower
                    or 'company comments' in lower
                    or 'contractor / vendor reply' in lower
                    or lower.startswith('rev.')):
                continue
            field = _field_for_cell(cell_index, len(cells), header_map)
            _append(current, field, value)


def extract_comments_from_pages(pdf_bytes: bytes, page_indices: List[int]) -> List[Dict]:
    """Extract logical review rows across header and continuation pages."""
    if not page_indices:
        return []

    state = {'records': [], 'current': None, 'status_code': '', 'page_number': None}
    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    try:
        for pidx in sorted(page_indices):
            if pidx < 0 or pidx >= len(doc):
                continue
            state['page_number'] = pidx + 1
            try:
                tables = doc[pidx].find_tables()
            except Exception as exc:
                logger.warning('[IOWF] find_tables failed on CRS page %d: %s', pidx, exc)
                continue
            for table in tables:
                try:
                    rows = table.extract()
                except Exception:
                    continue
                if rows:
                    _consume_rows(rows, state)
    finally:
        doc.close()

    if state.get('current'):
        state['records'].append(state['current'])

    comments = []
    for record in state['records']:
        if not (record['company_comment'] or record['contractor_reply']
                or record['company_decision']):
            continue
        status = _normalise_status_code(record.get('status_code', ''))
        record['status_code'] = status['code']
        record['status_meaning'] = status['meaning']
        comments.append(record)

    logger.info('[IOWF] Extracted %d logical comments from %d pages',
                len(comments), len(page_indices))
    return comments
