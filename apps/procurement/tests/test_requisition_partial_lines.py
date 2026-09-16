"""Partial recommendation lines preserve header amounts through guarded APIs."""

from decimal import Decimal

import fitz
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import resolve

from apps.procurement.models import PurchaseRequisition
from apps.procurement.services.procurement_vat import apply_confirmed_input
from apps.procurement.tests import test_procurement_crud_lifecycle as lifecycle
from apps.rbac.models import Module, RolePermission
from apps.rbac.route_guard import ModuleActionGuardMixin


class PartialLineVATCalculationTests(SimpleTestCase):
    def test_pr_partial_quote_does_not_replace_confirmed_header(self):
        values = {
            'vat_basis': 'exclusive', 'net_total_excl_vat': Decimal('100'),
            'total_price': Decimal('105'), 'price_remarks_data': {'discount_amount': '10'},
            'items': [{'quantity': '', 'unit_price': '', 'total': '12.00'}],
        }
        result = apply_confirmed_input(values, None, 'pr')
        self.assertEqual((result['net_total_excl_vat'], result['total_price']), (Decimal('100'), Decimal('105')))
        self.assertEqual(result['items'][0], {'quantity': '', 'unit_price': '', 'total': '12.00'})

    def test_po_partial_quote_keeps_its_existing_subtotal_behavior(self):
        result = apply_confirmed_input({
            'vat_basis': 'exclusive', 'net_amount': Decimal('100'), 'total_amount': Decimal('105'),
            'items': [{'quantity': '', 'unit_price': '', 'total': '12.00'}],
        }, None, 'po')
        self.assertEqual((result['net_amount'], result['total_amount']), (Decimal('12'), Decimal('12.60')))


