from datetime import date, datetime

from django.test import SimpleTestCase

from apps.procurement.services.document_filenames import build_procurement_pdf_filename


class ProcurementPdfFilenameTests(SimpleTestCase):
    def test_builds_purchase_order_filename(self):
        self.assertEqual(
            build_procurement_pdf_filename("RAD/PRJ/PO 0062", "po", date(2026, 8, 19)),
            "RAD_PRJ_PO_0062_Purchase_Order_2026-08-19.pdf",
        )

    def test_builds_purchase_requisition_filename_from_datetime(self):
        self.assertEqual(
            build_procurement_pdf_filename(
                "RAD-PRJ-PR-0101", "purchase_requisition", datetime(2026, 1, 5, 12, 30),
            ),
            "RAD-PRJ-PR-0101_Purchase_Requisition_2026-01-05.pdf",
        )

    def test_rejects_unknown_module(self):
        with self.assertRaises(ValueError):
            build_procurement_pdf_filename("RAD-1", "invoice", date(2026, 8, 19))
