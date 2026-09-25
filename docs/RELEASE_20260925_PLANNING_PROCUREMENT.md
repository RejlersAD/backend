# Planning, project lifecycle and procurement release — 25 September 2026

This release packages the current backend work on `development`. The initial
checkpoint is `595a74b883c90f7ae03af909617fc0baaa6feb4e`, based on
`f0600f5b`, which matched the fetched `origin/development` and `origin/main`.
Both alignment merges were already up to date. The follow-up source checkpoint
is `bf862f096255f49c08fc5debdf2c2ae130181821`. It includes the final reviewed
geometry/cache changes and an unsaved-generation serialization guard found
during release checks. Final verification and the source cutoff are recorded
below before handoff.

## Resulting behavior

- Document analysis retains bounded progress and compatible checkpoints,
  recovers truncated responses through smaller source sections, and pauses on
  safely classified persistent provider/account failures. Error summaries do
  not expose provider response bodies or credentials.
- Verified PDF register geometry preserves cell text, applicability marks,
  qualifications and source locators. Geometry caches remain bound to source
  identity; literal source evidence stays distinct from planning proposals.
- Planning-package generation restores an editable relational schedule using
  configured workflows and disclosed duration/calendar/sequence assumptions.
  Exact-source/review checks and retry identities preserve older versions.
  Generation does not confirm source facts or approve a baseline.
- Eligible project deletion requires an explicit permanent-deletion flag,
  current timestamp and existing authority. Protected history, linked business
  records and active processing continue to block deletion. Database deletion
  retains an independent audit and exact storage-cleanup manifest; unfinished
  cleanup is visible and supports explicit retry.
- PO commercial extraction uses bounded labels and page/column geometry to
  recover seller references and split payment labels without including unrelated
  neighboring fields or subsequent pages in delivery terms.
- PR source review persists Level labels and an Additional Approver separately
  from live authority. Unknown signer corrections require a Special note;
  original detector evidence and recorded signatures/dates remain protected.
  Saved detail exposes these annotations and the canonical Richa Level 0
  display reference without inventing a source approval.
- Reviewed project CSV values preserve all explicit references and matching
  project metadata. Existing-record attachment changes use a narrow guarded
  project command, leaving other commercial content protected.

Detailed contracts:
[document planning](document-driven-planning.md),
[extraction boundaries](planning-extraction-boundaries.md),
[core planning](core-planning-engine.md),
[project deletion](project-permanent-deletion.md),
[PR source review](PR_SOURCE_APPROVAL_REVIEW.md), and
[procurement lifecycle](PROCUREMENT_LIFECYCLE.md).

## Compatibility and recovery

The release changes no model, migration, configuration or dependency file. Deployment migration
verification remains separate from SQLite functional tests and is recorded
below. No release operation merges into `main` or performs live project deletion,
document import, approval, notification delivery or data repair.

Coordinate the matching frontend rollout. Existing archive-oriented clients
cannot silently invoke permanent deletion because the explicit flag is required.
Source-approval corrections now require a Special note. Older clients receive a
validation error and retain their saved evidence. Optional import annotations
remain backward compatible when omitted; project attachment edits are explicit.

Preserve source evidence, audit history and file-cleanup manifests during any
rollback or forward fix. Reverting application code cannot restore a project
that a user has subsequently authorized for permanent deletion. Stored planning
versions remain historical records and are not automatically regenerated.

## Verification

Verification is in progress. The release must not be represented as ready until
the final test results, migration evidence and source cutoff replace this note.

The initial staged manifest contained 90 source/test/document files. All 83
Python sources compiled, the staged whitespace check passed, and the bounded
credential-pattern scan found no matches. Ignored media, raw documents, caches,
private aggregate data and local test artifacts were excluded. Seven intentional
planning regression files previously hidden by a generic ignore rule were
included using narrow exceptions.

The final 84 changed Python files compiled and passed the fatal Flake8 checks
(`E9,F63,F7,F82`). Their hashes matched before and after the final focused
planning run, which passed all 267 cases in 71.504 seconds. Those 14 modules
cover PDF register geometry and cache integration, provider pauses, document
intelligence and register rows, planning-package source/builder/API/jobs,
document plans, operational reliability, preview confirmation and AI recovery.
The unsaved-generation serializer, analysis workspace and package jobs passed
26 further cases in 15.197 seconds. The serializer regression verifies that an
unsaved generation does not issue database queries or invent schedule IDs.

The isolated action-permission suite passed all 30 cases in 8.076 seconds with
`config.settings_permissions_test` and temporary media storage. It covers
`tests_action_enforcement`, `tests_module_actions` and
`tests_user_permission_overrides` under `apps.rbac.tests`.

The full planning/procurement suites and core project deletion/storage,
portfolio and milestone suites use the explicit `CONTRIBUTING.md` test
environment (`DATABASE_URL=sqlite:///:memory:`, testing environment flags,
`USE_S3=false`) and `--settings=config.settings_release_test --noinput`.
The release test settings provide temporary media and isolated cache/email.
Full results and any PostgreSQL-only skips will be recorded after completion.

Local logs and the before/after source manifests are under ignored
`.codex_tmp/backend-release-*`. These verification artifacts are not committed.
The PR workflow runs the stable **Purchase Recommendation Concurrency
(PostgreSQL)** check from
`.github/workflows/purchase-recommendation-concurrency.yml`; its remote result
must be checked after PR creation. The separate main-push workflow has
nonblocking lint and security steps. This document does not claim a production
deployment or a merge into `main`.
