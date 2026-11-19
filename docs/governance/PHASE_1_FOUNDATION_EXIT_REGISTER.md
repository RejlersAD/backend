# Phase 1 Foundation Exit Register

- **Version:** 1.0
- **Baseline date:** 19 November 2026
- **Phase:** 1 — Foundation
- **Phase status:** OPEN
- **Completion:** 37.5% (3 of 8 criteria verified)
- **Authority:** This is the authoritative, version-controlled Phase 1 exit
  register. The former `docs/PHASE_1_FOUNDATION_EXIT_REGISTER.md` is retained
  only as a pointer to this file.

## Gate rules

Each criterion has a binary status. `✅` means the specified evidence has been
found and meets the requirement; `❌` means it has not. Focused test passes do
not override a failing full suite. Drafts, design proposals and mock-based
acceptance do not establish approved policy or production behavior.

Phase 1 remains OPEN until every criterion is `✅`, all P1 blockers and open
risks are closed, required evidence is complete, governance controls are
verified, and the user explicitly approves closure. This register does not
authorize Phase 2.

## Exit Criteria Matrix

| ID | Requirement | Evidence Required | Evidence Found | Status | Owner | Blocking Reason |
|---|---|---|---|---|---|---|
| F1-01 | Maintain a version-controlled authoritative Phase 1 exit register with binary criteria and evidence. | This register committed at `docs/governance/PHASE_1_FOUNDATION_EXIT_REGISTER.md`; legacy location identifies it as superseded. | This versioned register is committed at the required path; the legacy file links here. | ✅ | Phase 1 register custodian; individual not named. | None for this criterion. |
| F1-02 | Track and validate Sales migrations 0038, 0039 and 0040 on PostgreSQL. | Ordered migration chain, disposable PostgreSQL version, application and schema evidence, and no Sales model drift. | Commit `2e4e65f5`; disposable PostgreSQL 15.18 run reported `migrate sales` success, all three migrations applied in `showmigrations`, schema inspection, and no drift from `makemigrations sales --check --dry-run`. | ✅ | Sales database/migration owner; individual not named. | None for the targeted Sales migration criterion. Separate post-migrate seed errors for unrelated apps remain an open risk. |
| F1-03 | The agreed Sales test suite has zero failures and zero errors, with skips explained. | Full command and result, complete skip reasons, triage/closure evidence for prior failures, and a passing rerun. | Last broad Docker run: 1,291 tests, 24 failures, 4 errors, 41 skips. Later focused workspace/ZIP suites passed 47 tests with 4 skips, but the broad suite was not rerun and skip reasons remain incomplete. | ❌ | Sales test owner; individual not named. | Provider-backed Opportunity Analysis and document-classification failures/errors remain; full-suite rerun and skip-reason evidence are missing. |
| F1-04 | Keep canonical Sales API and feature contracts in a tracked authoritative location. | Tracked route/payload map, permissions and lifecycle boundaries, plus current feature limitations. | Contract committed as `3e26630c`; current reference is [SALES_OPPORTUNITY_TO_PROPOSAL_CONTRACTS.md](../SALES_OPPORTUNITY_TO_PROPOSAL_CONTRACTS.md) and its linked detail contracts. | ✅ | Sales API/contract owner; individual not named. | None for the tracked-contract criterion. |
| F1-05 | Implement and pass seven-stage end-to-end acceptance with negative paths and source provenance. | Executable evidence for intake, qualification, tender analysis, delivery planning, proposal drafting, human review/approval gates, and PDF/submission-package behavior. | The synthetic manual-lane acceptance test passed in a 39-test focused run. It does not cover portal intake, automated capability/resource matching, a complete requirement matrix, mandatory compliance, a submission package, or external transmission. | ❌ | Sales product owner and QA owner; individuals not named. | No passing acceptance run covers the seven-stage workflow. |
| F1-06 | Define and approve a versioned standard technical-proposal template and reusable evidence baseline. | Approved template and evidence catalog, section-to-source rules, named accountable owner, review/approval record, verified claims and explicit unknowns. | [SALES_TECHNICAL_PROPOSAL_BASELINE.md](../SALES_TECHNICAL_PROPOSAL_BASELINE.md) is version 0.1 DRAFT. The corporate template/evidence source, owner and approval record are pending. | ❌ | Business/template owner, Legal/compliance reviewer and technical reviewer; not identified. | No authorized template/evidence baseline or approval evidence exists. |
| F1-07 | Verify governance controls throughout the accepted workflow; AI must not decide Go/No-Go, approve compliance/commercial terms, commit resources or submit to a client. | Human approval matrix; implementation and test evidence for independent required gates, audit/provenance, and acceptance results. | Current contracts state that AI output is advisory and does not execute governed actions. Human bid decision, separate reviewer/approver actors and draft approval checks have focused evidence; designated owners, mandatory compliance sign-off and capability/resource gates are not evidenced. | ❌ | Sales decision owner, Compliance, Delivery/PMO and submission authority; not identified. | Approval ownership and required independent gates are not established end-to-end. |
| F1-08 | Close Phase 1 only after all criteria, blockers, risks and governance controls are verified and the user approves closure. | All criteria `✅`; zero open P1 blockers and risks; complete evidence and explicit user approval. | Three of eight criteria are verified. P1-02 and P1-04 through P1-06 and multiple risks remain open; no phase-closure approval is recorded. | ❌ | Phase sponsor/user; approval not given. | Prerequisite criteria and blocker/risk closure conditions are unmet. |

