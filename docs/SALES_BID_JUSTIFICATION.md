# AI bid-decision justification

`POST /api/v1/sales/deals/{id}/bid-decision-justification/` is synchronous,
read-only writing assistance for a saved, qualified opportunity. It does not
record the decision, justification, stage or opportunity audit. The existing
bid-decision command remains the only save action and retains its own authority.

Request JSON contains only `decision` (`bid`, `conditional_bid`, `no_bid`) and
optional `text` (plain text, at most 4000 characters). Empty text requests a draft;
nonempty text requests a rewrite. Other fields and query parameters are rejected.

```json
{
  "text": "Bid is proposed for the RFQ; the decision grounds require review.",
  "source_context": {
    "opportunity_id": "saved-opportunity-uuid",
    "opportunity_type": "rfq",
    "opportunity_type_label": "RFQ",
    "decision": "bid",
    "mode": "draft",
    "updated_at": "saved-source-update-time",
    "evidence": [{"field": "opportunity_type", "excerpt": "RFQ"}]
  }
}
```

Require current active identity, Opportunity `read`, and existing Sales
record/organization scope before and after generation. Drafting does not require
an approval grant or business route and grants no authority to save a decision.
The server reads the saved type and bounds an allowlist of title/reference,
scope/description, risk, estimated value/currency and dates. No files, emails,
client contacts, qualification JSON, proposal content or arbitrary source fields
are ingested. Description is limited to its first 4000 characters. Missing type
is `Not provided`; unsupported historic type is `Not recognized`, with no inferred
replacement. Source or provider configuration changes discard the generated text.

The existing configured Sales AI transport resolves central credentials and
uses its established official provider endpoints, timeouts, private logging and
response limits. The new wrapper supplies bid-specific trusted instructions and
a 1400-token ceiling, further bounded by deployment configuration. No dependency,
fallback provider, automatic template success, job, cache or schema is added.

Provider output must match a strict decision/text/evidence schema. The decision
must match the user's choice; text is bounded plain text; each of 1-8 citations
must be a 1-300-character exact excerpt from a supplied named field. Additional
checks reject obvious opposite-decision wording and unsupported assertions of
approval, capacity or profitability. These checks are not proof of semantic
accuracy: source data and user rationale remain unverified, and the user reviews
and edits the proposed wording before the separate save action. Instructions in
record fields or draft text cannot grant authority or change system instructions.

Responses: 200 proposed text (`Cache-Control: private, no-store`); 400 invalid
input; 403 current access denied; 404 unavailable record; 409 unqualified/changed
source or provider configuration; 502 invalid AI output; 503 disabled,
unconfigured or failed provider; 504 timeout. AI failures use safe
`{detail, code, reason}` fields and never return credentials, prompts or raw
provider errors. The UI retains existing input on any failure or late response.

Verification uses synthetic SDK/provider responses through the real guarded Deal
router. No actual opportunity or customer information is sent to an AI provider
by the tests; SQLite checks do not certify PostgreSQL contention or deployment.

On 1 October 2026, the following passed all 80 tests (21 justification, 27 existing
bid-access/workflow and 32 shared-provider cases), with exit 0 and test database
cleanup. Log: `artifacts/bid-justification-backend-tests.log`.

```powershell
..\.venv\Scripts\python.exe manage.py test apps.sales.tests.test_bid_justification apps.sales.tests.test_bid_decision_access apps.sales.tests.test_workflow apps.sales.tests.test_email_ai_provider --settings=config.settings_release_test --noinput --verbosity=1
```

Coverage includes draft/rewrite, explicit missing type, scoped current access,
malformed input/output, timeout and safe failures, late permission/source/provider
changes, retained negation in capacity claims, injection kept as source data and
unchanged opportunity/audit state. Scoped fatal/import lint and whitespace checks
passed. No migration, production deployment or live-provider writing-quality test
was performed by this verification.

After aligning trusted instructions with the conservative verbatim guard, the
final focused justification suite passed 22/22 in 6.720 seconds (exit 0), including
the added positive case retaining a negated capacity sentence and its caveat.
Log: `artifacts/bid-justification-final-focused.log`; command as above with only
`apps.sales.tests.test_bid_justification`. This overlaps the earlier 80-test run;
it is final-change verification, not 22 additional independent cases.
