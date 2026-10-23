"""
Valve MTO — Excel exporter.

Deliberately simpler than the frontend's own client-side exporter
(src/config/valveMTOExporter.js, a full 5-sheet template with Notes/
Pivot sheets and styled header blocks matching a specific engineering
deliverable format) — this server-side export exists so the persisted
(server-of-record) data can be downloaded directly without round-
tripping through the browser's localStorage-driven UI state. One sheet
per `tab` value (island/field/combined), using the configured display
columns without deleting legacy stored fields. openpyxl only — same convention as
apps.instrument_io_workflow.excel_export.
"""
from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from .specification_lookup import size_nb_from_line_number

_HEADER_FILL = PatternFill(start_color='003366', end_color='003366', fill_type='solid')
_HEADER_FONT = Font(color='FFFFFF', bold=True)
_CENTER = Alignment(horizontal='center', vertical='center', wrap_text=True)

_COLUMNS = [
    ('tag_number', 'Tag Number'), ('valve_type', 'Type'), ('pms_class', 'PMS Class'),
    ('piping_class', 'Piping Class'), ('rating', 'Rating'), ('facing', 'Facing'),
    ('size_primary', 'SIZE (NB)'),
    ('size_secondary', 'Bore'),
    ('pid_number', 'P&ID Number'), ('line_number', 'Line Number'),
    ('description', 'Description'), ('qty_island', 'Qty Island'), ('qty_field', 'Qty Field'),
    ('qty_combined', 'Qty Combined'), ('qty_observed', 'Observed Qty (Review)'), ('unit', 'Unit'), ('area', 'Area'),
    ('operational_status', 'Operational Status'), ('remarks', 'Remarks'),
    ('confidence', 'Confidence'), ('location_hint', 'Location'),
]

_TAB_SHEETS = [('island', 'ISLAND'), ('field', 'FIELD'), ('combined', 'COMBINED'), ('unassigned', 'UNASSIGNED REVIEW')]


_FULL_EXTRA_COLUMNS = [
    ('variant', 'Variant'), ('category', 'Category'), ('type_basis', 'Type Basis'),
    ('scope_status', 'Scope Status'), ('piping_class_source', 'Class Source'),
    ('material_source', 'Material Source'), ('pid_rev', 'Rev'),
    ('page', 'Page'), ('pos_x', 'X'), ('pos_y', 'Y'),
    ('confidence_score', 'Confidence Score'), ('symbol_group_id', 'Symbol Group'),
]


def _write_sheet(ws, rows, header=None, columns=None):
    columns = columns or _COLUMNS
    start = 1
    if header:
        # Header block carries P&ID number + rev (§6) — never blank.
        ws.cell(row=1, column=1, value='P&ID NUMBER').font = Font(bold=True)
        ws.cell(row=1, column=2, value=header.get('pid_number') or 'N/A')
        ws.cell(row=2, column=1, value='REV').font = Font(bold=True)
        ws.cell(row=2, column=2, value=header.get('pid_rev') or 'N/A')
        ws.cell(row=3, column=1, value='PROJECT').font = Font(bold=True)
        ws.cell(row=3, column=2, value=header.get('project_name') or '')
        start = 5
    for idx, (_, label) in enumerate(columns, start=1):
        cell = ws.cell(row=start, column=idx, value=label)
        cell.fill, cell.font, cell.alignment = _HEADER_FILL, _HEADER_FONT, _CENTER
    for r_idx, row in enumerate(rows, start=start + 1):
        for c_idx, (field, _) in enumerate(columns, start=1):
            value = getattr(row, field)
            if field == 'size_primary' and not value:
                value = size_nb_from_line_number(row.line_number)
            # Never export blank P&ID NUMBER / PIPING MATERIAL CLASS (§6).
            if field == 'pid_number' and not value:
                value = (header or {}).get('pid_number', '')
            if field == 'pms_class' and not value:
                value = f'PER PMS {row.piping_class}' if row.piping_class else 'PER PMS'
            ws.cell(row=r_idx, column=c_idx, value=value)


def _project_header(project, all_rows):
    pid_number = next((r.pid_number for r in all_rows if r.pid_number), '')
    pid_rev = next((r.pid_rev for r in all_rows if r.pid_rev), '')
    return {'pid_number': pid_number, 'pid_rev': pid_rev,
            'project_name': project.project_name}


def export_project_to_xlsx(project, fmt='mto') -> bytes:
    """fmt (§6 Export Center):
      'mto'   — customer workbook: one sheet per tab + header block.
      'full'  — single FULL DATA sheet with every stored field.
      'order' — ORDER LIST sheet: in-scope NEW manual valves only."""
    """One sheet per tab (ISLAND/FIELD/COMBINED) — a row with an unknown/
    blank `tab` value falls into whichever sheet matches its `area`
    instead (case-insensitively), so nothing a user entered gets silently
    dropped from the export just because `tab` wasn't set."""
    wb = Workbook()
    all_rows = list(project.rows.all())
    header = _project_header(project, all_rows)

    if fmt == 'order':
        ws = wb.active
        ws.title = 'ORDER LIST'
        order_rows = [r for r in all_rows if (r.qty_combined or 0) > 0]
        _write_sheet(ws, order_rows, header=header)
    elif fmt == 'full':
        ws = wb.active
        ws.title = 'FULL DATA'
        _write_sheet(ws, all_rows, header=header,
                     columns=_COLUMNS + _FULL_EXTRA_COLUMNS)
    else:  # 'mto'
        first = True
        for tab_key, sheet_title in _TAB_SHEETS:
            ws = wb.active if first else wb.create_sheet(sheet_title)
            if first:
                ws.title = sheet_title
            first = False
            rows = [
                r for r in all_rows
                if ((r.tab or r.area or '').strip().lower() == tab_key
                    or (tab_key == 'unassigned' and (r.tab or r.area or '').strip().lower()
                        not in {'island', 'field', 'combined'}))
            ]
            _write_sheet(ws, rows, header=header)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
