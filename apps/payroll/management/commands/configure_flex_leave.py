"""Assign exact, reviewed RADAI accounts to the user-authorized FU route."""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from apps.hr_core.models import HRWorkflowDefinition, HRWorkflowStage
from apps.rbac.approval_eligibility import approval_access


class Command(BaseCommand):
    help = 'Configure a new FU workflow version: Jemal → Rafat → Mohammed → HR.'

    def add_arguments(self, parser):
        parser.add_argument('--jamal', '--jemal', dest='jamal', required=True, help='Exact RADAI email address')
        for name in ('rafat', 'mohammed'):
            parser.add_argument(f'--{name}', required=True, help='Exact RADAI email address')

    @transaction.atomic
    def handle(self, *args, **options):
        users = []
        for name in ('jamal', 'rafat', 'mohammed'):
            matches = list(get_user_model().objects.filter(email__iexact=options[name], is_active=True)[:2])
            if len(matches) != 1:
                raise CommandError(f'{name}: select exactly one active RADAI account.')
            users.append(matches[0])
        if len({user.pk for user in users}) != 3:
            raise CommandError('FU approvers must be three distinct accounts.')
        previous = HRWorkflowDefinition.objects.select_for_update().filter(code='flex_utilization_v1').order_by('-version').first()
        definition = HRWorkflowDefinition.objects.create(
            code='flex_utilization_v1', version=previous.version + 1 if previous else 1,
            name='Flex Utilization', subject_type='payroll.leave_request',
        )
        for sequence, (name, user) in enumerate(zip(('Jamal Ayoub', 'Rafat S. M. Saqer', 'Mohamad El-Ghawanmeh'), users), 1):
            code = ('jemal_review', 'rafat_review', 'mohammed_review')[sequence - 1]
            HRWorkflowStage.objects.create(definition=definition, code=code, name=name,
                sequence=sequence, approver_type='user', approver_value=str(user.pk), require_comment_on_reject=True)
        HRWorkflowStage.objects.create(definition=definition, code='hr_review', name='HR final approval',
            sequence=4, approver_type='role', approver_value='hr_manager', require_comment_on_reject=True)
        self.stdout.write(self.style.SUCCESS(f'Configured FU workflow version {definition.version}. Existing requests retain their route.'))
        missing = [user.email for user in users if not approval_access(user, 'payroll')]
        if missing:
            self.stdout.write(self.style.WARNING('FU submission remains blocked until payroll approval access is configured for: ' + ', '.join(missing)))
