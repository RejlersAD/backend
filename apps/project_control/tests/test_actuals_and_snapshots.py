from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.core.project_models import Project
from apps.finance.models import Invoice, InvoiceMatchStatus, InvoicePurchaseOrderAllocation
from apps.procurement.models import PurchaseOrder, Vendor
from apps.users.models import User

from ..models import (
    ApprovedHourEntry, BudgetAllocation, ControlAccount, CostAllocation,
    CostLedgerEntry, IntegratedReportingSnapshot, ReportingPeriod, WBSNode,
)
from ..services.actuals import create_integrated_snapshot, reconcile_reporting_period


class ActualsAndSnapshotTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='actual-owner', email='actual-owner@example.com')
        self.approver = User.objects.create_superuser(
            username='actual-approver', email='actual-approver@example.com', password='unused',
        )
        self.project = Project.objects.create(
            code='ACT-001', name='Actuals Project', owner=self.owner, currency='AED', progress=Decimal('40'),
            start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
        )
        self.wbs = WBSNode.objects.create(project=self.project, code='1.1', name='Engineering')
        self.budget = BudgetAllocation.objects.create(
            project=self.project, wbs_node=self.wbs, code='BUD-001', name='Engineering',
            amount=Decimal('1000'), currency='AED', status='approved', approved_at=timezone.now(),
        )
        self.account = ControlAccount.objects.create(
            project=self.project, wbs_node=self.wbs, code='CA-ENG', name='Engineering',
            manager=self.owner, baseline_start=date(2026, 1, 1), baseline_finish=date(2026, 1, 31),
            status='active', approved_by=self.approver, approved_at=timezone.now(),
        )
        self.period = ReportingPeriod.objects.create(
            project=self.project, sequence=1, name='January 2026',
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 31),
            data_date=date(2026, 1, 31), status='open', created_by=self.owner,
        )

    def test_reconciliation_posts_hours_and_verified_finance_once(self):
        hour = ApprovedHourEntry.objects.create(
            project=self.project, control_account=self.account, reporting_period=self.period,
            employee_code='E-100', work_date=date(2026, 1, 15), hours=Decimal('8'),
            hourly_cost_rate=Decimal('25'), labor_actual_cost=Decimal('200'), currency='AED',
            source_reference='TS-2026-001', status='approved', approved_by=self.approver,
        )
        vendor = Vendor.objects.create(vendor_code='ACT-VENDOR', name='Actual Vendor', status='active')
        order = PurchaseOrder.objects.create(
            po_number='ACT-PO', vendor=vendor, title='Service', category='other',
            total_amount=Decimal('500'), currency='AED', status='sent', enterprise_project=self.project,
            po_date=date(2026, 1, 3),
        )
        split = CostAllocation.objects.create(
            project=self.project, wbs_node=self.wbs, budget_allocation=self.budget,
            source_type='purchase_order', source_id=str(order.pk), source_reference=order.po_number,
            amount=Decimal('500'), currency='AED', status='approved', approved_by=self.approver,
            approved_at=timezone.now(),
        )
        invoice = Invoice.objects.create(
            invoice_number='ACT-INV', vendor=vendor, vendor_name=vendor.name,
            invoice_date=date(2026, 1, 20), amount=Decimal('300'), total_amount=Decimal('300'),
            currency='AED', original_filename='actual.pdf', file_path='tests/actual.pdf',
        )
        InvoicePurchaseOrderAllocation.objects.create(
            invoice=invoice, purchase_order=order, allocated_amount=Decimal('300'),
            currency='AED', match_status=InvoiceMatchStatus.VERIFIED, verified_at=timezone.now(),
        )

        first = reconcile_reporting_period(self.period, user=self.approver)
        second = reconcile_reporting_period(self.period, user=self.approver)

        self.assertEqual(first.status, 'completed')
        self.assertEqual(first.approved_hours, Decimal('8'))
        self.assertEqual(first.labor_actual_cost, Decimal('200'))
        self.assertEqual(first.finance_actual_cost, Decimal('300'))
        self.assertEqual(first.ledger_actual_cost, Decimal('500'))
        self.assertEqual(second.run_number, 2)
        self.assertEqual(CostLedgerEntry.objects.filter(entry_key=f'approved-hour:{hour.pk}').count(), 1)
        finance_row = CostLedgerEntry.objects.get(entry_key__startswith=f'invoice:', cost_allocation=split)
        self.assertEqual(finance_row.control_account, self.account)
        self.assertEqual(finance_row.reporting_period, self.period)

    def test_lock_snapshot_is_versioned_and_immutable(self):
        reconciliation = reconcile_reporting_period(self.period, user=self.approver)
        self.assertEqual(reconciliation.status, 'completed')
        self.period.status = 'submitted'
        self.period.submitted_by = self.owner
        self.period.save(update_fields=['status', 'submitted_by', 'updated_at'])

        snapshot = create_integrated_snapshot(self.period, user=self.approver)

        self.assertEqual(snapshot.version, 1)
        self.assertEqual(snapshot.budget_at_completion, Decimal('1000'))
        self.assertEqual(snapshot.earned_value, Decimal('400'))
        self.assertEqual(snapshot.planned_value, Decimal('1000'))
        self.assertTrue(snapshot.checksum)
        snapshot.actual_cost = Decimal('999')
        with self.assertRaisesMessage(ValueError, 'immutable'):
            snapshot.save()
        self.assertEqual(IntegratedReportingSnapshot.objects.get(pk=snapshot.pk).actual_cost, Decimal('0'))

    def test_unallocated_verified_actual_blocks_clean_reconciliation(self):
        vendor = Vendor.objects.create(vendor_code='UNMAP-VENDOR', name='Unmapped Vendor', status='active')
        order = PurchaseOrder.objects.create(
            po_number='UNMAP-PO', vendor=vendor, title='Unmapped', category='other',
            total_amount=Decimal('100'), currency='AED', status='sent', enterprise_project=self.project,
            po_date=date(2026, 1, 4),
        )
        invoice = Invoice.objects.create(
            invoice_number='UNMAP-INV', vendor=vendor, vendor_name=vendor.name,
            invoice_date=date(2026, 1, 21), amount=Decimal('100'), total_amount=Decimal('100'),
            currency='AED', original_filename='unmapped.pdf', file_path='tests/unmapped.pdf',
        )
        InvoicePurchaseOrderAllocation.objects.create(
            invoice=invoice, purchase_order=order, allocated_amount=Decimal('100'), currency='AED',
            match_status=InvoiceMatchStatus.VERIFIED, verified_at=timezone.now(),
        )

        run = reconcile_reporting_period(self.period, user=self.approver)

        self.assertEqual(run.status, 'exceptions')
        self.assertIn('unmapped_control_account', {item['type'] for item in run.exceptions})
