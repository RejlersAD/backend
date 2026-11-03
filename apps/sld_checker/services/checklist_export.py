"""
AI-SLD-01 checklist report export
==================================

FILLS the engineer's template workbook (openpyxl on the stored .xlsx keeps
styles/merges/validations) and adds an "AI Comment Register" sheet.
Mirrors apps.pid_verification_v2.services.checklist_export; finding
identifiers in the register are the checker's E/L/D/Q numbers.
"""
from __future__ import annotations

import io
import logging
from copy import copy

import openpyxl
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation

from .checklist_eval import default_template, evaluate

logger = logging.getLogger(__name__)

SEV = {'Critical': 3, 'Major': 2, 'Minor': 1}
RES = {'Non-Conformance': 0, 'Query': 1, 'Not Verifiable': 2}
SHEET_NAME = 'AI SLD Checklist'

REGISTER_HEADER = ['Item No.', 'Finding No.', 'Sheet', 'Tag / Object', 'Finding',
                   'Rule', 'AI Result', 'Severity', 'Suggested action',
                   'AI Check Mode', 'Engineer Verification', 'Remarks']
REGISTER_WIDTHS = [7, 9, 22, 28, 52, 14, 14, 9, 36, 12, 14, 26]


class TemplateChanged(Exception):
    """Column A of the template no longer matches the imported items."""


def _register_sheet(wb, ws, reg):
    src = ws.cell(7, 1)
    if 'AI Comment Register' in wb.sheetnames:
        del wb['AI Comment Register']
    cr = wb.create_sheet('AI Comment Register', 2)
    cr['A1'] = 'AI Comment Register'
    cr['A1'].font, cr['A1'].fill = copy(ws['A1'].font), copy(ws['A1'].fill)
    cr.merge_cells('A1:L1')
    cr['A2'] = 'One row per finding and per item the AI could not verify. Columns K-L are for the engineer.'
    cr['A2'].font = Font(italic=True, size=9)
    cr.merge_cells('A2:L2')
    for j, h in enumerate(REGISTER_HEADER, 1):
        c = cr.cell(3, j, h)
        c.font, c.fill, c.alignment, c.border = (copy(src.font), copy(src.fill),
                                                 copy(src.alignment), copy(src.border))
    reg.sort(key=lambda x: (-SEV.get(x[7], 0), RES.get(x[6], 9), str(x[1])))
    side = Side(style='thin', color='FFBFBFBF')
    bd = Border(left=side, right=side, top=side, bottom=side)
    yellow = PatternFill('solid', fgColor='FFFFF2CC')
    for i, row in enumerate(reg, 4):
        for j, v in enumerate(row, 1):
            c = cr.cell(i, j, v)
            c.alignment = Alignment(wrap_text=True, vertical='top')
            c.border = bd
            c.font = Font(size=9)
            if j >= 11:
                c.fill = yellow
    for col, w in zip('ABCDEFGHIJKL', REGISTER_WIDTHS):
        cr.column_dimensions[col].width = w
    n = 3 + max(len(reg), 1)
    dv = DataValidation(type='list', formula1='"Agree,Disagree,Modified"', allow_blank=True)
    cr.add_data_validation(dv)
    dv.add(f'K4:K{n}')
    cr.freeze_panes = 'C4'
    cr.auto_filter.ref = f'A3:L{n}'
    cr.page_setup.orientation = 'landscape'


def build_checklist_report(analysis, template=None):
    """Fill the AI-SLD-01 workbook for one analysis. (bytes, filename) or
    (None, None) when no template has been imported."""
    from ..models import SldFinding, SldSheet

    template = template or default_template()
    if template is None:
        return None, None
    ev = evaluate(analysis, template=template)
    if ev is None:
        return None, None

    findings = {str(f.id): f for f in SldFinding.objects.filter(analysis_id=analysis.id)}
    from ..models import SldNode
    node_tags = {str(n.id): n.tag for n in SldNode.objects.filter(analysis_id=analysis.id)}
    sheets = {s.id: s for s in SldSheet.objects.filter(analysis_id=analysis.id)}
    first_sheet = next(iter(sheets.values()), None)
    drawing_no = (first_sheet.drawing_no if first_sheet else '') or 'Drawing number to confirm'
    rev = first_sheet.rev if first_sheet else ''

    with template.source_file.open('rb') as fh:
        wb = openpyxl.load_workbook(io.BytesIO(fh.read()))
    if SHEET_NAME not in wb.sheetnames:
        raise TemplateChanged(f"Sheet '{SHEET_NAME}' not found in {template.code} v{template.version}")
    ws = wb[SHEET_NAME]

    import datetime
    today = datetime.date.today().strftime('%d-%b-%Y')
    for cell, value in (('C2', analysis.project.project_name),
                        ('F3', f'{drawing_no} / Rev {rev}'.rstrip(' /')),
                        ('C4', f'RADAI SLD Checker AI-SLD-01 (run {today})'),
                        ('F4', today)):
        try:
            ws[cell] = value
        except Exception:
            pass

    reg = []
    for item in ev['items']:
        r = item['xlsx_row']
        if str(ws.cell(r, 1).value or '').strip() != item['no']:
            raise TemplateChanged(f"{item['no']} expected at row {r}")
        ws.cell(r, 8).value = item['applicability']
        ws.cell(r, 9).value = item['ai_result'] if item['applicability'] == 'Y' else None
        ws.cell(r, 10).value = item['engineer_verification']
        ws.cell(r, 11).value = item['remarks'] or None
        if item.get('evidence'):
            note = 'RADAI AI: ' + item['evidence']
            if item.get('finding_numbers'):
                note += '\nFindings: ' + ', '.join(item['finding_numbers'])
            c = Comment(note, 'RADAI')
            c.width, c.height = 320, 140
            ws.cell(r, 9).comment = c
        if item['applicability'] == 'Y' and item['ai_result'] in RES:
            real = [findings[fid] for fid in item['finding_ids'] if fid in findings]
            if real:
                for f in real:
                    sh = sheets.get(f.sheet_id)
                    reg.append([item['no'], f.number or '-',
                                sh.drawing_no if sh and sh.drawing_no else
                                (f'Sheet {sh.page_index + 1:03d}' if sh else '-'),
                                ', '.join(node_tags.get(nid, '') for nid in (f.node_ids or [])) or '-',
                                f.found or f.title, f.rule_id, item['ai_result'],
                                item['severity'], f.action or '-', item['mode'],
                                item['engineer_verification'], item['remarks'] or None])
            else:
                nv = item['ai_result'] == 'Not Verifiable'
                reg.append([item['no'], '-', drawing_no, '-', item.get('evidence') or '',
                            ('Missing: ' + ', '.join(item['missing_inputs'])) if nv else '-',
                            item['ai_result'], item['severity'],
                            'Provide the missing input, then re-run.' if nv else 'Engineer to confirm.',
                            item['mode'], item['engineer_verification'],
                            item['remarks'] or None])
    _register_sheet(wb, ws, reg)

    out = io.BytesIO()
    wb.save(out)
    safe_no = str(drawing_no).replace('/', '-')
    return out.getvalue(), f'AI_SLD_Review_Checklist_01_{safe_no}_Rev{rev or "—"}.xlsx'
