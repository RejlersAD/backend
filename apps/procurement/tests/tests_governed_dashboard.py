from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.procurement.models import ProcurementReportingSnapshot, PurchaseOrder, Vendor
from apps.procurement.services.governed_dashboard import build_dashboard, create_snapshot


class GovernedProcurementDashboardTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='procurement.dashboard@test.invalid', password='test-only-password'
        )
        self.vendor = Vendor.objects.create(vendor_code='V-GOV-1', name='Governed Vendor')

    def _order(self, number, currency, amount):
        return PurchaseOrder.objects.create(
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
