"""Retry an existing authorized project deletion's durable file manifest."""
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError

from apps.core.project_deletion_storage import cleanup_project_files
from apps.rbac.models import AuditLog


class Command(BaseCommand):
    help = 'Retry pending file cleanup for one recorded permanent project deletion.'

    def add_arguments(self, parser):
        parser.add_argument('--audit-id', required=True, type=UUID,
                            help='UUID returned as deletion_id by permanent project deletion.')
        parser.add_argument('--database', default='default')

    def handle(self, *args, **options):
        try:
            result = cleanup_project_files(options['audit_id'], using=options['database'],
                                           trigger='management_command')
        except (ValueError, AuditLog.DoesNotExist) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            f"Deleted: {result['deleted']}; already absent: {result['already_absent']}; "
            f"shared files preserved: {result['preserved_shared']}; pending: {result['remaining']}."
        )
        if not result['completed']:
            raise CommandError('File cleanup remains pending. Correct the recorded failure and retry this audit ID.')
