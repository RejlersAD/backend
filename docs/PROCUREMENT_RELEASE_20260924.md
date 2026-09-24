# Procurement and Finance release verification — 24 September 2026

Release source: `development`, aligned with `main` at `ffdc690a`. The Finance
serializer merge preserves both upstream classification-field protections and
the server-owned confirmed PO references introduced by this release.

## Behavior

- Approved issued orders with remaining quantity or service value appear in the
  receiving queue. Completed orders missing accepted evidence have a separate
  reconciliation queue; recorded history is preserved.
- Receipts start Pending. Explicit recorder confirmation and configured
  inspection use current authority, source approval, balances and freshness.
  Partial/full coverage comes from accepted evidence. PO completion requires full
  accepted coverage; confirmation does not assert technical inspection checks.
- Permitted deletion removes only Pending, undecided receipts and atomically
  retains an audit snapshot. Retry identities, numbering and attachment evidence
  remain protected against duplicate creation and later cleanup.
- Finance queues and PO selectors use canonical allocations, approval eligibility
  and current access. Supplier-document import and allocation require explicit
  PO confirmation; generic invoice edits cannot forge confirmed references.
- PDF/Word share introduction and heading visibility, meaningful scope detection
  and a 12 pt narrative default. Empty rich-text markup cannot qualify a service
  order for receiving; meaningful recorded description fallback remains supported.

Contracts: [receipt and invoice handoff](PO_RECEIPT_INVOICE_HANDOFF.md),
[Finance handoff](FINANCE_PO_HANDOFF.md), and
[PR/PO lifecycle](PROCUREMENT_LIFECYCLE.md).

## Migrations and rollout

`procurement.0045_receipt_command_evidence` adds nullable unique operation identity,
command fingerprints and receipt workflow history. New inspection checks can
remain unknown instead of asserting a result. Existing stored declarations are
preserved.

`procurement.0046_receipt_recorded_date` allows an explicitly entered receipt date
and retains a local-date default. It changes field behavior without rewriting
existing dates. No Finance migration is introduced.

Railway's configured pre-deployment command is
`python manage.py migrate --noinput --skip-checks`. Apply migrations before
starting this backend and coordinate the frontend rollout: receipt commands need
the matching freshness/retry fields. Verify the target migration plan is empty,
the health endpoint responds, and authorized receipt/invoice workflows work after
deployment. Local verification does not certify a production database.

Preserve populated command history, audits and files during recovery. Use a
compatible coordinated rollback or forward fix; reversing migration 0045 after
new evidence has been recorded requires a data-preserving recovery procedure.
There is no automatic historical repair, receipt acceptance, PO reopening or
invoice creation.

## Verification boundaries

Service acceptance records net value; invoice allocations use invoice totals.
Without an approved correspondence, matching remains a review exception.
Receipt quantity consumption across separate invoices remains an explicit policy
boundary. Neither PO completion nor receipt confirmation authorizes payment.

Document regressions validate PDF/Word content and structure. Editable Word body
pages can reflow with fonts or Word versions; native Word pagination has not been
certified. Existing scoped test settings do not replace a real migration run or
PostgreSQL lock verification; those results are recorded separately below.

## Checks performed

Application changes were frozen at `87ca1326`; `1a0ed063` corrects a regression
fixture to reload its persisted Decimal pricing, and `b9a9aba` adds a protected
Finance-field denial example. Later release-note changes are documentation only.

| Check | Result |
| --- | --- |
| PostgreSQL 15 / Python 3.11 integration run | 364 executed: 363 passed and one pre-correction fixture error |
| Observed PostgreSQL lock-race scenarios within that run | All 11 passed |
| Corrected fixture and all invoice field-integrity tests | 22 passed in the focused run |
| Central permission and receipt command checks | 65 passed |
| Current receiving suite | 32 passed |
| Current rich-content/PDF/Word suite | 18 passed |

The initial integration process had loaded the old fixture before its correction;
its sole error was a string-versus-Decimal comparison in that fixture. The
corrected fixture passes the focused rerun. This is not a claim that the original
364-case command exited successfully. No application assertion failure remains
from that run. Test artifacts and any subsequent PostgreSQL rerun/fresh-schema
proof are retained under the workspace
`.codex-temp/release-procurement-20260924/` and summarized in the release PR.

### Existing local database

On Docker `postgres_local/radai_dev`, a guarded plan permitted only
`procurement.0046_receipt_recorded_date`; `0045` was already applied. `0046` applied
successfully. Afterwards the database had 557 historical migration records,
consistent history and zero pending graph migrations. `migrate --check` and
full-registry `makemigrations --check --dry-run` passed.

Read-only before/after audits found zero receipt rows and an unchanged complete
receipt-data digest. No receipt, approval or invoice was manufactured by the
migration. This evidence establishes the configured local database state only;
the production database was not migrated or verified in this session.

The local migration command disabled upstream RBAC catalogue startup sync to
avoid unrelated catalogue writes. Ordinary aligned application startup retains
that existing behavior; disposable verification checks it separately. Release
artifacts retain the command configuration and results without credentials.
