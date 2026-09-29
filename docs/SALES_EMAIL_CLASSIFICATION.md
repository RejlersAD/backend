# Sales email classification proposals (Task 3)

## Explicit conversion confirmation - 29 September 2026

The Email Intake redesign requires an explicit human classification before an
opportunity can be created. Both existing commands now require
`classification_code` and the JSON boolean `classification_confirmed: true`:

- `POST /api/v1/sales/mailbox-connections/{id}/convert-to-opportunity/`
- `POST /api/v1/sales/email-intakes/{id}/convert-to-opportunity/`

The code must be one of the 19 existing classification codes listed below.
The UI also offers manual `promotional_event` and `system_notification` choices;
these do not describe opportunities and the server rejects their conversion.
They do not add automatic classifier rules. Existing categories remain subject
to human review and existing opportunity permissions, without a new business
eligibility policy or an automatic qualification transition.

Absent, false or nonboolean confirmation returns HTTP 400 with a
`classification_confirmed` field error. An absent/invalid/non-opportunity code
returns HTTP 400 with a `classification_code` field error. Existing denial,
source-change, expiry and provider errors retain their meanings.

Confirming in the UI is a review for the current source/session. It does not
create a server record or claim a saved classification. The confirmation and
opportunity commit together: the existing `opportunity_created_from_email`
audit event records `reviewed_classification` with `version: 1`, code, canonical
label, `confirmed_by` (server actor ID) and `confirmed_at` (server timestamp).
Source analysis remains a proposal and GET requests remain read-only.

Live conversion includes the code and true confirmation in a version-2 reviewed
payload hash. Identical retries retain the original audit and return the existing
accessible opportunity; changed classification conflicts with HTTP 409. Historic
version-1 audit hashes may return an existing opportunity after matching the old
opportunity fields and all current source/scope/access checks. This never
backfills historical confirmation or creates another record. Imported retries
likewise reject changing a classification already recorded in the audit; legacy
records without that evidence retain their existing idempotent return behavior.

No schema migration, source mutation, new confirmation endpoint or mailbox write
is introduced. Deploy the coordinated UI and backend: older conversion clients
without these new fields receive a validation error and must be updated. The
persisted additions are ordinary JSON audit data; rollback does not erase them.

Verified locally on Python 3.11: 41 isolated SQLite API/regression cases passed,
including strict confirmation, invalid/non-opportunity codes, source and access
guards, audit rollback and historical/changed retries. Four disposable
PostgreSQL cases also passed: imported conversion and three observed competing
submissions (identical, changed fields, changed classification). The latter
observed distinct connections waiting on the existing row lock; exactly one
opportunity and its original review audit survived. The temporary test database
and named tmpfs PostgreSQL container were removed. These are functional and
locking checks, not an application migration or deployment. Evidence:
`artifacts/email-enterprise-classification-tests.log` and
`artifacts/email-enterprise-classification-postgresql-tests.log`.

Release preparation on 29 September 2026 reapplied only this feature to fetched
`main` at `c3ab24723443b70a2de82d282d6ad99315ce84e7`, preserving the later
shared-mailbox setup release. Against that isolated source, 65 Python 3.11
API/migration regressions and four PostgreSQL concurrency cases passed. The
migration cases apply/reverse the existing Sales 0008, 0009 and 0010 operations
using private historical SQLite fixtures. PostgreSQL tests use a disposable
tmpfs database and observed real row-lock contention; the database and container
were removed. These checks do not apply migrations to the application database.
Release logs are in workspace `artifacts/release-email-premium-20260929/`.
The full base-settings registry check loaded 58 apps and 533 migration files,
reported no migration conflicts and no model drift. It replaced database/cache/
email transports with private in-memory services before Django initialization.
This supersedes an initial raw-base-settings attempt that retained PostgreSQL
connection options under SQLite and was stopped after that configuration error.
No application migration state or deployed database was queried.

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
