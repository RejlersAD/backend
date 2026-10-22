from django.core.management.base import BaseCommand, CommandError
from apps.hr_core.recruitment_sync import sync_positions


class Command(BaseCommand):
    help = 'Synchronize all published Rejlers AE careers positions into Talent Acquisition.'

    def handle(self, *args, **options):
        result = sync_positions()
        if result['status'] == 'failed':
            raise CommandError(result['detail'])
        self.stdout.write(str(result))
