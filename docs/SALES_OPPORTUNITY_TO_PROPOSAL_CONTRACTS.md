# Sales Opportunity-to-Technical-Proposal Contracts

**Contract version:** 1.0

**Effective date:** 19 November 2026

**Authority:** This tracked document and the linked tracked contracts below are
the canonical Sales Opportunity-to-Proposal API and feature reference.
Workspace-root Markdown copies are local reference material, not the
version-controlled source of truth.

This contract describes current local implementation and explicit boundaries.
It is not proof of deployment or an approved universal company proposal
template. The seven-stage target is not yet a single automated workflow.

## Access and decision authority

Current active identity, Sales entry access and record visibility govern Sales
proposal preparation. Creating a proposal does not require a recorded Go,
Proposal stage, notification acknowledgement, CEO go-ahead, a particular
client status or `new_proposals_permitted`. The canonical client must remain
visible and match the opportunity. Current source export permission is required
where an endpoint reads document content. These rules do not broaden delete,
export, cross-module Planning, handover or external-submission access.

AI may only return a reviewable suggestion or bounded analysis. It cannot record
Go/No-Go, approve compliance or commercial terms, commit staff/resources,
persist proposal text, or send/submit a proposal. Explicit human commands and
existing audit/lifecycle rules remain authoritative.

The local Sales settings default `SALES_BID_DECISION_RBAC_FALLBACK_ENABLED` to
true. When no configured business route exists, the fallback permits a human
with current Sales/Opportunity access to record the bid decision; it does not
enforce a distinct designated approver. Proposal approval also retains the
documented existing self-approval semantics. No approved role/owner matrix for
these decisions was evidenced, so this is not a production approval policy.

## Stage and route map

All paths below are under `/api/v1/sales/`.

| Target stage | Current contract | Authoritative detail |
|---|---|---|
| Opportunity intake | `POST deals/` creates a manually reviewed opportunity. Mailbox capture and email-intake review remain separate explicit commands. Portal intake is not claimed by this contract. | [VF registration](SALES_VF_REGISTRATION.md), [email capture](SALES_EMAIL_CAPTURE.md), [email intelligence](SALES_EMAIL_INTELLIGENCE.md) |
| Qualification | `POST deals/{id}/submit-qualification/` records qualification workflow. `POST deals/{id}/bid-decision/` records the human decision. `POST deals/{id}/bid-decision-justification/` returns an editable suggestion only. | [lifecycle contract](SALES_LIFECYCLE_RELEASE.md), [bid justification](SALES_BID_JUSTIFICATION.md) |
| Tender intelligence | `GET deals/{id}/analysis/` reads the requester-scoped report; `POST` with `{retry?: boolean}` requests/queues analysis. `GET deals/{id}/analysis/summary/` returns bounded proposal-screen findings. `GET quotes/{id}/technical-basis/` projects current source inventory and bounded technical review signals. | [opportunity analysis](SALES_OPPORTUNITY_ANALYSIS.md), [tender basis](SALES_PROPOSAL_TENDER_BASIS.md) |
| Delivery planning | `GET/POST deals/{id}/bid-preparation/` exposes the explicit Planning workspace connection and preparation operations. This is not automated capability/staff availability matching and does not become an implicit prerequisite for proposal creation. | [bid and Planning preparation](SALES_BID_PREPARATION.md) |
| Proposal drafting | `GET quotes/preparation-opportunities/`; `POST quotes/start-draft/` with `{deal}`; proposal field drafting and source catalog below; `POST quotes/{id}/first-draft/` with `{expected_updated_at}`. | [readiness](SALES_PROPOSAL_READINESS.md), [field AI contract](SALES_PROPOSAL_DRAFT_AI.md) |
| Internal review | `POST quotes/{id}/send-internal-review/` with `{expected_updated_at}` binds the current review PDF and enters internal review. Review reads/writes remain on the existing revision-bound feedback endpoints. This is not a mandatory compliance checklist or sign-off gate. | [review contract](SALES_PROPOSAL_REVIEW.md) |
| PDF/submission | `POST quotes/{id}/technical-proposal-pdf/` with `{appendix_file_ids}` returns a private draft PDF. `POST quotes/{id}/send_to_client/` accepts `{recipient, evidence?}` only for an approved `ready_to_submit` quote and records a submitted version/hash and audit event. It does not create a submission package or transmit through email or a client portal. | [tender basis](SALES_PROPOSAL_TENDER_BASIS.md), [review contract](SALES_PROPOSAL_REVIEW.md) |

