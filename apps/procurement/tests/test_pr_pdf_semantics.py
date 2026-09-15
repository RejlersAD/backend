from decimal import Decimal

import pymupdf
from django.test import SimpleTestCase

from apps.procurement.services.pr_pdf_semantics import apply_pr_layout_semantics, approval_role
from apps.procurement.services.pr_pdf_text import extract_pr_pdf_text
from apps.procurement.services.signed_pr_pdf_import import detect_approval_evidence, extract_signed_pr_fields_from_text


class PRLayoutSemanticsTests(SimpleTestCase):
    def test_money_is_assigned_from_amount_column_instead_of_larger_remark_budget(self):
        def row(y, labels):
            return {"bbox": [10, y, 590, y + 25], "cells": [
                {"bbox": [left, y, right, y + 25], "text": text, "source": "ocr_cell", "confidence": 94}
                for (left, right), text in zip(((10, 320), (320, 430), (430, 590)), labels)
            ]}
        layout = {"page": 1, "width": 600, "height": 800, "coordinate_space": "pixels", "table_rows": [
            row(200, ("3. Price", "Amount", "Remarks")),
            row(225, ("Design services for 5902222", "EUR 3,450.00", "Sales Budget EUR 14,000.00")),
            row(250, ("Net Total excluding VAT", "EUR 3,450.00", "")),
        ]}
        baseline = extract_signed_pr_fields_from_text("PR No. RAD-PRJ-PR-0219_2026", "scan.pdf")
        fields = apply_pr_layout_semantics(baseline, {"page_layout": [layout]}, extract_signed_pr_fields_from_text, "scan.pdf")
        self.assertEqual(fields["net_total"], Decimal("3450.00"))
        self.assertEqual(fields["currency"], "EUR")
        self.assertEqual(fields["price_remarks"], "Sales Budget EUR 14,000.00")
        self.assertEqual(fields["field_provenance"]["price"]["section"], "net_total")
        self.assertEqual(fields["field_provenance"]["price"]["bbox"], [320, 250, 430, 275])

    def test_only_explicit_role_labels_map_to_workflow_stages(self):
        self.assertEqual(approval_role("PD"), "pm")
        self.assertEqual(approval_role("MoE"), "moe")
        self.assertEqual(approval_role("MoP"), "mop")
        self.assertEqual(approval_role("VP, Op"), "vp")
        for value in ("", "Name", "Signature", "Pankaj Kumar Singh", "Date", "3"):
            self.assertEqual(approval_role(value), "")


class PRScannedApprovalTableTests(SimpleTestCase):
    @staticmethod
    def make_scan(roles, signed=(), *, rasterized=True, printed=()):
        with pymupdf.open() as original:
            page = original.new_page(width=620, height=850)
            page.insert_text((190, 80), "Purchase Requisition", fontsize=15)
            page.insert_text((70, 110), "PR No. RAD-PRJ-PR-0190_2026", fontsize=11)
            page.insert_text((245, 495), "APPROVALS", fontsize=12)
            x_rules = (60, 135, 330, 480, 580)
            y_rules = [505, 540, *[540 + 38 * (index + 1) for index in range(len(roles))]]
            for x in x_rules:
                page.draw_line((x, 505), (x, y_rules[-1]), width=0.7)
            for y in y_rules:
                page.draw_line((60, y), (580, y), width=0.7)
            page.insert_text((200, 528), "Name", fontsize=12)
            page.insert_text((365, 528), "Signature", fontsize=12)
            page.insert_text((490, 528), "Remarks", fontsize=10)
            for index, role in enumerate(roles):
                baseline = 565 + index * 38
                page.insert_text((67, baseline), role, fontsize=12)
                page.insert_text((145, baseline), f"Approver {index + 1}", fontsize=11)
                if role in signed:
                    for offset in (0, 4, 8):
                        page.draw_bezier((352, baseline - 6 + offset), (380, baseline - 35), (430, baseline + 11), (465, baseline - 15 + offset), color=(0.1, 0.2, 0.7), width=1.5)
                if role in printed:
                    page.insert_text((340, baseline), "Awaiting signature", fontsize=10)
            if not rasterized:
                return original.tobytes()
            raster = page.get_pixmap(dpi=200, alpha=False).tobytes("png")
        with pymupdf.open() as scan:
            page = scan.new_page(width=620, height=850)
            page.insert_image(page.rect, stream=raster)
            return scan.tobytes()

    def test_three_row_scan_preserves_pd_mop_vp_without_inventing_moe(self):
        pdf = self.make_scan(("PD", "MoP", "VP, Op"), signed=("PD", "MoP", "VP, Op"))
        source = extract_pr_pdf_text(pdf)
        fields = detect_approval_evidence(pdf, _source=source)
        self.assertEqual([row["role_key"] for row in fields["approval_rows"]], ["pm", "mop", "vp"])
        self.assertEqual(len(fields["approval_rows"]), 3)
        self.assertEqual(fields["approver_names"]["moe"], "")
        self.assertFalse(fields["signatures"]["moe"])
        self.assertTrue(fields["signature_candidates"]["pm"])
        self.assertFalse(fields["all_four_signatures"])

    def test_four_row_scan_does_not_count_blank_cells_or_headers_as_signatures(self):
        pdf = self.make_scan(("PM", "MoE", "MoP", "VP, Op"))
        source = extract_pr_pdf_text(pdf)
        fields = detect_approval_evidence(pdf, _source=source)
        self.assertEqual([row["role_key"] for row in fields["approval_rows"]], ["pm", "moe", "mop", "vp"])
        self.assertEqual(len(fields["approval_rows"]), 4)
        self.assertFalse(any(fields["signatures"].values()))
        self.assertFalse(any(fields["signature_candidates"].values()))

    def test_native_signature_placeholder_text_is_not_signature_evidence(self):
        roles = ("PM", "MoE", "MoP", "VP, Op")
        pdf = self.make_scan(roles, rasterized=False, printed=roles)
        source = extract_pr_pdf_text(pdf)
        fields = detect_approval_evidence(pdf, _source=source)
        self.assertEqual(source["method"], "native")
        self.assertEqual([row["role_key"] for row in fields["approval_rows"]], ["pm", "moe", "mop", "vp"])
        self.assertFalse(any(fields["signatures"].values()))
        self.assertFalse(any(fields["signature_candidates"].values()))
