"""Explicit VAT choices preserve entered totals and round discount lines once."""
from decimal import Decimal

from copy import deepcopy
from unittest.mock import patch
from django.test import SimpleTestCase, TestCase, override_settings

from apps.procurement.services.procurement_vat import confirmed_totals, items_subtotal
from apps.procurement.models import PurchaseOrder, PurchaseRequisition
from apps.procurement.tests import test_procurement_crud_lifecycle as lifecycle
from apps.procurement.tests import test_po_document_reconciliation as reconciliation
from apps.procurement.tests import test_signed_pr_pdf_creation as signed_pr


class ConfirmedVATCalculationTests(SimpleTestCase):
    def test_user_choices_have_distinct_amounts(self):
        for basis, net, tax, total, rate in (
            ('exclusive', '100.00', '5.00', '105.00', '5.00'),
            ('inclusive', '95.24', '4.76', '100.00', '5.00'),
            ('none', '100.00', '0.00', '100.00', '0.00'),
        ):
            with self.subTest(basis=basis):
                self.assertEqual(confirmed_totals('100', basis), {
                    'net_amount': Decimal(net), 'tax_amount': Decimal(tax),
                    'total_amount': Decimal(total), 'vat_percentage': Decimal(rate),
                })

    def test_inclusive_price_is_never_increased_by_rounding(self):
        totals = confirmed_totals('0.10', 'inclusive')
        self.assertEqual(totals['total_amount'], Decimal('0.10'))
        self.assertEqual(totals['net_amount'] + totals['tax_amount'], Decimal('0.10'))

    def test_discount_uses_confirmed_input_basis_before_vat(self):
        inclusive = confirmed_totals('100', 'inclusive', '10')
        exclusive = confirmed_totals('100', 'exclusive', '10')
        self.assertEqual(inclusive['total_amount'], Decimal('90.00'))
        self.assertEqual(inclusive['tax_amount'], Decimal('4.29'))
        self.assertEqual(exclusive['net_amount'], Decimal('90.00'))
        self.assertEqual(exclusive['tax_amount'], Decimal('4.50'))

    def test_each_discounted_line_is_half_up_rounded_before_sum(self):
        rows = [{'quantity': '0.3333', 'unit_price': '3.01', 'discount': '0'}] * 2
        self.assertEqual(items_subtotal(rows), Decimal('2.00'))
        self.assertEqual(items_subtotal([{'quantity': '1', 'unit_price': '2.345', 'discount': '0.01'}]), Decimal('2.34'))

    def test_unconfirmed_or_unknown_choice_never_defaults_to_vat(self):
        for basis in (None, '', 'unconfirmed', 'unknown'):
            with self.subTest(basis=basis), self.assertRaisesRegex(ValueError, 'Confirm whether VAT applies'):
                confirmed_totals('100', basis)


@override_settings(ROOT_URLCONF=lifecycle.__name__)
class ExplicitVATAPITests(TestCase):
    setUp = lifecycle.ProcurementCRUDLifecycleTests.setUp
    order = lifecycle.ProcurementCRUDLifecycleTests.order

    def patch_record(self, kind, record, values):
        return self.client.patch(f'/api/v1/procurement/{kind}/{record.pk}/', values, format='json')

    def test_unconfirmed_zero_fifteen_and_arbitrary_tax_survive_metadata_edits(self):
        for index, (tax, rate) in enumerate((('0', '0'), ('15', '15'), ('8.37', '11.23'))):
            order = self.order(po_number=f'RAD-PRJ-PUR-00{index}1_2026', total_amount='100', tax_amount=tax, vat_percentage=rate)
            original = (order.total_amount, order.tax_amount, order.vat_percentage, order.status, order.attachments)
            response = self.patch_record('orders', order, {'title': 'Edited description'})
            self.assertEqual(response.status_code, 200, response.data)
            order.refresh_from_db()
            self.assertEqual(tuple(Decimal(str(value)) for value in original[:3]), (order.total_amount, order.tax_amount, order.vat_percentage))
            self.assertEqual((order.status, order.attachments), original[3:])
            self.assertEqual(order.vat_basis, 'unconfirmed')
            self.assertIsNone(order.net_amount)

    def test_confirmed_choices_reopen_without_compounding_and_preserve_source(self):
        order = self.order(total_amount='123', tax_amount='17', vat_percentage='15',
                           attachments=[{'type': 'signed_po_pdf', 'source_sha256': 'exact-original'}])
        for basis, net, tax, gross in (('exclusive','100.00','5.00','105.00'), ('inclusive','95.24','4.76','100.00'), ('none','100.00','0.00','100.00')):
            response = self.patch_record('orders', order, {'vat_basis': basis, 'entered_amount': '100.00'})
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(tuple(response.data[key] for key in ('net_amount','tax_amount','total_amount')), (net,tax,gross))
            unchanged = self.patch_record('orders', order, {'title': 'Only title', 'vat_basis': basis})
            self.assertEqual(unchanged.status_code, 200, unchanged.data)
            self.assertEqual(tuple(unchanged.data[key] for key in ('net_amount','tax_amount','total_amount')), (net,tax,gross))
            self.assertEqual(unchanged.data['attachments'][0]['source_sha256'], 'exact-original')

    def test_financial_changes_require_confirmation_even_if_invoice_total_stays_same(self):
        order = self.order(items=[{'description':'Before', 'quantity':2, 'unit_price':50, 'total':100}])
        for values in ({'total_amount':'101'}, {'tax_amount':'5'}, {'currency':'EUR'},
                       {'items':[{'description':'Same amount', 'quantity':1, 'unit_price':100, 'total':100}]}):
            response = self.patch_record('orders', order, values)
            self.assertEqual(response.status_code, 400, response.data)
            self.assertIn('vat_basis', response.data)
        response = self.patch_record('orders', order, {'items':[{'description':'After', 'qty':'2.000', 'price':'50.00', 'line_discount':'', 'line_total':'100.00'}]})
        self.assertEqual(response.status_code, 200, response.data)

    def test_pr_unknown_finances_preserved_for_description_only_then_explicit_inclusive(self):
        self.pr.total_price, self.pr.net_total_excl_vat = Decimal('115'), Decimal('100')
        self.pr.items = [{'description':'Before','quantity':'1','unit_price':'100','total':'100'}]
        self.pr.save()
        response = self.patch_record('requisitions', self.pr, {'items':[{'description':'After','quantity':'1','unit_price':'100','total':'100'}]})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data['total_price'],response.data['net_total_excl_vat']), ('115.00','100.00'))
        response = self.patch_record('requisitions', self.pr, {'vat_basis':'inclusive','entered_amount':'100'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data['total_price'],response.data['net_total_excl_vat']), ('100.00','95.24'))

    def test_model_save_has_no_automatic_financial_recalculation(self):
        order = self.order(total_amount='71.89', tax_amount='3.28', vat_percentage='12.37')
        order.title = 'Background metadata update'
        order.save()
        order.refresh_from_db()
        self.assertEqual((order.total_amount,order.tax_amount,order.vat_percentage), (Decimal('71.89'),Decimal('3.28'),Decimal('12.37')))


