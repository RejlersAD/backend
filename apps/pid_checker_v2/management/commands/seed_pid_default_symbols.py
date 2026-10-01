"""
Mirror static/default_symbols/ into LegendSymbolImage rows (is_default=True,
project=None) — see services/seed_default_symbols.py for the actual logic
(shared with the automatic post_migrate hook in apps.py, so both paths can
never drift apart).

Usage:
    python manage.py seed_pid_default_symbols

Idempotent — safe to run repeatedly; already-seeded (section, symbol_name)
pairs are skipped, never duplicated (also enforced at the DB level by
LegendSymbolImage's uniq_pidv2_default_symbol_per_section constraint).
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.pid_checker_v2.services.seed_default_symbols import seed_pid_default_symbols


class Command(BaseCommand):
    help = 'Seed LegendSymbolImage default (is_default=True) rows from static/default_symbols/.'

    def handle(self, *args, **options):
        stats = seed_pid_default_symbols(stdout=self.stdout)
        self.stdout.write(self.style.SUCCESS(
            f"Done — scanned {stats['scanned']} file(s), "
            f"created {stats['created']}, skipped {stats['skipped']} "
            f"(already seeded)."
        ))
