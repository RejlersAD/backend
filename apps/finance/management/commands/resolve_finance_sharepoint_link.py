"""Resolve a configured workbook link using existing application permissions."""
from django.core.management.base import BaseCommand, CommandError

from apps.finance.services.receivables_sharepoint import (
    FinanceSharePointSyncError, resolve_finance_sharepoint_link,
)


class Command(BaseCommand):
    help = 'Resolve FINANCE_SHAREPOINT_URL into stable drive/item IDs without changing access.'

    def handle(self, *args, **options):
        from apps.portfolio.sync import PortfolioSyncError

        try:
            result = resolve_finance_sharepoint_link()
        except (FinanceSharePointSyncError, PortfolioSyncError) as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(f"FINANCE_SHAREPOINT_DRIVE_ID={result['drive_id']}")
        self.stdout.write(f"FINANCE_SHAREPOINT_ITEM_ID={result['item_id']}")
