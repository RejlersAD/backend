"""Generated requisitions retain the final sign-offs on a single page."""

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pymupdf
from django.test import SimpleTestCase

from apps.procurement.views import PurchaseRequisitionViewSet


class GeneratedRequisitionPDFTests(SimpleTestCase):
    def test_long_generated_export_fits_all_items_and_last_signoff_on_one_page(self):
        final_approval = datetime(2026, 9, 18, 14, 35, tzinfo=timezone.utc)
        pr = SimpleNamespace(
            id='synthetic-pr', pr_number='RAD-PRJ-PR-9001_2026', status='approved',
            issued_date=date(2026, 9, 18), issued_by=None, project='Synthetic engineering project',
            project_department='Engineering', department='Engineering',
            supplier_name='Synthetic technical supplier', supplier_business_id='TEST-ONLY',
            items=[{
                'description': f'Item {index:02d}: engineering assessment of piping, equipment and structural interfaces for the approved technical package.',
                'quantity': 1, 'uom': 'LOT', 'unit_price': '100.00', 'total': '100.00',
            } for index in range(1, 13)],
            product_service='', net_total_excl_vat=Decimal('1200.00'),
            total_price=Decimal('1260.00'), currency='AED',
            pm_name=SimpleNamespace(get_full_name=lambda: 'Synthetic Project Manager'),
            vp_op_name=SimpleNamespace(get_full_name=lambda: 'Final Executive Approver'),
            pm_approved_at=final_approval, vp_op_approved_at=final_approval,
            pm_approval_status='approved', vp_op_approval_status='approved',
        )
        view = PurchaseRequisitionViewSet()
        view.get_object = lambda: pr
        response = view.export_pdf(SimpleNamespace())
        self.assertEqual(response.status_code, 200)
        with pymupdf.open(stream=response.content, filetype='pdf') as document:
            self.assertEqual(len(document), 1)
            page = document[0]
            text = page.get_text()
            for index in range(1, 13):
                self.assertIn(f'Item {index:02d}', text)
            for expected in ('Final Executive Approver', '2026-09-18 14:35 UTC', 'Commercial terms and budget approved.', '1,260.00 AED'):
                self.assertIn(expected, text)
            for word in page.get_text('words'):
                self.assertTrue(page.rect.contains(pymupdf.Rect(word[:4])), word[4])
