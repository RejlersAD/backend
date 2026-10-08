"""
Electrical Comparison — Excel exporter.

One sheet, every saved ElectricalComparisonResult row for the job (or,
when `source` is given, just that comparison's rows — backs the
frontend's per-tab Export Excel button).
openpyxl only, no third-party templates — same convention as
apps.valve_mto.excel_export / apps.instrument_io_workflow.excel_export.
"""
from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

_HEADER_FILL = PatternFill(start_color='003366', end_color='003366', fill_type='solid')
_HEADER_FONT = Font(color='FFFFFF', bold=True)
_CENTER = Alignment(horizontal='center', vertical='center', wrap_text=True)

_COLUMNS = [
    ('tag_number', 'Tag Number'),
    ('equipment_type', 'Equipment Type'),
    ('description', 'Description'),
    ('status', 'Status'),
    ('source', 'Source'),
    ('remarks', 'Remarks'),
]


def export_job_to_xlsx(job, source: str | None = None) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = 'Electrical Comparison'

    for idx, (_, label) in enumerate(_COLUMNS, start=1):
        cell = ws.cell(row=1, column=idx, value=label)
        cell.fill, cell.font, cell.alignment = _HEADER_FILL, _HEADER_FONT, _CENTER

    rows = job.results.filter(source=source) if source else job.results.all()
    for r_idx, row in enumerate(rows, start=2):
        for c_idx, (field, _) in enumerate(_COLUMNS, start=1):
            ws.cell(row=r_idx, column=c_idx, value=getattr(row, field))

    for col_cells in ws.columns:
        length = max((len(str(c.value)) if c.value is not None else 0) for c in col_cells)
        ws.column_dimensions[col_cells[0].column_letter].width = min(max(length + 2, 12), 60)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
