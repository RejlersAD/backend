# Controlled P&ID issue and EPC progress evidence — Task 4

Authority: the user's Task 4 request of 12 November 2026 and the explicit
choice to treat an approved live CRS chain plus completed exact revision and
document as the CRS issue prerequisite. The live CRS app has no separate
document-approval actor or approval command. This bounded rule does not create
one, change CRS status, or infer client acceptance.

Migration `pid_checker_v2.0029` adds immutable `PidControlledIssue` and
`PidIssueProgressEvidence` records. An issue is unique per Task 2 evidence link
and has a generated stable number `PID-{project_id}-{link_id}` and the exact
CRS revision label. It retains the project, EPC work item, extraction UUID,
CRS chain/document/revision, technical review round and actor, validation
snapshot, source file hash, evidence fingerprint, issuer, and issue time. The
issue row also preserves the exact issued PDF bytes, checked
against the approved source hash before commit. A later CRS file replacement
does not change the issued copy.
Both records are protected from ordinary ORM updates/deletes; existing append-only
`EPCWorkEvent` and `rbac.AuditLog` entries are written in the same transaction.

All paths below are under `/api/v1/pid-checker-v2/engineering-evidence/{extraction_id}/`:

| Endpoint | Request and permission | Result |
| --- | --- | --- |
| `GET issue/` | `pid_analysis.read`, `crs_documents.read`, project access | Project-scoped immutable issue and progress intake snapshot |
| `GET issue/download/` | Same read authority | Exact PDF bytes captured at issue time |
| `POST issue/` | Exact `revision_id` and `evidence_fingerprint`; `pid_analysis.approve`, `crs_documents.approve/read`, project write, engineering HOD/manager position | 201 new issue, 200 identical retry, 409 stale or failed prerequisites |
| `POST progress-evidence/` | Required `note`; `pid_analysis.update`, `project_control.update`, `crs_documents.read`, project write | 201 one EPC progress-evidence intake, 200 identical retry, 409 conflicting retry |

Issue requires a persisted approved Task 3 decision event for the current
review round, passing validation, an unchanged source fingerprint and readable
CRS file, approved CRS chain, completed current CRS revision and document, and
no unresolved CRS comments. The issuer must be independent of extraction
creator, technical submitter/reviewer, and EPC work owner. The service locks the
review link and source rows, then writes the issue, EPC event, and audit record
atomically. A unique link constraint prevents concurrent duplicate issues.

Project Control intake locks the issue and EPC work item, checks current project
and action grants, and records one append-only `EPCWorkEvent` plus a source
snapshot from the existing EPC `review_evidence` service. It records the current
activity progress and baseline checksum if present. It does **not** write an
`ActivityProgressUpdate`, accept the EPC work item, or earn progress. Existing
`accept_work` remains the sole EPC acceptance path that posts 100% progress
after its own criteria, review, baseline, and separation checks. This avoids
double counting or inventing a percentage for issue alone. No approved
schedule, cost, or integrated baseline is changed; no baseline proposal is
needed for this evidence-only transition.

Legacy unlinked extractions and existing Task 1–3 APIs are unaffected. A later
source change does not rewrite the immutable issued snapshot. A new issue
requires a separately reviewed extraction/link; this API does not revise an
issued record in place.

Verification: migrations 0027, 0028, and 0029 applied to an isolated
`postgres:15-alpine` database, with the issued-PDF column inspected afterward.
The focused Task 1–4 suite passed 45/45 tests on that disposable PostgreSQL;
the two concurrent tests used real row locks. On SQLite, 43 tests passed and
the two PostgreSQL-only lock tests were skipped. Historical SQL-only tables
outside the active model registry require cascading flush in the disposable
PostgreSQL concurrency harness. No production migration was run.
