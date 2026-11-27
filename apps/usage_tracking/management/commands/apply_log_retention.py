"""Roll old usage_log / system_activity rows into daily summaries and delete them.

    python manage.py apply_log_retention --dry-run        # what would happen, changes nothing
    python manage.py apply_log_retention                  # needs LOG_RETENTION_ENABLED=true ...
    python manage.py apply_log_retention --force          # ... or --force for a one-off manual run
    python manage.py apply_log_retention --max-days 7     # limit calendar days per table
"""
from django.core.management.base import BaseCommand, CommandError

from apps.usage_tracking import config as cfg
from apps.usage_tracking.retention import apply_retention


class Command(BaseCommand):
    help = 'Summarise and delete usage_log / system_activity rows older than the retention windows.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Report what would be summarised and deleted.')
        parser.add_argument('--force', action='store_true', help='Run even when LOG_RETENTION_ENABLED is false.')
        parser.add_argument('--max-days', type=int, default=None, help='Calendar days per table in this run.')

    def handle(self, *args, dry_run=False, force=False, max_days=None, **options):
        if not (dry_run or force or cfg.LOG_RETENTION_ENABLED):
            raise CommandError('LOG_RETENTION_ENABLED is false. Use --dry-run to preview or --force to run once.')
        for row in apply_retention(dry_run=dry_run, max_days=max_days):
            verb = 'would delete' if dry_run else 'deleted'
            self.stdout.write(f"{row['table']}: keep {row['keep_days']} days; {row['days_processed']} day(s) "
                              f"{row['first_day'] or '-'} .. {row['last_day'] or '-'}; "
                              f"{row['rows_summarised']} rows summarised, {verb} {row['rows_deleted'] if not dry_run else row['rows_summarised']}")
