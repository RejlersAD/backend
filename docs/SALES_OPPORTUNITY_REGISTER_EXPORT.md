# Opportunity register export

The Opportunity Register UI continues to use scoped `GET /api/v1/sales/deals/`.
Its list includes `service_categories`, and ordering permits `deal_code,id` for
stable complete traversal with the existing pagination contract.

`POST /api/v1/sales/deals/export/` accepts JSON containing only `ids`, a string
of 1-10,000 unique comma-separated opportunity UUIDs. It requires both read and
export actions for `sales_opportunities`. IDs are resolved through the existing
row-scoped queryset; if any are absent/inaccessible, no rows are exported and the
response is a generic 403. Bad IDs, duplicate IDs and oversized selections return
400. This endpoint does not accept query parameters or infer an omitted selection.

The successful UTF-8 BOM CSV attachment contains VF code, title, client, type,
service line, submission deadline, owner, estimated value, currency, probability,
stage, bid decision and next action. Missing values remain blank. Decimal values
remain decimal strings; text that may be interpreted as a spreadsheet formula
is prefixed as literal text. Headers specify `private, no-store` and `nosniff`.

No persistence, schema, numbering or workflow changes. Tests are in
`apps/sales/tests/test_vf_opportunity_export.py`; run with the established isolated
release test settings and explicit test database configuration, never ambient
production environment values. The guarded API tests cover action and row scope,
invalid input, formula escaping, exact amounts, list fields and pagination order.

Verified: 39 isolated export/VF registration/core tests plus the subsequent API
pagination-order test passed, along with fatal Python lint and whitespace checks.
The local backend was restarted successfully. Guarded inspection of the local
postgres_local/radai_dev database found no pending Sales migration dependencies;
this change adds no migration. No production deployment.

## Release preparation - 30 September 2026

The fetched development and main branches were already ancestors of the VF
registration source; normal merges reported already up to date. The release
combines existing VF registration with this scoped export and list contract.

Release verification passed 107 VF registration, export, nullable-value and
live/saved email conversion tests using isolated release settings. Scoped fatal
Python lint and whitespace checks passed. The guarded local PostgreSQL check
confirmed `postgres_local/radai_dev`, consistent history, 564 applied migrations,
zero pending migrations across all applications, Sales 0011 applied and no Sales
model drift. No local business records were changed by these checks.
The separate disposable PostgreSQL VF concurrency/API run passed 13 tests.

Apply backend Sales migration `0011_vf_opportunity_registration` before releasing
the matching frontend. Production migration/deployment remains unverified;
development integration and a main-target pull request do not certify production.