### Proposal readiness

`GET quotes/preparation-opportunities/` accepts `page`, `page_size` (1–500),
`search` and `pending_only=true|false`. It returns visible opportunity
projections, never creates a Quote, and redacts inaccessible client identity.
`POST quotes/start-draft/` accepts exactly `{deal}` and returns a saved draft
Quote (201) with canonical client/currency. Unknown price, cost, validity and
hours remain unknown, not zero. See `SALES_PROPOSAL_READINESS.md`.

### AI field draft and selected sources

The field writer routes are `POST deals/{id}/proposal-draft-field/` and
`POST quotes/{id}/draft-field/`. They accept
`{field, text, draft_fields?, source_file_ids?}`. Fields are `scope`,
`deliverables`, `assumptions` and `exclusions`; each narrative is at most 4,000
characters and combined draft text at most 16,000. The response is private,
non-cacheable editable text with `source_context`; it does not save a Deal or
Quote.

`GET deals/{id}/proposal-draft-sources/?search=&offset=` lists current Tender
and Correspondence sources. At most four distinct current upload IDs may be
selected. Supported types are PDF, DOCX, TXT, MD, EML, MSG, XLSX, CSV, HTML and
HTM; each source is at most 32 MiB. Current opportunity export access is
required to read content. Unreadable, unsupported, stale or inaccessible
selections fail explicitly. Only bounded excerpts are supplied as untrusted
evidence. Exact excerpt matching establishes provenance to text, not semantic
correctness or complete tender coverage. See
`SALES_PROPOSAL_DRAFT_AI.md`.

### Technical basis and PDF

Technical-basis inspection requires export access and is bounded to at most two
RFT PDFs and twelve bulletin PDF/DOCX/MSG files. It does not OCR scanned PDFs or
make a complete clause-by-clause compliance decision. Other tracked files remain
an inventory/review cue.

PDF appendices must be current RADAI PDFs from Tender or Correspondence; up to
four files, 32 MiB each and 500 pages combined are accepted. Generated output is
a reviewable draft with unresolved evidence/placeholders, not an approved
universal template, compliance certification, commercial approval or submission.

## Human approval gates

Proposal approval requires saved scope and deliverables, `estimated_hours.total`,
a valid-until date, positive total price and non-null estimated delivery cost.
The approval command writes the existing actor/time/status/audit evidence. An
incomplete draft remains unapproved. No AI endpoint invokes this command.
Internal review feedback is not commercial approval, and no separate compliance
sign-off is required by the current approval endpoint. The human
`send_to_client` command requires an approved revision and recipient, then
records submission state and audit evidence; no outbound mail or portal
transport is implemented by that endpoint.

## Schema and migration contract

The current local Sales migration dependency order is:

`0038_opportunity_zip_import` → `0039_quote_draft_optional_estimates` →
`0040_tender_content_classification`.

Migration 0039 makes draft financial totals, estimated cost and validity
nullable; the approval requirements above remain. Do not treat model
autodetection as evidence that the migrations were applied. PostgreSQL migration
validation and deployment status are recorded separately in the Phase 1 exit
register.

## Known feature boundaries

- Portal-to-opportunity automation is not established by the current API map.
- Qualification and bid decisions remain human-governed.
- Tender analysis is bounded; scanned-content OCR and complete requirement
  traceability are not claimed.
- Delivery planning does not automatically match approved capability evidence
  to live staff availability.
- Internal review supports revision-bound comments and outcomes, but it does not
  enforce a separate compliance checklist or reviewer sign-off before
  commercial approval.
- Bid-decision fallback and proposal approval do not establish an independently
  approved role/owner matrix; the documented current behavior includes
  self-approval semantics.
- AI currently drafts four narrative fields; it does not author an entire
  verified technical proposal or infer commercial values.
- The PDF is an interim technical draft. No approved universal company template
  or reusable capability/evidence library is established here.
- `send_to_client` records an approved revision as submitted but does not
  assemble a governed submission package or transmit it externally.
- No single automated seven-stage acceptance result is claimed.

## Contract ownership and change control

Update this file and the linked tracked detail contract in the same change when
an API body, response, access rule, migration or workflow boundary changes.
Historical release notes remain historical; they do not override this current
contract. Phase 1 cannot close until its exit register contains objective test,
migration and acceptance evidence and has no open blockers or risks.
