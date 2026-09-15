from contextlib import nullcontext
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from apps.procurement.services.pr_document_reconciliation import (
    compare_existing_pr,
    link_selected_purchase_order,
    normalize_document_number,
    reconcile_pr_po_link,
)


MODULE = "apps.procurement.services.pr_document_reconciliation"


def make_pr(**changes):
    fields = dict(
        pk="pr-1", pr_number="RAD-PRJ-PR-0002_2026", product_service="Telecom services",
        title="Telecom services", issued_by=SimpleNamespace(get_full_name=lambda: "Sukanya Ravichandran"),
        issued_date=date(2025, 12, 31), supplier_name="Exctel Engineering Pte Ltd",
        preferred_supplier_if_any="", project="5901056", project_department="Project 5901056",
        currency="USD", total_price=Decimal("225608.00"), net_total_excl_vat=Decimal("225608.00"),
        po_number_reference="RAD-PRJ-PUR-0002_JAN2026", po_applicable=False,
        status="approved", save=Mock(),
    )
    fields.update(changes)
    return SimpleNamespace(**fields)


def pdf_fields(**changes):
    fields = dict(
        pr_number="RAD-PRJ-PR-0002_2026", product_service="Telecom services",
        issued_by_name="Sukanya Ravichandran", issued_date=date(2025, 12, 31),
        supplier_name="Exctel Engineering Pte Ltd", project_number="5901056",
        currency="USD", net_total=Decimal("225608.00"), po_reference="RAD-PRJ-PUR-0002_JAN2026",
    )
    fields.update(changes)
    return fields


def make_po(**changes):
    fields = dict(pk="po-1", po_number="RAD-PRJ-PUR-0002_JAN2026", pr_reference_id=None,
                  attachments=[], contact_persons={}, save=Mock())
    fields.update(changes)
    return SimpleNamespace(**fields)


class PRDocumentComparisonTests(SimpleTestCase):
    def test_compares_case_whitespace_dates_money_and_project_codes(self):
        pr = make_pr()
        report = compare_existing_pr(pr, pdf_fields(
            product_service=" telecom\n  SERVICES ", issued_by_name="sukanya   ravichandran",
            issued_date="31.12.2025", currency="usd", net_total="225,608.000",
            project_number="", project_department="Design (10522 & 10523), 5901056",
            po_reference="rad - prj - pur - 0002 _ JAN2026",
        ))
        self.assertTrue(report["identity_matched"])
        self.assertEqual(report["matched_count"], 9)
        self.assertFalse(report["has_mismatches"])
        pr.save.assert_not_called()

    def test_identifies_changed_amount_and_different_pr_without_overwriting(self):
        pr = make_pr()
        report = compare_existing_pr(pr, pdf_fields(pr_number="RAD-PRJ-PR-0003_2026", net_total="99.00"))
        self.assertFalse(report["identity_matched"])
        self.assertEqual(report["identity_status"], "mismatch")
        self.assertEqual({field["field"] for field in report["fields"] if field["status"] == "mismatch"}, {"pr_number", "net_total"})
        self.assertEqual(pr.total_price, Decimal("225608.00"))
        pr.save.assert_not_called()

    def test_missing_values_are_distinct_from_differences(self):
        report = compare_existing_pr(make_pr(), pdf_fields(net_total=None, issued_date=None))
        self.assertEqual(report["missing_count"], 2)
        self.assertFalse(report["has_mismatches"])

    def test_manual_or_filename_identity_is_not_proof_of_matching_pdf(self):
        for source in ("filename", "manual_review", "manual_override"):
            report = compare_existing_pr(make_pr(), pdf_fields(field_provenance={"pr_number": {"source": source}}))
            self.assertFalse(report["identity_matched"])
            self.assertEqual(report["identity_status"], "missing")

    def test_zero_is_a_valid_money_value_but_nan_is_not_a_match(self):
        pr = make_pr(net_total_excl_vat=Decimal("0.00"))
        report = compare_existing_pr(pr, pdf_fields(net_total="0"))
        money = next(field for field in report["fields"] if field["field"] == "net_total")
        self.assertEqual(money["status"], "matched")
        report = compare_existing_pr(pr, pdf_fields(net_total="NaN"))
        self.assertTrue(report["has_mismatches"])

    def test_reference_normalization_does_not_drop_month_identity(self):
        self.assertEqual(normalize_document_number(" rad - prj - pur - 0002 _ jan 2026 "), "RAD-PRJ-PUR-0002_JAN2026")
        self.assertNotEqual(normalize_document_number("RAD-PRJ-PUR-0002_JAN2026"), normalize_document_number("RAD-PRJ-PUR-0002_2026"))
        self.assertEqual(normalize_document_number("RAD-PRJ-PR-0002_JAN2026", kind="PR"), "")


