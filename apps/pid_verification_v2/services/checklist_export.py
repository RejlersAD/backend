"""
AI-PID-02 checklist report export (spec §2.6)
=============================================

FILLS the engineer's template workbook (openpyxl on the stored .xlsx keeps
styles, merges, validations, conditional formats and the Summary formulas)
— never rebuilds a layout. Adds the AI Comment Register sheet after
'Summary'; nothing else in the workbook changes.
"""
from __future__ import annotations

import io
import logging
from copy import copy

import openpyxl
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation

from .checklist_ai02 import evaluate_sheet

logger = logging.getLogger(__name__)

SEV = {'Critical': 3, 'Major': 2, 'Minor': 1}
RES = {'Non-Conformance': 0, 'Query': 1, 'Not Verifiable': 2}
RULES_VERSION = 'AI-PID-02'

REGISTER_HEADER = ['Item No.', 'Finding ID', 'Sheet', 'Grid', 'Tag / Object', 'Finding',
                   'Reference doc & clause', 'AI Result', 'Severity', 'Suggested correction',
                   'Confidence', 'AI Check Mode', 'Engineer Verification', 'Remarks']
REGISTER_WIDTHS = [7, 8, 20, 7, 30, 52, 32, 14, 9, 36, 10, 12, 14, 26]


class TemplateChanged(Exception):
    """Column A of the template no longer matches the imported items —
    the caller answers HTTP 409 'template changed, re-import it'."""


def _zones_reader(doc):
    """Lazy per-page grid-zone reader for findings whose report_meta was
    written before the register fields existed (filled at finalize since
    30 Oct 2026). Reads the PDF through storage, never .path."""
    cache = {}
    pdf_holder = {}

    def zones_for(page):
        if page in cache:
            return cache[page]
        zones = None
        try:
            import fitz
            if 'doc' not in pdf_holder:
                from .scene3d_cache import get_pdf_bytes
                pdf_holder['doc'] = fitz.open(stream=get_pdf_bytes(doc), filetype='pdf')
            pdf = pdf_holder['doc']
            if 0 <= page < pdf.page_count:
                from .grid_zones import read_grid_zones
                zones = read_grid_zones(pdf[page])
        except Exception:
            logger.debug('[ChecklistExport] grid zones unavailable for page %s', page, exc_info=True)
        cache[page] = zones
        return zones
    return zones_for


def _finding_view(f, sheet_label, zones_for=None, page=0, label=None):
    """Uniform dict for a PIDVFinding row (report_meta with fallbacks)."""
    rm = f.report_meta or {}
    sev = getattr(f, 'severity', '')
    grid = rm.get('grid') or '-'
    if grid == '-' and zones_for and isinstance(f.bbox, list) and len(f.bbox) == 4:
        from .grid_zones import grid_label
        grid = grid_label(zones_for(page), (f.bbox[0] + f.bbox[2]) / 2, (f.bbox[1] + f.bbox[3]) / 2)
    return {
        'sheet_label': rm.get('sheet_label') or sheet_label,
        'grid': grid,
        'tag': rm.get('tag') or '-',
        'finding_text': rm.get('finding_text') or f.issue_observed,
        'reference': rm.get('reference') or f'rule {f.rule_id}',
        'suggested_correction': rm.get('suggested_correction') or f.action_required,
        'confidence': rm.get('confidence') or ('High' if sev in ('critical', 'major') else 'Medium'),
        'ai_result': rm.get('ai_result') or ('Non-Conformance' if sev in ('critical', 'major') else 'Query'),
        'engineer_verification': rm.get('engineer_verification'),
        'remarks': rm.get('remarks') or '',
        'n': f.sl_no,  # legacy F-number
        'label': label or f'F{f.sl_no}',  # checklist-serial display id (2 Nov 2026)
    }


