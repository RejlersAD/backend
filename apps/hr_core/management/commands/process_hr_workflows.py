from django.core.management.base import BaseCommand

from apps.hr_core.workflows import HRWorkflowService


class Command(BaseCommand):
    help = 'Send reminders and escalate overdue HR workflow tasks.'

    def handle(self, *args, **options):
        result = HRWorkflowService.process_overdue_tasks()
        self.stdout.write(self.style.SUCCESS(
            f"Processed HR workflows: {result['reminded']} reminder(s), {result['escalated']} escalation(s)."
        ))
