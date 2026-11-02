"""
Import an AI-PID-02-format checklist workbook as a ChecklistTemplate.

    python manage.py import_checklist_template <file.xlsx> --code AI-PID-02 --default

Walks rows 8–148 of 'AI P&ID Checklist' (column C empty → section or
sub-heading, otherwise an item), stores xlsx_row per item, saves the
original file to source_file, and asserts 125 items / 14 sections (the
AI-PID-02 shape). Importing the same code again creates the next version
and leaves older versions intact.
"""
import os

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

EXPECTED_SHEET = 'AI P&ID Checklist'
ROW_FIRST, ROW_LAST = 8, 148


class Command(BaseCommand):
    help = 'Import an AI-PID-02 checklist workbook as a ChecklistTemplate'

    def add_arguments(self, parser):
        parser.add_argument('file', help='Path to the .xlsx template')
        parser.add_argument('--code', default='AI-PID-02')
        parser.add_argument('--default', action='store_true', help='Mark as the default checklist for new projects')

    def handle(self, *args, **opts):
        import openpyxl
        from apps.pid_verification_v2.models import (
            PIDVChecklistItem, PIDVChecklistTemplate,
        )

        path = opts['file']
        if not os.path.exists(path):
            raise CommandError(f'File not found: {path}')

        wb = openpyxl.load_workbook(path)
        if EXPECTED_SHEET not in wb.sheetnames:
            raise CommandError(f"Sheet '{EXPECTED_SHEET}' not found (has: {wb.sheetnames})")
        ws = wb[EXPECTED_SHEET]

        items = []
        sections = 0
        section_no = ''
        section_title = ''
        sub_heading = ''
        order = 0
        for r in range(ROW_FIRST, ROW_LAST + 1):
            a = ws.cell(r, 1).value
            b = ws.cell(r, 2).value
            c = ws.cell(r, 3).value
            if c is None or not str(c).strip():
                # Section header (A = '0'…'13') or sub-heading (B = Piping/Valves)
                if a is not None and str(a).strip():
                    sections += 1
                    section_no = str(a).strip()
                    section_title = str(b or '').strip()
                    sub_heading = ''
                elif b and str(b).strip():
                    sub_heading = str(b).strip()
                continue
            order += 1
            items.append({
                'no': str(a).strip(),
                'section_no': section_no,
                'section_title': section_title,
                'sub_heading': sub_heading,
                'check_point': str(b or '').strip(),
                'mode': str(c).strip(),
                'ai_task': str(ws.cell(r, 4).value or '').strip(),
                'ref_inputs': str(ws.cell(r, 5).value or '').strip(),
                'evidence_required': str(ws.cell(r, 6).value or '').strip(),
                'severity': str(ws.cell(r, 7).value or '').strip(),
                'change_vs_original': str(ws.cell(r, 12).value or '').strip(),
                'xlsx_row': r,
                'order': order,
            })

        if len(items) != 125:
            raise CommandError(f'Expected 125 items, found {len(items)}')
        if sections != 14:
            raise CommandError(f'Expected 14 sections, found {sections}')

        code = opts['code']
        version = (PIDVChecklistTemplate.objects.filter(code=code).count()) + 1
        from django.db import transaction
        with transaction.atomic():
            template = PIDVChecklistTemplate(
                code=code,
                title=str(ws['A1'].value or '').strip(),
                version=version,
                item_count=len(items),
            )
            with open(path, 'rb') as fh:
                template.source_file.save(os.path.basename(path), File(fh), save=False)
            if opts['default']:
                PIDVChecklistTemplate.objects.filter(is_default=True).update(is_default=False)
                template.is_default = True
            template.save()
            PIDVChecklistItem.objects.bulk_create([
                PIDVChecklistItem(template=template, **it) for it in items
            ])
        self.stdout.write(self.style.SUCCESS(
            f'Imported {code} v{version}: {len(items)} items, {sections} sections'
            + (' (default)' if template.is_default else ''),
        ))