def _register_sheet(wb, ws, header, reg):
    src = ws.cell(7, 1)
    cr = wb.create_sheet('AI Comment Register', 2)
    cr['A1'] = 'AI Comment Register - ' + (header.get('doc_rev') or '')
    cr['A1'].font, cr['A1'].fill = copy(ws['A1'].font), copy(ws['A1'].fill)
    cr.merge_cells('A1:N1')
    cr['A2'] = 'One row per finding and per item the AI could not verify. Columns M-N are for the engineer.'
    cr['A2'].font = Font(italic=True, size=9)
    cr.merge_cells('A2:N2')
    for j, h in enumerate(REGISTER_HEADER, 1):
        c = cr.cell(3, j, h)
        c.font, c.fill, c.alignment, c.border = copy(src.font), copy(src.fill), copy(src.alignment), copy(src.border)
    reg.sort(key=lambda x: (-SEV.get(x[8], 0), RES.get(x[7], 9), [int(p) for p in x[0].split('.')]))
    side = Side(style='thin', color='FFBFBFBF')
    bd = Border(left=side, right=side, top=side, bottom=side)
    yellow = PatternFill('solid', fgColor='FFFFF2CC')
    for i, row in enumerate(reg, 4):
        for j, v in enumerate(row, 1):
            c = cr.cell(i, j, v)
            c.alignment = Alignment(wrap_text=True, vertical='top')
            c.border = bd
            c.font = Font(size=9)
            if j >= 13:
                c.fill = yellow
    for col, w in zip('ABCDEFGHIJKLMN', REGISTER_WIDTHS):
        cr.column_dimensions[col].width = w
    n = 3 + max(len(reg), 1)
    dv = DataValidation(type='list', formula1='"Agree,Disagree,Modified"', allow_blank=True)
    cr.add_data_validation(dv)
    dv.add(f'M4:M{n}')
    for cf in ws.conditional_formatting:
        for rule in cf.rules:
            cr.conditional_formatting.add(f'H4:H{n}', rule)
    cr.freeze_panes = 'C4'
    cr.auto_filter.ref = f'A3:N{n}'
    cr.page_setup.orientation = 'landscape'
    cr.sheet_properties.pageSetUpPr.fitToPage = True
    cr.page_setup.fitToWidth, cr.page_setup.fitToHeight = 1, 0


def _fill_workbook(wb, template, header, items, findings_by_id, sheet_label, extra_register_rows=()):
    """Shared filler. `items` = [{no, applicability, ai_result,
    engineer_verification, remarks, evidence, finding_ids,
    missing_inputs, xlsx_row, severity, mode}] (worst-of rows for
    scope=all)."""
    ws = wb['AI P&ID Checklist']
    for cell, key in (('C2', 'project_name'), ('F2', 'title'), ('C3', 'project_no'), ('F3', 'doc_rev'),
                      ('C4', 'tool'), ('F4', 'date'), ('C5', 'reviewer'), ('F5', 'approver')):
        if header.get(key):
            ws[cell] = header[key]

    reg = []
    for item in items:
        r = item['xlsx_row']
        if str(ws.cell(r, 1).value).strip() != item['no']:
            raise TemplateChanged(f"{item['no']} expected at row {r}")
        ws.cell(r, 8).value = item.get('applicability')
        ws.cell(r, 9).value = item.get('ai_result') if item.get('applicability') == 'Y' else None
        ws.cell(r, 10).value = item.get('engineer_verification')
        ws.cell(r, 11).value = item.get('remarks') or None
        if item.get('evidence'):
            note = 'RADAI AI: ' + item['evidence']
            _fnos = item.get('finding_labels') or item.get('finding_nos') or item.get('finding_ids')
            if _fnos:
                note += '\nFindings: ' + ', '.join(str(f) if str(f).startswith('F') or '.' in str(f) else f'F{f}' for f in _fnos)
            c = Comment(note, 'RADAI')
            c.width, c.height = 320, 140
            ws.cell(r, 9).comment = c
        if item.get('applicability') == 'Y' and item.get('ai_result') in RES:
            fids = item.get('finding_ids') or []
            real = [findings_by_id[fid] for fid in fids if fid in findings_by_id]
            if real:
                for fid, f in zip(fids, real):
                    reg.append([item['no'], f.get('label') or f"F{f.get('n') or fid}", f['sheet_label'], f['grid'], f['tag'],
                                f['finding_text'], f['reference'], f['ai_result'], item['severity'],
                                f['suggested_correction'], f['confidence'], item['mode'],
                                f['engineer_verification'], f['remarks']])
            else:
                nv = item['ai_result'] == 'Not Verifiable'
                reg.append([item['no'], '-', sheet_label, '-', '-', item.get('evidence') or '',
                            ('Missing: ' + ', '.join(item.get('missing_inputs') or [])) if nv else '-',
                            item['ai_result'], item['severity'],
                            'Provide the missing input, then re-run.' if nv else 'Engineer to confirm.',
                            '-', item['mode'], item.get('engineer_verification'), item.get('remarks') or None])
    reg.extend(extra_register_rows)
    _register_sheet(wb, ws, header, reg)


