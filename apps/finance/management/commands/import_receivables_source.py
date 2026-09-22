"""Publish audited workbook facts without rewriting operational invoice records."""
import json

from django.core.management.base import BaseCommand, CommandError

from apps.finance.services.receivables_source import import_receivables_source


class Command(BaseCommand):
    help = 'Import and activate a source-preserving external receivables workbook snapshot.'

    def add_arguments(self, parser):
        parser.add_argument('path')
        parser.add_argument('--sheet', default='External Invoice ')
        parser.add_argument('--header-row', type=int, default=5)
        parser.add_argument('--first-row', type=int, default=6)
        parser.add_argument('--last-row', type=int, default=4409)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        try:
            report = import_receivables_source(
                options['path'], sheet=options['sheet'], header_row=options['header_row'],
                first_row=options['first_row'], last_row=options['last_row'], dry_run=options['dry_run'])
        except (ValueError, OSError, KeyError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(report, indent=2))