@override_settings(ROOT_URLCONF=reconciliation.__name__)
class DocumentVATConfirmationTests(TestCase):
    setUp = reconciliation.PODocumentReconciliationTests.setUp
    reconcile = reconciliation.PODocumentReconciliationTests.reconcile

    def test_confirmed_review_reconciles_without_rewriting_original_ocr_or_pdf(self):
        # Confirmation can repair an unreadable price without changing raw OCR.
        self.fields['total_amount'] = '0.00'
        self.document.extracted_data = deepcopy(self.fields)
        self.document.save(update_fields=['extracted_data'])
        response = self.client.patch(f'/api/v1/procurement/po-documents/{self.document.pk}/',
            {'entered_amount':'100','vat_basis':'inclusive'}, format='json')
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(response.data['extracted_data']['total_amount'],'0.00')
        self.assertEqual(response.data['extracted_data']['tax_amount'],'0.00')
        self.assertEqual(response.data['canonical_financials']['net_amount'],'95.24')
        self.assertEqual(response.data['canonical_financials']['entered_amount'],'100.00')
        result = self.reconcile()
        self.assertEqual(result.status_code,200,result.data)
        order = PurchaseOrder.objects.get(pk=result.data['purchase_order_id'])
        self.assertEqual((order.net_amount,order.tax_amount,order.total_amount), (Decimal('95.24'),Decimal('4.76'),Decimal('100')))
        self.document.refresh_from_db()
        self.assertEqual(self.document.extracted_data['source_extracted_data'], self.fields)
        self.assertEqual(self.document.s3_key,self.key)

    def test_pending_source_financial_edit_requires_choice(self):
        response = self.client.patch(f'/api/v1/procurement/po-documents/{self.document.pk}/',
                                    {'total_amount':'105'}, format='json')
        self.assertEqual(response.status_code,400,response.data)
        self.document.refresh_from_db()
        self.assertEqual(self.document.extracted_data,self.fields)


class SignedPRVATConfirmationTests(TestCase):
    setUp = signed_pr.SignedPRPdfCreationTests.setUp
    _patch = signed_pr.SignedPRPdfCreationTests._patch
    _import = signed_pr.SignedPRPdfCreationTests._import

    def test_source_refresh_preserves_existing_financial_record(self):
        first = self._import()
        pr = PurchaseRequisition.objects.get(pk=first['pr_id'])
        original = (pr.total_price, pr.net_total_excl_vat, pr.currency, pr.items)
        self.fields['net_total'] = Decimal('9000')
        self.reviewed.update(net_total='9000', currency='USD')
        result = self._import(create_new=False)
        pr.refresh_from_db()
        self.assertEqual((pr.total_price,pr.net_total_excl_vat,pr.currency,pr.items), original)
        self.assertTrue(result['financial_values_preserved'])
        self.assertEqual(result['saved_financials']['total_price'],'1250.50')

    def test_reviewed_inclusive_confirmation_and_attach_only_keep_raw_source(self):
        result = self._import(manual_overrides={**self.reviewed,'vat_basis':'inclusive','entered_amount':'100'})
        pr = PurchaseRequisition.objects.get(pk=result['pr_id'])
        self.assertEqual((pr.net_total_excl_vat,pr.total_price),(Decimal('95.24'),Decimal('100')))
        self.assertEqual(pr.price_remarks_data['signed_document_verification']['source_fields']['net_total'],'1250.50')
        source_hash = pr.attachments[0]['sha256']
        self._import(create_new=False, attach_only=True, expected_pr_number=pr.pr_number,
                     manual_overrides={**self.reviewed,'vat_basis':'exclusive','entered_amount':'9000'})
        pr.refresh_from_db()
        self.assertEqual((pr.net_total_excl_vat,pr.total_price),(Decimal('95.24'),Decimal('100')))
        self.assertEqual(pr.attachments[0]['sha256'],source_hash)
