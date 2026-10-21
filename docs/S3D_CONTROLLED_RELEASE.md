# S3D controlled release and import pilot - 20 October 2026

Authority: I-06 and the user's explicit immediate Phase 3-4 continuation,
recorded in `../../docs/features/s3d-reference-intelligence-release.md`.
This adds controls to the established extraction/matching/preview/export flow.
It does not deploy production or execute SP3D remotely.

## Engineering trust chain

The shared preview builder consumes explicitly approved/remapped pinned match
decisions, using source component identity and checking nominal row size.
Industry/contractor/reportable commodity joins in supported SPEC and CAT rows
use the reviewed code. Manual overrides remain visible and win; a conflicting
CAT identifier cannot count as a consumed approved decision for release.
Ineligible/invalid-reference decisions and unexpanded size intervals cannot
establish readiness. Expanded generated rows require their own valid evidence.

Preview-only `origin` and per-cell `provenance` distinguish `extracted`,
`reference`, `default`, `override` and `inferred`. Uncertain builder-derived
values remain inferred; they are not relabeled as source facts. Passthrough
template rows are reference values. The canvas exposes origin in cell tooltips.
Provenance metadata is not added to bulk-load worksheet columns.

## Persistence and immutability

Migration **0015_controlled_release** adds a persisted override revision,
ValidationRun, SpecRelease and SpecAuditEvent. Migration **0016_import_pilot**
adds ImportAttempt. Both are additive.

Evidence records reject ordinary model updates/deletion and queryset
update/deletion. Command code appends new records rather than rewriting history.
This is application-level immutability, not a database-administrator/raw-SQL
tamper-proof ledger. Source jobs with validation/release evidence are protected
by foreign keys; project deletion rejects frozen/pinned reference evidence.

- A draft records the current manifest and blockers.
- Validation records results, high/medium/low counts, blocking error count,
  input/preview hashes, actor and trigger source. Existing Canvas validation
  also persists runs; release freeze requires a current run covering **both**
  workbooks.
- Freeze appends a `frozen` successor of a matching draft, referring to the
  exact validation run and storing SPEC/CAT bytes and their SHA-256 hashes.
- Workbook cell save/clear, batch save, row/bulk delete and applied chatbot
  edits advance the persisted revision and append an `invalidated` successor
  of a previous frozen record. The original record, manifest and files retain
  their original values. A repeated freeze of an unchanged draft reuses its
  existing frozen successor. An invalidated draft cannot issue again.
- Match review/run commands are audited; changed match evidence invalidates
  a prior freeze. Unchanged matching reruns retain its input identity.
- Snapshot checks also catch source/override changes made outside these HTTP
  commands, file tampering, template/generator/validator changes and stale
  validation. Such privileged changes do not silently permit old exports.

Editing, validation, freeze and export commands serialize on the extraction job.
Project/reference commands retain project-first locking where needed. SQLite
checks behavior only; the PostgreSQL probe does not certify every concurrent
race or all deployment middleware/module grants.

## Manifest and released export

The manifest includes original source SHA-256, stored document SHA-256, job UUID,
pinned reference UUID/hash, template/generator/validator hashes, override
revision, preview SHA-256 and input SHA-256. Frozen manifests add the two actual
artifact hashes. Source classes/components, override provenance and match
decisions participate in the input fingerprint; API/provider credentials do not.

The renderer receives the same in-memory preview that was hashed for freeze.
ZIP ordering/timestamps and Office created/modified timestamps are normalized.
Frozen exports return the persisted bytes after checking current input identity
and artifact integrity. They do not rebuild a potentially changed workbook.

Existing SPEC/CAT endpoints now require a current unchanged frozen release by
default. `?draft=1` retains completed/nonpartial draft export. Draft downloads
carry a DRAFT filename, draft response header, and draft workbook properties;
the frontend explicitly requests and labels these downloads. Raw extracted
JSON/combined XLSX are separate existing data-review exports.

Freeze returns 409 with blockers unless extraction is complete (unknown
coverage is insufficient), source/reference inputs are available and hashed,
a validated frozen approved reference is pinned with edition/units recorded,
all components have consumed reviewed matches, there are no unrouted parts,
both workbooks have current zero-error validation, the draft is current, and
the actor is an explicitly configured release authority. Validation also checks
generated SPEC-to-CAT identifiers and duplicate CAT identifiers. Truncated
validation cannot establish readiness. No exception policy is inferred.

## API contracts

