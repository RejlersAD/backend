# Purchase order receiving and invoice handoff

Implemented for the user's five-point handoff request, 24 September 2026. Current verification is recorded separately; this document does not certify deployment or production data repair.

## Records and authority

Purchase orders, receipts and supplier invoices remain separate canonical records. The new queues project orders that need work; they do not manufacture receipt or invoice rows. Existing effective module grants and configured technical inspection authority remain required. The later recorder-confirmation choice below adds a narrow explicit delivery command; no general approval role, tolerance or payment permission is granted.

The receipt queue requires receipt read and purchase order read access. Recording or reconciling requires receipt create plus purchase order read. Inspection requires receipt approve and the existing deployment-configured business route. Unknown or missing approval configuration still denies inspection.

Finance PO discovery requires Incoming Invoice read and Purchase Order read. Import retains Incoming Invoice create authority and requires Purchase Order read when confirming a PO. Manual allocation requires Incoming Invoice update and Purchase Order read, and uses the established project reconciliation command and its canonical-project requirement.

## Receiving API

| Route | Contract |
| --- | --- |
| `GET /api/v1/procurement/receipts/available-orders/` | `queue=awaiting` or `reconciliation`, `search`, `page`, `page_size`; DRF paginated PO rows with decimal-string receiving balances, capabilities and blocked reasons. |
| `GET /api/v1/procurement/orders/{id}/receiving-summary/` | Current basis, line balances and exact `po_updated_at` token. |
| `POST /api/v1/procurement/receipts/` | Pending receipt, required UUID `operation_key`, exact `expected_po_updated_at`, `purchase_order`, canonical `items_received` and recorded metadata, including optional date-only `receipt_date` (`YYYY-MM-DD`). |
| `POST /api/v1/procurement/receipts/reconcile/` | Same command plus a nonempty `reason`, only for completed orders with missing accepted coverage. Creates pending evidence and preserves PO completion and approval history. |
| `POST /api/v1/procurement/receipts/{id}/accept/` | Configured inspector decision with exact receipt `expected_updated_at`. Mixed accepted/rejected lines use the existing Partial disposition. |
| `POST /api/v1/procurement/receipts/{id}/confirm_delivery/` | Saved recorder confirms delivery with exact receipt `expected_updated_at` and optional `notes` of at most 4000 characters. All other payload fields are rejected. |
| `POST /api/v1/procurement/receipts/{id}/reject_delivery/` | Configured inspector decision, exact receipt token and rejection reason. |
| `PATCH /api/v1/procurement/receipts/{id}/` | Pending metadata only, with exact receipt token. Decided evidence, recorded receipt date and PO/line identities cannot be replaced. |

Quantity lines submit `line_id`, `received_qty` and `rejected_qty`. Service-value lines submit `line_id`, `received_amount` and `rejected_amount`. Values are decimal strings. Service acceptance uses recorded engineering/maintenance service scope and positive confirmed net value in the PO currency, never an invented quantity of one or a guessed gross-to-net conversion. Missing/ambiguous source data stays blocked and visible.

Received minus rejected values reserve the available balance while inspection is pending. Accepted/partially accepted evidence consumes the balance; rejected receipts do not. Legacy evidence must map unambiguously to the saved PO line basis. Duplicate, invalid or over-limit evidence cannot silently increase availability.

Commands lock and recheck the PO and record actor/time/history atomically. Recording, reconciliation and inspection decisions advance the PO freshness timestamp; pending metadata edits advance the receipt timestamp only. An identical operation-key retry by the same authorized recorder returns its existing receipt, including after later inspection; conflicting reuse or stale data returns HTTP 409. Explicit decisions also preserve recorded history. A partial receipt does not close a PO. Mark Complete requires full accepted receipt/service coverage.

Inspection accepts optional nullable `quality_check_passed`, `dimensional_check_passed`, `visual_inspection_passed`, `material_verification_passed` and `inspection_notes`. Unrecorded checks stay unknown. Identical same-actor decision retries recheck current authority and return the existing decision; changed payloads cannot rewrite inspected evidence.

Receipt creation and completed-order reconciliation accept the entered business `receipt_date` as a date, separately from the server's `created_at` timestamp. Omission remains supported for older clients and defaults to the current date in the active server timezone. Invalid/empty dates and timestamps are rejected; no new past/future date policy is imposed. Explicit dates participate in the existing command fingerprint, so changing the date with the same operation key conflicts. An unchanged retry that omitted the date retains its original receipt date even after midnight. The recording history includes the chosen date. Existing stored dates retain their prior creation-date meaning; there is no historical backfill or implied verification of physical arrival.

