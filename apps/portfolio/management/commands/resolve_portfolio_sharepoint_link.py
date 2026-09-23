"""Print stable item identifiers for an explicitly configured sharing URL."""
from django.core.management.base import BaseCommand, CommandError

from apps.portfolio.sync import PortfolioSyncError, resolve_sharepoint_link


class Command(BaseCommand):
    help = 'Resolve PORTFOLIO_SHAREPOINT_URL into drive/item IDs with the current application permissions.'

    def handle(self, *args, **options):
        try:
            result = resolve_sharepoint_link()
        except PortfolioSyncError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(f"PORTFOLIO_SHAREPOINT_DRIVE_ID={result['drive_id']}")
        self.stdout.write(f"PORTFOLIO_SHAREPOINT_ITEM_ID={result['item_id']}")