@override_settings(ROOT_URLCONF=lifecycle.__name__, TEAMS_APPROVAL_WEBHOOK_URL='', WEB_PUSH_VAPID_PRIVATE_KEY='')
class RequisitionPartialLinesAPITests(TestCase):
    setUp = lifecycle.ProcurementCRUDLifecycleTests.setUp

    def create_requisition(self, items, **overrides):
        payload = {
            'pr_number': f'RAD-GEN-PR-{PurchaseRequisition.objects.count() + 1:04d}_2026',
            'requisition_type': 'general', 'po_applicable': True,
            'title': 'Partial line recommendation', 'items': items,
            'total_price': '100.00', 'net_total_excl_vat': '95.00',
            'approval_workflow_config': [{
                'role': 'Level 1 Approver', 'stage': 'Level 1 Approver 1',
                'level': 1, 'user_id': str(self.user.pk),
            }],
        }
        payload.update(overrides)
        url = '/api/v1/procurement/requisitions/'
        self.assertTrue(issubclass(resolve(url).func.cls, ModuleActionGuardMixin))
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return PurchaseRequisition.objects.get(pk=response.data['id'])

    def reload_save_submit(self, pr):
        url = f'/api/v1/procurement/requisitions/{pr.pk}/'
        before = (pr.total_price, pr.net_total_excl_vat, pr.items)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.data)
        values = {field: response.data[field] for field in (
            'items', 'total_price', 'net_total_excl_vat', 'vat_basis', 'price_remarks_data',
        )}
        values['title'] = 'Saved partial line recommendation'
        response = self.client.patch(url, values, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        response = self.client.post(url + 'submit/', {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        pr.refresh_from_db()
        self.assertEqual(pr.status, 'submitted')
        self.assertEqual((pr.total_price, pr.net_total_excl_vat, pr.items), before)
        return pr

    def test_unconfirmed_partial_lines_reload_save_and_submit_without_changing_header(self):
        pr = self.create_requisition([
            {'description': '', 'quantity': '2', 'unit_price': '', 'total': ''},
            {'description': 'Scope pending', 'quantity': '', 'unit_price': '25'},
        ])
        self.assertEqual(pr.items[0]['description'], '')
        self.assertEqual(pr.items[0]['unit_price'], '')
        self.assertEqual(pr.items[1]['quantity'], '')
        self.assertEqual(pr.items[1]['total'], '')
        self.reload_save_submit(pr)

    def test_confirmed_partial_quotes_keep_each_header_basis_and_discount(self):
        for basis, net, gross in (
            ('exclusive', '100.00', '105.00'),
            ('inclusive', '95.24', '100.00'),
            ('none', '100.00', '100.00'),
        ):
            with self.subTest(basis=basis):
                pr = self.create_requisition([
                    {'description': '', 'quantity': '', 'unit_price': '', 'total': '12.00'},
                ], vat_basis=basis, net_total_excl_vat=net, total_price=gross,
                   price_remarks_data={'discount_amount': '10.00'})
                self.assertEqual((pr.net_total_excl_vat, pr.total_price), (Decimal(net), Decimal(gross)))
                self.assertEqual(pr.items[0]['total'], '12.00')
                self.assertEqual(pr.items[0]['quantity'], '')
                self.assertEqual(pr.items[0]['unit_price'], '')
                self.reload_save_submit(pr)

    def test_zero_quantity_is_preserved_and_can_submit(self):
        pr = self.create_requisition([
            {'description': '', 'quantity': 0, 'unit_price': '25', 'total': '0.00'},
        ], total_price='0.00', net_total_excl_vat='0.00')
        self.assertEqual(pr.items[0]['quantity'], '0')
        self.assertEqual(pr.items[0]['total'], '0.00')
        self.reload_save_submit(pr)

    def test_metadata_only_partial_row_survives_reload_save_and_submit(self):
        line_details = [{'vendor_id': str(self.vendor.pk), 'budget': '250.00'}]
        pr = self.create_requisition([
            {'description': '', 'quantity': '', 'unit_price': '', 'total': ''},
        ], price_remarks_data={'line_details': line_details})
        self.assertEqual(len(pr.items), 1)
        self.assertEqual(pr.items[0]['description'], '')
        self.assertEqual(pr.items[0]['quantity'], '')
        self.assertEqual(pr.items[0]['unit_price'], '')
        self.assertEqual(pr.items[0]['total'], '')
        self.reload_save_submit(pr)
        self.assertEqual(pr.price_remarks_data['line_details'], line_details)

    def test_partial_line_edit_uses_recorded_header_without_discounting_it_again(self):
        pr = self.create_requisition([
            {'description': 'Quote', 'quantity': '', 'unit_price': '', 'total': '12.00'},
        ], vat_basis='exclusive', net_total_excl_vat='100.00', total_price='105.00',
           price_remarks_data={'discount_amount': '10.00'})
        response = self.client.patch(f'/api/v1/procurement/requisitions/{pr.pk}/', {
            'vat_basis': 'exclusive',
            'items': [{'description': 'Updated quote', 'quantity': '', 'unit_price': '', 'total': '13.00'}],
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        pr.refresh_from_db()
        self.assertEqual((pr.net_total_excl_vat, pr.total_price), (Decimal('100'), Decimal('105')))
        self.assertEqual(pr.items[0]['total'], '13.00')
        self.reload_save_submit(pr)

    def test_explicit_entered_amount_takes_priority_over_partial_quotes(self):
        pr = self.create_requisition([
            {'description': 'Quote', 'quantity': '', 'unit_price': '', 'total': '12.00'},
        ], vat_basis='exclusive', entered_amount='200.00')
        self.assertEqual((pr.net_total_excl_vat, pr.total_price), (Decimal('200'), Decimal('210')))
        self.assertEqual(pr.items[0]['total'], '12.00')
        self.reload_save_submit(pr)

    def test_wrong_basis_header_edit_is_not_silently_replaced_with_saved_money(self):
        pr = self.create_requisition([
            {'description': 'Quote', 'quantity': '', 'unit_price': '', 'total': '12.00'},
        ], vat_basis='exclusive', net_total_excl_vat='100.00', total_price='105.00')
        response = self.client.patch(f'/api/v1/procurement/requisitions/{pr.pk}/', {
            'vat_basis': 'exclusive', 'total_price': '200.00',
        }, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('vat_basis', response.data)
        pr.refresh_from_db()
        self.assertEqual((pr.net_total_excl_vat, pr.total_price), (Decimal('100'), Decimal('105')))

    def test_legacy_confirmed_headers_survive_partial_edits_without_recalculation(self):
        pr = self.create_requisition([
            {'description': 'Quote', 'quantity': '', 'unit_price': '', 'total': '12.00'},
        ], vat_basis='exclusive', net_total_excl_vat='100.00', total_price='105.00')
        PurchaseRequisition.objects.filter(pk=pr.pk).update(total_price=Decimal('112.37'))
        for index, headers in enumerate(({}, {'total_price': '112.37', 'net_total_excl_vat': '100.00'})):
            with self.subTest(headers=headers):
                response = self.client.patch(f'/api/v1/procurement/requisitions/{pr.pk}/', {
                    'vat_basis': 'exclusive', **headers,
                    'items': [{'description': 'Updated quote', 'quantity': '', 'unit_price': '', 'total': str(13 + index)}],
                }, format='json')
                self.assertEqual(response.status_code, 200, response.data)
                pr.refresh_from_db()
                self.assertEqual((pr.net_total_excl_vat, pr.total_price), (Decimal('100'), Decimal('112.37')))
        self.reload_save_submit(pr)

    def test_approved_partial_lines_export_as_pdf_without_changing_recorded_values(self):
        pr = self.reload_save_submit(self.create_requisition([
            {'description': 'Pending price detail', 'quantity': '', 'unit_price': '', 'total': ''},
        ]))
        profile = self.user.rbac_profile
        profile.signature_image = 'saved-test-signature'
        profile.save(update_fields=['signature_image'])
        url = f'/api/v1/procurement/requisitions/{pr.pk}/'
        response = self.client.post(url + 'process_dynamic_approval/', {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        module = Module.objects.get(code='procurement_requisitions')
        for permission in module.permissions.filter(action='export', is_active=True):
            RolePermission.objects.get_or_create(role=self.role, permission=permission)
        before = (pr.items, pr.total_price, pr.net_total_excl_vat)
        response = self.client.get(url + 'export_pdf/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        with fitz.open(stream=response.content, filetype='pdf') as document:
            text = '\n'.join(page.get_text() for page in document)
        self.assertIn('Pending price detail', text)
        self.assertIn('100.00', text)
        pr.refresh_from_db()
        self.assertEqual((pr.items, pr.total_price, pr.net_total_excl_vat), before)
