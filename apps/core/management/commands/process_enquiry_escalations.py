from django.core.management.base import BaseCommand

from apps.core.enquiry_workflow import process_sla_escalations


class Command(BaseCommand):
    help = 'Escalate open enquiries that have breached their SLA deadline.'

    def handle(self, *args, **options):
        processed = process_sla_escalations()
        self.stdout.write(self.style.SUCCESS(f'Processed {processed} enquiry escalation(s).'))