The simplified entry form submits Purchase Order, Delivery Note / Reference (`delivery_note_number`), Receipt Date (`receipt_date`), per-line received quantities/values and Remarks (`notes`). Inspection declarations may be omitted; new receipts remain Pending and unrecorded checks remain unknown. Technical inspection still requires its separate authorized command.

### Recorder delivery confirmation — 24 September 2026

The user explicitly selected **the person who recorded the receipt** to confirm
delivery. The new command authorizes only the immutable saved `received_by`
user, with current effective Receipt read/create and Purchase Order read access.
Missing ownership, inactive/deleted/locked identities, disabled modules, revoked
grants and explicit relevant action denials fail closed. An explicit Receipt
approve denial also prevents confirmation; an absent approve grant alone does
not. Administrators cannot confirm another person's receipt. No HR position or
deployment-configured inspector route is invented for the recorder.

Confirmation remains an explicit action after Pending creation. It uses the same
PR -> PO -> Receipt locks, PO approval/source checks, canonical decimal capacity
validation and acceptance disposition as inspection. It does not complete the
PO or change technical inspection flags, inspector identity, inspection notes,
NDT data or recorded remarks. New confirmation notes live in the command history.
Existing rejected line portions remain rejected and yield Partial acceptance.
The existing configured accept/reject commands remain available under their
existing authority for Pending receipts. Confirmation decides the receipt, so
those Pending-only commands do not become a later technical inspection stage.

Receipt representations add a read-only `confirmation` object:

```json
{
  "can_confirm": true,
  "blocked_reason": "",
  "responsible_user_id": 42,
  "responsible_user_name": "Receipt recorder",
  "confirmed_by_id": null,
  "confirmed_by_name": null,
  "confirmed_at": null
}
```

The saved recorder is identified even when confirmation is blocked. Capability
checks include the same PO approval, issue state, line basis and balance checks
as the command. Existing missing approvals or ambiguous historical lines remain
actionable blockers; no approval history or receipt data is repaired by this
feature. Confirmed actor/name/time are projected only from a recorded
`confirm_delivery` history event; historical accepted/inspected rows are not
backfilled or relabeled as recorder-confirmed.

The command records status, canonical items, actor ID/name, timestamp, optional
notes, original freshness token and payload fingerprint atomically in the
existing `workflow_history`. An identical original-token retry by the same
still-authorized recorder returns the same result; changed notes conflict with
HTTP 409. Current grant/deny checks apply before replay. Stale pending evidence
returns 409; source/input failures preserve the receipt and PO. The legacy
`open_inspections` metric key remains compatible and describes Pending receipts
awaiting confirmation or configured inspection. No schema migration or live
record mutation is required for this follow-up.

## Finance API

| Route | Contract |
| --- | --- |
| `GET /api/v1/finance/invoices/awaiting-purchase-orders/` | Orders with uninvoiced value derived from canonical allocations, approval evidence and source permissions; server search and pagination. |
| `GET /api/v1/finance/invoices/purchase-order-options/` | Searchable/paginated PO options, optionally exact `id` or `vendor`; removes the previous newest-500 selection limit. |
| `POST /api/v1/finance/invoices/{id}/allocate-purchase-order/` | `purchase_order_id`, positive decimal `allocated_amount`, explicit `confirm_po_match: true`, `expected_updated_at` and reason. Existing Invoice/PO locks, business validation, matching and audit apply. |

Invoice list/detail responses include read-only `confirmed_po_references`. Captured `po_reference_text` is preserved separately. Register search includes confirmed PO numbers. Generic invoice writes cannot manufacture confirmed references or allocation evidence.

The awaiting-invoice action opens reviewed import with the PO selected, but still requires an actual supplier document and explicit confirmation. Queue browsing creates no invoice. Current approval, vendor, currency and remaining value are rechecked under the PO lock on import and manual linking; historical inconsistent allocations are flagged for review. Existing configured matching tolerance remains unchanged.

