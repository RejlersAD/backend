# Proposal field AI drafting

The read-only writer supports a new proposal before Quote creation and an
existing editable Quote. It reuses the configured Sales AI provider and centrally
managed credentials with proposal-specific trusted instructions. It does not
create, save, approve, submit or revise any business record. There is no schema
change, historical backfill, document ingestion or outbound correspondence.

## HTTP contract

- `POST /api/v1/sales/deals/{id}/proposal-draft-field/`: new-proposal context.
- `POST /api/v1/sales/quotes/{id}/draft-field/`: exact saved-Quote context.

Both accept only `{field, text, draft_fields?}` with no query parameters. `field`
is `scope`, `deliverables`, `assumptions` or `exclusions`. Text defaults to empty;
nonblank text requests a rewrite. Every supplied text is a string of at most
4,000 characters. Optional `draft_fields` contains any of those four keys, with
at most 16,000 characters across the target and its siblings. If it includes
the target field, that value must match `text` exactly. The target is sent to
the provider once. Unsupported fields and invalid control characters fail 400.

The private, non-cacheable response is:

```json
{
  "text": "Editable suggested wording",
  "source_context": {
    "opportunity_id": "UUID",
    "quote_id": null,
    "field": "scope",
    "mode": "draft",
    "opportunity_type": "rfq",
    "opportunity_type_label": "RFQ",
    "opportunity_updated_at": "ISO timestamp",
    "quote_updated_at": null,
    "evidence": [{"field": "opportunity_type", "excerpt": "RFQ"}]
  }
}
```

For saved drafts, `quote_id` and `quote_updated_at` identify the exact Quote.
Scope is plain paragraph text; the other fields return one item per line,
bounded to 40 nonempty lines. The existing explicit form save converts those
lines to Quote JSON lists. The AI endpoint never performs that conversion or
saves its output. It returns neither provider credentials nor raw provider data.

## Authority and freshness

The route guard treats the endpoint as a read. The domain additionally requires
fresh active-account Proposal read and create (new) or update (saved), plus
Opportunity and Client read permissions and current record/organization scope.
New drafting calls `proposal_readiness.require_proposal_creation` before and
after generation: Proposal stage, recorded Bid/Conditional Bid, canonical
accessible client and current proposal eligibility. Active and Prospect clients
are permitted only while `new_proposals_permitted` remains true, as explicitly
authorized for preparation. Other client statuses remain blocked.

Saved drafting requires the exact Quote to remain editable under
`bid_preparation.require_editable`, its canonical client to match the Deal,
recorded Bid/Conditional Bid and Proposal or Negotiation stage. Existing
approval/submission evidence prevents generation for immutable revisions.
These checks do not grant commercial approval or submission authority.

Before returning output, the helper reloads permissions, scope, eligibility and
source records, and compares the normalized source fingerprint and configured
provider identity. Changed current source/configuration returns 409. Revoked
access returns denial; unavailable records remain unavailable. No database lock
is held by the helper across the provider request. The result is a snapshot
checked at return, not a reservation against later edits.

The UI must discard responses after any target/sibling edit, selected opportunity
or Quote change, save, close or cancelled request. It verifies response source
identity and keeps manual input on errors. Explicit Save/Create remains the
persistence action. Unchanged structured Planning JSON must be omitted from an
editor PATCH rather than flattened merely by opening the form.

## Source and output boundaries

Allowed facts are saved opportunity title/reference/type/scope/description,
recorded bid decision/rationale, visible client name, location and service
categories; saved Quote scope/deliverables/assumptions/exclusions; and separate
unverified current browser draft strings. Missing or unknown Opportunity Type
remains unknown. Narrative fields are bounded to 4,000 source characters.
Legacy/Planning lists inspect at most 40 items and only string narrative keys
(`document_number`, `title`, `name`, `description`, `text`, `content`,
`discipline`). Arbitrary nested JSON, resource prices, costs, hours, approvals,
private documents and commercial calculations are not added as structured facts.
Authored narrative remains its author's text; it is not a verified commercial
finding. Oversized historical narrative is an excerpt, not a complete review.

Provider instructions focus on client-specific clarity and execution using the
supplied facts. They prohibit invented experience, commitments, available staff,
approval, prices/hours/dates or winning guarantees. Assumptions/exclusions remain
proposed terms subject to review and confirmation. Source text cannot change
trusted instructions, request tools or grant authority. No new automatic AI
fallback or template-success response is introduced.

Validation requires the exact target field, bounded passive plain text, strict
JSON shape and 1–8 exact source excerpts of at most 300 characters. Invalid output
fails 502 without returning the draft. Exact excerpts prove source presence,
not semantic accuracy, completeness, engineering validity or agreed terms.
Human review is required; this writer is not a general semantic proof system.
Existing provider input/output byte limits and timeouts also apply. Output is
capped at 2,000 tokens without increasing a lower configured provider limit.
Unavailable configuration and provider errors produce safe 503 responses;
provider timeout produces 504. Draft input and credentials are not logged by
this helper, and no AI response is cached or retained as approval evidence.

## Verification

Focused test module: `apps.sales.tests.test_proposal_draft_ai`. Its isolated
guarded HTTP fixtures cover all four fields, fresh access/scope, Active/Prospect
and blocked clients, open-Go and immutable-proposal eligibility, exact evidence,
bounded structured sources, hostile text isolation, safe failures and changed
source/provider/access. Tests assert no Deal, Quote or audit mutation and mock
provider SDK access. No live client documents or live provider calls are used.
Verified on 1 October 2026 from `backend/`, with the explicit local test
environment documented in workspace `CONTRIBUTING.md`:

```powershell
..\.venv\Scripts\python.exe manage.py test apps.sales.tests.test_proposal_draft_ai apps.sales.tests.test_bid_justification apps.sales.tests.test_email_ai_provider --settings=config.settings_release_test --noinput
```

All 74 tests passed in 12.923 seconds, including 20 new proposal-writing cases.
Log: `artifacts/proposal-draft-ai-regressions.log`. The first focused run found
two test-fixture errors: an email-less synthetic account triggered an unrelated
biometric synchronization signal, and instance deletion nulled a cached profile
ID. Both fixture issues were corrected before the passing combined run. Python
compilation, scoped fatal-code lint and tracked-file whitespace checks passed.
SQLite verification does not claim PostgreSQL locking or migration coverage;
the AI helper changes no persistence. Live provider wording quality and
production deployment were not tested by these checks.
