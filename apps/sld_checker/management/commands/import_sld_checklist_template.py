"""
Import an AI-SLD-01-format checklist workbook as an SldChecklistTemplate.

    python manage.py import_sld_checklist_template <file.xlsx> --code AI-SLD-01 --default

Walks rows 8–72 of 'AI SLD Checklist' (column C empty → section or
sub-heading, otherwise an item), stores xlsx_row per item, saves the
original file to source_file, and asserts the AI-SLD-01 shape (54 items).
Importing the same code again creates the next version and leaves older
versions intact. Mirrors import_checklist_template (AI-PID-02).
"""
import os

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

EXPECTED_SHEET = 'AI SLD Checklist'
ROW_FIRST, ROW_LAST = 8, 72
EXPECTED_ITEMS = 54


class Command(BaseCommand):
    help = 'Import an AI-SLD-01 checklist workbook as an SldChecklistTemplate'

    def add_arguments(self, parser):
        parser.add_argument('file', help='Path to the .xlsx template')
        parser.add_argument('--code', default='AI-SLD-01')
        parser.add_argument('--default', action='store_true',
                            help='Mark as the default checklist')

    def handle(self, *args, **opts):
        import openpyxl
        from apps.sld_checker.models import SldChecklistItem, SldChecklistTemplate

        path = opts['file']
        if not os.path.exists(path):
            raise CommandError(f'File not found: {path}')

        wb = openpyxl.load_workbook(path)
        if EXPECTED_SHEET not in wb.sheetnames:
            raise CommandError(f"Sheet '{EXPECTED_SHEET}' not found (has: {wb.sheetnames})")
        ws = wb[EXPECTED_SHEET]

        items = []
        section_no = ''
        section_title = ''
        sub_heading = ''
        order = 0
        for r in range(ROW_FIRST, ROW_LAST + 1):
            a = ws.cell(r, 1).value
            b = ws.cell(r, 2).value
            c = ws.cell(r, 3).value
            if c is None or not str(c).strip():
                # Section header (A = '0'…'8') or sub-heading row
                if a is not None and str(a).strip():
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
                'xlsx_row': r,
                'order': order,
            })

        if len(items) != EXPECTED_ITEMS:
            raise CommandError(f'Expected {EXPECTED_ITEMS} items, found {len(items)}')

        code = opts['code']
        version = SldChecklistTemplate.objects.filter(code=code).count() + 1
        from django.db import transaction
        with transaction.atomic():
            template = SldChecklistTemplate(
                code=code,
                title=str(ws['A1'].value or '').strip(),
                version=version,
                item_count=len(items),
            )
            with open(path, 'rb') as fh:
                template.source_file.save(os.path.basename(path), File(fh), save=False)
            if opts['default']:
                SldChecklistTemplate.objects.filter(is_default=True).update(is_default=False)
                template.is_default = True
            template.save()
            SldChecklistItem.objects.bulk_create([
                SldChecklistItem(template=template, **it) for it in items
            ])
        self.stdout.write(self.style.SUCCESS(
            f'Imported {code} v{version}: {len(items)} items'
            + (' (default)' if template.is_default else ''),
        ))
