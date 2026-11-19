# Proposal field AI drafting

The read-only writer supports a new proposal before Quote creation and an
existing editable Quote. It reuses the configured Sales AI provider and centrally
managed credentials with proposal-specific trusted instructions. It does not
create, save, approve, submit or revise any business record. Explicitly selected
current opportunity documents may supply bounded excerpts; this does not create
a separate document-ingestion record or send outbound correspondence.

## HTTP contract

- `POST /api/v1/sales/deals/{id}/proposal-draft-field/`: new-proposal context.
- `POST /api/v1/sales/quotes/{id}/draft-field/`: exact saved-Quote context.

Both accept `{field, text, draft_fields?, source_file_ids?}` with no query
parameters. `field` is `scope`, `deliverables`, `assumptions` or `exclusions`.
Text defaults to empty;
nonblank text requests a rewrite. Every supplied text is a string of at most
4,000 characters. Optional `draft_fields` contains any of those four keys, with
at most 16,000 characters across the target and its siblings. If it includes
the target field, that value must match `text` exactly. The target is sent to
the provider once. Unsupported fields and invalid control characters fail 400.

`source_file_ids` is optional and accepts at most four distinct current upload
UUIDs from the same opportunity's Tender or Correspondence folder. The source
catalog is `GET /api/v1/sales/deals/{id}/proposal-draft-sources/?search=&offset=`.
The writer accepts `.pdf`, `.docx`, `.txt`, `.md`, `.eml`, `.msg`, `.xlsx`,
`.csv`, `.html` and `.htm` sources up to 32 MiB each. Unreadable, unsupported,
stale or inaccessible selections fail rather than being silently ignored.
Selected content is extracted into bounded, field-ranked excerpts and passed as
untrusted evidence, not application instructions.

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
    "sources": [{"field": "source_1", "id": "UUID", "name": "Scope.pdf",
                 "folder": "tender", "source_path": "Scope.pdf",
                 "version": 1, "sha256": "content digest"}],
    "evidence": [{"field": "source_1", "excerpt": "Exact source excerpt"}]
  }
}
```

For saved drafts, `quote_id` and `quote_updated_at` identify the exact Quote.
Scope is plain paragraph text; the other fields return one item per line,
bounded to 40 nonempty lines. The existing explicit form save converts those
lines to Quote JSON lists. The AI endpoint never performs that conversion or
saves its output. It returns neither provider credentials nor raw provider data.

## Authority and freshness

The route guard treats the endpoint as a read. The domain revalidates the
current active identity, Sales entry, current record/organization scope and
canonical visible client. This follows the 17 November 2026 Sales decision-access
direction: proposal preparation does not require a prior Go decision, a
particular stage, notification acknowledgement, CEO go-ahead, a Client status,
or `new_proposals_permitted`. Current Sales policy and record visibility remain
authoritative.

Saved drafting additionally requires the exact Quote to remain editable under
`bid_preparation.require_editable` and its canonical client to match the Deal.
Existing approval/submission evidence prevents generation for immutable
revisions. These checks do not grant commercial approval or submission
authority. Selected documents also require current opportunity export access.

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

Allowed record facts are saved opportunity title/reference/type/scope/description,
recorded bid decision/rationale, visible client name, location and service
categories; saved Quote scope/deliverables/assumptions/exclusions; and separate
unverified current browser draft strings. Explicitly selected Tender or
Correspondence files contribute only the bounded source excerpts described
above. Missing or unknown Opportunity Type remains unknown. Narrative fields are
bounded to 4,000 source characters.
Legacy/Planning lists inspect at most 40 items and only string narrative keys
(`document_number`, `title`, `name`, `description`, `text`, `content`,
`discipline`). Arbitrary nested JSON, resource prices, costs, hours, approvals
and commercial calculations are not added as structured facts. No unselected
document content is sent to this writer.
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
guarded HTTP fixtures cover all four fields, current Sales access/scope, drafting
without Go or stage/client-status restrictions, immutable proposals, exact
evidence, selected document limits and freshness, hostile text isolation, safe
failures and changed source/provider/access. Tests assert no Deal, Quote or audit
mutation and mock provider SDK access. No live client documents or live
provider calls are used.

Historical verification — 1 October 2026, before the current November Sales
decision-access policy. The focused run used the explicit local test
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
