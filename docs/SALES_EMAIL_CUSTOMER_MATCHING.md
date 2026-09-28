# Sales email customer matching for review (Task 4)

Saved intake and live mailbox detail responses now add
`extracted_information.customer_match` using the current request actor. The
source's existing mailbox/intake guards run first. The pure email analysis and
Microsoft Graph service remain independent of canonical client access. Matching
does not change stored email content, human review, a client or an opportunity.
There is no schema change, new endpoint, external request or background job.

## Matching and source contract

The additive version 1 object contains `status`, `method: exact_name_v1`,
`detected_name`, `needs_review: true`, `evidence: {excerpt, source_ids}`,
`candidates` and `has_more`.

| Status | Meaning |
| --- | --- |
| matched | One accessible canonical record has an exact normalized name. |
| ambiguous | Multiple accessible records match; no winner is chosen. |
| no_match | No accessible record matches the evidenced name. |
| not_detected | The available analysis has no explicit organization-name evidence. |
| conflicting | Organization-name evidence conflicts; no directory lookup occurs. |
| unavailable | Source references are invalid/missing, request context is absent, or the directory read failed. |
| denied | The actor is unauthenticated, inactive or lacks effective sales_clients.read. |

A resolved v2 `organization_name` must have existing evidence and references into
`analysis.sources`; legacy detection uses its original `customer_name` meaning.
The final v2 Customer Name display may use an explicit organization or a qualified
low-confidence label derived from an evidenced external domain. This display
fallback never becomes matcher input or client creation. `customer_domain` stays
separate supporting evidence. See [the current contract](SALES_EMAIL_INTELLIGENCE.md).
Missing/conflicting organization evidence never falls back to `company_name`,
sender identity, email domains or portal links. Joined evidence
can refer to multiple source segments; retained source excerpts are bounded, so
the whole joined evidence string need not occur in any single excerpt. The
name itself must occur in its organization evidence after the same normalization.
The original analysis coverage and warnings are retained.

Matching applies NFC Unicode normalization, casefolding and collapsed whitespace
to `company_name`, `legal_name` and `trading_name`. It preserves punctuation and
legal suffixes. It does not guess acronyms, translate names or perform fuzzy
matching. An exact result is a suggestion, not verified legal identity or
business eligibility. No-match does not prove the company is absent globally.

## Record and action authority

Every match evaluation checks authenticated, active identity and effective
`sales_clients.read`, including user-specific denials that override admin grants.
It separately uses `visible_email_clients` and the same Sales/account_manager
visibility as the canonical Client endpoint. Canonical Sales teammates may share
clients; mailbox ownership does not define client access. Invisible matches do
not affect the status, candidate count or ambiguity.

Each candidate exposes only `id`, `client_code`, `company_name`, `matched_fields`,
`status`, `verification_status` and boolean `new_proposals_permitted`. Financial,
contact, ownership and private-note fields are neither selected nor returned.
Status and proposal metadata are informational. Existing conversion commands
recheck permissions and selected client access at submission.

Saved intake also exposes `can_create_client` for the existing explicit manual
new-client option. It is true only for received/under_review sources when all
email-opportunity permissions and `sales_clients.create` are effective. No request
context or missing grants produces false. This capability does not itself create
a client, authorize a different command or bypass submission validation.

## Completeness and resource boundary

One request-local matcher scans the complete minimal authorized projection on
the first valid claim. A saved list reuses it across source names. There is no
500-record cutoff or cross-request/user cache. All aliases of one canonical ID
are deduplicated. Results contain at most 20 candidates and `has_more`; matching
more than 20 records remains ambiguous. A failed partial scan is discarded and
returns unavailable, never a false unique result or false no-match.

The portable name index requires O(N) memory in authorized client names per
response. Database iteration fetches batches of 500 but does not bound retained
index memory. Very large directories may need a separately designed normalized
database index later. Current request scope is the boundary; subsequent requests
rebuild after ownership/role changes. The matcher checks action grants again even
when its current-request name index has already been built.

## UI and delivery boundary

The frontend presents supporting evidence and explicit client choices. A match
must not replace a manual selection automatically. Older or malformed responses
are unavailable. Existing source freshness, client selection, guarded manual
client creation and opportunity retry contracts remain in force. Responses stay
private and no-store. Match proposals are not persisted in captured source data
or included in the live source-review hash.

Deploy compatible backend code before the matching UI. No migration or source
backfill is needed; existing records gain proposals on their next authorized
read. This task is local implementation, not a production deployment.

## Verification

Focused checks use synthetic rows and the real guarded API routes, effective
permissions and canonical visibility. They cover exact aliases, Unicode,
whitespace, distinct suffixes/punctuation, duplicates and the 20-result cap,
a match after 550 earlier records, inaccessible matches, current team scope and
role removal, revoked read permission, missing/conflicting/bad source references,
interrupted scans, single directory scan on a list, saved/live parity, unchanged
source/review/business state, GET-only Graph reads and the manual-create capability.

From `backend`, with the isolated testing environment in workspace
`CONTRIBUTING.md`:

```powershell
..\.venv\Scripts\python.exe manage.py test apps.sales.tests.test_email_customer_matching apps.sales.tests.test_email_customer_matching_api --settings=config.settings_release_test --noinput
```

Verified on 28 September 2026: all **29** focused service/API tests passed in
9.216 seconds. The result log is `artifacts/sales-email-customer-matching-tests.log`.
The same 29 passed on the local container's Python 3.11 runtime in 4.049 seconds
(`artifacts/sales-customer-matching-python311-tests.log`). The 265 retained Sales
tests also passed on that runtime in 61.139 seconds
(`artifacts/sales-customer-matching-regression-tests.log`).
These SQLite/model-sync checks do not certify migrations or PostgreSQL locks;
this feature adds neither schema nor a new contested write. Wider retained
workflow, runtime and UI verification is recorded in workspace
`docs/features/sales-email-customer-matching.md`.

The actual local PostgreSQL registry separately showed 561 applied migrations,
zero pending/conflicts and no model drift. Eight authorized saved-detail reads
returned the new projection while source/review/client/opportunity/audit hashes
remained unchanged. The verified local actor has no accessible clients: six
detected names correctly returned no_match and two sources returned not_detected.
This is local readiness evidence, not a production configuration or successful
business-client match. See `artifacts/sales-customer-matching-live-check.log`
and `sales-customer-matching-directory-check.log`. Automatic sync stayed running.