class PRPOLinkReconciliationTests(SimpleTestCase):
    def reconcile(self, pr, orders, **kwargs):
        pr_manager, po_manager = Mock(), Mock()
        pr_manager.select_for_update.return_value.get.return_value = pr
        po_manager.only.return_value.iterator.return_value = iter(orders)
        def locked_filter(**query):
            result = Mock()
            result.order_by.return_value = [po for po in orders if po.pk in query["pk__in"]]
            return result
        po_manager.select_for_update.return_value.filter.side_effect = locked_filter
        with patch(f"{MODULE}.transaction.atomic", return_value=nullcontext()), patch(f"{MODULE}.PurchaseRequisition.objects", pr_manager), patch(f"{MODULE}.PurchaseOrder.objects", po_manager):
            result = reconcile_pr_po_link(pr, **kwargs)
        pr_manager.select_for_update.assert_called_once()
        return result

    def test_unique_exact_po_reference_links_without_changing_business_values(self):
        pr, po = make_pr(), make_po()
        result = self.reconcile(pr, [po], extracted_fields=pdf_fields())
        self.assertEqual(result["status"], "linked")
        self.assertFalse(result["manual_link_required"])
        self.assertIs(po.pr_reference, pr)
        self.assertEqual(pr.total_price, Decimal("225608.00"))
        self.assertEqual(pr.status, "converted")
        po.save.assert_called_once_with(update_fields=["pr_reference", "updated_at"])

    def test_source_alias_connects_month_reference_to_canonical_imported_po(self):
        po = make_po(po_number="RAD-PRJ-PUR-0002_2026", attachments=[{
            "type": "po_excel_import_source", "source_po_number": "RAD-PRJ-PUR-0002_JAN2026",
            "procurement_register": {"PR Number": "RAD-PRJ-PR-0002_2026"},
        }])
        result = self.reconcile(make_pr(), [po])
        self.assertEqual(result["status"], "linked")

    def test_explicit_po_register_pr_number_links_and_fills_empty_back_reference(self):
        pr = make_pr(po_number_reference="")
        po = make_po(attachments=[{"type": "signed_purchase_order_pdf", "procurement_register": {"PR Number": pr.pr_number}}])
        result = self.reconcile(pr, [po])
        self.assertEqual(result["status"], "linked")
        self.assertEqual(pr.po_number_reference, po.po_number)

    def test_no_reference_match_returns_notice_and_does_not_guess_from_amount(self):
        pr, po = make_pr(), make_po(po_number="RAD-PRJ-PUR-0099_2026", total_amount=Decimal("225608.00"))
        result = self.reconcile(pr, [po])
        self.assertEqual(result["status"], "not_found")
        self.assertTrue(result["manual_link_required"])
        po.save.assert_not_called()

    def test_existing_link_to_another_pr_is_never_stolen(self):
        po = make_po(pr_reference_id="another-pr")
        result = self.reconcile(make_pr(), [po])
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(po.pr_reference_id, "another-pr")
        po.save.assert_not_called()

    def test_conflicting_source_pr_number_blocks_link_even_when_po_number_matches(self):
        po = make_po(attachments=[{"type": "po_excel_import_source", "procurement_register": {"PR Number": "RAD-PRJ-PR-0099_2026"}}])
        result = self.reconcile(make_pr(), [po])
        self.assertEqual(result["status"], "conflict")
        po.save.assert_not_called()

    def test_multiple_matching_orders_require_manual_selection(self):
        first = make_po()
        second = make_po(pk="po-2", po_number="RAD-PRJ-PUR-0099_2026", contact_persons={"requisition_number": "RAD-PRJ-PR-0002_2026"})
        result = self.reconcile(make_pr(), [first, second])
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(len(result["candidates"]), 2)
        first.save.assert_not_called()
        second.save.assert_not_called()

    def test_existing_same_pr_link_is_idempotent(self):
        pr = make_pr(po_applicable=True, status="converted")
        po = make_po(pr_reference_id=pr.pk)
        result = self.reconcile(pr, [po])
        self.assertEqual(result["status"], "already_linked")
        self.assertFalse(result["manual_link_required"])
        po.save.assert_not_called()
        pr.save.assert_not_called()

    def test_source_identity_mismatch_never_links(self):
        po = make_po()
        result = self.reconcile(make_pr(), [po], extracted_fields=pdf_fields(pr_number="RAD-PRJ-PR-0099_2026"))
        self.assertEqual(result["status"], "identity_conflict")
        po.save.assert_not_called()

    def test_conflicting_pdf_and_stored_po_references_require_review(self):
        po = make_po()
        result = self.reconcile(make_pr(), [po], extracted_fields=pdf_fields(po_reference="RAD-PRJ-PUR-0003_JAN2026"))
        self.assertEqual(result["status"], "conflict")
        po.save.assert_not_called()


