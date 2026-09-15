from contextlib import nullcontext
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import UUID

from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.procurement.services.pr_excel_import import ParsedRow, import_pr_workbook
from apps.procurement.views import PurchaseRequisitionViewSet


RECONCILIATION = "apps.procurement.services.pr_document_reconciliation"
EXCEL = "apps.procurement.services.pr_excel_import"
PO_ID = "aaaa1111-2222-4333-8444-555555555555"


class ManualPurchaseOrderLinkEndpointTests(SimpleTestCase):
    def request(self, payload, *, result=None, permitted=True):
        request = APIRequestFactory().post("/requisitions/111/link-purchase-order/", payload, format="json")
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True, pk=1))
        pr = SimpleNamespace(pk="pr-1")
        checked_modules = []
        def permission(_self, _request, view):
            checked_modules.append(view.module_required)
            return permitted
        with patch("apps.procurement.views.HasModuleAccess.has_permission", autospec=True, side_effect=permission), patch.object(PurchaseRequisitionViewSet, "get_object", return_value=pr), patch(f"{RECONCILIATION}.link_selected_purchase_order", return_value=result or {}) as link:
            response = PurchaseRequisitionViewSet.as_view({"post": "link_purchase_order"})(request, pk="pr-1")
        return response, link, checked_modules

    def test_success_uses_orders_permission_and_returns_link(self):
        result = {"status": "linked", "po_id": PO_ID, "po_number": "RAD-PRJ-PUR-0002_2026", "manual_link_required": False, "message": "Linked."}
        response, link, modules = self.request({"purchase_order_id": PO_ID}, result=result)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["requisition_id"], "pr-1")
        self.assertEqual(response.data["po_link"], result)
        self.assertEqual(modules, ["procurement_orders"])
        self.assertEqual(link.call_args.args[1], UUID(PO_ID))
        self.assertEqual(link.call_args.kwargs["actor"].pk, 1)

    def test_permission_denial_never_calls_link_service(self):
        response, link, modules = self.request({"purchase_order_id": PO_ID}, permitted=False)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(modules, ["procurement_orders"])
        link.assert_not_called()

    def test_invalid_order_identifier_returns_400_without_mutation(self):
        response, link, _modules = self.request({"purchase_order_id": "bad-id"})
        self.assertEqual(response.status_code, 400)
        link.assert_not_called()

    def test_conflict_is_actionable_and_preserves_service_result(self):
        result = {"status": "conflict", "manual_link_required": True, "message": "Order belongs to another PR."}
        response, _link, _modules = self.request({"purchase_order_id": PO_ID}, result=result)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"], result["message"])

    def test_missing_order_returns_404(self):
        result = {"status": "not_found", "manual_link_required": True, "message": "Order no longer exists."}
        response, _link, _modules = self.request({"purchase_order_id": PO_ID}, result=result)
        self.assertEqual(response.status_code, 404)


class ExcelPRLinkIntegrationTests(SimpleTestCase):
    def run_import(self, *, dry_run, existing=False, link_result=None):
        row = ParsedRow(sheet="PR", row_number=3, pr_number="RAD-PRJ-PR-0123_2026", values={
            "product_service": "Engineering", "issued_date": date(2026, 9, 15),
            "status": "draft", "currency": "AED", "total_price": "150.00",
        })
        pr_manager = Mock()
        pr_manager.filter.return_value.values_list.return_value = [row.pr_number] if existing else []
        instance = SimpleNamespace(id="new-pr", pk="new-pr", pr_number=row.pr_number,
                                   price_remarks_data={"payment_terms": "Net 45", "budget_in_aed": "2000.00"}, save=Mock())
        pr_manager.create.return_value = instance
        lookups = []
        for _index in range(3):
            manager = Mock()
            manager.all.return_value.only.return_value = []
            lookups.append(manager)
        with patch(f"{EXCEL}.parse_pr_workbook", return_value=([row], [])), patch(f"{EXCEL}.PurchaseRequisition.objects", pr_manager), patch(f"{EXCEL}.Vendor.objects", lookups[0]), patch(f"{EXCEL}.Project.objects", lookups[1]), patch(f"{EXCEL}.CoreProject.objects", lookups[2]), patch(f"{EXCEL}.transaction.atomic", return_value=nullcontext()), patch(f"{RECONCILIATION}.reconcile_pr_po_link", return_value=link_result or {}) as link:
            result = import_pr_workbook(SimpleNamespace(name="register.xlsx"), user=SimpleNamespace(pk=1), dry_run=dry_run)
        return result, pr_manager, link

    def test_dry_run_neither_creates_nor_links(self):
        result, manager, link = self.run_import(dry_run=True)
        manager.create.assert_not_called()
        link.assert_not_called()
        self.assertEqual(result["linking_notices"], [])

    def test_actual_created_row_has_link_result_and_actionable_notice(self):
        outcome = {"status": "not_found", "manual_link_required": True, "message": "Create or import the matching order.", "po_id": None, "po_number": ""}
        result, manager, link = self.run_import(dry_run=False, link_result=outcome)
        manager.create.assert_called_once()
        link.assert_called_once_with(manager.create.return_value)
        self.assertEqual(result["created"][0]["po_link"], outcome)
        self.assertEqual(result["rows"][0]["po_link"], outcome)
        self.assertEqual(result["linking_notices"][0]["requisition_id"], "new-pr")
        self.assertEqual(result["created_count"], 1)
        self.assertEqual(manager.create.return_value.price_remarks_data["po_link"], outcome)
        self.assertEqual(manager.create.return_value.price_remarks_data["payment_terms"], "Net 45")
        self.assertEqual(manager.create.return_value.price_remarks_data["budget_in_aed"], "2000.00")
        manager.create.return_value.save.assert_called_once_with(update_fields=["price_remarks_data", "updated_at"])

    def test_successful_link_does_not_emit_manual_link_notice(self):
        outcome = {"status": "linked", "manual_link_required": False, "message": "Linked.", "po_id": PO_ID, "po_number": "RAD-PRJ-PUR-0123_2026"}
        result, _manager, _link = self.run_import(dry_run=False, link_result=outcome)
        self.assertEqual(result["created"][0]["po_link"], outcome)
        self.assertEqual(result["linking_notices"], [])

    def test_existing_duplicate_row_is_not_mutated_or_linked(self):
        result, manager, link = self.run_import(dry_run=False, existing=True)
        manager.create.assert_not_called()
        link.assert_not_called()
        self.assertEqual(result["skipped_count"], 1)
