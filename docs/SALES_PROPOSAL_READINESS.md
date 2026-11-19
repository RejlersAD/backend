# Proposal preparation opportunities

Current policy follows the user's 17 November 2026 Sales decision-access
direction, which supersedes the original October Go/stage/client-status
eligibility rules recorded later in this historical brief. Any active user with
current Sales entry access may prepare proposals on visible records. Proposal
creation does not require a recorded Go decision, Proposal stage, notification
acknowledgement, CEO go-ahead, a particular Client status, or
`new_proposals_permitted`. Current identity, Sales access, record/client
visibility, canonical Deal/Client consistency, required fields and audit
integrity remain enforced. Commercial approval and external submission remain
separate governed actions.

`GET /api/v1/sales/quotes/preparation-opportunities/` provides canonical Deal
summaries for visible opportunities. The Proposal register uses these as
derived **Proposal preparation** rows until an explicit Quote exists. Reading
never creates Quotes, revisions, Planning workspaces, prices, validity dates or
business audit events.

Query parameters: `page` (default 1), `page_size` (1-500, default 100), `search`
(up to 200 characters), and `pending_only=true|false` (default false). Other
parameters are rejected. Pending-only excludes every Deal with any Quote,
including cancelled or historic Quotes. Default mode retains those candidates
for explicitly creating another revision. Multiple Quote revisions remain valid.

Return `{count, next, previous, results}`, ordered by descending
`stage_entered_at,id`. Pagination links are relative to this endpoint. Each row
contains canonical Deal `id`, `deal_code`, `deal_name`, `client`, `client_name`,
`opportunity_type`, `stage`, `bid_decision`, `submission_due_date`, `owner`,
`owner_name`, `service_categories`, `currency`, `updated_at`, `has_proposal`,
`can_create_proposal` and `blocked_reason`. There is no fabricated Quote ID,
number, revision or financial figure. The browser follows pages rather than
joining independently paginated Client and Deal lists.

Current active identity, Sales entry and existing Deal/known-organization
visibility govern the queue. Client identity/name are null when the current
actor cannot access that client, and the inaccessible client-name field is
excluded from search. The row reports current creation capability and a
blocker when its canonical client is unavailable.
Responses are `private, no-store`.

Client status and `new_proposals_permitted` do not independently block proposal
preparation under the current Sales decision-access policy. The canonical client
must still be visible and match the opportunity. Preparation never changes
Client status or commercial approval/submission rules.

`require_proposal_creation(actor, deal, client=None)` accepts objects or canonical
IDs and returns fresh `(actor, deal, client)`. It validates current active
identity, Sales entry, record/client scope and matching canonical client. The
ordinary Quote serializer uses this helper. Quote creation locks Deal then
its current Client and revalidates before committing Quote and its existing audit
together. Explicit revisions remain supported; unique proposal numbers prevent
duplicate same-number retries. This does not add general idempotent replay or a
one-Quote-per-Deal constraint. No model or migration change is needed.

The source `Deal` remains authoritative for derived rows. Existing Quote APIs,
revision identity, protected approval/submission evidence, review documents and
commercial workflows retain their existing behavior.

Creation also supplies a date-only default for optional `issue_date` using
`CreateOnlyDefault(timezone.localdate)`. The previous model default yielded an
in-memory timestamp, causing DRF to reject the create response when the actual
form omitted this field. Existing PUT/PATCH issue dates remain unchanged when
omitted; this API correction needs no schema migration.

## Historical verification — 1 October 2026

These results predate the 17 November Sales decision-access policy. The listed
Go/stage and client-eligibility expectations, including the PostgreSQL
restriction-contention scenario below, describe superseded behavior and are not
acceptance criteria for current proposal preparation.

The guarded SQLite API suites passed 63 tests in 22.266 seconds, exit 0:

```powershell
..\.venv\Scripts\python.exe manage.py test apps.sales.tests.test_proposal_readiness apps.sales.tests.test_bid_preparation apps.sales.tests.test_proposal_register apps.sales.tests.test_workflow --settings=config.settings_release_test --noinput --verbosity=1
```

Log: `artifacts/proposal-readiness-backend-tests.log`. After limiting the issue-date
default to creation, the final focused `apps.sales.tests.test_proposal_readiness`
run passed all 18 cases in 6.793 seconds, exit 0, including existing full-update
date preservation. Log: `artifacts/proposal-readiness-final-focused.log`. These
overlapping runs are not 81 separate test cases. Test databases were cleaned up.

Coverage includes paginated scoped Go rows without writes, no-Go/stage exclusion,
Active/Prospect and blocked clients, client redaction/search, permission/current
identity denial, creation replacing a derived row, explicit additional revisions,
same-number retry protection, client changes between validation and locked save,
atomic audit rollback, protected Quote content and Prospect Planning preparation
connection/import. Scoped E9/F lint and whitespace checks passed. These functional
checks do not certify migrations or deployment. Separate PostgreSQL 15 checks
passed all 18 readiness cases and the observed contention test in
`apps.sales.tests.test_proposal_readiness_postgresql`: a client restriction
committed while creation waits causes rejection without Quote/audit writes.
The initial observation reused a cached statistics snapshot; the corrected
test refreshes it and verifies the actual blocking transaction. Final contention
log: `artifacts/proposal-readiness-postgresql-contention.log` (1 test, 6.548s).
The disposable test database/container was cleaned up. Commands and limits are
recorded in the cross-repository feature brief.