Service acceptance records net value while allocations use invoice value; unproved net/line correspondence remains a matching exception. A header-only invoice does not become verified merely because one partial receipt exists. Rechecks validate the current PO status and approvals; repeated lines within an invoice share their accepted quantity balance. There is no per-line receipt-consumption ledger across separate invoices; that policy remains unresolved, independently of aggregate PO value checks. This change does not approve tax, non-PO expense, tolerance, posting or payment policy.

## Schema and rollout

`procurement.0045_receipt_command_evidence` adds nullable unique operation identity, a command fingerprint and workflow history to Receipt. Four inspection booleans become nullable with unknown defaults for new records; prior stored inspection declarations are preserved.

`procurement.0046_receipt_recorded_date` changes the existing date field from automatic creation date to a callable local-date default. This changes application field behavior without changing the column type or rewriting existing values. New clients send the selected date; the matching backend is required because older backends ignored this read-only field. Migration 0046 must be included in the release migration state. No production or existing local database migration is implied by this implementation.

Apply the migration before running the new backend. Deploy the matching frontend with it: recording and inspection commands now require freshness/retry fields that older forms do not send. Read-only register contracts retain their existing fields. The frontend retains entered data on validation/stale failures and offers an explicit refresh/review.

There is no automatic historical backfill, PO reopen, receipt acceptance or invoice creation. Recovery must preserve populated command history; do not reverse the schema migration after recording new evidence without an approved data-preserving recovery procedure.

## Verification evidence - 24 September 2026

The integrated Procurement/Finance regression suite passed 155 tests on Python 3.11.16 and PostgreSQL 15, including five observed row-lock races: competing receipts, same-command receipt retry, competing invoice allocations, and inspection versus edit in both directions. The latter verifies the central approval guard and domain command share the PR -> PO -> Receipt lock order without weakening approval checks.

All 527 current graph migrations applied successfully to a disposable PostgreSQL database using the full 58-app registry. Migration history, zero-pending and model-drift checks passed. The guarded local application of procurement 0045 also passed with zero pending and unchanged evidence for the two existing receipts. This evidence does not certify production schema or records. Local test/migration logs are retained under the workspace `.codex-temp/po-handoff-20260924/`.

Frontend verification passed 14 browser cases and five Node cases; changed-source lint reported zero errors/warnings. The final Node 20 production/PWA build passed after responsive-form and confirmed-PO-link checks. Builds retain existing bundle-size and Browserslist warnings. These changes remain local and unpushed; production requires its own migration and coordinated release.

### Receipt date follow-up verification

The simplified receipt entry follow-up passed 50 receiving-handoff and receipt-inspection tests with `config.settings_release_test` on isolated SQLite, including five new date persistence, reconciliation/retry, invalid-input, local-date fallback and evidence-protection cases. Command: `..\.venv\Scripts\python.exe manage.py test apps.procurement.tests.test_receiving_handoff apps.procurement.tests.tests_receipt_inspection --settings=config.settings_release_test --noinput --verbosity 1`, with the explicit test environment from the workspace contribution guide.

The full configured app registry loaded all 528 migration nodes and passed `makemigrations --check --dry-run` with its database explicitly replaced by in-memory SQLite and URL system checks skipped. The documented reduced migration settings failed on existing unrelated HR URL imports; the successful check used the full model registry. Migration 0046 applied and reversed on an isolated historical Receipt table with synthetic parent keys; existing row values and dates were unchanged, and explicit/default dates persisted after applying it. This focused SQLite check does not certify a full migration run, PostgreSQL locking or deployment. Neither the existing local database nor production was changed for this follow-up.

### Recorder-confirmation follow-up verification

The initial isolated SQLite receiving/inspection/confirmation run passed 64
tests in 36.143 seconds. After adding malformed-body coverage, the complete
15-case confirmation suite plus 21 central action-enforcement cases passed under
`config.settings_permissions_test` (36 tests, 6.506 seconds). These exercise the
real guarded router, same-recorder authority without a global approve grant,
other/missing recorder and explicit-denial cases, revoked/deactivated access,
source/balance blockers, stale and identical/conflicting retries, unchanged
technical evidence, and transaction rollback. Logs are in the workspace
`.codex-temp/receipt-confirmation-20260924/backend-sqlite.log` and
`backend-permissions.log`. No schema or existing business record changed.

The final isolated PostgreSQL run passed 118 confirmation, receiving,
inspection, PO-approval and Finance handoff tests (94.314 seconds), including
eight observed lock races. Three new races verify recorder confirmation versus
metadata edits in both directions and identical concurrent confirmations.
The disposable database and internal network were removed after the run. These
model-sync tests establish locking behavior, not a release migration run.

