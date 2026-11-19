# Sales Technical Proposal and Evidence Baseline

**Document version:** 0.1

**Date:** 19 November 2026

**Status:** DRAFT — business approval pending

**Scope:** Candidate structure and evidence controls for a standardized
technical-proposal workflow. This document is not authorization to issue a
proposal, make a compliance claim, or use unverified company material.

## Approval state

The user requested that a proposal/evidence baseline be defined and versioned.
No corporate-approved universal template, authoritative corporate evidence
library, approval owner, or signed approval record was found in the inspected
workspace. The current generated technical-proposal PDF and prior-project sample
are explicitly review-only. This draft records a safe candidate structure; it
must remain `DRAFT` until the designated human owner approves its content and
source set.

## Candidate document structure

| Section | Required when | Evidence rule |
|---|---|---|
| Cover, document control and contents | Always | Use approved corporate identity, current client/tender identifiers, revision and issue date. Missing identity or reference stays visibly unconfirmed. |
| Executive summary and opportunity understanding | Always | Link each tender-specific statement to a current opportunity field or source requirement. Do not infer the client's objectives from a file name. |
| Qualification and compliance response | Per tender | Map every applicable requirement ID to a response and evidence source. “Compliant” requires human verification; missing evidence is `Needs review`. |
| Technical approach and methodology | Per scope | Cite the governing SOW/RFT/bulletin and identify assumptions, interfaces, exclusions and unresolved conflicts. |
| Deliverables and acceptance basis | Per tender | Preserve exact requirement IDs, source version/page/section and response status. Do not invent quantities, standards or acceptance criteria. |
| Organization, roles and key personnel | When requested | Require a current approved organization chart, verified qualifications, person consent and confirmed availability. Never infer assignments from a CV or old project file. |
| Schedule and project controls | When requested | Require an approved opportunity-specific schedule/resource basis. Prior project dates are not reusable commitments. |
| Quality and HSE | When requested | Require current approved policies, certifications/statistics and tender-specific response evidence. No blanket certification assertion from a prior submission. |
| Subcontractors, JV, local-content or investment response | Conditional on tender | Require current authorization and applicability review. “Not applicable” is not carried forward from another tender. |
| Assumptions, exclusions, risks and deviations | Always | Keep proposed terms separate from agreed terms; cite the source/owner of each. Unresolved commercial language requires human review. |
| Appendices and evidence register | When included | Bind exact current file identity, version and content hash; list source location and relevant page/section. Do not use prior-proposal material as current-project evidence without validation and approval. |

This table is a candidate information architecture, not an approved company
layout. The user-provided Q-102131 master prompt and Q-101505 sample are
project-specific design inputs only; Q-101505 assertions are not evidence for
Q-102131 or any other opportunity.

## Evidence record requirements

Every factual proposal claim must be traceable to a versioned source record
containing:

- stable source ID, repository/folder, file name, version and SHA-256;
- page, heading, requirement ID or structured record field;
- source owner, approval authority, effective/expiry date and permitted reuse
  scope;
- extracted quotation or structured value, plus reviewer verification state;
- target proposal section and the exact generated response text it supports.

Use explicit states: `Verified`, `Needs human verification`, `Missing`, `Stale`,
`Conflicting`, or `Not applicable (approved rationale required)`. AI-generated
wording is never evidence for its own factual claim. Exact excerpt matching
proves that text is present, not that it is accurate, complete, current, or
compliant.

## Reuse restrictions

- Prior proposals, CVs, experience lists, schedules, organization charts,
  subcontractor lists, HSE responses, certifications and investment responses
  are candidates only until their owners verify currentness and reuse authority.
- Do not state experience, personnel, staff availability, certifications,
  performance, client approval, pricing, schedule commitments or compliance
  without an approved source and required human review.
- Conflicting or unreadable sources remain disclosed; do not silently select a
  preferred source or fabricate missing evidence.
- Generated text and PDFs remain drafts. AI cannot make Go/No-Go, compliance,
  commercial, resource, approval or client-submission decisions.

## Current artifact limitations

The local PDF generator produces a reviewable draft with visible gaps and
placeholders. Its selected appendices do not prove complete source review. The
technical-basis projection is bounded and does not provide scanned-PDF OCR or a
complete clause-by-clause compliance gate. No end-to-end source-to-requirement-
to-response evidence store is established by this document.

## Approval record — required before version 1.0

| Approval item | Value |
|---|---|
| Accountable business owner | Pending identification |
| Corporate template source and revision | Pending |
| Evidence-library owner and source locations | Pending |
| Legal/compliance reviewer | Pending |
| Technical/proposal reviewer | Pending |
| Approval date and decision record | Pending |
| Approved reuse and expiry rules | Pending |

**Promotion rule:** Do not change the status to `APPROVED` or publish version
1.0 until the approval record is complete and the approving authority has
reviewed the rendered template and evidence rules. This draft therefore does
not close Phase 1 criterion F1-06.

## Change log

- **0.1 — 19 November 2026:** Candidate structure, evidence provenance rules and
  approval gaps recorded from the current local implementation and user-supplied
  project material. No corporate claims are approved.
