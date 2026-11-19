# Sales Phase 1 Foundation Exit Register

- **Version:** 0.2
- **Baseline date:** 19 November 2026
- **Phase status:** OPEN
**Closure rule:** Every exit criterion is `VERIFIED`, there are zero open P1
blockers and zero open risks, required evidence is attached or linked, and the
user explicitly approves phase closure. No Phase 2 work is authorized by this
register.

This register turns the approved Phase 1 foundation workstreams into binary
exit criteria. A test pass in one focused suite does not override a failed
broader suite, an unverified migration, or a missing end-to-end acceptance
scenario. Status is `NOT MET` until the listed evidence is available.

## Exit criteria

| ID | Exit criterion | Required evidence | Status |
|---|---|---|---|
| F1-01 | This register is tracked and committed, with criteria and evidence requirements defined. | Initial register committed as `70ec0fd0`; current evidence and status are recorded in version 0.2. | VERIFIED |
| F1-02 | Sales migrations 0038, 0039 and 0040 are tracked, dependency-ordered and applied successfully to a disposable PostgreSQL database. | Migration graph, exact PostgreSQL version/commands, `showmigrations`/schema evidence, and no model drift. | VERIFIED |
| F1-03 | The agreed Sales test suite has zero failures and zero errors. | Full command, test count, skips with reasons, failure-free output; previously known failures are triaged and resolved or explicitly excluded by approved scope. | NOT MET |
| F1-04 | Canonical Sales API and feature contracts are current and stored in an authoritative tracked location. | Tracked route/payload map and linked detail contracts committed as `3e26630c`; current limitations and superseded policy boundaries are identified. | VERIFIED |
| F1-05 | A seven-stage end-to-end acceptance suite is implemented and passes, including negative paths and provenance. | Executable tests and run evidence for intake, qualification, tender analysis, planning, proposal drafting, human review/approval gates, and PDF/submission-package behavior. | NOT MET |
| F1-06 | The technical proposal template and reusable evidence baseline have an approved, versioned definition. | Versioned template/evidence catalog, section-to-source rules, approval owner/date, verified factual claims, and explicit unknowns/placeholders. | NOT MET |
| F1-07 | Governance boundaries are verified throughout the accepted workflow. AI cannot decide Go/No-Go, approve compliance or commercial terms, commit resources, or submit to a client. | Code/test evidence, approval matrix, audit/provenance checks, and acceptance results. | NOT MET |
| F1-08 | Phase closure is authorized only after all criteria above are verified and all blockers/risks are closed. | Final evidence matrix, zero-blocker/zero-risk register, and explicit user approval. | NOT MET |

## Current blocker register

| ID | Blocker | Current evidence | Closure action |
|---|---|---|---|
| P1-01 | **Closed:** migrations were untracked and PostgreSQL application was unverified. | Migrations and model state committed as `2e4e65f5`; disposable PostgreSQL 15.18 reported successful `migrate sales`, 0038-0040 applied in `showmigrations`, inspected schema columns, and `makemigrations sales --check --dry-run` found no drift. | Retain the command/schema record. Separately investigate the non-fatal post-migrate seed errors for unrelated apps before a full-application migration certification. |
| P1-02 | **Open:** agreed Sales suite is not clean. | Latest full run before the final test-fixture correction: 1,274 tests, 25 failures, 6 errors and 41 skips. Focused post-fix email/review and access suites pass; see Test Evidence. | Resolve the remaining code/test/environment failures, capture skip reasons, and rerun the full suite with zero failures/errors. |
| P1-03 | **Closed:** canonical contracts were not in a tracked authoritative location. | Current contract and linked route details committed as `3e26630c`. | Maintain the tracked contract when routes, payloads, access rules or lifecycle behavior changes. |
| P1-04 | **Open:** no passing seven-stage acceptance run exists. | The synthetic manual-intake test committed as `e18fa75c` passes, but it does not automate delivery planning/capability matching or exercise a complete requirement matrix, compliance gate, submission package or external transport. | Extend and run acceptance for the complete target with provenance, denial, stale-source, provider failure, approval and output cases. |
| P1-05 | **Open:** no approved universal proposal template/evidence baseline is available. | `SALES_TECHNICAL_PROPOSAL_BASELINE.md` is version 0.1 DRAFT; owner, corporate template/evidence library, and approval record remain pending. | Obtain the authorized source material and named business/legal/technical approvers; keep unsupported claims as placeholders. |
| P1-06 | **Open:** governance roles and controls are not fully established. | Current tests prove human bid decision, separate reviewer/approver actors, draft approval guard and provenance. No approved role/owner matrix, mandatory compliance sign-off, automated capability/resource gate, or external submission transport is evidenced. | Obtain approval ownership and implement/test the required independent human gates without granting AI decision authority. |

## Test evidence

- Broad command: `python manage.py test apps.sales.tests --settings=config.settings_release_test --noinput --verbosity=1`.
  Found 1,274 tests; 25 failures, 6 errors, 41 skips; exit code 1. This run
  preceded the final fixture-only correction to the out-of-department registration
  test; the affected 25-test subset then passed with one PostgreSQL-only skip.
- Remaining clusters: 17 Opportunity Analysis failures and 3 errors because the
  current pipeline is rules-only while the tracked feature contract and tests
  require the configured provider/durable refresh behavior; 5 classification
  failures and 1 test error; 2 PDF/letters errors because Windows cannot load
  WeasyPrint's `libgobject-2.0-0`; and failures in ZIP classification and
  workspace deletion. The test error in the classification storage-change case
  is an `override_settings` fixture cleanup error.
