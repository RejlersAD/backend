"""
Excel exporter for an IOListDocument.

Three sheets:
  - "Comments Resolution Sheet" (5 columns + status meaning + linked tags)
  - "IO List" (all canonical columns, or only the ones relevant to this
    document's own document_type — see columns= / _relevant_columns_for)
  - "Legend Check" (any legend findings recorded against this document)

openpyxl only — no third-party templates required.
"""

from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment

from .services.config import (
    COMMENT_SHEET_COLUMNS, IO_LIST_CANONICAL_COLUMNS,
)


_HEADER_FILL = PatternFill(start_color='003366', end_color='003366',
                            fill_type='solid')
_HEADER_FONT = Font(color='FFFFFF', bold=True)
_CENTER      = Alignment(horizontal='center', vertical='center', wrap_text=True)

# Mirrors airflow_frontend/src/config/ioListWorkflow.config.js's
# IO_PREVIEW_COMMON/TABLE_ONLY/PID_DRAWING_ONLY_COLUMNS split — an io_list
# table document never populates the P&ID-only fields and vice versa, so
# "Show relevant columns only" hides the half of IO_LIST_CANONICAL_COLUMNS
# that's guaranteed blank for this document's own document_type.
_COMMON_COLUMNS = ['tag_number', 'instrument_type', 'service_description']
_TABLE_ONLY_COLUMNS = [
    'loop_number', 'pid_no', 'hmi_description', 'io_type', 'system',
    'signal_type', 'unit', 'alarm_priority',
]
_PID_DRAWING_ONLY_COLUMNS = [
    'kind', 'equipment_tag', 'line_tag', 'symbol_type', 'location',
]


def _relevant_columns_for(document_type: str) -> list[str]:
    """Canonical columns actually populated by this document's own
    extraction path, in IO_LIST_CANONICAL_COLUMNS order."""
    wanted = set(_COMMON_COLUMNS) | set(
        _PID_DRAWING_ONLY_COLUMNS if document_type == 'pid_drawing'
        else _TABLE_ONLY_COLUMNS
    )
    return [c for c in IO_LIST_CANONICAL_COLUMNS if c in wanted]


def _write_header(ws, columns):
    for idx, col in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=idx, value=col)
        cell.fill, cell.font, cell.alignment = _HEADER_FILL, _HEADER_FONT, _CENTER


def export_document_to_xlsx(document, columns: str = 'all') -> bytes:
    """columns: 'all' (every canonical column) or 'relevant' (only the
    columns applicable to this document's own document_type — see
    _relevant_columns_for)."""
    wb = Workbook()

    # Sheet 1 — comments
    ws_c = wb.active
    ws_c.title = 'Comments Resolution Sheet'
    headers_c = COMMENT_SHEET_COLUMNS + ['status_meaning', 'page_number',
                                          'linked_tags']
    _write_header(ws_c, headers_c)
    for r_idx, c in enumerate(document.extracted_comments.all(), start=2):
        row = [
            c.s_no, c.company_comment, c.contractor_reply, c.company_decision,
            c.status_code, c.status_meaning, c.page_number,
            ', '.join(c.linked_tags or []),
        ]
        for c_idx, val in enumerate(row, start=1):
            ws_c.cell(row=r_idx, column=c_idx, value=val)

    # Sheet 2 — IO rows
    ws_r = wb.create_sheet('IO List')
    body_columns = (
        _relevant_columns_for(document.document_type)
        if columns == 'relevant' else list(IO_LIST_CANONICAL_COLUMNS)
    )
    body_columns = [c for c in body_columns if c != 'tag_number']
    headers_r = ['tag_number', 'page_number'] + body_columns
    _write_header(ws_r, headers_r)
    for r_idx, row in enumerate(document.extracted_rows.all(), start=2):
        d = row.data or {}
        ws_r.cell(row=r_idx, column=1, value=row.tag_number)
        ws_r.cell(row=r_idx, column=2, value=row.page_number)
        for c_idx, col in enumerate(headers_r[2:], start=3):
            ws_r.cell(row=r_idx, column=c_idx, value=d.get(col, ''))

    # Sheet 3 — legend findings (whatever was recorded at extraction time)
    ws_l = wb.create_sheet('Legend Check')
    headers_l = ['section', 'source', 'field', 'value', 'severity', 'issue', 'expected']
    _write_header(ws_l, headers_l)
    for r_idx, finding in enumerate(document.legend_findings or [], start=2):
        row = [finding.get(k, '') for k in headers_l]
        for c_idx, val in enumerate(row, start=1):
            ws_l.cell(row=r_idx, column=c_idx, value=val)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
