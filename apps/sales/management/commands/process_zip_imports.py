"""Recover durable, already-authorized ZIP imports without a broker."""
from django.core.management.base import BaseCommand

from apps.sales.zip_import import due_zip_import_ids, run_zip_import


class Command(BaseCommand):
    help = 'Extract due opportunity ZIP imports (queued, or running with an expired lease).'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=5)

    def handle(self, *args, **options):
        completed = 0
        for zip_import_id in due_zip_import_ids(limit=max(1, min(options['limit'], 50))):
            result = run_zip_import(zip_import_id)
            completed += bool(result.get('processed'))
            self.stdout.write(f"{zip_import_id}: {result.get('status')}")
        self.stdout.write(f'Processed {completed} ZIP imports.')
