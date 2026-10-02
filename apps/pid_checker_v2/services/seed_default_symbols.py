"""
Mirror the repo-committed static/default_symbols/ picture library into the
database (LegendSymbolImage, is_default=True, project=None) — so the
shared default-picture library survives independently of the static files
on disk too (see that model's own docstring). services/
default_symbol_images.py still reads the static files directly as a
second-line fallback for anything not yet (re-)seeded.

Unlike I/O List's counterpart (services/seed_default_symbols.py in
apps.instrument_io_workflow), this app has a canonical, non-lossy source
of symbol names — legend_defaults.DEFAULT_TEMPLATES' lookup keys (the
same source default_symbol_images._known_symbol_names already reads) — so
this reuses that instead of reconstructing a name from the filename slug.

Idempotent: skip_if_exists check against (section, symbol_name,
is_default=True), not just the DB's own partial unique constraint — so
re-running never creates duplicates or errors, and only touches rows for
newly-added static files.

Called from two places, both wrapping this one function so the logic
never drifts apart:
  - management/commands/seed_pid_default_symbols.py — manual/CI run.
  - apps.py's post_migrate signal handler — automatic, right after
    `manage.py migrate` finishes.
"""
from __future__ import annotations

import logging

from django.contrib.staticfiles import finders

from .default_symbol_images import (
    DEFAULT_SYMBOL_IMAGE_EXTENSIONS, DEFAULT_SYMBOLS_STATIC_SUBDIR,
    _known_symbol_names, slug_for_symbol_name,
)

logger = logging.getLogger(__name__)

_CONTENT_TYPES = {'png': 'image/png', 'jpg': 'image/jpeg',
                  'jpeg': 'image/jpeg', 'svg': 'image/svg+xml'}


def seed_pid_default_symbols(stdout=None) -> dict:
    """Returns {'scanned': N, 'created': N, 'skipped': N}."""
    from ..legend_defaults import SECTIONS
    from ..models import LegendSymbolImage
    from django.core.files.base import ContentFile

    def _log(msg):
        if stdout is not None:
            stdout.write(msg)
        logger.info(msg)

    existing = set(
        LegendSymbolImage.objects.filter(is_default=True)
        .values_list('section', 'symbol_name')
    )

    stats = {'scanned': 0, 'created': 0, 'skipped': 0}
    for section in SECTIONS:
        for name in _known_symbol_names(section):
            slug = slug_for_symbol_name(name)
            if not slug:
                continue
            found_path = None
            found_ext = None
            for ext in DEFAULT_SYMBOL_IMAGE_EXTENSIONS:
                relative_path = f'{DEFAULT_SYMBOLS_STATIC_SUBDIR}/{section}/{slug}.{ext}'
                path = finders.find(relative_path)
                if path:
                    found_path, found_ext = path, ext
                    break
            if not found_path:
                continue
            stats['scanned'] += 1
            if (section, name) in existing:
                stats['skipped'] += 1
                continue

            with open(found_path, 'rb') as fh:
                content = fh.read()
            image = LegendSymbolImage(
                section=section,
                symbol_name=name,
                is_default=True,
                project=None,
                content_type=_CONTENT_TYPES.get(found_ext, 'image/png'),
            )
            image.image_file.save(f'{slug}.{found_ext}', ContentFile(content), save=False)
            image.save()
            existing.add((section, name))
            stats['created'] += 1

    _log(
        f"[seed_pid_default_symbols] scanned={stats['scanned']} "
        f"created={stats['created']} skipped={stats['skipped']}"
    )
    return stats
