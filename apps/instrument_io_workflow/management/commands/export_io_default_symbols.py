"""
Promote uploaded I/O List legend symbol pictures (IOListLegendSymbolImage
rows, user-scoped DB+storage) into the repo-committed static
default-picture library (services/default_symbol_images.py) — so they ship
with the code and are available to every user/project, on any server, with
zero database rows required. I/O List counterpart to
apps.pid_checker_v2.management.commands.export_default_symbols — same
approach, adjusted because IOListLegendSymbolImage is scoped to a user
(created_by), not a project (P&ID's LegendSymbolImage.project) — I/O List
legends/symbols were never project-scoped in the first place, so there is
no per-project filter to offer, and no single canonical "default project"
name to default --user to either; omitting --user exports every user's
uploads.

Named export_io_default_symbols (not export_default_symbols) because
Django resolves management commands by name across ALL installed apps —
apps.pid_checker_v2 already owns that exact name, and a second app
defining the same name doesn't create two reachable commands, it silently
shadows one of them. `manage.py export_default_symbols` always resolves
to the pid_checker_v2 one regardless of app load order; this command is
only reachable under its own distinct name.

Usage:
    python manage.py export_io_default_symbols
    python manage.py export_io_default_symbols --user someone@example.com
    python manage.py export_io_default_symbols --user someone@example.com --overwrite

After running:
    - Every exported picture lands at
      static/io_list_default_symbols/<section>/<slug_for_symbol_name(name)>.<ext>
      (same slug function DefaultSymbolImagesView already uses to look
      these up, so no other code needs to change for them to be found).
    - Commit the static/io_list_default_symbols/ folder to git — from then
      on, every fresh deployment has these pictures with no upload/seeding
      step needed.
    - Remember to run `manage.py collectstatic` (and restart the server —
      WhiteNoise indexes static files once at startup) after adding new
      pictures this way, same as any other static file change.
"""
from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.instrument_io_workflow.models import IOListLegendSymbolImage
from apps.instrument_io_workflow.services.default_symbol_images import (
    STATIC_SUBDIR,
    slug_for_symbol_name,
)


class Command(BaseCommand):
    help = (
        "Copy uploaded IOListLegendSymbolImage pictures into the static "
        "default-picture library, making them available to every "
        "user/project without needing to be re-uploaded."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--user', type=str, default=None, metavar='EMAIL',
            help=(
                'Only export symbols uploaded by this user (matched by '
                'email). Omit to export every user\'s uploaded symbols — '
                'I/O List legends/symbols have no project scoping and no '
                'single canonical "default" user to fall back to, unlike '
                'the P&ID version of this command.'
            ),
        )
        parser.add_argument(
            '--overwrite', action='store_true',
            help='Overwrite a static file that already exists at the target path (default: skip it).',
        )

    def handle(self, *args, **options):
        user_email = options['user']
        overwrite = options['overwrite']

        rows = IOListLegendSymbolImage.objects.exclude(image_file='').order_by('section', 'symbol_name')
        if user_email:
            User = get_user_model()
            user = User.objects.filter(email=user_email).first()
            if user is None:
                raise CommandError(f'No user with email {user_email!r} found.')
            rows = rows.filter(created_by=user)

        total = rows.count()
        if total == 0:
            self.stdout.write(self.style.WARNING(
                'No uploaded I/O List symbol pictures found — nothing to export.'
                if not user_email else
                f'User {user_email!r} has no uploaded I/O List symbol pictures — nothing to export.'
            ))
            return

        static_root = Path(settings.BASE_DIR) / 'static' / STATIC_SUBDIR

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
            # image_file lives on local disk or S3) rather than assuming a
            # local filesystem path.
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
                'static/io_list_default_symbols/ to git.'
            ))
