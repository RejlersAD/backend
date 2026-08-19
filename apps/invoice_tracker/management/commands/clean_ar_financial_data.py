from decimal import Decimal

from django.core.management.base import BaseCommand

from apps.invoice_tracker.models import CustomerInvoice, Currency, InvoiceCategory


class Command(BaseCommand):
    help = 'Audit and optionally normalize A/R financial values using the finance engine.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Persist corrections; default is a dry run.')

    def handle(self, *args, **options):
        apply_changes = options['apply']
        audited = corrected = flagged = 0
        valid_currencies = {choice for choice, _ in Currency.choices}
        for invoice in CustomerInvoice.objects.all().iterator():
            audited += 1
            changed = set()
            if invoice.currency not in valid_currencies:
                invoice.currency = Currency.AED
                changed.add('currency')
            if invoice.invoice_amount is None and invoice.grand_total is not None:
                invoice.invoice_amount = invoice.grand_total
                changed.add('invoice_amount')
            if invoice.grand_total is None and invoice.invoice_amount is not None:
                invoice.grand_total = invoice.invoice_amount
                changed.add('grand_total')
            if (
                invoice.category != InvoiceCategory.INTERNAL and
                invoice.actual_payment_received is not None and
                invoice.actual_payment_received < 0
            ):
                invoice.actual_payment_received = Decimal('0')
                changed.add('actual_payment_received')
            changed.update(invoice.recompute_all())
            if changed:
                corrected += 1
                if apply_changes:
                    invoice.save(_skip_recompute=True)
            total = invoice.grand_total or invoice.invoice_amount or Decimal('0')
            paid = invoice.actual_payment_received or Decimal('0')
            if total < 0 or paid > total + Decimal('0.01'):
                flagged += 1
        mode = 'Applied' if apply_changes else 'Dry run'
        self.stdout.write(self.style.SUCCESS(
            f'{mode}: audited={audited}, corrected={corrected}, flagged_for_review={flagged}.'
        ))