All routes below are under `/api/v1/spec-customization/paper-spec/jobs/{job_id}/`.
Existing authentication and source-job/project ownership/admin checks apply.
Freeze additionally checks explicitly configured release authority. UUIDs are
used throughout. Evidence bodies omit workbook/report binary data.

| Method and route | Behavior |
| --- | --- |
| `GET releases/` | Current manifest, readiness blockers, latest validation, up to 100 release records and configured disposable pilot choices. |
| `POST releases/` | Append a draft manifest, including unresolved blockers; returns 201. |
| `POST releases/validate/` | Validate both previews and append ValidationRun; returns 201. |
| `POST releases/{draft_id}/freeze/` | Freeze current reviewed inputs or return 409 with detailed blockers. |
| `GET release-history/` | Latest 500 audit events and 100 validation runs. |
| `GET export-spec/`, `GET export-cat/` | Current frozen bytes; optional `release_id` selects a specific still-current freeze. Missing/stale/invalidated release returns 409. `draft=1` requests a draft. |
| `GET import-attempts/` | Latest 100 evidence records and the accuracy-publication boundary. |
| `POST import-attempts/` | Append planned/import/reconciliation evidence for a current frozen release and configured disposable target. |
| `GET import-attempts/{attempt_id}/report/` | Authorized report download with SHA-256 integrity verification; records an audit event. Historical reports remain readable. |

Released exports add `X-Spec-Export-State`, `X-Spec-Release-ID` and
`X-Content-SHA256`, alongside pinned-reference headers. Drafts add
`X-Spec-Export-State: draft`.

## Governance configuration

These settings accept JSON arrays through the existing environment/.env loader.
They default to empty arrays. No role, staff flag or owner identity automatically
becomes engineering release authority.

```text
S3D_RELEASE_AUTHORITY_USER_IDS=["<authorized-user-id>"]
S3D_APPROVED_REFERENCE_SET_IDS=["<approved-reference-uuid>"]
S3D_DISPOSABLE_PILOT_TARGETS=[{"id":"<target-id>","label":"<pilot label>","disposable":true,"sp3d_version":"<agreed version>","import_profile":"<agreed profile>"}]
```

These placeholders are not configured authority or version/profile choices.
Operators must record the actual business approval basis before supplying them.
Targets without `disposable: true` and a version/profile are rejected. No target
credentials, endpoint or production connector is inferred or persisted here.

## Import evidence and measurements

Submit JSON for planning, or multipart for reports:

```json
{
  "release_id": "<frozen-release-uuid>",
  "target_id": "<configured-disposable-target-id>",
  "status": "planned",
  "rejection_count": 0,
  "result_details": {"notes": "<target report/reconciliation notes>"},
  "measurements": {"ground_truth_reference": "<reviewed sample identity>"}
}
```

Other statuses are `succeeded`, `failed`, `reconciled`. Nonplanned records
require a target-generated report file, limited to 5 MB, whose bytes/hash/name
are retained. Successful imports cannot report rejected rows. Reconciliation
requires `parent_id` referencing an earlier unreconciled attempt on the same
release/target. Follow-up records retain original reports. Identical repeated
submissions by the same actor reuse the latest matching evidence record.

Measurements accept nonnegative true-positive, false-positive, false-negative,
reviewer-minutes, provider-cost-USD and import-failure values. Accuracy counts
require a ground-truth reference. Decision/ambiguous counts are captured from
the pinned job. Raw supplied counts are evidence for review, not verified
accuracy or an automatically completed benchmark. Public percentages are not
computed or published. Recording a report does **not** execute or certify SP3D.

Actual target import, engineering reconciliation and representative benchmarking
remain external acceptance work requiring the agreed environment and owners.

## Verification

`scripts/check_s3d_controlled_release.py --apply` asserts local PostgreSQL
`radai_dev`, allows only pending 0015/0016, and runs six synthetic rolled-back
probes: freeze/export reproducibility, immutable invalidation, report storage/
reconciliation, unavailable target/missing report, unauthorized access and real
preview/XLSX reviewed-code consumption. It patches document/reference storage
in memory and uses isolated endpoint routing/middleware. It does not persist
governance settings or synthetic records, run SP3D, or deploy production.

Run the full Spec Customization suite with `config.settings_release_test` and
the documented SQLite/testing/no-S3 environment. Browser checks use
`S3D_TEST_TIMEOUT_MS=240000` and `playwright.s3d.config.js` on strict port 5187.
The feature brief records final counts and artifact logs.