## P1 Blockers

The original six blocker IDs are retained for traceability. `CLOSED` means the
specific blocker was closed with the evidence listed; it does not close Phase 1.

| ID | State | Blocker and evidence found | Owner | Blocking reason / closure action |
|---|---|---|---|---|
| P1-01 | CLOSED | Sales migrations 0038–0040 were tracked and applied to disposable PostgreSQL 15.18; see F1-02. | Sales database/migration owner; not named. | Targeted Sales migration criterion has evidence. Separately resolve or scope the unrelated `pid_checker_v2` and `instrument_io_workflow` post-migrate seed errors before claiming full-application migration certification. |
| P1-02 | OPEN | Broad Docker suite reported 1,291 tests, 24 failures, 4 errors and 41 skips. Focused suites passed, but the broad suite has not been rerun. | Sales test owner; not named. | Resolve remaining failures/errors, capture every skip reason, then rerun the agreed full Sales suite with zero failures/errors. |
| P1-03 | CLOSED | Canonical API/feature contracts are tracked; contract and linked route details were committed as `3e26630c`. | Sales API/contract owner; not named. | Maintain the canonical contract with future route, payload, access, migration and lifecycle changes. |
| P1-04 | OPEN | Synthetic manual-lane acceptance passed; it does not automate or verify all seven target stages. | Sales product owner and QA owner; not named. | Implement and execute seven-stage positive/negative acceptance with provenance, stale-source, provider-failure, approval and output cases. |
| P1-05 | OPEN | Version 0.1 proposal/evidence baseline is a DRAFT; no corporate template, evidence-library owner or approval record is established. | Business/template owner, Legal/compliance and technical approvers; not identified. | Obtain authorized sources and named approvals; retain unsupported claims as explicit placeholders until approved. |
| P1-06 | OPEN | Current evidence does not establish a complete accountable role matrix, mandatory compliance gate, resource-commitment gate or external submission authority/transport. | Sales decision owner, Compliance, Delivery/PMO and submission authority; not identified. | Approve role ownership and implement/test the independent human gates without granting AI decision authority. |

## Evidence Summary

### Test evidence

- Broad command: `docker exec radai_backend_local python manage.py test apps.sales.tests --settings=config.settings_release_test --noinput --verbosity=1`
- Result: 1,291 tests; 24 failures, 4 errors, 41 skips; exit code 1. Skip reasons were not fully emitted.
- Recorded focused results: email analysis/customer matching/review, 216 passed; registration/readiness/justification, 66 passed; lifecycle/VF registration, 25 passed and 1 PostgreSQL-only skip; PDF/letter, 65 passed; acceptance/governance, 39 passed.
- Subsequent focused workspace/ZIP-import suites: 47 passed, 4 skipped. These do not replace the required broad-suite rerun.
- Sales model drift check reported no changes.

### Migration evidence

The ordered chain is `0038_opportunity_zip_import` →
`0039_quote_draft_optional_estimates` →
`0040_tender_content_classification`. On disposable PostgreSQL 15.18,
`migrate sales` exited successfully, `showmigrations sales` marked all three
applied, and schema inspection confirmed added fields/tables. The database was
removed after validation. The targeted run logged non-fatal `post_migrate`
seed errors for unrelated `pid_checker_v2` and `instrument_io_workflow`; this
is not full-application migration certification.

### Acceptance evidence

[The Phase 1 acceptance test](../../apps/sales/tests/test_phase1_opportunity_to_proposal_acceptance.py)
passed as part of the 39-test focused run. It verifies a synthetic manual lane:
opportunity creation, qualification, a human bid decision, tender-source
provenance, editable AI suggestion, explicit field save, bounded technical
basis, draft PDF, separate review/approval actors, and audited
`send_to_client` status recording. The action records state and audit evidence;
it does not transmit externally. Portal intake, automated capability/resource
matching, complete requirement matrix, mandatory compliance gate,
submission-package generation and email/portal transmission remain unverified.

## Open Risks

| Risk | Status | Evidence / consequence |
|---|---|---|
| Opportunity Analysis and document classification do not meet the provider-backed behavior required by their tracked contracts/tests. | OPEN | Accounts for the majority of known broad-suite failures/errors; no clean broad rerun exists. |
| Broad-suite skip reasons are incomplete. | OPEN | The 41 skip reasons were not fully captured at verbosity 1. |
| Full-application migration path has unrelated post-migrate seed errors. | OPEN | Targeted Sales migrations passed, but unrelated apps' seed errors require owner review. |
| The seven-stage workflow lacks complete automation and acceptance. | OPEN | Portal intake, full requirement traceability, live capability/resource matching, compliance gate, submission package and transmission are not established. |
| Proposal template/evidence sources lack authorized ownership and approval. | OPEN | Current baseline is DRAFT; factual reuse rules and corporate source set are not approved. |
| Approval ownership and independent governance gates are incomplete. | OPEN | Named role matrix, mandatory compliance approval, resource commitment control and external submission authority remain pending. |

## Completion and change history

Three of eight exit criteria are verified: F1-01, F1-02 and F1-04.
Criterion-count completion is **37.5% (3/8)**. Phase 1 is **OPEN**; no phase
closure or Phase 2 authorization is recorded here.

- **1.0 — 19 November 2026:** Consolidated the existing Phase 1 audit into the
  authoritative binary register at `docs/governance/`.
