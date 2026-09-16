"""Signed PO uploads may retain unresolved references without inventing records."""

from copy import deepcopy
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.procurement.models import PODocument, PurchaseOrder, PurchaseRequisition, Vendor
from apps.procurement.services.signed_po_pdf_import import import_signed_po_pdf


SERVICE = "apps.procurement.services.signed_po_pdf_import"


class SignedPODeferredImportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="po-upload-reviewer")
        self.vendor = Vendor.objects.create(vendor_code="PO-TEST-01", name="Example Supplier", status="active")
        self.fields = {
            "source_po_number": "RAD-PRJ-PUR-0002_JAN2026",
            "po_number": "RAD-PRJ-PUR-0002_2026",
            "po_date": date(2026, 1, 7), "vendor_name": self.vendor.name,
            "vendor_license_no": "", "seller_reference": "", "quote_ref": "Quote 123",
            "project_number": "5901056", "summary": "Telecom engineering service",
            "payment_terms": "Net 30 days", "payment_mode": "Bank Transfer",
            "delivery_terms": "Services accepted", "expected_delivery": date(2026, 2, 1),
            "total_amount": Decimal("225608.00"), "tax_amount": Decimal("0.00"),
            "gross_amount": Decimal("225608.00"), "currency": "USD", "items": [],
        }
        extractor = patch(f"{SERVICE}.extract_signed_po_fields", side_effect=lambda *args: deepcopy(self.fields))
        extractor.start()
        self.addCleanup(extractor.stop)
        storage = patch(f"{SERVICE}.default_storage")
        self.storage = storage.start()
        self.addCleanup(storage.stop)
        self.storage.save.return_value = "test-only/signed-po.pdf"
        self.storage.url.return_value = "/test-only/signed-po.pdf"

    def upload(self):
        return import_signed_po_pdf(
            b"%PDF-synthetic-deferred-po", filename="signed-po.pdf", user=self.user,
            signature_verified=True, stamp_verified=True,
            approved_by_name="Document Approver", approved_date="2026-01-07",
        )

    def create_pr(self, reference=""):
        return PurchaseRequisition.objects.create(
            pr_number="RAD-PRJ-PR-0002_2026", po_number_reference=reference,
            vendor=self.vendor, supplier_name=self.vendor.name, product_service=self.fields["summary"],
            issued_by=self.user, requested_by=self.user, status="approved",
        )

    def test_unmatched_pr_creates_po_without_fabricating_a_recommendation(self):
        result = self.upload()
        po = PurchaseOrder.objects.get(pk=result["purchase_order_id"])
        self.assertEqual(result["operation"], "created")
        self.assertIsNone(po.pr_reference_id)
        self.assertEqual(po.vendor_id, self.vendor.pk)
        self.assertEqual(po.total_amount, Decimal("225608.00"))
        self.assertTrue(result["reconciliation_required"])
        self.assertTrue(result["reconciliation_issues"])
        self.assertFalse(PurchaseRequisition.objects.exists())
        document = PODocument.objects.get(pk=result["document_id"])
        self.assertEqual(document.confirmed_po_id, po.pk)
        self.assertTrue(document.extracted_data["reconciliation_required"])
        self.assertEqual(po.attachments[0]["source_po_number"], self.fields["source_po_number"])

    def test_unmatched_supplier_stages_source_document_without_creating_vendor_or_po(self):
        self.fields["vendor_name"] = "Unrelated Unregistered Supplier"
        result = self.upload()
        self.assertEqual(result["operation"], "uploaded")
        self.assertIsNone(result["purchase_order_id"])
        self.assertTrue(result["reconciliation_required"])
        self.assertTrue(any("Supplier" in issue for issue in result["reconciliation_issues"]))
        self.assertFalse(PurchaseOrder.objects.exists())
        self.assertEqual(Vendor.objects.count(), 1)
        document = PODocument.objects.get(pk=result["document_id"])
        self.assertIsNone(document.confirmed_po_id)
        self.assertEqual(document.extracted_data["vendor_name"], self.fields["vendor_name"])
        self.assertEqual(result["source_document_url"], self.storage.url.return_value)

    def test_duplicate_staged_pdf_reuses_document_and_storage_object(self):
        self.fields["vendor_name"] = "Unrelated Unregistered Supplier"
        first = self.upload()
        second = self.upload()
        self.assertEqual(first["document_id"], second["document_id"])
        self.assertEqual(PODocument.objects.count(), 1)
        self.assertEqual(self.storage.save.call_count, 1)
        self.assertFalse(PurchaseOrder.objects.exists())

    def test_existing_po_keeps_its_link_when_pr_has_no_text_po_reference(self):
        pr = self.create_pr()
        po = PurchaseOrder.objects.create(
            po_number=self.fields["po_number"], pr_reference=pr, vendor=self.vendor,
            title="Existing order", category="other", total_amount=Decimal("225608.00"),
            created_by=self.user, items=[{"description": "Existing item", "quantity": 1}],
        )
        result = self.upload()
        po.refresh_from_db()
        self.assertEqual(result["operation"], "attached")
        self.assertEqual(po.pr_reference_id, pr.pk)
        self.assertEqual(result["pr_id"], str(pr.pk))
        self.assertFalse(result["reconciliation_required"])
        self.assertEqual(po.items, [{"description": "Existing item", "quantity": 1}])
        self.assertEqual(po.title, 'Existing order')

    def test_matching_pr_and_supplier_keep_normal_creation_and_conversion(self):
        pr = self.create_pr(self.fields["source_po_number"])
        result = self.upload()
        pr.refresh_from_db()
        po = PurchaseOrder.objects.get(pk=result["purchase_order_id"])
        self.assertEqual(result["operation"], "created")
        self.assertEqual(po.pr_reference_id, pr.pk)
        self.assertEqual(po.vendor_id, self.vendor.pk)
        self.assertEqual(pr.status, "converted")
        self.assertEqual(pr.po_number_reference, self.fields["po_number"])
        self.assertFalse(result["reconciliation_required"])
        self.assertEqual(result["reconciliation_issues"], [])
        self.assertEqual(PODocument.objects.get().confirmed_po_id, po.pk)

    def test_zero_tax_source_retains_zero_vat(self):
        result = self.upload()
        po = PurchaseOrder.objects.get(pk=result['purchase_order_id'])
        self.assertEqual(po.vat_percentage, Decimal('0.00'))

    def test_signed_source_attaches_to_completed_order_without_resetting_business_fields(self):
        po = PurchaseOrder.objects.create(
            po_number=self.fields['po_number'], vendor=self.vendor, status='completed',
            title='Completed native order', total_amount=Decimal('98.50'), currency='AED',
            vat_percentage=Decimal('0.00'), approval_log=[{'stage': 'Native approval', 'status': 'Approved'}],
        )
        result = self.upload()
        po.refresh_from_db()
        self.assertEqual(result['operation'], 'attached')
        self.assertTrue(result['reconciliation_required'])
        self.assertEqual(po.status, 'completed')
        self.assertEqual(po.title, 'Completed native order')
        self.assertEqual(po.total_amount, Decimal('98.50'))
        self.assertEqual(po.currency, 'AED')
        self.assertEqual(po.vat_percentage, Decimal('0.00'))
        self.assertEqual(po.approval_log[0], {'stage': 'Native approval', 'status': 'Approved'})
        self.assertEqual(PODocument.objects.get().confirmed_po_id, po.pk)

    def test_duplicate_unverified_upload_keeps_recorded_signature_and_date(self):
        first = self.upload()
        po = PurchaseOrder.objects.get(pk=first['purchase_order_id'])
        signature = po.approval_signature
        second = import_signed_po_pdf(b'%PDF-synthetic-deferred-po', filename='signed-po.pdf', user=self.user)
        po.refresh_from_db()
        self.assertEqual(second['operation'], 'attached')
        self.assertEqual(po.approval_signature, signature)
        self.assertEqual(po.approved_by_name, 'Document Approver')
        self.assertEqual(po.approved_date, date(2026, 1, 7))
        self.assertEqual(len(po.approval_log), 1)
        self.assertEqual(len(po.attachments), 1)
        self.assertEqual(PODocument.objects.count(), 1)
        self.assertEqual(self.storage.save.call_count, 1)
        self.assertTrue(second['signature_verified'])

    def test_pending_duplicate_uploaded_by_another_user_stays_in_each_owners_register(self):
        self.fields['vendor_name'] = 'Unregistered supplier'
        first = self.upload()
        other = get_user_model().objects.create_user('second-po-uploader', email='second-po-uploader@example.test')
        second = import_signed_po_pdf(b'%PDF-synthetic-deferred-po', filename='signed-po.pdf', user=other)
        self.assertNotEqual(first['document_id'], second['document_id'])
        self.assertEqual(PODocument.objects.filter(uploaded_by=self.user).count(), 1)
        self.assertEqual(PODocument.objects.filter(uploaded_by=other).count(), 1)
