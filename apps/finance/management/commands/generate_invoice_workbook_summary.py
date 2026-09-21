"""Generate a reviewed workbook summary artifact without reading/writing the DB."""
import json
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.finance.services.workbook_summary_snapshot import generate_workbook_snapshot


class Command(BaseCommand):
    help = 'Generate aggregate invoice workbook JSON; does not import invoice records.'
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('workbook', type=Path)
        parser.add_argument('--last-row', type=int, required=True,
                            help='Last invoice row, excluding any total or footer rows.')
        parser.add_argument('--sheet', default='External Invoice ')
        parser.add_argument('--header-row', type=int, default=5)
        parser.add_argument('--snapshot-at', help='Optional ISO timestamp with timezone for reproducibility.')
        parser.add_argument('--output', type=Path, required=True)

    def handle(self, *args, **options):
        try:
            captured = datetime.fromisoformat(options['snapshot_at']) if options['snapshot_at'] else None
            data = generate_workbook_snapshot(
                options['workbook'], last_row=options['last_row'], sheet=options['sheet'],
                header_row=options['header_row'], snapshot_at=captured,
            )
            output = options['output']
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        except (OSError, ValueError, KeyError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(f'Wrote {data["invoice_count"]} invoice rows to {output}. No database records changed.'))