Frontend verification passed 30 browser scenarios, then 11 affected scenarios
after the final responsive-dialog change; 10 Node cases and scoped lint passed.
The final Node 20 production/PWA build passed, retaining existing Browserslist
and bundle-size warnings. Local evidence is under the workspace
`.codex-temp/receipt-confirmation-20260924/` and frontend
`.codex-temp/receipt-confirmation-browser*.log`. The local backend workers were
reloaded; read-only verification of the two reported receipts preserved their
Pending state and surfaced their existing PO approval/receiving-basis blockers.
No receipt confirmation, source repair or production deployment was performed.

The subsequent pending-delete implementation passed 157 tests on isolated
PostgreSQL 15 / Python 3.11 (94.199 seconds), including 11 observed lock races.
The three new races cover delete then confirm (404), confirm then delete
(protected evidence), and simultaneous deletion (one audit, second404).
Receipt CRUD, PO approval and Finance handoff regressions passed in the same run.
Log: workspace `.codex-temp/receipt-actions-20260924/postgresql-regression.log`.
The disposable database/network were removed after verification; these
model-sync tests do not certify release migration application. The local
backend workers were reloaded without mutating any existing receipt.

The matching visible Confirm/Delete UI passed all 39 browser scenarios, 10 unit
cases, two final responsive visual cases and scoped lint. The final Node 20
production/PWA build passed (112 precache entries); existing Browserslist and
bundle-size warnings remain. Evidence is in frontend
`.codex-temp/receipt-actions-browser-final.log` and workspace
`.codex-temp/receipt-actions-20260924/frontend-build.log`. Both reported local
Pending receipts project deletion as available for the current recorder's
account; source blockers for confirmation remain unchanged. No live deletion,
confirmation, source repair or production deployment was performed.

### Explicit pending-receipt deletion - 24 September 2026

`DELETE /api/v1/procurement/receipts/{id}/` accepts a JSON object containing
the exact `expected_updated_at` and returns 204. Current receipt read/delete
and purchase-order read access are required. Recorder ownership authorizes
delivery confirmation separately; deletion uses the existing module delete
grant and creates no new permissions. Read-only receipt responses include
`deletion: {can_delete, blocked_reason}` and record-scoped `capabilities.delete`.
The summary's delete capability denotes its module grant only.

Only Pending records without prior accepted/rejected/confirmed decision history
can be deleted. Decided evidence remains protected, even if its status was
inconsistently changed back to Pending. Deletion can remove an incorrect pending
reservation when its PO approval or receiving basis is missing; it never grants
that PO approval or permits delivery confirmation to bypass its existing gates.

The command locks PR -> PO -> Receipt and validates freshness before mutation.
It atomically removes the pending reservation, advances the parent timestamp and
records a surviving RBAC `AuditLog` with the actual actor/time, receipt snapshot,
number and operation key. The audit stores receipt metadata and references,
without expanded PO/person data or file bytes. Audit/write failure rolls back
the deletion. Files are retained, including shared receipt attachment references
when later PO/recommendation cleanup checks whether storage is still referenced.

Legacy receipt numbers advance the existing sequence floor before deletion, so
numbers are not reused. Retained audit operation keys prevent a delayed creation
retry from recreating a deleted receipt. A replacement uses a new creation key.
Missing or already deleted targets return 404, stale versions and protected
evidence return 409, denied access returns 403, and malformed payloads return
400. No schema migration, live deletion, source-record repair or deployment is
introduced by this follow-up.

The isolated SQLite permission-settings run passed **101 tests in 40.761
seconds**: 15 deletion cases, 15 recorder-confirmation cases, 50 receiving and
inspection regressions, and 21 central action-enforcement cases. The deletion
suite covers quantity/service balances, pending source blockers, current access
and explicit denial, immutable decided evidence, malformed/stale/missing/retry
requests, deleted creation-key reuse, legacy numbering, durable snapshots,
retained attachment bytes after later PO cleanup, and audit/delete/parent-write
rollback. Log: workspace `.codex-temp/receipt-actions-20260924/backend-permissions.log`.
These model-sync tests do not certify migrations or PostgreSQL row locking;
the separate concurrency verification is recorded by the scoped feature brief.
