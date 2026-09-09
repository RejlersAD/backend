"""
Mirror the repo-committed static/io_list_default_symbols/ picture library
into the database (IOListLegendSymbolImage, is_default=True) — so the
shared default-picture library survives independently of the static files
on disk (see that model's own docstring for why: a "belt AND suspenders"
durability step, on top of — not instead of — the static-file library,
which services/default_symbol_images.py still reads directly as a
second-line fallback for anything not yet (re-)seeded).

Idempotent: skip_if_exists check against (section, symbol_name,
is_default=True), not just the DB's own partial unique constraint — so
re-running never creates duplicates or errors, and only touches rows for
newly-added static files.

Called from two places, both wrapping this one function so the logic
never drifts apart:
  - management/commands/seed_io_default_symbols.py — manual/CI run.
  - apps.py's post_migrate signal handler — automatic, right after
    `manage.py migrate` finishes.
"""
from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)

STATIC_SUBDIR = 'io_list_default_symbols'
IMAGE_EXTENSIONS = {'png': 'image/png', 'jpg': 'image/jpeg',
                    'jpeg': 'image/jpeg', 'svg': 'image/svg+xml'}


def _static_source_dir() -> Path | None:
    """Prefer the app's own static/ source dir (works pre-collectstatic,
    e.g. right after a fresh migrate in dev); fall back to STATICFILES_DIRS
    entries, then STATIC_ROOT (post-collectstatic in prod)."""
    candidates = [Path(settings.BASE_DIR) / 'static' / STATIC_SUBDIR]
    for d in getattr(settings, 'STATICFILES_DIRS', []) or []:
        candidates.append(Path(d) / STATIC_SUBDIR)
    if getattr(settings, 'STATIC_ROOT', None):
        candidates.append(Path(settings.STATIC_ROOT) / STATIC_SUBDIR)
    for c in candidates:
        if c.is_dir():
            return c
    return None


def symbol_name_for_file(stem: str) -> str:
    """Best-effort reconstruction of a display name from a slugged
    filename, e.g. 'V_CONE_FLOW_METER' -> 'V CONE FLOW METER'. Lossy
    (slugging isn't reversible — a hyphen and a space both became '_'),
    but matching at read time is done by re-slugging both sides (see
    views.SymbolImagesListView), so this only needs to be a reasonable,
    stable display label, not a byte-exact original."""
    return stem.replace('_', ' ').strip()


def seed_io_default_symbols(stdout=None) -> dict:
    """Returns {'scanned': N, 'created': N, 'skipped': N, 'source_dir': str|None}."""
    from ..models import IOListLegendSymbolImage

    def _log(msg):
        if stdout is not None:
            stdout.write(msg)
        logger.info(msg)

    base_dir = _static_source_dir()
    stats = {'scanned': 0, 'created': 0, 'skipped': 0,
              'source_dir': str(base_dir) if base_dir else None}
    if not base_dir:
        _log(f'[seed_io_default_symbols] No {STATIC_SUBDIR}/ directory found — nothing to seed.')
        return stats

    existing = set(
        IOListLegendSymbolImage.objects.filter(is_default=True)
        .values_list('section', 'symbol_name')
    )

    for section_dir in sorted(p for p in base_dir.iterdir() if p.is_dir()):
        section = section_dir.name
        for file_path in sorted(section_dir.iterdir()):
            if not file_path.is_file():
                continue
            ext = file_path.suffix.lstrip('.').lower()
            if ext not in IMAGE_EXTENSIONS:
                continue
            stats['scanned'] += 1
            symbol_name = symbol_name_for_file(file_path.stem)
            if not symbol_name:
                continue
            if (section, symbol_name) in existing:
                stats['skipped'] += 1
                continue

            with open(file_path, 'rb') as fh:
                content = fh.read()
            image = IOListLegendSymbolImage(
                section=section,
                symbol_name=symbol_name,
                is_default=True,
                created_by=None,
                content_type=IMAGE_EXTENSIONS[ext],
            )
            image.image_file.save(file_path.name, ContentFile(content), save=False)
            image.save()
            existing.add((section, symbol_name))
            stats['created'] += 1

    _log(
        f"[seed_io_default_symbols] scanned={stats['scanned']} "
        f"created={stats['created']} skipped={stats['skipped']} "
        f"source_dir={stats['source_dir']}"
    )
    return stats
