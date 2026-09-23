"""Manually validate or synchronize the configured SharePoint POC workbook."""
from django.core.management.base import BaseCommand, CommandError

from apps.portfolio.sync import PortfolioSyncError, sync_sharepoint


class Command(BaseCommand):
    help = 'Synchronize the configured SharePoint workbook; --dry-run validates without publication.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--source-key', default='poc')

    def handle(self, *args, **options):
        try:
            result = sync_sharepoint(source_key=options['source_key'], dry_run=options['dry_run'])
        except PortfolioSyncError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(f"Portfolio synchronization: {result['status']}.")
