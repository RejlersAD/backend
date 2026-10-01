# Proposal preparation opportunities

`GET /api/v1/sales/quotes/preparation-opportunities/` provides canonical Deal
summaries for recorded `bid`/`conditional_bid` decisions in stage `proposal`.
The Proposal register uses these as derived **Proposal preparation** rows until
an explicit Quote exists. Reading never creates Quotes, revisions, Planning
workspaces, prices, validity dates or business audit events.

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

Current active identity and Proposal/Opportunity read grants govern the queue;
existing Deal and known-organization scope remain. Client identity/name are null
when the current actor lacks Client read or client-record access, and the
inaccessible client-name field is excluded from search. Eligible scoped Go opportunities
remain visible when creation is blocked, with an explicit capability reason.
Responses are `private, no-store`.

The user explicitly permits **Active and Prospect** clients for preparation;
`new_proposals_permitted` must still be true. Inactive/other restricted statuses
remain blocked. The shared `client_permits_proposal_preparation` predicate covers
candidate/create eligibility, editable Quote content and existing BidPreparation
connect/import capabilities. It never changes Client status or commercial
approval/submission rules.

`require_proposal_creation(actor, deal, client=None)` accepts objects or canonical
IDs and returns fresh `(actor, deal, client)`. Require Proposal create, Opportunity
read, Client read, current record/client scope and matching inherited client,
recorded Go plus Proposal stage, and the client rule above. No negotiation-stage
creation or stage-only substitute for an actual bid decision is introduced.
The ordinary Quote serializer uses this helper. Quote creation locks Deal then
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

## Verification — 1 October 2026

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
