# Receipt delivery information

28 September 2026. Additive delivery declarations for the requested Record Goods
Receipt panel. These fields preserve the existing record-then-confirm workflow.

## API

`POST /api/v1/procurement/receipts/` and `receipts/reconcile/` accept these optional
fields in addition to the existing PO, canonical lines, date, operation UUID and
exact PO freshness token:

| Field | Values / limit |
| --- | --- |
| `delivery_location` | Entered delivery location, up to 300 characters |
| `supplier_reference` | Supplier delivery reference, up to 100 characters |
| `condition` | `good`, `damaged`, `not_inspected`, or blank |
| `delivery_status` | `full`, `partial`, `rejected`, or blank |
| `exception_reason` | Up to 4,000 characters |

Supplying a nonblank `delivery_status` requires a nonblank location and condition.
Partial or rejected delivery additionally requires an exception reason. Omitted
fields stay blank for existing clients and historical receipts; the migration
does not manufacture a location, condition, delivery assessment or exception.
There is no canonical office directory in this receipt contract; location is
entered evidence, not an organization or permission scope selector.

The service validates declarations against canonical receipt lines and current
balances while holding the existing PO lock. Full means all available balances
are covered without rejected portions. Partial means some amount is accepted,
with an incomplete balance or rejected portion. Rejected means every entered
received amount is rejected. Quantity decimals and service currency values use
the existing exact arithmetic and precision rules. This adds no new PO lines,
tolerance, change to service basis, or acceptance authority.

`delivery_status` is distinct from the protected receipt `status`. All records
start Pending, including declared rejected deliveries. `condition=good` does not
set a quality or inspection flag. All-rejected evidence cannot be confirmed by
the recorder; the existing configured inspection rejection command remains
separate. `received_by` remains the current server-owned recording user, and
receipt type remains derived from the canonical PO receiving basis.

List/detail responses return the saved fields. Creation history contains their
snapshot in `workflow_history[].delivery_information`, and the creation command
fingerprint includes submitted delivery fields. Identical retries retain the
existing record; changing a delivery field under the same operation key conflicts.

Pending generic updates may correct location, supplier reference, condition and
exception reason using the existing exact receipt freshness token and recorded
before/after history. They cannot clear required delivery information/reasons or
change `delivery_status` independently of immutable receipt lines. Accepted,
partially accepted and rejected evidence remains read-only. Existing route,
record ownership, explicit denial, transaction and PR -> PO -> Receipt lock
behavior remains in force.

## Migration and recovery

`procurement.0047_receipt_delivery_information` adds five blank-compatible
columns, depending on 0046. Apply it before running the updated backend; deploy
the matching frontend afterward. No existing row is assigned a declaration.
Legacy request payloads remain compatible. Reverting application code can leave
the additive columns intact; reversing the migration drops populated delivery
evidence and requires a data-preserving recovery procedure.

## Local verification

The full 58-app registry loads 529 migration nodes and passes the model-drift
check. Migration 0047 applied and reversed on a disposable in-memory SQLite
receipt table. Existing receipt columns were preserved, new columns defaulted
blank, and new delivery values persisted. This focused check does not certify a
full PostgreSQL migration or any deployed database; no existing database was
accessed or changed.

The new functional cases cover saved values/history, full multi-line coverage
with pending reservations, partial/rejected evidence, service values, reason and
length validation, duplicate/conflicting retries, legacy omission, stale/denied
creation, forged recorder, pending edits, protected decided evidence and rollback.
All 94 cases passed across delivery information, receiving handoff, recorder
confirmation, deletion and receipt inspection suites (27.915 seconds), using
`config.settings_release_test` and the explicit isolated environment from the
workspace `CONTRIBUTING.md`. The real guarded receipt router is exercised by
these fixtures; model-sync SQLite tests do not certify PostgreSQL concurrency.

Evidence at workspace root:
`.codex-temp/receipt-delivery-information-regression.log` and
`.codex-temp/receipt-delivery-information-migration.log`. The migration harness
is `.codex-temp/receipt_delivery_migration_check.py`. Those isolated checks did
not access the existing application database.

## Local development activation

The required migration was subsequently applied only to the verified Docker
development application, `radai_backend_local` with `postgres_local/radai_dev`.
The container declares `ENVIRONMENT=development` and `AIFLOW_ENVIRONMENT=local`,
and `/app` mounts the current workspace backend. A PostgreSQL-enforced read-only
probe confirmed the actual database server matched the local PostgreSQL 15
container and 0047 was the only pending migration.

The guarded script applied only 0047 in a transaction, with lock/statement
timeouts, exact target/plan assertions, disabled startup catalogue sync and
isolated notification transports. All 529 current graph migrations are now
applied, with zero pending and consistent history. Original receipt columns were
compared before/after; the local receipt table currently contained zero rows.
The separate synthetic migration test establishes historical-row preservation.

Only the local Gunicorn workers were gracefully reloaded. Seven permission,
role and grant table snapshots remained identical before migration and after
reload; the existing worker catalogue sync had no missing modules to create.
Post-reload read-only checks confirmed the five schema/API fields, zero pending
migrations and unchanged receipt count. Health returned HTTP 200; the protected
receipt API returned HTTP 401 without authentication. Initial health probes
timed out while the new workers loaded; subsequent checks passed.

Local activation evidence: `.codex-temp/receipt-delivery-local-readonly.log`,
`receipt-delivery-local-migration.log`, `receipt-delivery-local-post-reload.log`
and `receipt-delivery-local-verification.json` at workspace root. No receipt was
created/confirmed/deleted, no permissions changed, and no remote database,
production deployment or push was involved.
