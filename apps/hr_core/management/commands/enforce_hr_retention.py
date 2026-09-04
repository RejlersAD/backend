"""Review or enforce explicitly configured HR retention policies."""
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.hr_core.governance import audit
from apps.hr_core.models import HRAssistantInteraction, HRRetentionPolicy


SUPPORTED_CATEGORIES = {
    'assistant_interactions': (HRAssistantInteraction, 'created_at'),
}


class Command(BaseCommand):
    help = 'Preview HR retention candidates; pass --execute for configured delete/anonymize actions.'

    def add_arguments(self, parser):
        parser.add_argument('--execute', action='store_true', help='Apply eligible actions. Default is dry-run.')

    def handle(self, *args, **options):
        execute = options['execute']
        total = 0
        for policy in HRRetentionPolicy.objects.filter(enabled=True):
            if policy.legal_hold:
                self.stdout.write(f'{policy.data_category}: skipped (legal hold)')
                continue
            target = SUPPORTED_CATEGORIES.get(policy.data_category)
            if not target:
                self.stdout.write(f'{policy.data_category}: review required (no automatic handler)')
                continue
            model, date_field = target
            cutoff = timezone.now() - timedelta(days=policy.retention_days)
            queryset = model.objects.filter(**{f'{date_field}__lt': cutoff})
            count = queryset.count()
            total += count
            self.stdout.write(f'{policy.data_category}: {count} candidate(s), action={policy.disposition_action}')
            if not execute or not count or policy.disposition_action == 'review':
                continue
            if policy.disposition_action == 'delete':
                queryset.delete()
            elif policy.disposition_action == 'anonymize':
                queryset.update(question='[RETAINED METADATA - CONTENT REMOVED]', answer='[RETAINED METADATA - CONTENT REMOVED]', citations=[])
            else:
                raise CommandError(f'Unsupported disposition action: {policy.disposition_action}')
            audit(actor=None, action='retention.enforce', object_type=model.__name__, metadata={
                'data_category': policy.data_category, 'count': count, 'action': policy.disposition_action,
            })
        self.stdout.write(self.style.SUCCESS(f'{"Applied" if execute else "Dry run"}: {total} total candidate(s).'))
