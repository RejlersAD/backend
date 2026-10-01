"""
Valve MTO — Excel exporter.

Deliberately simpler than the frontend's own client-side exporter
(src/config/valveMTOExporter.js, a full 5-sheet template with Notes/
Pivot sheets and styled header blocks matching a specific engineering
deliverable format) — this server-side export exists so the persisted
(server-of-record) data can be downloaded directly without round-
tripping through the browser's localStorage-driven UI state. One sheet
per `tab` value (island/field/combined), each with every canonical
column. openpyxl only, no third-party templates — same convention as
apps.instrument_io_workflow.excel_export.
"""
from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

_HEADER_FILL = PatternFill(start_color='003366', end_color='003366', fill_type='solid')
_HEADER_FONT = Font(color='FFFFFF', bold=True)
_CENTER = Alignment(horizontal='center', vertical='center', wrap_text=True)

_COLUMNS = [
    ('tag_number', 'Tag Number'), ('valve_type', 'Type'), ('pms_class', 'PMS Class'),
    ('rating', 'Rating'), ('size_primary', 'Size 1'), ('size_secondary_2', 'Size 2'),
    ('size_secondary', 'Bore'),
    ('line_number', 'Line Number'), ('line_list_ref', 'Line List Ref'),
    ('description', 'Description'), ('qty_island', 'Qty Island'), ('qty_field', 'Qty Field'),
    ('qty_combined', 'Qty Combined'), ('unit', 'Unit'), ('area', 'Area'),
    ('operational_status', 'Operational Status'), ('remarks', 'Remarks'),
]

_TAB_SHEETS = [('island', 'ISLAND'), ('field', 'FIELD'), ('combined', 'COMBINED')]


def _write_sheet(ws, rows):
    for idx, (_, label) in enumerate(_COLUMNS, start=1):
        cell = ws.cell(row=1, column=idx, value=label)
        cell.fill, cell.font, cell.alignment = _HEADER_FILL, _HEADER_FONT, _CENTER
    for r_idx, row in enumerate(rows, start=2):
        for c_idx, (field, _) in enumerate(_COLUMNS, start=1):
            ws.cell(row=r_idx, column=c_idx, value=getattr(row, field))


def export_project_to_xlsx(project) -> bytes:
    """One sheet per tab (ISLAND/FIELD/COMBINED) — a row with an unknown/
    blank `tab` value falls into whichever sheet matches its `area`
    instead (case-insensitively), so nothing a user entered gets silently
    dropped from the export just because `tab` wasn't set."""
    wb = Workbook()
    all_rows = list(project.rows.all())

    first = True
    for tab_key, sheet_title in _TAB_SHEETS:
        ws = wb.active if first else wb.create_sheet(sheet_title)
        if first:
            ws.title = sheet_title
        first = False
        rows = [
            r for r in all_rows
            if (r.tab or r.area or '').strip().lower() == tab_key
        ]
        _write_sheet(ws, rows)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