def build_checklist_report(doc, page: int, template=None):
    """One P&ID (one sheet of one document). Returns (bytes, filename)."""
    from apps.pid_verification_v2.models import PIDVFinding, PIDVSheet

    template = template or evaluate_template(doc)
    ev = evaluate_sheet(doc, page, template)
    if ev is None:
        return None, None

    drawing = doc.drawings.filter(page_index=page).first()
    tb = ((drawing.metadata or {}).get('title_block') or {}) if drawing else {}
    sheet = PIDVSheet.objects.filter(document=doc, page_index=page).first()
    drawing_no = (sheet.drawing_no if sheet else None) or tb.get('drawing_number') or doc.file_name
    rev = (sheet.rev if sheet else '') or tb.get('revision') or ''
    sheet_label = f'{drawing_no} (p.{page + 1})'

    findings = list(PIDVFinding.objects.filter(drawing=drawing)) if drawing else []
    zones_for = _zones_reader(doc)
    from .display_ids import assign_display_ids
    _labels = assign_display_ids(findings)
    findings_by_id = {f.id: _finding_view(f, sheet_label, zones_for, page, label=_labels.get(f.id)) for f in findings}

    import datetime
    today = datetime.date.today().strftime('%d-%b-%Y')
    header = {
        'project_name': doc.project.project_name if doc.project_id else '',
        'title': tb.get('title') or (drawing.title if drawing else ''),
        'project_no': (doc.project.metadata or {}).get('project_number', '') if doc.project_id else '',
        'doc_rev': f'{drawing_no} / Rev {rev}'.rstrip(' /'),
        'tool': f'RADAI P&ID Checker {RULES_VERSION} (run {today})',
        'date': today,
        'reviewer': doc.reviewed_by or '',
        'approver': doc.approved_by or '',
    }

    with template.source_file.open('rb') as fh:
        wb = openpyxl.load_workbook(io.BytesIO(fh.read()))
    items = [dict(it, xlsx_row=_xlsx_row(template, it['no'])) for it in ev['items']]
    _fill_workbook(wb, template, header, items, findings_by_id, sheet_label)

    out = io.BytesIO()
    wb.save(out)
    safe_no = str(drawing_no).replace('/', '-')
    return out.getvalue(), f'AI_PID_Review_Checklist_02_{safe_no}_Rev{rev or "—"}.xlsx'


