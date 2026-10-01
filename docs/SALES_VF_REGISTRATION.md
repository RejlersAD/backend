# Initial VF opportunity registration

Implementation: 30 September 2026. Not a production deployment.

Subsequent phase, 1 October 2026: the configurable
[opportunity document workspace](SALES_OPPORTUNITY_WORKSPACE.md) now persists
setup intent with the same registration transaction. Its six-folder SharePoint
integration is disabled until separately configured; a network-drive destination
and live storage activation remain unresolved. The registration evidence below
describes the original phase and does not certify this later integration.

## Approved scope

RADAI issues new VF codes beginning at **Q-102101**, on the existing Sales Deal.
Historical codes and records stay unchanged. Registering the manual "Archive"
creates an active Open opportunity, not an archived/closed record. Document
categories and shared-drive integration are a later phase.

Initial registration captures type, title, canonical client, opening date,
known submission deadline and owner. Code, creator and creation timestamp are
server controlled. Unknown amount/currency/scope/award date may be completed
before qualification. AI/email extraction remains a reviewed proposal; creating
the record requires explicit saving.

## Contract

Existing `/api/v1/sales/deals/` endpoints remain canonical. Additive fields are
`opportunity_type` (tender/rfq/eoi/direct_enquiry/other; historical blank allowed),
`open_date` (nullable date), and read-only `created_by`. List/detail expose
`created_by_name`. `deal_code` is read-only. Existing `created_at` is actual
creation time, independent of opening date.

The new form requires reviewed type and opening date for manual creation.
Legacy callers may omit type. Missing manual open_date defaults to the server's
local date; explicit null stays unknown. Email defaults derive from the trusted
received timestamp in Asia/Dubai. Reviewed edits do not alter source timestamps.

`submission_due_date` remains the proposal/submission date-only field. Exact
email time/timezone and other action deadlines remain source evidence. An EOI
response date is not automatically assigned as a proposal deadline.

`estimated_value`, `weighted_value`, `expected_close_date` may be null initially;
currency and scope_type may be blank. Qualification still requires scope,
submission deadline, expected award date, value, currency and owner. Existing
stage codes and governed actions remain; lead displays Open. Award pending,
No Bid and Cancelled stay distinct; no free status edit manufactures approval.

`GET /api/v1/sales/deals/registration-options/` requires opportunity read/create.
Returns default_owner (User ID), owners [{id,name}] and opportunity_types
[{value,label}]. Active owner choices reuse existing Sales record visibility.
Client read authority and canonical client/owner visibility are checked on the
server. GET does not reserve a code.

Manual POST accepts optional registration_request_id (UUID). Same actor, UUID
and validated payload returns the existing accessible record (200); changed
payload returns 409. Creation returns 201. The form retains its UUID/input on
failure. Clients omitting UUID do not receive manual retry deduplication.

Actor no-key row locking serializes retries while permitting owner FK references.
A singleton sequence row serializes allocation across actors and entrypoints.
Counter, record and audit commit atomically; rollback consumes no committed
number. Existing collisions are skipped. Committed issued numbers are not
recycled. Email source-bound retry/tender guards remain, including historical
scope-hash compatibility.

## Migration and recovery

Sales 0011 adds fields/sequence, relaxes initial commercial nullability and changes
the lead display label. No renumbering or guessed historical creator/date backfill.
Apply backend migration before the frontend and restart backend/worker processes
together; mixed old/new writers are not supported during activation.

Reversal is permitted only before issued numbers, new registration evidence or
records incompatible with the old NOT NULL schema exist. Migration refuses
destructive reversal. After use, retain schema for a forward fix or restore a
verified pre-migration backup through established recovery; do not reset the
counter or fabricate commercial data to force rollback.

## Verification

Tests use synthetic data and isolated databases. No production business records
are inserted for verification.

- Full Sales suite: 690 tests run; 671 passed and 19 skipped by the SQLite harness
  (`artifacts/vf-sales-regressions.log`).
- Final affected registration/email suite: 73 tests passed after permission,
  historical retry and response-contract fixes (`artifacts/vf-final-integration.log`).
- Nullable Sales/executive reports: 65 tests passed, including 53 retained
  executive cases (`artifacts/vf-nullable-consumers-executive-tests.log`).
- Disposable PostgreSQL 16: 13 API/concurrency tests passed, including five
  observed real-lock races, cross-owner assignment, same-request replay and
  audit rollback (`artifacts/vf-postgres-concurrency-final.log`). Initial harness
  cleanup timed out at 20 seconds; the final test-only timeout is 120 seconds.
- Scoped fatal Python lint and whitespace checks passed.
- Final API smoke after eager-loading creator names: 8 tests passed
  (`artifacts/vf-final-api-smoke.log`).
- Frontend maintains its own verification record in docs/SALES_VF_REGISTRATION.md.

- PostgreSQL Sales migration chain through 0011 and `makemigrations sales --check
  --dry-run` passed. Reversing 0011 before use, upgrading a historical opportunity
  and refusing reversal after issuing numbers passed. Existing code/value and
  unknown historical creator/date were preserved
  (`artifacts/vf-postgres-sales-migration.log`). This certifies the Sales chain,
  not a fresh rebuild of every unrelated application.
- Local database `radai_dev` on `postgres_local` applied only pending Sales 0011.
  Read-only verification confirmed the migration, next code Q-102101, no starting
  collision and the registration-options route. Backend/workers restarted;
  backend health and updated frontend source on localhost:5173 returned 200.
  No local opportunity was created for verification. Evidence:
  `artifacts/vf-local-migration.log`, `artifacts/vf-local-verification.log`.

The initial implementation was activated locally. Release preparation is now
authorized separately; no production deployment or main merge has been performed.

## Release verification - 30 September 2026

The release integration run passed 107 tests covering VF registration, secure
export, nullable consumers and live/saved email conversion
(`artifacts/vf-release-integration.log`). Fatal Python lint and whitespace checks
passed. Both fetched development and main were already ancestors; alignment did
not change application code.

A fresh disposable PostgreSQL 16 database passed the Sales migration chain through
0011, schema drift check, pre-use reversal, historical opportunity upgrade and
issued-number rollback refusal (`artifacts/vf-release-postgres-migrations.log`).
This certifies the scoped Sales migration chain, not every unrelated application.
The final disposable PostgreSQL concurrency/API run passed 13 tests, including
the observed allocation/retry lock races and rollback behavior
(`artifacts/vf-release-postgres-concurrency.log`).

Guarded read-only inspection of `postgres_local/radai_dev` confirmed 564 applied
migrations, consistent history, zero pending migrations across all applications,
Sales 0011 applied and no Sales model drift
(`artifacts/vf-release-local-migrations.log`). The application database was not
used for synthetic registration tests. Production migration remains unverified;
deploy the backend and apply Sales 0011 before the corresponding frontend.
