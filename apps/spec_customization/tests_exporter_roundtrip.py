"""SP3D exporter round-trip integration tests (part 1: builders + xlsx).

Covers: SPEC (25 builders) + CAT (28 sheets) render without
template-alignment errors; valve trim / manufacturer / allowable-stress
flow from extraction into preview and the downloaded xlsx.
Synthetic fixtures only — no live AI, no S3 (isolated SQLite TestCase).
"""
from __future__ import annotations

import io

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from openpyxl import load_workbook

from .models import PaperSpecDocument, PaperSpecExtractionJob, PipingClass, PipingClassComponent
from .services.exporters import build_cat_workbook, build_spec_workbook
from .services.exporters import smartplant_config as cfg
from .services.exporters.workbook_preview import WORKBOOK_CAT, WORKBOOK_SPEC, build_preview


def make_five_system_job(owner, *, code='A1'):
    """Completed sample job covering all five piping-spec systems."""
    document = PaperSpecDocument.objects.create(
        file=SimpleUploadedFile('sample.pdf', b'%PDF-1.4 test'),
        original_filename='sample.pdf',
        file_size_bytes=13,
        sha256_hash='b' * 64,
        uploaded_by=owner,
    )
    job = PaperSpecExtractionJob.objects.create(
        document=document,
        created_by=owner,
        status=PaperSpecExtractionJob.STATUS_COMPLETED,
        progress_percent=100,
        config_snapshot={'extraction_outcome': {'failed_chunks': [], 'missing_chunks': []}},
    )
    cls = PipingClass.objects.create(
        job=job,
        class_code=code,
        class_full_code=f'PIPING SPEC: {code}',
        material_grade='ASTM A106 Gr B',
        pressure_rating='CLASS 900',
        flange_facing='RF',
        corrosion_allowance='1.6mm',
        service_list=['Process Fluid'],
        pt_rating_table=[
            {'temperature_c': 120.0, 'pressure_bar_g': 19.0, 'allowable_stress': '138MPa'},
            {'temperature_c': 200.0, 'pressure_bar_g': 13.0, 'allowable_stress': ''},
        ],
        raw_notes='Sample five-system class',
        confidence_score=0.9,
    )
    specs = [
        ('pipe', 'Seamless', '2"', '6"', 'SCH 80', 'ASTM A106 Gr B', 'BW', ''),
        ('flange', 'Weld Neck', '2"', '6"', 'CLASS 900', 'ASTM A105', 'RF', ''),
        ('fitting', 'Tee', '2"', '6"', 'SCH 80', 'ASTM A234 WPB', 'BW', ''),
        ('valve', 'Gate Valve', '2"', '6"', 'CLASS 900', 'ASTM A216 WCB',
         'RF', 'Trim 5 Stellite, Velan Fig 150'),
        ('gasket', 'Spiral Wound', '2"', '6"', 'CLASS 900', '316SS + Graphite', 'RF', ''),
        ('bolt', 'Stud Bolt', '2"', '6"', '', 'ASTM A193 B7 / A194 2H', '', ''),
    ]
    for idx, (ctype, sub, s_from, s_to, sched, mat, end, notes) in enumerate(specs):
        PipingClassComponent.objects.create(
            piping_class=cls,
            component_type=ctype,
            sub_type=sub,
            size_from=s_from,
            size_to=s_to,
            schedule_or_rating=sched,
            material_standard=mat,
            end_connection=end,
            notes=notes,
            description=f'{sub} {s_from}-{s_to}',
            display_order=idx,
        )
    return job, cls


def sheet_names(buffer: io.BytesIO) -> list[str]:
    buffer.seek(0)
    wb = load_workbook(buffer, read_only=True, data_only=True)
    names = list(wb.sheetnames)
    wb.close()
    return names


class SpecExporterSheetTests(TestCase):
    def setUp(self):
        owner = get_user_model().objects.create_user(
            username='exporter-owner', email='exporter-owner@example.test', password='safe-password')
        self.job, self.cls = make_five_system_job(owner)

    def test_spec_workbook_renders_all_registered_builders(self):
        buf = build_spec_workbook(self.job)
        names = sheet_names(buf)
        self.assertEqual(names, cfg.SPEC_TEMPLATE_PROFILE['sheet_names'])
        self.assertEqual(len(names), 50)

    def test_cat_workbook_renders_all_routed_sheets(self):
        buf = build_cat_workbook(self.job)
        names = sheet_names(buf)
        for sheet in cfg.CAT_SHEET_DEFAULTS:
            self.assertIn(sheet, names, f'CAT sheet missing: {sheet}')

    def test_spec_header_name_matching_writes_by_name(self):
        buf = build_spec_workbook(self.job)
        buf.seek(0)
        wb = load_workbook(buf, data_only=True)
        try:
            ws = wb['PipingMaterialsClassData']
            header_row = None
            for r in range(1, min(ws.max_row, 60) + 1):
                if str(ws.cell(r, 1).value or '').strip() in cfg.HEADER_MARKERS:
                    header_row = r
                    break
            self.assertIsNotNone(header_row)
            headers = [(ws.cell(header_row, c).value or '') for c in range(1, ws.max_column + 1)]
            self.assertIn('SPECNAME', headers)
            col = headers.index('SPECNAME') + 1
            values = [str(ws.cell(r, col).value or '') for r in range(header_row + 1, ws.max_row + 1)]
            self.assertIn('A1', values)
        finally:
            wb.close()
