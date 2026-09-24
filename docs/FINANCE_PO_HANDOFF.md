# Purchase order to supplier invoice handoff

Implemented 24 September 2026 for the explicitly requested Procurement/Finance handoff. This extends existing records and commands; it does not create an invoice when a PO is completed or approve an invoice/payment.

## Read contracts

`GET /api/v1/finance/invoices/awaiting-purchase-orders/` lists approved issued orders (including completed orders) with remaining uninvoiced value. `GET /api/v1/finance/invoices/purchase-order-options/` supplies the import/matching selector. Both require effective `finance_incoming.read` and `procurement_orders.read`, including individual deny overrides. Existing PO module read authority is retained; no new organization/project visibility policy is inferred.

Both accept `search` (PO number, title, supplier), `id`, `vendor`, `page`, and `page_size` (25 default, 100 maximum). They return `{count,next,previous,results}`. Each result contains `id`, `po_number`, `title`, `status`, `vendor_id`, `vendor_name`, `currency`, `total_amount`, `allocated_amount`, `remaining_amount`, `enterprise_project_id`, `allocation_issue` and `can_import_invoice`. Money is a decimal string. The awaiting queue adds the Procurement `receiving` summary, using the current user's receipt capabilities. Results must satisfy the existing PO approval evidence service, not merely a lifecycle label.

Allocation totals come from `InvoicePurchaseOrderAllocation`, not legacy PO `invoice_status`, `related_invoices`, or `total_invoiced_amount`. Existing allocations with incompatible currency/vendor or nonpositive values produce `allocation_issue`, a null `remaining_amount`, and disabled import capability; historical records are not rewritten. There is no implicit currency conversion or guessed cancellation/reversal policy. Existing allocations remain counted until an authorized correction changes their evidence.

Invoice list and detail add `confirmed_po_references: [{id,po_number}]`. Search includes those canonical links. `po_reference_text` remains the separately captured supplier/OCR reference. Detail adds `capabilities.can_allocate_purchase_order` and `capabilities.can_recheck_match`.

## Commands

`POST /api/v1/finance/invoices/{id}/allocate-purchase-order/` requires `finance_incoming.update`, existing invoice visibility, and `procurement_orders.read`. Payload: `purchase_order_id`, positive two-decimal `allocated_amount`, `confirm_po_match: true`, exact `expected_updated_at`, and optional `reason` (maximum 500 characters). The command locks invoice then PO, checks the timestamp before effects, and reuses the existing canonical-project allocation/audit/matching command. A stale token returns 409. Repeating an existing allocation does not create another one. The response is HTTP 201 with the allocation result and refreshed `invoice` detail.

New allocations require an approved issued PO, matching supplier/currency, open invoice, remaining invoice amount, and current PO capacity using the existing configured tolerance. The same locked PO validation is applied to reviewed import and Project Reconciliation. Historical mismatches are not silently repaired. Missing canonical project linkage remains the existing Project Reconciliation validation error.

Reviewed PDF import still requires explicit PO confirmation and duplicate supplier-invoice/PDF checks. Its PO selection is now searched/paginated independently; OCR no longer embeds the newest 500 unscoped POs. OCR suggestions use the requesting user's current PO read authority, approval eligibility and canonical allocation balances. Without that source authority they return no PO data; an unallocated supplier invoice can still be imported with existing Finance authority. Source checks and PO balance checks run before financial records are created.

## Receipt matching boundary

New receipt line identities map back to canonical PO lines and existing invoice item references. An invoice without positive line quantities cannot become verified merely because some accepted goods receipt exists. This yields `invoice_line_match_requires_review`.

Rechecking an existing allocation revalidates current PO status and approval evidence; cancelled, unissued or approval-invalid orders produce `purchase_order_approval_requires_review`. Multiple invoice lines with the same unambiguous PO reference are summed before comparing ordered/accepted quantity. Ambiguous normalized references cannot verify a line. Quantity checks cover this invoice only; there is no approved per-line receipt-consumption ledger between separate invoices, and the evidence explicitly records `receipt_quantities_not_allocated_between_invoices`. Aggregate PO value checks remain separate from this unresolved quantity-allocation policy.

Service acceptance records contain confirmed net values, while invoice allocations use invoice totals. Without an approved line/value mapping, matching records the service evidence with `value_basis: net_excluding_vat` and returns `service_value_match_requires_review`. It does not assume quantity one or equate gross invoices to net service acceptance. D-16 policy remains open; service acceptance is visible but does not alone certify a three-way match or authorize payment.

## Verification

The guarded API suite is `apps.finance.tests_purchase_order_handoff`. Existing reviewed import, procurement invoice model, field integrity and project relationship suites cover adjacent contracts. Tests use synthetic records, temporary storage and explicit grants; external HTTP is blocked. SQLite runs do not certify PostgreSQL locking, migration application or deployed authorization. PostgreSQL race tests are separately maintained in `apps.procurement.tests.test_handoff_concurrency_postgresql`.

No Finance model migration is introduced. Procurement receiving fields have their own migration and verification.
