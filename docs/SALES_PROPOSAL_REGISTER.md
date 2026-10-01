# Sales Proposal Register projection and export

The compact register continues to use canonical `sales.Quote`. This change adds
read-only list fields and an authorized CSV projection; it changes no models,
migrations, approval policy, submission commands or PDF review behavior.

## Register data

`GET /api/v1/sales/quotes/` retains its existing fields and adds:

- `deal_code`: the associated opportunity's canonical VF identifier.
- `submission_due_date`: the opportunity's date-only submission deadline or null.
- `service_categories`: the opportunity's saved category keys, possibly empty.

`valid_until` remains proposal validity, not the submission deadline. The proposal
owner is its actual `prepared_by`/`prepared_by_name`, not the opportunity owner.
`Quote.version`, bound PDF review revision and private file version remain
distinct facts. Detail already exposes opportunity data through `deal_details`.

List ordering is `-created_at,-id` to make equal timestamps deterministic across
pages. Existing page-number pagination and status/client/deal filters and search
remain unchanged. The default page size is 500. A client showing global tab
counts must consume the complete bounded listing, not silently count one page.
This deterministic order does not make a multi-page listing a database snapshot.
The legacy Quote list/detail visibility behavior is unchanged; the new export
independently enforces opportunity visibility, as the scoped PDF review does.

## CSV export

`POST /api/v1/sales/quotes/export/` accepts exactly:

```json
{"ids":"proposal-uuid,another-proposal-uuid"}
```

The selection must contain 1–10,000 unique valid UUIDs; omission never means
all rows. Query parameters and extra body fields are rejected. The endpoint
requires authenticated `sales_proposals.read` and `.export`, plus
`sales_opportunities.read`. It does not require create permission or opportunity
export permission. The existing Sales Deal visibility filter scopes every row,
and Quote/Deal client identities must still agree. A missing or unavailable row
rejects the whole response with the same generic 403, before any CSV is returned.

Rows retain requested order. Columns are Proposal, Version, VF code, Title,
Client, Status, Owner, Service line, Submission deadline, Valid until, Issue date,
Proposed price, Estimated cost and Currency. Owner comes from `prepared_by`;
unknown facts are blank. Dates are ISO date-only values and money uses decimal
text. CSV quotes all cells, escapes spreadsheet formula prefixes including
leading controls, and has a UTF-8 BOM. The attachment is named
`proposals-YYYY-MM-DD.csv` with `Cache-Control: private, no-store` and `nosniff`.
No private notes, arbitrary file paths, approval comments or submission evidence
are included. No model or external-storage writes occur.

## Evidence presented in the workspace

Approval evidence is the saved Quote's `approved_by`, `approved_at` and
`approval_history`. No technical/commercial/final approval stages or assigned
approvers exist in this schema. Preparation completeness and internal PDF
review outcomes are separate from governed approval. Submission details use
`sent_date`, `submission_recipient`, `submission_evidence` and
`submitted_version_hash`; the existing command records submission and does not
certify email delivery. Its hash is not a PDF content hash.

Bound PDF revisions come from the existing scoped review projection. Files in
the opportunity Proposal folder are not automatically bound to every Quote.
See [the review contract](SALES_PROPOSAL_REVIEW.md) and
[the opportunity workspace contract](SALES_OPPORTUNITY_WORKSPACE.md).

## Verification

The isolated SQLite run passed 49 tests with one existing PostgreSQL-only review
concurrency case skipped (50 discovered, 36.523 seconds). It includes 16 new
register/export cases, 13 existing opportunity-export regressions, 20 review
cases and the configured proposal-approval regression. The new cases exercise
canonical display fields and nulls, distinct dates, stable tied-time pagination,
existing search/filter behavior, actual module grants/explicit denial, hidden
and missing selections, mismatched client scope, CSV formatting/formula safety,
bounded input and anonymous/method denials. Initial fixture setup mistakes
(required unique user emails and Quote subtotal) were corrected before the
passing runs. No application-source defect was masked with authorization mocks.

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:DATABASE_URL = 'sqlite:///:memory:'
$env:AIFLOW_ENVIRONMENT = 'testing'
$env:ENVIRONMENT = 'testing'
$env:USE_S3 = 'false'
$env:SPEC_SKIP_CORS_ON_READY = '1'
$env:S3_AUTO_APPLY_CORS = '0'
..\.venv\Scripts\python.exe manage.py test apps.sales.tests.test_proposal_register apps.sales.tests.test_vf_opportunity_export apps.sales.tests.test_proposal_review apps.sales.tests.testintake.ProposalApprovalTests --settings=config.settings_release_test --noinput --verbosity 1
```

Evidence: `artifacts/sales-proposal-register-tests.log` (native exit 0); the
standalone 16 new cases also pass in `artifacts/sales-proposal-register-targeted-tests.log`.
New source/tests pass flake8 F/E9 checks, edited legacy source passes fatal
Python checks, and `git diff --check` passes. This read-only change has no schema
delta; SQLite is not new evidence of migration execution or PostgreSQL locking.
No production deployment, remote release or live export was performed.