class ManualPRPOLinkTests(SimpleTestCase):
    def link(self, pr, po, *, other_link_exists=False, actor=None):
        pr_manager, po_manager = Mock(), Mock()
        pr_manager.select_for_update.return_value.get.return_value = pr
        po_manager.select_for_update.return_value.filter.return_value.first.return_value = po
        po_manager.filter.return_value.exclude.return_value.exists.return_value = other_link_exists
        with patch(f"{MODULE}.transaction.atomic", return_value=nullcontext()), patch(f"{MODULE}.PurchaseRequisition.objects", pr_manager), patch(f"{MODULE}.PurchaseOrder.objects", po_manager):
            result = link_selected_purchase_order(pr, "selected-id", actor=actor)
        pr_manager.select_for_update.assert_called_once()
        po_manager.select_for_update.assert_called_once()
        return result

    def test_explicit_selection_resolves_reference_and_converts_an_approved_pr(self):
        pr, po = make_pr(po_number_reference="RAD-PRJ-PUR-0099_2026"), make_po()
        result = self.link(pr, po)
        self.assertEqual(result["status"], "linked")
        self.assertEqual(pr.po_number_reference, po.po_number)
        self.assertEqual(pr.status, "converted")
        self.assertEqual(pr.total_price, Decimal("225608.00"))

    def test_linking_draft_does_not_approve_or_convert_it(self):
        pr, po = make_pr(status="draft"), make_po()
        result = self.link(pr, po)
        self.assertEqual(result["status"], "linked")
        self.assertEqual(pr.status, "draft")

    def test_another_pr_link_is_preserved(self):
        po = make_po(pr_reference_id="another-pr")
        result = self.link(make_pr(), po)
        self.assertEqual(result["status"], "conflict")
        po.save.assert_not_called()

    def test_different_existing_order_link_blocks_replacement(self):
        po = make_po()
        result = self.link(make_pr(), po, other_link_exists=True)
        self.assertEqual(result["status"], "conflict")
        po.save.assert_not_called()

    def test_explicit_source_conflict_blocks_manual_selection(self):
        po = make_po(contact_persons={"requisition_number": "RAD-PRJ-PR-9999_2026"})
        result = self.link(make_pr(), po)
        self.assertEqual(result["status"], "conflict")
        po.save.assert_not_called()

    def test_missing_selected_order_returns_not_found(self):
        self.assertEqual(self.link(make_pr(), None)["status"], "not_found")

    def test_success_replaces_stale_notice_and_records_actor_without_changing_financial_metadata(self):
        pr = make_pr(price_remarks_data={"po_link": {"status": "not_found", "manual_link_required": True}, "budget_in_aed": "5000.00", "payment_terms": "Net 30"})
        actor = SimpleNamespace(pk=91, get_full_name=lambda: "Procurement Buyer")
        result = self.link(pr, make_po(), actor=actor)
        self.assertEqual(pr.price_remarks_data["po_link"], result)
        self.assertEqual(result["linked_by_id"], "91")
        self.assertEqual(result["linked_by_name"], "Procurement Buyer")
        self.assertEqual(result["method"], "manual")
        self.assertTrue(result["linked_at"])
        self.assertFalse(result["manual_link_required"])
        self.assertEqual(pr.price_remarks_data["budget_in_aed"], "5000.00")
        self.assertEqual(pr.price_remarks_data["payment_terms"], "Net 30")
        self.assertIn("price_remarks_data", pr.save.call_args.kwargs["update_fields"])
