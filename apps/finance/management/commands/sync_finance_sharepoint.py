"""Validate or publish the configured Finance workbook without remote writes."""
import json

from django.core.management.base import BaseCommand, CommandError

from apps.finance.services.receivables_sharepoint import (
    FinanceSharePointSyncError, sync_finance_sharepoint,
)


class Command(BaseCommand):
    help = 'Read the Finance SharePoint workbook; --dry-run validates without publishing.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        from apps.portfolio.sync import PortfolioSyncError

        try:
            result = sync_finance_sharepoint(dry_run=options['dry_run'])
        except (FinanceSharePointSyncError, PortfolioSyncError) as exc:
            raise CommandError(str(exc)) from None
        if result['status'] == 'busy':
            raise CommandError('Another Finance workbook synchronization is running; retry later.')
        self.stdout.write(json.dumps(result, sort_keys=True))
