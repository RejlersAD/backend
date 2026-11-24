"""Apply each Enterprise organization's data-retention policy to RADAI Intelligence chats.

    python manage.py apply_data_retention             # preview only (default), deletes nothing
    python manage.py apply_data_retention --apply     # delete expired chats, messages and files
    python manage.py apply_data_retention --org REJ   # limit to one organization code

Schedule the --apply form (cron / Celery beat) only after reviewing a preview.
"""
from django.core.management.base import BaseCommand

from apps.enterprise import access as acl
from apps.enterprise import retention
from apps.enterprise.audit import audit
from apps.enterprise.models import OrganizationPolicy


class Command(BaseCommand):
    help = 'Preview (default) or apply organization data retention to RADAI Intelligence chats.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Actually delete. Without it, only preview.')
        parser.add_argument('--org', help='Organization code to limit the run to.')

    def handle(self, *args, **options):
        if not acl.enterprise_enabled():
            self.stdout.write('Team & Enterprise features are off; nothing to do.')
            return
        policies = (OrganizationPolicy.objects.select_related('organization')
                    .filter(data_retention_days__isnull=False, organization__is_active=True))
        if options['org']:
            policies = policies.filter(organization__code=options['org'])
        ran = False
        for policy in policies:
            org = policy.organization
            if acl.effective_edition(org) != 'enterprise':
                self.stdout.write(f'{org.code}: skipped (not on the Enterprise edition).')
                continue
            ran = True
            days = policy.data_retention_days
            if not options['apply']:
                p = retention.preview(org, days)
                self.stdout.write(f"{org.code}: would delete {p['conversations']} chats, {p['messages']} messages, "
                                  f"{p['files']} files (no activity for {days}+ days). Preview only.")
                continue
            done = retention.apply(org, days)
            audit(None, 'delete', 'EnterpriseRetention', org, f'{org.name} retention ({days} days)',
                  {'retention_days': days, **done})
            self.stdout.write(self.style.SUCCESS(
                f"{org.code}: deleted {done['conversations']} chats, {done['messages']} messages, {done['files']} files."))
        if not ran:
            self.stdout.write('No Enterprise organization has a retention period set.')
