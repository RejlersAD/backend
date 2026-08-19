from django.core.management.base import BaseCommand

from apps.finance.models import Invoice
from apps.finance.services.payables import evaluate_three_way_match, reconcile_invoice_status


class Command(BaseCommand):
    help = 'Re-evaluate A/P matching evidence and reconcile lifecycle/payment statuses.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Persist changes; default is a dry run.')

    def handle(self, *args, **options):
        apply_changes = options['apply']
        changed_invoices = allocations_checked = 0
        for invoice in Invoice.objects.prefetch_related('po_allocations').iterator(chunk_size=200):
            if apply_changes:
                for allocation in invoice.po_allocations.all():
                    evaluate_three_way_match(allocation)
                    allocations_checked += 1
            if reconcile_invoice_status(
                invoice, audit=apply_changes, persist=apply_changes,
            ):
                changed_invoices += 1
        mode = 'Applied' if apply_changes else 'Dry run'
        self.stdout.write(self.style.SUCCESS(
            f'{mode}: {changed_invoices} invoices require/reconciled changes; '
            f'{allocations_checked} allocations re-evaluated.'
        ))