- Focused email analysis/customer matching/review suites: 216 passed after the
  Sales visibility-role correction.
- Focused registration, proposal-readiness and bid-justification suites: 66
  passed after client-scope corrections.
- Current Sales lifecycle and VF registration suites: 25 passed, 1
  PostgreSQL-only skip.
- Acceptance plus bid-decision/workflow governance tests: 39 passed.
- Sales model drift check: `python manage.py makemigrations sales --check
  --dry-run --settings=config.settings` reported no changes. The test settings
  disable migrations, so they are not used for migration certification.

The broad suite still fails. Its 41 skip reasons were not fully emitted at
verbosity 1; that evidence must be captured before F1-03 can be verified.

## PostgreSQL migration evidence

The ordered chain is `0038_opportunity_zip_import` →
`0039_quote_draft_optional_estimates` → `0040_tender_content_classification`.
On a disposable PostgreSQL 15.18 database, `migrate sales` exited successfully,
`showmigrations sales` marked all three applied, and direct schema inspection
confirmed the added fields/tables. The database container was stopped and
removed after validation. The targeted run logged non-fatal `post_migrate` seed
errors for unrelated `pid_checker_v2` and `instrument_io_workflow` tables that
were not part of the selected migration plan; those are disclosed and are not
reported as clean full-application migration evidence.

## API contract verification

The tracked canonical contract documents the route/payload map, permission
boundaries, migration order and current feature limitations. Its current
authority is `docs/SALES_OPPORTUNITY_TO_PROPOSAL_CONTRACTS.md`; workspace-root
Markdown copies are not treated as the version-controlled source of truth.

## Acceptance evidence

`apps.sales.tests.test_phase1_opportunity_to_proposal_acceptance` passed in the
39-test focused run. Its synthetic manual lane verifies opportunity creation,
qualification, a human bid decision, selected tender-source provenance,
editable AI suggestion, explicit field save, bounded technical basis, draft
PDF, internal review by a separate actor, human approval and audited
`send_to_client` status recording. It does not prove seven-stage automation:
there is no portal intake, automated capability/resource matching, complete
requirement matrix, mandatory compliance gate, submission-package generation,
or email/portal transmission. The client-submit action records state and audit
evidence; it does not transmit externally.

## Risk register and approval matrix

| Risk | Status | Owner/approval evidence |
|---|---|---|
| Analysis and classification providers are not integrated to the behavior required by their tracked contracts/tests. | OPEN | Technical owner and provider behavior acceptance pending. |
| Windows PDF tests cannot load the required native WeasyPrint library. | OPEN | Build/test environment owner and reproducible supported runtime pending. |
| Full Sales suite has failures/errors and unclassified skips. | OPEN | Sales engineering/test owner pending. |
| Proposal template/evidence sources and claims are not approved. | OPEN | Business owner, evidence-library owner, Legal/compliance and technical approver pending. |
| Target acceptance lacks automated planning, compliance and submission-package/transport coverage. | OPEN | Sales, delivery/PMO, compliance and submission owners pending. |
| Targeted Sales migration emitted unrelated post-migrate seed errors. | OPEN | Owners of `pid_checker_v2` and `instrument_io_workflow`; full-app validation pending. |

| Decision/control | Current implementation | Required accountable approval |
|---|---|---|
| Go/No-Go | Human Sales action; AI may only advise. Current fallback does not identify a designated approver. | Named Sales decision owner/role pending. |
| Compliance sign-off | No mandatory separate compliance gate is enforced. | Named compliance approver and checklist policy pending. |
| Commercial approval | Human proposal approval is recorded; existing self-approval semantics remain. | Named independent commercial approver policy pending. |
| Resource commitment | No automated capability-to-live-availability match or enforced resource commitment gate. | Delivery/PMO resource owner pending. |
| Client submission | Human action records approved revision/hash/status; no email or portal transmission exists. | Named submission authority and transport policy pending. |
| Template/evidence baseline | Version 0.1 candidate remains DRAFT. | Authorized business owner and Legal/technical review pending. |

## Completion

Three of eight exit criteria are VERIFIED: F1-01, F1-02 and F1-04. The
criterion-count measure is **37.5% (3/8)**. This is not a phase pass: F1-03 and
F1-05 through F1-08 remain NOT MET, P1 blockers and risks remain open, and the
user has not authorized phase closure.

## Verified safety invariants

- AI output is an editable suggestion only; persistence remains an explicit
  user action.
- Go/No-Go, compliance sign-off, commercial approval, resource commitment and
  client submission remain human-governed actions.
- Proposal approval requires complete technical and commercial fields,
  including total estimated hours and estimated delivery cost.
- Source excerpts and identifiers are not proof of semantic completeness;
  unsupported or missing facts remain explicit for review.
- The generated PDF is not represented as an approved universal template,
  compliance certification or submitted proposal.

## Acceptance evidence format

For each scenario record: scenario ID, fixture/source identity and hash,
expected and observed result, human actor/permission, AI call boundary,
business/audit mutations, generated file/version, test command, result, and
artifact/log location. Do not record success from mocked fixtures as live
provider, production-storage or deployment evidence.

## Change log

- **0.1 — 19 November 2026:** Initial Phase 1 exit register and baseline
  blockers.
- **0.2 — 19 November 2026:** Recorded migration, contract, test and synthetic
  acceptance evidence; updated blocker/risk/approval status. Phase remains OPEN.
