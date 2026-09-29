# Sales email classification proposals (Task 3)

Existing saved-intake and live-mailbox analysis now derives
`extracted_information.classification` from the selected message's current
unquoted text. No new endpoint, database column, background classification job
or external AI provider is introduced. Existing source capture and sync continue
independently. The proposal does not create a lead, client or opportunity, and
does not change an email's review status or mailbox state.

## Response contract

The additive object contains `version: 1`, `status`, `code`, `label`, `confidence`,
`needs_review: true`, `evidence` and `alternatives`.

- Status: `classified`, `ambiguous`, `unclassified` or `draft`.
- Confidence: `level` (`high`, `medium`, `low` or `unresolved`),
  `method: rule_evidence_v1`, and `reason`. This describes rule-evidence strength,
  not a calibrated probability. There is no percentage score.
- Evidence: `source_id`, `location` (`subject` or `body`), exact `excerpt` and
  `rule_id`. Source IDs refer to existing `analysis.sources`.
- Alternatives: candidate `code`, `label` and evidence. Ambiguous proposals have
  blank primary code and `Needs review` label, rather than an arbitrary winner.

High means a fresh subject and current body both provide explicit supported
category wording; medium means one source location provides it; low describes
recognized acknowledgement/meeting wording. Unresolved covers unavailable,
conflicting or unrecognized evidence. A qualified or contradictory body can
make a positive-looking subject ambiguous. Partial coverage downgrades high to
medium. These are conservative English rules with known false-negative limits.

Categories are tender opportunity, RFP, RFQ, RFT, EOI, ITT, proposal request,
clarification, tender bulletin, tender addendum, award notification, regret
notification, contract award, framework agreement, purchase order, variation
request, vendor request, invoice related and general communication. Literal EIO
remains in underlying request evidence without being silently normalized to EOI.

The selected message's classification is separate from `analysis.message_kind`
and the original `request_type_code`. A current tender addendum can concern an
older RFT. A reply quoting an invitation does not thereby constitute a new
invitation. General communication is a fallback suggestion, not proof that mail
is noncommercial. Negative, conditional and uncertain evidence needs review.
The [final extraction contract](SALES_EMAIL_INTELLIGENCE.md) anchors request type
to the original solicitation. Later incidental words cannot override it; only an
assertive positive later reclassification of that solicitation creates a review
conflict. This does not change the independent selected-message classification.

## Source and access boundaries

Existing customer, reference, date and scope extraction remains source-backed.
Missing and ambiguous values stay blank with evidence/warnings. Received time
does not become a submission date. For captured saved messages, analysis now
receives the retained source mailbox address so own quoted messages cannot
establish an incoming customer/request. Legacy records keep unknown context.

Saved analysis covers the individual saved body and visible quoted chain.
It does not fetch separately captured siblings or attachments. Live analysis
retains its bounded authorized conversation read. Classification itself adds no
Graph request, attachment download, portal fetch or other network call.

Intake and mailbox scope/action checks apply before analysis. Proposals contain
no canonical client matches or additional record references; all existing client
and opportunity permissions remain separate. GET responses remain private and
no-store. Repeated reads never overwrite source or human review evidence.

The shared detection component renders classification, confidence and literal
evidence in saved/live/reloaded detail, preserving the five detected fields,
independent panels and explicit reviewed opportunity form. Older or invalid
classification responses are shown as unavailable/unresolved. Classification
text cannot grant permissions, approve a bid or execute instructions.

## Deployment and verification

Deploy compatible backend code before the frontend. No migration or backfill is
required: existing sources receive derived proposals on their next authorized
read. Restart the local web process to load Python changes. The mailbox sync
worker does not depend on this presentation feature and can continue running.

Verification results, rule limitations and actual local metadata checks are
recorded in workspace `docs/features/sales-email-classification.md`. This local
implementation is not a production deployment or company-policy certification.

Verified on 28 September 2026: **265** Python 3.11 backend tests, **20** focused
browser cases, scoped lint, accessibility/desktop/mobile inspection and Node 20
production/PWA build passed. Eight actual local saved details exposed proposals
without changing source/review rows or creating clients/opportunities. Two were
recognized as EOI; six remained unclassified, illustrating the explicit review
boundary. Local full registry reports 561 applied migrations, no pending changes
or model drift. Logs: backend `artifacts/sales-classification-*.log` and frontend
`artifacts/email-classification/`. Production has not been deployed.
