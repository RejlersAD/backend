import json

from django.core.management.base import BaseCommand, CommandError

from apps.portfolio.importer import import_workbook


class Command(BaseCommand):
    help = 'Validate and import a versioned POC workbook without editing operational projects.'

    def add_arguments(self, parser):
        parser.add_argument('path')
        parser.add_argument('--source-key', default='poc')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        try:
            result = import_workbook(options['path'], source_key=options['source_key'], dry_run=options['dry_run'])
        except (ValueError, OSError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result, default=str))
