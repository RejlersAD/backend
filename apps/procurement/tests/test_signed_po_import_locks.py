"""Import discovery must be rechecked after PR-before-PO locking."""

from unittest.mock import patch

from django.db.models.query import QuerySet
from django.test import TestCase

from apps.procurement.models import PODocument, PurchaseOrder, PurchaseRequisition, Vendor
from apps.procurement.services.procurement_lifecycle import ProcurementDeleteConflict
from apps.procurement.services.signed_po_pdf_import import _lock_import_relationships


class SignedPOImportRelationshipLockTests(TestCase):
    def setUp(self):
        self.number = 'RAD-PRJ-PUR-0681_2026'
        self.pr = PurchaseRequisition.objects.create(
            pr_number='RAD-PRJ-PR-0681_2026', po_number_reference=self.number,
        )
        self.vendor = Vendor.objects.create(vendor_code='LOCKS', name='Lock tests supplier')

    def order(self):
        return PurchaseOrder.objects.create(
            po_number=self.number, pr_reference=self.pr, vendor=self.vendor,
            title='Import lock test', total_amount='100',
        )

    def test_new_order_resolves_the_unique_source_recommendation(self):
        order, pr = _lock_import_relationships(self.number, self.number)
        self.assertIsNone(order)
        self.assertEqual(pr.pk, self.pr.pk)

    def test_stale_order_relationship_aborts_without_storing_source(self):
        order = self.order()
        other = PurchaseRequisition.objects.create(pr_number='RAD-PRJ-PR-0682_2026')
        original = QuerySet.select_for_update

        def changed_link(queryset, *args, **kwargs):
            if queryset.model is PurchaseOrder:
                PurchaseOrder.objects.filter(pk=order.pk).update(pr_reference=other)
            return original(queryset, *args, **kwargs)

        with patch.object(QuerySet, 'select_for_update', new=changed_link):
            with self.assertRaisesMessage(ProcurementDeleteConflict, 'linked recommendation changed'):
                _lock_import_relationships(self.number, self.number)
        self.assertFalse(PODocument.objects.exists())

    def test_changed_implicit_source_match_aborts_instead_of_guessing(self):
        original = QuerySet.select_for_update

        def changed_reference(queryset, *args, **kwargs):
            if queryset.model is PurchaseRequisition:
                PurchaseRequisition.objects.filter(pk=self.pr.pk).update(po_number_reference='different-order')
            return original(queryset, *args, **kwargs)

        with patch.object(QuerySet, 'select_for_update', new=changed_reference):
            with self.assertRaisesMessage(ProcurementDeleteConflict, 'matching recommendations changed'):
                _lock_import_relationships(self.number, self.number)
        self.assertFalse(PurchaseOrder.objects.exists())

    def test_explicit_origin_does_not_acquire_incompatible_recommendation_lock(self):
        self.order()
        other = PurchaseRequisition.objects.create(pr_number='RAD-PRJ-PR-0682_2026')
        with patch.object(QuerySet, 'select_for_update') as lock:
            with self.assertRaisesMessage(ProcurementDeleteConflict, 'already linked to another'):
                _lock_import_relationships(self.number, self.number, originating_pr=other)
        lock.assert_not_called()
