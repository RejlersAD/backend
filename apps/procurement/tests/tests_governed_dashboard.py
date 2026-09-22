from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.procurement.models import ProcurementReportingSnapshot, PurchaseOrder, PurchaseRequisition, Receipt, Vendor
from apps.procurement.services.governed_dashboard import build_dashboard, create_snapshot


class GovernedProcurementDashboardTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='procurement.dashboard@test.invalid', password='test-only-password'
        )
        self.vendor = Vendor.objects.create(vendor_code='V-GOV-1', name='Governed Vendor')

    def _order(self, number, currency, amount, **updates):
        order = PurchaseOrder.objects.create(
            po_number=number,
            vendor=self.vendor,
            title='Governance test order',
            category='other',
            total_amount=Decimal(amount),
            currency=currency,
            status='sent',
            expected_delivery=timezone.localdate() - timedelta(days=2),
            created_by=self.user,
        )
        if updates:
            PurchaseOrder.objects.filter(pk=order.pk).update(**updates)
            order.refresh_from_db()
        return order

    def test_commitments_are_grouped_without_implicit_currency_conversion(self):
        self._order('PO-GOV-AED', 'AED', '100.00')
        self._order('PO-GOV-USD', 'USD', '25.00')

        payload = build_dashboard(self.user, {'scope': 'portfolio'})

        self.assertEqual(payload['metrics']['po_commitment'], [
            {'currency': 'AED', 'amount': '100.00'},
            {'currency': 'USD', 'amount': '25.00'},
        ])
        self.assertEqual(payload['metrics']['po_commitment_aed']['currency'], 'AED')
        self.assertEqual(payload['metrics']['po_commitment_aed']['amount'], '191.81')
        self.assertTrue(payload['metrics']['po_commitment_aed']['conversion_complete'])
        self.assertEqual(payload['metrics']['counts']['overdue_deliveries'], 2)
        self.assertIsNone(payload['metrics']['realized_savings'])

    def test_reporting_snapshot_is_immutable_and_audited(self):
        self._order('PO-GOV-SNAPSHOT', 'AED', '500.00')
        snapshot = create_snapshot(self.user, {'scope': 'portfolio', 'currency': 'AED'})

        self.assertTrue(snapshot.checksum)
        self.assertTrue(snapshot.calculations.filter(metric_key='po_commitment').exists())
        snapshot.reporting_currency = 'USD'
        with self.assertRaisesMessage(ValueError, 'immutable'):
            snapshot.save()

        with self.assertRaisesMessage(ValueError, 'immutable'):
            ProcurementReportingSnapshot.objects.get(pk=snapshot.pk).delete()

    def test_monthly_trend_uses_controlled_fx_and_excludes_drafts_and_cancelled_orders(self):
        self._order('PO-TREND-AED', 'AED', '100.00', po_date=date(2026, 1, 8))
        self._order('PO-TREND-USD', 'USD', '25.00', po_date=date(2026, 3, 8))
        self._order('PO-TREND-DRAFT', 'AED', '800.00', po_date=date(2026, 2, 8), status='draft')
        self._order('PO-TREND-CANCELLED', 'AED', '900.00', po_date=date(2026, 2, 8), status='cancelled')
        self._order('PO-TREND-OUTSIDE', 'AED', '1000.00', po_date=date(2025, 12, 31))

        payload = build_dashboard(self.user, {'period_start': '2026-01-01', 'period_end': '2026-03-31'})

        self.assertEqual(payload['spend_trend']['months'], [
            {'month': '2026-01', 'amount': '100.00', 'cumulative_amount': '100.00', 'order_count': 1, 'missing_currencies': []},
            {'month': '2026-02', 'amount': '0.00', 'cumulative_amount': '100.00', 'order_count': 0, 'missing_currencies': []},
            {'month': '2026-03', 'amount': '91.81', 'cumulative_amount': '191.81', 'order_count': 1, 'missing_currencies': []},
        ])
        self.assertEqual(payload['metrics']['po_commitment_aed']['amount'], '191.81')
        self.assertEqual(payload['supplier_spend_aed']['suppliers'][0]['percent'], 100)
        self.assertEqual(payload['purchase_order_status']['total'], 4)
        self.assertEqual(sum(row['count'] for row in payload['purchase_order_status']['statuses']), 4)

    def test_missing_fx_withholds_supplier_shares_and_cumulative_spend(self):
        self._order('PO-UNKNOWN-FX', 'ZZZ', '100.00', po_date=date(2026, 1, 8))
        self._order('PO-KNOWN-FX', 'AED', '100.00', po_date=date(2026, 2, 8))

        payload = build_dashboard(self.user, {'period_start': '2026-01-01', 'period_end': '2026-02-28'})

        self.assertFalse(payload['spend_trend']['conversion_complete'])
        self.assertEqual(payload['spend_trend']['missing_currencies'], ['ZZZ'])
        self.assertIsNone(payload['spend_trend']['months'][0]['amount'])
        self.assertEqual(payload['spend_trend']['months'][1]['amount'], '100.00')
        self.assertIsNone(payload['spend_trend']['months'][1]['cumulative_amount'])
        self.assertFalse(payload['supplier_spend_aed']['conversion_complete'])
        self.assertEqual(payload['supplier_spend_aed']['suppliers'], [])

    def test_order_coverage_keeps_one_issued_cohort_and_deduplicates_receipts(self):
        self._order('PO-FLOW-SENT', 'AED', '100.00', po_date=date(2026, 1, 8))
        acknowledged = self._order('PO-FLOW-ACK', 'AED', '100.00', po_date=date(2026, 1, 9), status='acknowledged')
        self._order('PO-FLOW-DRAFT', 'AED', '100.00', po_date=date(2026, 1, 10), status='draft')
        prior = self._order('PO-FLOW-PRIOR', 'AED', '100.00', po_date=date(2025, 12, 8))
        for index, order in enumerate([acknowledged, acknowledged, prior]):
            receipt = Receipt.objects.create(receipt_number=f'GR-FLOW-{index}', purchase_order=order, status='accepted')
            Receipt.objects.filter(pk=receipt.pk).update(receipt_date=date(2026, 2, 1))

        flow = build_dashboard(self.user, {'period_start': '2026-01-01', 'period_end': '2026-01-31'})['purchasing_flow']

        self.assertEqual(flow['purchase_orders'], 3)
        self.assertEqual(flow['issued_purchase_orders'], 2)
        self.assertEqual(flow['supplier_acknowledged_orders'], 1)
        self.assertEqual(flow['acknowledgement_percent'], 50)
        self.assertEqual(flow['orders_with_accepted_receipts'], 1)
        self.assertEqual(flow['receipt_coverage_percent'], 50)
        self.assertEqual(flow['accepted_receipts'], 0)

    def test_readiness_distinguishes_master_data_from_compliance(self):
        complete = Vendor.objects.create(
            vendor_code='V-GOV-COMPLETE', name='Complete supplier', tax_id='TAX',
            trade_license_number='LIC', contact_person='Owner', email='supplier@example.test',
            phone='123', address='Address', country='AE', audit_status='failed',
        )
        self._order('PO-READINESS-A', 'AED', '100.00')
        self._order('PO-READINESS-B', 'AED', '100.00', vendor=complete)
        Vendor.objects.create(vendor_code='V-GOV-OUTSIDE', name='Unrelated supplier')

        readiness = build_dashboard(self.user, {})['supplier_readiness']

        self.assertEqual(readiness['total'], 2)
        self.assertEqual(readiness['complete'], 0)
        self.assertEqual(readiness['incomplete'], 2)
        self.assertEqual(readiness['master_data_complete'], 1)
        self.assertEqual(readiness['master_data_complete_percent'], 50)

    def test_recent_decisions_include_recorded_supplier_and_approval_status(self):
        now = timezone.now()
        self._order('PO-DECISION', 'AED', '100.00', approved_at=now, approved_by=self.user)
        PurchaseRequisition.objects.create(
            pr_number='PR-DECISION', vendor=self.vendor, status='approved',
            approved_at=now, approved_by=self.user,
        )

        decisions = build_dashboard(self.user, {})['recent_decisions']

        self.assertEqual(len(decisions), 2)
        self.assertTrue(all(row['supplier'] == self.vendor.name and row['status'] == 'approved' for row in decisions))
