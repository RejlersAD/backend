from django.core.management.base import BaseCommand
from apps.rbac.ai_snapshots import capture_workforce


class Command(BaseCommand):
    help = 'Capture today\'s AI-eligible workforce. Does not backfill or replace historical snapshots.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS(f'Created {capture_workforce()} organization snapshots.'))
