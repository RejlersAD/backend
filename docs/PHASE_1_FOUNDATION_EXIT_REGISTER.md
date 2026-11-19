# Sales Phase 1 Foundation Exit Register

- **Version:** 0.1
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
| F1-01 | This register is tracked and committed, with criteria and evidence requirements defined. | Commit containing this file; repository status/link. | NOT MET |
| F1-02 | Sales migrations 0038, 0039 and 0040 are tracked, dependency-ordered and applied successfully to a disposable PostgreSQL database. | Migration graph, exact PostgreSQL version/commands, clean apply, `showmigrations`/schema evidence, and no model drift. | NOT MET |
| F1-03 | The agreed Sales test suite has zero failures and zero errors. | Full command, test count, skips with reasons, failure-free output; previously known failures are triaged and resolved or explicitly excluded by approved scope. | NOT MET |
| F1-04 | Canonical Sales API and feature contracts are current and stored in an authoritative tracked location. | Contract-to-route/payload comparison, tracked diff, and no active superseded-policy contradictions. | NOT MET |
| F1-05 | A seven-stage end-to-end acceptance suite is implemented and passes, including negative paths and provenance. | Executable tests and run evidence for intake, qualification, tender analysis, planning, proposal drafting, human review/approval gates, and PDF/submission-package behavior. | NOT MET |
| F1-06 | The technical proposal template and reusable evidence baseline have an approved, versioned definition. | Versioned template/evidence catalog, section-to-source rules, approval owner/date, verified factual claims, and explicit unknowns/placeholders. | NOT MET |
| F1-07 | Governance boundaries are verified throughout the accepted workflow. AI cannot decide Go/No-Go, approve compliance or commercial terms, commit resources, or submit to a client. | Code/test evidence, approval matrix, audit/provenance checks, and acceptance results. | NOT MET |
| F1-08 | Phase closure is authorized only after all criteria above are verified and all blockers/risks are closed. | Final evidence matrix, zero-blocker/zero-risk register, and explicit user approval. | NOT MET |

## Baseline evidence and open blockers

| ID | Blocker | Current evidence | Closure action |
|---|---|---|---|
| P1-01 | Migration files are not tracked and PostgreSQL migration application is unverified. | Local files 0038-0040 exist; model autodetection reported no changes. No PostgreSQL migration run is recorded. | Track migrations; run ordered migration validation on a disposable PostgreSQL instance; record schema state. |
| P1-02 | The broad Sales test run is not clean. | A previous workspace record reports 105 failures and 39 errors in a 1,264-test run. A later focused run passed 57 tests with one PostgreSQL-only skip. The broad failures are not yet triaged. | Reproduce, classify and fix in-scope failures; rerun the agreed suite. |
| P1-03 | Shared API/feature Markdown is ignored by the outer repository. | Contract content was edited locally, but `*.md` is ignored at the workspace root and the files do not appear in tracked diffs. | Move authoritative contracts to tracked documentation or update repository ignore policy through review. |
| P1-04 | No integrated seven-stage acceptance run is evidenced. | Focused backend and recorded browser checks cover pieces; no single acceptance suite proves the workflow and provenance end to end. | Implement and execute F1-05 with success, denial, stale-source, provider failure, approval and PDF cases. |
| P1-05 | No approved universal proposal template/evidence baseline is available. | Current generated PDF is documented as an interim review draft with gaps/placeholders, not a company-approved standard. | Identify authoritative source material and approval owner; version only verified claims and section rules. |

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
