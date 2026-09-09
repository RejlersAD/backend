"""
Mirror static/io_list_default_symbols/ into IOListLegendSymbolImage rows
(is_default=True) — see services/seed_default_symbols.py for the actual
logic (shared with the automatic post_migrate hook in apps.py, so both
paths can never drift apart).

Usage:
    python manage.py seed_io_default_symbols

Idempotent — safe to run repeatedly; already-seeded (section, symbol_name)
pairs are skipped, never duplicated (also enforced at the DB level by
IOListLegendSymbolImage's uniq_io_default_symbol_per_section constraint).
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.instrument_io_workflow.services.seed_default_symbols import seed_io_default_symbols


class Command(BaseCommand):
    help = 'Seed IOListLegendSymbolImage default (is_default=True) rows from static/io_list_default_symbols/.'

    def handle(self, *args, **options):
        stats = seed_io_default_symbols(stdout=self.stdout)
        self.stdout.write(self.style.SUCCESS(
            f"Done — scanned {stats['scanned']} file(s), "
            f"created {stats['created']}, skipped {stats['skipped']} "
            f"(already seeded)."
        ))
