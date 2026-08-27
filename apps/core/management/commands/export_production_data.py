"""
Export V1 (pid_verification), V2 (pid_verification_v2), and pid_checker_v2
legend/symbol data into one portable zip file, for migrating from a local
database to production (or between any two environments).

Usage:
    python manage.py export_production_data --output export.zip
    python manage.py export_production_data --output export.zip --project "hfkjds" --project "ADOC"

What's included:
    - pid_verification  (V1): PIDVProject, PIDVDocument   — records only
    - pid_verification_v2 (V2): PIDVProject, PIDVDocument — records only
    - pid_checker_v2: PidCheckerV2LegendSheet             — records only
    - pid_checker_v2: LegendSymbolImage                   — records + the
      actual image file bytes (these are the only binary files exported;
      V1/V2 document uploads are exported as metadata/references only, not
      re-packaged PDF bytes — see the module docstring on the import
      command for why, and how to extend this if you need them too).

Records reference their owning user by EMAIL (not by primary key — user
IDs are essentially never the same across two databases) so the import
command can re-resolve ownership against whichever users exist on the
target environment.

Zip layout:
    manifest.json   — counts, timestamps, sha256 checksum of records.json
    records.json    — every exported row, as portable JSON
    images/<image_id>.<ext>  — one file per LegendSymbolImage that has a
                                picture attached

Safe to run against a live database — this command only reads.
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def _user_email(user) -> str | None:
    return user.email if user and getattr(user, 'email', None) else None


class Command(BaseCommand):
    help = 'Export V1, V2, and pid_checker_v2 legend/symbol data to a portable zip file.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output', type=str, default='export.zip',
            help='Path to write the zip file to (default: export.zip).',
        )
        parser.add_argument(
            '--project', action='append', default=None, metavar='NAME',
            help=(
                'Limit the export to project(s) with this project_name '
                '(repeatable, e.g. --project "hfkjds" --project "ADOC"). '
                'Matched against V1 and V2 projects independently. Symbol '
                'images and legend sheets are NOT project-scoped by this '
                'flag — legend sheets belong to a user, and symbol images '
                'are shared cross-project by design (see '
                'services/default_symbol_images.py), so they are always '
                'exported in full. Omit this flag to export every V1/V2 '
                'project.'
            ),
        )

    def handle(self, *args, **options):
        output_path = Path(options['output'])
        project_filter = options['project']

        from apps.pid_verification.models import PIDVProject as V1Project, PIDVDocument as V1Document
        from apps.pid_verification_v2.models import PIDVProject as V2Project, PIDVDocument as V2Document
        from apps.pid_checker_v2.models import PidCheckerV2LegendSheet, LegendSymbolImage

        records: dict[str, list[dict]] = {
            'v1_projects': [], 'v1_documents': [],
            'v2_projects': [], 'v2_documents': [],
            'legend_sheets': [], 'symbol_images': [],
        }
        image_files: list[tuple[str, bytes]] = []  # (arcname, bytes)

        # ── V1 projects + documents ─────────────────────────────────────
        v1_projects_qs = V1Project.objects.all()
        if project_filter:
            v1_projects_qs = v1_projects_qs.filter(project_name__in=project_filter)
        v1_project_ids = set()
        for p in v1_projects_qs:
            v1_project_ids.add(p.project_id)
            records['v1_projects'].append({
                'project_id': str(p.project_id),
                'project_name': p.project_name,
                'description': p.description,
                'legend_knowledge_data': p.legend_knowledge_data,
                'legend_built_at': _iso(p.legend_built_at),
                'created_by_email': _user_email(p.created_by),
                'created_at': _iso(p.created_at),
                'updated_at': _iso(p.updated_at),
            })
        self.stdout.write(f'Exporting {len(records["v1_projects"])} V1 project(s)...')

        v1_docs_qs = V1Document.objects.all()
        if project_filter:
            v1_docs_qs = v1_docs_qs.filter(project__project_id__in=v1_project_ids)
        for d in v1_docs_qs:
            records['v1_documents'].append({
                'document_id': str(d.document_id),
                'project_id': str(d.project.project_id) if d.project_id else None,
                'file_name': d.file_name,
                's3_path': d.s3_path,
                'file_hash': d.file_hash,
                'original_file_name': d.original_file.name if d.original_file else None,
                'status': d.status,
                'error_message': d.error_message,
                'uploaded_by_email': _user_email(d.uploaded_by),
                'excel_s3_url': d.excel_s3_url,
                'pdf_s3_url': d.pdf_s3_url,
                'created_at': _iso(d.created_at),
                'updated_at': _iso(d.updated_at),
            })
        self.stdout.write(f'Exporting {len(records["v1_documents"])} V1 document(s)...')

        # ── V2 projects + documents ─────────────────────────────────────
        v2_projects_qs = V2Project.objects.all()
        if project_filter:
            v2_projects_qs = v2_projects_qs.filter(project_name__in=project_filter)
        v2_project_ids = set()
        for p in v2_projects_qs:
            v2_project_ids.add(p.project_id)
            records['v2_projects'].append({
                'project_id': str(p.project_id),
                'project_name': p.project_name,
                'description': p.description,
                'legend_knowledge_data': p.legend_knowledge_data,
                'legend_built_at': _iso(p.legend_built_at),
                'metadata': p.metadata,
                'created_by_email': _user_email(p.created_by),
                'created_at': _iso(p.created_at),
                'updated_at': _iso(p.updated_at),
            })
        self.stdout.write(f'Exporting {len(records["v2_projects"])} V2 project(s)...')

        v2_docs_qs = V2Document.objects.all()
        if project_filter:
            v2_docs_qs = v2_docs_qs.filter(project__project_id__in=v2_project_ids)
        for d in v2_docs_qs:
            records['v2_documents'].append({
                'document_id': str(d.document_id),
                'project_id': str(d.project.project_id) if d.project_id else None,
                'file_name': d.file_name,
                's3_path': d.s3_path,
                'file_hash': d.file_hash,
                'original_file_name': d.original_file.name if d.original_file else None,
                'status': d.status,
                'error_message': d.error_message,
                'uploaded_by_email': _user_email(d.uploaded_by),
                'excel_s3_url': d.excel_s3_url,
                'pdf_s3_url': d.pdf_s3_url,
                'created_at': _iso(d.created_at),
                'updated_at': _iso(d.updated_at),
            })
        self.stdout.write(f'Exporting {len(records["v2_documents"])} V2 document(s)...')

        # ── pid_checker_v2 legend sheets (NOT filtered by --project — they
        # belong to a user, not a project) ──────────────────────────────
        for ls in PidCheckerV2LegendSheet.objects.all():
            records['legend_sheets'].append({
                'legend_id': str(ls.legend_id),
                'created_by_email': _user_email(ls.created_by),
                'section': ls.section,
                'name': ls.name,
                'description': ls.description,
                'definition': ls.definition,
                'is_active': ls.is_active,
                'created_at': _iso(ls.created_at),
                'updated_at': _iso(ls.updated_at),
            })
        self.stdout.write(f'Exporting {len(records["legend_sheets"])} legend sheet(s)...')

        # ── pid_checker_v2 symbol images — records + actual files (NOT
        # filtered by --project — shared cross-project by design) ───────
        symbol_qs = LegendSymbolImage.objects.exclude(image_file='')
        total_images = symbol_qs.count()
        self.stdout.write(f'Exporting {total_images} symbol image(s)...')
        for i, img in enumerate(symbol_qs, start=1):
            ext = img.image_file.name.rsplit('.', 1)[-1] if '.' in img.image_file.name else 'png'
            arcname = f'images/{img.image_id}.{ext}'
            with img.image_file.open('rb') as f:
                image_files.append((arcname, f.read()))
            records['symbol_images'].append({
                'image_id': str(img.image_id),
                'project_name': img.project.project_name if img.project_id else None,
                'section': img.section,
                'symbol_name': img.symbol_name,
                'content_type': img.content_type,
                'image_filename': arcname,
                'created_at': _iso(img.created_at),
                'updated_at': _iso(img.updated_at),
            })
            if i % 25 == 0 or i == total_images:
                self.stdout.write(f'  ...{i}/{total_images} image files read')

        # ── Serialize, checksum, package ────────────────────────────────
        records_bytes = json.dumps(records, indent=2, sort_keys=True).encode('utf-8')
        checksum = hashlib.sha256(records_bytes).hexdigest()

        manifest = {
            'version': 1,
            'exported_at': datetime.now(timezone.utc).isoformat(),
            'source_environment': getattr(settings, 'ENVIRONMENT', 'unknown'),
            'project_filter': project_filter,
            'counts': {k: len(v) for k, v in records.items()},
            'checksum_sha256': checksum,
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('manifest.json', json.dumps(manifest, indent=2))
            zf.writestr('records.json', records_bytes)
            for arcname, data in image_files:
                zf.writestr(arcname, data)

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'Wrote {output_path} ({output_path.stat().st_size:,} bytes)'))
        self.stdout.write('Summary:')
        for key, count in manifest['counts'].items():
            self.stdout.write(f'  {key}: {count}')
        self.stdout.write(f'  image files packaged: {len(image_files)}')
        self.stdout.write(f'  checksum (sha256): {checksum}')
