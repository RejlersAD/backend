"""Real guarded invoice/PO handoff routes with synthetic records only."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.core.project_models import Project
from apps.procurement.models import PurchaseOrder, Receipt, Vendor
from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, RolePermission, UserProfile, UserRole
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints
from .models import Invoice, InvoiceLineItem, InvoicePurchaseOrderAllocation
from .views import InvoiceViewSet

router = DefaultRouter()
router.register('invoices', InvoiceViewSet, basename='invoice')
urlpatterns = [path('api/v1/finance/', include(router.urls))]
secure_module_endpoints(urlpatterns)
BASE = '/api/v1/finance/invoices/'


@override_settings(ROOT_URLCONF=__name__)
class PurchaseOrderHandoffTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        for target in ('requests.sessions.Session.request', 'httpx.Client.send'):
            blocker = patch(target, side_effect=AssertionError('No external HTTP in handoff tests'))
            mocked = blocker.start()
            self.addCleanup(blocker.stop)
            self.addCleanup(mocked.assert_not_called)
        self.org = Organization.objects.create(code='HANDOFF-TEST', name='Synthetic handoff')
        self.modules = {}
        for code in ('finance_incoming', 'procurement_orders'):
            module, _ = Module.objects.get_or_create(code=code, defaults={'name': code})
            ensure_module_actions(Module, Permission, module_ids=[module.pk])
            self.modules[code] = module
        self.user = self.actor('operator', {'finance_incoming': ('read', 'create', 'update'), 'procurement_orders': ('read',)})
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.vendor = Vendor.objects.create(vendor_code='HANDOFF-VENDOR', name='Synthetic supplier')
        self.project = Project.objects.create(code='HANDOFF-PROJECT', name='Synthetic project')
        self.order = self.po('OLDER-PO')
        self.invoice = self.make_invoice('HANDOFF-INV')

    def actor(self, name, grants):
        user = get_user_model().objects.create_user(f'handoff-{name}', email=f'{name}@handoff.example.test')
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': self.org, 'status': 'active'})
        role = Role.objects.create(code=f'handoff_{name}', name=name, level=5)
        UserRole.objects.create(user_profile=profile, role=role)
        for code, actions in grants.items():
            RoleModule.objects.create(role=role, module=self.modules[code])
            for permission in self.modules[code].permissions.filter(action__in=actions):
                RolePermission.objects.create(role=role, permission=permission)
        return user

    def po(self, number, **extra):
        values = dict(po_number=number, title='Synthetic equipment', vendor=self.vendor,
                      enterprise_project=self.project, category='other', total_amount=Decimal('100.00'),
                      currency='AED', status='completed',
                      items=[{'item_no': '1', 'description': 'Synthetic equipment', 'quantity': '1'}],
                      approval_log=[{'status': 'approved', 'approver': 'Synthetic historical approver'}])
        return PurchaseOrder.objects.create(**{**values, **extra})

    def make_invoice(self, number, **extra):
        values = dict(invoice_number=number, vendor=self.vendor, vendor_name=self.vendor.name,
                      currency='AED', total_amount=Decimal('100.00'), submitted_by=self.user,
                      original_filename='synthetic.pdf', file_path='', procurement_status='ready_for_matching')
        return Invoice.objects.create(**{**values, **extra})

    def allocate(self, **changes):
        self.invoice.refresh_from_db()
        payload = dict(purchase_order_id=str(self.order.pk), allocated_amount='60.00',
                       confirm_po_match=True, expected_updated_at=self.invoice.updated_at.isoformat(), reason='Reviewed supplier order')
        return self.client.post(f'{BASE}{self.invoice.pk}/allocate-purchase-order/', {**payload, **changes}, format='json')

    def test_queue_includes_completed_and_partial_allocations_ignores_stale_legacy_status(self):
        self.order.invoice_status = 'fully_invoiced'
        self.order.total_invoiced_amount = Decimal('100')
        self.order.save(update_fields=['invoice_status', 'total_invoiced_amount'])
        InvoicePurchaseOrderAllocation.objects.create(invoice=self.invoice, purchase_order=self.order,
                                                     allocated_amount=Decimal('30'), currency='AED')
        response = self.client.get(BASE + 'awaiting-purchase-orders/')
        self.assertEqual(response.status_code, 200, response.data)
        row = response.data['results'][0]
        self.assertEqual((row['allocated_amount'], row['remaining_amount']), ('30.00', '70.00'))
        self.assertEqual(row['status'], 'completed')
        self.assertIn('receiving', row)
        self.assertTrue(row['can_import_invoice'])
        self.assertEqual(Invoice.objects.count(), 1)

    def test_fully_allocated_draft_cancelled_and_unapproved_not_in_queue(self):
        InvoicePurchaseOrderAllocation.objects.create(invoice=self.invoice, purchase_order=self.order,
                                                     allocated_amount=Decimal('100'), currency='AED')
        self.po('DRAFT', status='draft')
        self.po('CANCELLED', status='cancelled')
        self.po('UNAPPROVED', approval_log=[{'status': 'pending', 'approver': 'Synthetic'}])
        response = self.client.get(BASE + 'awaiting-purchase-orders/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['count'], 0)
        option = self.client.get(BASE + 'purchase-order-options/', {'id': str(self.order.pk)}).data['results'][0]
        self.assertFalse(option['can_import_invoice'])
        from .services.vendor_invoice_import import VendorInvoiceImportService
        self.assertEqual(VendorInvoiceImportService()._suggest_purchase_orders(
            {'po_reference_text': self.order.po_number}, [], user=self.user), [])

    def test_search_finds_po_older_than_500_and_paginates(self):
        template = dict(vendor=self.vendor, title='Newer synthetic', category='other', total_amount=Decimal('1'),
                        currency='AED', status='sent', approval_log=self.order.approval_log)
        PurchaseOrder.objects.bulk_create([PurchaseOrder(po_number=f'NEWER-{index:04}', **template) for index in range(501)])
        response = self.client.get(BASE + 'purchase-order-options/', {'search': 'OLDER-PO'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row['id'] for row in response.data['results']], [str(self.order.pk)])
        page = self.client.get(BASE + 'purchase-order-options/', {'page': 2, 'page_size': 3})
        self.assertEqual(page.data['count'], 502)
        self.assertEqual(len(page.data['results']), 3)
        self.assertIsNotNone(page.data['next'])
        self.assertIsNotNone(page.data['previous'])

    def test_finance_access_alone_cannot_disclose_orders(self):
        actor = self.actor('finance_only', {'finance_incoming': ('read', 'update')})
        self.client.force_authenticate(actor)
        for action in ('awaiting-purchase-orders/', 'purchase-order-options/'):
            response = self.client.get(BASE + action)
            self.assertEqual(response.status_code, 403)
        from .services.vendor_invoice_import import VendorInvoiceImportService
        self.assertEqual(VendorInvoiceImportService()._suggest_purchase_orders({}, [], user=actor), [])

    def test_order_read_without_finance_read_is_denied(self):
        actor = self.actor('po_only', {'procurement_orders': ('read',)})
        self.client.force_authenticate(actor)
        self.assertEqual(self.client.get(BASE + 'purchase-order-options/').status_code, 403)

    def test_link_is_explicit_preserves_captured_reference_and_is_searchable(self):
        response = self.allocate()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['invoice']['po_reference_text'], '')
        self.assertEqual(response.data['invoice']['confirmed_po_references'],
                         [{'id': str(self.order.pk), 'po_number': self.order.po_number}])
        self.assertIn('missing_accepted_receipt', response.data['exception_codes'])
        found = self.client.get(BASE, {'search': 'OLDER-PO'})
        rows = found.data['results'] if isinstance(found.data, dict) else found.data
        self.assertEqual([row['id'] for row in rows], [self.invoice.pk])
        self.assertEqual(rows[0]['confirmed_po_references'][0]['po_number'], 'OLDER-PO')

    def test_duplicate_stale_and_missing_confirmation_do_not_allocate_twice(self):
        token = self.invoice.updated_at.isoformat()
        self.assertEqual(self.allocate(confirm_po_match=False).status_code, 400)
        self.assertEqual(self.allocate().status_code, 201)
        self.assertEqual(self.allocate(expected_updated_at=token).status_code, 409)
        self.assertEqual(self.allocate().status_code, 400)
        self.assertEqual(InvoicePurchaseOrderAllocation.objects.count(), 1)

    def test_invalid_amount_vendor_currency_and_po_state_leave_no_effects(self):
        for amount in ('NaN', 'Infinity', '-1', '0', '101.123', '106'):
            with self.subTest(amount=amount):
                self.assertEqual(self.allocate(allocated_amount=amount).status_code, 400)
        alternate = Vendor.objects.create(vendor_code='HANDOFF-OTHER', name='Other synthetic supplier')
        other = self.po('OTHER-VENDOR', vendor=alternate)
        self.assertEqual(self.allocate(purchase_order_id=str(other.pk)).status_code, 400)
        other.currency = 'USD'
        other.vendor = self.vendor
        other.save(update_fields=['currency', 'vendor'])
        self.assertEqual(self.allocate(purchase_order_id=str(other.pk)).status_code, 400)
        self.order.status = 'draft'
        self.order.save(update_fields=['status'])
        self.assertEqual(self.allocate().status_code, 400)
        self.assertEqual(InvoicePurchaseOrderAllocation.objects.count(), 0)

    def test_remaining_invoice_and_po_balances_rechecked(self):
        another = self.make_invoice('OTHER-INVOICE')
        InvoicePurchaseOrderAllocation.objects.create(invoice=another, purchase_order=self.order,
                                                     allocated_amount=Decimal('80'), currency='AED')
        self.assertEqual(self.allocate(allocated_amount='30').status_code, 400)
        other_po = self.po('OTHER-PO')
        InvoicePurchaseOrderAllocation.objects.create(invoice=self.invoice, purchase_order=other_po,
                                                     allocated_amount=Decimal('90'), currency='AED')
        self.assertEqual(self.allocate(allocated_amount='15').status_code, 400)
        self.assertEqual(InvoicePurchaseOrderAllocation.objects.count(), 2)

    def test_read_only_actor_cannot_link(self):
        actor = self.actor('reader', {'finance_incoming': ('read',), 'procurement_orders': ('read',)})
        self.client.force_authenticate(actor)
        self.assertEqual(self.allocate().status_code, 403)
        self.assertEqual(InvoicePurchaseOrderAllocation.objects.count(), 0)

    def test_approved_invoice_cannot_be_reallocated(self):
        self.invoice.status = 'approved'
        self.invoice.save(update_fields=['status'])
        self.assertEqual(self.allocate().status_code, 400)

    def test_inconsistent_legacy_currency_is_flagged_without_inventing_balance(self):
        other = self.make_invoice('FOREIGN-CURRENCY', currency='USD')
        InvoicePurchaseOrderAllocation.objects.create(invoice=other, purchase_order=self.order,
                                                     allocated_amount=Decimal('20'), currency='USD')
        response = self.client.get(BASE + 'awaiting-purchase-orders/')
        row = response.data['results'][0]
        self.assertIsNone(row['remaining_amount'])
        self.assertTrue(row['allocation_issue'])
        self.assertFalse(row['can_import_invoice'])
        self.assertEqual(self.allocate().status_code, 400)

    def test_wrong_supplier_fully_allocated_po_stays_visible_for_review(self):
        vendor = Vendor.objects.create(vendor_code='WRONG-SUPPLIER', name='Other supplier')
        other = self.make_invoice('WRONG-SUPPLIER-INV', vendor=vendor)
        InvoicePurchaseOrderAllocation.objects.create(invoice=other, purchase_order=self.order,
                                                     allocated_amount=Decimal('100'), currency='AED')
        response = self.client.get(BASE + 'awaiting-purchase-orders/')
        self.assertEqual(response.data['count'], 1)
        self.assertTrue(response.data['results'][0]['allocation_issue'])
        self.assertIsNone(response.data['results'][0]['remaining_amount'])

    def test_canonical_receipt_identity_maps_to_original_po_reference(self):
        self.order.items = [{'item_no': 'A-900', 'description': 'Synthetic part', 'quantity': '2', 'unit': 'ea'}]
        self.order.save(update_fields=['items'])
        Receipt.objects.create(receipt_number='CANONICAL-GRN', purchase_order=self.order, status='accepted',
                               items_received=[{'line_id': 'line:1', 'line_number': 1, 'basis': 'quantity', 'accepted_qty': '2'}])
        InvoiceLineItem.objects.create(invoice=self.invoice, line_number=1, po_item_reference='A-900', quantity=Decimal('2'))
        response = self.allocate()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['match_status'], 'verified')

    def test_service_values_require_review_and_remain_net_evidence(self):
        self.order.items = []
        self.order.save(update_fields=['items'])
        Receipt.objects.create(receipt_number='SERVICE-GRN', purchase_order=self.order, status='accepted',
                               items_received=[{'line_id': 'service:total', 'line_number': 1, 'basis': 'service_value',
                                                'accepted_amount': '20.00', 'uom': 'AED'}])
        response = self.allocate()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['match_status'], 'exception')
        self.assertIn('service_value_match_requires_review', response.data['exception_codes'])
        evidence = self.invoice.po_allocations.get().match_evidence['service_acceptance'][0]
        self.assertEqual(evidence['accepted_amount'], '20.00')
        self.assertEqual(evidence['value_basis'], 'net_excluding_vat')

    def test_explicit_po_line_number_receipt_maps_to_supplier_reference(self):
        self.order.items = [{'line_number': 10, 'item_no': 'A-900', 'description': 'Synthetic part',
                             'quantity': '2', 'unit': 'ea'}]
        self.order.save(update_fields=['items'])
        Receipt.objects.create(receipt_number='EXPLICIT-LINE-GRN', purchase_order=self.order, status='accepted',
                               items_received=[{'line_id': 'line:10', 'line_number': 10, 'po_item_reference': 'A-900',
                                                'basis': 'quantity', 'accepted_qty': '2'}])
        InvoiceLineItem.objects.create(invoice=self.invoice, line_number=1, po_item_reference='A-900', quantity=Decimal('2'))
        response = self.allocate()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['match_status'], 'verified')
        self.assertEqual(self.invoice.po_allocations.get().match_evidence['line_checks'][0]['accepted_quantity'], '2')

    def test_reordered_po_numbers_do_not_transfer_receipt_coverage(self):
        self.order.items = [
            {'line_number': 2, 'item_no': 'PART-A', 'quantity': '2', 'unit': 'ea'},
            {'line_number': 1, 'item_no': 'PART-B', 'quantity': '2', 'unit': 'ea'},
        ]
        self.order.save(update_fields=['items'])
        Receipt.objects.create(receipt_number='REORDERED-GRN', purchase_order=self.order, status='accepted',
                               items_received=[{'line_id': 'line:2', 'line_number': 2, 'po_item_reference': 'PART-A',
                                                'basis': 'quantity', 'accepted_qty': '2'}])
        InvoiceLineItem.objects.create(invoice=self.invoice, line_number=1, po_item_reference='PART-B', quantity=Decimal('2'))
        response = self.allocate()
        self.assertEqual(response.data['match_status'], 'exception')
        self.assertEqual(self.invoice.po_allocations.get().match_evidence['line_checks'][0]['accepted_quantity'], '0')

    def test_ambiguous_po_aliases_cannot_verify_an_invoice_line(self):
        self.order.items = [
            {'line_number': 1, 'item_no': 'A-900', 'quantity': '2', 'unit': 'ea'},
            {'line_number': 2, 'item_no': 'A900', 'quantity': '2', 'unit': 'ea'},
        ]
        self.order.save(update_fields=['items'])
        Receipt.objects.create(receipt_number='AMBIGUOUS-GRN', purchase_order=self.order, status='accepted',
                               items_received=[{'line_id': 'line:1', 'basis': 'quantity', 'accepted_qty': '2'}])
        InvoiceLineItem.objects.create(invoice=self.invoice, line_number=1, po_item_reference='A900', quantity=Decimal('2'))
        response = self.allocate()
        self.assertEqual(response.data['match_status'], 'exception')
        self.assertIn('invoice_po_line_mismatch', response.data['exception_codes'])

    def test_header_only_invoice_cannot_verify_on_partial_goods_receipt(self):
        Receipt.objects.create(receipt_number='HEADER-GRN', purchase_order=self.order, status='partial',
                               items_received=[{'item_no': '1', 'accepted_qty': '0.5'}])
        response = self.allocate()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['match_status'], 'exception')
        self.assertIn('invoice_line_match_requires_review', response.data['exception_codes'])

    def test_recheck_cancelled_or_approval_invalid_po_removes_verified_match(self):
        Receipt.objects.create(receipt_number='RECHECK-GRN', purchase_order=self.order, status='accepted',
                               items_received=[{'item_no': '1', 'accepted_qty': '1'}])
        InvoiceLineItem.objects.create(invoice=self.invoice, line_number=1, po_item_reference='1', quantity=Decimal('1'))
        self.assertEqual(self.allocate().data['match_status'], 'verified')
        for changes in ({'status': 'cancelled'}, {'status': 'sent', 'approval_log': [{'status': 'pending', 'approver': 'Synthetic'}]}):
            with self.subTest(changes=changes):
                PurchaseOrder.objects.filter(pk=self.order.pk).update(**changes)
                response = self.client.post(f'{BASE}{self.invoice.pk}/run-three-way-match/', {}, format='json')
                self.assertEqual(response.status_code, 200, response.data)
                allocation = response.data['invoice']['po_allocations'][0]
                self.assertEqual(allocation['match_status'], 'exception')
                self.assertIn('purchase_order_approval_requires_review', allocation['exception_codes'])

    def test_duplicate_invoice_references_share_one_received_quantity_balance(self):
        self.order.items = [{'item_no': 'A-900', 'quantity': '10', 'unit': 'ea'}]
        self.order.save(update_fields=['items'])
        Receipt.objects.create(receipt_number='DUPLICATE-LINES-GRN', purchase_order=self.order, status='accepted',
                               items_received=[{'line_id': 'line:1', 'basis': 'quantity', 'accepted_qty': '10'}])
        for number in (1, 2):
            InvoiceLineItem.objects.create(invoice=self.invoice, line_number=number, po_item_reference='A-900', quantity=Decimal('6'))
        response = self.allocate()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['match_status'], 'exception')
        self.assertIn('invoice_quantity_exceeds_receipt', response.data['exception_codes'])
        evidence = self.invoice.po_allocations.get().match_evidence
        self.assertEqual([line['invoice_po_line_quantity'] for line in evidence['line_checks']], ['12.0000', '12.0000'])
        self.assertEqual(evidence['quantity_scope'], 'this_invoice_only')
        self.assertIn('receipt_quantities_not_allocated_between_invoices', evidence['limitations'])

    def test_duplicate_invoice_references_can_share_sufficient_coverage(self):
        self.order.items = [{'item_no': 'A-900', 'quantity': '10', 'unit': 'ea'}]
        self.order.save(update_fields=['items'])
        Receipt.objects.create(receipt_number='DUPLICATE-VALID-GRN', purchase_order=self.order, status='accepted',
                               items_received=[{'line_id': 'line:1', 'basis': 'quantity', 'accepted_qty': '10'}])
        for number in (1, 2):
            InvoiceLineItem.objects.create(invoice=self.invoice, line_number=number, po_item_reference='A-900', quantity=Decimal('5'))
        response = self.allocate()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['match_status'], 'verified')

    def test_invoice_line_without_quantity_cannot_claim_received_coverage(self):
        Receipt.objects.create(receipt_number='NO-QTY-GRN', purchase_order=self.order, status='accepted',
                               items_received=[{'item_no': '1', 'accepted_qty': '1'}])
        InvoiceLineItem.objects.create(invoice=self.invoice, line_number=1, description='No quantity recorded')
        response = self.allocate()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['match_status'], 'exception')
        self.assertIn('invoice_line_match_requires_review', response.data['exception_codes'])

    def test_detail_capabilities_match_grants_and_open_state(self):
        data = self.client.get(f'{BASE}{self.invoice.pk}/').data
        self.assertTrue(data['capabilities']['can_allocate_purchase_order'])
        self.assertFalse(data['capabilities']['can_recheck_match'])
        self.invoice.status = 'approved'
        self.invoice.save(update_fields=['status'])
        data = self.client.get(f'{BASE}{self.invoice.pk}/').data
        self.assertFalse(data['capabilities']['can_allocate_purchase_order'])

    def test_explicit_source_read_denial_overrides_existing_grant(self):
        from apps.rbac.models import UserPermissionOverride
        permission = self.modules['procurement_orders'].permissions.filter(action='read').first()
        UserPermissionOverride.objects.create(user_profile=self.user.rbac_profile, permission=permission, allowed=False)
        cache.clear()
        self.assertEqual(self.client.get(BASE + 'purchase-order-options/').status_code, 403)
        self.assertEqual(self.allocate().status_code, 403)
        self.assertEqual(InvoicePurchaseOrderAllocation.objects.count(), 0)
