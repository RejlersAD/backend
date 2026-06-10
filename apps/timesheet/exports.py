"""
Excel + PDF exporters for daily and monthly time-sheet reports.

Reuses libraries that are already in requirements.txt (openpyxl, reportlab).
Returns Django HttpResponse objects with proper content-type + filename.
"""
from __future__ import annotations

import datetime as dt
import io
from typing import Iterable

from django.http import HttpResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import services as ts_services_sql
from . import mirror_services as ts_services_mirror
from . import config as ts_config


def _svc():
    """Soft-coded backend dispatcher (mirrors views._svc)."""
    return ts_services_mirror if ts_config.DATA_SOURCE == 'mirror' else ts_services_sql


# ─────────────────────────────────────────────────────────────────────────────
# Excel
# ─────────────────────────────────────────────────────────────────────────────
HEADER_FILL = PatternFill('solid', fgColor='003366')
HEADER_FONT = Font(name='Calibri', size=11, bold=True, color='FFFFFF')


def export_daily_excel(date: str | None = None) -> HttpResponse:
    payload = _svc().daily_report(date)
    wb = Workbook()
    ws = wb.active
    ws.title = f"Daily {payload['date']}"

    headers = ['Employee Code', 'Name', 'Email', 'Department',
               'First In', 'Last Out', 'Hours', 'Late?', 'Full Day?', 'Matched']
    _write_header(ws, headers)
    for r in payload['rows']:
        ws.append([
            r.get('employee_code'),
            r.get('radai_full_name') or r.get('name') or '',
            r.get('radai_email') or r.get('email') or '',
            r.get('radai_department') or r.get('department') or '',
            _fmt(r.get('first_in')),
            _fmt(r.get('last_out')),
            r.get('hours_worked'),
            'Yes' if r.get('is_late') else 'No',
            'Yes' if r.get('is_full_day') else 'No',
            r.get('matched_by') or 'unmatched',
        ])
    _autosize(ws)
    return _xlsx_response(wb, f'timesheet_daily_{payload["date"]}.xlsx')


def export_monthly_excel(year: int | None = None, month: int | None = None) -> HttpResponse:
    payload = _svc().monthly_report(year, month)
    wb = Workbook()
    ws = wb.active
    ws.title = f"Monthly {payload['year']}-{payload['month']:02d}"

    headers = ['Employee Code', 'Name', 'Email', 'Department',
               'Days Present', 'Full Days', 'Half Days', 'Late Arrivals',
               'Total Hours', 'Avg Hours/Day', 'Matched']
    _write_header(ws, headers)
    for r in payload['rows']:
        ws.append([
            r.get('employee_code'),
            r.get('radai_full_name') or r.get('name') or '',
            r.get('radai_email') or r.get('email') or '',
            r.get('radai_department') or r.get('department') or '',
            r.get('days_present'),
            r.get('full_days'),
            r.get('half_days'),
            r.get('late_arrivals'),
            r.get('total_hours'),
            r.get('avg_hours_per_day'),
            r.get('matched_by') or 'unmatched',
        ])
    _autosize(ws)

    # Per-day drilldown on a second sheet
    ws2 = wb.create_sheet('Per-Day Detail')
    _write_header(ws2, ['Employee Code', 'Name', 'Date', 'First In', 'Last Out', 'Hours'])
    for r in payload['rows']:
        for d in r.get('days_detail') or []:
            ws2.append([
                r.get('employee_code'),
                r.get('radai_full_name') or r.get('name') or '',
                d.get('date'),
                _fmt(d.get('first_in')),
                _fmt(d.get('last_out')),
                d.get('hours'),
            ])
    _autosize(ws2)
    return _xlsx_response(wb, f'timesheet_monthly_{payload["year"]}_{payload["month"]:02d}.xlsx')


def _write_header(ws, headers: Iterable[str]):
    ws.append(list(headers))
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal='center', vertical='center')


def _autosize(ws):
    for col_idx, col_cells in enumerate(ws.columns, 1):
        max_len = max((len(str(c.value)) for c in col_cells if c.value is not None), default=10)
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max(max_len + 2, 12), 40)


def _fmt(v) -> str:
    if v is None:
        return ''
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    return str(v)


def _xlsx_response(wb, filename: str) -> HttpResponse:
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = HttpResponse(
        buf.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp


# ─────────────────────────────────────────────────────────────────────────────
# PDF (reportlab — lightweight summary, not pixel-perfect like Excel)
# ─────────────────────────────────────────────────────────────────────────────
def export_monthly_pdf(year: int | None = None, month: int | None = None) -> HttpResponse:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    )

    payload = _svc().monthly_report(year, month)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                            leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('title', parent=styles['Title'],
                                 fontSize=16, textColor=colors.HexColor('#003366'))

    elements = []
    elements.append(Paragraph(
        f"Time Sheet — Monthly Report ({payload['year']}-{payload['month']:02d})",
        title_style,
    ))
    elements.append(Spacer(1, 4 * mm))
    elements.append(Paragraph(
        f"Period: {payload['start']} to {payload['end']}<br/>"
        f"Working days in month: {payload['working_days_in_month']}<br/>"
        f"Total employees with attendance: {len(payload['rows'])}",
        styles['Normal'],
    ))
    elements.append(Spacer(1, 6 * mm))

    headers = ['Employee', 'Email', 'Dept', 'Days', 'Full',
               'Half', 'Late', 'Hours', 'Avg/Day']
    data = [headers]
    for r in payload['rows']:
        data.append([
            (r.get('radai_full_name') or r.get('name') or r.get('employee_code') or '')[:32],
            (r.get('radai_email') or r.get('email') or '')[:36],
            (r.get('radai_department') or r.get('department') or '')[:20],
            r.get('days_present'),
            r.get('full_days'),
            r.get('half_days'),
            r.get('late_arrivals'),
            r.get('total_hours'),
            r.get('avg_hours_per_day'),
        ])
    table = Table(data, repeatRows=1, hAlign='LEFT')
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#003366')),
        ('TEXTCOLOR',  (0, 0), (-1, 0), colors.whitesmoke),
        ('FONTNAME',   (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE',   (0, 0), (-1, -1), 8),
        ('GRID',       (0, 0), (-1, -1), 0.25, colors.HexColor('#cccccc')),
        ('ALIGN',      (3, 1), (-1, -1), 'RIGHT'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f5f7fb')]),
    ]))
    elements.append(table)
    doc.build(elements)
    buf.seek(0)
    resp = HttpResponse(buf.getvalue(), content_type='application/pdf')
    resp['Content-Disposition'] = (
        f'attachment; filename="timesheet_monthly_{payload["year"]}_{payload["month"]:02d}.pdf"'
    )
    return resp
