from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from apps.procurement.services.requisition_conversion import RequisitionConversionService


class FakeRelatedOrders:
    def __init__(self, existing=None):
        self.existing = existing

    def order_by(self, *args):
        return self

    def first(self):
        return self.existing


class FakeRequisition(SimpleNamespace):
    def save(self, *args, **kwargs):
        self.saved_with = kwargs


class RequisitionConversionServiceTests(SimpleTestCase):
    def setUp(self):
        self.actor = SimpleNamespace(id='buyer-1')
        self.issuer = SimpleNamespace(
            email='issuer@example.com',
            get_full_name=lambda: 'PR Issuer',
        )
        self.vendor = SimpleNamespace(
            id='vendor-1',
            status='active',
            contact_person='Vendor Contact',
            trade_license_number='TL-100',
            phone='+971500000000',
            email='vendor@example.com',
            address='Abu Dhabi',
        )

    def _pr(self, **overrides):
        values = {
            'id': 'pr-id',
            'pk': 'pr-id',
            'pr_number': 'RAD-PRJ-PR-0042_2026',
            'status': 'approved',
            'purchase_orders': FakeRelatedOrders(),
            'vendor_id': self.vendor.id,
            'vendor': self.vendor,
            'total_price': Decimal('1250.00'),
            'estimated_budget': Decimal('1500.00'),
            'items': [],
            'price_description': 'Engineering review service',
            'product_service': 'Engineering Services',
            'title': 'Engineering Services',
            'issued_by': self.issuer,
            'supplier_business_id': '',
            'supplier_name': 'Vendor Company LLC',
            'preferred_supplier_if_any': '',
            'description_reason': 'Required project engineering review.',
            'category': 'engineering_services',
            'currency': 'AED',
            'price_remarks_data': {'payment_terms': 'Net 45'},
            'project': 'Project Alpha',
            'pm_name': None,
            'required_date': None,
            'approval_workflow_config': [{
                'role': 'Project Manager',
                'status': 'approved',
                'approved_by_name': 'Project Manager',
                'approved_at': '2026-08-06T10:00:00+04:00',
            }],
            'purchase_recommendation': 'Proceed with selected vendor.',
            'notes': 'Source PR notes',
            'attachments': [{'filename': 'quote.pdf'}],
            'po_number_reference': '',
            'enterprise_project': None,
        }
        values.update(overrides)
        return FakeRequisition(**values)

    @patch.object(RequisitionConversionService, '_convert_locked')
    @patch('apps.procurement.services.requisition_conversion.get_object_or_404')
    @patch('apps.procurement.services.requisition_conversion.PurchaseRequisition.objects')
    def test_conversion_locks_pr_without_joining_nullable_relations(
        self,
        requisitions,
        get_object_or_404,
        convert_locked,
    ):
        locked_queryset = MagicMock()
        requisitions.select_for_update.return_value = locked_queryset
        pr = self._pr()
        get_object_or_404.return_value = pr
        convert_locked.return_value = ('pr-result', 'po-result')

        result = RequisitionConversionService.convert.__wrapped__(
            RequisitionConversionService,
            pr.id,
            self.actor,
        )

        requisitions.select_for_update.assert_called_once_with()
        get_object_or_404.assert_called_once_with(locked_queryset, pk=pr.id)
        self.assertEqual(result, ('pr-result', 'po-result'))

    @patch('apps.project_control.models.CostAllocation.objects')
    @patch('apps.procurement.services.requisition_conversion.PurchaseOrder.objects')
    def test_approved_pr_is_mapped_to_one_draft_po(self, purchase_orders, allocations):
        allocations.filter.return_value = []
        purchase_orders.filter.return_value.exists.return_value = False
        created_po = SimpleNamespace(id='po-id', po_number='RAD-PRJ-PUR-0042_2026')
        purchase_orders.create.return_value = created_po
        pr = self._pr()

        result_pr, result_po = RequisitionConversionService._convert_locked(pr, self.actor)

        self.assertIs(result_pr, pr)
        self.assertIs(result_po, created_po)
        self.assertEqual(pr.status, 'converted')
        self.assertEqual(pr.po_number_reference, created_po.po_number)
        self.assertEqual(pr.saved_with['update_fields'], ['status', 'po_number_reference', 'price_remarks_data', 'updated_at'])
        self.assertEqual(pr.price_remarks_data['po_link_previous_status'], 'approved')

        create_data = purchase_orders.create.call_args.kwargs
        self.assertEqual(create_data['po_number'], 'RAD-PRJ-PUR-0042_2026')
        self.assertEqual(create_data['pr_reference'], pr)
        self.assertEqual(create_data['vendor'], self.vendor)
        self.assertEqual(create_data['total_amount'], Decimal('1250.00'))
        self.assertEqual(create_data['payment_terms'], 'Net 45')
        self.assertEqual(create_data['items'][0]['total'], 1250.0)
        self.assertEqual(create_data['approval_log'][0]['status'], 'Approved')

    @patch('apps.procurement.services.requisition_conversion.PurchaseOrder.objects')
    def test_unapproved_pr_cannot_be_converted(self, purchase_orders):
        pr = self._pr(status='submitted')

        with self.assertRaisesMessage(ValidationError, 'Only approved requisitions'):
            RequisitionConversionService._convert_locked(pr, self.actor)

        purchase_orders.create.assert_not_called()

    @patch('apps.procurement.services.requisition_conversion.PurchaseOrder.objects')
    def test_existing_linked_po_blocks_duplicate_conversion(self, purchase_orders):
        existing = SimpleNamespace(id='existing-id', po_number='PO-EXISTING')
        pr = self._pr(purchase_orders=FakeRelatedOrders(existing))

        with self.assertRaisesMessage(ValidationError, 'already converted to PO-EXISTING'):
            RequisitionConversionService._convert_locked(pr, self.actor)

        purchase_orders.create.assert_not_called()

    @patch('apps.procurement.services.requisition_conversion.PurchaseOrder.objects')
    def test_converted_status_blocks_duplicate_when_relation_is_missing(self, purchase_orders):
        pr = self._pr(status='converted')

        with self.assertRaisesMessage(ValidationError, 'already been converted'):
            RequisitionConversionService._convert_locked(pr, self.actor)

        purchase_orders.create.assert_not_called()

    @patch('apps.procurement.services.requisition_conversion.Vendor.objects')
    @patch('apps.procurement.services.requisition_conversion.PurchaseOrder.objects')
    def test_active_linked_vendor_is_required(self, purchase_orders, vendors):
        vendors.filter.return_value.__getitem__.return_value = []
        pr = self._pr(vendor_id=None, vendor=None)

        with self.assertRaisesMessage(ValidationError, 'no exact active vendor match'):
            RequisitionConversionService._convert_locked(pr, self.actor)

        purchase_orders.create.assert_not_called()

    @patch('apps.project_control.models.CostAllocation.objects')
    @patch('apps.procurement.services.requisition_conversion.Vendor.objects')
    @patch('apps.procurement.services.requisition_conversion.PurchaseOrder.objects')
    def test_unique_exact_supplier_match_is_linked_during_conversion(self, purchase_orders, vendors, allocations):
        allocations.filter.return_value = []
        vendors.filter.return_value.__getitem__.return_value = [self.vendor]
        purchase_orders.filter.return_value.exists.return_value = False
        purchase_orders.create.return_value = SimpleNamespace(
            id='po-id',
            po_number='RAD-PRJ-PUR-0042_2026',
        )
        pr = self._pr(vendor_id=None, vendor=None)

        RequisitionConversionService._convert_locked(pr, self.actor)

        self.assertEqual(pr.vendor, self.vendor)
        self.assertIn('vendor', pr.saved_with['update_fields'])
        self.assertEqual(purchase_orders.create.call_args.kwargs['vendor'], self.vendor)

    @patch('apps.procurement.services.requisition_conversion.PurchaseOrder.objects')
    def test_positive_total_is_required(self, purchase_orders):
        pr = self._pr(total_price=Decimal('0'), estimated_budget=None)

        with self.assertRaisesMessage(ValidationError, 'positive requisition total'):
            RequisitionConversionService._convert_locked(pr, self.actor)

        purchase_orders.create.assert_not_called()

    @patch('apps.procurement.services.requisition_conversion.PurchaseOrder.objects')
    def test_po_creation_failure_does_not_mutate_pr(self, purchase_orders):
        purchase_orders.filter.return_value.exists.return_value = False
        purchase_orders.create.side_effect = RuntimeError('database failure')
        pr = self._pr()

        with self.assertRaisesMessage(RuntimeError, 'database failure'):
            RequisitionConversionService._convert_locked(pr, self.actor)

        self.assertEqual(pr.status, 'approved')
        self.assertEqual(pr.po_number_reference, '')
        self.assertFalse(hasattr(pr, 'saved_with'))
