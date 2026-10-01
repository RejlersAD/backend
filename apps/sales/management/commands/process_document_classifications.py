"""Recover durable, already-authorized document jobs without a broker."""
from django.core.management.base import BaseCommand

from apps.sales.document_classification import due_classification_ids, run_document_classification


class Command(BaseCommand):
    help = 'Process due opportunity document classification intents; current scope is rechecked per job.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=20)

    def handle(self, *args, **options):
        completed = 0
        for run_id in due_classification_ids(limit=max(1, min(options['limit'], 100))):
            completed += bool(run_document_classification(run_id).get('processed'))
        self.stdout.write(f'Processed {completed} classification jobs.')