def build_project_checklist_report(project, template=None):
    """scope=all: worst result per item over the project's current sheets;
    the register lists every sheet's findings plus the cross-drawing
    findings (X…). Returns (bytes, filename)."""
    from apps.pid_verification_v2.models import (
        PIDVChecklistTemplate, PIDVCrossFinding, PIDVFinding, PIDVSheet,
    )

    template = template or _default_for_project(project)
    if template is None:
        return None, None
    items_meta = {it.no: it for it in template.items.all()}

    sheets = list(PIDVSheet.objects.filter(project=project, is_drawing=True, superseded_by__isnull=True))
    # Evaluate every current sheet of every document.
    per_item = {}
    sheet_labels = {}
    all_findings = {}
    zones_by_doc = {}
    from .display_ids import assign_display_ids
    for s in sheets:
        ev = evaluate_sheet(s.document, s.page_index, template)
        if ev is None:
            continue
        label = f'{s.drawing_no or s.document.file_name} (p.{s.page_index + 1})'
        sheet_labels[s.pk] = label
        if s.document_id not in zones_by_doc:
            zones_by_doc[s.document_id] = _zones_reader(s.document)
        sheet_findings = list(PIDVFinding.objects.filter(drawing__document_id=s.document_id,
                                                         drawing__page_index=s.page_index))
        _labels = assign_display_ids(sheet_findings)
        for f in sheet_findings:
            all_findings[f.id] = _finding_view(f, label, zones_by_doc[s.document_id], s.page_index,
                                               label=_labels.get(f.id))
        for it in ev['items']:
            cur = per_item.setdefault(it['no'], {
                **{k: it[k] for k in ('no', 'severity', 'mode', 'missing_inputs')},
                'applicability': 'N', 'ai_result': None, 'engineer_verification': None,
                'remarks': '', 'evidence': [], 'finding_ids': [], 'sheets': 0,
            })
            cur['sheets'] += 1
            if it['applicability'] == 'Y':
                cur['applicability'] = 'Y'
            # worst of Non-Conformance > Query > Not Verifiable > OK
            a, b = cur['ai_result'], it['ai_result']
            order = {'OK': 3, 'Not Verifiable': 2, 'Query': 1, 'Non-Conformance': 0, None: 4}
            if b and order.get(b, 4) < order.get(a, 4):
                cur['ai_result'] = b
            if it.get('evidence'):
                cur['evidence'].append(it['evidence'])
            cur['finding_ids'].extend(it.get('finding_ids') or [])
            if not cur['missing_inputs'] and it.get('missing_inputs'):
                cur['missing_inputs'] = it['missing_inputs']

    items = []
    for no, cur in per_item.items():
        ev_text = f"on {cur['sheets']} P&IDs: " + '; '.join(dict.fromkeys(cur['evidence'])) if cur['evidence'] else ''
        _fids = list(dict.fromkeys(cur['finding_ids']))
        items.append({
            **cur,
            'evidence': ev_text,
            'finding_ids': _fids,
            # Checklist-serial labels for display — match the Findings list,
            # never the database PKs.
            'finding_nos': [all_findings[fid]['n'] for fid in _fids if fid in all_findings],
            'finding_labels': [all_findings[fid]['label'] for fid in _fids if fid in all_findings],
            'xlsx_row': items_meta[no].xlsx_row,
        })

    # Cross-drawing findings as register rows.
    extra = []
    for cf in PIDVCrossFinding.objects.filter(project=project).order_by('number'):
        nos = RULE_TO_ITEMS_X.get(cf.code, [])
        labels = [sheet_labels.get(sid, str(sid)) for sid in cf.sheets or []]
        extra.append([
            nos[0] if nos else '-', f'X{cf.number}', ' ↔ '.join(labels), '-',
            ', '.join(labels), f'{cf.title}. {cf.detail}', 'cross-drawing check',
            'Non-Conformance' if cf.kind == 'error' else 'Query',
            'Major' if cf.kind == 'error' else 'Minor',
            'See the involved sheets', '-', 'Automated', None, None,
        ])

    import datetime
    today = datetime.date.today().strftime('%d-%b-%Y')
    header = {
        'project_name': project.project_name,
        'title': 'All P&IDs — combined review',
        'project_no': (project.metadata or {}).get('project_number', ''),
        'doc_rev': f'{len(sheets)} P&IDs – see AI Comment Register',
        'tool': f'RADAI P&ID Checker {RULES_VERSION} (run {today})',
        'date': today,
        'reviewer': '', 'approver': '',
    }
    with template.source_file.open('rb') as fh:
        wb = openpyxl.load_workbook(io.BytesIO(fh.read()))
    _fill_workbook(wb, template, header, items, all_findings, 'All P&IDs', extra_register_rows=extra)

    out = io.BytesIO()
    wb.save(out)
    pno = (project.metadata or {}).get('project_number', '') or str(project.project_id)[:8]
    return out.getvalue(), f'AI_PID_Review_Checklist_02_{pno}_AllPIDs.xlsx'


from .checklist_map import RULE_TO_ITEMS as RULE_TO_ITEMS_X  # noqa: E402


def _xlsx_row(template, no):
    it = template.items.filter(no=no).first()
    return it.xlsx_row if it else -1


def evaluate_template(doc):
    return _default_for_project(doc.project)


def _default_for_project(project):
    from .checklist_ai02 import project_template
    return project_template(project)
