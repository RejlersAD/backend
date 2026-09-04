"""
Promote one project's manually-uploaded legend symbol pictures
(LegendSymbolImage rows, per-project DB+storage) into the repo-committed
static default-picture library (services/default_symbol_images.py) — so
they ship with the code and are available to every project, on any server,
with zero database rows required.

Usage:
    python manage.py export_default_symbols
    python manage.py export_default_symbols --project "hfkjds"
    python manage.py export_default_symbols --project "hfkjds" --overwrite

After running:
    - Every exported picture lands at
      static/default_symbols/<section>/<slug_for_symbol_name(name)>.<ext>
      (same slug function DefaultSymbolImagesView already uses to look
      these up, so no other code needs to change for them to be found).
    - Commit the static/default_symbols/ folder to git — from then on,
      every fresh deployment has these pictures with no upload/seeding
      step needed.
    - Remember to run `manage.py collectstatic` (and restart the server —
      WhiteNoise indexes static files once at startup) after adding new
      pictures this way, same as any other static file change.
"""
from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.pid_checker_v2.models import LegendSymbolImage
from apps.pid_checker_v2.services.default_symbol_images import (
    DEFAULT_SYMBOLS_STATIC_SUBDIR,
    slug_for_symbol_name,
)
from apps.pid_verification.models import PIDVProject


class Command(BaseCommand):
    help = (
        "Copy a project's uploaded LegendSymbolImage pictures into the "
        "static default-picture library, making them available to every "
        "project without needing to be re-uploaded."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--project', type=str, default='hfkjds',
            help="Project name to export symbols FROM (default: hfkjds).",
        )
        parser.add_argument(
            '--overwrite', action='store_true',
            help='Overwrite a static file that already exists at the target path (default: skip it).',
        )

    def handle(self, *args, **options):
        project_name = options['project']
        overwrite = options['overwrite']

        try:
            project = PIDVProject.objects.get(project_name=project_name)
        except PIDVProject.DoesNotExist:
            raise CommandError(f'No project named {project_name!r} found.')
        except PIDVProject.MultipleObjectsReturned:
            raise CommandError(
                f'Multiple projects are named {project_name!r} — re-run using '
                f'a Django shell and export_symbols_for_project(project) directly.'
            )

        rows = (
            LegendSymbolImage.objects
            .filter(project=project)
            .exclude(image_file='')
            .order_by('section', 'symbol_name')
        )
        total = rows.count()
        if total == 0:
            self.stdout.write(self.style.WARNING(
                f'Project {project_name!r} has no uploaded symbol pictures — nothing to export.'
            ))
            return

        static_root = Path(settings.BASE_DIR) / 'static' / DEFAULT_SYMBOLS_STATIC_SUBDIR

        copied = 0
        skipped_existing = 0
        skipped_bad_ext = 0
        by_section: dict[str, int] = {}

        for row in rows:
            slug = slug_for_symbol_name(row.symbol_name)
            if not slug:
                self.stdout.write(self.style.WARNING(
                    f'  Skipping {row.symbol_name!r} ({row.section}) — empty slug.'
                ))
                continue

            src_name = row.image_file.name
            ext = src_name.rsplit('.', 1)[-1].lower() if '.' in src_name else ''
            if ext not in ('png', 'jpg', 'jpeg', 'svg'):
                skipped_bad_ext += 1
                self.stdout.write(self.style.WARNING(
                    f'  Skipping {row.symbol_name!r} ({row.section}) — unrecognised extension {ext!r}.'
                ))
                continue

            dest_dir = static_root / row.section
            dest_path = dest_dir / f'{slug}.{ext}'

            if dest_path.exists() and not overwrite:
                skipped_existing += 1
                continue

            dest_dir.mkdir(parents=True, exist_ok=True)
            # Read through Django's storage abstraction (works whether
            # image_file lives on local disk or S3 — see storage_backends.py)
            # rather than assuming a local filesystem path.
            with row.image_file.open('rb') as src_f:
                dest_path.write_bytes(src_f.read())

            copied += 1
            by_section[row.section] = by_section.get(row.section, 0) + 1
            self.stdout.write(f'  {row.section}/{slug}.{ext}  <-  {row.symbol_name!r}')

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Exported {copied} picture(s) to {static_root} '
            f'({skipped_existing} already existed, {skipped_bad_ext} skipped — bad extension).'
        ))
        if by_section:
            self.stdout.write('By section: ' + ', '.join(f'{s}={n}' for s, n in sorted(by_section.items())))
        if copied:
            self.stdout.write(self.style.WARNING(
                'Next steps: run `manage.py collectstatic`, restart the server '
                '(WhiteNoise indexes static files at startup), then commit '
                'static/default_symbols/ to git.'
            ))
