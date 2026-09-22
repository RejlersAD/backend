"""Generate a dated Finance workbook artifact without importing invoice records."""
import json
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.finance.services.invoice_performance_snapshot import generate_invoice_performance_snapshot


class Command(BaseCommand):
    help = 'Generate aggregate daily invoice performance JSON; no database records are changed.'
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('workbook', type=Path)
        parser.add_argument('--last-row', type=int, required=True)
        parser.add_argument('--sheet', default='External Invoice ')
        parser.add_argument('--header-row', type=int, default=5)
        parser.add_argument('--snapshot-at', help='Optional ISO timestamp with timezone for reproducibility.')
        parser.add_argument('--output', type=Path, required=True)

    def handle(self, *args, **options):
        try:
            captured = datetime.fromisoformat(options['snapshot_at']) if options['snapshot_at'] else None
            data = generate_invoice_performance_snapshot(
                options['workbook'], last_row=options['last_row'], sheet=options['sheet'],
                header_row=options['header_row'], snapshot_at=captured,
            )
            options['output'].parent.mkdir(parents=True, exist_ok=True)
            options['output'].write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        except (OSError, ValueError, KeyError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(f'Wrote {len(data["days"])} currency/day aggregates. No database records changed.'))
