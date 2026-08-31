"""Preview or apply canonical PR/PO/invoice commercial reconciliation."""
import json

from django.core.management.base import BaseCommand

from apps.finance.models import Invoice, InvoiceMatchStatus
from apps.procurement.models import PurchaseOrder, PurchaseRequisition
from apps.procurement.services.project_relationships import build_project_relationship_report
from apps.project_control.services.commercial_events import capture_historical_commercial_events


class Command(BaseCommand):
    help = 'Reconcile historical PRs, POs, invoices and commercial events. Preview is the default.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Apply exact canonical links and create idempotent events.')
        parser.add_argument('--json', action='store_true', help='Print machine-readable JSON.')

    def handle(self, *args, **options):
        apply = options['apply']
        relationships = build_project_relationship_report(apply=apply, sample_limit=100)
        invoices = Invoice.objects.all()
        matched = invoices.filter(po_allocations__match_status=InvoiceMatchStatus.VERIFIED).distinct().count()
        report = {
            'mode': 'apply' if apply else 'preview',
            'purchase_requisitions': PurchaseRequisition.objects.count(),
            'purchase_orders': PurchaseOrder.objects.count(),
            'invoices': invoices.count(),
            'verified_invoices': matched,
            'invoices_requiring_reconciliation': invoices.exclude(
                po_allocations__match_status=InvoiceMatchStatus.VERIFIED,
            ).distinct().count(),
            'canonical_relationships': relationships,
            'events_created': capture_historical_commercial_events() if apply else None,
        }
        if options['json']:
            self.stdout.write(json.dumps(report, indent=2, default=str))
            return
        self.stdout.write('Historical Commercial Reconciliation')
        self.stdout.write(f"Mode: {report['mode']}")
        self.stdout.write(
            f"PRs={report['purchase_requisitions']} POs={report['purchase_orders']} "
            f"Invoices={report['invoices']} verified={report['verified_invoices']} "
            f"manual_review={report['invoices_requiring_reconciliation']}"
        )
        self.stdout.write(f"Canonical changes applied: {relationships['changes_applied']}")
        if apply:
            self.stdout.write(self.style.SUCCESS(f"Events created: {report['events_created']}"))
        else:
            self.stdout.write(self.style.WARNING('Preview only. Re-run with --apply after reviewing ambiguous records.'))
