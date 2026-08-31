from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.core.project_models import Project
from apps.finance.models import Invoice, InvoiceMatchStatus, InvoicePurchaseOrderAllocation
from apps.procurement.models import PurchaseOrder, Vendor
from apps.project_control.models import BudgetAllocation, CostLedgerEntry, WBSNode
from apps.project_control.services.cost_ledger import rebuild_project_ledger
from apps.project_control.services.kpis import compute_project_kpis


class CostLedgerIntegrationTests(TestCase):
    def test_kpis_use_posted_budget_commitment_and_verified_actual(self):
        project = Project.objects.create(code='LEDGER-001', name='Ledger Project', currency='AED')
        wbs = WBSNode.objects.create(project=project, code='1.1', name='Procurement')
        BudgetAllocation.objects.create(
            project=project, wbs_node=wbs, code='BUD-001', name='Materials',
            amount=Decimal('1000'), currency='AED', status='approved', approved_at=timezone.now(),
        )
        vendor = Vendor.objects.create(vendor_code='LEDGER-VENDOR', name='Ledger Vendor', status='active')
        order = PurchaseOrder.objects.create(
            po_number='LEDGER-PO', vendor=vendor, title='Materials', category='other',
            total_amount=Decimal('600'), currency='AED', status='sent', enterprise_project=project,
        )
        invoice = Invoice.objects.create(
            invoice_number='LEDGER-INV', vendor=vendor, vendor_name=vendor.name,
            amount=Decimal('250'), total_amount=Decimal('250'), currency='AED',
            original_filename='ledger.pdf', file_path='tests/ledger.pdf',
        )
        InvoicePurchaseOrderAllocation.objects.create(
            invoice=invoice, purchase_order=order, allocated_amount=Decimal('250'),
            currency='AED', match_status=InvoiceMatchStatus.VERIFIED, verified_at=timezone.now(),
        )

        rebuild_project_ledger(project)
        kpis = compute_project_kpis(project)

        self.assertEqual(Decimal(kpis['budget']), Decimal('1000'))
        self.assertEqual(Decimal(kpis['committed']), Decimal('600'))
        self.assertEqual(Decimal(kpis['spent']), Decimal('250'))
        self.assertEqual(Decimal(kpis['remaining']), Decimal('750'))
        self.assertEqual(kpis['utilisation_pct'], 25.0)
        self.assertEqual(kpis['calculation_source'], 'posted_cost_ledger')
        self.assertEqual(CostLedgerEntry.objects.filter(project=project, status='posted').count(), 3)
