"""
Import a zip file produced by `export_production_data` into the current
database — designed to run on production (or any target environment)
after copying the zip there.

Usage:
    python manage.py import_production_data --input export.zip
    python manage.py import_production_data --input export.zip --dry-run
    python manage.py import_production_data --input export.zip --strict

Behaviour:
    - The whole import runs inside ONE database transaction. Any
      unexpected error (corrupt zip, bad JSON, unforeseen DB error) rolls
      back EVERYTHING this run touched — nothing is left half-imported.
    - The zip's checksum is verified BEFORE any database write happens —
      a tampered/corrupted export.zip is rejected outright.
    - Safe to run more than once: every record is matched by its stable
      natural key (project_id / document_id / legend_id / image_id, all
      UUIDs assigned at creation time, not by database row number) — an
      already-imported record is skipped, never duplicated.
    - Users are re-matched by EMAIL against the target database (primary
      keys are never portable across two separate databases). A
      project/document whose owner can't be found is imported anyway with
      that field left blank (both FKs are nullable — matches the
      "unassigned" state those models already support). A legend sheet or
      symbol image whose owner/project can't be found CANNOT be imported
      at all (those FKs are required, non-nullable, by design) — it's
      skipped with a clear warning rather than the whole run failing,
      unless --strict is passed, in which case any unresolved reference
      aborts (and rolls back) the entire import.
    - Image files are written through Django's normal storage abstraction
      (the same ImageField.save() every upload endpoint in this codebase
      already uses) — so they land on S3 automatically when
      DEFAULT_FILE_STORAGE is the S3 backend (production), and on local
      disk otherwise (dev), with zero branching needed here.
    - V1/V2 document rows are metadata-only (see export_production_data's
      docstring) — original_file_name is restored as a reference/label,
      but no PDF bytes are re-uploaded by this command.
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


def _parse_dt(value: str | None):
    if not value:
        return None
    return datetime.fromisoformat(value)


class _Counters:
    def __init__(self):
        self.created = 0
        self.skipped_existing = 0
        self.skipped_unresolved = 0
        self.skipped_no_image = 0

    def line(self, label: str) -> str:
        base = f'  {label}: {self.created} created, {self.skipped_existing} already existed, {self.skipped_unresolved} skipped (unresolved reference)'
        if self.skipped_no_image:
            base += f', {self.skipped_no_image} skipped (image upload failed)'
        return base


class Command(BaseCommand):
    help = 'Import a zip file produced by export_production_data into this database.'

    def add_arguments(self, parser):
        parser.add_argument('--input', type=str, required=True, help='Path to the export.zip to import.')
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Verify the zip and report what WOULD be imported, without writing anything.',
        )
        parser.add_argument(
            '--strict', action='store_true',
            help=(
                'Abort (and roll back) the entire import if ANY legend sheet or '
                'symbol image has an owner/project that cannot be resolved on '
                'this database, instead of skipping just that record.'
            ),
        )

    def handle(self, *args, **options):
        input_path = Path(options['input'])
        dry_run = options['dry_run']
        strict = options['strict']
        if not input_path.exists():
            raise CommandError(f'{input_path} does not exist.')

        with zipfile.ZipFile(input_path, 'r') as zf:
            manifest = json.loads(zf.read('manifest.json'))
            records_bytes = zf.read('records.json')

            actual_checksum = hashlib.sha256(records_bytes).hexdigest()
            expected_checksum = manifest.get('checksum_sha256')
            if actual_checksum != expected_checksum:
                raise CommandError(
                    f'Checksum mismatch — export.zip may be corrupted or tampered with.\n'
                    f'  expected: {expected_checksum}\n'
                    f'  actual:   {actual_checksum}\n'
                    f'Aborting. Nothing was imported.'
                )
            self.stdout.write(self.style.SUCCESS('Checksum verified.'))
            self.stdout.write(f'Export was made: {manifest.get("exported_at")} from {manifest.get("source_environment")!r}')

            records = json.loads(records_bytes)

            if dry_run:
                self.stdout.write(self.style.WARNING('\n--dry-run: no changes will be written.'))
                for key, count in manifest.get('counts', {}).items():
                    self.stdout.write(f'  Would process {count} {key}')
                return

            with transaction.atomic():
                self._import_all(zf, records, strict)

    # ── Import ────────────────────────────────────────────────────────

    def _import_all(self, zf: zipfile.ZipFile, records: dict, strict: bool) -> None:
        User = get_user_model()

        def resolve_user(email: str | None):
            if not email:
                return None
            return User.objects.filter(email=email).first()

        from apps.pid_verification.models import PIDVProject as V1Project, PIDVDocument as V1Document
        from apps.pid_verification_v2.models import PIDVProject as V2Project, PIDVDocument as V2Document
        from apps.pid_checker_v2.models import PidCheckerV2LegendSheet, LegendSymbolImage

        # ── V1 projects ──────────────────────────────────────────────
        v1_proj_counters = _Counters()
        for row in records.get('v1_projects', []):
            if V1Project.objects.filter(project_id=row['project_id']).exists():
                v1_proj_counters.skipped_existing += 1
                continue
            V1Project.objects.create(
                project_id=row['project_id'],
                project_name=row['project_name'],
                description=row.get('description') or '',
                legend_knowledge_data=row.get('legend_knowledge_data'),
                legend_built_at=_parse_dt(row.get('legend_built_at')),
                created_by=resolve_user(row.get('created_by_email')),
            )
            v1_proj_counters.created += 1
        self.stdout.write(v1_proj_counters.line('V1 projects'))

        # ── V1 documents ─────────────────────────────────────────────
        v1_doc_counters = _Counters()
        for row in records.get('v1_documents', []):
            if V1Document.objects.filter(document_id=row['document_id']).exists():
                v1_doc_counters.skipped_existing += 1
                continue
            project = None
            if row.get('project_id'):
                project = V1Project.objects.filter(project_id=row['project_id']).first()
            V1Document.objects.create(
                document_id=row['document_id'],
                project=project,
                file_name=row['file_name'],
                s3_path=row.get('s3_path') or '',
                file_hash=row.get('file_hash') or '',
                status=row.get('status') or 'uploaded',
                error_message=row.get('error_message') or '',
                uploaded_by=resolve_user(row.get('uploaded_by_email')),
                excel_s3_url=row.get('excel_s3_url') or '',
                pdf_s3_url=row.get('pdf_s3_url') or '',
            )
            v1_doc_counters.created += 1
        self.stdout.write(v1_doc_counters.line('V1 documents'))

        # ── V2 projects ──────────────────────────────────────────────
        v2_proj_counters = _Counters()
        for row in records.get('v2_projects', []):
            if V2Project.objects.filter(project_id=row['project_id']).exists():
                v2_proj_counters.skipped_existing += 1
                continue
            V2Project.objects.create(
                project_id=row['project_id'],
                project_name=row['project_name'],
                description=row.get('description') or '',
                legend_knowledge_data=row.get('legend_knowledge_data'),
                legend_built_at=_parse_dt(row.get('legend_built_at')),
                metadata=row.get('metadata') or {},
                created_by=resolve_user(row.get('created_by_email')),
            )
            v2_proj_counters.created += 1
        self.stdout.write(v2_proj_counters.line('V2 projects'))

        # ── V2 documents ─────────────────────────────────────────────
        v2_doc_counters = _Counters()
        for row in records.get('v2_documents', []):
            if V2Document.objects.filter(document_id=row['document_id']).exists():
                v2_doc_counters.skipped_existing += 1
                continue
            project = None
            if row.get('project_id'):
                project = V2Project.objects.filter(project_id=row['project_id']).first()
            V2Document.objects.create(
                document_id=row['document_id'],
                project=project,
                file_name=row['file_name'],
                s3_path=row.get('s3_path') or '',
                file_hash=row.get('file_hash') or '',
                status=row.get('status') or 'uploaded',
                error_message=row.get('error_message') or '',
                uploaded_by=resolve_user(row.get('uploaded_by_email')),
                excel_s3_url=row.get('excel_s3_url') or '',
                pdf_s3_url=row.get('pdf_s3_url') or '',
            )
            v2_doc_counters.created += 1
        self.stdout.write(v2_doc_counters.line('V2 documents'))

        # ── Legend sheets — created_by is REQUIRED (non-nullable FK) ───
        legend_counters = _Counters()
        for row in records.get('legend_sheets', []):
            if PidCheckerV2LegendSheet.objects.filter(legend_id=row['legend_id']).exists():
                legend_counters.skipped_existing += 1
                continue
            owner = resolve_user(row.get('created_by_email'))
            if owner is None:
                msg = (f"Legend sheet {row['name']!r} ({row['section']}) — owner "
                       f"{row.get('created_by_email')!r} not found on this database.")
                if strict:
                    raise CommandError(f'--strict: {msg} Aborting, rolling back.')
                self.stdout.write(self.style.WARNING(f'  SKIP: {msg}'))
                legend_counters.skipped_unresolved += 1
                continue
            PidCheckerV2LegendSheet.objects.create(
                legend_id=row['legend_id'],
                created_by=owner,
                section=row['section'],
                name=row['name'],
                description=row.get('description') or '',
                definition=row.get('definition') or {},
                is_active=row.get('is_active', False),
            )
            legend_counters.created += 1
        self.stdout.write(legend_counters.line('Legend sheets'))

        # ── Symbol images — project is REQUIRED (non-nullable FK) ──────
        # PIDVProject here is the pid_verification (V1) one — that's what
        # LegendSymbolImage.project actually points to (see models.py).
        #
        # BUG FIX: this used to unconditionally call obj.save() after the
        # image-upload attempt, whether or not that attempt actually ran —
        # `if image_filename and image_filename in zf.namelist():` silently
        # skipped the upload (missing key, path mismatch, whatever) and
        # fell straight through to obj.save() anyway, creating a DB row
        # with an EMPTY image_file. Confirmed real symptom on production:
        # rows exist in the DB with a path recorded but no matching object
        # on S3 — the actual gap was this loop being able to leave
        # image_file unset/blank while still persisting the row as if it
        # had succeeded, with no warning anywhere. Now: no image bytes
        # found -> skip the whole record (don't create a broken row);
        # image bytes found but the upload can't be CONFIRMED to have
        # landed in storage -> also skip, loudly, instead of trusting
        # FieldFile.save() blindly. Only a verified-present file gets its
        # DB row saved at all.
        symbol_counters = _Counters()
        symbol_storage = LegendSymbolImage._meta.get_field('image_file').storage
        storage_looks_like_s3 = hasattr(symbol_storage, 'bucket_name')
        if not storage_looks_like_s3:
            msg = (
                f'Symbol images will be uploaded via {type(symbol_storage).__name__}, '
                f'NOT an S3-backed storage. If this command is running somewhere other '
                f'than the real production app (a one-off shell, a different environment '
                f'with USE_S3 unset), the files it "uploads" now will NOT be visible to '
                f'the actual production web/worker processes reading from S3 afterward — '
                f'exactly the "DB has a path but the file is missing on S3" symptom this '
                f'fix addresses. Re-run this command from an environment where USE_S3=True '
                f'and the S3 credentials actually resolve.'
            )
            if strict:
                raise CommandError(f'--strict: {msg} Aborting, rolling back.')
            self.stdout.write(self.style.WARNING(f'WARNING: {msg}'))

        total = len(records.get('symbol_images', []))
        for i, row in enumerate(records.get('symbol_images', []), start=1):
            if LegendSymbolImage.objects.filter(image_id=row['image_id']).exists():
                symbol_counters.skipped_existing += 1
                continue
            project_name = row.get('project_name')
            project = V1Project.objects.filter(project_name=project_name).first() if project_name else None
            if project is None:
                msg = (f"Symbol {row['symbol_name']!r} ({row['section']}) — project "
                       f"{project_name!r} not found on this database.")
                if strict:
                    raise CommandError(f'--strict: {msg} Aborting, rolling back.')
                self.stdout.write(self.style.WARNING(f'  SKIP: {msg}'))
                symbol_counters.skipped_unresolved += 1
                continue

            image_filename = row.get('image_filename')
            if not image_filename or image_filename not in zf.namelist():
                msg = (f"Symbol {row['symbol_name']!r} ({row['section']}) — image file "
                       f"{image_filename!r} not found in export.zip.")
                if strict:
                    raise CommandError(f'--strict: {msg} Aborting, rolling back.')
                self.stdout.write(self.style.WARNING(f'  SKIP: {msg}'))
                symbol_counters.skipped_no_image += 1
                continue

            obj = LegendSymbolImage(
                image_id=row['image_id'],
                project=project,
                legend_sheet=None,  # see SymbolImageUploadView — always unset for manual pictures
                section=row['section'],
                symbol_name=row['symbol_name'],
                content_type=row.get('content_type') or 'image/png',
            )
            data = zf.read(image_filename)
            dest_name = image_filename.rsplit('/', 1)[-1]
            obj.image_file.save(dest_name, ContentFile(data), save=False)

            # Verified upload, not an assumed one — ask the SAME storage
            # backend Django just wrote through whether the file is
            # actually there before trusting it enough to save the DB row.
            if not obj.image_file.storage.exists(obj.image_file.name):
                msg = (f"Symbol {row['symbol_name']!r} ({row['section']}) — upload to "
                       f"{type(obj.image_file.storage).__name__} did not verify as present "
                       f"afterward; not saving a DB record that would point at a missing file.")
                if strict:
                    raise CommandError(f'--strict: {msg} Aborting, rolling back.')
                self.stdout.write(self.style.WARNING(f'  SKIP: {msg}'))
                symbol_counters.skipped_no_image += 1
                continue

            obj.save()
            symbol_counters.created += 1
            if i % 25 == 0 or i == total:
                self.stdout.write(f'  ...{i}/{total} symbol images processed')
        self.stdout.write(symbol_counters.line('Symbol images'))

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Import complete (transaction committed).'))
