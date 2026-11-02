"""
Import a revised AI-PID-02 workbook as a new template version (spec §2.1).

A file whose row-7 headers equal the current template's is recognised as
the AI-PID-02 format — no column mapping. Returns the diff against the
current version (items added, removed, reworded).
"""
from __future__ import annotations

import io

import openpyxl

EXPECTED_SHEET = 'AI P&ID Checklist'
EXPECTED_HEADERS = ['Sl. No', 'Check Point', 'AI Check Mode', 'What AI Shall Do / Check',
                    'Reference / Input Documents', 'AI Output / Evidence Required', 'Severity',
                    'Applicability\n(Y / N / NA)', 'AI Result', 'Engineer Verification',
                    'Remarks', 'Change vs Original']
ROW_FIRST, ROW_LAST = 8, 148


class NotChecklistFormat(Exception):
    pass


def _parse(ws):
    """Rows → (items {no: dict}, sections count)."""
    items = {}
    sections = 0
    section_no = section_title = sub_heading = ''
    order = 0
    for r in range(ROW_FIRST, ROW_LAST + 1):
        a, b, c = ws.cell(r, 1).value, ws.cell(r, 2).value, ws.cell(r, 3).value
        if c is None or not str(c).strip():
            if a is not None and str(a).strip():
                sections += 1
                section_no, section_title, sub_heading = str(a).strip(), str(b or '').strip(), ''
            elif b and str(b).strip():
                sub_heading = str(b).strip()
            continue
        order += 1
        items[str(a).strip()] = {
            'no': str(a).strip(), 'section_no': section_no, 'section_title': section_title,
            'sub_heading': sub_heading, 'check_point': str(b or '').strip(),
            'mode': str(c).strip(), 'ai_task': str(ws.cell(r, 4).value or '').strip(),
            'ref_inputs': str(ws.cell(r, 5).value or '').strip(),
            'evidence_required': str(ws.cell(r, 6).value or '').strip(),
            'severity': str(ws.cell(r, 7).value or '').strip(),
            'change_vs_original': str(ws.cell(r, 12).value or '').strip(),
            'xlsx_row': r, 'order': order,
        }
    return items, sections


def headers_match(file_bytes: bytes) -> bool:
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True)
    if EXPECTED_SHEET not in wb.sheetnames:
        return False
    ws = wb[EXPECTED_SHEET]
    row7 = [str(ws.cell(7, c).value or '') for c in range(1, 13)]
    return row7 == EXPECTED_HEADERS


def import_new_version(file_bytes: bytes, filename: str, code: str = 'AI-PID-02'):
    """Import as the next version of `code`. Returns (template, diff)."""
    from apps.pid_verification_v2.models import (
        PIDVChecklistItem, PIDVChecklistTemplate,
    )

    if not headers_match(file_bytes):
        raise NotChecklistFormat('Row 7 headers do not match the AI-PID-02 template')

    wb = openpyxl.load_workbook(io.BytesIO(file_bytes))
    ws = wb[EXPECTED_SHEET]
    new_items, sections = _parse(ws)

    current = PIDVChecklistTemplate.objects.filter(code=code).order_by('-version').first()
    version = (current.version + 1) if current else 1
    template = PIDVChecklistTemplate(
        code=code, title=str(ws['A1'].value or '').strip(), version=version,
        item_count=len(new_items),
    )
    from django.core.files.base import ContentFile
    template.source_file.save(filename, ContentFile(file_bytes), save=False)
    # keep exactly one default
    if not PIDVChecklistTemplate.objects.filter(is_default=True).exists():
        template.is_default = True
    template.save()
    PIDVChecklistItem.objects.bulk_create([
        PIDVChecklistItem(template=template, **it) for it in new_items.values()
    ])

    diff = {'added': [], 'removed': [], 'reworded': []}
    if current:
        old_items = {it.no: it for it in current.items.all()}
        for no, it in new_items.items():
            if no not in old_items:
                diff['added'].append(no)
            elif old_items[no].check_point != it['check_point']:
                diff['reworded'].append(no)
        diff['removed'] = [no for no in old_items if no not in new_items]
    return template, diff
