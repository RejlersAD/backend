"""
Seed every I/O List legend section's shared default row — see
services/seed_default_legends.py for the actual logic (shared with the
automatic post_migrate hook in apps.py, so both paths can never drift
apart).

Usage:
    python manage.py seed_io_default_legends

Idempotent — safe to run repeatedly; a section that already has a default
row is skipped, never duplicated.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.instrument_io_workflow.services.seed_default_legends import seed_io_default_legends


class Command(BaseCommand):
    help = 'Seed IOListLegendSheet default (is_default=True) rows for every legend section.'

    def handle(self, *args, **options):
        stats = seed_io_default_legends(stdout=self.stdout)
        self.stdout.write(self.style.SUCCESS(
            f"Done — {stats['total']} section(s) total, "
            f"created {stats['created']}, skipped {stats['skipped']} "
            f"(already had a default)."
        ))
