# Existing PO number correction

The user-authorized 28 September 2026 correction changes an existing PO's number,
including approved/completed records, through a dedicated command. Other
commercial edits remain subject to the existing locks.

`POST /api/v1/procurement/orders/{id}/correct-number/`

```json
{
  "po_number": "RAD-PRJ-PUR-0085_JUL2026",
  "expected_updated_at": "2026-09-28T08:00:00Z"
}
```

The response is the full current `PurchaseOrderSerializer` representation.
Existing PO update/module access is required. The exact saved timestamp is
mandatory; unknown fields and invalid numbers are rejected with 400. Stale or
duplicate numbers return 409, permission failures 403. Validation uses the
existing company format, model length and linked PR scope/year.

The command holds PR then PO locks. It saves the number and matching current
PR reference/PO-link projection, advances freshness, and records an `AuditLog`
update with `metadata.operation=correct_po_number` in the same transaction.
An audit failure rolls back all writes. An identical latest retry by the same
actor does not duplicate history/audit. It does not change status or send messages.

Approval rows, source extracts and original files retain their historical values.
`contact_persons._retained_po_number_corrections` is server-owned history of
number-only before/after fingerprints. Approval validation recomputes each
backward bridge using otherwise identical current commercial terms. A later
price, vendor or other commercial change cannot be hidden by this history.
Corrections do not repair an already mismatched approved-content fingerprint.
Serializer create/update and unsaved previews reject client authority over the
history by stripping supplied values and restoring the saved protected metadata.

The original order UUID and FK-based receipt/invoice links remain unchanged.
Source metadata is not renamed and no file is moved. Exact owned old storage
keys are retained using the existing protected attachment mechanism; legacy
original selection accepts those exact safe keys. A repeated old PDF remains
bound to its confirmed order and cannot create/rebind another order after rename.

The PO detail header offers only Edit PO number, its input, Save/Cancel and
necessary errors/loading. Failed responses preserve input. Successful responses
refresh the detail and its existing preview revision. The normal Edit action in
both the detail page and register opens this correction editor for locked or
completed orders, alongside the authenticated original PDF preview. This path
does not mount the full commercial form or run its unrelated required-field and
autosave logic. Native unlocked draft form edits retain their existing behavior;
generic approved/completed commercial editing stays locked.
No migration or new permission is introduced.

## Implementation locations

| File | Change |
| --- | --- |
| `apps/procurement/services/purchase_order_number_correction.py:58` | Atomic number correction, validation, reference updates and audit |
| `apps/procurement/views.py:2081` | Saved-order correction endpoint |
| `apps/rbac/action_policy.py:242` | Existing PO update permission mapping |
| `apps/procurement/services/purchase_order_content.py:120` | Preserve approved fingerprints through verified number-only corrections |
| `apps/procurement/serializers.py:1332` and `:1639` | Protect correction history from generic writes |
| `apps/procurement/services/purchase_order_document_preview.py:150` | Preserve trusted metadata in unsaved previews |
| `apps/procurement/services/purchase_order_sources.py:53` | Keep access to retained original upload keys |
| `apps/procurement/services/signed_po_pdf_import.py:769` | Prevent old-source reimports from rebinding a corrected PO |
| `frontend/src/pages/Procurement/PurchaseOrderNumberEditor.jsx:39` | Compact editor and save/error handling |
| `frontend/src/pages/Procurement/PurchaseOrderDetail.jsx:667` | Detail-header editor placement |
| `frontend/src/pages/Procurement/PurchaseOrderForm.jsx:3072` | Route normal locked/completed Edit to number correction |
| `frontend/src/pages/Procurement/OrderManagement.jsx:625` | Load current order for permitted register editing |
| `frontend/src/pages/Procurement/ProcurementRegister.jsx:111` | Allow existing Edit actions for completed orders |

The `frontend/` paths above are relative to the parent workspace, since it is a
separate Git repository. Tests are in `test_po_number_correction.py` and
`tests/accessibility/purchase-order-number-correction.spec.js` respectively.

## Verification

188 distinct backend tests passed under isolated `config.settings_release_test`:
163 existing regressions and 25 new correction cases, including permissions,
database persistence, stale/duplicate conflicts, atomic failed-audit rollback,
retry, approval integrity and retained source access. No live records changed;
SQLite checks do not certify PostgreSQL lock races. Python compilation and
whitespace checks passed. All 21 distinct browser cases passed (13 new correction
cases and 8 existing approval guards). The final production build passed;
targeted ESLint passed with six prop-types warnings and no errors. Browser API
responses use synthetic fixtures; database persistence is checked by the backend
tests. Detailed runs and frontend evidence are recorded in the workspace
`docs/features/purchase-order-number-preservation.md`.

The regular-Edit follow-up adds an actual signed-import regression: generic PATCH
returns the reported approval-lock message, while the correction command persists
the number and keeps source/approval evidence unchanged. All 40 backend checks
(26 correction and 14 approved-content cases) passed. The local Gunicorn backend
was gracefully reloaded after these checks; the correction route now returns the
expected unauthenticated 401 instead of 404. No business records were changed by
runtime verification. Frontend and runtime evidence for this follow-up is in
`artifacts/po-number-edit-path-20260928/` in the parent workspace.
All 41 distinct browser checks for the follow-up passed, including the normal
detail/register Edit flow for uploaded sent/completed POs, persisted reload,
conflict recovery and existing approval, draft-save and PR-link behavior. The
final production/PWA build passed; scoped lint had no errors and ten existing
full-form warnings. See the workspace brief for the exact run breakdown.

## Release verification - 28 September 2026

The backend development branch was aligned with fetched `origin/main`
`620541e2` before the PO changes were committed. Remote development `da744228`
was already included in that main commit, with identical application trees;
alignment required no conflict resolution or application changes.

The final aligned source passed 272 procurement tests in 70.664 seconds and
30 action-permission tests in 4.154 seconds. All 16 changed/new Python files
compiled and passed fatal Flake8 checks (`E9,F63,F7,F82`); whitespace validation
passed. Tests use the documented isolated SQLite settings, temporary media and
synthetic records. The audit-failure regression intentionally exercises a 500
response and verifies rollback.

Full-registry migration verification used the actual local Docker PostgreSQL
15.18 database with a read-only session, Python 3.11.16 and Django 5.0. All 528
current migration files across the 58-app registry are applied; history is
consistent, the graph has no conflicts and zero migrations are pending.
`migrate --check` and full-registry `makemigrations --check --dry-run` passed.
The recorder includes 29 additional historical entries; they were not removed.
This release adds no model or migration changes and requires no new migration.
Verification did not replay migrations or invoke post-migrate seed hooks, and
does not claim production migration status or deployment.

Local evidence is retained under the parent workspace
`artifacts/po-number-release-20260928/`: `backend-regressions.log`,
`backend-permissions-final.log`, `backend-fatal-lint.log`,
`backend-source-hashes.json` and `migrations/local-readonly-final.log`.
An initial permission-test command failed before test execution because of
Windows command-line quoting; the corrected isolated run passed all 30 cases.
