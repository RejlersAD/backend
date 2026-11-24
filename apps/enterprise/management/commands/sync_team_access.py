"""Re-sync team -> AI project access. Run daily (cron / Celery beat) so expired guests lose access.

    python manage.py sync_team_access
"""
from django.core.management.base import BaseCommand

from apps.enterprise.access import sync_access
from apps.enterprise.models import TeamWorkspaceAccess


class Command(BaseCommand):
    help = 'Re-apply team access to RADAI Intelligence AI projects (removes expired guests).'

    def handle(self, *args, **options):
        totals = {}
        for access in TeamWorkspaceAccess.objects.select_related('team', 'workspace'):
            for outcome, n in sync_access(access).items():
                totals[outcome] = totals.get(outcome, 0) + n
        self.stdout.write(self.style.SUCCESS(f'Team access synced: {totals or "nothing to do"}'))
