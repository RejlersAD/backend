"""
Seed every I/O List legend section's shared default row (IOListLegendSheet,
created_by=None, is_default=True, is_active=True) from the static snapshot
in _default_legend_data.py — same "belt AND suspenders" reasoning as
services/seed_default_symbols.py for symbol images: production must be able
to get this content from a plain command/post_migrate run, not depend on a
migration chain that (before this rework) needed an existing legend row to
bootstrap its shared owner from.

Idempotent: skipped entirely per-section if a default already exists there
(get_or_create keyed on (section, is_default=True) — the same pair the
partial unique constraint on is_active enforces at most one active instance
of) — safe to run any number of times, never duplicates, never clobbers a
default an admin has since hand-edited.

Called from two places, both wrapping this one function so the logic never
drifts apart:
  - management/commands/seed_io_default_legends.py — manual/CI run.
  - apps.py's post_migrate signal handler — automatic, right after
    `manage.py migrate` finishes (mirrors seed_io_default_symbols exactly).
"""
from __future__ import annotations

import logging

from ._default_legend_data import DEFAULT_LEGEND_SECTIONS

logger = logging.getLogger(__name__)


def seed_io_default_legends(stdout=None) -> dict:
    """Returns {'total': N, 'created': N, 'skipped': N}."""
    from ..models import IOListLegendSheet

    def _log(msg):
        if stdout is not None:
            stdout.write(msg)
        logger.info(msg)

    stats = {'total': len(DEFAULT_LEGEND_SECTIONS), 'created': 0, 'skipped': 0}

    for entry in DEFAULT_LEGEND_SECTIONS:
        section = entry['section']
        if IOListLegendSheet.objects.filter(section=section, is_default=True).exists():
            stats['skipped'] += 1
            continue
        IOListLegendSheet.objects.create(
            section=section,
            name=entry['name'],
            description=entry.get('description', ''),
            definition=entry.get('definition', {}),
            is_active=True,
            is_default=True,
            created_by=None,
        )
        stats['created'] += 1

    _log(
        f"[seed_io_default_legends] total={stats['total']} "
        f"created={stats['created']} skipped={stats['skipped']}"
    )
    return stats
